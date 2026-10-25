"""Lowering table of the enclosure class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, a read, or a verb this member does not have); None = not checked.  The labs carry the class world
(tasks/enclosure_world.py: the glovebox scene)."""
from .enclosure_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

CASES = {
    'inert_purelab_he_2gb': {'steps': [
        ('status', []),
        ('open', []),                                                             # WHICH_PART (outer or inner)
        ('open outer', [('antechamber.outer_door.set', {'open': True})]),
        ('open inner', []),                                                       # OTHER_DOOR_OPEN
        ('cycle', []),                                                            # DOOR_OPEN
        ('close outer', [('antechamber.outer_door.set', {'open': False})]),
        ('open inner', []),                                                       # ANTECHAMBER_NOT_PURGED
        ('cycle --cycles 2 --wait --max 20min', [('antechamber.set_cycles', {'cycles': 2}), ('antechamber.cycle.start', {})]),
        ('open inner', [('antechamber.inner_door.set', {'open': True})]),
        ('cycle', []),                                                            # DOOR_OPEN (inner)
        ('close inner', [('antechamber.inner_door.set', {'open': False})]),
        ('cycle --cycles 12', []),                                                # BAD_CYCLES (1..9)
        ('cycle', [('antechamber.cycle.start', {})]),                             # two cycles are already set
        ('cycle stop', [('antechamber.cycle.stop', {})]),
        ('cycle stop', []),                                                       # NO_CYCLE
        ('fan 60%', [('blower.configure', {'speed_pct': 60.0})]),
        ('fan on', []),                                                           # BAD_FAN_SETTING (speed only)
        ('light on', [('light.set', {'on': True})]),
        ('light on', []),                                                         # already on
        ('read', []),
        ('sash safe', []),                                                        # not supported
        ('uv on', []),                                                            # not supported
    ]},
    'esco_ac24s8': {'steps': [
        ('status', []),
        ('fan on', []),                                                           # SASH_CLOSED
        ('uv on', [('uv.toggle', {})]),
        ('uv off', [('uv.toggle', {})]),
        ('sash safe', [('sash.move', {'direction': 'up'})]),
        ('uv on', []),                                                            # SASH_NOT_CLOSED
        ('fan on --wait --max 10min', [('fan.set', {'state': 'on'})]),
        ('light off', [('lamp.toggle', {})]),                                     # the warm-up switched the light on
        ('light on', [('lamp.toggle', {})]),
        ('mode quickstart', [('settings.configure', {'mode': 'quickstart'})]),
        ('mode maintenance', []),                                                 # UNKNOWN_MODE (needs the admin PIN in the menu)
        ('sash open', [('sash.move', {'direction': 'up'})]),
        ('read', []),
        ('sash safe', [('sash.move', {'direction': 'down'})]),
        ('mode normal', [('settings.configure', {'mode': 'normal'})]),
        ('fan off', [('fan.set', {'state': 'off'})]),
        ('sash close', [('sash.move', {'direction': 'down'})]),
        ('sash 300mm', []),                                                       # UNKNOWN_POSITION (fixed stops)
        ('cycle', []),                                                            # not supported
    ]},
    'esco_efa5uduvw': {'steps': [
        ('status', []),
        ('sash open', []),                                                        # NEEDS_HOLD (above the 457 mm stop)
        ('sash open --hold', [('sash.set', {'height_m': 0.67, 'hold': True})]),
        ('sash safe', [('sash.set', {'height_m': 0.457, 'hold': False})]),
        ('sash 0.8m', []),                                                        # OUT_OF_RANGE (0..670 mm)
        ('sash 300mm', [('sash.set', {'height_m': 0.3, 'hold': False})]),
        ('mode night', [('monitor.night_setback', {'closed': True})]),
        ('mode night', []),                                                       # already
        ('mode normal', [('monitor.night_setback', {'closed': False})]),
        ('fan off', [('fan.set', {'on': False})]),
        ('fan on', [('fan.set', {'on': True})]),
        ('fan standby', []),                                                      # BAD_FAN_SETTING
        ('light off', [('light.set', {'on': False})]),
        ('read', []),
        ('uv on', []),                                                            # not supported
    ]},
}
