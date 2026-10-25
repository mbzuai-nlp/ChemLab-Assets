"""Lowering table of the wash_sterilize class: per member, verbs in order and the native calls (capability, arguments in the unit
the argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, or a verb this member does not have); None = not checked.  The labs carry the class world (tasks/wash_sterilize_world.py)."""
from .wash_sterilize_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
MEDIA = 'load media_1 --kind liquid --mass 700g --liquid water --volume 1L --material borosilicate_glass --closure loose'

CASES = {
    'miele_plw6111': {'steps': [
        ('status', []),
        ('program quick', []),                                                    # UNKNOWN_PROGRAM (did you mean quick_wash)
        ('program quick_wash', [('programme.select', {'programme_id': 'quick_wash'})]),
        ('program quick_wash', []),                                               # already selected
        ('configure temp=60C', []),                                               # UNKNOWN_SETTING (fixed in the programme blocks)
        ('configure drying=on', [('programme.set_drying', {'enabled': True})]),
        ('open door', [('door.open', {})]),
        ('start', []),                                                            # DOOR_OPEN
        ('close door', [('door.close', {})]),
        ('pause on', []),                                                         # NOT_RUNNING
        ('start', [('programme.start', {})]),
        ('open door', []),                                                        # DOOR_LOCKED
        ('program universal', []),                                                # BUSY
        ('pause on', [('programme.interrupt', {})]),
        ('open door', []),                                                        # DOOR_LOCKED (also when interrupted)
        ('pause off', [('programme.resume', {})]),
        ('#wait 600', None),
        ('stop', [('programme.cancel', {})]),
        ('stop', []),                                                             # NO_CYCLE
        ('start --wait --max 3h', [('programme.start', {})]),
        ('open door', [('door.open', {})]),
        ('load rack --kind solid --mass 1kg --material pp', []),                  # not supported (glassware by hand)
    ]},
    'tuttnauer_3870elvd': {'steps': [
        ('status', []),
        ('configure temp=121C', []),                                              # NOT_EDITABLE (liquids_121 selected)
        ('program custom_1', [('program.select', {'program': 'custom_1'})]),
        ('configure temp=121C time=20min exhaust=slow_liquids end_temp=80C',
         [('program.configure', {'program': 'custom_1', 'temperature_k': K0 + 121, 'time_s': 1200.0, 'exhaust': 'slow_liquids', 'end_temperature_k': K0 + 80})]),
        ('configure temp=150C', []),                                              # OUT_OF_RANGE (105..138 C)
        (MEDIA, []),                                                              # LID_CLOSED
        ('open lid', [('lid.set', {'open': True})]),
        (MEDIA, [('load.add', {'load_id': 'media_1', 'kind': 'liquid', 'mass_kg': 0.7, 'liquid': 'water', 'container_volume_m3': 0.001,
                               'material': 'borosilicate_glass', 'closure': 'loose'})]),
        ('load bottle_2 --kind liquid --mass 500g --material pp', []),           # MISSING_SETTING (usage: liquid and volume)
        ('unload flask_9', []),                                                   # UNKNOWN_LOAD
        ('start', []),                                                            # LID_OPEN
        ('close lid', [('lid.set', {'open': False})]),
        ('stop', []),                                                             # NO_CYCLE
        ('start', [('cycle.start', {})]),
        ('open lid', []),                                                         # LID_LOCKED
        ('program solids_134', []),                                               # BUSY
        ('pause on', []),                                                         # not supported
        ('#wait 300', None),
        ('stop', [('cycle.stop', {})]),
        ('#wait 600', None),
        ('open lid', []),                                                         # LID_LOCKED (still under pressure / hot after the abort)
        ('#wait 10800', None),
        ('open lid', [('lid.set', {'open': True})]),
        ('unload media_1', [('load.remove', {'load_id': 'media_1'})]),
    ]},
}
