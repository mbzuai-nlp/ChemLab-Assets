"""Lowering table of the electrical class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts (input schemas and documented example arguments), not from the
mapping.  [] = no native call (a class check refused, a usage error, a read, a wait); a runtime refusal still lists the call
that was sent.  The labs carry the class world (tasks/electrical_world.py)."""
import math

from .electrical_world import config  # noqa: F401  (the lowering harness builds its labs from this config)
from .thermal_analysis_lowering import NUMBER, OBJECT, SERIES

K0 = 273.15
M91_METHOD = {'current_a': 0.0005, 'current_range_a': 0.001, 'voltage_range_v': 0.001, 'phase_duration_s': 0.1, 'repeats': 1,
              'field_settling_tolerance_t': 0.0001}
QP_METHOD = {'auto_current': True, 'current_min_a': 1e-08, 'current_max_a': 0.001, 'fixed_current_a': 0.0001, 'voltage_range_v': 0.2,
             'voltage_compliance_v': 20.0, 'target_voltage_min_v': 0.005, 'target_voltage_max_v': 0.08, 'phase_duration_s': 0.3, 'settling_s': 0.2,
             'require_calibration': True, 'require_warmup': False}
VSM_METHOD = {'ramp_rate_t_s': 1.0, 'averaging_time_s': 0.05, 'settling_time_s': 0.05, 'require_calibration': False}
AT_METHOD = {'source_voltage_rms_v': 0.3, 'cycles_per_point': 1, 'settling_s': 0.01, 'require_calibration': False}
LSR_METHOD = {'duration_s': 3.0, 'phase_interval_s': 0.05, 'current_a': 0.01, 'minimum_delta_k': 0.1, 'equilibrium_tolerance_k': 0.05,
              'geometry_factor': 1.0, 'contact_resistance_correction_ohm': 0.0002}

