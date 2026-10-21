"""front-panel tests for CHEM-008 ika_plate_rctdigital (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_ika_plate_rctdigital.py -q

Every gesture goes through the session API (panel.press / panel.turn / panel.switch, the runtime's normal invoke path).  Each check looks at the
runtime state and at the screen twice: the structured frame (panel.frame) and the rendered pixels, read back with a seven-segment reader that
knows only the LCD geometry of the spec.  Themes: the seven key procedures of the menu survey; panel = direct capability (same runtime state);
refusals shown as the device's own indication (menu entry while heating, keys while locked); documented time-outs (mode D 5 s revert, SAFE
2 s, lock flash) and no menu time-out; HOT!, Err. codes, degF, factory reset, unsupported probe calibration; render determinism and the PNGs
of outputs/panel_ui_pilot/ika_plate_rctdigital/; contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import ika_plate_rctdigital as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'ika_plate_rctdigital_01'
C0 = 273.15
RPM = rt.RPM
OUT = ROOT / 'outputs/panel_ui_pilot/ika_plate_rctdigital'
SURVEY_TASKS = ['stir at 600 rpm', 'heat to 80 C', 'set safety temperature limit', 'set intermittent stirring 1 min on / 10 s pause', 'run a 30 min timer',
                'show viscosity trend', 'lock the controls']
DONE_TASKS = set()
LCD = rt.PANEL_SPEC['displays']['lcd']
_DIGITS = {v: k for k, v in pu.SEG7.items() if k in '0123456789'}
_LETTERS = {v: k for k, v in pu.SEG7.items() if k not in '0123456789' and v}


# ---------------------------------------------------------------- reading the rendered LCD
def _dark(img, x, y):
    px = img[int(round(y)), int(round(x))]
    return int(px[0]) + int(px[1]) + int(px[2]) < 300


def read_field(img, fid):
    """Seven-segment reader: sample the middle of each segment of each cell (geometry from the spec only) and spell the field."""
    f = LCD['fields'][fid]
    n, cw, ch, gap, cgap = f['cells'], f['cell_w'], f['cell_h'], f.get('gap', 6), f.get('colon_gap', 0)
    t = f.get('thickness', max(2.0, cw * 0.18))
    colons = f.get('colons', [])
    out = ''
    for i in range(n):
        x = f['x'] + i * (cw + gap) + cgap * sum(1 for b in colons if b <= i)
        if i in colons:
            cx = x - (gap + cgap) / 2.0
            out += ':' if _dark(img, cx, f['y'] + ch * 0.3) and _dark(img, cx, f['y'] + ch * 0.7) else ''
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2),
                   g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if _dark(img, px, py))
        out += _DIGITS.get(segs, _LETTERS.get(segs, ' ' if not segs else '?'))
        r = max(1.5, t * 0.6)
        if _dark(img, x + cw + gap / 2.0, f['y'] + ch - r):
            out += '.'
    return out.strip()


def _box(ic):
    if ic['kind'] == 'text':
        return ic['x'], ic['y'], pu.text_width(ic['text'], ic.get('scale', 2)), 7 * ic.get('scale', 2)
    return ic['x'], ic['y'], ic.get('w', 16), ic.get('h', 16)


def icon_lit(img, iid):
    """Any dark pixel in the icon's box outside the boxes of the other icons (the printed '2PID' shares its P)."""
    x, y, w, h = _box(LCD['icons'][iid])
    mask = np.ones((h, w), dtype=bool)
    for k, other in LCD['icons'].items():
        if k != iid:
            ox, oy, ow, oh = _box(other)
            mask[max(oy - y, 0):max(oy + oh - y, 0), max(ox - x, 0):max(ox + ow - x, 0)] = False
    box = img[y:y + h, x:x + w].astype(int).sum(axis=2)
    return bool(((box < 300) & mask).any())


def norm(text):
    """What the pixels can tell apart: the lit segments of each cell (S and 5 look the same), points and colons."""
    out = []
    for ch in text:
        out.append(ch if ch in '.:' else pu.SEG7.get(ch, pu.SEG7.get(ch.upper(), pu.SEG7.get(ch.lower(), ''))) or '_')
    return tuple(out)


