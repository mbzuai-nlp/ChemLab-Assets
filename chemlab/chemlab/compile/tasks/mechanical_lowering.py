"""Lowering table of the mechanical class: per member, verbs in order and the native calls (capability, arguments in the unit
the argument declares) each must make - written from the contracts (a method call carries the contract's reference arguments,
``T(...)``, with the values the verb changes), not from the mapping.  [] = no native call (a class check refused, a usage error,
an unsupported verb); None = not checked (an evaluation whose arguments are the arrays of the run just made; its twin state
is still compared).  Two extra lab entries cover other worlds: ``mcr_empty`` (MCR 503 without a sample, for the zero-gap
procedure) and ``hit5p_ready`` (HIT5P calibrated, re-armed, with a notched bar on the supports)."""
import copy
import json

from ...hosts import contract_index
from . import mechanical_world as W

PI = 3.141592653589793
K0 = 273.15


def T(slug, cap, **over):
    """The contract's reference arguments of a capability, with the values a step changes."""
    c = json.loads(contract_index()['instrument.' + slug].read_text(encoding='utf-8'))
    a = copy.deepcopy(c['capabilities'][cap]['example_arguments'])
    a.update(over)
    return a


def config():
    cfg = W.config()
    by = {e['id']: e for e in cfg['instruments']}
    base = W.load_config()
    mcr = copy.deepcopy(next(e for e in base['instruments'] if e['id'] == 'antonpaar_mcr503'))
    mcr['id'] = 'mcr_empty'
    mcr['default_world'] = False                       # without a sample (the default lab has the MCR loaded)
    hit = copy.deepcopy(by['zwick_hit5p'])
    hit['id'] = 'hit5p_ready'
    hit['setup'] = list(hit.get('setup') or []) + W._resolve('zwick_hit5p', W.HIT5P_READY)
    cfg['instruments'] += [mcr, hit]
    return cfg


TRB, RST, HIT, AGS, H5, MF, MCR, FT4 = ('antonpaar_trb', 'antonpaar_rst300', 'antonpaar_hit300', 'shimadzu_ags10knxd', 'zwick_hit5p',
                                       'zwick_mflow', 'antonpaar_mcr503', 'freeman_ft4powder')

