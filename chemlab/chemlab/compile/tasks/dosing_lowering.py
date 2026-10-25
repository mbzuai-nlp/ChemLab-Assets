"""Lowering table of the dosing class (Quantos Q2 + XPE206DR), in the dosing world (tasks/dosing_world.py: head head_1 and an
empty vial vial_1 on the balance pan, front door travel = the contract's authored travel, powder rate 0.5 mg/s).

Expected native calls come from the contract: dosing.set_target takes kg, dosing.set_tolerance_percent takes % of the target,
dosing.set_tolerance kg, container.select_position the host's position id (balance_pan), door.set_opening the front door
opening in m (0 = closed, the contract's authored travel 0.5495 m = lever fully lifted = open), door.set_side open true/false, head.set_lock locked,
head.set_height raised/lowered; balance.tare, dosing.start, dosing.abort and dosing.read take no arguments.  Each step's calls
are only the ones the state needs (a selected head is not selected again, a closed door is not closed again, an unchanged
target or tolerance is not set again; the vial is tared before every dose).
"""
from .dosing_world import config  # noqa: F401  (the lowering harness builds both labs from this config)
from . import dosing_world as W

Q = 'mt_quantos_q2_xpe206dr'
DOSE = [('balance.tare', {}), ('dosing.start', {})]

CASES = {
    Q: {'prep': None, 'steps': [
        ('status', []),
        ('head', []),                                                                    # show: reads only
        ('vial', []),
        ('dose 10mg --dry-run', []),
        ('dose 9g', []),                                                                 # usage: outside 1 µg .. 5 g
        ('dose 10mg --tolerance 300%', []),                                              # usage: outside 0.001 .. 100 %
        ('head install', []),                                                            # the operator's work: not supported
        ('tare', [('container.select_position', {'position_id': 'balance_pan'}), ('balance.tare', {})]),
        ('open side', [('door.set_side', {'open': True})]),
        ('tare', []),                                                                    # refused: a door is open
        ('dose 10mg --tolerance 1%', [('head.select', {'head_id': 'head_1'}), ('door.set_side', {'open': False}),
                                      ('dosing.set_target', {'target_mass_kg': 1e-05}),
                                      ('dosing.set_tolerance_percent', {'percent': 1.0})] + DOSE),
        ('open front', []),                                                              # refused: dosing
        ('#wait 30', None),
        ('result', [('dosing.read', {})]),
        ('dose 5mg --tolerance 0.1mg --sample-id S-1', [('dosing.set_target', {'target_mass_kg': 5e-06}),
                                                        ('dosing.set_tolerance', {'tolerance_kg': 1e-07}),
                                                        ('dosing.set_ids', {'user_id': None, 'sample_id': 'S-1'})] + DOSE),
        ('stop', [('dosing.abort', {})]),
        ('stop', []),                                                                    # refused: not dosing
        ('result', [('dosing.read', {})]),
        ('head lower', [('head.set_height', {'position': 'lowered'})]),
        ('head raise', [('head.set_height', {'position': 'raised'})]),
        ('vial place', [('door.set_opening', {'opening_m': W.TRAVEL_M})]),
        ('dose 20mg', [('door.set_opening', {'opening_m': 0.0}), ('dosing.set_target', {'target_mass_kg': 2e-05})] + DOSE),
        ('#wait 50', None),
        ('result', [('dosing.read', {})]),
        ('head unlock', None),                                                           # state dependent (locked by the dose)
        ('head remove', []),                                                             # already unlocked: nothing to send
        ('vial remove', [('door.set_opening', {'opening_m': W.TRAVEL_M})]),
        ('close', [('door.set_opening', {'opening_m': 0.0})]),
        ('open side', [('door.set_side', {'open': True})]),
        ('close side', [('door.set_side', {'open': False})]),
    ]},
}
