"""front-panel tests for CHEM-144 shimadzu_ags10knxd (panel_ui.json, APPROXIMATE + scripts/asset_runtime/_panel_ui.py + the 1.1.0 operating-
panel functions of the runtime):  python3 -m pytest tests/test_panel_ui_shimadzu_ags10knxd.py -q

Every gesture goes through the session API (panel.press / panel.switch, the runtime's normal invoke path).  Each screen check reads the
structured frame (panel.frame) and the rendered pixels: every character cell of the VFD equals the cell drawn alone from the frame text
with the spec colours.  Themes: the six key procedures of the menu survey (test with a stored method, jog to the mounting position, zero
force / stroke, RETURN after a test, a series of specimens, emergency stop); display modes and lamps; METHOD and FILE menus; SYSTEM
(not documented); panel = direct capability; refusals; render determinism; evidence PNGs; contract entries."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import shimadzu_ags10knxd as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'ags_01'
OUT = ROOT / 'outputs/panel_ui/shimadzu_ags10knxd'
CONTRACT = rt.ASSET / 'asset.contract.json'
SURVEY_TASKS = ['run a test from the tester with a PC-stored method', 'move the crosshead to the specimen mounting position', 'zero force or stroke',
                'return the crosshead after a test', 'run a series of specimens with TRAPEZIUM X', 'stop in an emergency']
DONE_TASKS = set()
KEY_SEEN = set()
VFD = rt.PANEL_SPEC['displays']['vfd']
MM_MIN = 1e-3 / 60
CAPTURE = Path(tempfile.mkdtemp(prefix='ags_panel_'))


def cell_image(ch, inverse=False):
    """One character cell drawn alone (as the VFD draws it)."""
    from PIL import Image, ImageDraw
    cw, chh = VFD['cell']
    px = VFD['dot']
    bg, fg = tuple(VFD['colors']['background']), tuple(VFD['colors']['text'])
    img = Image.new('RGB', (cw, chh), fg if inverse else bg)
    dr = ImageDraw.Draw(img)
    for gx, bits in enumerate(pu.FONT5X7.get(ch, pu.FONT5X7['?'])):
        for gy in range(8):
            if bits >> gy & 1:
                dr.rectangle([1 + gx * px, 1 + gy * px, gx * px + px, gy * px + px], fill=bg if inverse else fg)
    return np.asarray(img)


def fline(label, newton=None):
    """VFD line 1: FORCE / PEAK / BREAK in N (0.01 N), right-aligned on the 20 columns."""
    return f'{label:<6}' + ('        ---- N' if newton is None else f'{newton:>12.2f} N')


def sline(mm):
    return f'STROKE{mm:>11.3f} mm'


class Rig:
    """A configured tester (plant, SCG tensile fixture with TRAPEZIUM X and the extensometer, a steel specimen) with the agent at the panel."""

    def __init__(self, power=True, sample=True, grips=True, method=True, plant=None):
        self.h = rt.ShimadzuAgs10knxdRuntime(INST)
        self.h.panel_capture_dir = str(CAPTURE)
        orig = self.h.invoke

        def recorded(cap, **a):
            out = orig(cap, **a)
            KEY_SEEN.add(cap)
            return out
        self.h.invoke = recorded
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, **(plant or {})))
        self.call(self.E, 'fixture.configure', **rt.FIXTURE_EXAMPLE)
        if method:                                                            # the current test method of the tester
            self.call(self.A, 'method.configure', **rt.METHOD_EXAMPLE)
        if sample:
            self.call(self.E, 'sample.load', **rt.SAMPLE_EXAMPLE)
        if sample and grips:
            self.grips(True)
        if power:
            assert self.switch('on')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'm{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def step(self, s):
        self.h.step(float(s))

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def grips(self, closed):
        for side in ('upper', 'lower'):
            self.call(self.E, 'grip.set_closed', which=side, closed=closed)
        self.step(1.0)

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self):
        """Text rows shown now, from the frame and from the pixels (every character cell must agree)."""
        d = self.frame()['displays']['vfd']
        img = self.h.panel.render('vfd')
        cw, chh, m = VFD['cell'][0], VFD['cell'][1], VFD['margin']
        for r in range(VFD['rows']):
            text = d['shown'][r] if r < len(d['shown']) else ''
            for c in range(VFD['cols']):
                ch = text[c] if c < len(text) else ' '
                got = img[m + r * chh:m + (r + 1) * chh, m + c * cw:m + (c + 1) * cw]
                assert np.array_equal(got, cell_image(ch, bool(d['inverse'][r]) if r < len(d['inverse']) else False)), (r, c, ch)
        return [x.rstrip() for x in d['lines']]

    def lamps(self):
        f = self.frame()['displays']
        return sorted(k[4:] for k, v in f.items() if k.startswith('led_') and v['shown'])

    def item(self):
        m = self.frame()['ui']['menu']
        return m and m['item']

    def editor(self):
        ed = self.frame()['ui']['editor']
        return ed and ed['buffer'][ed['field']]

    # gestures
    def press(self, key, hold=0.1, held=None):
        args = dict(input_id=key, duration_s=hold)
        if held:
            args['held'] = held
        return self.call(self.A, 'panel.press', **args)['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def to(self, item_id, limit=6):
        """Jog keys (up / down) until the cursor is on this item."""
        for key in ('key_down', 'key_up'):
            for _ in range(limit):
                if self.item() == item_id:
                    return True
                if not self.press(key):
                    break
        assert self.item() == item_id, (self.item(), item_id)
        return True

    def digits(self, value):
        """Enter a 7-digit value (0000.000) digit by digit: the jog keys change the digit, START moves to the next one (the last confirms)."""
        want = f'{value:08.3f}'.replace('.', '')
        for k in range(7):
            ed = self.frame()['ui']['editor']
            assert ed['digit'] == k, (ed['digit'], k)
            cur = f'{ed["buffer"][ed["field"]]:08.3f}'.replace('.', '')
            diff = int(want[k]) - int(cur[k])
            for _ in range(abs(diff)):
                assert self.press('key_up' if diff > 0 else 'key_down')
            assert self.press('key_start')

    def set_speed(self, mm_min):
        assert self.press('key_method')
        self.to('speed')
        assert self.press('key_start')
        self.digits(mm_min)
        assert self.press('key_test')

    def file(self, item, number):
        """FILE > LOAD FILE / SAVE FILE nn (jog keys change the number, START confirms)."""
        assert self.press('key_file')
        self.to(item)
        assert self.press('key_start')
        diff = number - int(self.editor())
        for _ in range(abs(diff)):
            assert self.press('key_up' if diff > 0 else 'key_down')
        ok = self.press('key_start')
        self.press('key_test')
        return ok

    def run_to_break(self, limit=120, start=True):
        assert not start or self.press('key_start')
        for _ in range(limit):
            if self.val('acquisition.phase') != 'running':
                break
            self.step(1.0)
        return self.val('acquisition.phase')

    def unload(self):
        """Open the manual grips and take the broken halves out (operator actions, not panel keys)."""
        self.grips(False)
        self.call(self.E, 'sample.remove')


class SurveyProcedures(unittest.TestCase):
    def test_run_a_test_with_a_stored_method(self):
        """The method is selected on the operating panel (here: a method file kept in the tester; PC-linked methods are not simulated), START
        starts the test; the result is read by the agent that pressed START."""
        r = Rig()
        r.call(r.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, speed_m_s=60 * MM_MIN))
        self.assertTrue(r.file('save', 5))
        self.assertEqual(r.val('method.files')[4], 'speed 60 mm/min')
        r.call(r.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, speed_m_s=10 * MM_MIN))
        self.assertTrue(r.file('load', 5))
        self.assertAlmostEqual(r.h.method['speed_m_s'], 60 * MM_MIN)
        self.assertEqual(r.screen(), 'test')
        self.assertEqual(r.lines(), [fline('FORCE', 0.0), sline(0.0)])
        self.assertEqual(r.run_to_break(), 'succeeded')
        op = r.h.operation_id
        res = r.A.invoke(instance_id=INST, capability_id='acquisition.result', arguments={}, request_id='res')
        self.assertEqual(res['status'], 'succeeded', res['error'])
        self.assertEqual((res['result']['operation_id'], res['result']['terminal_reason']), (op, 'fracture'))
        self.assertGreater(len(res['result']['rows']), 100)
        self.assertTrue(r.press('key_disp'))
        peak = max(x['force_n'] for x in res['result']['rows'])
        self.assertEqual(r.lines()[0], fline('PEAK', peak))
        self.assertEqual(r.lamps(), ['peak', 'test'])
        DONE_TASKS.add('run a test from the tester with a PC-stored method')

    def test_move_the_crosshead_to_the_mounting_position(self):
        """Jog keys move the crosshead for as long as they are held (HIGH SPEED held: ten times faster); it stops at the lower end, the specimen
        loading position."""
        r = Rig(sample=False)
        self.assertTrue(r.press('key_up', hold=6.0))
        self.assertAlmostEqual(r.val('crosshead.target_m'), 6.0 * 50 * MM_MIN)
        r.step(6.0)
        self.assertEqual(r.lines()[1], sline(5.0))
        self.assertTrue(r.press('key_up', hold=3.0, held=['key_high']))
        r.step(3.0)
        self.assertAlmostEqual(r.val('crosshead.position_m'), 6.0 * 50 * MM_MIN + 3.0 * 500 * MM_MIN)
        self.assertEqual(r.lines()[1], sline(30.0))
        self.assertTrue(r.press('key_down', hold=4.0, held=['key_high']))       # longer than needed: stops at the lower end
        self.assertEqual(r.val('crosshead.target_m'), 0.0)
        r.step(4.0)
        self.assertEqual(r.val('crosshead.position_m'), 0.0)
        r.call(r.E, 'sample.load', **rt.SAMPLE_EXAMPLE)                          # loading needs the crosshead at the mounting position
        r.grips(True)
        self.assertTrue(r.val('sample.present'))
        DONE_TASKS.add('move the crosshead to the specimen mounting position')

    def test_zero_force_or_stroke(self):
        r = Rig(sample=False)
        r.press('key_up', hold=12.0)
        r.step(12.0)
        r.call(r.A, 'force.calibrate', reference_force_n=1.0, expect='rejected')   # nothing on the cell
        before = r.lines()
        self.assertEqual(before[1], sline(10.0))
        self.assertTrue(r.press('key_zero_stroke'))
        self.assertAlmostEqual(r.val('display.stroke_zero_m'), 10e-3)
        self.assertEqual(r.lines()[1], sline(0.0))
        self.assertAlmostEqual(r.val('crosshead.indicated_m'), 10e-3)            # the crosshead and its public reading stay where they are
        self.assertTrue(r.press('key_zero_force'))
        self.assertEqual(r.val('calibration.force_zero_n'), r.h.sensor_force)
        self.assertEqual(r.lines()[0], fline('FORCE', 0.0))
        DONE_TASKS.add('zero force or stroke')

    def test_return_the_crosshead_after_a_test(self):
        r = Rig()
        self.assertEqual(r.run_to_break(), 'succeeded')
        top = r.val('crosshead.position_m')
        self.assertGreater(top, 1e-3)
        self.assertFalse(r.press('key_return'))                                  # broken halves still clamped: refused, nothing moves
        self.assertEqual(r.val('crosshead.target_m'), top)
        r.unload()
        self.assertTrue(r.press('key_return'))
        self.assertEqual(r.val('crosshead.target_m'), 0.0)
        r.step(top / (1000 * MM_MIN) + 0.01)
        self.assertEqual(r.val('crosshead.position_m'), 0.0)
        self.assertEqual(r.lines()[1], sline(0.0))
        DONE_TASKS.add('return the crosshead after a test')

    def test_run_a_series_of_specimens(self):
        """Mount, START, RETURN, mount the next, START again; each specimen is its own operation (TRAPEZIUM X wizard and PC not simulated)."""
        r = Rig()
        r.set_speed(120.0)
        self.assertAlmostEqual(r.h.method['speed_m_s'], 120 * MM_MIN)
        ops = []
        for n in range(3):
            self.assertTrue(r.press('key_zero_force'))
            self.assertEqual(r.run_to_break(), 'succeeded', n)
            ops.append(r.h.operation_id)
            r.unload()
            self.assertTrue(r.press('key_return'))
            r.step(5.0)
            if n < 2:
                r.call(r.E, 'sample.load', **rt.SAMPLE_EXAMPLE)
                r.grips(True)
        self.assertEqual(len(set(ops)), 3)
        for op in ops:
            self.assertEqual(r.h.records[op]['terminal_reason'], 'fracture')
            self.assertEqual(r.h._owners[op], 'agent')
        DONE_TASKS.add('run a series of specimens with TRAPEZIUM X')

    def test_stop_in_an_emergency(self):
        r = Rig()
        self.assertTrue(r.press('key_start'))
        r.step(1.0)
        self.assertEqual(r.val('acquisition.phase'), 'running')
        self.assertTrue(r.press('key_emergency'))
        self.assertEqual((r.val('acquisition.phase'), r.h.terminal_reason), ('cancelled', 'EMERGENCY_STOP'))
        pos = r.val('crosshead.position_m')
        self.assertEqual(r.val('crosshead.target_m'), pos)                       # stops at once
        self.assertEqual(r.screen(), 'emergency')
        self.assertEqual(r.lines(), ['EMERGENCY STOP', 'RELEASE THE BUTTON'])
        self.assertEqual(r.lamps(), ['real'])                                    # the display-mode lamp stays
        for key in ('key_start', 'key_return', 'key_up', 'key_method'):
            r.press(key)
        r.step(2.0)
        self.assertEqual(r.val('crosshead.position_m'), pos)
        self.assertEqual(r.val('acquisition.phase'), 'cancelled')
        r.call(r.E, 'emergency.reset')                                           # turning the button out: a host action
        self.assertEqual(r.screen(), 'test')
        DONE_TASKS.add('stop in an emergency')


class DisplayModes(unittest.TestCase):
    def test_lines_values_and_lamps(self):
        r = Rig()
        self.assertEqual(r.lamps(), ['real', 'test'])
        self.assertTrue(r.press('key_disp'))
        self.assertEqual((r.lines(), r.lamps()), ([fline('PEAK'), sline(0.0)], ['peak', 'test']))
        self.assertTrue(r.press('key_disp'))
        self.assertEqual((r.lines()[0], r.lamps()), (fline('BREAK'), ['break', 'test']))
        self.assertTrue(r.press('key_line'))
        self.assertEqual(r.lines(), [fline('BREAK'), ''])
        self.assertTrue(r.press('key_disp'))
        self.assertTrue(r.press('key_line'))
        self.assertEqual(r.lines()[1], sline(0.0))
        self.assertTrue(r.press('key_start'))
        r.step(2.0)
        f = r.val('force.indicated_n')
        self.assertEqual(r.lines()[0], fline('FORCE', f))
        r.run_to_break(start=False)
        rows = r.h.records[r.h.operation_id]['rows']
        pre = [x['force_n'] for x in rows if x['kind'] == 'fracture_pre'][-1]
        self.assertEqual((r.val('acquisition.break_force_n'), r.val('acquisition.peak_force_n')), (pre, max(x['force_n'] for x in rows)))
        r.press('key_disp')
        r.press('key_disp')
        self.assertEqual(r.lines()[0], fline('BREAK', pre))
        r.switch('off')
        self.assertEqual((r.screen(), r.lines(), r.lamps()), ('off', ['', ''], []))
        self.assertFalse(r.press('key_disp'))                                    # off: only the power switch and the emergency stop act
        r.switch('on')
        self.assertEqual(r.screen(), 'test')


class MenuFunctions(unittest.TestCase):
    def test_method_menu(self):
        r = Rig()
        self.assertTrue(r.press('key_method'))
        self.assertEqual((r.screen(), r.lines(), r.lamps()), ('method', ['METHOD', '>MODE          SPEED'], ['method', 'real']))
        self.assertTrue(r.press('key_start'))                                    # MODE: jog keys change, START confirms
        self.assertEqual(r.lines(), ['MODE', 'SPEED'])
        self.assertTrue(r.press('key_up'))
        self.assertEqual(r.lines(), ['MODE', 'FORCE'])
        self.assertTrue(r.press('key_start'))
        self.assertEqual(r.h.method['mode'], 'force')
        self.assertEqual(r.h.method['sample_rate_hz'], 100)                      # automatic control samples at most 100 Hz
        r.call(r.E, 'fixture.configure', **dict(rt.FIXTURE_EXAMPLE, extensometer_enabled=False), expect='rejected')   # specimen in: refused
        self.assertTrue(r.press('key_start'))
        r.press('key_up')
        self.assertEqual(r.lines(), ['MODE', 'STRAIN'])
        self.assertTrue(r.press('key_return'))                                   # RETURN cancels the edit
        self.assertEqual((r.h.method['mode'], r.lines()[1]), ('force', '>MODE          FORCE'))
        self.assertTrue(r.press('key_down'))
        self.assertEqual(r.lines(), ['METHOD', '>SPEED      0030.000'])
        self.assertTrue(r.press('key_start'))
        r.digits(250.5)
        self.assertAlmostEqual(r.h.method['speed_m_s'], 250.5 * MM_MIN)
        self.assertEqual(r.lines()[1], '>SPEED      0250.500')
        self.assertTrue(r.press('key_down'))
        self.assertEqual(r.lines()[1], '>STOP F       5000 N')
        self.assertTrue(r.press('key_start'))
        for _ in range(3):
            r.press('key_down')
        self.assertTrue(r.press('key_start'))
        self.assertEqual(r.h.method['force_stop_n'], 4970.0)
        self.assertEqual(r.h.method['mode'], 'force')
        self.assertTrue(r.press('key_up'))
        self.assertTrue(r.press('key_up'))
        self.assertTrue(r.press('key_start'))                                    # MODE again: back to SPEED
        r.press('key_down')
        r.press('key_start')
        self.assertEqual(r.h.method['mode'], 'speed')
        self.assertTrue(r.press('key_test'))
        self.assertEqual(r.screen(), 'test')

    def test_method_refused_by_the_tester(self):
        r = Rig()
        r.call(r.E, 'sample.remove', expect='rejected')                          # clamped: unload first
        r.grips(False)
        r.call(r.E, 'sample.remove')
        r.call(r.E, 'fixture.configure', **dict(rt.FIXTURE_EXAMPLE, extensometer_enabled=False))
        r.press('key_method')
        r.press('key_start')
        r.press('key_up')
        r.press('key_up')
        self.assertEqual(r.lines(), ['MODE', 'STRAIN'])
        self.assertFalse(r.press('key_start'))                                   # strain control without the extensometer: refused
        self.assertIsNone(r.h.method)
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'EXTENSOMETER_REQUIRED')

    def test_file_menu(self):
        r = Rig(method=False)
        self.assertTrue(r.press('key_file'))
        self.assertEqual((r.lines(), r.lamps()), (['FILE', '>LOAD FILE        01'], ['file', 'real']))
        self.assertFalse(r.file('load', 7))                                      # empty file: refused, nothing loaded
        self.assertIsNone(r.h.method)
        self.assertFalse(r.file('save', 2))                                      # no method yet
        r.set_speed(12.5)
        self.assertTrue(r.file('save', 20))
        self.assertEqual(r.val('method.files')[19], 'speed 12.5 mm/min')
        r.press('key_file')
        self.assertEqual(r.lines()[1], '>LOAD FILE        20')
        r.press('key_test')
        r.set_speed(40.0)
        self.assertTrue(r.file('load', 20))
        self.assertAlmostEqual(r.h.method['speed_m_s'], 12.5 * MM_MIN)
        self.assertTrue(r.press('key_start'))
        r.step(0.5)
        self.assertFalse(r.file('save', 3))                                      # busy during the test
        self.assertEqual(r.val('method.files')[2], None)
        self.assertTrue(r.press('key_stop'))
        self.assertEqual((r.val('acquisition.phase'), r.h.terminal_reason), ('cancelled', 'USER_CANCEL'))

    def test_system_menu_not_documented(self):
        r = Rig()
        self.assertFalse(r.press('key_system'))                                  # not documented: the screen only names it
        self.assertEqual((r.screen(), r.lines(), r.lamps()), ('system', ['SYSTEM', 'NOT DOCUMENTED'], ['real', 'system']))
        self.assertIn('SYSTEM menu', r.frame()['ui']['unsupported'])
        before = r.val('method.configured'), r.val('crosshead.target_m')
        for key in ('key_start', 'key_up', 'key_down', 'key_zero_force'):
            r.press(key)
        self.assertEqual((r.val('method.configured'), r.val('crosshead.target_m')), before)
        self.assertTrue(r.press('key_test'))

    def test_stop_halts_a_return(self):
        r = Rig(sample=False)
        r.press('key_up', hold=30.0, held=['key_high'])
        r.step(30.0)
        r.call(r.A, 'acquisition.start', expect='rejected')
        r.h.return_origin = 0.0
        self.assertTrue(r.press('key_return'))
        r.step(0.3)
        self.assertTrue(r.press('key_stop'))
        here = r.val('crosshead.position_m')
        self.assertGreater(here, 0.0)
        r.step(2.0)
        self.assertEqual(r.val('crosshead.position_m'), here)


class Refusals(unittest.TestCase):
    def test_refused_keys_and_arguments(self):
        r = Rig(grips=False)
        self.assertFalse(r.press('key_start'))                                   # grips open
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'GRIPS_NOT_CLOSED')
        self.assertFalse(r.press('key_up', hold=2.0))                            # a loose specimen: the crosshead does not move
        self.assertEqual(r.val('acquisition.phase'), 'idle')
        for args in (dict(input_id='key_nope'), dict(input_id='key_start', duration_s=-1.0), dict(input_id='power_switch', position='up')):
            cap = 'panel.switch' if 'position' in args else 'panel.press'
            self.assertEqual(r.call(r.A, cap, expect='rejected', **args)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        r.switch('off')
        self.assertFalse(r.press('key_start'))
        self.assertTrue(r.press('key_emergency'))                                # the emergency stop acts with the power off
        self.assertTrue(r.val('emergency.engaged'))
        r.call(r.A, 'method.save_file', file=21, expect='rejected')


class CallerRules(unittest.TestCase):
    """1.1.1 (sync audit, rule 2): a panel gesture acts as the gesturing session's caller; the run-owner guards (stop / result / power off
    while running) live in the runtime, so a second agent's STOP key or power switch is refused exactly as its direct calls are."""

    def test_foreign_panel_stop_and_power_off_refused(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        self.assertTrue(r.press('key_start'))
        op = r.h.operation_id
        self.assertEqual(r.h._owners[op], 'agent')
        r.step(0.5)
        before = r.h.read('debug')
        for cap, args in (('panel.press', dict(input_id='key_stop', duration_s=0.1)),):
            self.assertFalse(r.call(B, cap, **args)['accepted'], cap)
            self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'ACCESS_DENIED', cap)
            self.assertEqual(r.frame()['ui']['message']['text'], 'RUN OF ANOTHER USER', cap)   # the refusal shows on the VFD (sweep)
        for cap, args in (('acquisition.stop', {}),):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h.read('debug'), before)                              # state unchanged
        self.assertEqual((r.val('acquisition.phase'), r.val('power.enabled')), ('running', True))
        self.assertIsNone(r.h._caller)
        self.assertEqual(r.A.poll(op)['status'], 'running')                       # the owner polls and cancels its panel-started test
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        self.assertEqual(r.A.cancel(op, 'cancel-1')['status'], 'succeeded')
        self.assertEqual((r.val('acquisition.phase'), r.h.terminal_reason), ('cancelled', 'USER_CANCEL'))

    def test_power_switch_is_a_hard_switch(self):
        """Sweep 2026-10-19: the power switch cuts the power on the real tester; another agent's switch-off is not refused, the test ends as on a
        power loss."""
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        self.assertTrue(r.press('key_start'))
        op = r.h.operation_id
        r.step(0.5)
        self.assertTrue(r.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])
        self.assertEqual((r.val('acquisition.phase'), r.val('power.enabled'), r.h.terminal_reason), ('cancelled', False, 'POWER_OFF'))
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')

    def test_own_panel_stop_and_switch_off(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        self.assertTrue(r.call(B, 'panel.press', input_id='key_start', duration_s=0.1)['accepted'])
        op = r.h.operation_id
        self.assertEqual(r.h._owners[op], 'agent_b')
        r.step(0.3)
        self.assertFalse(r.press('key_stop'))                                     # agent A: not its test
        self.assertEqual(B.poll(op)['status'], 'running')
        self.assertTrue(r.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])
        self.assertEqual((r.val('acquisition.phase'), r.h.terminal_reason, r.val('power.enabled')), ('cancelled', 'POWER_OFF', False))
        self.assertEqual(B.poll(op)['status'], 'cancelled')