# ---------------------------------------------------------------- rig
class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.IkaPlateRctdigitalRuntime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            assert cap == 'sim.step' or self.h.t == t0, cap                              # gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    # gestures (agent session)
    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def hold(self, key):
        return self.press(key, 2.0)

    def turn(self, knob, n):
        return self.call(self.A, 'panel.turn', input_id=knob, detents=n)['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    # screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lcd(self):
        """Fields shown now, from the frame and from the pixels (both must agree)."""
        f = self.frame()['displays']['lcd']
        img = self.h.panel.render('lcd')
        shown = {k: v['shown'].strip() for k, v in f['fields'].items() if v['shown'].strip()}
        read = {k: read_field(img, k) for k in LCD['fields']}
        read = {k: v for k, v in read.items() if v}
        assert {k: norm(v) for k, v in shown.items()} == {k: norm(v) for k, v in read.items()}, (shown, read)
        return shown

    def icons(self):
        f = self.frame()['displays']['lcd']
        img = self.h.panel.render('lcd')
        on = {k for k, v in f['icons'].items() if v['shown']}
        assert on == {k for k in LCD['icons'] if icon_lit(img, k)}, (on, {k for k in LCD['icons'] if icon_lit(img, k)})
        return on

    def screen(self):
        return self.frame()['screen']

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def state(self, *ids):
        o = self.E.read(instance_id=self.inst, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def on(self):
        assert self.switch('on')

    def menu_to(self, item):
        """Open the menu and turn D to an item (position from the spec's item list)."""
        ids = [it['id'] for it in rt.PANEL_SPEC['menus']['main']['items']]
        assert self.press('menu_key')
        assert self.screen() == 'menu'
        if ids.index(item):
            assert self.turn('knob_speed', ids.index(item))
        assert self.frame()['ui']['menu']['item'] == item


def runtime_state(h):
    r = h.read('evaluator')
    r.pop('clock.time_s')
    return json.dumps(dict(read=r, menu=h.menu, heat=h.heat, stir=h.stir, timer=h.timer, locked=h.locked), sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_stir_at_600_rpm(self):
        g = Rig()
        self.assertEqual(g.screen(), 'off')
        self.assertEqual(g.lcd(), {})
        g.on()                                                                         # power button left: mode A shows
        self.assertEqual(g.screen(), 'home')
        self.assertIn('mode_a', g.icons())
        self.assertTrue(g.turn('knob_speed', 1))                                       # 0 -> 50 rpm (0 / 50-1500)
        self.assertEqual(g.lcd()['f01'], '50')
        self.assertTrue(g.turn('knob_speed', 55))                                      # 50 -> 600 in 10 rpm detents
        self.assertAlmostEqual(g.state('stirring.set_rad_s') / RPM, 600.0, places=9)
        self.assertEqual(g.lcd()['f01'], '600')
        self.assertFalse(g.state('stirring.on'))
        self.assertTrue(g.press('speed_push_key'))                                     # push D: stirring starts
        self.assertTrue(g.state('stirring.on'))
        g.step(10.0)
        self.assertEqual(g.lcd()['f08'], '600')
        self.assertIn('motor_ring', g.icons())
        DONE_TASKS.add('stir at 600 rpm')

    def test_heat_to_80_with_pt1000_2p(self):
        g = Rig()
        g.on()
        self.assertEqual(g.lcd()['f11'], '370')                                        # check the safety limit (SAFE 370)
        self.assertIn('safe_label', g.icons())
        self.assertTrue(g.turn('knob_temperature', 55))                                # 25 -> 80 C
        self.assertEqual(g.state('heating.set_k'), C0 + 80.0)
        self.assertEqual(g.lcd()['f02'], '80')
        g.call(g.A, 'vessel.place', vessel='beaker_600ml', substance_id='water', volume_m3=4e-4)
        g.call(g.A, 'sensor.set', connected=True, immersed=True)                        # PT1000 into the medium (robot), then the menu
        self.assertEqual({'sensor_1', 'sensor_probe', 'ctrl_pid'} - g.icons(), set())
        g.menu_to('temperature_control')
        self.assertIn('ctrl_pid', g.icons())
        self.assertTrue(g.press('speed_push_key'))
        self.assertTrue(g.turn('knob_speed', 1))
        self.assertEqual(g.frame()['ui']['editor']['buffer']['temperature_control'], '2p')
        self.assertTrue(g.press('speed_push_key'))
        self.assertEqual(g.state('heating.control'), '2p')
        self.assertTrue(g.press('menu_key'))
        self.assertEqual(g.screen(), 'home')
        self.assertIn('ctrl_2p', g.icons())
        self.assertTrue(g.press('temp_push_key'))                                      # push C: heating starts, heating symbol
        self.assertTrue(g.state('heating.on'))
        self.assertIn('heater', g.icons())
        g.step(600.0)
        self.assertGreater(float(g.lcd()['f07']), 40.0)
        DONE_TASKS.add('heat to 80 C')

    def test_set_safety_temperature_limit(self):
        g = Rig()
        g.on()
        self.assertTrue(g.turn('safety_screw', 400))                                   # screw to the clockwise stop
        self.assertEqual(g.state('safety.limit_k'), C0 + 370.0)
        self.assertTrue(g.turn('knob_temperature', 75))                                # set point = desired safe temperature 100 C
        self.assertTrue(g.press('temp_push_key'))
        g.step(900.0)                                                                  # wait until it is reached
        self.assertIsNone(g.state('error.code'))
        while g.state('error.code') is None:                                           # screw slowly anticlockwise ...
            self.assertTrue(g.turn('safety_screw', -5))
            self.assertGreater(g.state('safety.limit_k'), C0 + 60.0)
        self.assertEqual(g.state('error.code'), 25)                                    # ... until the heating stops and Err. 25 shows
        self.assertFalse(g.state('heating.on'))
        lcd = g.lcd()
        self.assertEqual(lcd['f08'], 'E25')
        self.assertEqual(lcd['f11'], str(round(g.state('safety.limit_k') - C0)))       # the Safe Temp value is displayed
        self.assertNotIn('heater', g.icons())
        DONE_TASKS.add('set safety temperature limit')

    def test_intermittent_1_min_on_10_s_pause(self):
        g = Rig()
        g.on()
        g.turn('knob_speed', 31)
        g.press('speed_push_key')
        g.turn('knob_temperature', 15)
        g.press('temp_push_key')
        self.assertFalse(g.press('menu_key'))                                          # refused while heating / stirring run
        g.press('temp_push_key')                                                       # stop heating and stirring
        g.press('speed_push_key')
        g.menu_to('intermittent')
        self.assertEqual(g.lcd(), {'f04': '00:00:00'})
        self.assertIn('motor_ring', g.icons())
        self.assertTrue(g.press('speed_push_key'))                                     # open: run time flashes
        self.assertTrue(g.turn('knob_speed', 6))                                       # 60 s
        self.assertEqual(g.lcd()['f04'], '00:01:00')
        self.assertTrue(g.frame()['displays']['lcd']['fields']['f04']['blink'])
        g.press('speed_push_key')
        self.assertEqual(g.frame()['ui']['editor']['field'], 'pause_s')
        g.turn('knob_speed', 7)                                                        # 3 -> 10 s
        self.assertEqual(g.lcd()['f04'], '00:00:10')
        self.assertIn('intermittent_dot', g.icons())
        g.press('speed_push_key')
        self.assertEqual(g.frame()['ui']['editor']['field'], 'auto_reverse')
        self.assertEqual(g.lcd()['f01'], 'OFF')
        self.assertTrue(g.press('speed_push_key'))                                     # keep OFF: all written now
        self.assertEqual(g.state('stirring.pattern'), dict(direction='cw', auto_reverse=False, run_s=60.0, pause_s=10.0, intermittent=True))
        self.assertTrue(g.press('menu_key'))
        self.assertEqual(g.screen(), 'home')
        self.assertIn('intermittent_dot', g.icons())
        DONE_TASKS.add('set intermittent stirring 1 min on / 10 s pause')

    def test_30_min_timer(self):
        g = Rig()
        g.on()
        self.assertTrue(g.hold('timer_key'))                                           # F for 2 s: entry, hh flashes
        self.assertEqual(g.screen(), 'timer_edit')
        f = g.frame()['displays']['lcd']['fields']['f04']
        self.assertEqual((f['text'], f['blink_mask']), ('00:00:00', '11000000'))
        self.assertTrue(g.press('speed_push_key'))                                     # hh 00
        self.assertTrue(g.turn('knob_speed', 30))                                      # mm 30
        self.assertEqual(g.frame()['displays']['lcd']['fields']['f04']['blink_mask'], '00011000')
        self.assertTrue(g.press('speed_push_key'))
        self.assertTrue(g.press('speed_push_key'))                                     # ss 00: timer ready
        self.assertEqual(g.screen(), 'home')
        self.assertEqual(g.state('timer.state')['remaining_s'], 1800.0)
        self.assertFalse(g.state('timer.state')['running'])
        self.assertEqual(g.lcd()['f04'], '00:30:00')
        self.assertIn('timer_t', g.icons())
        self.assertTrue(g.press('timer_key'))                                          # F: count down
        g.step(61.0)
        self.assertEqual(g.lcd()['f04'], '00:28:59')
        self.assertTrue(g.press('timer_key'))                                          # F: pause
        g.step(30.0)
        self.assertEqual(g.lcd()['f04'], '00:28:59')
        self.assertTrue(g.press('timer_key'))                                          # F: restart
        g.step(1739.0)
        self.assertTrue(g.state('timer.state')['expired'])
        f = g.frame()['displays']['lcd']['fields']['f04']
        self.assertEqual((f['text'], f['blink']), ('00:00:00', True))                  # flashes at 00:00:00
        self.assertIn('TIMER_EXPIRED', [e['code'] for e in g.call(g.A, 'events.read')['events']])
        g.step(0.5)
        self.assertNotIn('f04', g.lcd())                                               # off phase of the flash
        self.assertTrue(g.hold('timer_key'))                                           # F for 2 s: exit
        self.assertIsNone(g.state('timer.state')['mode'])
        self.assertNotIn('f04', g.lcd())
        DONE_TASKS.add('run a 30 min timer')

    def test_counter_mode_and_day_hour(self):
        g = Rig()
        g.on()
        g.hold('timer_key')
        for _ in range(3):
            g.press('speed_push_key')                                                  # 00:00:00 accepted three times: counter
        self.assertEqual(g.state('timer.state')['mode'], 'counter')
        self.assertIn('counter_c', g.icons())
        g.press('timer_key')
        g.step(4 * 86400.0 + 5 * 3600.0 + 7.0)
        self.assertEqual(g.frame()['displays']['lcd']['fields']['f04']['text'], ' d04:05')   # day:hour above 100 h (manual 'd99:23')
        self.assertEqual(norm(read_field(g.h.panel.render('lcd'), 'f04')), norm('d04:05'))

    def test_viscosity_trend(self):
        g = Rig()
        g.on()
        g.menu_to('safe_visc')
        g.press('speed_push_key')
        g.turn('knob_speed', 1)
        self.assertIn('visc_label', g.icons())
        g.press('speed_push_key')
        self.assertEqual(g.state('menu.state')['display'], 'visc')
        g.press('menu_key')
        g.call(g.A, 'vessel.place', vessel='beaker_600ml', substance_id='ethylene_glycol', volume_m3=4e-4, stir_bar_length_m=0.06)
        g.turn('knob_speed', 76)                                                       # 800 rpm
        g.press('speed_push_key')
        g.step(30.0)                                                                   # speed settled: the trend starts at 100 %
        self.assertEqual({'visc_label', 'percent'} - g.icons(), set())
        self.assertAlmostEqual(float(g.lcd()['f11']), 100.0, delta=2.0)
        g.call(g.E, 'medium.modify', viscosity_factor=8.0)
        g.step(5.0)
        self.assertGreater(float(g.lcd()['f11']), 150.0)
        self.assertTrue(g.hold('menu_key'))                                            # Menu for 2 s: back to 100 %
        self.assertEqual(g.screen(), 'home')
        g.step(1.0)
        self.assertAlmostEqual(float(g.lcd()['f11']), 100.0, delta=2.0)
        DONE_TASKS.add('show viscosity trend')

    def test_lock(self):
        g = Rig()
        g.on()
        self.assertFalse(g.press('lock_key'))                                          # a short press does nothing
        self.assertTrue(g.hold('lock_key'))                                            # Lock G for 2 s
        self.assertTrue(g.state('lock.on'))
        self.assertIn('lock', g.icons())
        self.assertTrue(g.hold('lock_key'))
        self.assertFalse(g.state('lock.on'))
        self.assertNotIn('lock', g.icons())
        DONE_TASKS.add('lock the controls')


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def pair(self):
        a, b = Rig(seed=3), Rig(seed=3)
        a.on()
        b.call(b.A, 'power.set', on=True)
        return a, b

    def test_set_points_and_toggles(self):
        a, b = self.pair()
        a.turn('knob_temperature', 55)
        a.turn('knob_speed', 56)
        a.press('temp_push_key')
        a.press('speed_push_key')
        b.call(b.A, 'heating.set_temperature', temperature_k=C0 + 80.0)
        b.call(b.A, 'stirring.set_speed', speed_rad_s=600.0 * RPM)
        b.call(b.A, 'heating.toggle')
        b.call(b.A, 'stirring.toggle')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.step(120.0)
        b.step(120.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))

    def test_menu_items(self):
        a, b = self.pair()
        seq = [('operating_mode', [1]), ('temperature_control', [1]), ('beep', [-1]), ('temperature_unit', [1]), ('err5', [5]),
               ('timer_expired', [1, 1]), ('safe_visc', [1])]
        for item, turns in seq:
            a.menu_to(item)
            a.press('speed_push_key')
            for n in turns:
                a.turn('knob_speed', n)
                a.press('speed_push_key')
            self.assertIsNone(a.frame()['ui']['editor'])
            a.press('menu_key')
        b.call(b.A, 'menu.configure', operating_mode='B', beep=False, unit='F', err5_timeout_s=300.0, expiry_stop_heating=True, expiry_stop_stirring=True,
               display='visc')
        b.call(b.A, 'heating.set_mode', mode='2p')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))

    def test_intermittent_continuous_ccw_and_timer(self):
        a, b = self.pair()
        a.menu_to('intermittent')
        a.press('speed_push_key')
        a.press('speed_push_key')                                                      # run 00:00 = continuous: the direction comes next
        self.assertEqual(a.frame()['ui']['editor']['field'], 'direction')
        a.turn('knob_speed', 1)
        self.assertTrue(a.frame()['displays']['lcd']['icons']['motor_ring']['blink'])
        self.assertEqual(a.frame()['displays']['lcd']['icons']['motor_ring']['direction'], 'ccw')
        a.press('speed_push_key')
        a.press('menu_key')
        a.hold('timer_key')
        a.turn('knob_speed', 1)
        a.press('speed_push_key')
        a.turn('knob_speed', 2)
        a.press('speed_push_key')
        a.turn('knob_speed', 3)
        a.press('speed_push_key')
        b.call(b.A, 'stirring.configure', direction='ccw', auto_reverse=False, run_s=0.0)
        b.call(b.A, 'timer.set', duration_s=3600.0 + 120.0 + 3.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))

    def test_screw_lock_power(self):
        a, b = self.pair()
        a.turn('safety_screw', -170)
        a.hold('lock_key')
        b.call(b.A, 'safety.set_limit', limit_k=C0 + 200.0)
        b.call(b.A, 'lock.toggle')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.switch('off')
        b.call(b.A, 'power.set', on=False)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


