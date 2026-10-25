"""Lowering table of the quant_analyzer class: per member, verbs in order and the native calls (capability, arguments in the unit
the argument declares) each must make - written from the contracts (method arguments start from the contract example, which is
read from the contract here), not from the mapping.  [] = no native call (a class check refused, an unsupported verb, a dry run
or a usage error).  Queries the class makes (records.read, result.read, ...) are native calls too and are listed.

Fit observations built from measured records hold measured numbers; they are matched by shape (``Obs``): how many there are,
the fields the agent gave (reference id, level) exactly, and the measured fields present and numeric.

Each member starts from a world stage (tasks/quant_analyzer_world.py) applied the same way to the lab and its twin.
"""
import json
import math

from ...hosts import contract_index
from ..lowering import Obs
from . import quant_analyzer_world as W
from .quant_analyzer_world import config  # noqa: F401  (the lowering harness builds its labs from this config)


def EX(slug, cap, **over):
    """The contract example of a capability (the documented default the class method verb starts from), with overrides."""
    c = json.loads(contract_index()['instrument.' + slug].read_text(encoding='utf-8'))
    a = json.loads(json.dumps(c['capabilities'][cap].get('example_arguments') or {}))
    a.update(over)
    return a


def stage(iid, st, sample=False):
    return lambda b: W.bring_up(b, iid, st, sample=sample)


R = 'records.read'
ICPE, ICPMS, TOC, AA, EDX = W.ICPE, W.ICPMS, W.TOC, W.AA, W.EDX
STD = W.STANDARDS

