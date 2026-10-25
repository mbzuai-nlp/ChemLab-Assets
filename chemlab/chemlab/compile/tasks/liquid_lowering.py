"""Lowering table of the liquid class (the Flex): verbs and the native calls each must make.  Joint targets are the deck
registry's (lab-config deck + contract slot geometry, chemlab/compile/deck.py); everything else is written from the contract."""
import copy

from ...labbackend import load_config
from ..deck import build_registry
from .liquid_tasks import FLEX_SLUG, flex_entry
from .syringe_pump_world import config as syringe_world

CONTENTS = {'reservoir:A1': ('buffer', 10000), 'reservoir:A2': ('water', 10000)}


def config():
    c = load_config()
    c['instruments'] = [e for e in c['instruments'] if e['id'] != FLEX_SLUG] + [flex_entry(CONTENTS, iid=FLEX_SLUG)]
    return syringe_world(c)                                         # the NE-1000 with a B-D 10 mL syringe and its line


def _registry():
    e = flex_entry(CONTENTS, iid=FLEX_SLUG)
    import json
    from ...hosts import contract_index
    contract = json.loads(contract_index()['instrument.' + FLEX_SLUG].read_text(encoding='utf-8'))
    return build_registry(e['deck'], contract)


REG = _registry()
LW = {lw['name']: lw for lw in REG['labware']}


def pos(name):
    if name == 'trash':
        return copy.deepcopy(REG['trash']['position_m'])
    lw, well = name.split(':')
    return copy.deepcopy(LW[lw]['wells'][well]['position_m'])


def mv(name):
    return ('motion.move', pos(name))


CASES = {
    FLEX_SLUG: {'steps': [
        ('status', []),
        ('lights on', [('robot.set_lights', {'on': True})]),
        ('home', [('robot.home', {})]),
        ('pick-up-tip --rack tiprack200', [mv('tiprack200:A1'), ('tip.attach', {'tip_id': 'tiprack200_A1'})]),
        ('aspirate --volume 50uL --from reservoir:A1', [mv('reservoir:A1'), ('sample.aspirate', {'container_id': 'reservoir_A1', 'volume_m3': 5e-8})]),
        ('dispense --volume 0.05mL --to plate:A1', [mv('plate:A1'), ('sample.dispense', {'container_id': 'plate_A1', 'volume_m3': 5e-8})]),
        ('aspirate --volume 250uL --from reservoir:A1', []),                    # TIP_CAPACITY: a 200 µL tip
        ('aspirate --volume 2mL --from reservoir:A1', []),                      # VOLUME_OUT_OF_RANGE (usage)
        ('drop-tip', [mv('trash'), ('tip.release', {'target_slot_id': 'A1'})]),
        ('dispense --volume 10 --to plate:A1', []),                             # NO_TIP
        ('transfer --volume 300 --from reservoir:A1 --to plate:B1 --rack tiprack200',
         [mv('tiprack200:B1'), ('tip.attach', {'tip_id': 'tiprack200_B1'}),
          mv('reservoir:A1'), ('sample.aspirate', {'container_id': 'reservoir_A1', 'volume_m3': 1.5e-7}),
          mv('plate:B1'), ('sample.dispense', {'container_id': 'plate_B1', 'volume_m3': 1.5e-7}),
          mv('reservoir:A1'), ('sample.aspirate', {'container_id': 'reservoir_A1', 'volume_m3': 1.5e-7}),
          mv('plate:B1'), ('sample.dispense', {'container_id': 'plate_B1', 'volume_m3': 1.5e-7}),
          mv('trash'), ('tip.release', {'target_slot_id': 'A1'})]),
        ('transfer --volume 7.5uL --from reservoir:A2 --to plate:C1-C2 --dry-run', []),
        ('move-to plate:H12', [mv('plate:H12')]),
        ('move-to plate:Z9', []),                                               # UNKNOWN_WELL (usage)
        ('mix', []),                                                            # not supported
        ('deck-config A1 trash_bin', [('deck.configure', {'slot': 'A1', 'fixture': 'trash_bin'})]),
        ('restart', []),                                                        # destructive: only a preview without --yes
        ('protocols', []),
        ('lights off', [('robot.set_lights', {'on': False})]),
        ('power off', [('power.set', {'enabled': False})]),
        ('home', []),                                                           # POWER_OFF
    ]},
    # NE-1000 syringe pump (its own adapter, classes/syringe_pump.py): calls written from the contract - diameter in m, rates in
    # m3/s with the pump's display units (MH mL/h, MM mL/min, UH uL/h, UM uL/min), volumes in m3, directions INF / WDR / REV,
    # phase functions RAT / STP / ...; the B-D 10 mL inside diameter 14.43 mm is the manual's syringe table (12.7 p.50).
    'newera_ne1000': {'steps': [
        ('status', []),
        ('syringe', []),
        ('rate 2mL/h', []),                                                     # DIAMETER_NOT_SET
        ('syringe bd_99ml', []),                                                # UNKNOWN_SYRINGE (usage)
        ('syringe loaded', [('syringe.set_diameter', {'diameter_m': 0.01443})]),
        ('syringe 14.43mm', []),                                                # already set
        ('rate 100mL/min', []),                                                 # OUT_OF_RANGE for 14.43 mm (usage)
        ('rate 1mL/min', [('rate.set', {'rate_m3_s': 1.6666666666666667e-08, 'units': 'MM'})]),
        ('volume 0.5mL', [('volume.set', {'volume_m3': 5e-07})]),
        ('direction infuse', []),                                               # already infusing
        ('run --dry-run', []),
        ('run', [('pump.run', {})]),
        ('volume 1mL', []),                                                     # BUSY
        ('#wait 40', None),
        ('direction withdraw', [('direction.set', {'direction': 'WDR'})]),
        ('volume 0.2mL', [('volume.set', {'volume_m3': 2e-07})]),
        ('run --wait', [('pump.run', {})]),
        ('volume clear', [('volume.clear', {'direction': 'BOTH'})]),
        ('phase', []),
        ('phase 1 rate rate=2mL/min volume=0.1mL direction=infuse', [('phase.select', {'phase': 1}),
                                                                     ('phase.set_function', {'function': 'RAT', 'rate_m3_s': 3.33333333333333e-08,
                                                                                             'rate_units': 'MM', 'volume_m3': 1e-07,
                                                                                             'direction': 'INF'})]),
        ('phase 2 pause param=5', [('phase.select', {'phase': 2}), ('phase.set_function', {'function': 'PAS', 'parameter': 5})]),
        ('phase 3 rate rate=2mL/min volume=0.1mL direction=withdraw', [('phase.select', {'phase': 3}),
                                                                       ('phase.set_function', {'function': 'RAT', 'rate_m3_s': 3.33333333333333e-08,
                                                                                               'rate_units': 'MM', 'volume_m3': 1e-07,
                                                                                               'direction': 'WDR'})]),
        ('phase 4 stop', [('phase.select', {'phase': 4}), ('phase.set_function', {'function': 'STP'})]),
        ('phase 5 wobble', []),                                                 # BAD_FUNCTION (usage)
        ('run --phase 1 --wait', [('pump.run', {'phase': 1})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('purge on', [('pump.purge', {'on': True})]),
        ('#wait 2', None),
        ('stop', [('pump.stop', {})]),
        ('phase clear', [('program.clear', {})]),
    ]},
}