CASES = {
    TRB: {'steps': [
        ('status', []),
        ('zero', []),                                                           # POWER_OFF (member requirement)
        ('contact off', []),                                                    # already lifted: nothing to send
        ('power on', [('power.set', {'enabled': True})]),                       # shared layer
        ('zero', [('sensor.zero', {})]),
        ('method load=5N speed=100rpm time=30s', [
            ('normal_load.set', {'force_n': 5.0}),
            ('drive.configure', T(TRB, 'drive.configure', mode='rotary', angular_speed_rad_s=100 * 2 * PI / 60)),
            ('method.configure', T(TRB, 'method.configure', duration_s=30.0))]),
        ('method load=80N', []),                                                # OUT_OF_RANGE (usage, 0..60 N)
        ('method bogus=1', []),                                                 # UNKNOWN_SETTING
        ('move 8mm', [('radius.set', {'radius_m': 0.008})]),
        ('move 50mm', []),                                                      # OUT_OF_RANGE (0..40 mm)
        ('contact on', [('contact.set', {'engaged': True})]),
        ('move 9mm', []),                                                       # ARM_LOADED (member requirement)
        ('start', []),                                                          # LID_OPEN
        ('close lid', [('lid.set', {'opened': False})]),                        # shared layer
        ('start --dry-run', []),
        ('start', [('acquisition.start', {})]),
        ('#wait 10', None),
        ('stop', [('acquisition.stop', {})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('result', [('acquisition.result', {})]),
        ('set-temp 50C', [('thermal.set_temperature', {'temperature_k': K0 + 50})]),   # refused by the runtime: NO_HEATER
        ('set-temp 500C', []),                                                  # OUT_OF_RANGE (max 450 °C)
        ('calibrate friction-gain=1.02', [('sensor.calibrate', T(TRB, 'sensor.calibrate', friction_gain_correction=1.02))]),
        ('clamp on', []),                                                       # not supported
        ('analyze', []),                                                        # not supported
        ('open lid', [('lid.set', {'opened': True})]),
        ('contact off', [('contact.set', {'engaged': False})]),
    ]},
    RST: {'steps': [
        ('contact on', []),                                                     # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('zero', [('sensor.zero', {})]),
        ('contact on', []),                                                     # NOT_CLAMPED
        ('clamp on', [('clamp.set', {'locked': True})]),
        ('move 10mm --wait', [('stage.move', {'x_m': 0.01, 'y_m': 0.0, 'speed_m_s': 0.005})]),
        ('move 80mm', []),                                                      # OUT_OF_RANGE (0..70 mm)
        ('contact on', [('head.set_position', {'distance_m': 0.0}), ('contact.set', {'engaged': True})]),
        ('move 12mm', []),                                                      # HEAD_NOT_CLEAR
        ('zero', []),                                                           # CONTACT_ENGAGED
        ('method mode=prescan start=10mm length=3mm', [('method.configure', T(RST, 'method.configure', pass_kind='prescan', start_x_m=0.01, length_m=0.003))]),
        ('start', [('acquisition.start', {})]),
        ('#wait 30', None),
        ('result', [('acquisition.result', {})]),
        ('contact off', [('contact.set', {'engaged': False}), ('head.set_position', {'distance_m': 0.04})]),
        ('move 10mm --wait', [('stage.move', {'x_m': 0.01, 'y_m': 0.0, 'speed_m_s': 0.005})]),
        ('contact on', [('head.set_position', {'distance_m': 0.0}), ('contact.set', {'engaged': True})]),
        ('method mode=scratch start=10mm length=3mm load-start=1N load-end=30N', [
            ('method.configure', T(RST, 'method.configure', pass_kind='scratch', start_x_m=0.01, length_m=0.003, initial_force_n=1.0, final_force_n=30.0))]),
        ('start', [('acquisition.start', {})]),
        ('#wait 30', None),
        ('result --save-to rst.json', [('acquisition.result', {})]),
        ('analyze threshold=1N', None),
        ('set-temp 30C', []),                                                   # not supported
        ('stop', []),                                                           # NOT_RUNNING
        ('calibrate normal-gain=1.01', [('sensor.calibrate', T(RST, 'sensor.calibrate', normal_gain_correction=1.01))]),
        ('contact off', [('contact.set', {'engaged': False}), ('head.set_position', {'distance_m': 0.04})]),
        ('clamp off', [('clamp.set', {'locked': False})]),
    ]},
    HIT: {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('zero', [('sensor.zero', {})]),
        ('contact on', []),                                                     # NOT_CLAMPED
        ('clamp on', [('clamp.set', {'locked': True})]),
        ('move 20mm --y 20mm --wait', [('stage.set_y', {'position_m': 0.02, 'accept_manual': True}),
                                       ('stage.move_x', {'position_m': 0.02, 'speed_m_s': 0.005})]),
        ('contact on', [('head.set_position', {'distance_m': 0.0}), ('contact.set', {'engaged': True})]),
        ('method load=5mN rate=0.2mN/s unload-rate=0.2mN/s hold=5s', [
            ('method.configure', T(HIT, 'method.configure', peak_load_n=0.005, loading_rate_n_s=0.0002, unloading_rate_n_s=0.0002, hold_s=5.0))]),
        ('method load=1N', []),                                                 # OUT_OF_RANGE (max 0.5 N)
        ('method rate=fast', []),                                               # BAD_NUMBER (usage)
        ('start', [('acquisition.start', {})]),
        ('#wait 150', None),
        ('result', [('acquisition.result', {})]),
        ('analyze poisson=0.2 --index 1', None),
        ('analyze --index 5', [('acquisition.result', {})]),                    # BAD_INDEX (2 indents)
        ('contact off', [('contact.set', {'engaged': False}), ('head.set_position', {'distance_m': 0.09})]),
        ('set-temp 30C', []),                                                   # not supported
        ('calibrate depth-gain=1.0', [('sensor.calibrate', T(HIT, 'sensor.calibrate', depth_gain_correction=1.0))]),
        ('zero', [('sensor.zero', {})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('clamp off', [('clamp.set', {'locked': False})]),
    ]},
    AGS: {'steps': [
        ('status', []),
        ('zero', [('force.zero', {})]),
        ('calibrate', []),                                                      # MISSING_SETTING (usage)
        ('method speed=1mm/min target=3mm', [('method.configure', T(AGS, 'method.configure', speed_m_s=1e-3 / 60, target_crosshead_m=0.003))]),
        ('method mode=strain', [('method.configure', T(AGS, 'method.configure', mode='strain'))]),   # runtime: EXTENSOMETER_REQUIRED
        ('method speed=1mm/min target=3mm', [('method.configure', T(AGS, 'method.configure', speed_m_s=1e-3 / 60, target_crosshead_m=0.003))]),
        ('start --dry-run', []),
        ('start', [('acquisition.start', {})]),
        ('#wait 20', None),
        ('result', [('acquisition.result', {})]),
        ('analyze area=2mm2 gauge=25mm', None),
        ('calibrate ref=850N', [('force.calibrate', {'reference_force_n': 850.0})]),   # the loaded specimen at rest as the reference
        ('contact on', []),                                                     # not supported
        ('clamp on', []),                                                       # not supported
        ('set-temp 30C', []),                                                   # not supported
        ('move 5mm --speed 10mm/min --wait', [('crosshead.move_to', {'position_m': 0.005, 'speed_m_s': 0.01 / 60})]),
        ('move 1m', []),                                                        # OUT_OF_RANGE (0..300 mm)
        ('calibrate ref=100N', [('force.calibrate', {'reference_force_n': 100.0})]),   # refused: NO_REFERENCE_FORCE (specimen broken)
        ('stop', []),                                                           # NOT_RUNNING
        ('power off', [('power.set', {'enabled': False})]),
        ('zero', []),                                                           # POWER_OFF
    ]},
    H5: {'steps': [
        ('start', []),                                                          # CALIBRATION_REQUIRED
        ('method interval=2ms time=10s', [('method.configure', T(H5, 'method.configure', sample_interval_s=0.002, timeout_s=10.0))]),
        ('calibrate x=1', []),                                                  # NO_SETTINGS (usage)
        ('calibrate --dry-run', []),
        ('calibrate', [('calibration.start', {})]),
        ('#wait 10', None),
        ('calibrate', []),                                                      # NOT_ARMED (the swing released the pendulum)
        ('result', [('acquisition.result', {})]),
        ('analyze', [('acquisition.result', {})]),                              # NO_RESULT: a calibration swing has no rebound
        ('move 1mm', []),                                                       # not supported
        ('zero', []),                                                           # not supported
        ('stop', []),                                                           # NOT_RUNNING
    ]},
    'hit5p_ready': {'steps': [
        ('status', []),
        ('calibrate', []),                                                      # EMPTY_REQUIRED (a bar on the supports)
        ('start', [('acquisition.start', {})]),
        ('#wait 10', None),
        ('result', [('acquisition.result', {})]),
        ('analyze area=32mm2', None),
        ('start', []),                                                          # SAMPLE_SPENT
    ]},
    MF: {'steps': [
        ('status', []),
        ('start', [('acquisition.start', {})]),                                 # runtime: PREHEAT_NOT_COMPLETE
        ('set-temp 250C', [('thermal.set_temperature', {'target_k': K0 + 250})]),
        ('set-temp 500C', []),                                                  # OUT_OF_RANGE (max 450 °C)
        ('method window=40mm..10mm time=300s', [('method.configure', T(MF, 'method.configure', window_upper_m=0.04, window_lower_m=0.01, duration_s=300.0))]),
        ('method window=40mm', []),                                             # BAD_VALUE: not a range
        ('method load=30kg', []),                                               # OUT_OF_RANGE (max 21.6 kg)
        ('#wait 300', None),
        ('start', [('acquisition.start', {})]),
        ('#wait 300', None),                                                    # the weight acts only after the preheat (zwick 1.1.3): ~280 s
        ('result', [('acquisition.result', {})]),
        ('analyze density=1.1g/cm3', None),
        ('clamp on', []), ('contact on', []), ('zero', []), ('calibrate', []), ('move 10mm', []),     # not supported
        ('stop', []),                                                           # NOT_RUNNING
    ]},
    MCR: {'steps': [
        ('zero', []),                                                           # SAMPLE_PRESENT (member requirement)
        ('start', [('acquisition.start', {})]),                                 # runtime: GAP_FILL_MISMATCH
        ('move 1mm --wait', [('geometry.set_gap', {'gap_m': 0.001})]),
        ('set-temp 25C --wait', [('thermal.set_temperature', {'temperature_k': K0 + 25})]),
        ('method mode=oscillation strain=10% freq=5Hz time=2s interval=1ms discard=1s', [
            ('method.configure', T(MCR, 'method.configure', mode='oscillation', strain_amplitude=0.1, frequency_hz=5.0, duration_s=2.0,
                                   sample_interval_s=0.001, discard_time_s=1.0))]),
        ('method mode=spin', []),                                               # BAD_CHOICE (usage)
        ('start', [('acquisition.start', {})]),
        ('#wait 5', None),
        ('result', [('acquisition.result', {})]),
        ('analyze strain=0.1 freq=5Hz', None),
        ('method mode=rotation shear-rate=100 time=2s interval=10ms', [
            ('method.configure', T(MCR, 'method.configure', mode='rotation', shear_rate_s_inv=100.0, duration_s=2.0, sample_interval_s=0.01))]),
        ('start', [('acquisition.start', {})]),
        ('#wait 5', None),
        ('analyze', [('acquisition.result', {})]),                              # NO_RESULT: not an oscillation run
        ('calibrate torque-gain=1.0 torque-zero=0', [('sensor.calibrate', {'torque_gain_correction': 1.0, 'torque_zero_nm': 0.0})]),
        ('contact on', []), ('clamp on', []),                                   # not supported
        ('stop', []),                                                           # NOT_RUNNING
    ]},
    'mcr_empty': {'steps': [
        ('zero --dry-run', []),
        ('zero', [('instrument.procedure', {'procedure': 'zero_gap'})]),
        ('move 2mm --wait', [('geometry.set_gap', {'gap_m': 0.002})]),
        ('move 100mm', []),                                                     # OUT_OF_RANGE (max 80 mm)
        ('start', []),                                                          # NO_SAMPLE
        ('set-temp 40C', [('thermal.set_temperature', {'temperature_k': K0 + 40})]),
        ('set-temp 150C', []),                                                  # OUT_OF_RANGE (max 100 °C)
    ]},
    FT4: {'steps': [
        ('clamp on', [('clamp.set', {'locked': True})]),
        ('start', []),                                                          # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('start', []),                                                          # DOOR_INTERLOCK
        ('close door', [('door.set', {'opened': False})]),
        ('clamp off', []),                                                      # DOOR_CLOSED (member requirement)
        ('move 10mm --wait', [('axis.move', {'position_m': 0.01, 'speed_m_s': 0.01})]),
        ('move 0mm --speed 20mm/s --wait', [('axis.move', {'position_m': 0.0, 'speed_m_s': 0.02})]),
        ('calibrate force-zero=0.01N torque-zero=0', [('sensor.calibrate', T(FT4, 'sensor.calibrate', force_zero_n=0.01, torque_zero_nm=0.0))]),
        ('method cycles=3', [('method.configure', T(FT4, 'method.configure', measurement_cycles=3))]),
        ('start', [('acquisition.start', {})]),
        ('#wait 60', None),
        ('result', [('acquisition.result', {})]),
        ('analyze --index 1', None),
        ('zero', []), ('contact on', []), ('set-temp 30C', []),                 # not supported
        ('stop', []),                                                           # NOT_RUNNING
    ]},
}
