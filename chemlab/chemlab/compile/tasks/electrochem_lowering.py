"""Lowering table of the electrochem class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts and the technique definitions (a cyclic sweep from E1 to E2 and
back in equal steps, a potential / current step held for its time, a probe path), not from the mapping.
[] = no native call (a class check refused, a read, a wait, or a verb this member does not have); None = not checked."""
from .electrochem_world import config  # noqa: F401  (the lowering harness builds its labs from this config)
from .sample_intro_lowering import example

RPM = 2 * 3.141592653589793 / 60


def sweep(e1, e2, step):
    n = int(round(abs(e2 - e1) / step))
    fwd = [round(e1 + (e2 - e1) * i / n, 9) for i in range(n + 1)]
    return fwd + list(reversed(fwd[:-1]))


CV = sweep(0.6, -0.2, 0.002)                                           # 801 points, 2 mV apart; 100 mV/s -> 20 ms per point
EQCM_REF = example('ch_chi440c', 'method.configure')['reference_electrode']
SECM = example('ch_chi920d', 'method.configure')
LINE = [{'x_m': -4e-05, 'y_m': -4e-05, 'z_m': 5e-06}, {'x_m': 0.0, 'y_m': -4e-05, 'z_m': 5e-06}, {'x_m': 4e-05, 'y_m': -4e-05, 'z_m': 5e-06}]
APPROACH = [{'x_m': 0.0, 'y_m': 0.0, 'z_m': 5e-05}, {'x_m': 0.0, 'y_m': 0.0, 'z_m': 2e-05}, {'x_m': 0.0, 'y_m': 0.0, 'z_m': 1e-05}]
GAMRY_PORTS = [('slot.set_connection', {'port_id': p, 'connection_id': i}) for p, i in
               (('working_electrode', 'we_01'), ('reference_electrode', 'ref_01'), ('counter_electrode', 'ce_01'), ('cell', 'cell_01'))]

