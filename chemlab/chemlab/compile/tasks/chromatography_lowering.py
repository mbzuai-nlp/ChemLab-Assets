"""Lowering table of the chromatography class: per member, verbs in order and the native calls (capability, arguments in the unit
the argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, a usage error, an unsupported verb, a read of slots or a wait); None = not checked.  The labs are built with the class
world (tasks/chromatography_world.py).  `power` / `open` / `close` are shared-layer verbs, used here only to reach a state."""
from .chromatography_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
ML_MIN = 1e-6 / 60
STEPS_CE = ('[{"type":"rinse","inlet":"BI:A1","outlet":"BO:A1","pressure_pa":137895.14586336,"duration_s":120.0},'
            '{"type":"inject","injection_mode":"pressure","inlet":"SI:A1","outlet":"BO:B1","pressure_pa":3447.378646584,"duration_s":5.0},'
            '{"type":"separate","inlet":"BI:B1","outlet":"BO:B1","control":"voltage","voltage_v":25000.0,"current_limit_a":0.0003,'
            '"ramp_s":10.0,"duration_s":600.0}]')
CE_STEPS = [{'type': 'rinse', 'inlet': 'BI:A1', 'outlet': 'BO:A1', 'pressure_pa': 137895.14586336, 'duration_s': 120.0},
            {'type': 'inject', 'injection_mode': 'pressure', 'inlet': 'SI:A1', 'outlet': 'BO:B1', 'pressure_pa': 3447.378646584, 'duration_s': 5.0},
            {'type': 'separate', 'inlet': 'BI:B1', 'outlet': 'BO:B1', 'control': 'voltage', 'voltage_v': 25000.0, 'current_limit_a': 0.0003,
             'ramp_s': 10.0, 'duration_s': 600.0}]
AF4_STEPS = [{'mode': 'elution', 'duration_s': 60.0, 'cross_flow_start_m3_s': 2 * ML_MIN},
             {'mode': 'focus', 'duration_s': 60.0, 'cross_flow_start_m3_s': 2 * ML_MIN},
             {'mode': 'focus_inject', 'duration_s': 120.0, 'cross_flow_start_m3_s': 2 * ML_MIN},
             {'mode': 'focus', 'duration_s': 120.0, 'cross_flow_start_m3_s': 2 * ML_MIN},
             {'mode': 'elution', 'duration_s': 1500.0, 'cross_flow_start_m3_s': 2 * ML_MIN, 'cross_flow_end_m3_s': 0.1 * ML_MIN, 'profile': 'linear'},
             {'mode': 'elution', 'duration_s': 300.0, 'cross_flow_start_m3_s': 0.0}]
QUANT = 'quantify --standards 0.1mM=0.01,0.5mM=0.05 --window 150s..220s'


