"""Lowering table of the solid_prep class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts (a method call carries the contract's reference arguments, ``T``,
with the values the verb changes), not from the mapping.  [] = no native call; None = not checked (``pump --to ... --hold``
strokes until the cycle is complete, so the number of strokes is the press's; the twin state is still compared)."""
import copy
import json

from ...hosts import contract_index
from .solid_prep_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

PI = 3.141592653589793
K0 = 273.15
RPM = 2 * PI / 60


def T(slug, cap, **over):
    c = json.loads(contract_index()['instrument.' + slug].read_text(encoding='utf-8'))
    a = copy.deepcopy(c['capabilities'][cap]['example_arguments'])
    a.update(over)
    return a


PM, AS, PT, SP, LN = 'retsch_pm100', 'retsch_as200', 'retsch_pt100', 'specac_atlasmanual', 'malvern_leneo'

CASES = {
    PM: {'steps': [
        ('status', []),
        ('intensity 300rpm', [('milling.set_speed', {'speed_rad_s': 300 * RPM})]),
        ('intensity 1000rpm', []),                                              # OUT_OF_RANGE (100..650 rpm)
        ('method speed=400rpm time=1min', [('method.configure', T(PM, 'method.configure', speed_rad_s=400 * RPM, duration_s=60.0))]),
        ('method spin=1', []),                                                  # UNKNOWN_SETTING
        ('program 1', [('program.select', {'slot': 1})]),
        ('program 0', [('program.select', {'slot': 0})]),
        ('program 11', []),                                                     # OUT_OF_RANGE (0..10)
        ('start --dry-run', []),
        ('start', [('acquisition.start', {})]),
        ('#wait 5', None),
        ('method speed=300rpm', []),                                            # BUSY (member requirement)
        ('stop', [('acquisition.stop', {})]),
        ('#wait 10', None),
        ('rearm', [('cycle.rearm', {})]),
        ('rearm', []),                                                          # NOTHING_TO_REARM
        ('set-temp 50C', []), ('pause on', []), ('feeder on', []), ('pump 1', []),   # not supported
        ('start --delay 30s', [('acquisition.start_in', {'delay_s': 30.0})]),
        ('stop', [('acquisition.stop', {})]),                                   # cancels the countdown
        ('result', [('milling.measure', {})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 150', None),
        ('start', []),                                                          # LID_OPEN: the lid opened by itself at the end
        ('result --save-to pm100.json', [('milling.measure', {})]),
    ]},
    AS: {'steps': [
        ('intensity 1.5mm', [('sieving.set_lift', {'total_lift_m': 0.0015})]),
        ('intensity 5mm', []),                                                  # OUT_OF_RANGE (0.2..3 mm)
        ('method amplitude=1mm time=2min', [('method.configure', T(AS, 'method.configure', total_lift_m=0.001, duration_s=120.0))]),
        ('program 0', [('program.select', {'slot': 0})]),
        ('pause on', []),                                                       # NOT_RUNNING
        ('start', [('acquisition.start', {})]),
        ('#wait 10', None),
        ('pause on', [('acquisition.pause', {})]),
        ('pause off', [('acquisition.resume', {})]),
        ('pause off', []),                                                      # NOT_PAUSED
        ('#wait 200', None),
        ('result', [('sieving.measure', {})]),
        ('start', []),                                                          # CYCLE_FINISHED
        ('method time=3min', []),                                               # CYCLE_FINISHED
        ('rearm', [('cycle.rearm', {})]),
        ('method time=3min', [('method.configure', T(AS, 'method.configure', duration_s=180.0))]),
        ('stop', []),                                                           # NOT_RUNNING
        ('feeder on', []), ('set-temp 30C', []), ('valve close', []),           # not supported
    ]},
    PT: {'steps': [
        ('start', []),                                                          # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),                       # shared layer
        ('start', []),                                                          # NO_METHOD
        ('intensity 60', []),                                                   # NO_METHOD
        ('method time=1min intensity=60 gap=5mm', [('hopper.set_gap', {'gap_m': 0.005}),
                                                   ('method.configure', {'duration_s': 60.0, 'intensity': 60})]),
        ('method time=2min', []),                                               # BAD_CHOICE (the timer steps)
        ('intensity 70', [('feeder.set_intensity', {'intensity': 70})]),
        ('intensity 120', []),                                                  # OUT_OF_RANGE (0..99)
        ('intensity 7.5', []),                                                  # NOT_AN_INTEGER
        ('#wait 3', None),
        ('start', []),                                                          # FEEDER_OFF
        ('feeder on', [('feeder.set_power', {'enabled': True})]),
        ('feeder on', []),                                                      # already on
        ('start', [('acquisition.start', {})]),
        ('#wait 10', None),
        ('pause on', [('acquisition.pause', {})]),
        ('pause off', [('acquisition.resume', {})]),
        ('#wait 120', None),
        ('result', [('dividing.measure', {})]),
        ('start', []),                                                          # CYCLE_DONE
        ('program 1', []), ('rearm', []), ('leadscrew 10mm', []),               # not supported
    ]},
    SP: {'steps': [
        ('status', []),
        ('pump', []),                                                           # NO_METHOD
        ('method load=68kN hold=12s', [('method.configure', T(SP, 'method.configure', target_load_N=68000.0, hold_s=12.0))]),
        ('method load=200kN', []),                                              # OUT_OF_RANGE (max 147 kN)
        ('method limit=70kN', [('relief.set_limit', {'load_N': 70000.0})]),
        ('start', []),                                                          # GUARD_OPEN
        ('close guard', [('guard.set_raised', {'side': 'front', 'raised': False})]),
        ('start', []),                                                          # VALVE_OPEN
        ('valve close', [('valve.set_open', {'open': False})]),
        ('leadscrew 73mm', [('screw.set_position', {'position_m': 0.073})]),
        ('leadscrew 200mm', []),                                                # OUT_OF_RANGE (0..89 mm)
        ('start', [('acquisition.start', {})]),
        ('pump 2', [('pump.stroke', {}), ('pump.stroke', {})]),
        ('pump --to 200kN', []),                                                # ABOVE_LIMIT (70 kN)
        ('pump --hold', []),                                                    # NEED_TO (usage)
        ('pump --to 68kN --hold', None),
        ('result', [('press.measure', {})]),
        ('pump', []),                                                           # CYCLE_DONE
        ('valve open', [('valve.set_open', {'open': True})]),
        ('open guard', [('guard.set_raised', {'side': 'front', 'raised': True})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('intensity 5', []), ('program 1', []), ('set-temp 50C', []), ('pause on', []),   # not supported
    ]},
    LN: {'steps': [
        ('start', []),                                                          # NO_METHOD
        ('method segments=10min@1065C,9min@1065C rocking=10deg', [
            ('method.configure', T(LN, 'method.configure', segments=[{'duration_s': 600.0, 'end_temperature_K': K0 + 1065},
                                                                     {'duration_s': 540.0, 'end_temperature_K': K0 + 1065}], rocking_rad=10 * PI / 180))]),
        ('method segments=abc', []),                                            # BAD_VALUE
        ('method pour=7', []),                                                  # OUT_OF_RANGE (1..4)
        ('set-temp 900C', [('thermal.set_temperature', {'temperature_K': K0 + 900})]),
        ('set-temp 2000C', []),                                                 # OUT_OF_RANGE (max 1200 °C)
        ('program std', [('program.load', {'name': 'std'})]),                   # refused by the runtime: PROGRAM_NOT_FOUND
        ('start', []),                                                          # DOOR_OPEN
        ('close door', [('door.set_open', {'open': False})]),                   # shared layer
        ('#wait 5', None),
        ('start', [('acquisition.start', {})]),
        ('#wait 600', None),
        ('result', [('fusion.measure', {})]),
        ('method rocking=5deg', []),                                            # BUSY
        ('stop', [('acquisition.stop', {})]),
        ('start', []),                                                          # CYCLE_DONE
        ('pause on', []), ('intensity 5', []), ('rearm', []),                   # not supported
    ]},
}
