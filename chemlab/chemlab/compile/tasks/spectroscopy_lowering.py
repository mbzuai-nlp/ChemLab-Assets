"""Lowering table of the spectroscopy class: per member, verbs in order and the native calls each must make (capability, arguments
in the unit the argument declares - SI for every member of this class), written from the contracts, not from the mapping.
[] = no native call (a class check refused, a usage error, an unsupported verb, a read of state slots, or a wait); None = not
checked (only the analysis steps whose inputs are the measured arrays of the previous run; their state equality is still checked).

Method calls send every argument the capability requires: the contract's documented example method with the given changes
(``ex(slug, cap, **changes)`` reads that example from the contract).

Extra lab entries of the same asset types (``*_s``, ``*_atr``, ``*_irf``, ``*_novt``) start from a stage world (``prep``,
applied identically to the lab and its twin): a blank already recorded by the operator and the sample cell in place, the ATR
accessory installed, the IRF scatterer in the holder, a minispec without its VT accessory.
"""
import copy
import json

from ...hosts import contract_index
from ...labbackend import load_config
from . import spectroscopy_world as W

HALF_PI = 1.5707963267948966
K0 = 273.15
_CI = contract_index()


def contract(slug):
    return json.load(open(_CI.get(slug) or _CI['instrument.' + slug], encoding='utf-8'))


def ex(slug, cap, **changes):
    a = copy.deepcopy(contract(slug)['capabilities'][cap]['example_arguments'])
    a.update(changes)
    return a


EXTRA = {'jasco_j1500_s': 'jasco_j1500', 'jasco_fp8350_s': 'jasco_fp8350', 'jasco_ftir4700_atr': 'jasco_ftir4700',
         'edinburgh_fls1000tcspc_irf': 'edinburgh_fls1000tcspc', 'jasco_fvs6000_s': 'jasco_fvs6000', 'jasco_cpl300_s': 'jasco_cpl300',
         'bruker_magnettechesr5000_s': 'bruker_magnettechesr5000', 'bruker_minispecmq20_novt': 'bruker_minispecmq20'}


def config():
    """all203.json + the spectroscopy default world + the extra entries of the stage worlds."""
    cfg = W.config(load_config())
    by_id = {e['id']: e for e in cfg['instruments']}
    for iid, slug in EXTRA.items():
        e = copy.deepcopy(by_id[slug])
        e['id'] = iid
        e.pop('instance_id', None)
        if iid == 'bruker_minispecmq20_novt':
            e['setup'] = [dict(st, arguments=ex(slug, 'plant.configure', vt_installed=False)) if st.get('capability') == 'plant.configure' else st
                          for st in e['setup']]
        cfg['instruments'].append(e)
    return cfg


# ------------------------------------------------------------------ stage worlds (prep: a LabBackend -> None)
def p_j1500(b):
    W.j1500_blank_recorded(b, 'jasco_j1500_s')


def p_fp8350(b):
    W.jasco_blank_recorded(b, 'jasco_fp8350_s', tag='fps')


def p_esr(b):
    W.jasco_blank_recorded(b, 'bruker_magnettechesr5000_s', tag='esrs')


def p_ftir_atr(b):
    w = W.World(b, 'jasco_ftir4700_atr', 'atr')
    w.op('lid.set_angle', {'angle_rad': HALF_PI})
    w.op('accessory.install', {'mode': 'atr'})
    w.op('lid.set_angle', {'angle_rad': 0.0})


def p_fls_irf(b):
    w = W.World(b, 'edinburgh_fls1000tcspc_irf', 'irf')
    w.op('power.set', {'enabled': False})
    w.ev('sample.cover', {'opened': True})
    w.ev('sample.load', W.FLS_SCATTERER)
    w.ev('sample.cover', {'opened': False})
    w.op('power.set', {'enabled': True})
    w.ev('source.arm', {'enabled': True})


def p_fvs(b):
    iid = 'jasco_fvs6000_s'
    w = W.World(b, iid, 'fvss')
    w.op('power.set', {'enabled': True})
    w.op('method.configure', ex('jasco_fvs6000', 'method.configure'))
    w.op('blank.start', {})
    w.until(lambda v: v.get('scan.phase') != 'running', 3600, 1.0)
    W.swap_cover_cell(b, iid, ex('jasco_fvs6000', 'sample.load'), tag='fvss2')