CASES = {
    'agilent_hplc1260': {'steps': [
        ('status', []),
        ('method flow=0.5mL/min', []),                                          # POWER_OFF (class check)
        ('flow on', []),                                                        # POWER_OFF
        ('run', []),                                                            # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),                       # shared verb, to reach the state
        ('method flow=0.5mL/min run=2min gradient=0:5%,1min:95%', [('chromatography.set_method', {
            'flow_m3_s': 0.5 * ML_MIN, 'injection_volume_m3': 1e-08, 'column_temperature_K': 303.15, 'pressure_limit_Pa': 6e7,
            'wavelength_m': 2.54e-07, 'gradient': [{'time_s': 0.0, 'fraction_b': 0.05}, {'time_s': 60.0, 'fraction_b': 0.95}],
            'run_duration_s': 120.0, 'sample_interval_s': 1.0, 'integration_method': 'trapezoidal'})]),
        ('method column-temp=35C inj=5uL', [('chromatography.set_method', {
            'flow_m3_s': 0.5 * ML_MIN, 'injection_volume_m3': 5e-09, 'column_temperature_K': K0 + 35, 'pressure_limit_Pa': 6e7,
            'wavelength_m': 2.54e-07, 'gradient': [{'time_s': 0.0, 'fraction_b': 0.05}, {'time_s': 60.0, 'fraction_b': 0.95}],
            'run_duration_s': 120.0, 'sample_interval_s': 1.0, 'integration_method': 'trapezoidal'})]),
        ('method flow=99mL/min', []),                                           # BAD_SETTING (above the contract maximum)
        ('method run=30s', []),                                                 # BAD_SETTING: gradient ends after the run
        ('method foo=1', []),                                                   # UNKNOWN_SETTING
        ('run', []),                                                            # NO_SAMPLE
        ('load mix1', [('door.set_angle', {'angle_rad': 1.5707963267948966}), ('sample.load', {'sample_id': 'mix1'}),
                       ('door.set_angle', {'angle_rad': 0.0})]),
        ('load mix1', []),                                                      # SAMPLE_PRESENT
        ('run', []),                                                            # FLOW_OFF
        ('flow on', [('mobile_phase.set_available', {'available': True})]),
        ('flow on', []),                                                        # already on
        ('run --dry-run', []),
        ('run', [('chromatography.measure', {})]),
        ('method flow=1mL/min', []),                                            # BUSY
        ('#wait 30', None),
        ('chromatogram', [('chromatography.get_chromatogram', {})]),
        ('stop', [('chromatography.stop', {})]),
        ('stop', []),                                                           # NOT_RUNNING
        (QUANT, []),                                                            # not supported
        ('set-temp 40C', []),                                                   # not supported
        ('flow off', [('mobile_phase.set_available', {'available': False})]),
    ]},
    'agilent_gc8890b': {'steps': [
        ('status', []),
        ('method oven=80C:1min..200C@30C/min:1min inlet=250C split=20 flow=1.2mL/min det=300C', [
            ('thermal.set_temperature', {'zone': 'oven', 'temperature_K': K0 + 80}),
            ('method.configure', {'component': 'oven', 'settings': {'initial_time_s': 60.0, 'ramp_1_rate_K_s': 0.5, 'ramp_1_final_K': K0 + 200,
                                                                    'ramp_1_final_time_s': 60.0, 'ramp_2_rate_K_s': 0.0}}),
            ('thermal.set_temperature', {'zone': 'inlet', 'temperature_K': K0 + 250}),
            ('thermal.set_temperature', {'zone': 'fid', 'temperature_K': K0 + 300}),
            ('method.configure', {'component': 'inlet', 'settings': {'split_ratio': 20.0}}),
            ('method.configure', {'component': 'column', 'settings': {'flow_m3_s': 1.2 * ML_MIN}})]),
        ('method oven=80C..500C@10C/min', []),                                  # OUT_OF_RANGE: above the oven range
        ('method inlet-mode=splitless', [('method.configure', {'component': 'inlet', 'settings': {'mode': 'Splitless'}})]),
        ('method oven=80C --dry-run', []),
        ('load mix1', [('lid.set_angle', {'angle_rad': 1.9198621771937625}), ('sample.load', {'sample_id': 'mix1'}),
                       ('lid.set_angle', {'angle_rad': 0.0})]),
        ('run', []),                                                            # NOT_READY (zones heating)
        ('run --wait-ready 20min', [('run.prep', {}), ('run.start', {})]),
        ('#wait 60', None),
        ('stop', [('run.stop', {})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('chromatogram', []),                                                   # read of gc.last_run
        ('set-temp 260C --zone inlet', [('thermal.set_temperature', {'zone': 'inlet', 'temperature_K': K0 + 260})]),
        ('set-temp 900C', []),                                                  # OUT_OF_RANGE
        ('set-temp 100C --zone column', []),                                    # UNKNOWN_ZONE
        ('flow off', [('method.configure', {'component': 'column', 'settings': {'on': False}})]),
        ('flow on', [('method.configure', {'component': 'column', 'settings': {'on': True}})]),
        (QUANT, []),                                                            # not supported
    ]},
    'agilent_7890b_ssl_fid': {'steps': [
        ('run', []),                                                            # NO_PROGRAM (oven program length 0)
        ('method oven=80C:1min..200C@30C/min:1min inlet=250C det=300C split=50 inlet-mode=split flow=1.2mL/min', [
            ('thermal.set_temperature', {'zone': 'oven', 'temperature_k': K0 + 80}),
            ('oven.set_initial_time', {'time_s': 60.0}),
            ('oven.set_ramp', {'index': 1, 'rate_k_s': 0.5, 'final_k': K0 + 200, 'final_time_s': 60.0}),
            ('oven.set_ramp', {'index': 2, 'rate_k_s': 0.0}),
            ('thermal.set_temperature', {'zone': 'inlet', 'temperature_k': K0 + 250}),
            ('thermal.set_temperature', {'zone': 'detector', 'temperature_k': K0 + 300}),
            ('inlet.configure', {'split_ratio': 50.0, 'mode': 'split'}),
            ('column.configure', {'flow_m3_s': 1.2 * ML_MIN})]),
        ('method oven=80C..900C@10C/min', []),                                  # OUT_OF_RANGE
        ('method purge_time_s=60', [('inlet.configure', {'purge_time_s': 60.0})]),   # native name, bare number in the declared unit
        ('run --wait-ready 20min', [('sample.aspirate', {'fraction': 0.01}), ('run.start', {}), ('syringe.insert', {}),
                                    ('syringe.depress', {'fraction': 1.0}), ('syringe.retract', {})]),
        ('#wait 60', None),
        ('run', []),                                                            # BUSY
        ('stop', [('run.stop', {})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('chromatogram', []),                                                   # not supported
        ('load vial', []),                                                      # not supported
        (QUANT, []),                                                            # not supported
        ('flow off', [('inlet.set_gas', {'on': False})]),
        ('flow on', [('inlet.set_gas', {'on': True})]),
        ('set-temp 260C --zone inlet', [('thermal.set_temperature', {'zone': 'inlet', 'temperature_k': K0 + 260})]),
        ('set-temp 60C --zone column', []),                                     # UNKNOWN_ZONE
    ]},
    'agilent_1260infinity': {'steps': [
        ('run --pos 1:1', []),                                                  # NO_METHOD
        ('method flow=1.5mL/min column-temp=40C gradient=0:2%,1min:2%,5min:45%,6min:45% run=7min wl=254nm', [('method.configure', {
            'flow_m3_s': 1.5 * ML_MIN, 'modifier_channel': 'B1',
            'program': [{'time_s': 0.0, 'modifier_fraction': 0.02}, {'time_s': 60.0, 'modifier_fraction': 0.02},
                        {'time_s': 300.0, 'modifier_fraction': 0.45}, {'time_s': 360.0, 'modifier_fraction': 0.45}],
            'bpr_pa': 1.3e7, 'bpr_temperature_k': 333.15, 'column_temperature_k': K0 + 40, 'column_position': 1, 'injection_volume_m3': 2e-09,
            'wavelength_m': 2.54e-07, 'run_time_s': 420.0, 'data_rate_hz': 5.0})]),
        ('run --pos 1:1', []),                                                  # FLOW_OFF
        ('flow on', [('pump.set', {'enabled': True})]),
        ('run', []),                                                            # MISSING_OPTION --pos
        ('run --pos 9:1', []),                                                  # OUT_OF_RANGE (drawers 1-4)
        ('run --pos 1:1', []),                                                  # NOT_READY (flow / back pressure settling)
        ('run --pos 1:1 --wait-ready 10min', [('run.start', {'drawer': 1, 'position': 1})]),
        ('#wait 30', None),
        ('stop', [('run.abort', {})]),
        ('chromatogram', [('chromatogram.read', {})]),
        (QUANT, [('peaks.quantify', {'calibration': [{'concentration_mol_m3': 0.1, 'area_au_s': 0.01},
                                                     {'concentration_mol_m3': 0.5, 'area_au_s': 0.05}],
                                     'retention_window_s': [150.0, 220.0]})]),
        ('quantify --standards 0.1mM=0.01 --window 150s..220s', []),            # BAD_STANDARDS (needs 2)
        ('quantify --standards 0.1mM=0.01,0.5mM=0.05 --window 220s..150s', []),  # BAD_WINDOW
        ('set-temp 40C', []),                                                   # not supported
        ('load vial1', []),                                                     # not supported
        ('flow off', [('pump.set', {'enabled': False})]),
    ]},
    'agilent_1290infinity': {'steps': [
        ('run', []),                                                            # FLOW_OFF
        ('method flow=0.8mL/min column-temp=35C run=3min', [('method.configure', {
            'flow_m3_s': 0.8 * ML_MIN, 'column_index': 0, 'zone_targets_k': [K0 + 35, K0 + 35], 'injection_m3': 1e-08, 'run_s': 180.0,
            'solvent': 'THF', 'dn_dc_m3_kg': 0.000185})]),
        ('flow on', [('pump.set', {'enabled': True})]),
        ('run', []),                                                            # NOT_READY (zones cooling to 35 °C)
        ('run --wait-ready 30min', [('acquisition.start', {})]),
        ('#wait 200', None),
        ('chromatogram', [('process.measure', {})]),
        ('stop', []),                                                           # NOT_RUNNING (finished)
        (QUANT, []),                                                            # not supported (molar mass)
        ('set-temp 40C', []),                                                   # not supported
        ('load x', []),                                                         # not supported
        ('flow off', []),                                                       # the pump stopped with the run
    ]},
    'thermo_dionexintegrion': {'steps': [
        ('method flow=1.2mL/min eluent=20mM', [('method.update', {'flow_m3_s': 1.2 * ML_MIN, 'eluent_mol_m3': 20.0})]),
        ('method column-temp=35C', [('method.configure', {                     # refused: SAMPLE_LOADED (member code)
            'flow_m3_s': 1.2 * ML_MIN, 'eluent_mol_m3': 20.0, 'column_k': K0 + 35, 'suppression_current_a': 0.05, 'mode': 'anion',
            'rfic': True, 'run_s': 60.0, 'injection_m3': 1e-08})]),
        ('run', []),                                                            # FLOW_OFF
        ('flow on', [('pump.set', {'enabled': True})]),
        ('run', [('acquisition.start', {})]),
        ('run', []),                                                            # BUSY
        ('#wait 70', None),
        ('chromatogram', [('process.measure', {})]),
        ('quantify --standards 0.1mM=0.002,0.5mM=0.01 --window 20s..40s', [('quantification.integrate', {
            'start_s': 20.0, 'stop_s': 40.0, 'calibration_s_m_s_per_mol_m3': 0.02})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('set-temp 35C', [('temperature.set', {'zone': 'column', 'enabled': True, 'setpoint_k': K0 + 35})]),
        ('set-temp 32C --zone cell', [('temperature.set', {'zone': 'cd_cell', 'enabled': True, 'setpoint_k': K0 + 32})]),
        ('set-temp 30C --zone oven', []),                                       # UNKNOWN_ZONE
        ('load x', []),                                                         # not supported
        ('flow off', []),                                                       # the pump stopped with the run
    ]},
    'gas_flavourspec25gc': {'steps': [
        ('method run=200s inlet=90C', []),                                      # GAS_ON (member requirement: method with the gas off)
        ('flow off', [('gas.set', {'enabled': False})]),
        ('method run=200s inlet=90C', [('method.configure', {
            'gc_k': 333.15, 'ims_k': 318.15, 'injector_k': K0 + 90, 'incubator_k': 353.15, 'incubation_s': 10.0, 'injection_m3': 5e-07,
            'column_length_m': 15.0, 'gas': 'nitrogen', 'carrier_scale': 1.0, 'drift_flow_m3_s': 2.5e-06, 'retention_scale': 1.0,
            'drift_ratio_scale': 1.0, 'run_s': 200.0, 'accept_method_transfer': True})]),
        ('run', []),                                                            # FLOW_OFF
        ('flow on', [('gas.set', {'enabled': True})]),
        ('run', []),                                                            # NOT_READY (injector heating to 90 °C)
        ('run --wait-ready 10min', [('acquisition.start', {})]),
        ('run', []),                                                            # BUSY
        ('#wait 30', None),
        ('stop', [('acquisition.stop', {})]),
        ('chromatogram', [('process.measure', {})]),
        ('flow on', [('gas.set', {'enabled': True})]),                          # stop switched the gas off
        ('#wait 300', None),
        ('run --wait-ready 10min', [('acquisition.start', {})]),
        ('#wait 260', None),
        ('chromatogram', [('process.measure', {})]),
        ('quantify --standards 0mM=0,0.1mM=50 --window 0s..200s', [
            ('calibration.fit', {'roi': {'start_s': 0.0, 'stop_s': 200.0, 'drift_min_ratio': 0.6, 'drift_max_ratio': 2.5},
                                 'standards': [{'concentration_mol_m3': 0.0, 'area_s': 0.0}, {'concentration_mol_m3': 0.1, 'area_s': 50.0}],
                                 'label': 'chemlab quantify'}),
            ('concentration.analyze', {})]),
        ('quantify --standards 0mM=0,0.1mM=50 --window 0s..200s --drift 1.0..1.4', [
            ('calibration.fit', {'roi': {'start_s': 0.0, 'stop_s': 200.0, 'drift_min_ratio': 1.0, 'drift_max_ratio': 1.4},
                                 'standards': [{'concentration_mol_m3': 0.0, 'area_s': 0.0}, {'concentration_mol_m3': 0.1, 'area_s': 50.0}],
                                 'label': 'chemlab quantify'}),
            ('concentration.analyze', {})]),
        ('set-temp 60C', []),                                                   # not supported
        ('load x', []),                                                         # not supported
        ('flow off', []),                                                       # the gas switched off at the end of the run
    ]},
    'wyatt_eclipseaf4': {'steps': [
        ('run', []),                                                            # NO_METHOD
        ('method flow=1mL/min', [('method.set', {'detector_flow_m3_s': ML_MIN, 'steps': AF4_STEPS})]),
        ('load unknown_1', []),                                                 # MISSING_OPTION --volume
        ('load unknown_1 --volume 900uL', []),                                  # OUT_OF_RANGE (loop)
        ('load unknown_1 --volume 20uL', [('sample.load', {'sample_id': 'unknown_1', 'provided_id': 'unknown_1', 'volume_m3': 2e-08})]),
        ('load unknown_1 --volume 20uL', []),                                   # SAMPLE_PRESENT
        ('run', [('run.start', {})]),
        ('#wait 100', None),
        ('stop', [('run.abort', {})]),
        ('chromatogram', [('fractogram.read', {})]),
        ('chromatogram --signal uv_au', [('fractogram.read', {})]),
        ('flow on', []),                                                        # not supported
        ('set-temp 30C', []),                                                   # not supported
        (QUANT, []),                                                            # not supported
    ]},
    'sciex_pa800': {'steps': [
        ('run', []),                                                            # NO_METHOD
        ('method wl=200nm', [('detector.configure', {'module': 'pda', 'mode': 'direct_uv', 'wavelength_m': 2e-07, 'data_rate_hz': 4.0})]),
        ("method steps='%s' wl=214nm" % STEPS_CE, [('method.configure', {'steps': CE_STEPS}),
                                                   ('detector.configure', {'module': 'pda', 'mode': 'direct_uv', 'wavelength_m': 2.14e-07,
                                                                           'data_rate_hz': 4.0})]),
        ('method steps=[1,2', []),                                              # BAD_VALUE (not JSON)
        ('run', [('run.start', {})]),                                           # refused: lamp off -> NOT_READY (member fix: lamp=on)
        ('method lamp=on', [('lamp.set', {'on': True})]),
        ('run', [('run.start', {})]),                                           # refused: lamp still warming -> NOT_READY
        ('run --wait-ready 10min', [('run.start', {})]),
        ('#wait 60', None),
        ('stop', [('run.stop', {})]),
        ('chromatogram', [('electropherogram.read', {})]),
        ('set-temp 30C', [('capillary.set_temperature', {'temperature_k': K0 + 30})]),
        ('set-temp 10C --zone sample', [('sample_storage.set_temperature', {'temperature_k': K0 + 10})]),
        ('set-temp 70C', []),                                                   # OUT_OF_RANGE
        ('flow on', []),                                                        # not supported
        ('load x', []),                                                         # not supported
        (QUANT, []),                                                            # not supported
    ]},
    'camag_tlcscanner': {'steps': [
        ('run', []),                                                            # NO_METHOD
        ('method wl=254nm lamp=deuterium', [
            ('method.configure', {'mode': 'absorbance', 'wavelengths_m': [2.54e-07], 'bandwidth_m': 2e-08, 'slit_length_m': 0.006,
                                  'slit_width_m': 0.0003, 'scan_speed_m_s': 0.02, 'data_step_m': 0.0001, 'tracks_x_m': [0.015, 0.03],
                                  'scan_start_y_m': 0.004, 'scan_end_y_m': 0.074, 'application_y_m': 0.008, 'front_y_m': 0.07}),
            ('lamp.select', {'source': 'deuterium'})]),
        ('method lamp=sodium', []),                                             # BAD_SETTING (not a lamp)
        ('run', []),                                                            # NOT_READY (lamp warming)
        ('run --wait-ready 10min', [('scan.start', {})]),
        ('stop', [('scan.stop', {})]),
        ('chromatogram', [('results.read', {})]),
        ('run', [('scan.start', {})]),
        ('#wait 30', None),
        ('chromatogram --pos 1', [('results.read', {})]),
        ('flow on', []),                                                        # not supported
        ('set-temp 30C', []),                                                   # not supported
        ('load plate', []),                                                     # not supported
        (QUANT, []),                                                            # not supported
    ]},
    'biotage_selekt': {'steps': [
        ('run', [('acquisition.start', {})]),                                   # refused: no method yet
        ('method flow=30mL/min gradient=0:0%,4min:80% wl=300nm,350nm', [('method.configure', {
            'channel': 0, 'flow_m3_s': 30 * ML_MIN, 'gradient': [{'time_s': 0.0, 'strong_fraction': 0.0}, {'time_s': 240.0, 'strong_fraction': 0.8}],
            'wavelengths_m': [3e-07, 3.5e-07], 'collection_mode': 'threshold', 'threshold_absorbance': 0.02, 'fraction_volume_m3': 5e-06,
            'timeout_s': 1200.0})]),
        ('method gradient=0:10,4min:80%', []),                                  # BAD_GRADIENT (fraction without a unit)
        ('run', [('acquisition.start', {})]),
        ('#wait 60', None),
        ('stop', [('acquisition.stop', {})]),
        ('chromatogram', [('history.read', {})]),
        ('load x', []), ('flow on', []), ('set-temp 30C', []), (QUANT, []),     # not supported
    ]},
    'gilson_cpc250': {'steps': [
        ('method flow=10mL/min speed=1600rpm run=40min', [('method.configure', {   # refused: METHOD_LOCKED (sample configured)
            'speed_rad_s': 167.55160819145564, 'direction': 'DSC', 'phase_system': 'water_1octanol', 'flow_m3_s': 10 * ML_MIN,
            'duration_s': 2400.0, 'collection_start_s': 0.0, 'collection_end_s': 2400.0, 'fraction_volume_m3': 3e-05,
            'exceptional_speed_reviewed': False})]),
        ('run', [('acquisition.start', {})]),
        ('#wait 60', None),
        ('stop', [('acquisition.stop', {})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('chromatogram', [('history.read', {})]),
        ('load x', []), ('flow on', []), ('set-temp 30C', []), (QUANT, []),     # not supported
    ]},
    'biorad_miniprotean': {'steps': [
        ('method voltage=200V run=35min constant=voltage', [('psu.program', {
            'constant': 'voltage', 'voltage_v': 200.0, 'current_a': 0.4, 'time_s': 2100.0, 'power_failure_detection': False})]),
        ('method voltage=500V', []),                                            # BAD_SETTING (max 300 V)
        ('load lysate_1', []),                                                  # MISSING_OPTION --lane
        ('load lysate_1 --lane 2 --volume 10uL', [('sample.load', {'sample_id': 'lysate_1', 'position': 1, 'lane': 2, 'volume_m3': 1e-08})]),
        ('run', [('lid.place', {}), ('leads.connect', {'polarity': 'normal'}), ('psu.start', {})]),
        ('#wait 60', None),
        ('stop', [('psu.stop', {})]),
        ('chromatogram', [('gel.observe', {'position': 1})]),
        ('load lysate_1 --lane 3 --volume 5uL', [('leads.disconnect', {}), ('lid.remove', {}),
                                                 ('sample.load', {'sample_id': 'lysate_1', 'position': 1, 'lane': 3, 'volume_m3': 5e-09})]),
        ('flow on', []), ('set-temp 30C', []), (QUANT, []),                     # not supported
    ]},
}
