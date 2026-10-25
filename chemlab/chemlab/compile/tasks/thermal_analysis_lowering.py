"""Lowering table of the thermal_analysis class: per member, verbs in order and the native calls (capability, arguments in the
unit the argument declares) each must make - written from the contracts (input schemas and documented example arguments), not
from the mapping.  [] = no native call (a class check refused, a usage error, a read, a wait); a runtime refusal still lists the
call that was sent.  The labs carry the class world (tasks/thermal_analysis_world.py)."""
from ..lowering import Measured
from .thermal_analysis_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
ML_MIN = 1e-6 / 60


# arguments the analysis verbs take from the instrument's own record (series, objects, numbers of it) are data, not lowering: the
# table matches their kind (framework matchers); every argument the agent gives (mass, thickness, window ...) is checked exactly, and
# the twin replay compares the full state with the exact arguments.
SERIES, NUMBER, OBJECT = Measured(list), Measured(), Measured(dict)
K_MIN = 1.0 / 60


def detector_on(backend):
    """LFA: the IR detector switched on (as the agent would with `chemlab call ... detector.set_enabled`) before the steps."""
    backend.invoke('agent', 'netzsch_lfa467', 'detector.set_enabled', {'enabled': True}, 'prep-detector')
    backend.advance(60.0)


DSC_METHOD = {'start_temperature_k': K0 + 140, 'segments': [{'target_k': K0 + 180, 'rate_k_s': 10 * K_MIN, 'hold_s': 60.0}],
              'sample_interval_s': 0.1, 'modulation_amplitude_k': 0.0, 'modulation_period_s': 60.0, 'start_tolerance_k': 0.3,
              'timeout_s': (240.0 + 60.0) * 1.5 + 1800.0}
TGA_METHOD = {'start_temperature_k': K0 + 40, 'segments': [{'target_k': K0 + 300, 'rate_k_s': 20 * K_MIN, 'hold_s': 0.0, 'gas': 'N2'},
                                                           {'target_k': K0 + 300, 'rate_k_s': 20 * K_MIN, 'hold_s': 120.0, 'gas': 'N2'}],
              'sample_interval_s': 0.1, 'start_tolerance_k': 0.5, 'timeout_s': (780.0 + 120.0) * 1.5 + 1800.0}
DMA_BLOCK = {'temperature_k': K0 + 25, 'frequency_hz': 1.0, 'strain_amplitude': 1e-4, 'control': 'strain', 'force_amplitude_n': 0.01,
             'settle_cycles': 5, 'measure_cycles': 3}
DMA_REST = {'heating_rate_k_s': 10 * K_MIN, 'cooling_rate_k_s': 5 * K_MIN, 'temperature_tolerance_k': 0.3, 'timeout_s': 100000.0,
            'samples_per_cycle': 128}
TMA_METHOD = {'start_temperature_k': K0 + 30, 'segments': [{'target_temperature_k': K0 + 60, 'temperature_rate_k_s': 5 * K_MIN, 'hold_s': 30.0,
                                                            'motor_force_n': 0.05, 'force_rate_n_s': 0.1}],
              'start_tolerance_k': 0.5, 'sample_interval_s': 0.1, 'timeout_s': (360.0 + 30.0) * 1.5 + 1800.0, 'accept_condition_transfer': True}
LFA_METHOD = {'temperature_k': K0 + 25, 'rate_k_s': 50 * K_MIN, 'equilibrium_tolerance_k': 0.2, 'equilibrium_dwell_s': 0.2, 'pretrigger_s': 0.001,
              'pulse_energy_j': 0.1, 'pulse_width_s': 0.0001, 'sample_interval_s': 0.0001, 'record_duration_s': 0.25, 'timeout_s': 3600.0,
              'accept_condition_transfer': True}
TPS_METHOD = {'power_w': 0.5, 'duration_s': 1.5, 'sample_interval_s': 0.025, 'temperature_k': 296.15, 'geometry_model': 'isotropic_bulk',
              'accept_gaussian_plane_approximation': True}
