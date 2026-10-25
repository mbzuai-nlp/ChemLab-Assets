"""Lowering table of the bioassay class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check refused,
an unsupported verb, a usage error or a dry run); None = not checked.  The lab is the bioassay world (tasks/bioassay_world.py)."""
from .bioassay_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
SPR_CYCLE = {'type': 'sample', 'position': 'R1B1', 'contact_s': 60.0, 'dissociation_s': 60.0, 'flow_rate_m3_s': 5e-10}
ITC_INJ = [{'volume_m3': 4e-10, 'duration_s': 0.8, 'spacing_s': 150.0}] + [{'volume_m3': 2e-9, 'duration_s': 4.0, 'spacing_s': 150.0}] * 4
QPCR_STEPS = [{'kind': 'temperature', 'temperature_k': K0 + 95, 'hold_s': 180.0}, {'kind': 'temperature', 'temperature_k': K0 + 95, 'hold_s': 10.0},
              {'kind': 'temperature', 'temperature_k': K0 + 60, 'hold_s': 30.0, 'plate_read': True}, {'kind': 'goto', 'goto_step': 2, 'cycles': 39},
              {'kind': 'melt', 'start_k': K0 + 65, 'end_k': K0 + 95, 'melt_increment_k': 0.5, 'hold_s': 5.0}]