class StopForceOnReading(unittest.TestCase):
    """1.1.1 (sync audit, rule 1): the METHOD stop force set on the panel acts on the tester's own reading (here a load cell reading 20 % high)."""

    def test_panel_stop_force_on_the_reading(self):
        r = Rig(power=False, plant=dict(force_gain=1.2))
        r.switch('on')
        r.press('key_zero_force')
        r.call(r.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, force_stop_n=300.0))
        self.assertTrue(r.press('key_start'))
        r.step(3.0)
        self.assertEqual(r.h.terminal_reason, 'METHOD_FORCE_STOP')
        self.assertAlmostEqual(r.val('force.indicated_n'), 300.0, delta=0.011)
        self.assertAlmostEqual(r.val('truth.force_n'), 250.0, delta=0.05)
        self.assertEqual(r.lines()[0], fline('FORCE', 300.0))
        self.assertFalse(r.press('key_start'))                                    # START refused: the reading is at the stop force
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'METHOD_FORCE_STOP')


class Equivalence(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(power=False), Rig(power=False)
        a.switch('on')
        a.press('key_zero_force')
        a.set_speed(60.0)
        a.file('save', 4)
        a.press('key_zero_stroke')
        a.press('key_start')
        b.call(b.A, 'power.set', enabled=True)
        b.call(b.A, 'force.zero')
        b.call(b.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, speed_m_s=60.0 / 60000.0))
        b.call(b.A, 'method.save_file', file=4)
        b.call(b.A, 'display.zero_stroke')
        b.call(b.A, 'acquisition.start')
        for r in (a, b):
            r.step(5.0)
        sa, sb = a.h.read('evaluator'), b.h.read('evaluator')
        self.assertEqual(sa, sb)
        self.assertEqual(a.h.method, b.h.method)