MMC_METHOD = {'start_temperature_k': K0 + 50, 'end_temperature_k': K0 + 220, 'mode': 'hws', 'step_k': 10.0, 'heat_rate_k_min': 2.0, 'wait_min': 15.0,
              'search_min': 15.0, 'threshold_k_min': 0.02, 'max_pressure_pa': 14000000.0, 'max_rate_k_min': 50.0, 'iso_temperature_k': None,
              'iso_hours': None, 'cool_to_k': K0 + 50, 'data_interval_s': 30.0}

CASES = {
    'ta_dsc2500': {'steps': [
        ('status', []),
        ('load 2', []),                                                          # EMPTY_POSITION (class check: tray 2 is empty)
        ('start', []),                                                           # NO_SAMPLE
        ('load 1', [('autosampler.load', {'position': 1})]),                     # waits for the transfer
        ('zero', []),                                                            # NOT_EMPTY
        ('set-temp 140C', []),                                                   # LID_OPEN
        ('close lid', [('lid.set', {'opened': False})]),                         # waits until closed
        ('gas 50mL/min', [('gas.set_flow', {'flow_m3_s': 50 * ML_MIN}), ('gas.set_open', {'opened': True})]),
        ('method start=2000C ramp=180C@10K/min', []),                            # OUT_OF_RANGE (usage)
        ('method ramp=180C@10K/min', []),                                        # MISSING_SETTING start
        ('method start=140C ramp=180C@10K/min hold=1min', [('method.configure', DSC_METHOD)]),
        ('set-temp 140C --rate 20K/min --dry-run', []),
        ('set-temp 140C --rate 20K/min', [('thermal.set_temperature', {'temperature_k': K0 + 140, 'rate_k_s': 20 * K_MIN})]),
        ('wait-temp --within 0.3K --max 1h', []),
        ('start', [('acquisition.start', {})]),
        ('method start=25C ramp=50C@10K/min', []),                               # BUSY
        ('wait --max 1h', []),                                                   # the shared wait on job-1
        ('result', [('acquisition.result', {})]),
        ('analyze', []),                                                         # MISSING_SETTING mass
        ('analyze integrate mass=2mg from=150C to=170C', [('acquisition.result', {}),
                                                          ('analysis.integrate', {'time_s': SERIES, 'heatflow_w': SERIES, 'temperature_k': SERIES,
                                                                                  'baseline_w': SERIES, 'sample_mass_kg': 2e-06})]),
        ('stop', []),                                                            # NOT_RUNNING
        ('set-temp off', [('thermal.disable', {})]),
        ('wait-temp --to 35C --within 3K --max 1h', []),
        ('open lid', [('lid.set', {'opened': True})]),
        ('unload', [('autosampler.unload', {'position': 1})]),
        ('zero', []),                                                            # NOT_AMBIENT (the empty cell is still warm)
        ('wait-temp --to 25C --within 0.001K --max 1h', []),
        ('zero', [('sensor.zero', {})]),
        ('calibrate temperature_gain_correction=1', []),                         # MISSING_SETTING (every factor is needed)
        ('calibrate sample_flow_gain_correction=1 reference_flow_gain_correction=1 sample_flow_zero_w=0 reference_flow_zero_w=0 '
         'temperature_gain_correction=1 temperature_zero_k=0.1K',
         [('sensor.calibrate', {'sample_flow_gain_correction': 1.0, 'reference_flow_gain_correction': 1.0, 'sample_flow_zero_w': 0.0,
                                'reference_flow_zero_w': 0.0, 'temperature_gain_correction': 1.0, 'temperature_zero_k': 0.1})]),
        ('gas off', [('gas.set_open', {'opened': False})]),
    ]},
    'ta_tga5500': {'steps': [
        ('zero', [('balance.tare', {})]),
        ('load 1', [('autosampler.load', {'position': 1})]),
        ('zero', []),                                                            # NOT_EMPTY
        ('start', []),                                                           # FURNACE_OPEN
        ('close', [('furnace.set', {'opened': False})]),
        ('gas O2', [('gas.select', {'gas': 'O2'})]),
        ('gas N2 25mL/min --balance 10mL/min', [('gas.select', {'gas': 'N2'}),
                                                 ('gas.set_flow', {'sample_flow_m3_s': 25 * ML_MIN, 'balance_flow_m3_s': 10 * ML_MIN}),
                                                 ('gas.set_open', {'opened': True})]),
        ('method start=40C ramp=300C@20K/min gas=N2 iso=2min', [('method.configure', TGA_METHOD)]),
        ('set-temp 40C --rate 20K/min', [('thermal.set_temperature', {'temperature_k': K0 + 40, 'rate_k_s': 20 * K_MIN})]),
        ('wait-temp --max 30min', []),
        ('start', [('acquisition.start', {})]),
        ('wait --max 2h', []),
        ('result --save-to tga.json', [('acquisition.result', {})]),
        ('analyze split=380C,450C', [('acquisition.result', {}),
                                     ('analysis.mass_stages', {'time_s': SERIES, 'temperature_k': SERIES, 'mass_kg': SERIES, 'baseline_kg': SERIES,
                                                               'initial_mass_kg': NUMBER, 'stage_boundaries': SERIES})]),
        ('unload', []),                                                          # FURNACE_CLOSED (class check)
        ('open furnace', [('furnace.set', {'opened': True})]),                   # runtime HOT_CELL (refused, nothing changed)
        ('wait-temp --to 35C --within 3K --max 2h', []),
        ('open furnace', [('furnace.set', {'opened': True})]),
        ('unload', [('autosampler.unload', {'position': 1})]),
        ('stop', []),                                                            # NOT_RUNNING
    ]},
    'ta_dma850': {'steps': [
        ('load --preload 0.01N', []),                                            # NO_AIR (class check, before any call)
        ('gas 50mL/min', []),                                                    # NO_FLOW (not supported: on/off only)
        ('gas on', [('gas.set_open', {'opened': True})]),
        ('load --preload 0.01N', [('clamp.set', {'locked': True}), ('drive.preload', {'force_n': 0.01, 'ramp_s': 2.0})]),
        ('zero', []),                                                            # NOT_EMPTY
        ('close furnace', [('furnace.set', {'opened': False})]),
        ('method steps=25C..45C/10K', [('method.configure', dict(DMA_REST, blocks=[dict(DMA_BLOCK, temperature_k=K0 + t, strain_amplitude=1e-4)
                                                                                    for t in (25, 35, 45)]))]),   # runtime SOURCE_ANCHOR_ONLY
        ('method step=25C freq=1Hz strain=0.01%', [('method.configure', dict(DMA_REST, blocks=[DMA_BLOCK]))]),
        ('set-temp 25C --rate 5K/min', [('thermal.set_temperature', {'temperature_k': K0 + 25, 'rate_k_s': 5 * K_MIN})]),
        ('start', [('acquisition.start', {})]),
        ('wait --max 1h', []),
        ('result', [('acquisition.result', {})]),
        ('analyze length=10mm', []),                                             # MISSING_SETTING area
        ('analyze length=10mm area=0.8mm2', [('acquisition.result', {}),
                                             ('analysis.modulus', {'time_s': SERIES, 'force_n': SERIES, 'displacement_m': SERIES, 'frequency_hz': 1.0,
                                                                   'length_m': 0.01, 'area_m2': 8e-07})]),
        ('analyze transition', [('acquisition.result', {})]),                    # TOO_FEW_POINTS (one block)
        ('unload', []),                                                          # FURNACE_CLOSED
        ('set-temp off', [('thermal.disable', {})]),
        ('open furnace', [('furnace.set', {'opened': True})]),
        ('unload', [('drive.unload', {'ramp_s': 2.0}), ('clamp.set', {'locked': False})]),
        ('calibrate force_gain_correction=1 force_zero_n=0 displacement_gain_correction=1 displacement_zero_m=0 temperature_gain_correction=1 '
         'temperature_zero_k=0', [('sensor.calibrate', {'force_gain_correction': 1.0, 'force_zero_n': 0.0, 'displacement_gain_correction': 1.0,
                                                         'displacement_zero_m': 0.0, 'temperature_gain_correction': 1.0, 'temperature_zero_k': 0.0})]),
    ]},
    'ta_tma450': {'steps': [
        ('zero', []),                                                            # NO_CONTACT
        ('load', [('probe.contact', {'engaged': True})]),
        ('zero', [('displacement.zero', {})]),
        ('close', [('furnace.set', {'opened': False})]),
        ('gas --channel 1 100mL/min', [('gas.set_flow', {'flow_m3_s': 100 * ML_MIN}), ('gas.set_open', {'opened': True})]),
        ('method start=30C ramp=60C@5K/min hold=30s force=0.05N', [('method.configure', TMA_METHOD)]),
        ('set-temp 30C --rate 10K/min', [('thermal.set_temperature', {'temperature_k': K0 + 30, 'rate_k_s': 10 * K_MIN})]),
        ('wait-temp --max 30min', []),
        ('start', [('acquisition.start', {})]),
        ('wait --max 1h', []),
        ('result', [('acquisition.result', {})]),
        ('analyze length=1.6234mm from=35C to=55C', [('acquisition.result', {}),
                                                     ('analysis.cte', {'temperature_k': SERIES, 'displacement_m': SERIES, 'blank_m': SERIES,
                                                                       'initial_length_m': 0.0016234, 'start_index': 0, 'end_index': NUMBER})]),
        ('unload', []),                                                          # FURNACE_CLOSED
        ('set-temp off', [('thermal.disable', {})]),
        ('wait-temp --to 35C --within 3K --max 1h', []),
        ('open', [('furnace.set', {'opened': True})]),
        ('unload', [('probe.contact', {'engaged': False})]),
    ]},
    'netzsch_lfa467': {'prep': detector_on, 'steps': [
        ('unload', []),                                                          # not supported
        ('zero', []),                                                            # not supported
        ('load 3', []),                                                          # EMPTY_POSITION (the specimen sits at 1)
        ('load 1', [('sampler.select', {'position': 1})]),
        ('close', [('furnace.set', {'opened': False})]),
        ('set-temp 50C', []),                                                    # NO_PURGE (protective gas off)
        ('gas 50mL/min', []),                                                    # MISSING_CHANNEL (usage)
        ('gas 50mL/min --channel protective', [('gas.set_flow', {'channel': 'protective', 'flow_m3_s': 50 * ML_MIN}),
                                               ('gas.set_open', {'channel': 'protective', 'opened': True})]),
        ('gas 50mL/min --channel purge1', [('gas.set_flow', {'channel': 'purge1', 'flow_m3_s': 50 * ML_MIN}),
                                           ('gas.set_open', {'channel': 'purge1', 'opened': True})]),
        ('method temp=25C', [('method.configure', LFA_METHOD)]),
        ('start', [('acquisition.start', {})]),
        ('wait --max 1h', []),
        ('result', [('acquisition.result', {})]),
        ('analyze thickness=2mm', [('acquisition.result', {}),
                                   ('analysis.parker', {'time_s': SERIES, 'temperature_k': SERIES, 'thickness_m': 0.002, 'pulse_time_s': NUMBER,
                                                        'baseline_k': NUMBER, 'plateau_k': NUMBER, 'accept_adiabatic_instant_pulse': True})]),
        ('calibrate temperature_gain_correction=1 temperature_zero_k=0 pulse_gain_correction=1 pulse_zero_w=0',
         [('sensor.calibrate', {'temperature_gain_correction': 1.0, 'temperature_zero_k': 0.0, 'pulse_gain_correction': 1.0, 'pulse_zero_w': 0.0})]),
        ('set-temp 30C --rate 10K/min', [('thermal.set_temperature', {'temperature_k': K0 + 30, 'rate_k_s': 10 * K_MIN})]),
        ('set-temp off', [('thermal.disable', {})]),
        ('open', [('furnace.set', {'opened': True})]),
    ]},
    'hot_tps2500': {'steps': [
        ('start', []),                                                           # POWER_OFF
        ('load', []),                                                            # not supported
        ('set-temp 30C', []),                                                    # not supported
        ('power on', [('power.set', {'enabled': True})]),                        # shared verb
        ('method time=1.5s power=0.5W', [('method.configure', TPS_METHOD)]),
        ('method temp=30C', [('method.configure', dict(TPS_METHOD, temperature_k=K0 + 30))]),   # runtime EXTERNAL_TEMPERATURE_BOUNDARY
        ('start', [('acquisition.start', {})]),
        ('wait', []),
        ('result', [('acquisition.result', {})]),
        ('analyze', [('acquisition.result', {}),
                     ('analysis.fit', {'time_s': SERIES, 'temperature_rise_k': SERIES, 'power_w': 0.5, 'radius_m': 0.0064, 'minimum_diffusivity_m2_s': 1e-08,
                                       'maximum_diffusivity_m2_s': 0.0004, 'accept_gaussian_plane_approximation': True})]),
        ('calibrate gain_correction=1 offset_correction_k=0.1K', [('sensor.calibrate', {'gain_correction': 1.0, 'offset_correction_k': 0.1})]),
        ('temp', []),
    ]},
    'ta_tamiv': {'steps': [
        ('set-temp 25C', []),                                                    # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('set-temp 25C --rate 1K/min', []),                                      # NO_RATE (not supported)
        ('set-temp 25C', [('thermal.set_temperature', {'temperature_k': K0 + 25})]),
        ('wait-temp --max 1h', []),
        ('method interval=0.1s', []),                                            # MISSING_SETTING time
        ('method time=20s interval=0.1s', [('method.configure', {'duration_s': 20.0, 'sample_interval_s': 0.1, 'equilibrium_tolerance_k': 0.1})]),
        ('start', [('acquisition.start', {})]),
        ('wait --max 10min', []),
        ('result', [('acquisition.result', {})]),
        ('analyze', [('acquisition.result', {}), ('analysis.baseline', {'time_s': SERIES, 'heat_flow_w': SERIES})]),
        ('start', [('acquisition.start', {})]),
        ('stop', [('acquisition.stop', {})]),
        ('calibrate gain_correction=1 baseline_intercept_w=0 baseline_drift_w_s=0',
         [('sensor.calibrate', {'gain_correction': 1.0, 'baseline_intercept_w': 0.0, 'baseline_drift_w_s': 0.0})]),
        ('load', []),                                                            # not supported
        ('set-temp off', [('thermal.disable', {})]),
    ]},
    'netzsch_mmc315': {'steps': [
        ('start', []),                                                           # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('start', []),                                                           # NO_METHOD
        ('method start=50C', []),                                                # MISSING_SETTING end
        ('method start=50C end=220C', [('method.configure', MMC_METHOD)]),
        ('start', [('test.start', {})]),
        ('#wait 3600', None),
        ('stop', [('test.stop', {})]),
        ('result', [('test.result', {})]),
        ('analyze', [('test.result', {})]),                                      # TOO_FEW_POINTS (stopped before an exotherm)
        ('set-temp 50C', []),                                                    # not supported
    ]},
    'ika_c6000': {'steps': [
        ('load C6012-0001', []),                                                 # NOT_WAITING
        ('method name=BA_1 mass=1g', [('measurement.create', {'name': 'BA_1', 'sample_mass_kg': 0.001, 'kind': 'measurement', 'vessel_name': None,
                                                              'aids': []})]),
        ('start', []),                                                           # MODE_MISMATCH (isoperibol_22 vs the cooling water)
        ('method mode=isoperibol_25', [('mode.set', {'mode': 'isoperibol_25'})]),
        ('start', []),                                                           # NOT_READY (system test after the mode change)
        ('#wait 420', None),
        ('start', [('measurement.start', {'measurement_id': 'm-1'})]),
        ('load C6012-0001', [('vessel.scan', {'rfid': 'C6012-0001'}), ('vessel.hang', {'rfid': 'C6012-0001'}), ('measurement.confirm', {})]),
        ('wait --max 1h', []),
        ('result', [('measurement.result', {'measurement_id': 'm-1'})]),
        ('analyze sulfur=0.1% hydrogen=4.95%', [('measurement.evaluate', {'measurement_id': 'm-1', 'sulfur_mass_fraction': 0.001, 'total_acid_mol': None,
                                                                           'hydrogen_mass_fraction': 0.0495, 'moisture_mass_fraction': 0.0})]),
        ('unload', [('vessel.remove', {})]),
        ('calibrate c=8750J/K', [('vessel.set_c_value', {'c_value_j_k': 8750.0, 'vessel_name': 'C6012 no. 1', 'mode': 'isoperibol_25'})]),
        ('close cover', [('cover.close', {})]),
        ('open cover', [('cover.open', {})]),
        ('set-temp 25C', []),                                                    # not supported
    ]},
}
