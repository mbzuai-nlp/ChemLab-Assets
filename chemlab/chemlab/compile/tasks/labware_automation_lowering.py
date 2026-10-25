"""Lowering table of the labware automation class: per member, verbs in order and the native calls (capability, arguments in the
unit the argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, an unsupported verb, a usage error, a dry run, or the part is already where it should be).  The lab is the labware world
(tasks/labware_automation_world.py: sealer plate and film, decapper tube, a 96-rack, the arm scene with named places)."""
import math

from .labware_automation_world import ARM, config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
HOME = ARM['home']
PLATE = ARM['containers']['plate_1']['pose']
NEST = ARM['ports']['sealer_nest']['pose']
A4S = 'azenta_automated_roll_heat_sealer_a4s'

CASES = {
    A4S: {'steps': [
        ('status', []),
        ('start', []),                                                         # NO_TIME
        ('configure time=3s', [('sealing.set_time', {'press_time_s': 3.0})]),
        ('configure time=30s', []),                                            # OUT_OF_RANGE (usage)
        ('configure colour=red', []),                                          # UNKNOWN_SETTING (usage)
        ('set-temp 170C', [('thermal.set_temperature', {'temperature_K': K0 + 170})]),
        ('set-temp 250C', []),                                                 # OUT_OF_RANGE
        ('start', []),                                                         # NOT_AT_TEMPERATURE
        ('#wait 400', None),
        ('close carrier', []),                                                 # PLATE_ON_CARRIER
        ('open carrier', []),                                                  # already out: nothing to do
        ('start --dry-run', []),
        ('start', [('sealing.seal_plate', {})]),
        ('stop', [('sealing.cancel', {'operation_id': 'seal_%s_1' % A4S})]),
        ('stop', []),                                                          # NOT_RUNNING
        ('result', [('sealing.read', {})]),
        ('open carrier', [('carrier.open', {})]),                              # a cancelled seal leaves the carrier in
        ('start', [('sealing.seal_plate', {})]),
        ('#wait 10', None),
        ('result', [('sealing.read', {})]),
        ('start', []),                                                         # ALREADY_SEALED
        ('configure protocol=4ti-0520', [('protocol.select', {'name': '4ti-0520', 'password': ''})]),
        ('configure protocol=4ti-052', []),                                    # UNKNOWN_PROTOCOL (usage)
        ('move home', []),                                                     # not supported
        ('power off', [('power.set', {'enabled': False})]),                    # shared layer
        ('configure time=2s', []),                                             # POWER_OFF
    ]},
    'azenta_intellixcap_s1_46_0001': {'steps': [
        ('status', []),
        ('start', []),                                                         # NO_TUBE_TYPE
        ('configure tube=tube_15ml mode=decap operation=release_cap time=2',
         [('consumable.select', {'consumable_id': 'tube_15ml'}), ('cycle.set_mode', {'mode': 'decap'}),
          ('cycle.set_operation_mode', {'mode': 'release_cap'}), ('cycle.set_time_setting', {'setting': 2})]),
        ('configure time=5', []),                                              # NOT_ALLOWED (1, 2 or 3)
        ('configure mode=sideways', []),                                       # BAD_CHOICE
        ('start', [('cycle.start', {})]),
        ('configure mode=recap', []),                                          # BUSY (a cycle runs)
        ('#wait 15', None),
        ('result', [('cycle.read', {})]),
        ('configure mode=recap', [('cycle.set_mode', {'mode': 'recap'})]),
        ('start', [('cycle.start', {})]),
        ('#wait 15', None),
        ('result', [('cycle.read', {})]),
        ('stop', []),                                                          # not supported
        ('set-temp 30C', []),                                                  # not supported
        ('power off', [('power.set', {'enabled': False})]),
        ('start', []),                                                         # POWER_OFF
    ]},
    'ziath_dp5_mirage_2023': {'steps': [
        ('status', []),
        ('start', []),                                                         # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('open rack', [('scene.set_rack_lift', {'lift_m': 0.05})]),
        ('open rack', []),                                                     # already lifted
        ('start', []),                                                         # RACK_RAISED
        ('close rack', [('scene.set_rack_lift', {'lift_m': 0.0})]),
        ('configure format=48', []),                                           # FORMAT_MISMATCH (a 96 rack is on the reader)
        ('configure matching=off', [('configuration.set', {'rack_format': 96, 'identity_matching': False, 'export_format': 'json'})]),
        ('configure matching=on export=csv', [('configuration.set', {'rack_format': 96, 'identity_matching': True, 'export_format': 'csv'})]),
        ('result', []),                                                        # NO_RESULT
        ('start', [('acquisition.start', {})]),
        ('stop', [('acquisition.cancel', {'operation_id': 'scan_ziath_dp5_mirage_2023_1'})]),
        ('stop', []),                                                          # NOT_RUNNING
        ('start', [('acquisition.start', {})]),
        ('#wait 5', None),
        ('result', [('results.export', {'format': 'csv'})]),
        ('result --format json', [('results.export', {'format': 'json'})]),
        ('set-temp 30C', []),                                                  # not supported
        ('pick plate_1', []),                                                  # not supported
    ]},
    'brooks_preciseflex_400': {'steps': [
        ('status', []),
        ('move home', []),                                                     # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('move home', []),                                                     # already there
        ('move nowhere', []),                                                  # UNKNOWN_LOCATION (usage)
        ('move z=100mm', [('motion.set_joints', {'joints': {'z_m': 0.1, 'shoulder_rad': 0.0, 'elbow_rad': 0.0, 'wrist_rad': 0.0}})]),
        ('move shoulder=200deg', []),                                          # OUT_OF_RANGE
        ('move z=50mm shoulder=30deg elbow=-45deg wrist=0deg',
         [('motion.set_joints', {'joints': {'z_m': 0.05, 'shoulder_rad': math.pi / 6, 'elbow_rad': -math.pi / 4, 'wrist_rad': 0.0}})]),
        ('place sealer_nest', []),                                             # NOT_HOLDING
        ('pick plate_1', [('motion.set_joints', {'joints': PLATE}), ('sample.attach', {'container_id': 'plate_1', 'constraint_id': 'grip_plate_1'})]),
        ('pick plate_1', []),                                                  # HOLDING
        ('place bench', []),                                                   # UNKNOWN_LOCATION
        ('place plate_1', []),                                                 # NOT_A_NEST
        ('place sealer_nest --dry-run', []),
        ('place sealer_nest', [('motion.set_joints', {'joints': NEST}), ('sample.release', {'receiver_port_id': 'sealer_nest'})]),
        ('result', [('state.read', {})]),
        ('start', []),                                                         # not supported
        ('configure speed=1', []),                                             # not supported
        ('move home', [('motion.set_joints', {'joints': HOME})]),
        ('power off', [('power.set', {'enabled': False})]),
    ]},
}