class Rendering(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        def run():
            r = Rig()
            r.press('key_method')
            r.to('speed')
            cap = r.call(r.A, 'panel.capture', display_id='vfd')
            return cap, Path(cap['image']['path']).read_bytes()
        (c1, p1), (_, p2) = run(), run()
        self.assertEqual(hashlib.sha256(p1).hexdigest(), hashlib.sha256(p2).hexdigest())
        self.assertEqual(hashlib.sha256(p1).hexdigest(), c1['image']['sha256'])
        self.assertEqual((c1['image']['width'], c1['image']['height']), (300, 64))
        r = Rig(power=False)
        caps = r.A.describe(instance_id=INST)['capabilities']
        self.assertIn('panel.press', caps)
        self.assertNotIn('panel.frame', caps)

    def test_evidence_renders(self):
        """home (2-line force / stroke during a test) / menu (METHOD, test speed) / error (emergency stop), deterministic, with renders.json."""
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        panel = r.h.panel
        r.press('key_start')
        r.step(2.0)                                                                # 1 mm of the 1.26 mm to fracture
        self.assertEqual(r.val('acquisition.phase'), 'running')
        r.lines()
        shots = {'home': panel.save_png('vfd', OUT / 'home.png')}
        r.press('key_stop')
        r.press('key_method')
        r.to('speed')
        r.lines()
        shots['menu'] = panel.save_png('vfd', OUT / 'menu.png')
        r.press('key_test')
        r.press('key_emergency')
        r.lines()
        shots['error'] = panel.save_png('vfd', OUT / 'error.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_shimadzu_ags10knxd_contract_metadata()
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for name, cap in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'errors'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        spec = rt.PANEL_SPEC
        for k, v in list(spec['inputs'].items()) + list(spec['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        for k in spec['displays']:
            if k.startswith('led_'):
                self.assertEqual(c['components'][k]['geometry_status'], 'absent', k)
        self.assertEqual(set(pu.spec_capabilities(spec)) - set(c['capabilities']), set())
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version'], ext['approximate']), ('panel_ui', 'text_menu', '0.1', True))
        self.assertTrue(spec['approximate'])
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        from asset_tool import validate_contract
        self.assertEqual(validate_contract(c, ROOT, check_files=False), [])


class ZKeyCoverage(unittest.TestCase):
    def test_every_menu_function_ran(self):
        if len(KEY_SEEN) > 10:
            self.assertEqual(set(rt.KEY_CAPS) - KEY_SEEN, set())


if __name__ == '__main__':
    unittest.main()