# ---------------------------------------------------------------- refusals, time-outs, special screens
class Rules(unittest.TestCase):
    def test_menu_refused_while_heating(self):
        g = Rig()
        g.on()
        g.turn('knob_temperature', 20)
        g.press('temp_push_key')
        before, img = runtime_state(g.h), g.h.panel.render('lcd')
        self.assertFalse(g.press('menu_key'))
        self.assertEqual(g.screen(), 'home')
        self.assertEqual(runtime_state(g.h), before)
        self.assertTrue((g.h.panel.render('lcd') == img).all())                        # no indication: the display does not change
        g.press('temp_push_key')
        g.turn('knob_speed', 10)
        g.press('speed_push_key')
        self.assertFalse(g.press('menu_key'))                                          # stirring alone also blocks it
        g.press('speed_push_key')
        self.assertTrue(g.press('menu_key'))
        g.call(g.A, 'heating.set', on=True)                                            # started another way while the menu is open
        self.assertEqual(g.screen(), 'home')

    def test_locked_keys_flash_the_lock_symbol(self):
        g = Rig()
        g.on()
        g.hold('lock_key')
        steady = g.h.panel.render('lcd')
        before = runtime_state(g.h)
        for gesture in (lambda: g.turn('knob_temperature', 5), lambda: g.press('speed_push_key'), lambda: g.press('menu_key'), lambda: g.hold('timer_key')):
            self.assertFalse(gesture())
            fr = g.frame()['displays']['lcd']['icons']['lock']
            self.assertEqual((fr['on'], fr['blink'], fr['shown']), (True, True, False))   # flash starts with the symbol off
            self.assertNotIn('lock', g.icons())
            self.assertFalse((g.h.panel.render('lcd') == steady).all())
            self.assertEqual(runtime_state(g.h), before)
            self.assertEqual(g.screen(), 'home')
        g.step(0.5)
        self.assertIn('lock', g.icons())                                               # on phase of the flash
        g.step(4.5)
        self.assertNotIn('lock', g.frame()['ui']['effects'])                           # five flashes are over: the symbol is steady
        self.assertIn('lock', g.icons())
        g.step(0.5)
        self.assertIn('lock', g.icons())
        self.assertTrue(g.switch('off'))                                               # the power switch is not locked
        self.assertTrue(g.switch('on'))
        self.assertTrue(g.state('lock.on'))                                            # the lock survives the power cycle
        self.assertIn('lock', g.icons())

    def test_no_menu_timeout(self):
        g = Rig()
        g.on()
        g.menu_to('beep')
        g.press('speed_push_key')
        g.step(3600.0)                                                                 # the manual describes no time-out
        fr = g.frame()
        self.assertEqual((fr['screen'], fr['ui']['menu']['item'], fr['ui']['editor']['field']), ('menu', 'beep', 'beep'))
        g.turn('knob_speed', -1)
        self.assertTrue(g.press('menu_key'))                                           # Menu exits from each state: the edit is discarded
        self.assertTrue(g.state('menu.state')['beep'])

    def test_mode_d_confirmation_and_timeout(self):
        g = Rig()
        g.on()
        g.menu_to('operating_mode')
        g.press('speed_push_key')
        g.turn('knob_speed', 2)
        g.press('speed_push_key')
        g.press('menu_key')
        self.assertEqual(g.state('menu.state')['operating_mode'], 'D')
        self.assertIn('mode_d', g.icons())
        g.switch('off')
        g.switch('on')                                                                 # mode D: the safe temperature must be confirmed
        self.assertEqual(g.screen(), 'safe_confirm')
        self.assertTrue(g.frame()['displays']['lcd']['fields']['f11']['blink'])
        self.assertFalse(g.turn('knob_temperature', 5))
        self.assertEqual(g.state('heating.set_k'), C0 + 25.0)
        self.assertTrue(g.press('speed_push_key'))                                     # either knob confirms
        self.assertEqual((g.screen(), g.state('stirring.on')), ('home', False))
        self.assertTrue(g.turn('knob_temperature', 10))                                # the new value flashes, not yet applied
        self.assertEqual(g.state('heating.set_k'), C0 + 25.0)
        f = g.frame()['displays']['lcd']['fields']['f02']
        self.assertEqual((f['text'], f['blink']), ('35', True))
        g.step(5.0)                                                                    # not confirmed within 5 s: skips back
        self.assertEqual(g.lcd()['f02'], '25')
        self.assertEqual(g.frame()['ui']['pending'], {})
        g.turn('knob_temperature', 10)
        g.step(4.0)
        self.assertTrue(g.press('temp_push_key'))                                      # confirmed: applied, heating still off
        self.assertEqual((g.state('heating.set_k'), g.state('heating.on')), (C0 + 35.0, False))
        self.assertEqual(g.lcd()['f02'], '35')
        self.assertTrue(g.press('temp_push_key'))                                      # now the push starts the heating
        self.assertTrue(g.state('heating.on'))
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)                 # a mains failure is a power-up too
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        self.assertEqual(g.screen(), 'safe_confirm')

    def test_safe_shown_2_s_while_adjusting_on_visc(self):
        g = Rig()
        g.on()
        g.call(g.A, 'menu.configure', display='visc')
        self.assertIn('visc_label', g.icons())
        self.assertTrue(g.turn('safety_screw', -10))
        self.assertEqual(g.lcd()['f11'], '360')
        self.assertEqual({'safe_label', 'unit_c_top'} - g.icons(), set())
        g.step(1.5)
        self.assertIn('safe_label', g.icons())
        g.step(0.5)                                                                    # back to VISC 2 s after the adjustment
        self.assertEqual({'visc_label', 'percent'} - g.icons(), set())
        self.assertNotIn('f11', g.lcd())

    def test_hot_off_screen_and_error_codes(self):
        g = Rig()
        g.on()
        g.turn('knob_temperature', 75)
        g.press('temp_push_key')
        g.step(600.0)
        self.assertTrue(g.switch('off'))
        lcd = g.lcd()
        self.assertEqual(lcd['f08'], 'HOt')                                            # HOT! and the plate temperature
        self.assertGreater(float(lcd['f07']), 50.0)
        fr = g.frame()['displays']
        self.assertTrue(fr['standby_led']['shown'] and fr['hot_lamp']['shown'])
        self.assertFalse(g.turn('knob_speed', 3))                                      # switched off: knobs do nothing
        self.assertFalse(g.press('menu_key'))
        g.step(4 * 3600.0)
        self.assertEqual(g.lcd(), {})
        g.on()
        g.call(g.E, 'fault.inject', fault='heater_switch_fault', active=True)
        self.assertEqual(g.lcd()['f08'], 'E31')
        self.assertFalse(g.press('temp_push_key'))                                     # ERROR_ACTIVE: refused, Err. 31 stays
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'ERROR_ACTIVE')
        codes = {e['code'] for e in rt.PANEL_SPEC['errors']['device']}
        self.assertTrue({1, 3, 4, 5, 6, 14, 21, 24, 25, 31} <= codes)                 # every code the runtime raises is in the table

    def test_degf_display(self):
        g = Rig()
        g.on()
        g.turn('knob_temperature', 55)
        g.menu_to('temperature_unit')
        g.press('speed_push_key')
        g.turn('knob_speed', 1)
        g.press('speed_push_key')
        g.press('menu_key')
        lcd = g.lcd()
        self.assertEqual((lcd['f02'], lcd['f11']), ('176', '698'))                     # 80 C, 370 C
        self.assertEqual({'unit_f_set', 'unit_f_top'} - g.icons(), set())
        self.assertEqual(g.state('heating.set_k'), C0 + 80.0)                          # the interface stays SI

    def test_factory_reset_cal_and_version(self):
        g = Rig()
        g.on()
        g.call(g.A, 'menu.configure', operating_mode='B', beep=False, unit='F', display='visc', expiry_stop_heating=True)
        g.call(g.A, 'stirring.configure', run_s=60.0, pause_s=20.0, auto_reverse=True)
        g.call(g.A, 'heating.set_mode', mode='2p')
        g.menu_to('factory_reset')
        self.assertEqual(g.lcd(), {'f02': 'FACt', 'f04': 'rESEt'})
        g.press('speed_push_key')
        self.assertEqual(g.lcd()['f01'], 'no')
        g.press('speed_push_key')                                                      # no: nothing changes
        self.assertEqual(g.state('menu.state')['operating_mode'], 'B')
        g.press('speed_push_key')
        g.turn('knob_speed', 1)
        self.assertEqual(g.lcd()['f01'], 'YES')
        self.assertTrue(g.press('speed_push_key'))
        self.assertEqual(g.state('menu.state'), dict(operating_mode='A', beep=True, unit='C', err5_timeout_s=300.0, expiry_stop_heating=False,
                                                     expiry_stop_stirring=False, display='safe'))
        self.assertEqual(g.state('stirring.pattern'), dict(direction='cw', auto_reverse=False, run_s=0.0, pause_s=3.0, intermittent=False))
        self.assertEqual(g.h.heat['mode'], 'pid')
        g.turn('knob_speed', 1)                                                        # probe calibration (see ProbeCalibration)
        self.assertEqual(g.lcd(), {'f07': 'CAL'})
        self.assertIn('sensor_probe', g.icons())
        g.press('speed_push_key')
        self.assertEqual(g.lcd(), {'f07': 'CAL', 'f04': 'SEt', 'f01': 'no'})
        g.turn('knob_speed', 1)
        before = runtime_state(g.h)
        self.assertFalse(g.press('speed_push_key'))                                    # no external sensor: the flow does not start
        fr = g.frame()
        self.assertEqual((fr['ui']['editor'], fr['ui']['menu']['item']), (None, 'probe_calibration'))
        self.assertEqual(runtime_state(g.h), before)
        g.turn('knob_speed', 1)
        self.assertEqual(g.lcd(), {'f08': '1.00'})
        self.assertTrue(g.press('speed_push_key'))                                     # info: nothing to edit
        self.assertIsNone(g.frame()['ui']['editor'])
        self.assertTrue(g.turn('knob_speed', 5))                                       # the list stops at its end
        self.assertEqual(g.frame()['ui']['menu']['item'], 'software_version')

    def test_rejected_and_unknown_gestures(self):
        g = Rig()
        r = g.call(g.A, 'panel.turn', expect='rejected', input_id='menu_key', detents=1)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('panel.frame', g.A.describe(instance_id=INST)['capabilities'])
        self.assertIn('panel.press', g.A.describe(instance_id=INST)['capabilities'])
        h = rt.IkaPlateRctdigitalRuntime('x')
        h.register_principal('a', 'agent')
        r = h.session('a').invoke(instance_id='x', capability_id='panel.press', arguments=dict(input_id='menu_key'), request_id='1')
        self.assertEqual(r['error']['code'], 'PLANT_REQUIRED')