CASES = {
    'lakeshore_measurereadym91': {'steps': [
        ('status', []),
        ('start', []),                                                           # WARMING_UP (30 min after power-on)
        ('contact on', []),                                                      # not on this member (no probe actuator)
        ('move 1mm 1mm', []),                                                    # not supported
        ('method range=1mA', []),                                                # MISSING_SETTING current
        ('method current=200mA', []),                                            # OUT_OF_RANGE (0 .. 100 mA)
        ('method current=0.5mA', [('method.configure', M91_METHOD)]),            # range auto: 1 mA
        ('method current=0.5mA range=10mA', [('method.configure', dict(M91_METHOD, current_range_a=0.01))]),
        ('method current=0.5mA range=2mA', []),                                  # BAD_CHOICE (ranges are decades)
        ('#wait 1800', None),
        ('contact check max-current=10mA', [('contact_check.auto', {'max_current_a': 0.01, 'max_voltage_v': 10.0, 'points': 11, 'minimum_r_squared': 0.9999})]),
        ('wait --max 10min', []),
        ('start', [('acquisition.start', {})]),
        ('wait', []),
        ('result', [('acquisition.result', {})]),
        ('analyze', []),                                                         # MISSING_SETTING thickness
        ('analyze thickness=500um', [('acquisition.result', {})]),               # TOO_FEW_POINTS (one repeat: 9 readings, the analysis needs 18)
        ('stop', []),                                                            # NOT_RUNNING
        ('calibrate voltage_gain_correction=1 voltage_offset_v=0 field_gain_correction=1 field_offset_t=0.1mT',
         [('sensor.calibrate', {'voltage_gain_correction': 1.0, 'voltage_offset_v': 0.0, 'field_gain_correction': 1.0, 'field_offset_t': 0.0001})]),
        ('set-temp 30C', []),                                                    # not supported (the lab's stage)
        ('temp', []),
    ]},
    'signatone_quadpro2': {'steps': [
        ('contact check', []),                                                   # not on this member
        ('set-temp 30C', []),                                                    # not supported
        ('method point=0mm,0mm point=20mm,0mm edge=3mm', [('map.configure', {'points': [{'x_m': 0.0, 'y_m': 0.0}, {'x_m': 0.02, 'y_m': 0.0}],
                                                                              'edge_exclusion_m': 0.003, 'thickness_prior_m': 1e-06})]),
        ('method current=0.1mA', [('method.configure', dict(QP_METHOD, auto_current=False))]),
        ('start', [('acquisition.start', {})]),                                  # runtime CALIBRATION_REQUIRED (the example method requires one)
        ('method calibration=false current=0.1mA', [('method.configure', dict(QP_METHOD, auto_current=False, require_calibration=False))]),
        ('start', [('acquisition.start', {})]),
        ('wait --max 10min', []),
        ('result', [('acquisition.result', {})]),
        ('analyze diameter=100mm point=2', [('acquisition.result', {}),
                                            ('analysis.sheet_resistance', {'diameter_m': 0.1, 'spacing_m': 0.00127, 'x_m': 0.02, 'y_m': 0.0,
                                                                           'observations': SERIES, 'calibration_gain': 1.0, 'thickness_prior_m': None})]),
        ('analyze diameter=100mm point=7', [('acquisition.result', {})]),       # OUT_OF_RANGE (the map has 2 points)
        ('contact on', [('probe.set_contact', {'contact': True})]),
        ('move 10mm 0mm', []),                                                   # PROBE_DOWN
        ('contact off', [('probe.set_contact', {'contact': False})]),
        ('move 10mm 5mm', [('stage.move', {'x_m': 0.01, 'y_m': 0.005})]),
        ('move 200mm 0mm', []),                                                  # OUT_OF_RANGE (usage)
        ('calibrate ref=100ohm/sq', [('calibration.start', {'reference_sheet_resistance_ohm_sq': 100.0})]),   # runtime STANDARD_REQUIRED
        ('stop', []),                                                            # NOT_RUNNING
    ]},
    'lakeshore_8604vsm': {'steps': [
        ('start', []),                                                           # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),                        # shared verb
        ('start', []),                                                           # NOT_LOWERED
        ('move --lift 0mm', [('sample.set_lift', {'lift_m': 0.0})]),
        ('move 1mm 1mm', []),                                                    # NO_STAGE (not on this member)
        ('method field=0.1T', [('method.configure', dict(VSM_METHOD, field_points_t=[0.1]))]),
        ('start', []),                                                           # NO_COIL
        ('method range=10emu vibrate=on loop=0.2T/0.1T', [('coil.configure', {'moment_range_a_m2': 0.01, 'nominal_coupling_wb_per_a_m3': 0.1}),
                                                          ('vibration.configure', {'enabled': True, 'amplitude_m': 0.0005, 'frequency_hz': 82.0}),
                                                          ('method.configure', dict(VSM_METHOD, field_points_t=[0.2, 0.1, 0.0, -0.1, -0.2, -0.1, 0.0, 0.1, 0.2]))]),
        ('set-field 0.1T --rate 10mT/s --wait', [('field.set_target', {'field_t': 0.1, 'ramp_rate_t_s': 0.01})]),
        ('start', [('acquisition.start', {})]),
        ('wait', []),
        ('result', [('acquisition.result', {})]),
        ('analyze', [('acquisition.result', {}), ('analysis.loop', {'rows': SERIES})]),
        ('move --lift 50mm', []),                                                # VIBRATING
        ('method vibrate=off', [('vibration.configure', {'enabled': False, 'amplitude_m': 0.0005, 'frequency_hz': 82.0})]),
        ('set-field 0 --wait', [('field.set_target', {'field_t': 0.0, 'ramp_rate_t_s': 1.0})]),
        ('move --lift 50mm --angle 90deg', [('sample.set_lift', {'lift_m': 0.05}), ('sample.set_angle', {'angle_rad': math.pi / 2})]),
        ('calibrate moment=3.47emu', []),                                        # NOT_VIBRATING (class check)
        ('contact on', []),                                                      # not supported
    ]},
    'novocontrol_alphat': {'steps': [
        ('start', []),                                                           # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('calibrate x=1', []),                                                   # not supported
        ('method voltage=0.3V', []),                                             # MISSING_SETTING freq
        ('method freq=1kHz,10kHz voltage=0.3V', [('method.configure', dict(AT_METHOD, frequencies_hz=[1000.0, 10000.0]))]),
        ('method sweep=10Hz..100kHz/5', [('method.configure', dict(AT_METHOD, frequencies_hz=[10.0, 100.0, 1000.0, 10000.0, 100000.0]))]),
        ('set-temp 2000C', []),                                                  # OUT_OF_RANGE
        ('set-temp 20.05C', [('thermal.set_temperature', {'temperature_k': 293.2})]),
        ('wait-temp --within 0.1K', []),
        ('start', [('acquisition.start', {})]),
        ('wait', []),
        ('result --save-to eps.json', [('acquisition.result', {})]),
        ('analyze diameter=20mm thickness=1mm point=3', [('acquisition.result', {}),
                                                         ('analysis.dielectric', {'frequency_hz': 1000.0, 'voltage_rms': OBJECT, 'current_rms': OBJECT,
                                                                                  'diameter_m': 0.02, 'thickness_m': 0.001})]),
        ('set-temp off', [('thermal.disable', {})]),
        ('move 1mm 1mm', []),                                                    # not supported
    ]},
    'linseis_lsr3': {'steps': [
        ('set-temp 100C', []),                                                   # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('set-temp 100C --rate 10K/min --gradient 5K', [('thermal.set_temperature', {'temperature_k': K0 + 100, 'ramp_rate_k_min': 10.0}),
                                                        ('thermal.set_gradient', {'delta_k': 5.0})]),
        ('wait-temp --max 1h', []),
        ('method current=10mA time=3s', [('method.configure', LSR_METHOD)]),
        ('method current=1A', []),                                               # OUT_OF_RANGE (at most 160 mA)
        ('start', [('acquisition.start', {})]),
        ('wait', []),
        ('result', [('acquisition.result', {})]),
        ('analyze width=3mm depth=3mm spacing=8mm point=5', [('acquisition.result', {}),
                                                             ('analysis.transport', {'zero_voltage_v': NUMBER, 'positive_voltage_v': NUMBER, 'negative_voltage_v': NUMBER,
                                                                                     'positive_current_a': 0.01, 'negative_current_a': -0.01, 'hot_temperature_k': NUMBER,
                                                                                     'cold_temperature_k': NUMBER, 'width_m': 0.003, 'depth_m': 0.003,
                                                                                     'probe_spacing_m': 0.008, 'geometry_factor': 1.0,
                                                                                     'contact_resistance_correction_ohm': 0.0})]),
        ('calibrate hot_offset_k=0.1K cold_offset_k=0 voltage_offset_v=0 voltage_gain_correction=1',
         [('sensor.calibrate', {'hot_offset_k': 0.1, 'cold_offset_k': 0.0, 'voltage_offset_v': 0.0, 'voltage_gain_correction': 1.0})]),
        ('set-temp off', [('thermal.disable', {})]),
        ('contact on', []),                                                      # not supported
    ]},
}
