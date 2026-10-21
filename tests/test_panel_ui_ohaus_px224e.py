"""Front-panel tests for the OHAUS Pioneer PX224/E (assets/PX224E/panel_ui.json + scripts/asset_runtime/_panel_ui.py over
scripts/asset_runtime/ohaus_balance.py): python3 -m pytest tests/test_panel_ui_ohaus_px224e.py -q

Every key goes through the session API (panel.press / panel.turn / panel.switch); objects on the pan, doors and the adapter are robot skills,
the host clock is sim.step.  Each screen is checked twice: the structured frame (panel.frame) and the rendered pixels, read back with a
seven-segment reader and a 5x7 dot reader that know only the display geometry of the spec.  Themes: the seven key procedures of the menu
survey; panel = direct capability; refusals and locks (lockout, password, approved mode and the security switch, unstable readings,
power-on zero range, overload, pan); time-outs (messages, auto dim, auto off, calibration timeout); the firmware functions behind the menus
(calibration correcting sensor errors, applications, printing, units, GLP texts, levelling); render determinism and the evidence PNGs."""
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import ohaus_balance as ob  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

MODEL = 'PX224E'
INST = 'ohaus_px224e_01'
SPEC = ob.PANEL_SPEC[MODEL]
LCD = SPEC['displays']['lcd']
OUT = ROOT / 'outputs/panel_ui/ohaus_px224e'
SURVEY_TASKS = ['zero and tare a container', 'change the weighing unit', 'span calibration (PX224/E ExCal)', 'set filter level', 'enable batch printing',
                'parts counting', 'set a password']
DONE_TASKS = set()
YES, NO, BACK, EXIT = 'key_zero', 'key_print', 'key_function', 'key_tare'
_DIGITS = {v: k for k, v in pu.SEG7.items() if k in '0123456789'}
_LETTERS = {v: k for k, v in pu.SEG7.items() if k not in '0123456789' and v}
_GLYPHS = {}
for _c, _cols in pu.FONT5X7.items():
    _GLYPHS.setdefault(bytes(_cols), _c)


def _lit(img, x, y, bg):
    px = img[int(round(y)), int(round(x))].astype(int).sum()
    return px < 0.5 * bg


def read_seven(img, f, bg):
    n, cw, ch, gap = f['cells'], f['cell_w'], f['cell_h'], f.get('gap', 6)
    t = f.get('thickness', max(2.0, cw * 0.18))
    out = ''
    for i in range(n):
        x = f['x'] + i * (cw + gap)
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2),
                   g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if _lit(img, px, py, bg))
        out += _DIGITS.get(segs, _LETTERS.get(segs, ' ' if not segs else '?'))
        r = max(1.5, t * 0.6)
        if _lit(img, x + cw + gap / 2.0, f['y'] + ch - r, bg):
            out += '.'
    return out.strip()