# ---------------------------------------------------------------- probe calibration (menu Probe calibration, manual p.16, p.19-20)
def calibration_rig(seed=1, offset_k=0.0, gain=0.0):
    g = Rig(seed=seed)
    if offset_k or gain:
        g.call(g.E, 'sensor.configure_error', offset_k=offset_k, gain=gain)
    g.call(g.A, 'vessel.place', vessel='beaker_600ml', substance_id='water', volume_m3=3e-4)
    g.call(g.A, 'sensor.set', connected=True, immersed=True)
    g.on()
    return g


def wait_stable(g, twin=None, limit_s=3600.0):
    """Step until the SAFE symbol of the calibration screen lights (the reading is stable)."""
    while 'safe_label' not in g.icons():
        assert g.h.t < limit_s, 'never stable'
        g.step(30.0)
        if twin is not None:
            twin.step(30.0)


def calibrate_via_panel(test, g, set_points=(50.0, 100.0), twin=None):
    """The manual's flow: CAL > SEt YES; per point set the temperature (C) and speed (D), push D, wait for SAFE, push D, enter the reference
    thermometer reading (EXT, here the true medium temperature to 0.1 K) with C, push D.  A twin runtime, when given, gets the same
    commands as direct capability calls at the same instants."""
    g.menu_to('probe_calibration')
    test.assertTrue(g.press('speed_push_key'))
    test.assertEqual(g.lcd(), {'f07': 'CAL', 'f04': 'SEt', 'f01': 'no'})
    g.turn('knob_speed', 1)
    test.assertTrue(g.press('speed_push_key'))
    test.assertEqual(g.screen(), 'cal_set')
    readings = []
    for k, sp in enumerate(set_points):
        n = int(round(sp - (g.state('heating.set_k') - C0)))
        if n:
            test.assertTrue(g.turn('knob_temperature', n))
        if twin is not None:
            twin.call(twin.A, 'heating.set_temperature', temperature_k=C0 + sp)
        if k == 0:
            test.assertTrue(g.turn('knob_speed', 46))                                   # 0 -> 500 rpm
            if twin is not None:
                twin.call(twin.A, 'stirring.set_speed', speed_rad_s=500.0 * RPM)
        f = g.frame()['displays']['lcd']['fields']['f02']
        test.assertEqual((f['text'], f['blink']), (str(int(sp)), True))
        test.assertTrue(g.press('speed_push_key'))                                     # heating and stirring start
        if twin is not None:
            twin.call(twin.A, 'heating.set', on=True)
            twin.call(twin.A, 'stirring.set', on=True)
        test.assertEqual((g.screen(), g.state('heating.on'), g.state('stirring.on')), ('cal_wait', True, True))
        wait_stable(g, twin)
        shown = g.lcd()['f07']
        true_c = g.call(g.E, 'truth.inspect')['process']['charge_k'] - C0
        test.assertTrue(g.press('speed_push_key'))
        test.assertEqual(g.screen(), 'cal_ext')
        lcd = g.lcd()
        test.assertEqual((lcd['f07'], lcd['f11']), (shown, 'EXt'))                      # the EXT entry starts at the reading
        ref = round(true_c, 1)
        n = int(round((ref - float(shown)) * 10))
        if n:
            test.assertTrue(g.turn('knob_temperature', n))
        test.assertEqual(float(g.lcd()['f07']), ref)
        last = g.press('speed_push_key')
        readings.append((C0 + float(shown), C0 + ref))
    if twin is not None:
        twin.call(twin.A, 'heating.set', on=False)
        twin.call(twin.A, 'stirring.set', on=False)
        (d1, r1), (d2, r2) = readings
        twin.call(twin.A, 'sensor.calibrate_two_point', expect='succeeded' if last else 'rejected', device_1_k=d1, reference_1_k=r1, device_2_k=d2,
                  reference_2_k=r2)
    fr = g.frame()
    test.assertEqual((fr['screen'], fr['ui']['menu']['item'], g.state('heating.on'), g.state('stirring.on')), ('menu', 'probe_calibration', False, False))
    return last, readings