def p_cpl(b):
    iid = 'jasco_cpl300_s'
    w = W.World(b, iid, 'cpls')
    w.op('power.set', {'enabled': True})
    w.op('lamp.set', {'enabled': True})
    w.until(lambda v: v.get('lamp.ready'), 600, 1.0)
    w.op('method.configure', ex('jasco_cpl300', 'method.configure'))
    w.op('blank.start', {})
    w.until(lambda v: v.get('scan.phase') != 'running', 3600, 1.0)
    W.swap_cover_cell(b, iid, ex('jasco_cpl300', 'sample.load'), tag='cpls2')


# ------------------------------------------------------------------ cases
J15_M = dict(start_m=2.35e-07, stop_m=1.9e-07, interval_m=1e-09, accumulations=2)
FP_M = dict(mode='emission', excitation_m=4.8e-07, start_m=5e-07, stop_m=6.2e-07, interval_m=2e-09)
IR_M = {'start_wavenumber_m_inv': 40000.0, 'stop_wavenumber_m_inv': 400000.0, 'resolution_m_inv': 400.0, 'scan_count': 16,
        'output_mode': 'absorbance'}
HEL_DELAYS = [-1e-12, 0.0, 1e-12, 2e-12, 5e-12, 1e-11, 2e-11, 5e-11, 1e-10, 2e-10, 5e-10, 1e-09]
HEL_DELAY_TEXT = ','.join(['-1ps', '0ps', '1ps', '2ps', '5ps', '10ps', '20ps', '50ps', '100ps', '200ps', '500ps', '1000ps'])
MQ_DELAYS = [0.001, 0.01, 0.05, 0.1, 0.2, 0.4, 0.8, 1.2, 2.0]

