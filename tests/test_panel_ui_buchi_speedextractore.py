"""front-panel tests for CHEM-028 buchi_speedextractore (panel_ui.json + scripts/asset_runtime/_panel_ui.py):
python3 -m pytest tests/test_panel_ui_buchi_speedextractore.py -q

Every gesture goes through the session API (panel.press / panel.turn / panel.switch, the runtime's normal invoke path) and every check looks at
the runtime state and at the screen (the structured frame of panel.frame; the crossed-out method name on the rendered pixels).  Themes: the key
procedures of the menu survey (preheat, occupy positions, create and save a method, standard leak test, flush into waste, start / pause / abort an
extraction); the method editor (solvent ratios, cycles with vial change, open / delete, crossed-out name), STOP pressed three times, SKIP, the
vial-change stop, instrument settings (language, contrast, preheat demand), solvent list, service functions (valves and manual pressure relief,
sensor zero, pump and Error 51, flow test, lifts, fans, operating hours), process faults shown over any screen; panel = direct capability (same
runtime state); refusals (shield open, no position, no method, cells loaded); no menu time-outs; render determinism and the PNGs of
outputs/panel_ui/buchi_speedextractore/; contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import buchi_speedextractore as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'speedextractor_01'
T0 = 273.15
LCD = 'lcd'
OUT = ROOT / 'outputs/panel_ui/buchi_speedextractore'
GRID = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-+*:%&/()=?!') + ['BACKSPACE']
SURVEY_TASKS = ['Preheat to the method temperature', 'Activate all six positions', 'Create and save a method', 'Run a standard leak test',
                'Flush the lines into waste', 'Start, pause and abort an extraction']
DONE_TASKS = set()


def _effects():
    meta = rt.export_buchi_speedextractore_contract_metadata()
    return {k: {e['state_id'] for e in v['state_effects']} for k, v in meta['capabilities'].items()}


EFFECTS = _effects()


class Rig:
    def __init__(self, **plant):
        self.h = rt.BuchiSpeedextractoreRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.EXAMPLE, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        before = self.h.read('evaluator')
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        if r['status'] in ('succeeded', 'accepted'):
            rt.validate(rt.CAPS[cap][1], r['result'])
            assert self.h.time_s == t0, cap                                              # gestures and commands take no simulated time
            if cap.startswith('panel.'):                                                 # a gesture changes only what its commands change
                after = self.h.read('evaluator')
                changed = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)}
                assert not changed - EFFECTS[cap], (cap, sorted(changed - EFFECTS[cap]))
            return r['result']
        return r

    # ---- gestures
    def press(self, key):
        return self.call(self.A, 'panel.press', input_id=key)['accepted']

    def turn(self, n):
        return self.call(self.A, 'panel.turn', input_id='knob', detents=int(n))['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self):
        return self.frame()['displays'][LCD]['lines']

    def text(self):
        return '\n'.join(self.lines())

    def item(self):
        m = self.frame()['ui']['menu']
        return m and m['item']

    def editing(self):
        return self.frame()['ui']['editor'] is not None

    def keys(self):
        return self.frame()['displays'][LCD].get('softkeys') or self.lines()[-1]

    def step(self, seconds, dt=1.0):
        t = 0.0
        while t < seconds - 1e-9:
            self.h.step(min(dt, seconds - t))
            t += dt

    def until(self, cond, limit=3000.0, dt=1.0):
        t = 0.0
        while not cond():
            assert t < limit, 'condition not reached'
            self.h.step(dt)
            t += dt
        return t

    # ---- menu navigation as the operator does it
    def to_item(self, item_id):
        """Move the cursor with the buttons this screen offers: ^ / v, or only the forward button (Next / v; those lists wrap round), or the
        knob on a list without arrow buttons."""
        for _ in range(130):
            f = self.frame()
            m = f['ui']['menu']
            if m['item'] == item_id:
                return
            items = rt.PANEL_SPEC['menus'][m['menu']]['items']
            want = next(i for i, it in enumerate(items) if it['id'] == item_id)
            keys = [k.strip() for k in f['displays'][LCD]['softkeys']]
            if keys[1] in ('^', '') and keys[2] == 'v':
                self.press('f3' if want > m['index'] else 'f2')
            elif keys[1] in ('Next', 'v'):
                self.press('f2')
            elif keys[2] == 'v':
                self.press('f3')
            else:
                self.turn(1 if want > m['index'] else -1)
        raise AssertionError(item_id)

    def open_item(self, item_id):
        self.to_item(item_id)
        assert self.press('f4')

    def set_number(self, item_id, detents):
        """Cursor to the value, turn the knob (the value opens for editing), OK."""
        self.to_item(item_id)
        assert self.turn(detents)
        assert self.editing()
        assert self.press('f4')

    def type_name(self, word):
        assert self.screen() == 'name_entry'
        self.press('f2')                                                             # DELETE: erase the whole name
        for ch in word:
            pos = self.frame()['ui']['vars']['grid_pos']
            self.turn(GRID.index(ch) - pos)
            self.press('f3')                                                         # SELECT

    def on(self):
        assert self.switch('on')
        assert self.screen() in ('main', 'preheat')

    def main(self):
        for _ in range(8):
            if self.screen() == 'main':
                return
            self.press('f4' if self.screen() in ('message', 'notice') else 'f1')
        assert self.screen() == 'main'

    def extraction(self, item):
        self.main()
        self.press('f2')
        self.open_item(item)

    def menu(self, item):
        self.main()
        self.press('f3')
        self.open_item(item)

    # ---- host actions (the operator's hands: shield, heating block, cells, vials)
    def open_block(self):
        self.call(self.A, 'shield.set_lift', lift_m=0.18)
        self.call(self.A, 'drawer.set_extension', extension_m=0.16)

    def close_block(self):
        self.call(self.A, 'drawer.set_extension', extension_m=0.0)
        self.call(self.A, 'shield.set_lift', lift_m=0.0)

    def load_samples(self, n=6, **kw):
        self.open_block()
        for i in range(n):
            self.call(self.E, 'sample.configure', **dict(rt.SAMPLE_EXAMPLE, position=i, sample_id=f'soil_{i}', **kw))
        self.close_block()

    def load_empty(self, positions=range(1, 7), volume=20e-6):
        self.open_block()
        for p in positions:
            self.call(self.E, 'cell.insert_empty', position=p, cell_volume_m3=volume, gripper_confirmed=True)
        self.close_block()

    def edit_ratios(self, ratios):
        """EXTRACTION > EDIT METHOD > SOLVENT: the ratios of ports 1-4 (percent)."""
        self.extraction('edit')
        self.open_item('solvent')
        cur = [self.frame()['ui']['vars'][f'sol_r{k}'] for k in range(1, 5)]
        order = sorted(range(4), key=lambda k: ratios[k] - cur[k])                    # lower first so the sum passes 100 % only at the end
        for k in order:
            if ratios[k] != cur[k]:
                self.set_number(f'ratio{k + 1}', ratios[k] - cur[k])
        assert self.press('f1')                                                      # < back to EDIT METHOD
        assert self.screen() == 'edit'

    def preheat_and_occupy(self):
        self.extraction('preheat')
        self.to_item('preheat')
        assert self.press('f4')                                                      # COPY the method temperature
        assert self.press('f2')                                                      # ON
        assert self.screen() == 'main'
        self.press('f2')
        self.open_item('positions')
        assert self.press('f4')                                                      # Occupy all positions? YES
        self.until(lambda: abs(self.h.temperature - self.h.method['temperature_k']) <= 1)


def ready_rig(cycles=None, **plant):
    """Method written from the panel (one change), preheated, positions occupied, six samples loaded."""
    g = Rig(**plant)
    g.on()
    g.extraction('edit')
    g.set_number('flush_gas', -2)                                                    # 3 -> 1 min (shortest gas flush, om p.56)
    if cycles:
        g.set_number('cycles', cycles - 1)
    g.preheat_and_occupy()
    g.load_samples()
    g.main()
    return g


class KeyProcedures(unittest.TestCase):
    def test_preheat_to_the_method_temperature(self):
        g = Rig()
        g.on()
        g.extraction('edit')
        g.set_number('temperature', -20)                                             # 100 -> 80 degC
        self.assertEqual(g.h.method['temperature_k'], T0 + 80.0)
        g.extraction('preheat')
        self.assertEqual(g.item(), 'preheat')                                        # the cursor starts on the preheat value
        self.assertIn('Current method:', g.text())
        self.assertTrue(g.press('f4'))                                               # COPY
        self.assertEqual(g.h.preheat_k, T0 + 80.0)
        self.assertFalse(g.h.preheat)
        self.assertEqual(g.lines()[-1].split()[1], 'On')
        self.assertTrue(g.press('f2'))                                               # ON: back to the main display
        self.assertEqual(g.screen(), 'main')
        self.assertTrue(g.h.preheat)
        self.assertIn('Preheat', g.lines()[2])
        self.assertIn('HEAT', g.lines()[4])
        g.until(lambda: abs(g.h.temperature - (T0 + 80.0)) <= 0.4)
        self.assertIn('Ready', g.lines()[2])
        self.assertIn('80 °C', g.lines()[7])
        g.extraction('preheat')
        self.assertIn('On', g.text().splitlines()[6])
        self.assertTrue(g.press('f2'))                                               # OFF
        self.assertFalse(g.h.preheat)
        DONE_TASKS.add('Preheat to the method temperature')

    def test_activate_all_six_positions(self):
        g = Rig()
        g.on()
        self.assertEqual(g.h._active(), [])                                          # all positions off by default (none loaded, never set)
        g.extraction('positions')
        self.assertEqual(g.screen(), 'occ_q')
        self.assertIn('Occupy all positions?', g.text())
        self.assertTrue(g.press('f4'))                                               # YES
        self.assertEqual(g.h.positions, [True] * 6)
        self.assertEqual(g.screen(), 'main')
        self.assertIn('(1) (2) (3) (4) (5) (6)', g.lines()[4])
        self.assertNotIn('strike_spans', g.frame()['displays'][LCD])
        # the four middle positions only: NO, ALL ON, then switch 1 and 6 off (om p.41)
        g.extraction('positions')
        self.assertTrue(g.press('f1'))                                               # NO: all off, the list opens
        self.assertEqual((g.screen(), g.h.positions), ('positions', [False] * 6))
        self.assertTrue(g.press('f2'))                                               # ALL ON
        self.assertEqual(g.h.positions, [True] * 6)
        self.assertEqual(g.lines()[-1].split()[-1], 'Off')
        self.assertTrue(g.press('f4'))                                               # position 1 off
        g.to_item('pos6')
        self.assertTrue(g.press('f4'))
        self.assertEqual(g.h.positions, [False, True, True, True, True, False])
        self.assertIn('[ ]', g.lines()[6])
        g.press('f1')
        spans = g.frame()['displays'][LCD]['strike_spans'][4]                        # cylinders 1 and 6 crossed out
        self.assertEqual(spans, [[0, 3], [20, 23]])
        DONE_TASKS.add('Activate all six positions')

    def test_create_and_save_a_method(self):
        g = Rig()
        g.on()
        g.extraction('edit')
        g.set_number('temperature', -20)                                             # 80 degC
        self.assertEqual(g.item(), 'pressure')                                       # OK moved the cursor to the next entry
        g.to_item('vial')
        g.turn(-3)                                                                    # 240 -> 60 mL (list)
        self.assertIn('60mL', g.text())
        g.press('f4')
        g.to_item('cell')
        g.turn(-1)                                                                    # 20 -> 10 mL
        g.press('f4')
        m = g.h.method
        self.assertEqual((m['temperature_k'], m['vial'], m['cell_volume_m3']), (T0 + 80.0, '60', 10e-6))
        g.edit_ratios([50, 50, 0, 0])                                                 # acetone / ethanol 50 / 50 (om p.57 example, ports 1 and 2)
        self.assertEqual(g.h.method['solvent_fractions'], [0.5, 0.5, 0.0, 0.0])
        g.set_number('cycles', 6)                                                     # 7 cycles x 10 mL cell > 60 mL vial: overflow warning
        self.assertEqual(g.screen(), 'w_overflow')
        self.assertIn('Vial overflow possible.', g.text())
        g.press('f4')                                                                 # YES: back to the method
        self.assertEqual(g.h.method['cycles'], 7)
        g.set_number('cycles', -4)
        self.assertEqual((g.screen(), g.h.method['cycles']), ('edit', 3))
        g.open_item('cycles_table')
        self.assertEqual(g.screen(), 'cycles')
        self.assertIn('n/a', g.lines()[4])
        g.press('f4')                                                                 # EDIT cycle 1
        self.assertEqual((g.screen(), g.item()), ('cycle', 'hold'))
        g.set_number('hold', 1)                                                       # 2 -> 3 min
        g.press('f1')
        self.assertEqual(g.item(), 'cyc1')
        g.press('f2')                                                                 # COPY cycle 1
        g.turn(1)                                                                     # the knob moves the cursor to cycle 2
        self.assertEqual(g.item(), 'cyc2')
        g.press('f3')                                                                 # PASTE
        steps = g.h.method['cycle_steps']
        self.assertEqual((steps[0]['hold_s'], steps[1]['hold_s'], steps[2]['hold_s']), (180.0, 180.0, 120.0))
        self.assertIn('3 min', g.lines()[2])
        g.press('f1')
        g.set_number('flush_solvent', 1)                                              # 1 -> 2 min
        self.assertEqual(g.h.method['solvent_flush_s'], 120.0)
        # SAVE METHOD: slot 001, name PAH-01
        g.extraction('save')
        self.assertEqual(g.item(), 'slot1')
        g.press('f4')
        self.assertEqual(g.screen(), 'name_entry')
        self.assertIn('DEFAULT_', g.text())
        g.type_name('PAH-01')
        self.assertIn('PAH-01_', g.text())
        g.press('f4')                                                                 # ACCEPT
        self.assertEqual(g.screen(), 'main')
        self.assertEqual((g.h.method_name, g.h.method_modified), ('PAH-01', False))
        self.assertIn('PAH-01', g.lines()[0])
        self.assertEqual(g.frame()['displays'][LCD]['strike_spans'][0], [])          # saved: no longer crossed out
        self.assertEqual(g.h.library[1]['method']['cycles'], 3)
        DONE_TASKS.add('Create and save a method')

    def test_run_a_standard_leak_test(self):
        g = Rig()
        g.on()
        g.load_empty()
        g.extraction('positions')
        g.press('f4')
        g.call(g.A, 'thermal.preheat', temperature_k=T0 + 100.0)
        g.until(lambda: abs(g.h.temperature - (T0 + 100.0)) < 0.5)
        g.menu('leak')
        self.assertEqual(g.item(), 'temperature')
        self.assertIn('Current method', g.lines()[0])
        # reference settings: 100 degC, 100 bar, 100 % ethanol (port 2), hold 4 min (om p.43)
        g.set_number('ratio2', 100)
        g.set_number('ratio1', -100)
        self.assertEqual(g.h.leak['solvent_fractions'], [0.0, 1.0, 0.0, 0.0])
        g.open_item('additional')
        self.assertIn('Flush into:', g.text())
        g.press('f1')
        self.assertTrue(g.press('f2'))                                               # START on the function buttons
        self.assertEqual(g.screen(), 'leak_run')
        g.step(10)
        self.assertIn('Tightness', g.text())
        self.assertEqual(g.lines()[6].split()[:6], ['7'] * 6)                        # nitrogen pre-pressure 7 bar
        g.until(lambda: g.h.aux['stage'] == 'hold')
        g.step(30)
        self.assertIn('Hold', g.lines()[2])
        self.assertIn('00:3', g.lines()[8])
        self.assertIn('100 bar', g.lines()[9])
        self.assertIn('Skip', g.lines()[-1])
        g.press('f4')                                                                 # SKIP: stop early, discharge and nitrogen flush
        self.assertEqual(g.h.aux['stage'], 'discharge')
        res = g.h.aux['result']
        self.assertTrue(res['passed'])
        self.assertFalse(res['completed'])
        g.until(lambda: g.h.aux is None)
        st = g.h.read()['leak_test.status']
        self.assertFalse(st['active'])
        self.assertEqual(g.h.heater, 0.0)
        self.assertIn('Finished', g.text())
        self.assertEqual(g.h.counters['leak_tests'], 1)
        # the leak-test solvent went into the empty cells and on into the vials; the ledger closes
        q = g.h._inventory()
        self.assertAlmostEqual(q['initial_solvent_m3'], q['reservoir_solvent_m3'] + q['cell_solvent_m3'] + q['extract_solvent_m3'] + q['waste_solvent_m3']
                               + q['removed_solvent_m3'], places=12)
        self.assertGreater(q['extract_solvent_m3'], 0)
        # a leaking cup seal shows as a lower position pressure (evaluator setting)
        g.call(g.E, 'seal.configure_leak', position=3, leak_pa_s=2e4)
        g.menu('leak')
        g.press('f2')
        g.until(lambda: g.h.aux is None, limit=4000)
        res = g.h.aux_last['leak_test']['result']
        self.assertTrue(res['completed'])
        self.assertFalse(res['passed'])
        self.assertGreater(res['max_difference_pa'], 15e5)
        DONE_TASKS.add('Run a standard leak test')

    def test_flush_the_lines_into_waste(self):
        g = Rig()
        g.on()
        g.load_empty()
        g.extraction('positions')
        g.press('f1')                                                                # NO, then only position 1 on
        g.to_item('pos1')
        g.press('f4')
        g.menu('flush')
        self.assertIn('Waste', g.text())
        self.assertEqual(g.h.flush_set['flush_into'], 'waste')
        g.set_number('solvent_s', -6)                                                 # 120 -> 60 s
        g.set_number('gas_s', -6)                                                     # 180 -> 120 s
        stock = sum(g.h.reservoir)
        self.assertTrue(g.press('f2'))                                               # START
        self.assertEqual(g.screen(), 'main')
        g.step(30)
        self.assertIn('Flush', g.lines()[2])
        self.assertEqual(g.h.aux['kind'], 'flush')
        g.until(lambda: g.h.aux is None)
        used = stock - sum(g.h.reservoir)
        self.assertAlmostEqual(used, 50e-6 / 60 * 60 + 0, delta=2e-7)                # full 50 mL/min for 60 s although five positions are off
        self.assertGreater(sum(g.h.waste), 0.9 * used)
        self.assertEqual(g.h.read()['flush.status']['stage'], 'release')
        # STOP from the flush menu
        g.menu('flush')
        g.press('f2')
        g.step(30)
        g.menu('flush')                                                              # open the flush submenu again: STOP
        self.assertEqual(g.lines()[-1].split()[1], 'STOP')
        self.assertTrue(g.press('f2'))
        g.until(lambda: g.h.aux is None)
        self.assertTrue(g.h.aux_last['flush'] is not None)
        DONE_TASKS.add('Flush the lines into waste')

    def test_start_pause_and_abort_an_extraction(self):
        g = ready_rig()
        self.assertIn('Ready', g.lines()[2])
        self.assertTrue(g.press('start'))
        self.assertEqual(g.h.phase, 'running')
        g.until(lambda: g.h.stage == 'hold')
        self.assertIn('Hold', g.lines()[2])
        self.assertNotEqual(g.lines()[2].split()[0], '--:--:--')                     # remaining time shown
        self.assertTrue(g.press('stop'))                                             # once: pause
        self.assertEqual((g.h.phase, g.h.stage), ('cancelled', 'hold'))
        self.assertIn('Pause', g.lines()[2])
        p = g.h._maxp()
        g.step(5)
        self.assertEqual(g.h._maxp(), p)
        self.assertTrue(g.press('start'))                                            # START continues
        self.assertEqual(g.h.phase, 'running')
        self.assertTrue(g.press('stop'))
        self.assertTrue(g.press('stop'))                                             # twice: abort with discharge and nitrogen flush
        self.assertTrue(g.h.aborting)
        self.assertEqual(g.h.stage, 'discharge')
        self.assertIn('Abort', g.lines()[2])
        g.until(lambda: g.h.stage == 'complete')
        self.assertEqual(g.h.phase, 'cancelled')
        self.assertLessEqual(g.h._maxp(), 1e5)
        self.assertEqual(g.h.heater, 0.0)
        DONE_TASKS.add('Start, pause and abort an extraction')


class MethodsAndRuns(unittest.TestCase):
    def test_full_run_with_vial_change_and_skip(self):
        g = Rig()
        g.on()
        g.extraction('edit')
        g.set_number('cycles', 1)
        g.open_item('cycles_table')
        g.press('f4')
        g.to_item('vc')
        g.turn(1)
        g.press('f4')
        self.assertTrue(g.h.method['cycle_steps'][0]['vial_change'])
        g.main()
        g.preheat_and_occupy()
        g.load_samples()
        g.main()
        g.press('start')
        g.until(lambda: g.h.stage == 'hold')
        self.assertIn('Skip', g.lines()[-1])
        self.assertTrue(g.press('f4'))                                               # SKIP the hold
        self.assertEqual(g.h.stage, 'discharge')
        g.until(lambda: g.h.stage == 'vial_change')
        self.assertEqual(g.screen(), 'notice')                                       # Error 4 Change vials over any screen
        self.assertIn('Error 4', g.text())
        g.press('f4')
        self.assertIn('Vial change', g.lines()[2])
        g.until(lambda: g.h.rack == 0.0)
        g.call(g.E, 'vials.exchange', gripper_confirmed=True)
        self.assertGreater(sum(c['collected'] for c in g.h.cells.values()), 0)
        g.press('start')
        g.until(lambda: g.h.stage == 'complete', limit=4000)
        self.assertEqual((g.h.phase, g.h.cycle), ('succeeded', 2))
        q = g.h._inventory()
        self.assertAlmostEqual(q['initial_target_kg'], sum(q[k] for k in ('sorbed_target_kg', 'dissolved_target_kg', 'extract_target_kg', 'degraded_target_kg',
                                                                         'removed_target_kg')), places=15)
        self.assertAlmostEqual(q['initial_solvent_m3'], sum(q[k] for k in ('reservoir_solvent_m3', 'cell_solvent_m3', 'extract_solvent_m3', 'waste_solvent_m3',
                                                                          'removed_solvent_m3')), places=12)
        items = g.h._product()['items']
        self.assertTrue(all(it['extract_target_kg'] > 0 for it in items))
        self.assertEqual(g.h.counters['extractions'], 1)

    def test_stop_three_times_and_manual_recovery(self):
        g = ready_rig()
        g.press('start')
        g.until(lambda: g.h.stage == 'hold')
        g.press('stop')
        g.press('stop')
        g.step(0.5)
        self.assertTrue(g.press('stop'))                                             # third press: stop immediately
        self.assertEqual((g.h.phase, g.h.stage), ('cancelled', 'halted'))
        p = g.h._maxp()
        g.step(10)
        self.assertGreater(g.h._maxp(), 0.5 * p)
        self.assertIn('Stop', g.lines()[2])
        self.assertFalse(g.press('start'))                                           # START does not resume
        self.assertEqual(g.h.stage, 'halted')
        # service menu: open position valves and the outlet valve, then lower the cell lift (om p.59, 84, 88)
        g.menu('service')
        self.assertEqual(g.screen(), 'q_service')
        g.press('f4')
        g.open_item('valves')
        g.press('f4')                                                                 # NEXT media valve
        g.press('f4')                                                                 # NEXT position valves
        g.to_item('position_open_all')
        g.press('f2')
        self.assertEqual(g.h.valves['position'], [True] * 6)
        g.press('f4')                                                                 # outlet valve
        self.assertEqual(g.item(), 'outlet_vial')
        g.press('f2')                                                                 # Open outlet (to the vials)
        self.assertEqual(g.h.valves['outlet'], 'vial')
        g.step(30)
        self.assertLessEqual(g.h._maxp(), 1e5)
        g.press('f4')                                                                 # END: STATUS with the current settings
        self.assertEqual(g.screen(), 'svc_status')
        self.assertIn('Outlet valve    Vial', g.text())
        g.press('f4')
        g.open_item('lift')
        g.press('f3')                                                                 # DOWN (motor: cell)
        g.until(lambda: g.h.heater == 0.0)
        g.press('f1')
        g.press('f3')
        g.press('f3')
        g.press('f3')
        g.press('f3')
        g.open_item('lift')
        g.turn(1)
        g.press('f4')                                                                 # motor selected: vial
        g.press('f3')
        g.until(lambda: g.h.rack == 0.0)
        g.open_block()
        g.call(g.E, 'sample.unload', position=0, sample_id='soil_0', gripper_confirmed=True)

    def test_open_and_delete_methods_and_crossed_out_name(self):
        g = Rig()
        g.on()
        g.extraction('edit')
        g.set_number('temperature', 10)
        self.assertTrue(g.h.method_modified)
        g.main()
        self.assertEqual(g.frame()['displays'][LCD]['strike_spans'][0], [[11, 18]])   # DEFAULT crossed out
        g.extraction('save')
        g.press('f3')
        g.press('f4')                                                                 # slot 002
        g.type_name('A1')
        g.press('f4')
        g.extraction('edit')
        g.set_number('temperature', 10)
        g.extraction('save')
        g.press('f4')
        g.type_name('B2')
        g.press('f4')
        self.assertEqual({k: v['name'] for k, v in g.h.library.items()}, {1: 'B2', 2: 'A1'})
        g.extraction('open')
        g.press('f3')
        g.press('f4')                                                                 # OPEN 002
        self.assertEqual((g.h.method_name, g.h.method['temperature_k']), ('A1', T0 + 110.0))
        g.extraction('open')
        g.to_item('slot5')
        g.press('f4')
        self.assertIn('Empty position.', g.text())
        g.extraction('delete')
        g.press('f4')
        self.assertEqual(g.screen(), 'q_delete')
        g.press('f4')
        self.assertEqual(list(g.h.library), [2])
        g.extraction('edit')
        self.assertEqual(g.lines()[-1].split()[1], 'Default')
        g.press('f2')                                                                 # DEFAULT parameters
        self.assertEqual(g.h.method['temperature_k'], T0 + 100.0)

    def test_eco_mode_and_preheat_refused_with_cells(self):
        g = ready_rig()
        g.extraction('preheat')
        g.to_item('eco')
        g.turn(1)
        self.assertEqual(g.lines()[-1].split(), ['ESC', 'OK'])
        g.press('f4')
        self.assertTrue(g.h.eco)
        g.to_item('preheat')
        g.turn(5)
        self.assertFalse(g.press('f4'))                                              # the system is closed with cells: refused
        self.assertEqual(g.screen(), 'message')
        self.assertIn('Remove the cells', g.text())
        g.main()
        g.press('start')
        g.until(lambda: g.h.stage == 'complete', limit=4000)
        self.assertFalse(g.h.preheat)                                                # EcoMode switched the heater off after the run
        self.assertFalse(g.h.eco)


class SettingsAndLists(unittest.TestCase):
    def test_language_contrast_beeps_and_preheat_demand(self):
        g = Rig()
        g.on()
        g.menu('settings')
        g.set_number('language', 1)
        self.assertEqual(g.h.settings['language'], 'Deutsch')
        g.main()
        self.assertIn('Temperatur:', g.lines()[6])
        self.assertIn('Bereit', g.lines()[2])
        self.assertIn('Extraktion', g.lines()[-1])
        g.menu('settings')
        g.set_number('contrast', -4)
        self.assertEqual(g.h.settings['contrast'], 50)
        self.assertEqual(g.frame()['displays'][LCD]['brightness'], 75.0)
        g.set_number('beep_key', 1)
        beeps = g.h.key_beeps
        g.press('f3')
        self.assertEqual(g.h.key_beeps, beeps + 1)
        g.to_item('preheat_demand')
        g.turn(1)
        g.press('f4')
        self.assertTrue(g.h.settings['preheat_demand'])
        g.switch('off')
        self.assertEqual(g.screen(), 'off')
        self.assertEqual(''.join(g.lines()), '')
        g.switch('on')
        self.assertEqual(g.screen(), 'preheat')                                      # PREHEAT submenu at power-on (om p.40)

    def test_solvent_list_edit_and_default(self):
        g = Rig()
        g.on()
        g.menu('solvlist')
        self.assertIn('001 Acetone', g.text())
        g.to_item('sol4')
        g.press('f4')                                                                 # EDIT Dichloromethane
        self.assertIn('Dichloromethane_', g.text())
        g.type_name('DCM')
        g.press('f4')
        self.assertEqual(g.h.solvent_list[3], 'DCM')
        self.assertEqual((g.screen(), g.item()), ('solvlist', 'sol4'))
        g.to_item('sol11')
        g.press('f4')
        g.type_name('CHLOROBENZENE')
        g.turn(len(GRID) - 1 - g.frame()['ui']['vars']['grid_pos'])
        g.press('f3')                                                                 # BACKSPACE
        g.press('f4')
        self.assertEqual(g.h.solvent_list[10], 'CHLOROBENZEN')
        g.to_item('sol1')
        g.press('f2')                                                                 # DEFAULT
        self.assertIn('First 10 entries replaced', g.text())
        g.press('f4')
        self.assertEqual(g.h.solvent_list[3], 'Dichloromethane')
        self.assertEqual(g.h.solvent_list[10], 'CHLOROBENZEN')
        # the method's solvent type comes from the list (LIST) or is renamed directly (EDIT)
        g.extraction('edit')
        g.open_item('solvent')
        g.press('f3')                                                                 # LIST
        g.turn(3)
        g.press('f4')
        self.assertEqual(g.h.method['solvent_names'][0], 'Dichloromethane')
        g.to_item('type2')
        g.press('f4')                                                                 # EDIT the name of port 2
        g.type_name('ETOH')
        g.press('f4')
        self.assertEqual(g.h.method['solvent_names'][1], 'ETOH')
        self.assertEqual(g.h.solvent_list[1], 'Acetonitrile')                        # the list is not changed

    def test_ratio_sum_must_be_100(self):
        g = Rig()
        g.on()
        g.extraction('edit')
        g.open_item('solvent')
        g.set_number('ratio1', -10)
        self.assertIsNone(g.h.method)                                                # 90 %: kept on the screen, not written
        g.press('f1')
        self.assertEqual(g.screen(), 'message')
        self.assertIn('equal to 100 %', g.text())


class Service(unittest.TestCase):
    def enter(self, g, item):
        g.menu('service')
        g.press('f4')
        g.open_item(item)

    def test_valves_pump_error51_and_flow_test(self):
        g = Rig()
        g.on()
        self.enter(g, 'pump')
        self.assertFalse(g.press('f2'))
        self.assertEqual(g.screen(), 'message')                                      # no open path: Error 51
        self.assertIn('Error 51', g.text())
        g.load_empty(positions=[1])
        g.call(g.A, 'lift.move', lift='cell', direction='up')
        g.call(g.A, 'lift.move', lift='vial', direction='up')
        g.step(5)
        for v in ('solvent_1', 'media_solvent', 'position_1', 'outlet_waste'):
            g.call(g.A, 'valve.set', valve=v, open=True)
        self.enter(g, 'pump')
        g.set_number('flow', 9)                                                       # 1 -> 10 mL/min
        self.assertTrue(g.press('f2'))                                               # ON
        g.step(20)
        self.assertIn('10 mL/min', g.text())
        self.assertGreater(sum(g.h.waste), 0)
        self.enter(g, 'valves')
        g.press('f4')
        g.press('f4')
        g.press('f4')                                                                 # outlet valve page
        g.to_item('outlet_waste')
        g.press('f2')                                                                 # close the outlet: the pump runs against a closed valve
        self.assertFalse(g.h.pump['on'])
        self.assertEqual(g.screen(), 'notice')
        self.assertIn('Error 51', g.text())
        g.press('f4')
        # flow test through position 1 into the waste
        g.call(g.A, 'valve.set', valve='outlet_waste', open=True)
        self.enter(g, 'flow')
        g.to_item('into')
        g.turn(1)
        g.press('f4')
        self.assertEqual(g.h.flow_set['flush_into'], 'waste')
        self.assertTrue(g.press('f3'))                                               # START
        g.step(30)
        self.assertIn('3.0 bar', g.text())
        self.assertIn('30 s', g.text())
        self.assertEqual(g.lines()[-1].split()[-1], 'STOP')
        g.press('f3')
        self.assertIsNone(g.h.aux)

    def test_sensors_zero_lifts_fans_hours(self):
        g = Rig()
        g.on()
        g.call(g.E, 'sensor.configure_error', sensor=2, offset_pa=0.3e5)
        self.enter(g, 'sensors')
        self.assertIn('Rack', g.text())
        self.assertIn('Lower[X]', g.text())
        g.press('f4')
        self.assertIn('0.3 bar', g.lines()[3])                                       # position 2 reads 0.3 bar
        g.press('f3')                                                                 # CALIBRATE to 0 bar
        self.assertIn('0.0 bar', g.lines()[3])
        g.press('f4')
        self.assertIn('Electronic:', g.text())
        g.press('f4')
        g.open_item('lift')
        g.call(g.A, 'shield.set_lift', lift_m=0.1)
        g.press('f2')                                                                 # UP with the shield open: Error 10
        self.assertIn('Error 10', g.text())
        g.call(g.A, 'shield.set_lift', lift_m=0.0)
        g.main()
        self.enter(g, 'lift')
        g.press('f2')
        g.step(1)
        self.assertIn('0.350 A', g.text())
        g.press('f4')                                                                 # STOP
        h = g.h.heater
        g.step(2)
        self.assertEqual(g.h.heater, h)
        g.press('f1')
        g.open_item('fan')
        g.set_number('extraction', 2)
        self.assertEqual(g.h.fans['extraction'], 50)
        g.press('f1')
        g.open_item('hours')
        self.assertIn('350 mA', g.text())
        g.press('f2')                                                                 # RESET the current maxima
        self.assertIn(' 0 mA', g.text())
        g.press('f1')
        g.open_item('unit')
        self.assertIn('01.03', g.text())


class FaultsAndRefusals(unittest.TestCase):
    def test_start_refusals_follow_the_manual_errors(self):
        g = Rig()
        g.on()
        g.press('start')
        self.assertIn('Error 58', g.text())                                          # no position
        g.extraction('positions')
        g.press('f4')
        g.press('start')
        self.assertIn('Error 7', g.text())                                           # no method written
        g.main()
        g.extraction('edit')
        g.set_number('temperature', -40)
        g.main()
        g.press('start')
        self.assertIn('Error 2', g.text())                                           # not preheated
        g.main()
        g.call(g.A, 'shield.set_lift', lift_m=0.18)
        g.press('start')
        self.assertIn('Error 10', g.text())
        g.call(g.A, 'drawer.set_extension', extension_m=0.16)
        g.call(g.A, 'drawer.set_extension', extension_m=0.0)
        g.call(g.A, 'shield.set_lift', lift_m=0.0)
        self.assertEqual(g.h.phase, 'idle')
        g.main()
        g.extraction('preheat')
        g.press('f2')
        g.until(lambda: abs(g.h.temperature - g.h.method['temperature_k']) <= 1)
        g.load_samples(n=5)                                                           # position 6 active but empty
        g.main()
        g.extraction('edit')
        g.turn(1)
        self.assertFalse(g.press('f4'))                                              # the method is locked once cells are loaded
        self.assertIn('Method cannot be changed:', g.text())
        g.main()
        g.press('start')
        g.until(lambda: g.h.phase == 'failed')
        self.assertEqual(g.screen(), 'notice')
        self.assertIn('Error 50', g.text())                                          # position 6: pressure too low
        self.assertIn('Position 6', g.text())
        g.press('f4')
        self.assertEqual(g.screen(), 'main')
        self.assertIn('Error', g.lines()[2])

    def test_power_gate_and_no_timeouts(self):
        g = Rig()
        self.assertFalse(g.press('f2'))
        self.assertEqual(g.screen(), 'off')
        g.on()
        g.menu('settings')
        g.step(3600)
        self.assertEqual(g.screen(), 'settings')                                      # no menu time-out
        g.switch('off')
        self.assertFalse(g.h.powered)
        self.assertFalse(g.press('f1'))
        self.assertEqual(g.screen(), 'off')


class Equivalence(unittest.TestCase):
    def test_panel_and_direct_commands_reach_the_same_state(self):
        a = Rig()
        a.on()
        a.extraction('edit')
        a.set_number('temperature', -20)
        a.extraction('positions')
        a.press('f4')
        a.extraction('preheat')
        a.press('f4')
        a.press('f2')
        a.menu('leak')
        a.set_number('pressure', -10)
        a.menu('settings')
        a.set_number('contrast', 2)
        b = Rig()
        b.call(b.A, 'power.set', enabled=True)
        b.call(b.A, 'method.configure', **dict(rt.DEFAULT_METHOD, temperature_k=T0 + 80.0))
        b.call(b.A, 'positions.set', active=[True] * 6)
        b.call(b.A, 'thermal.preheat', temperature_k=T0 + 80.0, enabled=False)
        b.call(b.A, 'thermal.preheat', temperature_k=T0 + 80.0, enabled=True)
        b.call(b.A, 'leak_test.configure', pressure_gauge_pa=9e6)
        b.call(b.A, 'settings.configure', key='contrast', value=80)
        ra, rb = a.h.read('evaluator'), b.h.read('evaluator')
        self.assertEqual(ra, rb)
        a.step(200)
        b.step(200)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))


class SyncFixes(unittest.TestCase):
    """Sync audit 2026-10-19: one pressure-reading model for every path and for control (R1), caller rules (R2), the extraction fan (R3)."""

    def test_device_controls_on_its_own_pressure_sensors(self):
        g = ready_rig()
        g.call(g.E, 'sensor.configure_error', sensor=2, offset_pa=2e5)                # position 2 reads 2 bar at rest
        m = g.call(g.A, 'extraction.measure')
        self.assertAlmostEqual(m['max_pressure_gauge_pa'], 2e5)
        self.assertAlmostEqual(g.E.read(instance_id=INST, state_ids=['pressure.max_gauge_pa'])['observations']['pressure.max_gauge_pa']['value'], 2e5)
        self.assertIn('2 bar', g.lines()[7])                                          # main display: max. Pressure
        self.assertEqual(g.E.read(instance_id=INST, state_ids=['truth.max_pressure_gauge_pa'])['observations']['truth.max_pressure_gauge_pa']['value'], 0.0)
        g.call(g.E, 'sensor.configure_error', sensor=2, offset_pa=3e5)                # now 3 bar high
        self.assertTrue(g.press('start'))
        op = g.h.operation_id
        g.until(lambda: g.h.stage == 'hold')
        g.step(20)
        rd, true = g.h._readings(), [g.h.cells[i]['pressure'] for i in range(6)]
        self.assertLess(abs(rd[2] - 100e5), 1e4)                                      # the pump holds the sensor at the set point ...
        self.assertLess(abs(true[1] - 97e5), 2e4)                                      # ... so the real cell sits 3 bar lower
        self.assertLess(abs(true[0] - 100e5), 1e4)
        for v in (g.E.read(instance_id=INST, state_ids=['pressure.max_gauge_pa'])['observations']['pressure.max_gauge_pa']['value'], g.call(g.A, 'extraction.measure')['max_pressure_gauge_pa'],
                  g.A.poll(op)['result']['max_pressure_gauge_pa']):
            self.assertAlmostEqual(v, max(rd[1:]))                                     # slot, measure and poll show the same reading
        self.assertAlmostEqual(g.E.read(instance_id=INST, state_ids=['truth.max_pressure_gauge_pa'])['observations']['truth.max_pressure_gauge_pa']['value'], max(true))
        g.call(g.E, 'sensor.configure_error', sensor=2, offset_pa=0.0)
        g.until(lambda: g.h.stage == 'complete')
        self.assertGreaterEqual(g.h.counters['max_pressure_pa'], 100e5 - 1e4)

    def test_extraction_fan_changes_the_heating_block_temperature(self):
        g = Rig()
        g.on()
        g.call(g.A, 'thermal.preheat', temperature_k=T0 + 100.0)
        g.until(lambda: abs(g.h.temperature - (T0 + 100.0)) < 0.01)                    # factory 30 %: the set temperature
        g.call(g.A, 'fan.set', fan='extraction', percent=100)
        g.step(200)
        amb = rt.EXAMPLE['ambient_k']
        self.assertAlmostEqual(g.h.temperature, (T0 + 100.0 + 0.07 * amb) / 1.07, places=3)   # about 5 K low
        self.assertIn('95 °C', g.text())
        g.call(g.A, 'fan.set', fan='extraction', percent=0)
        g.step(200)
        self.assertAlmostEqual(g.h.temperature, (T0 + 100.0 - 0.03 * amb) / 0.97, places=3)   # about 2.4 K high
        g.call(g.A, 'fan.set', fan='extraction', percent=30)
        g.h._fail('nitrogen_supply_empty')                                             # a fault event: the fan runs at 100 %
        self.assertEqual((g.h._fan_percent(), g.E.read(instance_id=INST, state_ids=['service.fans'])['observations']['service.fans']['value']['extraction']), (100, 30))

    def test_caller_rules(self):
        g = ready_rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertTrue(g.press('start'))
        op = g.h.operation_id
        self.assertEqual(g.h._owners, {op: 'agent'})
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        g.until(lambda: g.h.stage == 'hold')
        before = json.dumps(g.h.read('evaluator'), sort_keys=True, default=str)
        self.assertFalse(g.call(B, 'panel.press', input_id='stop')['accepted'])        # B's STOP: a note, the run goes on
        self.assertEqual(g.screen(), 'message')
        self.assertIn('Not permitted', g.text())
        for cap, args in (('acquisition.stop', {}), ('acquisition.abort', {}), ('acquisition.halt', {}), ('acquisition.skip', {}),
                          ('fan.set', {'fan': 'extraction', 'percent': 100}), ('thermal.set_eco_mode', {'enabled': True}),
                          ('thermal.preheat', {'enabled': False})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(json.dumps(g.h.read('evaluator'), sort_keys=True, default=str), before)
        g.A.cancel(op, 'c1')                                                           # the owner pauses its own panel-started run
        self.assertEqual(g.h.phase, 'cancelled')
        for cap, args in (('acquisition.start', {}), ('valve.set', {'valve': 'position_2', 'open': True}), ('pressure.calibrate', {})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)   # B cannot resume it or vent its cells
        g.main()
        self.assertTrue(g.press('start'))                                              # A resumes: the new operation is A's
        self.assertEqual(g.h._owners[g.h.operation_id], 'agent')
        self.assertTrue(g.call(B, 'panel.switch', input_id='power', position='off')['accepted'])   # 1.2.0: the mains rocker is physical: never refused
        self.assertFalse(g.h.powered)
        self.assertNotEqual(g.h.phase, 'running')                                     # a power loss ends the extraction

    def test_caller_rules_for_the_flush(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.on()
        g.load_empty()
        g.call(g.A, 'positions.set', active=[True] * 6)
        g.call(g.A, 'flush.start')
        self.assertEqual(g.h.aux['owner'], 'agent')
        for cap, args in (('flush.stop', {}), ('fan.set', {'fan': 'extraction', 'percent': 0})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(g.h.aux['kind'], 'flush')
        g.call(g.A, 'flush.stop')
        self.assertEqual(g.h.aux['stage'], 'release')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = ready_rig()
            g.press('start')
            g.until(lambda: g.h.stage == 'hold')
            return g
        a, b = scenario(), scenario()
        ia, ib = a.h.panel.render(LCD), b.h.panel.render(LCD)
        self.assertEqual(ia.dtype.name, 'uint8')
        self.assertTrue((ia == ib).all())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png(LCD, OUT / 'home.png')}
        a.press('f2')
        shots['menu'] = a.h.panel.save_png(LCD, OUT / 'menu.png')
        a.main()
        a.press('f1')
        shots['status'] = a.h.panel.save_png(LCD, OUT / 'status.png')
        c = Rig()
        c.on()
        c.extraction('edit')
        shots['edit'] = c.h.panel.save_png(LCD, OUT / 'edit.png')
        c.main()
        c.press('start')
        shots['error'] = c.h.panel.save_png(LCD, OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')
        img = c.h.panel.render(LCD)
        self.assertEqual(img.shape, (128, 288, 3))

    def test_capture_and_copy(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=LCD)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.on()
        r2 = g.call(g.A, 'panel.capture', display_id=LCD)
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.turn', 'panel.switch', 'panel.capture', 'panel.frame'})
        m = rt.export_buchi_speedextractore_contract_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in list(rt.PANEL_CAPS) + list(rt.DEVICE_CAPS):
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'graphic_lcd_softkeys', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/buchi_speedextractore.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('se_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        self.assertEqual(DONE_TASKS, set(SURVEY_TASKS))


if __name__ == '__main__':
    unittest.main()