class ProbeCalibration(unittest.TestCase):
    def test_two_point_calibration_removes_an_injected_error(self):
        g = calibration_rig(offset_k=1.5, gain=0.01)
        twin = calibration_rig(offset_k=1.5, gain=0.01)
        accepted, readings = calibrate_via_panel(self, g, twin=twin)
        self.assertTrue(accepted)
        cal = g.state('sensor.calibration')
        self.assertTrue(cal['calibrated'])
        self.assertAlmostEqual(cal['gain'], 1.0 / 1.01, delta=0.005)
        self.assertEqual(cal['points_k'], [list(x) for x in readings])
        self.assertEqual(runtime_state(g.h), runtime_state(twin.h))                    # panel = direct capabilities
        g.press('menu_key')
        g.turn('knob_temperature', -25)                                                 # 100 -> 75 C, heat and stir
        g.press('temp_push_key')
        g.press('speed_push_key')
        g.step(1800.0)
        true_c = g.call(g.E, 'truth.inspect')['process']['charge_k'] - C0
        self.assertAlmostEqual(float(g.lcd()['f07']), true_c, delta=0.15)               # display: the error is calibrated out ...
        self.assertAlmostEqual(true_c, 75.0, delta=0.5)                                 # ... and so is the control
        g.press('temp_push_key')
        g.press('speed_push_key')
        g.menu_to('probe_calibration')                                                  # re-set: back to the factory calibration
        g.press('speed_push_key')
        self.assertEqual(g.lcd(), {'f07': 'CAL', 'f04': 'rESEt', 'f01': 'no'})
        g.turn('knob_speed', 1)
        self.assertTrue(g.press('speed_push_key'))
        self.assertFalse(g.state('sensor.calibration')['calibrated'])
        true_c = g.call(g.E, 'truth.inspect')['process']['charge_k'] - C0
        self.assertGreater(g.state('display.temperature') - true_c, 1.5)               # the probe error shows again
        twin.call(twin.A, 'sensor.reset_calibration')
        self.assertEqual(g.state('sensor.calibration'), twin.state('sensor.calibration'))

    def test_ideal_sensor_calibration_changes_nothing(self):
        g = calibration_rig()
        accepted, _ = calibrate_via_panel(self, g)
        self.assertTrue(accepted)
        cal = g.state('sensor.calibration')
        self.assertAlmostEqual(cal['gain'], 1.0, delta=0.005)
        self.assertLess(abs(cal['offset_k']), 0.3)
        true_c = g.call(g.E, 'truth.inspect')['process']['charge_k'] - C0
        self.assertAlmostEqual(g.state('display.temperature'), true_c, delta=0.15)
        h = g.h
        self.assertLess(abs(h._sensor_reading() - h._sensor_raw()), 0.1)                 # the correction is (nearly) the identity

    def test_refused_points_abort_and_no_sensor(self):
        g = calibration_rig()
        accepted, _ = calibrate_via_panel(self, g, set_points=(50.0, 50.0))              # the same point twice
        self.assertFalse(accepted)
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'CAL_POINTS_TOO_CLOSE')
        self.assertFalse(g.state('sensor.calibration')['calibrated'])
        g.press('menu_key')
        g.menu_to('probe_calibration')
        g.press('speed_push_key')
        g.turn('knob_speed', 1)
        g.press('speed_push_key')
        g.press('speed_push_key')
        self.assertEqual(g.screen(), 'cal_wait')
        self.assertTrue(g.press('menu_key'))                                           # Menu aborts: heating and stirring off
        self.assertEqual((g.screen(), g.state('heating.on'), g.state('stirring.on')), ('home', False, False))
        g.menu_to('probe_calibration')
        g.call(g.A, 'sensor.set', connected=False, immersed=False)
        g.press('speed_push_key')
        g.turn('knob_speed', 1)
        self.assertFalse(g.press('speed_push_key'))                                    # no sensor: the flow does not start
        self.assertEqual(g.screen(), 'menu')


