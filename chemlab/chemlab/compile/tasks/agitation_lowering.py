"""Lowering table of the agitation class: per member, verbs in order and the native calls each must make (capability, arguments in
the declared unit), written from the contracts.  [] = no native call.  A few steps use verbs of the other parts of a composite
instrument (shared `power on`, `close lid`, `open hood`; the thermal class's set-temp / heat on the KS 4000 and the S60H) to bring
the instrument into the state the next class verb needs.  World: agitation_world.config()."""
import math

from .agitation_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
RPM = 2 * math.pi / 60
W2000G = math.sqrt(2000 * 9.80665 / 0.2)                     # 2000 x g on the large rotor (positions at 0.2 m)


def cf(speed, duration, temp=K0 + 4):
    return ('centrifuge.set_method', {'speed_rad_s': speed, 'temperature_k': temp, 'duration_s': duration, 'acceleration_level': 9, 'braking_level': 9})


def vcx(amp, duration):
    return ('method.configure', {'amplitude_fraction': amp, 'duration_s': duration, 'pulse_enabled': False, 'pulse_on_s': 1, 'pulse_off_s': 1,
                                 'energy_endpoint_J': 0, 'probe': '630-0220'})


CASES = {
    'eppendorf_5910ri': {'steps': [                                   # large rotor selected, lid open, tubes registered by the host
        ('status', []),
        ('start', []),                                                # LID_OPEN
        ('load tube_a', []),                                          # MISSING_SLOT (usage)
        ('load tube_a --slot slot_1', [('sample.load', {'slot_id': 'slot_1', 'sample_id': 'tube_a'})]),
        ('load tube_c --slot slot_2', [('sample.load', {'slot_id': 'slot_2', 'sample_id': 'tube_c'})]),   # out of balance (60 g vs 50 g)
        ('unload --slot slot_2', [('sample.unload', {'slot_id': 'slot_2'})]),
        ('load tube_b --slot slot_2', [('sample.load', {'slot_id': 'slot_2', 'sample_id': 'tube_b'})]),
        ('speed 2000rpm --rcf 1000g', []),                            # BOTH_SPEEDS (usage)
        ('speed --rcf 2000g --time 2min', [cf(W2000G, 120.0)]),       # temperature, acceleration, braking kept from the set points
        ('set-temp 50C', []),                                         # OUT_OF_RANGE (-11..40 °C)
        ('set-temp 4C', [cf(W2000G, 120.0)]),
        ('close lid', [('lid.set_angle', {'angle_rad': 0})]),         # shared verb
        ('load tube_c --slot slot_3', []),                            # LID_CLOSED
        ('start --wait', [('centrifuge.measure', {}), ('centrifuge.get_curve', {})]),
        ('result --save-to run.json', [('centrifuge.get_curve', {})]),
        ('time 30s', [cf(W2000G, 30.0)]),
        ('start', [('centrifuge.measure', {})]),
        ('time 1min', []),                                            # BUSY
        ('#wait 5', None),
        ('stop', [('centrifuge.stop', {})]),
        ('#wait 120', None),
        ('stop', []),                                                 # NOT_RUNNING
        ('intensity 40%', []),                                        # not supported
        ('speed 3000rpm --dry-run', []),
    ]},
    'thinky_are310s': {'steps': [                                     # cup (140 g gross) in the holder, lid open
        ('status', []),
        ('start', []),                                                # NO_METHOD
        ('speed 2000rpm', []),                                        # MISSING_TIME (usage)
        ('load --mass 140g', [('balance.set', {'gross_mass_kg': 0.14})]),
        ('speed 2000rpm --time 60s --defoam 2200rpm --defoam-time 30s', [('method.configure', {
            'program': [{'mode': 'mix', 'duration_s': 60.0, 'revolution_rpm': 2000.0}, {'mode': 'defoam', 'duration_s': 30.0, 'revolution_rpm': 2200.0}],
            'speed_envelope': 'family_SCE_reference_not_UL_verified'})]),
        ('speed 2500rpm --time 60s', []),                             # OUT_OF_RANGE (mix up to 2000 rpm)
        ('start', []),                                                # LID_OPEN
        ('close lid', [('lid.set_open', {'open': False})]),           # shared verb
        ('start', []),                                                # LID_MOVING (the lid is still closing)
        ('#wait 2', None),
        ('start --wait', [('acquisition.start', {}), ('mixing.measure', {})]),
        ('result', [('mixing.measure', {})]),
        ('load --mass 100g', []),                                     # LID_CLOSED
        ('time 60s', []),                                             # not supported
        ('stop', []),                                                 # NOT_RUNNING
    ]},
    'ika_ks4000': {'steps': [                                         # fixture and one flask on the platform, hood closed, power off
        ('speed 200rpm', []),                                         # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),             # thermal class verb on this composite
        ('speed 200.5rpm', []),                                       # BAD_STEP (whole rpm)
        ('speed 600rpm', []),                                         # OUT_OF_RANGE
        ('speed 200rpm --time 2min', [('shaking.set_speed', {'speed_rad_s': 200 * RPM}), ('timer.configure', {'mode': 'timed', 'duration_s': 120.0})]),
        ('start', [('shaking.start', {})]),
        ('time 5min', []),                                            # BUSY
        ('speed 250rpm', [('shaking.set_speed', {'speed_rad_s': 250 * RPM})]),   # speed may change in a run
        ('#wait 30', None),
        ('stop', [('shaking.stop', {})]),
        ('result', [('status.measure', {})]),
        ('time continuous', [('timer.configure', {'mode': 'continuous', 'duration_s': 120.0})]),
        ('open hood', [('hood.set_angle', {'angle_rad': 1.5707963267948966})]),   # shared verb
        ('start', []),                                                # HOOD_OPEN
        ('set-temp 37C', [('thermal.set_temperature', {'temperature_k': K0 + 37})]),   # thermal class verb
        ('load flask --slot 4', []),                                  # not supported
        ('intensity sweep', []),                                      # not supported
    ]},
    'ika_t25': {'steps': [                                            # vessel and clamped tool, power off
        ('start', []),                                                # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),             # shared verb
        ('start', []),                                                # NO_METHOD
        ('time 60s', []),                                             # OUT_OF_RANGE (up to 55 s)
        ('time 30s', [('method.configure', {'speed_rad_s': 3000 * RPM, 'duration_s': 30.0, 'head': 's25_n18g'})]),
        ('speed 30000rpm', []),                                       # OUT_OF_RANGE
        ('speed 15000rpm', [('motor.set_speed', {'speed_rad_s': 15000 * RPM})]),
        ('start --wait', [('acquisition.start', {}), ('dispersion.measure', {})]),
        ('result', [('dispersion.measure', {})]),
        ('stop', []),                                                 # NOT_RUNNING
        ('intensity 40%', []),                                        # not supported
        ('set-temp 30C', []),                                         # not supported
    ]},
    'sonics_vcx750': {'steps': [                                      # vessel and clamped probe, power on
        ('status', []),
        ('intensity 40%', []),                                        # NO_METHOD
        ('time 60s', [vcx(0.2, 60.0)]),                               # amplitude kept (20 %), pulser off, no energy end point
        ('intensity 10%', []),                                        # OUT_OF_RANGE (20..100 %)
        ('intensity 40%', [('sonication.set_amplitude', {'amplitude_fraction': 0.4})]),
        ('safety-limit 40C', [('thermal.set_temperature', {'temperature_K': K0 + 40})]),
        ('safety-limit 200C', []),                                    # OUT_OF_RANGE
        ('start --wait', [('acquisition.start', {}), ('sonication.measure', {})]),
        ('result', [('sonication.measure', {})]),
        ('time 2min', [vcx(0.4, 120.0)]),
        ('start', [('acquisition.start', {})]),
        ('#wait 10', None),
        ('stop', [('acquisition.stop', {})]),
        ('speed 100rpm', []),                                         # not supported
        ('set-temp 30C', []),                                         # not supported (alternative: safety-limit)
    ]},
    'elma_elmasonics60h': {'steps': [                                 # tank filled, power off
        ('intensity sweep', []),                                      # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),             # thermal class verb on this composite
        ('intensity sweep', [('ultrasound.set_mode', {'mode': 'sweep'})]),
        ('intensity loud', []),                                       # BAD_CHOICE
        ('time 2min', [('timer.configure', {'continuous': False, 'duration_s': 120.0})]),
        ('time 1h', []),                                              # OUT_OF_RANGE (up to 30 min)
        ('start --at-temp', []),                                      # HEATING_OFF
        ('start --wait', [('ultrasound.start', {'temperature_controlled': False}), ('status.measure', {})]),
        ('result', [('status.measure', {})]),
        ('time continuous', [('timer.configure', {'continuous': True, 'duration_s': 120.0})]),
        ('start', [('ultrasound.start', {'temperature_controlled': False})]),
        ('#wait 10', None),
        ('stop', [('ultrasound.stop', {})]),
        ('set-temp 50C', [('thermal.set_temperature', {'temperature_k': K0 + 50})]),   # thermal class verb
        ('heat on', [('thermal.set_enabled', {'enabled': True})]),                     # thermal class verb
        ('start --at-temp --dry-run', []),
        ('speed 100rpm', []),                                         # not supported
    ]},
}
