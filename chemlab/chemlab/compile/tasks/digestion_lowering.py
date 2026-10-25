"""Lowering table of the digestion class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, a read, or a wait); None = not checked.  Shared verbs (power, open, close) appear only where a step needs them."""
from .digestion_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
MAT = {'material_id': 'nist_srm_2711a', 'method_id': 'epa_3051a_hno3', 'sample_mass_kg': 0.0002, 'acid_volume_m3': 4e-06}
LOAD = '--material nist_srm_2711a --method epa_3051a_hno3 --mass 0.2g --acid 4mL'

CASES = {
    'multiwave_7101': {'steps': [
        ('status', []),
        ('set-temp 180C', []),                                                  # not supported (method steps)
        ('method temp=180C ramp=15min hold=15min name=MYDIG', [
            ('methods.create', {'name': 'MYDIG', 'program': 'temperature', 'max_power_W': 1500.0, 'max_temperature_C': 300.0}),
            ('methods.set_step', {'name': 'MYDIG', 'index': 0, 'temperature_C': 180.0, 'time_s': 900.0, 'step_type': 'time', 'fan': 1}),
            ('methods.add_step', {'name': 'MYDIG'}),
            ('methods.set_step', {'name': 'MYDIG', 'index': 1, 'temperature_C': 180.0, 'time_s': 900.0, 'step_type': 'time', 'fan': 1})]),
        ('method temp=170C ramp=10min hold=10min name=MYDIG power=1000W --dry-run', []),
        ('method program=150C@5min,180C@5min hold=10min name=MYDIG fan=2', [
            ('methods.delete', {'name': 'MYDIG'}),
            ('methods.create', {'name': 'MYDIG', 'program': 'temperature', 'max_power_W': 1500.0, 'max_temperature_C': 300.0}),
            ('methods.set_step', {'name': 'MYDIG', 'index': 0, 'temperature_C': 150.0, 'time_s': 300.0, 'step_type': 'time', 'fan': 2}),
            ('methods.add_step', {'name': 'MYDIG'}),
            ('methods.set_step', {'name': 'MYDIG', 'index': 1, 'temperature_C': 180.0, 'time_s': 300.0, 'step_type': 'time', 'fan': 2}),
            ('methods.add_step', {'name': 'MYDIG'}),
            ('methods.set_step', {'name': 'MYDIG', 'index': 2, 'temperature_C': 180.0, 'time_s': 600.0, 'step_type': 'time', 'fan': 2})]),
        ('method temp=350C hold=10min', []),                                    # OUT_OF_RANGE (300 °C)
        ('method name=NOPE', []),                                               # UNKNOWN_METHOD
        ('method name="EPA 3051A HNO3"', [('methods.select', {'name': 'EPA 3051A HNO3'})]),
        ('load 1 ' + LOAD, []),                                                 # DOOR_CLOSED
        ('open door', [('door.open', {})]),
        ('load 1 --mass 0.2g', []),                                             # MISSING_OPTION
        ('load 1,2 ' + LOAD, [('rack.set_slot', {'slot_index': 0, 'occupied': True}), ('digestion.load_template', dict(MAT, slot_index=0)),
                              ('rack.set_slot', {'slot_index': 1, 'occupied': True}), ('digestion.load_template', dict(MAT, slot_index=1))]),
        ('load 30', []),                                                        # BAD_POSITION
        ('start', []),                                                          # DOOR_OPEN
        ('close door', [('door.close', {})]),
        ('result', []),                                                         # MISSING_SLOT (two vessels)
        ('start', [('run.start', {})]),
        ('method name="EPA 3051A HNO3"', []),                                   # BUSY
        ('stop', [('run.abort', {})]),
        ('stop', []),                                                           # ALREADY_COOLING
        ('#wait 7200', None),
        ('result --slot 1', [('digestion.evaluate', {'slot_index': 0})]),
        ('preheat on', []),                                                     # not supported
    ]},
    'scp_digiprepms': {'steps': [
        ('start', []),                                                          # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('set-temp 200C', []),                                                  # OUT_OF_RANGE (20-180 °C)
        ('method temp=95C ramp=10min hold=20min', [('method.configure', {
            'knots': [{'time_s': 0.0, 'temperature_k': 293.15}, {'time_s': 600.0, 'temperature_k': K0 + 95}], 'hold_s': 1200.0, 'timeout_s': 9000.0})]),
        ('method program=60C@5min,90C@10min hold=10min --dry-run', []),
        ('method program=60C@5min,90C@10min hold=10min timeout=3h', [('method.configure', {
            'knots': [{'time_s': 0.0, 'temperature_k': 293.15}, {'time_s': 300.0, 'temperature_k': K0 + 60}, {'time_s': 900.0, 'temperature_k': K0 + 90}],
            'hold_s': 600.0, 'timeout_s': 10800.0})]),
        ('method speed=3', []),                                                 # UNKNOWN_SETTING
        ('set-temp 90C', [('thermal.set_temperature', {'temperature_k': K0 + 90})]),   # refused by the runtime: RAMP_PROGRAM (1.0.1)
        ('load 1', []),                                                         # not supported (operator places the tubes)
        ('start', [('acquisition.start', {})]),
        ('method temp=80C hold=5min', []),                                      # BUSY
        ('#wait 60', None),
        ('stop', [('acquisition.stop', {})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('set-temp 80C', [('thermal.set_temperature', {'temperature_k': K0 + 80})]),   # refused by the runtime: RAMP_PROGRAM
        ('result', [('digestion.measure', {})]),
        ('start', [('acquisition.start', {})]),                                 # a cancelled cycle resumes
        ('#wait 9000', None),
        # DigiPREP 1.0.1: after a finished or stopped cycle a new one starts (same programme, or a new method / temperature)
        ('start', [('acquisition.start', {})]),                                 # second cycle, same programme
        ('#wait 60', None),
        ('stop', [('acquisition.stop', {})]),
        ('method temp=80C hold=5min', [('method.configure', {'knots': [{'time_s': 0.0, 'temperature_k': K0 + 80}], 'hold_s': 300.0,
                                                             'timeout_s': 7500.0})]),
        ('set-temp 85C', [('thermal.set_temperature', {'temperature_k': K0 + 85})]),   # a single-temperature programme takes it
        ('start', [('acquisition.start', {})]),                                 # third cycle, the new programme
        ('set-temp 90C', []),                                                   # BUSY (class requirement)
        ('#wait 2400', None),
        ('result', [('digestion.measure', {})]),
        ('result --slot 1', []),                                                # NO_SLOTS
    ]},
    'metrohm_909uv': {'steps': [
        ('method temp=90.5C hold=30min', []),                                   # BAD_STEP (whole °C)
        ('method temp=90C hold=30.5min', []),                                   # BAD_STEP (whole minutes)
        ('method temp=90C', []),                                                # MISSING_SETTING
        ('method temp=90C hold=30min', [('method.configure', {'temperature_K': K0 + 90, 'duration_s': 1800.0})]),
        ('set-temp 120C', []),                                                  # OUT_OF_RANGE (80-100 °C)
        ('set-temp 95C', [('thermal.set_temperature', {'temperature_K': K0 + 95})]),
        ('start', []),                                                          # SHIELD_OPEN
        ('close shield', [('shield.set_open', {'open': False})]),
        ('start', []),                                                          # SHIELD_MOVING
        ('#wait 2', None),
        ('start --dry-run', []),
        ('start', [('acquisition.start', {})]),
        ('stop', [('acquisition.stop', {})]),
        ('start', []),                                                          # COOLING (10 min fan)
        ('result', [('digestion.measure', {})]),
        ('result --slot 1', []),                                                # NO_SLOTS
        ('load 1', []),                                                         # not supported
        ('preheat on', []),                                                     # not supported
    ]},
    'buchi_hydrolexh': {'steps': [
        ('method rinse-temp=45C', []),                                          # MISSING_SETTING (hold)
        ('method hold=30min', [('method.configure', {'hydrolysis_s': 1800.0, 'rinse_water_K': 323.15, 'minimum_rinse_cycles': 3,
                                                     'minimum_rinse_m3': 0.0004, 'accept_host_manual_sequence_automation': True})]),
        ('method hold=20min rinse-temp=45C rinse-cycles=4 rinse-volume=300mL', [('method.configure', {
            'hydrolysis_s': 1200.0, 'rinse_water_K': K0 + 45, 'minimum_rinse_cycles': 4, 'minimum_rinse_m3': 0.0003,
            'accept_host_manual_sequence_automation': True})]),
        ('preheat on', []),                                                     # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('preheat on', [('thermal.set_mode', {'mode': 'preheat'})]),
        ('start', []),                                                          # NOT_PREHEATED
        ('preheat on --wait', []),                                              # already preheating: waits 10 min
        ('start', []),                                                          # RACK_UP
        ('load 1,3 --mass 2g', []),                                             # NOT_RECORDED (usage)
        ('load 1,3', [('valve.set', {'position': 1, 'open': True}), ('valve.set', {'position': 2, 'open': False}),
                      ('valve.set', {'position': 3, 'open': True}), ('valve.set', {'position': 4, 'open': False}),
                      ('valve.set', {'position': 5, 'open': False}), ('valve.set', {'position': 6, 'open': False}),
                      ('rack.set_raised', {'raised': False})]),
        ('set-temp 100C', []),                                                  # not supported
        ('start', [('acquisition.start', {})]),
        ('load 1,3', []),                                                       # BUSY
        ('stop', [('acquisition.stop', {})]),
        ('result', [('hydrolysis.measure', {})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('preheat off', []),                                                    # the stop switched the heater off already
    ]},
}