# ---------------------------------------------------------------- rendering, capture, contract
class FaultScreens(unittest.TestCase):
    """Err. 13 / 22 / 26 / 44 / 46 (manual p.27-28) driven by evaluator fault injection: the documented code in field 08, heating off, starts
    refused while the code shows, and the manual's remedy."""

    def heating(self, g, knob=50):
        g.on()
        self.assertTrue(g.turn('knob_temperature', knob))
        self.assertTrue(g.press('temp_push_key'))
        g.step(120.0)
        self.assertTrue(g.state('heating.on'))

    def test_sensor_faults_26_44_46_power_cycle_after_repair(self):
        for fault, code in (('control_sensor_drift', 26), ('safety_sensor_disconnected', 44), ('safety_sensor_drift', 46)):
            g = Rig()
            self.heating(g)
            g.call(g.E, 'fault.inject', fault=fault, active=True)
            self.assertEqual((g.state('error.code'), g.state('heating.on')), (code, False))
            self.assertEqual(g.lcd()['f08'], f'E{code}')
            self.assertNotIn('heater', g.icons())
            self.assertFalse(g.press('temp_push_key'))                                 # heating cannot be started while the code shows
            self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'ERROR_ACTIVE')
            g.switch('off')
            g.on()
            self.assertEqual(g.lcd()['f08'], f'E{code}')                              # the cause is still there
            g.call(g.E, 'fault.inject', fault=fault, active=False)                      # repaired (defective sensor replaced)
            g.step(3600.0)
            g.switch('off')
            g.on()                                                                     # manual: switch off, leave to cool, switch on
            self.assertIsNone(g.state('error.code'))
            self.assertNotEqual(g.lcd().get('f08'), f'E{code}')

    def test_err13_remedy_is_another_safe_temp(self):
        g = Rig()
        g.on()
        g.call(g.E, 'fault.inject', fault='safety_circuit_open', active=True)
        self.assertEqual(g.lcd()['f08'], 'E13')
        self.assertFalse(g.press('temp_push_key'))
        self.assertTrue(g.turn('safety_screw', -10))                                   # manual p.27: change SAFE TEMP to a different value
        self.assertIsNone(g.state('error.code'))
        self.assertNotIn('safety_circuit_open', g.state('truth.faults'))
        self.assertTrue(g.press('temp_push_key'))                                      # heating can start again
        self.assertTrue(g.state('heating.on'))

    def test_err22_found_by_the_power_up_safety_test(self):
        g = Rig()
        g.on()
        g.call(g.E, 'fault.inject', fault='safety_check_fault', active=True)
        self.assertIsNone(g.state('error.code'))                                       # found only by the test at power-up
        g.switch('off')
        g.on()
        self.assertEqual(g.lcd()['f08'], 'E22')
        self.assertFalse(g.press('temp_push_key'))
        g.call(g.E, 'fault.inject', fault='safety_check_fault', active=False)
        g.switch('off')
        g.on()
        self.assertIsNone(g.state('error.code'))

    def test_err44_safe_temp_below_the_safety_sensor(self):
        g = Rig()
        self.heating(g, knob=75)                                                       # plate control to 100 C
        g.step(900.0)
        self.assertTrue(g.press('temp_push_key'))                                      # heating off
        self.assertFalse(g.state('heating.on'))
        tp = g.h.Tp - C0                                                               # control sensor; the safety sensor reads 2 K more (assumption)
        target = math.floor(tp) + 1                                                    # between the two readings
        self.assertTrue(g.turn('safety_screw', int(round(target - (g.state('safety.limit_k') - C0)))))
        self.assertEqual((g.state('safety.limit_k') - C0, g.state('error.code')), (target, 44))
        self.assertEqual(g.lcd()['f08'], 'E44')
        self.assertTrue(g.turn('safety_screw', 20))                                    # manual p.28: set SAFE TEMP higher
        g.switch('off')
        g.on()
        self.assertIsNone(g.state('error.code'))
        codes = {e['code'] for e in rt.PANEL_SPEC['errors']['device'] if e['simulated']}
        self.assertTrue({13, 22, 26, 44, 46} <= codes)


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig(seed=5)
            g.on()
            g.turn('knob_temperature', 75)
            g.turn('knob_speed', 56)
            g.press('speed_push_key')
            g.press('temp_push_key')
            g.hold('timer_key')
            g.press('speed_push_key')
            g.turn('knob_speed', 30)
            g.press('speed_push_key')
            g.press('speed_push_key')
            g.press('timer_key')
            g.step(300.0)
            return g
        a, b = scenario(), scenario()
        for d in rt.PANEL_SPEC['displays']:
            ia, ib = a.h.panel.render(d), b.h.panel.render(d)
            self.assertEqual(ia.dtype.name, 'uint8')
            self.assertEqual(ia.shape[2], 3)
            self.assertTrue((ia == ib).all())
            self.assertTrue((ia == a.h.panel.render(d)).all())
        self.assertEqual(a.h.panel.png_bytes('lcd'), b.h.panel.png_bytes('lcd'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {}
        shots['home'] = a.h.panel.save_png('lcd', OUT / 'home.png')
        a.press('temp_push_key')
        a.press('speed_push_key')
        a.menu_to('timer_expired')
        a.press('speed_push_key')
        a.turn('knob_speed', 1)
        shots['menu'] = a.h.panel.save_png('lcd', OUT / 'menu.png')
        self.assertEqual(a.lcd(), {'f07': 'StOP', 'f08': 'StOP', 'f02': 'YES', 'f04': '00:00:00', 'f01': 'no'})
        a.press('menu_key')
        a.call(a.E, 'safety.set_limit', limit_k=C0 + 60.0)                             # the plate is above the new limit: Err. 24
        self.assertEqual(a.lcd()['f08'], 'E24')
        shots['error'] = a.h.panel.save_png('lcd', OUT / 'error.png')
        for name, s in shots.items():
            data = (OUT / f'{name}.png').read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), s['sha256'])
            self.assertEqual((s['width'], s['height']), tuple(LCD['size']))
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.on()
        r2 = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        self.assertEqual(g.call(g.A, 'panel.capture', display_id='lcd'), r2)          # same screen, same file

    def test_session_snapshot_keeps_the_panel_bound(self):
        g = Rig()
        g.on()
        g.call(g.A, 'stirring.configure', expect='rejected', auto_reverse=True)          # a rejected call restores the snapshot
        self.assertIs(g.h.panel.host, g.h)
        self.assertTrue(g.turn('knob_speed', 3))
        c = copy.deepcopy(g.h)                                                         # a copied runtime gets its own panel
        self.assertIs(c.panel.host, c)
        c.panel.turn('knob_speed', 5)
        self.assertNotEqual(c.stir['set_rpm'], g.h.stir['set_rpm'])

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.turn', 'panel.switch', 'panel.capture', 'panel.frame'})
        for where, it in [x for m in rt.PANEL_SPEC['menus'].values() for x in _items(m['items'])]:
            self.assertTrue(it.get('evidence') or it.get('assumption') or '.fields' in where, where)
        m = rt.export_ika_plate_rctdigital_contract_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in rt.PANEL_CAPS:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/ika_plate_rctdigital.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])                    # the shared engine is not pinned per asset
        spec = importlib.util.spec_from_file_location('ika_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


def _items(items, where='items'):
    for i, it in enumerate(items):
        yield f'{where}[{i}]', it
        yield from _items(it.get('items', []), f'{where}[{i}].items')
        yield from _items(it.get('fields', []), f'{where}[{i}].fields')


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
