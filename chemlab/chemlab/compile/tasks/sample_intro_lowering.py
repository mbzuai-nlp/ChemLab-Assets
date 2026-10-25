"""Lowering table of the sample_intro class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts (input schemas and documented examples), not from the mapping.
[] = no native call (a class check refused, a read, a wait, or a verb this member does not have); None = not checked."""
import glob
import json
import math
import os

from .sample_intro_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'assets')


def example(slug, cap, **changes):
    """The contract's documented example arguments of ``cap`` with ``changes`` (what the class must send when the agent gives only
    those settings and nothing was set before)."""
    f = glob.glob(os.path.join(ASSETS, '**', slug, 'asset.contract.json'), recursive=True)[0]
    with open(f, encoding='utf-8') as fh:
        ex = dict(json.load(fh)['capabilities'][cap]['example_arguments'])
    ex.update(changes)
    return ex


ATOMX_M1 = example('teledyne_atomxxyz', 'method.configure', purge_k=K0 + 40, purge_s=600.0)
ATOMX_M2 = dict(ATOMX_M1, desorb_k=K0 + 245)                    # the second method keeps the first one's settings
EGA_M = example('frontier_egapy', 'method.configure', mode='single', pyrolysis_k=K0 + 600, pyrolysis_s=6.0)
UNITY_M = example('markes_unityxr', 'method.configure', mode='analyze', tube_k=K0 + 280, recollect=False)
LASER_M = example('teledyne_analyteexcite', 'method.configure', mode='spot', spot_size_m=8e-05, shots=20, path_m=[[0.03, 0.03, 0.01]])
LID_OPEN, LID_SHUT = ('lid.set_angle', {'angle_rad': math.pi / 2}), ('lid.set_angle', {'angle_rad': 0.0})