CASES = {
    'bmg_clariostarplus': {'steps': [
        ('status', []),
        ('load wp', []),                                                       # POWER_OFF (class requirement)
        ('power on', [('power.set', {'on': True})]),                           # shared layer
        ('load wp', []),                                                       # BUSY: starting up
        ('#wait 25', None),
        ('load wq', []),                                                       # UNKNOWN_ITEM (usage)
        ('load wp', [('drawer.set', {'open': True}), ('plate.load', {'plate_id': 'wp'}), ('drawer.set', {'open': False})]),
        ('load wp', []),                                                       # ALREADY_LOADED
        ('start', []),                                                         # NO_METHOD
        ('method mode=fi_endpoint wells=A1:A3 ex=485nm em=525nm',
         [('protocol.configure', {'mode': 'fi_endpoint', 'wells': ['A1:A3'], 'excitation_m': 4.85e-7, 'emission_m': 5.25e-7, 'format': '96'})]),
        ('method mode=fi_endpoint wells=A1 ex=100nm em=525nm', []),           # OUT_OF_RANGE (usage)
        ('method colour=red', []),                                             # UNKNOWN_SETTING (usage)
        ('start --dry-run', []),
        ('start', [('run.start', {})]),
        ('#wait 10', None),
        ('data', [('data.read', {})]),
        ('data --cycle 1 --max 10', [('data.read', {'cycle': 1, 'max_rows': 10})]),
        ('analyze blank blank=B1,B2,B3', [('data.analyze', {'fit': 'none', 'blank_wells': ['B1', 'B2', 'B3']})]),
        ('analyze linear std.A1=0uM std.A2=1uM', [('data.analyze', {'fit': 'linear', 'standards': {'A1': 0.0, 'A2': 1e-3}})]),
        ('analyze cubic', []),                                                 # UNKNOWN_MODEL (usage)
        ('method mode=abs_endpoint wells=A1:A3 wl=450nm cycles=3 interval=60s',
         [('protocol.configure', {'mode': 'abs_endpoint', 'wells': ['A1:A3'], 'wavelengths_m': [4.5e-7], 'cycles': 3, 'cycle_time_s': 60.0,
                                  'format': '96'})]),
        ('start', [('run.start', {})]),
        ('#wait 5', None),
        ('method mode=abs_endpoint wells=A1 wl=450nm', []),                   # RUN_ACTIVE
        ('stop', [('run.stop', {})]),
        ('stop', []),                                                          # NOT_RUNNING
        ('pause on', []),                                                      # not supported
        ('set-temp 37C', [('incubator.set', {'temperature_k': K0 + 37})]),
        ('set-temp 60C', []),                                                  # OUT_OF_RANGE
        ('set-temp off', [('incubator.set', {'temperature_k': None})]),
        ('prime pump=A volume=500uL', [('injector.prime', {'pump': 'A', 'volume_m3': 5e-7})]),   # refused by the instrument: NO_REAGENT
        ('prime pump=A volume=5uL', []),                                       # OUT_OF_RANGE
        ('unload', [('drawer.set', {'open': True}), ('plate.unload', {}), ('drawer.set', {'open': False})]),
        ('unload', []),                                                        # NOTHING_LOADED
        ('power off', [('power.set', {'on': False})]),
    ]},
    'biotek_405ts': {'steps': [
        ('status', []),
        ('start W-Corning_FLAT', [('protocol.run', {'name': 'W-Corning_FLAT'})]),   # refused by the instrument: NO_PLATE
        ('load p9', []),                                                       # UNKNOWN_ITEM
        ('load p1', [('plate.load', {'plate_id': 'p1'})]),
        ('load p1', []),                                                       # ALREADY_LOADED
        ('start W-Corning', []),                                               # UNKNOWN_PROTOCOL (usage)
        ('start', []),                                                         # MISSING_ARGUMENT
        ('start W-Corning_FLAT', [('protocol.run', {'name': 'W-Corning_FLAT'})]),
        ('start W-Corning_FLAT', []),                                          # RUN_ACTIVE
        ('stop', [('run.stop', {})]),
        ('stop', []),                                                          # NOT_RUNNING
        ('start W-Corning_FLAT', [('protocol.run', {'name': 'W-Corning_FLAT'})]),
        ('#wait 40', None),
        ('data', [('measurement.result', {'record_id': 'WS0002'})]),         # the last record (the stopped run's WS0001 is not 'last')
        ('data WS0002', [('measurement.result', {'record_id': 'WS0002'})]),
        ('prime', []),                                                         # PLATE_IN
        ('unload', [('plate.unload', {'plate_id': 'p1'})]),
        ('prime buffer=B volume=100mL', [('prime.run', {'buffer': 'B', 'volume_m3': 1e-4, 'rate': 9})]),   # rate: the contract example
        ('#wait 60', None),
        ('prime volume=1mL', []),                                              # OUT_OF_RANGE
        ('method name=x', []),                                                 # not supported
        ('pause on', []),                                                      # not supported
        ('analyze x', []),                                                     # not supported
        ('set-temp 30C', []),                                                  # not supported
        ('start PRIME_ALL_BUFFRS --dry-run', []),
        ('power off', [('power.set', {'enabled': False})]),
        ('start W-DAY_RINSE', []),                                             # POWER_OFF
    ]},
    'biorad_cfxopus': {'steps': [
        ('status', []),
        ('start', []),                                                         # NOTHING_LOADED
        ('load plate_9', []),                                                  # UNKNOWN_ITEM
        ('load plate_1', [('lid.open', {}), ('plate.load', {'plate_id': 'plate_1'}), ('lid.close', {})]),
        ('start', []),                                                         # NO_METHOD
        ('method name=SYBR volume=20uL lid=105C steps=95C@3min,95C@10s,60C@30s+read,goto2x39,melt65C..95C@0.5C/5s',
         [('protocol.set', {'name': 'SYBR', 'sample_volume_m3': 2e-8, 'lid_temperature_k': K0 + 105, 'steps': QPCR_STEPS})]),
        ('method name=x volume=20uL steps=95C@3min,bogus', []),               # BAD_PROGRAM (usage)
        ('method name=x volume=80uL steps=95C@3min', []),                     # OUT_OF_RANGE
        ('start --scan fret --dry-run', []),
        ('start', [('run.start', {'scan_mode': 'all_channels'})]),
        ('pause on', []),                                                      # lid heating: not running yet
        ('#wait 120', None),
        ('pause on', [('run.pause', {})]),
        ('pause off', [('run.resume', {})]),
        ('pause off', []),                                                     # NOT_PAUSED
        ('stop', [('run.stop', {})]),
        ('data', [('data.amplification', {'run_id': 'R001'})]),
        ('data --wells A1,B1', [('data.amplification', {'run_id': 'R001', 'wells': ['A1', 'B1']})]),
        ('analyze cq', [('analysis.cq', {'run_id': 'R001'})]),
        ('analyze cq threshold_rfu=50', [('analysis.cq', {'run_id': 'R001', 'threshold_rfu': 50.0})]),
        ('analyze bogus', []),                                                 # UNKNOWN_MODEL
        ('unload', [('lid.open', {}), ('plate.remove', {}), ('lid.close', {})]),
        ('unload', []),                                                        # NOTHING_LOADED
        ('prime', []),                                                         # not supported
        ('set-temp 50C', []),                                                  # not supported
    ]},
    'cytiva_biacoret200': {'steps': [
        ('status', []),
        ('start r1 samples=R1B1 contact=60s flow=30uL/min path=1-2 rate=1', []),   # NOTHING_LOADED
        ('load chip1', []),                                                    # MISSING_OPTION --type
        ('load chip1 --type CM5', [('chip.open_port', {}), ('chip.insert', {'chip_type': 'CM5', 'chip_id': 'chip1'}), ('chip.close_cover', {}),
                                   ('chip.dock', {})]),
        ('load chip2 --type CM5', []),                                         # ALREADY_LOADED
        ('prime', [('flow.prime', {})]),
        ('#wait 400', None),
        ('set-temp 25C', [('temperature.set', {'analysis_k': K0 + 25})]),
        ('set-temp 60C', []),                                                  # OUT_OF_RANGE
        ('start r1 samples=R1B1 contact=60s dissociation=60s flow=30uL/min path=1-2 ref=2-1 rate=1 startup=1',
         [('method.run', {'run_id': 'r1', 'data_rate_hz': 1, 'flow_path': '1-2', 'reference_subtraction': '2-1',
                          'cycles': [{'purpose': 'startup', 'commands': [SPR_CYCLE]}, {'purpose': 'sample', 'commands': [SPR_CYCLE]}]})]),
        ('pause on', [('run.pause', {})]),
        ('pause on', []),                                                      # already paused
        ('pause off', [('run.resume', {})]),
        ('#wait 400', None),
        ('data', [('data.report_points', {'run_id': 'r1'})]),
        ('data --cycle 2 --curves 2-1 --max 10', [('data.sensorgram', {'run_id': 'r1', 'cycle': 2, 'curves': ['2-1'], 'max_points': 10})]),
        ('data --cycle 2', []),                                                # MISSING_OPTION --curves
        ('analyze kinetics cycles=2 subtraction=2-1', [('eval.kinetics', {'run_id': 'r1', 'cycles': [2], 'subtraction': '2-1'})]),
        ('analyze affinity cycles=2 subtraction=2-1 concs=1uM', []),           # WRONG_COUNT: affinity needs two or more cycles (usage)
        ('analyze affinity cycles=1,2 subtraction=2-1 concs=0uM,1uM',
         [('eval.affinity', {'run_id': 'r1', 'cycles': [1, 2], 'subtraction': '2-1', 'concentrations_mol_m3': [0.0, 1e-3]})]),
        ('start r2 samples=R1B1 contact=60s flow=30uL/min path=1-2 rate=1',
         [('method.run', {'run_id': 'r2', 'data_rate_hz': 1, 'flow_path': '1-2',
                          'cycles': [{'purpose': 'sample', 'commands': [dict(SPR_CYCLE, dissociation_s=0.0)]}]})]),
        ('#wait 5', None),
        ('stop', [('run.stop', {})]),
        ('stop', []),                                                          # NOT_RUNNING
        ('method contact=60s', []),                                            # not supported
        ('unload', [('chip.eject', {}), ('chip.open_port', {}), ('chip.remove', {}), ('chip.close_cover', {})]),
        ('unload', []),                                                        # NOTHING_LOADED
    ]},
    'malvern_microcalpeaq': {'steps': [
        ('status', []),
        ('start e1', []),                                                      # NO_METHOD
        ('method temp=25C ref=5ucal/s feedback=high stir=750rpm delay=60s injections=0.4uL@0.8s,4x2uL@4s spacing=150s cell=0.1mM syringe=1mM',
         [('method.set', {'temperature_k': K0 + 25, 'reference_power_w': 2.092e-5, 'feedback': 'high', 'stir_rate_hz': 12.5,
                          'initial_delay_s': 60.0, 'injections': ITC_INJ, 'cell_concentration_mol_m3': 0.1, 'syringe_concentration_mol_m3': 1.0})]),
        ('method temp=30C', []),                                               # MISSING_SETTING injections
        ('method temp=200C injections=1uL@2s spacing=150s', []),             # OUT_OF_RANGE
        ('load nothing', []),                                                  # UNKNOWN_ITEM
        ('load edta', [('pipette.move', {'location': 'rest'}), ('cell.load', {'solution_id': 'edta', 'volume_m3': 3e-7, 'rinse': True})]),
        ('start e1', []),                                                      # SYRINGE_NOT_LOADED
        ('load ca --into syringe', [('tube.place', {'solution_id': 'ca', 'volume_m3': 6e-8}), ('fpa.attach', {}), ('syringe.plunger_down', {}),
                                    ('pipette.move', {'location': 'load'}), ('syringe.load', {}), ('pipette.move', {'location': 'rest'}),
                                    ('tube.remove', {}), ('fpa.detach', {}), ('pipette.move', {'location': 'cell'})]),
        ('set-temp 25C', [('temperature.set', {'temperature_k': K0 + 25})]),
        ('start e1', [('run.start', {'experiment_id': 'e1'})]),
        ('start e2', []),                                                      # RUN_ACTIVE
        ('#wait 300', None),
        ('stop', [('instrument.stop', {})]),
        ('stop', []),                                                          # NOT_RUNNING
        ('data', [('data.read', {'experiment_id': 'e1'})]),
        ('data e1 --max 50', [('data.read', {'experiment_id': 'e1', 'max_points': 50})]),
        ('analyze one_set_of_sites exclude=1',                                 # refused by the instrument: NO_DATA (the titration was stopped)
         [('data.analyze', {'experiment_id': 'e1', 'model': 'one_set_of_sites', 'exclude_injections': [1]})]),
        ('analyze bogus', []),                                                 # UNKNOWN_MODEL
        ('pause on', []),                                                      # not supported
        ('prime', []),                                                         # not supported
        ('unload', [('pipette.move', {'location': 'rest'}), ('cell.empty', {})]),
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'bd_facsariaiii': {'steps': [
        ('status', []),
        ('load t1', []),                                                       # STREAM_OFF
        ('prime', [('fluidics.startup', {}), ('stream.set', {'on': True})]),
        ('prime', []),                                                         # ALREADY_STARTED
        ('load t9', []),                                                       # UNKNOWN_ITEM
        ('start r1 --events 100', []),                                         # NOT_ACQUIRING
        ('load t1', [('tube.install', {'tube_id': 't1'}), ('sample.load', {})]),
        ('method voltage.B530_30=520V', [('cytometer.settings', {'voltages': {'B530_30': 520.0}})]),
        ('start r1', []),                                                      # MISSING_OPTION --events
        ('start r1 --events 5000', [('acquisition.record', {'record_id': 'r1', 'events': 5000})]),
        ('#wait 20', None),
        ('data r1', [('statistics.read', {'source': 'r1'})]),
        ('data', [('statistics.read', {'source': 'live'})]),
        ('analyze compensation unstained=r1 controls.B530_30=r1',
         [('compensation.calculate', {'unstained_record': 'r1', 'controls': {'B530_30': 'r1'}})]),     # refused: NO_POSITIVE_POPULATION
        ('set-temp 5C', []),                                                   # NOT_ALLOWED (fixed settings)
        ('set-temp 4C', [('sample.temperature', {'setpoint_k': K0 + 4})]),
        ('set-temp off', [('sample.temperature', {'setpoint_k': None})]),
        ('pause on', []),                                                      # not supported
        ('stop', [('acquisition.set', {'acquiring': False})]),
        ('stop', []),                                                          # NOT_RUNNING
        ('unload', [('sample.unload', {}), ('tube.remove', {})]),
        ('unload', []),                                                        # NOTHING_LOADED
    ]},
}