CASES = {
    'hach_dr6000': {'steps': [
        ('status', []),
        ('method from=400nm to=700nm step=2nm', [('program.start', {'kind': 'scan'}),
                                                 ('program.options', {'settings': {'start_m': 4e-07, 'stop_m': 7e-07, 'step_m': 2e-09}})]),
        ('load blank_01', []),                                                   # LID_CLOSED
        ('open lid', [('lid.set_angle', {'angle_rad': HALF_PI})]),
        ('load blank_01', [('sample.load', {'sample_id': 'blank_01'})]),
        ('load sample_01', []),                                                  # OCCUPIED
        ('blank', []),                                                           # LID_OPEN
        ('close', [('lid.set_angle', {'angle_rad': 0.0})]),
        ('measure', []),                                                         # BLANK_REQUIRED (no zero)
        ('blank', [('reading.zero', {})]),
        ('open', [('lid.set_angle', {'angle_rad': HALF_PI})]),
        ('unload', [('sample.unload', {})]),
        ('load sample_01', [('sample.load', {'sample_id': 'sample_01'})]),
        ('close', [('lid.set_angle', {'angle_rad': 0.0})]),
        ('measure', [('reading.read', {})]),
        ('spectrum', []),                                                        # reads dr.scan / dr.result
        ('spectrum --save-to dr_scan.json', []),
        ('method wavelength=520nm', [('program.start', {'kind': 'single'}), ('program.options', {'settings': {'wavelength_m': 5.2e-07}})]),
        ('measure', []),                                                         # BLANK_REQUIRED (new mode)
        ('method wavelength=150nm', []),                                         # OUT_OF_RANGE (usage)
        ('source on', []),                                                       # WHICH_LAMP (usage)
        ('source on --lamp vis', [('lamp.set', {'lamp': 'vis', 'on': True})]),
        ('stop', []),                                                            # not supported
        ('analyze conc', []),                                                    # not supported
        ('method program=490', [('program.start', {'kind': 'stored', 'number': 490})]),
        ('method from=400nm to=500nm step=1nm --dry-run', []),
        ('power off', [('power.set', {'enabled': False})]),
        ('method wavelength=520nm', []),                                         # POWER_OFF
    ]},
    'jasco_j1500': {'steps': [
        ('status', []),
        ('power on', [('power.set', {'enabled': True})]),
        ('source on', []),                                                       # PURGE_NOT_READY
        ('purge 50L/min', []),                                                   # OUT_OF_RANGE (usage)
        ('purge 5L/min --wait', [('purge.set', {'flow_std_m3_s': 5e-3 / 60})]),
        ('source on --wait', [('lamp.set', {'enabled': True})]),
        ('blank', []),                                                           # LID_OPEN
        ('close lid', [('lid.set', {'opened': False})]),
        ('open shutter', []),                                                    # UNKNOWN_PART: acquisitions open the shutter themselves
        ('blank', []),                                                           # NO_METHOD
        ('method from=100nm', []),                                               # OUT_OF_RANGE (usage)
        ('method from=260nm', [('method.configure', ex('jasco_j1500', 'method.configure', start_m=2.6e-07))]),
        ('blank', [('acquisition.start', {'kind': 'blank'})]),                   # TEMPLATE_RANGE (runtime)
        ('method from=235nm to=190nm step=1nm scans=2', [('method.configure', ex('jasco_j1500', 'method.configure', **J15_M))]),
        ('measure', []),                                                         # BLANK_REQUIRED
        ('blank --wait', [('acquisition.start', {'kind': 'blank'})]),
        ('measure', [('acquisition.start', {'kind': 'sample'})]),                # the blank cell is in: WRONG_CELL (runtime)
        ('spectrum', [('spectrum.read', {})]),
        ('analyze structure', []),                                               # MISSING_SETTING (usage)
        ('analyze structure conc=0.2mM path=1mm', [('structure.fit', {'residue_concentration_mol_m3': 0.2, 'path_length_m': 0.001})]),
        ('blank', [('acquisition.start', {'kind': 'blank'})]),
        ('stop', [('acquisition.stop', {})]),
        ('stop', []),                                                            # nothing running
        ('load blank_01', []),                                                   # not supported (operator places cells)
        ('set-temp 25C', []),                                                    # not supported
        ('blank --dry-run', []),
        ('measure --dry-run', []),                                               # BLANK_REQUIRED, checked in the dry run too
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'jasco_j1500_s': {'prep': p_j1500, 'steps': [
        ('status', []),
        ('measure --wait', [('acquisition.start', {'kind': 'sample'})]),
        ('spectrum --save-to cd.json', [('spectrum.read', {})]),
        ('analyze structure conc=0.2mM path=1mm', [('structure.fit', {'residue_concentration_mol_m3': 0.2, 'path_length_m': 0.001})]),
        ('method from=235nm', [('method.configure', ex('jasco_j1500', 'method.configure', start_m=2.35e-07))]),
        ('measure', []),                                                         # BLANK_REQUIRED (new method)
    ]},
    'jasco_fp8350': {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('source on --wait', [('lamp.set', {'enabled': True})]),
        ('close', [('lid.set', {'opened': False})]),
        ('method mode=emission ex=480nm from=500nm to=620nm step=2nm', [('method.configure', ex('jasco_fp8350', 'method.configure', **FP_M))]),
        ('method ex-bw=3nm', []),                                                # BAD_CHOICE (enum of slit widths)
        ('set-temp 30C', []),                                                    # NO_ACCESSORY
        ('blank --wait', [('acquisition.start', {'kind': 'blank'})]),
        ('measure', [('acquisition.start', {'kind': 'sample'})]),                # WRONG_CELL (runtime)
        ('spectrum', [('spectrum.read', {})]),
        ('analyze quantify conc=0.1uM,0.2uM,0.4uM signal=1200,2350,4800 unknown=3000 same-method=yes',
         [('quantify.fit', {'concentrations_mol_m3': [1e-4, 2e-4, 4e-4], 'intensities': [1200.0, 2350.0, 4800.0], 'unknown_intensity': 3000.0,
                            'accept_same_method': True})]),
        ('purge 5L/min', []),                                                    # not supported
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'jasco_fp8350_s': {'prep': p_fp8350, 'steps': [
        ('measure --wait', [('acquisition.start', {'kind': 'sample'})]),
        ('spectrum', [('spectrum.read', {})]),
        ('method from=520nm to=600nm', [('method.configure', ex('jasco_fp8350', 'method.configure', start_m=5.2e-07, stop_m=6e-07))]),
        ('measure', []),                                                         # BLANK_REQUIRED
    ]},
    'jasco_ftir4700': {'steps': [
        ('method from=400cm-1 to=4000cm-1', []),                                 # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('method from=400cm-1 to=4000cm-1 resolution=4cm-1', [('method.configure', IR_M)]),
        ('method resolution=0.1cm-1', []),                                       # OUT_OF_RANGE (usage)
        ('method mode=raman', []),                                               # BAD_CHOICE (usage)
        ('load ethanol_01', []),                                                 # LID_CLOSED
        ('open lid', [('lid.set_angle', {'angle_rad': HALF_PI})]),
        ('load ethanol_01', []),                                                 # NO_ACCESSORY
        ('blank', []),                                                           # LID_OPEN
        ('close', [('lid.set_angle', {'angle_rad': 0.0})]),
        ('blank', []),                                                           # NO_ACCESSORY
        ('measure', []),                                                         # NO_SAMPLE
        ('spectrum', []),                                                        # NO_RESULT (reads)
        ('source on', []), ('stop', []), ('analyze peaks', []),                  # not supported
    ]},
    'jasco_ftir4700_atr': {'prep': p_ftir_atr, 'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('method from=400cm-1 to=4000cm-1', [('method.configure', IR_M)]),
        ('blank', [('background.acquire', {})]),
        ('open', [('lid.set_angle', {'angle_rad': HALF_PI})]),
        ('load ethanol_01', [('sample.load', {'sample_id': 'ethanol_01'})]),
        ('close', [('lid.set_angle', {'angle_rad': 0.0})]),
        ('blank', []),                                                           # SAMPLE_IN
        ('measure', [('measure', {})]),
        ('spectrum', []),
        ('spectrum --save-to ir.json', []),
        ('unload', []),                                                          # LID_CLOSED
        ('open', [('lid.set_angle', {'angle_rad': HALF_PI})]),
        ('unload', [('sample.unload', {})]),
        ('method scans=32', [('method.configure', dict(IR_M, scan_count=32))]),  # unchanged settings keep their values
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'jasco_nrs5500': {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('source on', []),                                                       # LID_OPEN (door)
        ('close door', [('door.set', {'opened': False})]),
        ('source on', [('laser.set', {'enabled': True})]),
        ('measure', []),                                                         # NO_METHOD
        ('method from=200cm-1 to=1100cm-1', [('method.configure', ex('jasco_nrs5500', 'method.configure', start_m_inv=20000.0,
                                                                         stop_m_inv=110000.0))]),
        ('method power=100mW', []),                                              # OUT_OF_RANGE (usage)
        ('measure --wait', [('acquisition.start', {})]),
        ('spectrum', [('result.read', {})]),
        ('analyze peaks', None),                                                 # result.read + spectrum.peaks (measured arrays)
        ('analyze peaks prominence=-1/s', []),                                   # OUT_OF_RANGE (usage)
        ('blank', []),                                                           # not supported
        ('open door', [('door.set', {'opened': True})]),
        ('measure', []),                                                         # LID_OPEN
        ('stop', []),                                                            # nothing running
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'applied_sx20': {'steps': [
        ('measure', []),                                                         # POWER_OFF
        ('method wavelength=480nm', [('method.configure', ex('applied_sx20', 'method.configure', wavelength_m=4.8e-07))]),
        ('power on', [('power.set', {'enabled': True})]),
        ('method mode=fluorescence', [('method.configure', ex('applied_sx20', 'method.configure', mode='fluorescence'))]),  # POWER_ON
        ('measure --wait', [('acquisition.start', {})]),
        ('spectrum', [('result.read', {})]),
        ('analyze kinetics', None),                                              # result.read + kinetics.fit (measured trace)
        ('measure', []),                                                         # NEEDS_FLUSH
        ('stop', []),
        ('open', []), ('blank', []), ('set-temp 25C', []),                       # not supported
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'edinburgh_fls1000tcspc': {'steps': [
        ('power on', []),                                                        # already on (the operator armed the laser)
        ('measure', []),                                                         # NO_METHOD
        ('method em=520nm', [('method.configure', ex('edinburgh_fls1000tcspc', 'method.configure', emission_wavelength_m=5.2e-07))]),
        ('method ex=450nm', [('method.configure', ex('edinburgh_fls1000tcspc', 'method.configure', excitation_wavelength_m=4.5e-07))]),
        # INSTALLED_SOURCE_MISMATCH (runtime): only the installed 405 nm laser
        ('method em=520nm', [('method.configure', ex('edinburgh_fls1000tcspc', 'method.configure', emission_wavelength_m=5.2e-07))]),
        ('blank', [('irf.start', {})]),                                          # REFERENCE_KIND: the decay sample is in
        ('measure --wait', [('acquisition.start', {})]),
        ('spectrum --save-to decay.json', [('result.read', {})]),
        ('analyze lifetime tau=4ns', []),                                        # MISSING_SETTING irf
        ('analyze lifetime irf=decay.json tau=4ns', None),                       # IRF_GRID_MISMATCH (a decay is no IRF)
        ('source on', []),                                                       # not supported
        ('power off', [('power.set', {'enabled': False})]),
        ('measure', []),                                                         # POWER_OFF
    ]},
    'edinburgh_fls1000tcspc_irf': {'prep': p_fls_irf, 'steps': [
        ('method em=405nm', [('method.configure', ex('edinburgh_fls1000tcspc', 'method.configure', emission_wavelength_m=4.05e-07))]),
        ('blank --wait', [('irf.start', {})]),
        ('spectrum --save-to irf.json', [('result.read', {})]),
        ('measure', [('acquisition.start', {})]),                                # REFERENCE_KIND: the scatterer is in
    ]},
    'ultrafast_helios': {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('measure', [('acquisition.start', {})]),                                # no method yet: SAMPLE_METHOD_REQUIRED (runtime)
        ('method delays=%s' % HEL_DELAY_TEXT, [('method.configure', ex('ultrafast_helios', 'method.configure', delay_sequence_s=HEL_DELAYS))]),
        ('method laser=400nm', [('method.configure', ex('ultrafast_helios', 'method.configure', pump_wavelength_m=4e-07))]),  # EXTERNAL_LASER
        ('measure --wait', [('acquisition.start', {})]),
        ('spectrum', [('result.read', {})]),
        ('analyze kinetics tau=150ps', []),                                      # MISSING_SETTING at
        ('analyze kinetics at=600nm tau=150ps', None),                           # result.read + kinetics.fit
        ('blank', []), ('open', []),                                             # not supported
        ('stop', []),
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'jasco_fvs6000': {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('method from=1500cm-1 to=1800cm-1', [('method.configure', ex('jasco_fvs6000', 'method.configure', wavenumber_start_m_inv=150000.0,
                                                                         wavenumber_stop_m_inv=180000.0))]),
        ('set-temp 120C', []),                                                   # OUT_OF_RANGE (usage)
        ('set-temp 30C --wait', [('temperature.set', {'target_k': K0 + 30})]),
        ('measure', []),                                                         # BLANK_REQUIRED
        ('blank --wait', [('blank.start', {})]),
        ('measure', [('acquisition.start', {})]),                                # WRONG_SAMPLE_KIND: the blank cell is in
        ('spectrum', [('result.read', {})]),
        ('source on', []),                                                       # not supported
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'jasco_fvs6000_s': {'prep': p_fvs, 'steps': [
        ('measure --wait', [('acquisition.start', {})]),
        ('spectrum', [('result.read', {})]),
        ('analyze compare reference=1,2,3', None),                               # ANALYSIS_INPUT (length mismatch)
    ]},
    'jasco_cpl300': {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('blank', []),                                                           # SOURCE_NOT_READY
        ('source on --wait', [('lamp.set', {'enabled': True})]),
        ('method em=520nm', [('method.configure', ex('jasco_cpl300', 'method.configure', emission_m=5.2e-07))]),
        ('blank --wait', [('blank.start', {})]),
        ('measure', [('acquisition.start', {})]),                                # WRONG_SAMPLE_KIND
        ('spectrum', [('result.read', {})]),
        ('analyze glum left=1.02 right=0.98 counts=50000', [('glum.compute', {'left_relative': 1.02, 'right_relative': 0.98,
                                                                               'net_counts': 50000.0, 'minimum_net_counts': 25})]),
        ('open shutter', [('shutter.set', {'opened': True})]),                   # the cover is the operator's; the shutter is not
        ('close', [('shutter.set', {'opened': False})]),
        ('stop', []),
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'jasco_cpl300_s': {'prep': p_cpl, 'steps': [
        ('measure --wait', [('acquisition.start', {})]),
        ('spectrum --save-to cpl.json', [('result.read', {})]),
    ]},
    'magritek_spinsolve80': {'steps': [
        ('measure', []),                                                         # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('measure', []),                                                         # NO_SAMPLE
        ('load ethanol_01', [('sample.insert', {'sample_id': 'ethanol_01'})]),
        ('load ethanol_01', []),                                                 # OCCUPIED
        ('measure', []),                                                         # NO_METHOD
        ('method nucleus=1H scans=4', [('acquisition.configure', ex('magritek_spinsolve80', 'acquisition.configure', scans=4))]),
        ('measure', [('acquisition.start', {})]),                                # signals outside the window (runtime)
        ('method points=512', [('acquisition.configure', ex('magritek_spinsolve80', 'acquisition.configure', scans=4, points=512))]),
        ('measure', [('acquisition.start', {})]),
        ('#wait 10', None),
        ('spectrum', [('nmr.read_spectrum', {})]),
        ('analyze integrate from=-400Hz to=-350Hz', [('nmr.integrate', {'frequency_min_hz': -400.0, 'frequency_max_hz': -350.0})]),
        ('analyze integrate from=-100Hz', []),                                   # MISSING_SETTING
        ('stop', []),                                                            # completed: nothing running
        ('measure', [('acquisition.start', {})]),
        ('stop', [('acquisition.stop', {})]),
        ('unload', [('sample.remove', {})]),
        ('blank', []),                                                           # not supported
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'bruker_magnettechesr5000': {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('source on', []),                                                       # LID_OPEN
        ('close', [('lid.set', {'opened': False})]),
        ('source on', []),                                                       # NO_METHOD
        ('method center=338.4mT width=16mT', [('method.configure', ex('bruker_magnettechesr5000', 'method.configure', field_center_t=0.3384,
                                                                          sweep_width_t=0.016))]),
        ('method center=700mT', []),                                             # OUT_OF_RANGE (usage)
        ('source on', [('microwave.set', {'enabled': True})]),
        ('set-temp 30C --wait', [('thermal.set_temperature', {'temperature_k': K0 + 30})]),
        ('blank --wait', [('acquisition.start', {'kind': 'blank'})]),            # REFERENCE_TEMPERATURE_MISMATCH (runtime)
        ('set-temp 25C --wait', [('thermal.set_temperature', {'temperature_k': K0 + 25})]),
        ('blank --wait', [('acquisition.start', {'kind': 'blank'})]),
        ('measure', []),                                                         # SOURCE_OFF: the bridge goes off after a scan
        ('source on', [('microwave.set', {'enabled': True})]),
        ('measure', [('acquisition.start', {'kind': 'sample'})]),                # SAMPLE_KIND_MISMATCH (runtime)
        ('spectrum', [('spectrum.read', {})]),
        ('stop', []),
        ('load sample_01', []),                                                  # not supported
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'bruker_magnettechesr5000_s': {'prep': p_esr, 'steps': [
        ('measure --wait', [('acquisition.start', {'kind': 'sample'})]),
        ('spectrum --save-to epr.json', [('spectrum.read', {})]),
        ('analyze g-factor', None),                                              # spectrum.read + spectrum.analyze (measured trace)
    ]},
    'bruker_minispecmq20': {'steps': [
        ('set-temp 30C', [('thermal.set_temperature', {'temperature_k': K0 + 30})]),
        ('power on', [('power.set', {'enabled': True})]),
        ('measure', []),                                                         # LID_OPEN
        ('close', [('lid.set', {'opened': False})]),
        ('measure', []),                                                         # NO_METHOD
        ('method sequence=inversion_recovery delays=1ms,10ms,50ms,100ms,200ms,400ms,800ms,1200ms,2000ms points=9',
         [('method.configure', ex('bruker_minispecmq20', 'method.configure', delays_s=MQ_DELAYS, points=9))]),
        ('measure', [('acquisition.start', {})]),                                # 30 °C: TEMPLATE_TEMPERATURE_MISMATCH (runtime)
        ('set-temp 37C --wait', [('thermal.set_temperature', {'temperature_k': K0 + 37})]),
        ('measure --wait', [('acquisition.start', {})]),
        ('spectrum', [('signal.read', {})]),
        ('analyze relaxation', [('relaxation.fit', {})]),
        ('blank', []),                                                           # not supported
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'bruker_minispecmq20_novt': {'steps': [
        ('set-temp 30C', []),                                                    # NO_ACCESSORY (no VT accessory)
        ('status', []),
    ]},
}