CASES = {
    ICPE: {'prep': stage(ICPE, 'calibrated', sample=True), 'steps': [
        ('status', []),
        ('load soil --slot 1', []),                                                  # not supported: the operator loads
        ('blank', [('blank.read', {})]),
        ('records', [('records.list', {})]),
        ('records record_2', [(R, {'record_id': 'record_2'})]),
        ('calibrate multi --from record_2,record_3,record_4 --reference shimadzu_epa_ipc_assigned --levels 100,10,1 --dry-run', []),
        ('calibrate multi --from record_2,record_3,record_4 --reference shimadzu_epa_ipc_assigned', []),       # MISSING_OPTION --levels
        ('calibrate multi --from record_2,record_3,record_4 --reference shimadzu_epa_ipc_assigned --levels 100,10', []),   # LEVELS_MISMATCH
        ('calibrate multi --from record_2,record_3,record_4 --reference shimadzu_epa_ipc_assigned --levels 100,10,1',
         [(R, {'record_id': 'record_2'}), (R, {'record_id': 'record_3'}), (R, {'record_id': 'record_4'}),
          ('calibration.fit', {'curve_id': 'multi', 'accept_same_response_conditions': True,
                               'observations': Obs(3, [{'reference_id': STD[ICPE]['reference'], 'dilution_factor': d} for d in STD[ICPE]['levels']],
                                                   ['line_integrals'])})]),
        ('use-curve nope', [('calibration.select', {'curve_id': 'nope'})]),          # refused UNKNOWN_CURVE
        ('use-curve multi', [('calibration.select', {'curve_id': 'multi'})]),
        ('measure --wait', [('measurement.start', {}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('export json', [('result.export', {'format': 'json'})]),
        ('method repeats=2', []),                                                    # SOURCE_ON (class check)
        ('stop', []),                                                                # nothing running
        ('source off', [('plasma.extinguish', {})]),
        ('method bogus=1', []),                                                      # UNKNOWN_SETTING (usage)
        ('method rf=5kW', []),                                                       # OUT_OF_RANGE (usage)
        ('method repeats=2 rf=1.2kW integration=500ms', [('method.configure', EX(ICPE, 'method.configure', repeats=2, rf_power_w=1200.0, integration_s=0.5))]),
        ('method view=radial', [('method.configure', EX(ICPE, 'method.configure', repeats=2, rf_power_w=1200.0, integration_s=0.5, view='radial'))]),
        ('source on', []),                                                           # POSITIONING: the view mirror is still moving
        ('source on --wait', [('plasma.ignite', {})]),
        ('measure --wait', [('measurement.start', {})]),                             # refused BLANK_REQUIRED (new method, no blank)
        ('measure --slot 2', []),                                                    # NO_SLOTS (usage)
    ]},
    ICPMS: {'prep': stage(ICPMS, 'calibrated', sample=True), 'steps': [
        ('status', []),
        ('measure --wait', [('measurement.start', {}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('records', [('records.list', {})]),
        ('calibrate ms2 --from record_2,record_3,record_4 --reference infant_cal4 --levels 100,10,1',
         [(R, {'record_id': 'record_2'}), (R, {'record_id': 'record_3'}), (R, {'record_id': 'record_4'}),
          ('calibration.fit', {'curve_id': 'ms2', 'accept_same_response_conditions': True,
                               'observations': Obs(3, [{'reference_id': 'infant_cal4', 'dilution_factor': d} for d in (100.0, 10.0, 1.0)], ['channels'])})]),
        ('use-curve ms2', [('calibration.select', {'curve_id': 'ms2'})]),
        ('blank', [('blank.read', {})]),
        ('source off', [('plasma.extinguish', {})]),
        ('source off', []),                                                          # already off
        ('method dwell=50ms sweeps=5', [('method.configure', EX(ICPMS, 'method.configure', dwell_s=0.05, sweeps=5))]),
        ('method masses=63,65', [('method.configure', EX(ICPMS, 'method.configure', dwell_s=0.05, sweeps=5, mass_numbers=[63, 65]))]),   # refused by the instrument
        ('measure', []),                                                             # SOURCE_OFF (class check)
        ('source on', [('plasma.ignite', {})]),
        ('#wait 3', None),
        ('export csv', [('result.export', {'format': 'csv'})]),
        ('stop', []),
    ]},
    TOC: {'prep': stage(TOC, 'calibrated', sample=True), 'steps': [
        ('status', []),
        ('measure --wait', [('measurement.start', {}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('records', [('records.list', {'offset': 0, 'limit': 100}), ('calibration.list', {})]),
        ('calibrate tc2 --from %s:result:2,%s:result:3,%s:result:4 --reference crm_altus_khp_10mg --levels 10,2,1' % (TOC, TOC, TOC),
         [(R, {'record_id': '%s:result:2' % TOC}), (R, {'record_id': '%s:result:3' % TOC}), (R, {'record_id': '%s:result:4' % TOC}),
          ('calibration.fit', {'curve_id': 'tc2', 'channel': 'TC', 'accept_source_channel_transfer': False, 'accept_same_response_conditions': True,
                               'observations': Obs(3, [{'reference_id': 'crm_altus_khp_10mg', 'reference_channel': 'TC', 'dilution_factor': d}
                                                       for d in (10.0, 2.0, 1.0)], ['injection_volume_m3', 'peak_area_s'])})]),
        ('use-curve tc2', [('calibration.read', {'curve_id': 'tc2'}), ('calibration.select', {'channel': 'TC', 'curve_id': 'tc2'})]),
        ('use-curve tc2 --channel IC', [('calibration.select', {'channel': 'IC', 'curve_id': 'tc2'})]),    # refused: curve of channel TC
        ('blank', [('blank.read', {})]),
        ('export csv', [('result.export', {'format': 'csv'})]),
        ('method mode=NPOC dilution=2', [('method.configure', EX(TOC, 'method.configure', mode='NPOC', dilution_factor=2.0))]),
        ('method dilution=1.5', [('method.configure', EX(TOC, 'method.configure', mode='NPOC', dilution_factor=1.5))]),   # refused DILUTION_RANGE
        ('method injection=5mL', []),                                                # OUT_OF_RANGE (usage)
        ('source on', []),                                                           # not supported (furnace)
        ('load x', []),                                                              # not supported
        ('stop', []),
    ]},
    AA: {'prep': stage(AA, 'calibrated', sample=True), 'steps': [
        ('status', []),
        ('measure --wait', [('measurement.start', {}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('records %s:result:2' % AA, [(R, {'record_id': '%s:result:2' % AA})]),
        ('calibrate aa2 --from %s:result:2,%s:result:3,%s:result:4 --reference aj_wastewater_std3_cu --levels 10,2,1' % (AA, AA, AA),
         [(R, {'record_id': '%s:result:2' % AA}), (R, {'record_id': '%s:result:3' % AA}), (R, {'record_id': '%s:result:4' % AA}),
          ('calibration.fit', {'curve_id': 'aa2', 'accept_same_response_conditions': True,
                               'observations': Obs(3, [{'reference_id': 'aj_wastewater_std3_cu', 'dilution_factor': d} for d in (10.0, 2.0, 1.0)],
                                                   ['injection_volume_m3', 'integrated_absorbance_s'])})]),
        ('use-curve aa2', [('calibration.select', {'curve_id': 'aa2'})]),
        ('method element=Zn', []),                                                   # SOURCE_ON (class check)
        ('source off', [('flame.extinguish', {})]),
        ('method repeats=3 flush=300ms', [('method.configure', EX(AA, 'method.configure', repeats=3, flush_s=0.3))]),
        ('source on', [('flame.ignite', {})]),
        ('measure', [('measurement.start', {})]),                                    # refused: the flame is still igniting
        ('#wait 2', None),
        ('blank', [('blank.read', {})]),
        ('export json', [('result.export', {'format': 'json'})]),
        ('load x', []),
        ('stop', []),
    ]},
    EDX: {'prep': stage(EDX, 'calibrated', sample=True), 'steps': [
        ('status', []),
        ('measure --wait', [('measurement.start', {}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('records', [('records.list', {})]),
        ('calibrate x2 --from record_2,record_3,record_4 --reference blackmass_icp_f --levels 0.25,0.5,1',
         [(R, {'record_id': 'record_2'}), (R, {'record_id': 'record_3'}), (R, {'record_id': 'record_4'}),
          ('calibration.fit', {'curve_id': 'x2', 'accept_same_matrix_response': True,
                               'observations': Obs(3, [{'reference_id': 'blackmass_icp_f', 'reference_fraction': f} for f in (0.25, 0.5, 1.0)],
                                                   ['line_observations'])})]),
        ('use-curve x2', [('calibration.select', {'curve_id': 'x2'})]),
        ('blank', [('blank.read', {})]),
        ('method voltage=40kV current=200uA energy_max=40keV', [('method.configure', EX(EDX, 'method.configure', voltage_v=40000.0, current_a=0.0002,
                                                                                        energy_max_j=40 * 1.602176634e-16))]),
        ('method voltage=80kV', []),                                                 # OUT_OF_RANGE (usage)
        ('export csv', [('result.export', {'format': 'csv'})]),
        ('source on', []),                                                           # not supported (tube settings are method keys)
        ('stop', []),
    ]},
    W.MX: {'prep': stage(W.MX, 'calibrated', sample=True), 'steps': [
        ('status', []),
        ('measure --wait', [('measurement.start', {}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('records', [('records.list', {})]),
        ('calibrate aox2 --from record_2,record_3,record_4', [('calibration.fit', {'curve_id': 'aox2', 'record_ids': ['record_2', 'record_3', 'record_4']})]),
        ('calibrate --from record_2,record_3,record_4', []),                         # MISSING_ARGUMENT (curve name)
        ('calibrate aox3 --from record_1,record_3,record_4', [('calibration.fit', {'curve_id': 'aox3', 'record_ids': ['record_1', 'record_3', 'record_4']})]),  # refused: a blank
        ('use-curve aox2', [('calibration.select', {'curve_id': 'aox2'})]),
        ('blank', [('blank.read', {})]),
        ('source off', [('gas.set', {'enabled': False})]),
        ('measure', []),                                                             # NOT_READY (class check)
        ('source on --wait', [('gas.set', {'enabled': True})]),
        ('method integration=90s', [('method.configure', EX(W.MX, 'method.configure', integration_s=90.0))]),
        ('method temp=1200C', []),                                                   # OUT_OF_RANGE (usage)
        ('export csv', [('result.export', {'format': 'csv'})]),
        ('load x', []),
        ('stop', []),
    ]},
    W.DMA: {'prep': stage(W.DMA, 'calibrated'), 'steps': [
        ('status', []),
        ('load soil --slot 5', []),                                                  # MISSING_OPTION --mass
        ('load soil --slot 5 --mass 2g', []),                                        # OUT_OF_RANGE (usage)
        ('load soil --slot 5 --mass 100mg', [('table.set_entry', {'slot_index': 5, 'name': 'soil', 'amount_kg': 0.0001, 'in_statistics': False})]),
        ('measure --wait', []),                                                      # MISSING_OPTION --slot
        ('measure --slot 5 --wait', [('measurement.start', {'slot_index': 5}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('measure --slots 1,5', [('sequence.start', {'slot_indices': [1, 5]})]),     # refused: those boats were measured already
        ('records', [('records.list', {})]),
        ('calibrate hg2 --from record_2,record_3,record_4', [('calibration.fit', {'curve_id': 'hg2', 'record_ids': ['record_2', 'record_3', 'record_4']})]),
        ('use-curve hg2', [('calibration.select', {'curve_id': 'hg2'})]),
        ('blank', [('blank.read', {})]),
        ('method record=1s window=100ms', [('method.configure', EX(W.DMA, 'method.configure', record_s=1.0, window_s=0.1))]),
        ('source off', [('gas.set', {'enabled': False})]),
        ('measure --slot 0', []),                                                    # NOT_READY (class check: oxygen off)
        ('export json', [('result.export', {'format': 'json'})]),
        ('stop', []),
    ]},
    W.VARIO: {'prep': stage(W.VARIO, 'standards'), 'steps': [
        ('status', []),
        ('calibrate --from 1,2,3 --reference oem_sulfanilic_acid', []),              # MISSING_ARGUMENT (element)
        ('calibrate X --from 1,2,3 --reference oem_sulfanilic_acid', []),            # BAD_CHOICE (usage)
        ('calibrate C --from 1,2,9 --reference oem_sulfanilic_acid', [('result.read', {})]),   # refused: slot 9 not in the last sequence
        ('calibrate C --from 1,2,3 --reference oem_sulfanilic_acid',
         [('result.read', {}), ('calibration.fit', {'element': 'C', 'accept_same_method_basis': True,
                                                     'observations': Obs(3, {'reference_id': 'oem_sulfanilic_acid'}, ['sample_mass_kg', 'peak_area_s'])})]),
        ('calibrate N --from 1,2,3 --reference oem_sulfanilic_acid',
         [('result.read', {}), ('calibration.fit', {'element': 'N', 'accept_same_method_basis': True,
                                                     'observations': Obs(3, {'reference_id': 'oem_sulfanilic_acid'}, ['sample_mass_kg', 'peak_area_s'])})]),
        ('measure --wait', []),                                                      # MISSING_OPTION --slots
        ('measure --slots 4 --wait', [('sequence.configure', {'slot_indices': [4]}), ('sequence.start', {}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('blank', [('blank.read', {})]),
        ('export csv', [('result.export', {'format': 'csv'})]),
        ('method mode=CHN', [('method.configure', EX(W.VARIO, 'method.configure', mode='CHN'))]),
        ('use-curve C', []),                                                         # not supported
        ('source on', []),                                                           # not supported
        ('records', []),                                                             # not supported
        ('stop', []),
    ]},
    W.RGA: {'prep': stage(W.RGA, 'calibrated', sample=True), 'steps': [
        ('status', []),
        ('measure --wait', [('scan.start', {}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('records', [('records.list', {})]),
        ('calibrate n2b --from record_2,record_3,record_4', []),                     # MISSING_OPTION --species
        ('calibrate n2b --from record_2,record_3,record_4 --species nitrogen',
         [('calibration.fit', {'curve_id': 'n2b', 'species_id': 'nitrogen', 'record_ids': ['record_2', 'record_3', 'record_4']})]),
        ('use-curve cal1,n2b', [('calibration.select', {'curve_ids': ['cal1', 'n2b']})]),   # refused: two curves for one gas
        ('use-curve n2b', [('calibration.select', {'curve_ids': ['n2b']})]),
        ('blank', [('blank.read', {})]),
        ('source off', [('filament.set', {'enabled': False})]),
        ('method cycles=2 emission=1.5mA', [('method.configure', EX(W.RGA, 'method.configure', cycles=2, emission_current_a=0.0015))]),
        ('source on', [('filament.set', {'enabled': True})]),
        ('export csv', [('result.export', {'format': 'csv'})]),
        ('load gas', []),
        ('stop', []),
    ]},
    W.ORBI: {'prep': stage(W.ORBI, 'calibrated', sample=True), 'steps': [
        ('status', []),
        ('measure --wait', [('scan.start', {}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('records', [('records.list', {})]),
        ('calibrate m2 --from record_2,record_3,record_4', [('calibration.fit', {'curve_id': 'm2', 'record_ids': ['record_2', 'record_3', 'record_4']})]),
        ('use-curve m2', [('calibration.select', {'curve_id': 'm2'})]),
        ('blank', [('blank.read', {})]),
        ('method resolution=60000', [('method.configure', EX(W.ORBI, 'method.configure', resolution=60000))]),
        ('method resolution=50000', []),                                             # BAD_CHOICE (usage)
        ('source off', [('source.set', {'enabled': False})]),
        ('measure', []),                                                             # NOT_READY (class check)
        ('export json', [('result.export', {'format': 'json'})]),
        ('load x', []),
        ('stop', []),
    ]},
    W.LECO: {'prep': stage(W.LECO, 'calibrated', sample=True), 'steps': [
        ('status', []),
        ('measure --wait', [('measurement.start', {}), ('result.read', {})]),
        ('result', [('result.read', {})]),
        ('records', [('records.list', {})]),
        ('calibrate --from record_2,record_3,record_4', []),                         # MISSING_OPTION --elements
        ('calibrate c9 --from record_2,record_3,record_4 --elements O,N,H', []),     # CURVE_NAMED_BY_INSTRUMENT (usage)
        ('calibrate --from record_2,record_3,record_4 --elements O,N,H',
         [('calibration.fit', {'record_ids': ['record_2', 'record_3', 'record_4'], 'elements': ['O', 'N', 'H']})]),
        ('use-curve curve_2', [('calibration.select', {'curve_id': 'curve_2'})]),
        ('measure', []),                                                             # NOT_READY: the crucible is used (class check)
        ('blank', [('blank.read', {})]),
        ('source off', [('gas.set', {'enabled': False})]),
        ('method fusion_power=5kW integration=0.4s', [('method.configure', EX(W.LECO, 'method.configure', fusion_power_w=5000.0, integration_s=0.4))]),
        ('export csv', [('result.export', {'format': 'csv'})]),
        ('load pin', []),                                                            # not supported
        ('stop', []),
    ]},
    W.IRMS: {'prep': stage(W.IRMS, 'ready'), 'steps': [
        ('status', []),
        ('measure --time 600s', [('acquisition.start', {'duration_s': 600.0, 'reference_pulses': [{'gas': 1, 'start_s': 20.0, 'duration_s': 20.0}]})]),
        ('load usgs40 --amount 4umol --delay 100s', [('sample.inject', {'inlet': 1, 'species': 'CO2', 'amount_mol': 4e-06, 'substance_id': 'usgs40', 'delay_s': 100.0})]),
        ('load usgs41 --amount 4umol --delay 250s', [('sample.inject', {'inlet': 1, 'species': 'CO2', 'amount_mol': 4e-06, 'substance_id': 'usgs41', 'delay_s': 250.0})]),
        ('load usgs41', []),                                                         # MISSING_OPTION --amount
        ('method species=N2', [('magnet.set_species', {'species': 'N2'})]),       # refused by the instrument: acquiring (RUN_ACTIVE -> BUSY)
        ('#wait 620', None),
        ('result', [('results.read', {'since_peak': 0})]),
        ('calibrate 13C --from 2', []),                                              # BAD_SETTING (usage)
        ('calibrate 13C --from 2=usgs40,3=usgs41', [('calibration.normalize', {'isotope': '13C', 'standards': [{'peak': 2, 'substance_id': 'usgs40'},
                                                                                                                 {'peak': 3, 'substance_id': 'usgs41'}]})]),
        ('method flow=6mL/min dilution=0.5', [('centrion.set', {'carrier_m3_s': 1e-07, 'dilution': 0.5})]),
        ('method flow=1mL/min', []),                                                 # OUT_OF_RANGE (usage)
        ('method species=N2 amplifier=auto', [('magnet.set_species', {'species': 'N2'}), ('amplifier.configure', {'mode': 'auto'})]),
        ('stop', [('acquisition.stop', {})]),
        ('source off', [('source.set', {'filament': False})]),
        ('measure', []),                                                             # NOT_READY (class check: status not green)
        ('blank', []),                                                               # not supported
        ('use-curve 13C', []),                                                       # not supported
    ]},
    W.SEAL: {'prep': stage(W.SEAL, 'ready'), 'steps': [
        ('status', []),
        ('load river_1 --slot Z:1', []),                                             # UNKNOWN_LOCATION (usage)
        ('load river_1', []),                                                        # MISSING_OPTION --slot
        ('load river_1 --slot A:1', [('sampler.load', {'rack': 'A', 'position': 1, 'item_id': 'river_1', 'volume_m3': 1e-05})]),
        ('measure --wait', []),                                                      # MISSING_OPTION --cups
        ('measure --cups %s --wait --max 2h' % W.SEAL_CUPS,
         [('run.start', {'cups': [{'rack': 'S', 'position': 22, 'type': 'baseline'}, {'rack': 'S', 'position': 1, 'type': 'calibrant'},
                                  {'rack': 'S', 'position': 2, 'type': 'calibrant'}, {'rack': 'S', 'position': 3, 'type': 'calibrant'},
                                  {'rack': 'S', 'position': 4, 'type': 'calibrant'}, {'rack': 'A', 'position': 1, 'type': 'sample'},
                                  {'rack': 'S', 'position': 22, 'type': 'baseline'}], 'sample_s': 60.0, 'wash_s': 30.0}),
          ('data.results', {})]),
        ('result', [('data.results', {})]),
        ('calibrate --fit linear --no-drift', [('data.recalculate', {'fit': 'linear', 'carryover_correction': True, 'drift_correction': False})]),
        ('calibrate --fit cubic', []),                                               # BAD_CHOICE (usage)
        ('source on', []),                                                           # MISSING_CHANNEL (usage)
        ('source on --channel 1', [('lamp.set', {'channel': 'ch1', 'on': True})]),
        ('method method=MT508_A026_nitrate_nitrite --channel 1', [('channel.configure', {'channel': 'ch1', 'method_id': 'MT508_A026_nitrate_nitrite'})]),
        ('stop', []),
        ('blank', []),                                                               # not supported
        ('use-curve x', []),                                                         # not supported
    ]},
}
