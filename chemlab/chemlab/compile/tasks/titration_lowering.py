"""Lowering table of the titration class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts (input schemas and documented examples), not from the mapping.
[] = no native call (a class check refused, a read, a wait, or a verb this member does not have); None = not checked."""
from .sample_intro_lowering import example
from .titration_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

RPM = 2 * 3.141592653589793 / 60
UDK_T = example('velp_udk159', 'method.configure', titrant_equivalents_mol_m3=100.0)
UDK_M = dict(UDK_T, base_volume_m3=8e-05, boric_volume_m3=3.5e-05)

CASES = {
    'metrohm_omnis': {'steps': [
        ('start', []),                                                            # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('start', []),                                                            # NOT_CONNECTED
        ('connect electrode=electrode_01 cell=beaker_01', [('slot.set_connection', {'port_id': 'electrode', 'connection_id': 'electrode_01'}),
                                                          ('slot.set_connection', {'port_id': 'titration_cell', 'connection_id': 'beaker_01'})]),
        ('connect electrode=electrode_01', []),                                   # already connected: nothing to send
        ('connect reference=ref_01', []),                                         # UNKNOWN_PORT (usage)
        ('start', []),                                                            # NO_TITRANT
        ('titrant 0.1M --cylinder 30mL', []),                                     # OUT_OF_RANGE (5 / 10 / 20 / 50 mL)
        ('titrant 0.1M --cylinder 20mL', [('dosing.configure_cylinder', {'volume_m3': 2e-05}),
                                          ('titration.configure_titrant', {'concentration_mol_m3': 100.0})]),
        ('method mode=dynamic increment=0.1mL max=15mL', [('titration.configure_method', {'mode': 'dynamic_equivalent', 'dose_increment_m3': 1e-07,
                                                                                           'maximum_volume_m3': 1.5e-05, 'endpoint_potential_v': None})]),
        ('method hold=0.5s tolerance=0V rate=30mL/min', [('titration.configure_stability', {'hold_s': 0.5, 'tolerance_v': 0.0}),
                                                         ('dosing.set_rate', {'rate_m3_s': 5e-07})]),
        ('method mode=endpoint ep=0.1V --dry-run', []),
        ('method pH=7', []),                                                      # UNKNOWN_SETTING
        ('start', []),                                                            # NOT_STIRRING
        ('stir on --level 3', []),                                                # NEEDS_SPEED (usage)
        ('stir 5000rpm', []),                                                     # OUT_OF_RANGE (0..1800 rpm)
        ('stir 300rpm', [('stirring.set_speed', {'angular_speed_rad_s': 300 * RPM})]),
        ('result --sample 25mL', []),                                             # NO_ENDPOINT
        ('start --dry-run', []),
        ('start', [('titration.start', {})]),
        ('stir off', []),                                                         # BUSY (stirring must go on)
        ('titrant 0.2M', []),                                                     # BUSY
        ('wait --max 1h', []),
        ('curve', [('titration.read_curve', {})]),
        ('result --sample 25mL', [('titration.calculate_content', {'sample_volume_m3': 2.5e-05, 'stoichiometric_ratio': 1.0})]),
        ('result --sample 25mL --ratio 0.5 --save-to omnis.json', [('titration.calculate_content', {'sample_volume_m3': 2.5e-05, 'stoichiometric_ratio': 0.5})]),
        ('result', []),
        ('start', []),                                                            # ALREADY_DOSED
        ('stop', []),                                                             # NOT_RUNNING
        ('stir off', [('stirring.set_speed', {'angular_speed_rad_s': 0.0})]),
        ('sample 1g', []),                                                        # not supported
    ]},
    'metrohm_899': {'steps': [
        ('start', []),                                                            # NO_METHOD (no end-point hold)
        ('method current=300mA', []),                                             # OUT_OF_RANGE (100 / 200 / 400 mA)
        ('method current=400mA drift-comp=0ug/min hold=1s', [('coulometry.configure', {'current_a': 0.4, 'drift_compensation_kg_s': 0.0,
                                                                                        'endpoint_hold_s': 1.0})]),
        ('method stir=9 start-drift=20ug/min', [('coulometry.configure', {'stirring_level': 9, 'start_drift_threshold_kg_s': 20e-9 / 60})]),
        ('sample 10mg', [('coulometry.configure', {'sample_mass_kg': 1e-05})]),
        ('stir 300rpm', []),                                                      # NEEDS_LEVEL (usage)
        ('stir on --level 8', [('stirrer.set', {'on': True, 'rate': 8})]),
        ('stir off', [('stirrer.set', {'on': False})]),
        ('titrant 0.1M', []),                                                     # not supported
        ('start --dry-run', []),
        ('start', [('coulometry.start', {})]),
        ('method hold=2s', []),                                                   # BUSY
        ('start', []),                                                            # BUSY
        ('wait --max 1h', []),
        ('curve --save-to kf_curve.json', [('coulometry.curve', {})]),
        ('result', [('coulometry.reading', {})]),
        ('stop', []),                                                             # NOT_RUNNING
        ('connect cell=cell_01', []),                                             # not supported
    ]},
    'velp_udk159': {'steps': [
        ('titrant 0.1M', [('method.configure', UDK_T)]),
        ('titrant 0.1M --cylinder 20mL', []),                                     # NOT_ON_THIS_INSTRUMENT (usage)
        ('method base=80mL boric=35mL', [('method.configure', UDK_M)]),
        ('method steam=5%', []),                                                  # OUT_OF_RANGE (10..100 %)
        ('open door', [('door.set_open', {'open': True})]),
        ('start', []),                                                            # DOOR_OPEN
        ('close door', [('door.set_open', {'open': False})]),
        ('#wait 10', None),
        ('start', [('acquisition.start', {})]),
        ('method water=40mL', []),                                                # BUSY
        ('#wait 30', None),
        ('stop', [('acquisition.stop', {})]),
        ('stop', []),                                                             # NOT_RUNNING
        ('start', []),                                                            # COOLING (then NOT_IDLE: the stopped tube is still in place)
        ('curve', [('curve.read', {})]),
        ('result', [('analysis.measure', {})]),
        ('result --sample 1mL', []),                                              # NOT_ON_THIS_INSTRUMENT (usage)
        ('stir 100rpm', []),                                                      # not supported
        ('sample 1g', []),                                                        # not supported
    ]},
}