CASES = {
    'agilent_hs8697': {'steps': [
        ('status', []),
        ('start --vials 1', []),                                                  # EMPTY_POSITION (class check)
        ('load v1 --vial 1', [LID_OPEN, ('sample.load', {'sample_id': 'v1', 'slot_id': 'tray_01'}), LID_SHUT]),
        ('load v2 --vial 1', []),                                                 # POSITION_TAKEN
        ('load v2 --vial 2 --dry-run', []),
        ('load v2 --vial 2', [LID_OPEN, ('sample.load', {'sample_id': 'v2', 'slot_id': 'tray_02'}), LID_SHUT]),
        ('unload --vial 2', [LID_OPEN, ('sample.unload', {'slot_id': 'tray_02'}), LID_SHUT]),
        ('unload --vial 2', []),                                                  # EMPTY_POSITION
        ('load v9 --vial 3', [LID_OPEN, ('sample.load', {'sample_id': 'v9', 'slot_id': 'tray_03'})]),   # not registered: refused, lid stays open
        ('close lid', [LID_SHUT]),                                                # shared verb
        ('set-temp 70C', []),                                                     # MISSING_ZONE (usage)
        ('set-temp 70C --zone oven', [('thermal.set_temperature', {'zone': 'oven', 'temperature_K': K0 + 70})]),
        ('set-temp 80C --zone loop', [('thermal.set_temperature', {'zone': 'loop_valve', 'temperature_K': K0 + 80})]),
        ('set-temp 90C --zone transfer', [('thermal.set_temperature', {'zone': 'transfer_line', 'temperature_K': K0 + 90})]),
        ('set-temp 400C --zone oven', []),                                        # OUT_OF_RANGE (0..300 °C)
        ('set-temp 22C --zone oven', []),                                         # TOO_LOW (room 20 °C + 5 K)
        ('start --vials 1', []),                                                  # NO_METHOD (no equilibration time)
        ('method equilibration=10min gc-cycle=5min shake=3', [('method.set_equilibration_time', {'duration_s': 600.0}),
                                                               ('hs.method.configure', {'settings': {'gc_cycle_s': 300.0, 'shake': 3}})]),
        ('method shake=12', []),                                                  # OUT_OF_RANGE
        ('method vial-size=20mL', [('hs.method.configure', {'settings': {'vial_size_m3': 2e-05}})]),
        ('start --vials 1', [('sequence.start', {'vials': [1]})]),
        ('load v3 --vial 3', []),                                                 # BUSY (sequence running)
        ('#wait 600', None),
        ('stop', [('sequence.stop', {'scope': 'sequence'})]),
        ('stop', []),                                                             # NOT_RUNNING
        ('start --vial 1 --injections 2', [('sequence.start', {'vials': [1], 'injections_per_vial': 2})]),
        ('wait --max 3h', []),                                                    # shared wait on the class job
        ('result', []),
        ('result --save-to hs_result.json', []),
        ('inject', []),                                                           # not supported
        ('position 1 2 3', []),                                                   # not supported
        ('rinse', []),                                                            # not supported
    ]},
    'teledyne_atomxxyz': {'steps': [
        ('start --vial 1', []),                                                   # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('start --vial 1', []),                                                   # NO_METHOD
        ('method purge-temp=40C purge-time=10min', [('method.configure', ATOMX_M1)]),
        ('method purge-temp=400C', []),                                           # OUT_OF_RANGE (10..90 °C)
        ('method colour=blue', []),                                               # UNKNOWN_SETTING
        ('method desorb-temp=245C', [('method.configure', ATOMX_M2)]),
        ('open guard', [('guard.set', {'angle_rad': 1.4})]),
        ('start --vial 1', []),                                                   # COVER_OPEN
        ('close guard', [('guard.set', {'angle_rad': 0})]),
        ('start --vials 1,2', []),                                                # NOT_ON_THIS_INSTRUMENT (usage)
        ('start --vial 99', []),                                                  # UNKNOWN_POSITION (usage)
        ('start --vial 1 --dry-run', []),
        ('start --vial 1', [('acquisition.start', {'vial_index': 0})]),
        ('method purge-temp=30C', []),                                            # BUSY
        ('#wait 60', None),
        ('stop', [('acquisition.stop', {})]),
        ('stop', []),                                                             # NOT_RUNNING
        ('start --vial 3', [('acquisition.start', {'vial_index': 2})]),     # NOT_READY: the sparger still holds the stopped sample
        ('rinse 50mL', []),                                                      # OUT_OF_RANGE (1..25 mL)
        ('rinse', [('system.rinse', {'volume_m3': 7e-06})]),                     # the documented rinse volume
        ('start --vial 3', [('acquisition.start', {'vial_index': 2})]),
        ('rinse', []),                                                           # BUSY
        ('wait --max 1h', []),
        ('result', [('process.measure', {})]),
        ('load v1 --vial 1', []),                                                 # not supported
        ('set-temp 50C', []),                                                     # not supported
    ]},
    'frontier_egapy': {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('start', []),                                                            # NO_METHOD
        ('method mode=single pyrolysis-temp=600C pyrolysis-time=0.1min', [('method.configure', EGA_M)]),
        ('method pyrolysis-temp=600.5C', [('method.configure', dict(EGA_M, pyrolysis_k=K0 + 600.5))]),   # runtime: whole °C only
        ('inject', []),                                                           # NOT_RUNNING
        ('start', [('acquisition.start', {})]),
        ('inject', []),                                                           # NOT_YET (furnace heating)
        ('inject --wait --dry-run', []),
        ('inject --wait', [('cup.drop', {}), ('acquisition.confirm', {})]),
        ('wait --max 1h', []),
        ('result', [('process.measure', {})]),
        ('stop', []),                                                             # NOT_RUNNING
        ('set-temp 600C', []),                                                    # not supported
        ('position 1 1 1', []),                                                   # not supported
    ]},
    'markes_unityxr': {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('method mode=analyze desorb-temp=280C recollect=false', [('method.configure', UNITY_M)]),
        ('method desorb-temp=400C', [('method.configure', dict(UNITY_M, tube_k=K0 + 400))]),     # runtime: sorbent limit 350 °C
        ('method desorb-temp=450C', []),                                          # OUT_OF_RANGE (35..425 °C)
        ('open lid', [('lid.set', {'lift_m': 0.18})]),
        ('start', []),                                                            # COVER_OPEN
        ('close lid', [('lid.set', {'lift_m': 0})]),
        ('start', [('acquisition.start', {})]),
        ('start', []),                                                            # BUSY
        ('wait --max 1h', []),
        ('result --save-to unity.json', [('process.measure', {})]),
        ('stop', []),                                                             # NOT_RUNNING
        ('inject', []),                                                           # not supported
    ]},
    'teledyne_analyteexcite': {'steps': [
        ('position 30mm 30mm 10mm', []),                                          # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('position 30mm 30mm 10mm', [('stage.move', {'position_m': [0.03, 0.03, 0.01]})]),
        ('position 200mm 0 0', []),                                               # OUT_OF_RANGE (0..100 mm)
        ('method mode=spot spot=80um shots=20 path_m=[[0.03,0.03,0.01]]', [('method.configure', LASER_M)]),
        ('open guard', [('guard.set', {'angle_rad': 1.1})]),
        ('open drawer', [('drawer.set', {'open_m': 0.23})]),
        ('start', []),                                                            # COVER_OPEN
        ('position 10mm 10mm 10mm', []),                                          # DRAWER_OPEN
        ('close drawer', [('drawer.set', {'open_m': 0})]),
        ('close guard', [('guard.set', {'angle_rad': 0})]),
        ('start', [('acquisition.start', {})]),
        ('wait', []),
        ('result', [('process.measure', {})]),
        ('rinse 5mL', []),                                                       # NOT_ON_THIS_INSTRUMENT (usage)
        ('rinse', [('washout.start', {})]),
        ('wait', []),
        ('load s1 --vial 1', []),                                                 # not supported
        ('inject', []),                                                           # not supported
    ]},
}