CASES = {
    'gamry_reference620': {'steps': [
        ('start', []),                                                            # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('technique cv --from 0.6V --to -0.2V --rate 100mV/s --step 2mV',
         [('electrochem.configure_waveform', {'program': {'mode': 'potential', 'sample_interval_s': 0.02, 'values': CV}})]),
        ('technique qcm --time 1s', []),                                          # NOT_ON_THIS_INSTRUMENT (usage)
        ('technique ca --potential 0.5V', []),                                    # MISSING_OPTION (usage)
        ('technique ca --potential 12V --time 1s', []),                           # OUT_OF_RANGE (±11 V)
        ('technique cv --from 0V --to 1V --step 0.01mV', []),                     # TOO_MANY_POINTS
        ('start', []),                                                            # NOT_CONNECTED
        ('connect working=we_01 reference=ref_01 counter=ce_01 cell=cell_01', GAMRY_PORTS),
        ('connect anode=a1', []),                                                 # UNKNOWN_PORT (usage)
        ('start --dry-run', []),
        ('start', [('electrochem.start', {})]),
        ('technique ca --potential 0.5V --time 1s', []),                          # BUSY
        ('wait --max 10min', []),
        ('data', [('electrochem.read_series', {})]),
        ('technique steps --program 0.2V@0.5s,0.5V@0.3s --interval 0.1s',
         [('electrochem.configure_waveform', {'program': {'mode': 'potential', 'sample_interval_s': 0.1, 'values': [0.2] * 5 + [0.5] * 3}})]),
        ('start', [('electrochem.start', {})]),
        ('#wait 0.3', None),
        ('stop', [('electrochem.stop', {})]),
        ('stop', []),                                                             # NOT_RUNNING
        ('technique cp --current 1mA --time 0.5s --interval 0.1s',
         [('electrochem.configure_waveform', {'program': {'mode': 'current', 'sample_interval_s': 0.1, 'values': [0.001] * 5}})]),
        ('rotate 1000rpm', []),                                                   # not supported
        ('zero', []),                                                             # not supported
        ('move 0 0 0', []),                                                       # not supported
    ]},
    'ch_chi440c': {'steps': [
        ('technique cp --current 10uA --time 2s', [('method.configure', {'mode': 'current', 'sample_interval_s': 0.1, 'reference_electrode': EQCM_REF,
                                                                         'steps': [{'duration_s': 2.0, 'potential_v': 0.0, 'current_a': 1e-05}]})]),
        ('technique cv --from 0V --to 1V', []),                                   # NOT_ON_THIS_INSTRUMENT (usage)
        ('technique steps --program 0.1V@0.25s --interval 0.1s', []),             # BAD_STEP (usage)
        ('start', []),                                                            # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('zero', []),                                                             # NO_SIGNAL (no frequency yet)
        ('#wait 2', None),
        ('zero', [('baseline.zero', {})]),
        ('open lid', [('lid.set', {'lift_m': 0.05})]),
        ('start', []),                                                            # LID_OPEN
        ('close lid', [('lid.set', {'lift_m': 0})]),
        ('start', [('acquisition.start', {})]),
        ('zero', []),                                                             # BUSY
        ('wait', []),
        ('data', [('process.measure', {})]),
        ('technique steps --program 0.1V@1s,-0.1V@1s --reference Ag/AgCl',
         [('method.configure', {'mode': 'potential', 'sample_interval_s': 0.1, 'reference_electrode': 'Ag/AgCl',
                                'steps': [{'duration_s': 1.0, 'potential_v': 0.1, 'current_a': 0.0},
                                          {'duration_s': 1.0, 'potential_v': -0.1, 'current_a': 0.0}]})]),
        ('technique qcm --time 1s', [('method.configure', {'mode': 'qcm_only', 'sample_interval_s': 0.1, 'reference_electrode': 'Ag/AgCl',
                                                           'steps': [{'duration_s': 1.0, 'potential_v': 0.0, 'current_a': 0.0}]})]),
        ('start', [('acquisition.start', {})]),                                  # runtime: qcm only for an adsorbing film (WRONG_TECHNIQUE)
        ('technique ca --potential -0.1V --time 1s', [('method.configure', {'mode': 'potential', 'sample_interval_s': 0.1, 'reference_electrode': 'Ag/AgCl',
                                                                            'steps': [{'duration_s': 1.0, 'potential_v': -0.1, 'current_a': 0.0}]})]),   # cathodic: plates
        ('start', [('acquisition.start', {})]),
        ('#wait 0.5', None),
        ('stop', [('acquisition.stop', {})]),
        ('stop', []),                                                             # NOT_RUNNING
        ('connect working=we_01', []),                                            # not supported
        ('rotate 100rpm', []),                                                    # not supported
    ]},
    'ch_chi920d': {'steps': [
        ('move 0 0 1mm', []),                                                     # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('move 0 0 1mm', [('stage.move', {'x_m': 0.0, 'y_m': 0.0, 'z_m': 0.001})]),
        ('move 0 0 60mm', []),                                                    # OUT_OF_RANGE (0..50 mm)
        ('start', []),                                                            # NO_TECHNIQUE
        ('technique secm-line --path -40um,-40um,5um;0um,-40um,5um;40um,-40um,5um --tip 0.5V --substrate 0V',
         [('method.configure', {'mode': 'constant_height', 'path': LINE, 'tip_potential_v': 0.5, 'substrate_potential_v': 0.0,
                                'dwell_s': SECM['dwell_s'], 'reference_electrode': SECM['reference_electrode'], 'min_gap_m': SECM['min_gap_m']})]),
        ('technique ca --potential 1V --time 1s', []),                            # NOT_ON_THIS_INSTRUMENT (usage)
        ('technique secm-line --path 1,2', []),                                   # BAD_PATH (usage)
        ('start', [('acquisition.start', {})]),
        ('move 0 0 2mm', []),                                                     # BUSY
        ('wait', []),
        ('data --save-to secm.json', [('process.measure', {})]),
        ('technique secm-approach --path 0,0,50um;0,0,20um;0,0,10um --tip 0.5V --substrate 0V --dwell 0.5s',
         [('method.configure', {'mode': 'approach', 'path': APPROACH, 'tip_potential_v': 0.5, 'substrate_potential_v': 0.0, 'dwell_s': 0.5,
                                'reference_electrode': SECM['reference_electrode'], 'min_gap_m': SECM['min_gap_m']})]),
        ('start', [('acquisition.start', {})]),
        ('stop', [('acquisition.stop', {})]),
        ('stop', []),                                                             # NOT_RUNNING
        ('zero', []),                                                             # not supported
    ]},
    'pine_wavevortex10': {'steps': [
        ('rotate 1000rpm', []),                                                   # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('open enclosure', [('enclosure.set', {'lift_m': 0.285})]),
        ('start --time 3s', []),                                                  # ENCLOSURE_OPEN
        ('close enclosure', [('enclosure.set', {'lift_m': 0})]),
        ('start --time 3s', []),                                                  # ROTATION_OFF
        ('rotate 50rpm', []),                                                     # OUT_OF_RANGE (100..8000 rpm)
        ('rotate 1000rpm', [('speed.set', {'angular_speed_rad_s': 1000 * RPM}), ('motor.run', {'enabled': True})]),
        ('rotate 1600rpm', [('speed.set', {'angular_speed_rad_s': 1600 * RPM})]),
        ('start', []),                                                            # MISSING_OPTION (usage)
        ('start --time 3s', [('acquisition.start', {'duration_s': 3.0, 'sample_interval_s': 0.1})]),
        ('rotate 4000rpm', [('speed.set', {'angular_speed_rad_s': 4000 * RPM})]),   # runtime: at most 3000 rpm while recording
        ('wait', []),
        ('data', [('process.measure', {})]),
        ('start --time 3s --interval 0.5s', [('acquisition.start', {'duration_s': 3.0, 'sample_interval_s': 0.5})]),
        ('stop', [('acquisition.stop', {})]),
        ('stop', []),                                                             # NOT_RUNNING
        ('rotate off', [('motor.run', {'enabled': False})]),
        ('technique cv --from 0V --to 1V', []),                                   # not supported
        ('zero', []),                                                             # not supported
    ]},
}