def read_dots(img, f, bg):
    s = f.get('scale', 2)
    out = ''
    for i in range(f['cells']):
        cols = []
        for cx in range(5):
            bits = 0
            for cy in range(8):
                if _lit(img, f['x'] + (i * 6 + cx) * s + s // 2, f['y'] + cy * s + s // 2, bg):
                    bits |= 1 << cy
            cols.append(bits)
        out += _GLYPHS.get(bytes(cols), '?')
    return out.rstrip()


def seg_norm(text):
    return tuple(ch if ch in '.' else pu.SEG7.get(ch, pu.SEG7.get(ch.upper(), pu.SEG7.get(ch.lower(), ''))) or '_' for ch in text)


class Rig:
    def __init__(self, on=True, instance=INST, warm_s=10.0):
        self.h = ob.OhausBalanceRuntime(MODEL, instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.inst = instance
        if on:
            self.call(self.A, 'power.connect_adapter', connected=True)
            assert self.press(YES)
            self.step(warm_s)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            ob.validate(ob.ALL_CAPS[MODEL][cap]['output_schema'], r['result'])
            assert cap == 'sim.step' or self.h.t == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def hold(self, key):
        return self.press(key, 2.5)

    def keys(self, *ks):
        for k in ks:
            assert self.press(k), (k, self.lcd())

    def load(self, grams, settle=5.0):
        self.call(self.A, 'pan.set_load', mass_kg=grams / 1000.0)
        self.step(settle)

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lcd(self):
        """Fields shown now: frame and pixels must agree (main by seven segments, unit / message by 5x7 dots)."""
        fr = self.frame()['displays']['lcd']
        img = self.h.panel.render('lcd')
        bg = int(img[0, -1].astype(int).sum())
        shown = {k: v['shown'].strip() for k, v in fr['fields'].items()}
        read = dict(main=read_seven(img, LCD['fields']['main'], bg), unit=read_dots(img, LCD['fields']['unit'], bg).strip(),
                    msg=read_dots(img, LCD['fields']['msg'], bg).strip())
        assert seg_norm(shown['main']) == seg_norm(read['main']), (shown, read)
        assert shown['unit'] == read['unit'] and shown['msg'] == read['msg'], (shown, read)
        return shown

    def icons(self):
        fr = self.frame()['displays']['lcd']
        return {k for k, v in fr['icons'].items() if v['shown']}

    def screen(self):
        return self.frame()['screen']

    def state(self, *ids):
        o = self.E.read(instance_id=self.inst, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def item(self):
        return (self.frame()['ui']['menu'] or {}).get('item')

    def menu_to(self, *path):
        """Hold Tare / Menu-Cal, then No to each sub-menu / item and Yes to enter it (the last one is not entered)."""
        if self.screen() != 'menu':
            assert self.hold(EXIT)
        for i, target in enumerate(path):
            for _ in range(60):
                if self.item() == target:
                    break
                assert self.press(NO)
            assert self.item() == target, (target, self.lcd())
            if i < len(path) - 1:
                assert self.press(YES)

    def choose(self, value_text):
        """Yes opens the value, No steps to the wanted one, Yes accepts it."""
        assert self.press(YES)
        for _ in range(40):
            if self.frame()['ui']['editor'] and self.lcd()['msg'].endswith(value_text) or self._buffer_text() == value_text:
                break
            assert self.press(NO)
        assert self._buffer_text() == value_text, (value_text, self.lcd())
        assert self.press(YES)

    def _buffer_text(self):
        ed = self.frame()['ui']['editor']
        if not ed:
            return None
        it = self.h.panel._current_field(self.h.panel.ui['editor'])
        return self.h.panel._field_text(it, ed['buffer'][ed['field']])


def runtime_state(h):
    r = h.read('evaluator')
    for k in ('clock.time_s', 'clock.datetime', 'display.message'):
        r.pop(k, None)
    return json.dumps(dict(read=r, s=h.s, app=h.appstate, unit=h.unit, zero=h.zero_kg, tare=h.tare_kg, cal=h.cal), sort_keys=True, default=str)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_zero_and_tare_a_container(self):
        g = Rig(on=False)
        g.call(g.A, 'power.connect_adapter', connected=True)
        self.assertEqual(g.screen(), 'off')
        self.assertTrue(g.press(YES))                                                # On/Zero turns it on (p.10)
        g.step(10)
        self.assertEqual(g.lcd()['main'], '0.0000')
        self.assertIn('zero', g.icons())                                             # >0<
        g.load(0.3)                                                                  # drift on the empty pan, then Zero
        g.keys(YES)
        self.assertEqual(g.lcd()['main'], '0.0000')
        g.load(20.3)                                                                 # an empty container (20 g) on the pan
        self.assertEqual(g.lcd()['main'], '20.0000')
        g.keys(EXIT)                                                                 # Tare (p.11)
        self.assertEqual(g.lcd()['main'], '0.0000')
        self.assertIn('net', g.icons())
        g.load(32.6456)                                                              # add material: net weight
        self.assertEqual(g.lcd()['main'], '12.3456')
        g.load(0.3)                                                                  # everything removed: negative tare
        self.assertEqual(g.lcd()['main'], '20.0000')
        self.assertIn('negative', g.icons())
        g.keys(EXIT)                                                                 # Tare clears it
        self.assertEqual(g.lcd()['main'], '0.0000')
        self.assertNotIn('net', g.icons())
        DONE_TASKS.add('zero and tare a container')

    def test_change_the_weighing_unit(self):
        g = Rig()
        g.load(12.3456)
        self.assertTrue(g.hold(NO))                                                  # hold Print / Unit: the Unit menu (p.34)
        self.assertEqual(g.screen(), 'units_select')
        self.assertEqual(g.lcd()['msg'], 'Unit Gram')
        g.keys(NO, NO, NO)                                                           # kg, mg, ct
        self.assertEqual(g.lcd()['msg'], 'Unit Carat')
        g.keys(YES)
        self.assertEqual(g.state('weighing.unit'), 'ct')
        self.assertEqual(g.lcd()['main'], '61.7280')
        self.assertEqual(g.lcd()['unit'], 'ct')
        DONE_TASKS.add('change the weighing unit')

    def test_span_calibration(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', gain=0.0004)                           # the load cell reads 0.04 % high
        g.load(100.0)
        self.assertEqual(g.lcd()['main'], '100.0400')
        g.load(0.0)
        g.menu_to('m_calibration', 'cal_span')                                       # hold Menu-Cal, Yes, No until Span Cal (p.28)
        self.assertEqual(g.lcd()['msg'], 'Span Cal')
        g.keys(YES)
        self.assertEqual(g.screen(), 'calibration')
        g.step(5)                                                                    # zero captured
        self.assertEqual(g.lcd(), dict(main='100.0000', unit='g', msg='Place weights'))
        g.keys(BACK)                                                                 # Function: the other span point
        self.assertEqual(g.lcd()['main'], '200.0000')
        g.keys(BACK)
        g.load(100.0)
        self.assertEqual(g.lcd()['msg'], 'Calibration done')
        g.load(0.0)
        g.keys(NO)                                                                   # any key returns
        self.assertEqual(g.screen(), 'home')
        g.load(150.0)
        self.assertEqual(g.lcd()['main'], '150.0000')                                # the stored gain corrects the reading
        self.assertTrue(g.state('calibration.state')['calibrated'])
        DONE_TASKS.add('span calibration (PX224/E ExCal)')

    def test_set_filter_level(self):
        g = Rig()
        g.menu_to('m_setup', 'setup_filter')
        self.assertEqual(g.lcd()['msg'], 'Filter Level Medium')
        g.choose('High')
        self.assertEqual(g.state('settings')['filter'], 'high')
        g.keys(EXIT)
        self.assertEqual(g.screen(), 'home')
        g.call(g.A, 'pan.set_load', mass_kg=0.05)
        g.step(4)
        self.assertFalse(g.state('weighing.stable'))                                # High: slower stabilisation (6 s instead of 3 s)
        g.step(3)
        self.assertTrue(g.state('weighing.stable'))
        DONE_TASKS.add('set filter level')

    def test_enable_batch_printing(self):
        g = Rig()
        g.call(g.A, 'interface.connect', device='printer')
        g.menu_to('m_print', 'pr_batch')                                             # No until Print, Yes, No until Batch Printing (p.12)
        g.choose('On')
        g.keys(EXIT)
        self.assertTrue(g.state('settings')['batch_printing'])
        g.hold(BACK)
        g.keys(YES)                                                                  # re-enter Weighing
        self.assertEqual(g.lcd()['msg'], 'Place sample 1')
        for i, grams in enumerate((5.2403, 6.2404, 7.0)):
            g.load(grams)
            g.keys(BACK)                                                             # Function stores (p.13)
        self.assertEqual(g.lcd()['msg'], 'Place sample 4')
        g.keys(NO)                                                                   # Print ends and prints
        lines = [x['line'] for x in g.call(g.A, 'events.read')['output']['lines']]
        self.assertIn('---- Sample Data (g) -----', lines)
        self.assertIn('2: 6.2404 g', lines)
        DONE_TASKS.add('enable batch printing')

    def test_parts_counting(self):
        g = Rig()
        g.hold(BACK)                                                                 # hold Function / Mode (p.13)
        g.keys(NO)
        self.assertEqual(g.lcd()['msg'], 'Parts Counting')
        g.keys(YES)
        self.assertEqual(g.lcd()['msg'], 'Clear APW?')
        g.keys(YES)
        self.assertEqual(g.frame()['displays']['lcd']['fields']['msg']['text'], 'Sample size 10')
        g.keys(NO, NO, BACK)                                                         # No / Back increase / decrease
        self.assertEqual(g.frame()['displays']['lcd']['fields']['msg']['text'], 'Sample size 11')
        g.keys(BACK, YES)
        self.assertEqual(g.lcd()['msg'], 'Place 10 samples')
        g.load(10 * 0.4321)
        g.keys(BACK)                                                                 # Function establishes the APW
        self.assertEqual(g.lcd(), dict(main='10', unit='PCS', msg='Parts Counting'))
        g.load(37 * 0.4321)
        self.assertEqual(g.lcd()['main'], '37')
        g.keys(BACK)                                                                 # view the piece weight
        self.assertEqual(g.lcd()['main'], '0.4321')
        DONE_TASKS.add('parts counting')

    def test_set_a_password(self):
        g = Rig()
        g.menu_to('m_setup', 'setup_password')
        g.keys(YES)
        self.assertEqual(g.screen(), 'password_set')
        g.keys(YES, NO, BACK)                                                        # 1, 3, 2 (Zero = 1, Print = 3, Function = 2; p.30)
        self.assertTrue(g.lcd()['msg'].startswith('Password 132'))
        g.keys(EXIT)
        self.assertEqual(g.lcd()['msg'], 'Do you confirm?')
        g.keys(YES)
        self.assertEqual(g.state('settings')['password'], '132')
        g.keys(EXIT)
        g.hold(YES)                                                                  # off and on: the password is asked again
        g.press(YES)
        g.step(5)
        g.hold(EXIT)
        self.assertEqual(g.screen(), 'password_entry')
        g.keys(YES, EXIT)                                                            # wrong password: protected menus locked
        self.assertEqual(g.screen(), 'menu')
        g.menu_to('m_setup', 'setup_filter')
        g.keys(YES, NO)
        self.assertFalse(g.press(YES))
        self.assertEqual(g.lcd()['msg'], 'Menu is locked')
        self.assertEqual(g.state('settings')['filter'], 'medium')
        DONE_TASKS.add('set a password')


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def test_panel_and_direct_calls_give_the_same_state(self):
        a, b = Rig(), Rig()
        a.menu_to('m_setup', 'setup_auto_tare')
        a.choose('On')
        a.keys(NO)
        a.choose('10 Divisions')
        a.keys(EXIT)
        a.hold(NO)
        a.keys(NO, NO, YES)
        b.call(b.A, 'settings.configure', auto_tare=True)
        b.call(b.A, 'settings.configure', graduations=10)
        b.call(b.A, 'weighing.set_unit', unit='mg')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


# ---------------------------------------------------------------- refusals, locks, errors
class Refusals(unittest.TestCase):
    def test_lockout_and_security_switch(self):
        g = Rig()
        g.menu_to('m_lockout', 'lock_units')
        g.choose('On')
        g.keys(EXIT)
        g.menu_to('m_units', 'unit_mg')
        g.keys(YES, NO)
        self.assertFalse(g.press(YES))                                               # locked sub-menu (p.45)
        self.assertEqual(g.lcd()['msg'], 'Menu is locked')
        g.keys(EXIT)
        self.assertTrue(g.call(g.A, 'panel.switch', input_id='security_switch', position='locked')['accepted'])
        g.menu_to('m_lockout', 'lock_units')
        g.keys(YES, NO)
        self.assertFalse(g.press(YES))                                               # Lock menu read-only with the switch locked (p.40)
        self.assertTrue(g.state('settings')['lockout']['units'])

    def test_approved_mode_needs_the_switch(self):
        g = Rig()
        g.menu_to('m_setup', 'setup_approved')
        g.keys(YES, NO)
        self.assertFalse(g.press(YES))                                               # switch unlocked: cannot turn it on (p.33)
        self.assertEqual(g.lcd()['msg'], 'Set security switch')
        g.keys(EXIT)
        g.call(g.A, 'panel.switch', input_id='security_switch', position='locked')
        g.menu_to('m_setup', 'setup_approved')
        g.choose('On')
        self.assertTrue(g.state('settings')['approved'])
        ids = [it['id'] for it in SPEC['menus']['main']['items'][1]['items']]
        g.keys(EXIT)
        g.hold(EXIT)
        visible = []
        for _ in range(12):
            visible.append(g.item())
            g.keys(NO)
        self.assertNotIn('m_lockout', visible)                                       # Lockout hidden in approved mode (p.33)
        self.assertIn('setup_filter', ids)
        g.menu_to('m_setup', 'setup_filter')
        g.keys(YES, NO)
        self.assertFalse(g.press(YES))                                               # filter locked
        self.assertEqual(g.lcd(), dict(main='--------', unit='', msg='Menu is locked'))
        self.assertNotIn('setup_graduations', [g.item() for _ in range(16) if g.press(NO)])   # graduations hidden
        self.assertEqual(g.call(g.A, 'calibration.start', expect='rejected', kind='span')['error']['code'], 'SECURITY_LOCKED')   # sealed

    def test_power_on_zero_range_overload_and_pan(self):
        g = Rig(on=False)
        g.call(g.A, 'power.connect_adapter', connected=True)
        g.call(g.A, 'pan.set_load', mass_kg=0.05)                                    # 50 g on the pan at power-on (> 10 %)
        g.press(YES)
        g.step(5)
        self.assertEqual(g.lcd(), dict(main='--------', unit='', msg='Over init. range'))
        self.assertFalse(g.press(EXIT))
        g.load(0.0)
        self.assertEqual(g.lcd()['main'], '0.0000')
        g.load(230.0)
        self.assertEqual(g.lcd(), dict(main='Error 8.3', unit='', msg='Over load'))
        g.load(0.0)
        g.hold(YES)
        g.call(g.A, 'pan.remove')
        g.press(YES)
        g.step(3)
        self.assertEqual(g.lcd()['msg'], 'Under init. range')

    def test_unstable_keys_and_doors(self):
        g = Rig()
        g.call(g.A, 'doors.set', open_fraction=0.5)
        g.load(10.0)
        self.assertNotIn('stable', g.icons())
        self.assertFalse(g.press(EXIT))                                              # tare needs a stable reading
        g.call(g.A, 'doors.set', open_fraction=0.0)
        g.step(4)
        self.assertTrue(g.press(EXIT))

    def test_counting_and_percent_reference_too_small(self):
        g = Rig()
        g.hold(BACK)
        g.keys(NO, YES, YES, YES)
        g.load(0.0001)
        self.assertFalse(g.press(BACK))
        self.assertEqual(g.lcd()['msg'], 'Low ref. weight')                          # APW < 0.1 d (p.45)
        g.hold(BACK)
        g.keys(NO, NO, YES, YES)
        g.load(0.005)
        self.assertFalse(g.press(BACK))
        self.assertEqual(g.lcd()['msg'], 'Invalid')                                  # reference < 100 d


# ---------------------------------------------------------------- time-outs and display
class TimeAndDisplay(unittest.TestCase):
    def test_auto_dim_auto_off_capacity_bar_and_levelling(self):
        g = Rig()
        g.call(g.A, 'settings.configure', auto_dim='10', capacity_bar=True, auto_off='30')
        g.load(110.0)
        bar = g.frame()['displays']['lcd']['icons']['bar']
        self.assertTrue(bar['shown'])
        self.assertAlmostEqual(bar['fraction'], 0.5, places=3)
        self.assertEqual(g.frame()['displays']['lcd']['brightness'], 100)
        g.step(600)
        self.assertEqual(g.frame()['displays']['lcd']['brightness'], 33)            # dimmed after 10 min without motion
        g.load(110.5, 1)
        self.assertEqual(g.frame()['displays']['lcd']['brightness'], 100)
        g.step(1800)
        self.assertEqual(g.screen(), 'off')                                          # Auto Off after 30 min
        g.press(YES)
        g.call(g.E, 'environment.set', tilt_rad=0.006)                               # bench slope: the bubble is off centre, no stable reading
        g.step(5)
        self.assertFalse(g.state('level.bubble_centered'))
        self.assertNotIn('stable', g.icons())
        self.assertTrue(g.call(g.A, 'panel.turn', input_id='foot_front_right', detents=8)['accepted'])
        self.assertTrue(g.call(g.A, 'panel.turn', input_id='foot_rear_right', detents=8)['accepted'])
        g.step(5)
        self.assertTrue(g.state('level.bubble_centered'))
        self.assertIn('stable', g.icons())

    def test_calibration_timeout_and_fail(self):
        g = Rig()
        g.call(g.A, 'calibration.start', kind='span')
        g.step(2)
        g.call(g.E, 'environment.set', vibration=True)
        g.step(61)
        self.assertEqual(g.lcd()['msg'], 'Timeout')
        g.keys(NO)
        g.call(g.E, 'environment.set', vibration=False)
        g.call(g.A, 'calibration.start', kind='span')
        g.step(5)
        g.load(50.0)                                                                 # wrong mass for the 100 g point
        self.assertEqual(g.lcd()['msg'], 'Calibration fail')


# ---------------------------------------------------------------- applications and firmware functions
class Functions(unittest.TestCase):
    def test_linearity_calibration_removes_nonlinearity(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', nonlinearity=0.00001, gain=-0.0002)
        g.load(100.0)
        self.assertNotEqual(g.lcd()['main'], '100.0000')
        g.load(0.0)
        g.menu_to('m_calibration', 'cal_lin')
        g.keys(YES)
        g.step(5)
        self.assertEqual(g.lcd()['main'], '100.0000')
        g.load(100.0)
        self.assertEqual(g.lcd()['main'], '200.0000')
        g.load(200.0)
        self.assertEqual(g.lcd()['msg'], 'Linearity done')
        g.keys(NO)
        for grams in (50.0, 100.0, 200.0):
            g.load(grams)                                                            # exact at the points, the parabola's residual between them
            self.assertAlmostEqual(float(g.lcd()['main']), grams, delta=0.0006 if grams == 50.0 else 0.00011)

    def test_percent_dynamic_density_formulation(self):
        g = Rig()
        g.hold(BACK)
        g.keys(NO, NO, YES, YES)                                                     # Percent, Clear reference? Yes
        self.assertEqual(g.lcd()['msg'], 'Place sample')
        g.load(80.0)
        g.keys(BACK)
        self.assertEqual(g.lcd()['main'], '100.00')
        g.load(20.0)
        self.assertEqual(g.lcd(), dict(main='25.00', unit='%', msg='Percent Weighing'))
        g.load(0.0)
        g.hold(BACK)
        g.keys(NO, NO, NO, YES, YES)                                                 # Dynamic, Change parameter? Yes
        g.keys(BACK, BACK, BACK, BACK, BACK, YES)                                    # 5 s
        self.assertEqual(g.state('app.dynamic')['time_s'], 5)
        self.assertEqual(g.lcd()['msg'], 'Ready')
        g.call(g.A, 'pan.set_load', mass_kg=0.05)
        g.step(2)
        self.assertTrue(g.lcd()['msg'].startswith('Time left'))
        g.step(4)
        self.assertEqual(g.lcd(), dict(main='50.0000', unit='g', msg='Final weight'))
        g.load(0.0)
        self.assertEqual(g.lcd()['msg'], 'Ready')
        g.hold(BACK)
        g.keys(NO, NO, NO, NO, YES, NO)                                              # Density, keep the parameters (solid, water 20 C)
        self.assertEqual(g.lcd()['msg'], 'Weight in air')
        g.load(27.0)
        g.keys(BACK)
        self.assertEqual(g.lcd()['msg'], 'Weight in liquid')
        g.load(17.0)
        g.keys(BACK)
        rho = 27.0 / 10.0 * (ob.water_density(20.0) - 0.0012) + 0.0012
        self.assertEqual(g.lcd()['main'], f'{rho:.4f}')
        self.assertEqual(g.lcd()['msg'], 'Density')
        g.load(0.0)
        g.hold(BACK)
        g.keys(NO, NO, NO, NO, NO, YES)                                              # Formulation
        self.assertEqual(g.lcd()['msg'], 'Place component 1')
        g.load(10.0)
        g.keys(BACK, EXIT)                                                           # store, tare
        g.load(15.0)
        g.keys(BACK, NO)                                                             # store, no more components
        self.assertEqual(g.lcd()['msg'], 'Add filler?')
        g.keys(NO)
        self.assertEqual(g.lcd(), dict(main='15.0000', unit='g', msg='Total weight'))
        g.keys(BACK, BACK)
        self.assertEqual(g.lcd()['msg'], 'Component 2')
        self.assertEqual(g.lcd()['main'], '5.0000')

    def test_print_settings_glp_and_custom_unit(self):
        g = Rig()
        g.call(g.A, 'interface.connect', device='pc')
        g.call(g.A, 'settings.configure', print_to='pc')                             # Print To PC (factory default Printer, p.36)
        g.menu_to('m_glp', 'glp_user')
        g.keys(YES)
        for _ in range(3):
            g.keys(NO)                                                               # ' ' -> C
        g.hold(YES)                                                                  # hold Yes stores the text
        self.assertEqual(g.state('settings')['user_name_text'], 'C')
        g.keys(EXIT)
        g.load(49.98)
        g.keys(NO)                                                                   # Print
        lines = [x['line'] for x in g.call(g.A, 'events.read')['output']['lines']]
        self.assertIn('User Name: C', lines)
        self.assertIn('Header 1', lines)
        self.assertTrue(any(ln.startswith('Gross:') and '49.9800' in ln for ln in lines))
        g.call(g.A, 'settings.configure', units={'C1': True}, custom_factor=0.5643834, custom_exponent=0, custom_lsd=1)
        g.call(g.A, 'weighing.set_unit', unit='C1')
        self.assertEqual(g.lcd()['main'], '28.20788')                                # LSD 1 on the decade of 1 d in the custom unit

    def test_auto_tare_azt_and_auto_print(self):
        g = Rig()
        g.call(g.A, 'settings.configure', auto_tare=True, auto_print='on_stability', print_to='pc')
        g.call(g.A, 'interface.connect', device='pc')
        self.assertEqual(g.lcd()['msg'], 'Place container')
        g.load(20.0)
        self.assertEqual(g.lcd()['main'], '0.0000')                                  # the first stable load is tared
        self.assertGreater(g.state('weighing.tare_kg'), 0.019)
        n0 = g.state('interface.lines')
        g.load(25.0)
        self.assertGreater(g.state('interface.lines'), n0)                           # printed on stability
        g.load(0.0)
        g.load(0.0)
        self.assertEqual(g.state('weighing.tare_kg'), 0.0)                           # pan cleared: auto tare re-armed
        g.call(g.A, 'settings.configure', auto_tare=False, azt='1')
        g.load(0.00003, 2)                                                           # slow drift at zero is tracked
        g.step(5)
        self.assertEqual(g.lcd()['main'], '0.0000')

    def test_date_time_factory_reset_rs232(self):
        g = Rig()
        g.menu_to('m_setup', 'setup_time')
        g.keys(YES)
        for _ in range(6):
            g.keys(NO)                                                               # hour 8 -> 14
        g.keys(YES, YES, YES)
        self.assertTrue(g.state('clock.datetime').endswith('T14:00:00'))
        g.keys(EXIT)
        g.menu_to('m_rs232', 'rs_baud')
        g.choose('19200')
        self.assertEqual(g.state('settings')['baud'], 19200)
        g.keys(EXIT)
        g.menu_to('m_factory', 'factory_reset_all')
        g.keys(YES)
        self.assertEqual(g.state('settings')['baud'], 9600)
        self.assertEqual(g.screen(), 'home')


class DirectCommands(unittest.TestCase):
    def test_direct_application_commands_and_density_parameters(self):
        g = Rig()
        g.call(g.E, 'plant.configure', g_local=9.79)                                 # another site: readings off until calibrated
        g.load(100.0)
        self.assertNotEqual(g.lcd()['main'], '100.0000')
        self.assertAlmostEqual(g.call(g.E, 'truth.inspect')['process']['load_kg'], 0.1)
        g.call(g.A, 'app.select', app='counting')
        g.call(g.A, 'counting.set_sample_size', size=20)
        g.call(g.A, 'counting.establish')
        self.assertEqual(g.lcd()['main'], '20')
        g.call(g.A, 'app.select', app='percent')
        g.call(g.A, 'percent.establish')
        self.assertEqual(g.lcd()['main'], '100.00')
        g.call(g.A, 'settings.configure', batch_printing=True, print_to='pc')
        g.call(g.A, 'interface.connect', device='pc')
        g.call(g.A, 'app.select', app='weighing')
        g.call(g.A, 'batch.store')
        g.call(g.A, 'batch.finish')
        self.assertTrue(any(x['line'].startswith('1: ') for x in g.call(g.A, 'events.read')['output']['lines']))
        g.menu_to('m_glp', 'glp_header1')
        g.keys(YES, NO)
        g.hold(YES)
        self.assertEqual(g.state('settings')['glp_header'][0], 'Ieader 1')         # No steps the flashing first character H -> I
        g.keys(EXIT)
        g.hold(BACK)
        g.keys(NO, NO, NO, NO, YES, YES)                                             # Density, Change parameter? Yes
        self.assertEqual(g.screen(), 'density_params')
        g.keys(YES, NO, YES)                                                         # Sample Type: Liquid
        self.assertEqual(g.state('app.density')['sample_type'], 'liquid')
        g.load(0.0)
        g.call(g.A, 'power.set', on=False)
        g.call(g.A, 'pan.remove')
        g.call(g.A, 'pan.install')
        self.assertTrue(g.state('pan.installed'))


# ---------------------------------------------------------------- rendering, evidence
class SyncFixes(unittest.TestCase):
    """Sync audit 2026-10-19: Output Format / Print To / Single Header act, system errors can be injected, language kept, session describe()."""

    def printout(self, g):
        n0 = len(g.call(g.A, 'events.read')['output']['lines'])
        g.keys(NO)                                                                   # Print
        return [x['line'] for x in g.call(g.A, 'events.read')['output']['lines']][n0:]

    def test_output_format_sics_and_undocumented_formats(self):
        g = Rig()
        self.assertEqual(g.state('settings')['print_to'], 'printer')               # factory default (bold, p.36)
        g.call(g.A, 'interface.connect', device='printer')
        g.load(20.0)
        g.keys(EXIT)                                                                 # tare the container
        g.load(69.98)
        px = self.printout(g)
        self.assertTrue(any(ln.startswith('Net:') and ln.endswith('49.9800     g    N') for ln in px))
        g.menu_to('m_print', 'pr_output_format')
        g.choose('SICS')
        g.keys(EXIT)
        self.assertEqual(g.state('settings')['output_format'], 'SICS')
        sics = self.printout(g)
        self.assertIn('Header 1', sics)                                              # header and info lines stay text
        self.assertIn('S S    49.9800 g', sics)                                      # MT-SICS weight response: value right aligned in 10
        self.assertIn('TA A    20.0000 g', sics)
        self.assertFalse(any(ln.startswith(('Gross:', 'Net:', 'Tare:')) for ln in sics))
        g.call(g.A, 'pan.set_load', mass_kg=0.07)                                    # moving: dynamic status D
        g.step(0.2)
        self.assertTrue(any(ln.startswith('S D ') for ln in self.printout(g)))
        g.step(5)
        g.call(g.A, 'pan.set_load', mass_kg=0.06998)
        g.step(5)
        g.call(g.A, 'settings.configure', output_format='ST')                       # not documented in our manuals: OHAUS-PX layout
        st_lines = self.printout(g)
        self.assertEqual(st_lines[:5] + st_lines[6:], px[:5] + px[6:])               # same layout (only the time line differs)
        self.assertIn('ST', [u['item'] for u in SPEC['unsupported']][-1])

    def test_print_to_routes_the_printout(self):
        g = Rig()
        g.load(10.0)
        g.call(g.A, 'interface.connect', device='pc')
        self.assertEqual(self.printout(g), [])                                       # Print To Printer: nothing reaches the PC
        g.menu_to('m_print', 'pr_print_to')
        g.choose('PC')
        g.keys(EXIT)
        self.assertEqual(g.state('settings')['print_to'], 'pc')
        self.assertTrue(any('10.0000' in ln for ln in self.printout(g)))
        g.call(g.A, 'interface.connect', device='printer')
        self.assertEqual(self.printout(g), [])
        g.call(g.A, 'settings.configure', auto_print='continuous')                  # auto print follows the same rule
        n = g.state('interface.lines')
        g.step(3)
        self.assertEqual(g.state('interface.lines'), n)

    def test_single_header_once_a_day(self):
        g = Rig()
        g.call(g.A, 'interface.connect', device='printer')
        g.menu_to('m_print', 'pr_single_header')
        g.choose('On')
        g.keys(EXIT)
        self.assertTrue(g.state('settings')['single_header'])
        self.assertIn('Header 1', self.printout(g))                                  # the first printout of the day has the header
        second = self.printout(g)
        self.assertNotIn('Header 1', second)
        self.assertTrue(any(ln.startswith('Balance ID') for ln in second))
        g.step(86400)                                                                # next day
        self.assertIn('Header 1', self.printout(g))
        g.call(g.A, 'settings.configure', single_header=False)
        self.assertIn('Header 1', self.printout(g))
        self.assertIn('Header 1', self.printout(g))                                  # Off: every printout

    def test_system_errors_restart_and_service(self):
        g = Rig()
        g.load(10.0)
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='2.0')['error']['code'], 'ACCESS_DENIED')
        g.menu_to('m_setup', 'setup_filter')                                         # a system error shows whatever screen was open
        g.call(g.E, 'fault.inject', code='2.0')
        self.assertEqual(g.screen(), 'home')
        self.assertEqual(g.lcd(), dict(main='Error 2.0', unit='', msg='---------'))  # p.46
        self.assertEqual(g.state('status'), 'fault')
        self.assertIsNone(g.state('weighing.net_kg'))
        for k in (YES, NO, BACK, EXIT):
            self.assertFalse(g.press(k))                                             # keys do nothing
        self.assertFalse(g.hold(YES))
        self.assertEqual(g.call(g.A, 'weighing.tare', expect='rejected')['error']['code'], 'SYSTEM_ERROR')
        self.assertEqual(g.call(g.A, 'power.set', expect='rejected', on=False)['error']['code'], 'SYSTEM_ERROR')
        g.call(g.A, 'power.connect_adapter', connected=False)                       # remedy: disconnect the power and restart
        g.call(g.A, 'power.connect_adapter', connected=True)
        self.assertTrue(g.press(YES))
        g.step(10)
        self.assertEqual(g.state('status'), 'stable')
        self.assertEqual(g.lcd()['main'], '0.0000')                                  # restarted: power-on zero with the 10 g on the pan
        g.call(g.E, 'fault.inject', code='9.5', persistent=True)                    # the error persists after the restart
        g.call(g.A, 'power.connect_adapter', connected=False)
        g.call(g.A, 'power.connect_adapter', connected=True)
        g.keys(YES)
        self.assertEqual(g.lcd()['main'], 'Error 9.5')
        g.call(g.E, 'fault.clear')                                                   # service
        g.step(10)
        self.assertEqual(g.state('status'), 'stable')
        for code, text in (('IDNR', 'IDNR error'), ('Service', 'Service')):
            g.call(g.E, 'fault.inject', code=code)
            self.assertEqual(g.lcd(), dict(main='--------', unit='', msg=text))
            g.call(g.A, 'power.connect_adapter', connected=False)
            g.call(g.A, 'power.connect_adapter', connected=True)
            g.keys(YES)
            self.assertEqual(g.lcd()['msg'], text)                                   # contact service: a restart does not clear it
            g.call(g.E, 'fault.clear')
            g.step(10)
            self.assertEqual(g.state('status'), 'stable')

    def test_language_is_kept_and_shown_texts_stay_english(self):
        g = Rig()
        g.menu_to('m_setup', 'setup_language')
        g.choose('Deutsch')
        self.assertEqual(g.state('settings')['language'], 'Deutsch')
        self.assertEqual(g.lcd()['msg'], 'Language Deutsch')
        g.keys(EXIT)
        g.call(g.A, 'settings.configure', auto_tare=True)
        self.assertEqual(g.lcd()['msg'], 'Place container')                          # no display texts in other languages in our manual

    def test_session_describe(self):
        g = Rig()
        da = g.A.describe(instance_id=INST)
        de = g.E.describe(asset_type_id='instrument.ohaus_px224e')
        self.assertEqual((da['asset_type_id'], da['asset_version']), ('instrument.ohaus_px224e', ob.ASSET_VERSIONS[MODEL]))
        self.assertIn('weighing.tare', da['capabilities'])
        self.assertIn('panel.press', da['capabilities'])
        self.assertNotIn('fault.inject', da['capabilities'])
        self.assertNotIn('truth.process', da['state_slots'])
        self.assertIn('fault.inject', de['capabilities'])
        self.assertIn('truth.process', de['state_slots'])
        self.assertEqual(de['capabilities']['weighing.tare']['availability'], 'implemented')
        self.assertEqual(de['state_slots']['weighing.net_kg']['unit'], 'kg')
        self.assertEqual(set(de['state_slots']), set(g.h.read('evaluator')))
        for kw in ({}, dict(instance_id=INST, asset_type_id='instrument.ohaus_px224e')):
            with self.assertRaises(ob.ActionRejected):
                g.A.describe(**kw)
        with self.assertRaises(ob.ActionRejected):
            g.A.describe(asset_type_id='instrument.ohaus_spx2201')


class RenderAndEvidence(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        a, b = Rig(), Rig()
        for g in (a, b):
            g.load(12.3456)
        self.assertTrue(np.array_equal(a.h.panel.render('lcd'), b.h.panel.render('lcd')))
        cap = a.call(a.A, 'panel.capture', display_id='lcd')
        self.assertTrue(Path(cap['image']['path']).is_file())
        self.assertEqual((cap['image']['width'], cap['image']['height']), tuple(LCD['size']))
        self.assertEqual(a.call(a.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_evidence_pngs(self):
        OUT.mkdir(parents=True, exist_ok=True)
        g = Rig()
        g.load(12.3456)
        g.h.panel.save_png('lcd', OUT / 'home.png')
        g.menu_to('m_setup', 'setup_filter')
        g.h.panel.save_png('lcd', OUT / 'menu.png')
        g.keys(EXIT)
        g.load(230.0)
        g.h.panel.save_png('lcd', OUT / 'error.png')
        g.load(0.0)
        g.call(g.E, 'fault.inject', code='5.3')                                      # a system error screen (p.46)
        g.h.panel.save_png('lcd', OUT / 'fault.png')
        (OUT / 'renders.json').write_text(json.dumps({k: f'outputs/panel_ui/ohaus_px224e/{k}.png' for k in ('home', 'menu', 'error', 'fault')}, indent=1) + '\n')


class SurveyCoverage(unittest.TestCase):
    def test_zz_all_survey_procedures_done(self):
        if not DONE_TASKS:
            self.skipTest('run with the key procedure tests')
        self.assertEqual([t for t in SURVEY_TASKS if t not in DONE_TASKS], [])


if __name__ == '__main__':
    unittest.main()
