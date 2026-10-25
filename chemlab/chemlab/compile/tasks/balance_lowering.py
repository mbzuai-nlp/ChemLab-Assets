"""Lowering table of the balance class: for each member, verbs in order and the native calls each must make (written from the
contracts, not from the mapping).  Run with `python -m chemlab.compile.lowering --class balance` (or tests_compile)."""
from .balance_tasks import place


def prep_load(kg):
    def prep(backend):
        for iid in backend.instruments:
            place(backend, iid, kg)
        backend.advance(30.0)
    return prep


CASES = {
    'mt_xpr204e': {'prep': prep_load(0.0253), 'steps': [
        ('weigh', [('balance.reading', {})]),
        ('weigh --stable', [('balance.reading', {})]),
        ('tare', [('balance.tare', {})]),
        ('zero', []),                                           # refused by the class check: 25 g is outside the zero range
        ('unit mg', [('display.set_unit', {'unit': 'mg'})]),
        ('unit g', [('display.set_unit', {'unit': 'g'})]),
        ('door open', [('door.operate', {'side': 'both', 'action': 'open'})]),
        ('tare', []),                                           # refused: not stable with the doors open
        ('door close --side left', [('door.operate', {'side': 'left', 'action': 'close'})]),
        ('door close', [('door.operate', {'side': 'both', 'action': 'close'})]),
        ('tare --wait 30s', [('balance.tare', {})]),
        ('power off', [('power.set', {'enabled': False})]),
        ('tare', []),                                           # refused: switched off
        ('power on', [('power.set', {'enabled': True})]),
        ('zero --dry-run', []),
        ('status', []),
    ]},
    'ohaus_px224e': {'prep': prep_load(0.0253), 'steps': [
        ('weigh', []),                                          # no query capability: a read
        ('tare', [('weighing.tare', {})]),
        ('zero', []),
        ('unit mg', [('weighing.set_unit', {'unit': 'mg'})]),
        ('unit g', [('weighing.set_unit', {'unit': 'g'})]),
        ('door open', [('doors.set', {'open_fraction': 1.0})]),
        ('#wait 2', None),                                      # an open door makes the reading unstable as time passes
        ('tare', []),
        ('door close', [('doors.set', {'open_fraction': 0.0})]),
        ('tare --wait 30s', [('weighing.tare', {})]),
        ('power off', [('power.set', {'on': False})]),
        ('power on', [('power.set', {'on': True})]),
        ('weigh --stable', []),
        ('status', []),
    ]},
    'ohaus_spx2201': {'prep': prep_load(0.1203), 'steps': [
        ('weigh --stable', []),
        ('tare', [('weighing.tare', {})]),
        ('unit kg', [('weighing.set_unit', {'unit': 'kg'})]),
        ('unit mg', []),                                        # usage error: the SPX does not offer mg
        ('unit g', [('weighing.set_unit', {'unit': 'g'})]),
        ('door close', []),                                     # not supported (no draft shield)
        ('power off', [('power.set', {'on': False})]),
        ('zero', []),
        ('power on', [('power.set', {'on': True})]),
        ('zero --wait 30s', [('weighing.zero', {})]),           # sent (the start-up zero took the 120 g, so gross reads ~0), and the
                                                                # SPX refuses ZERO_RANGE: its range counts from the calibration zero
        ('tare --wait 30s', [('weighing.tare', {})]),
        ('status', []),
    ]},
}
