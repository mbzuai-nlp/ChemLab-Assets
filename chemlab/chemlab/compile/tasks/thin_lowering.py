"""Lowering table of the thin class (hand-written from the contracts)."""
from .thin_world import config  # noqa: F401

CASES = {
    'wtw_oxitopi': {'steps': [
        ('status', []),
        ('power on', [('platform.set_power', {'on': True})]),
        ('open incubator_door', [('incubator.set_door', {'open': True})]),
        ('close incubator_door', [('incubator.set_door', {'open': False})]),
        ('start', []),                                                          # MISSING_HEAD (usage)
        ('start --head h9', []),                                                # BAD_CHOICE (usage)
        ('method --head h2 days=3', [('head.configure', {'head': 'h2', 'duration_days': 3})]),
        ('start --head h1', [('head.start', {'head': 'h1'})]),
        ('result --head h1', [('head.read', {'head': 'h1'})]),
        ('stop --head h1', [('head.stop', {'head': 'h1'})]),
        ('ack', []),                                                            # not supported
        ('power off', [('platform.set_power', {'on': False})]),
    ]},
    'inert_puresolvmd': {'steps': [
        ('status', []),
        ('open cabinet_left_door', [('cabinet.door', {'side': 'left', 'angle_rad': 1.7})]),
        ('close cabinet_left_door', [('cabinet.door', {'side': 'left', 'angle_rad': 0.0})]),
        ('open cabinet_right_door', [('cabinet.door', {'side': 'right', 'angle_rad': 1.7})]),
        ('open gas_isolation_valve', [('gas.isolation', {'open': True})]),
        ('close gas_isolation_valve', [('gas.isolation', {'open': False})]),
        ('open hatch', []),                                                     # UNKNOWN_PART (usage)
        ('start', []),                                                          # not supported
        ('power on', []),                                                       # not supported
    ]},
    'vaisala_dmt152': {'steps': [
        ('status', []),
        ('result', [('measurement.send', {})]),                                 # refused: switched off
        ('power on', [('power.set', {'on': True, 'voltage_v': 24.0})]),
        ('#wait 60', None),
        ('result', [('measurement.send', {})]),
        ('start', []),                                                          # not supported (continuous transmitter)
        ('power off', [('power.set', {'on': False, 'voltage_v': 24.0})]),
    ]},
    'cem_libertyblue': {'steps': [
        ('status', []),
        ('start', [('acquisition.start', {})]),                                 # refused: off, cover and clamp open
        ('power on', [('power.set', {'enabled': True})]),
        ('close cover', [('cover.set_lift', {'lift_m': 0.0})]),
        ('close clamp', [('clamp.set_lift', {'lift_m': 0.0})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 30', None),
        ('result', [('synthesis.measure', {})]),
        ('stop', [('acquisition.stop', {})]),
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'cytiva_ktaoligosynt': {'steps': [
        ('status', []),
        ('power on', [('power.set', {'enabled': True})]),
        ('close clamp', [('clamp.set_lift', {'lift_m': 0.0})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 30', None),
        ('result', [('synthesis.measure', {})]),
        ('stop', [('acquisition.stop', {})]),
        ('open clamp', [('clamp.set_lift', {'lift_m': 0.04})]),
    ]},
}
