"""front-panel tests for CHEM-011 binder_fd056 (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_binder_fd056.py -q

Every gesture goes through the session API (panel.press, the runtime's normal invoke path).  Each check looks at the runtime state and at the
LCD twice: the structured frame (panel.frame) and the rendered pixels, read back with a seven-segment reader and a 5x7 dot-matrix reader that
know only the display geometry of the spec.  Themes: the seven key procedures of the menu survey; the user menu (password L1, date and time,
unit, passwords, language) and password L2; panel = direct capability (same runtime state); refusals (standby, no mains, wrong passwords,
discarded entries, impossible dates, stick removed); flashing values and no menu time-out; !TLim, Overtemperature and timer texts; render
determinism and the PNGs of outputs/panel_ui/binder_fd056/; contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import json
import sys
import unicodedata
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import binder_fd056 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'binder_fd056_01'
C0 = 273.15
OUT = ROOT / 'outputs/panel_ui/binder_fd056'
SURVEY_TASKS = ['set temperature to 105 C', 'set safety controller to 110 C', 'set air flap half open', "run 'Delayed Off' timer for 2 h 30 min",
                'program a ramp of 1 C/min to 40 C', 'reset a safety controller alarm', 'start USB recording']
DONE_TASKS = set()
LCD = rt.PANEL_SPEC['displays']['lcd']
_GLYPHS = {}
for _ch, _g in pu.FONT5X7.items():
    _GLYPHS.setdefault(bytes(_g), _ch)
_DIGITS = {v: k for k, v in pu.SEG7.items() if k in '0123456789'}
_LETTERS = {v: k for k, v in pu.SEG7.items() if k not in '0123456789' and v}


# ---------------------------------------------------------------- reading the rendered LCD (white on black)
def _lit(img, x, y):
    px = img[int(round(y)), int(round(x))]
    return int(px[0]) + int(px[1]) + int(px[2]) > 400


def read_digits(img):
    """Seven-segment reader: sample the middle of each segment of each cell (geometry from the spec only) and spell the field."""
    f = LCD['fields']['main']
    n, cw, ch, gap, cgap = f['cells'], f['cell_w'], f['cell_h'], f.get('gap', 6), f.get('colon_gap', 0)
    t = f.get('thickness', max(2.0, cw * 0.18))
    colons = f.get('colons', [])
    out = ''
    for i in range(n):
        x = f['x'] + i * (cw + gap) + cgap * sum(1 for b in colons if b <= i)
        if i in colons:
            cx = x - (gap + cgap) / 2.0
            out += ':' if _lit(img, cx, f['y'] + ch * 0.3) and _lit(img, cx, f['y'] + ch * 0.7) else ''
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2),
                   g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if _lit(img, px, py))
        out += _DIGITS.get(segs, _LETTERS.get(segs, ' ' if not segs else '?'))
    return out.strip()


def read_text(img):
    """5x7 dot-matrix reader of the text line: the dots of each character cell matched against the font."""
    f = LCD['fields']['text']
    s = f.get('scale', 2)
    out = ''
    for i in range(f['cells']):
        cols = []
        for cx in range(5):
            b = 0
            for cy in range(7):
                if _lit(img, f['x'] + (i * 6 + cx) * s + s // 2, f['y'] + cy * s + s // 2):
                    b |= 1 << cy
            cols.append(b)
        out += _GLYPHS.get(bytes(cols), '?')
    return out.rstrip()


def _plain(text):
    return ''.join(unicodedata.normalize('NFKD', c)[:1] if c not in pu.FONT5X7 else c for c in text)


def norm(text):
    """What the pixels can tell apart: the lit segments of each cell (S and 5 look the same), and colons."""
    return tuple(ch if ch == ':' else pu.SEG7.get(ch, pu.SEG7.get(ch.upper(), pu.SEG7.get(ch.lower(), ''))) or '_' for ch in text if ch != ' ')


def _box(ic):
    if ic['kind'] == 'text':
        return ic['x'], ic['y'], pu.text_width(ic['text'], ic.get('scale', 2)), 7 * ic.get('scale', 2)
    return ic['x'], ic['y'], ic.get('w', 16), ic.get('h', 16)


def icon_lit(img, iid):
    x, y, w, h = _box(LCD['icons'][iid])
    box = img[y:y + h, x:x + w].astype(int).sum(axis=2)
    return bool((box > 400).any())


# ---------------------------------------------------------------- rig
class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.BinderFd056Runtime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        before = self.h.read('evaluator')
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            assert cap == 'sim.step' or self.h.t == t0, cap                              # gestures take no simulated time
            if cap.startswith('panel.'):                                                 # a gesture changes only what its commands change
                after = self.h.read('evaluator')
                changed = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)}
                assert not changed - set(rt.EFFECTS.get(cap, [])), (cap, sorted(changed - set(rt.EFFECTS.get(cap, []))))
        return r['result'] if expect == 'succeeded' else r

    # gestures (agent session)
    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def keys(self, *seq):
        """Press a sequence of keys ('up', 'down', 'ok', 'back'; 'up*5' repeats); every press must be accepted."""
        for k in seq:
            name, _, times = k.partition('*')
            for _ in range(int(times or 1)):
                assert self.press('btn_' + name), (k, self.screen())

    def standby_key(self):
        return self.press('btn_standby', 3.0)

    # screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lcd(self):
        """(digits, text line) shown now, from the frame and from the pixels (both must agree)."""
        f = self.frame()['displays']['lcd']
        img = self.h.panel.render('lcd')
        digits, text = f['fields']['main']['shown'].strip(), f['fields']['text']['shown'].rstrip()
        assert norm(digits) == norm(read_digits(img)), (digits, read_digits(img))
        assert _plain(text) == read_text(img), (text, read_text(img))                  # accented letters drawn as their base letter
        return digits, text

    def icons(self):
        f = self.frame()['displays']['lcd']
        img = self.h.panel.render('lcd')
        on = {k for k, v in f['icons'].items() if v['shown']}
        assert on == {k for k in LCD['icons'] if icon_lit(img, k)}, (on, {k for k in LCD['icons'] if icon_lit(img, k)})
        return on

    def screen(self):
        return self.frame()['screen']

    def field(self):
        ed = self.frame()['ui']['editor']
        return ed and ed['field']

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def state(self, *ids):
        o = self.E.read(instance_id=self.inst, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def on(self):
        assert self.standby_key()
        assert self.screen() == 'home'

    def set_value(self, current, target):
        """Arrow presses from current to target (one step per press), then OK."""
        d = int(round(target - current))
        if d:
            self.keys(('up' if d > 0 else 'down') + f'*{abs(d)}')
        self.keys('ok')

    def enter_user_menu(self, code=0):
        self.keys('up*5', 'ok')
        assert self.screen() == 'pw_user'
        self.set_value(0, code // 100)
        self.set_value(0, code % 100)


def runtime_state(h):
    r = h.read('evaluator')
    r.pop('clock.time_s')
    return json.dumps(dict(read=r, set_k=h.set_k, safe_k=h.safe_k, flap=h.flap, timer=h.timer, ramp=h.ramp, unit=h.unit, usb={k: v for k, v in h.usb.items()},
                           language=h.language, passwords=h.passwords, clock=h.clock, on=h.on), sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_set_temperature_to_105(self):
        g = Rig()
        self.assertEqual(g.screen(), 'standby')
        self.assertEqual(g.lcd(), ('', ''))
        self.assertEqual(g.icons(), {'standby'})
        g.on()
        self.assertEqual(g.lcd(), ('22', 'Setp      25°C'))                          # Normal display: actual temperature, set-point line
        g.keys('up')                                                                    # Set-point page
        self.assertEqual(g.lcd(), ('25', 'Setp'))
        g.keys('ok')                                                                    # the set-point flashes
        self.assertEqual(g.field(), 'sp')
        self.assertTrue(g.frame()['displays']['lcd']['fields']['main']['blink'])
        g.keys('up*80')                                                                 # one degree per press
        self.assertEqual(g.state('temperature.set_k'), C0 + 25.0)                      # not yet taken
        g.keys('ok')
        self.assertAlmostEqual(g.state('temperature.set_k'), C0 + 105.0, places=9)
        self.assertEqual(g.lcd(), ('105', 'Setp'))
        g.keys('back')
        self.assertEqual(g.screen(), 'home')
        g.step(600.0)
        self.assertEqual('heating' in g.icons(), g.state('indicator.heating'))
        self.assertEqual(g.lcd()[1], 'Setp     105°C')
        DONE_TASKS.add('set temperature to 105 C')

    def test_set_safety_controller_to_110(self):
        g = Rig()
        g.on()
        g.keys('up*4')
        self.assertEqual(g.lcd(), ('300', 'TLim'))                                      # factory value unknown: the maximum (runtime assumption)
        g.keys('ok')
        self.assertEqual(g.field(), 'lim')
        g.keys('down*190', 'ok')
        self.assertAlmostEqual(g.state('safety.limit_k'), C0 + 110.0, places=9)
        self.assertEqual(g.lcd(), ('110', 'TLim'))
        g.keys('back')
        self.assertEqual(g.screen(), 'home')
        DONE_TASKS.add('set safety controller to 110 C')

    def test_set_air_flap_half_open(self):
        g = Rig()
        g.on()
        g.keys('up*2')
        self.assertEqual(g.lcd(), ('CLOS', 'Flap'))
        g.keys('ok', 'up')
        self.assertEqual(g.lcd()[0], '1')
        self.assertEqual(g.state('flap.position'), 0)
        g.keys('up', 'ok')
        self.assertEqual((g.state('flap.position'), g.lcd()), (2, ('2', 'Flap')))
        g.keys('ok', 'up*5', 'ok')                                                     # stops at OPEn
        self.assertEqual((g.state('flap.position'), g.lcd()[0]), (4, 'OPEn'))
        g.keys('ok', 'down*2', 'ok', 'back')
        self.assertEqual(g.state('flap.position'), 2)
        DONE_TASKS.add('set air flap half open')

    def test_delayed_off_timer_2_h_30_min(self):
        g = Rig()
        g.on()
        g.keys('down')                                                                  # no USB stick: the timer page directly
        self.assertEqual(g.lcd(), ('', 'DelayOff'))
        g.keys('ok')
        self.assertEqual((g.field(), g.lcd()), ('days', ('0', 'Days')))
        g.keys('ok')
        self.assertEqual((g.field(), g.lcd()), ('hours', ('0', 'Hours')))
        g.keys('up*2', 'ok')
        self.assertEqual((g.field(), g.lcd()), ('minutes', ('0', 'Minutes')))
        g.keys('up*30', 'ok')
        self.assertEqual((g.field(), g.lcd()), ('fan', ('FOn', 'Fan')))
        self.assertFalse(g.state('timer.state')['running'])                            # nothing taken before the last OK
        g.keys('up', 'ok')                                                              # fan Off after expiry (FOn, FOFF)
        tm = g.state('timer.state')
        self.assertEqual((tm['running'], tm['set_s'], tm['fan_after']), (True, 9000.0, 'off'))
        self.assertEqual(g.lcd(), ('02:30', 'Off     FOFF'))
        self.assertIn('timer', g.icons())
        g.keys('back')
        self.assertEqual(g.lcd()[1], 'Off    02:30')
        g.step(60.0)
        self.assertEqual(g.lcd()[1], 'Off    02:29')
        g.step(9000.0 - 60.0)
        self.assertTrue(g.state('timer.state')['expired'])
        self.assertEqual(g.lcd()[1], 'Off     FOFF')                                   # expired: heating off, fan as selected
        self.assertFalse(g.state('fan.running'))
        g.keys('ok')                                                                    # OK in the Normal display ends the timer function
        self.assertFalse(g.state('timer.state')['expired'])
        self.assertEqual(g.lcd()[1], 'Setp      25°C')
        DONE_TASKS.add("run 'Delayed Off' timer for 2 h 30 min")

    def test_ramp_1_c_per_min_to_40(self):
        g = Rig()
        g.on()
        g.keys('up*3')
        self.assertEqual(g.lcd(), ('', 'Ramp'))
        g.keys('ok')
        self.assertEqual((g.field(), g.lcd()), ('grad', ('0', '/min')))
        g.keys('up', 'ok')
        self.assertEqual((g.field(), g.lcd()), ('target', ('25', 'Setp')))
        g.keys('up*15', 'ok')
        r = g.state('ramp.state')
        self.assertEqual((r['gradient_k_min'], r['target_k'], r['active']), (1, C0 + 40.0, True))
        self.assertEqual(g.lcd(), ('', '/ 1       40'))
        g.keys('back')
        self.assertEqual(g.lcd(), ('22', '/ 1       40'))                              # actual, gradient and target
        g.step(120.0)
        g.keys('up')                                                                    # the set-point page shows the effective ramp set-point
        eff = g.state('ramp.state')['effective_k'] - C0
        self.assertEqual(g.lcd()[0], str(int(round(eff))))
        self.assertGreater(eff, 25.0)
        g.keys('ok', 'up*10', 'ok')                                                     # and sets the target ramp set-point
        self.assertEqual((g.state('ramp.state')['target_k'], g.state('ramp.state')['gradient_k_min']), (C0 + 50.0, 1))
        g.keys('back')
        self.assertEqual(g.lcd()[1], '/ 1       50')
        g.keys('up*3', 'ok', 'down', 'ok', 'ok')                                         # gradient 0: ramp off
        self.assertEqual(g.state('ramp.state')['gradient_k_min'], 0)
        self.assertEqual(g.lcd(), ('', 'Ramp'))
        DONE_TASKS.add('program a ramp of 1 C/min to 40 C')

    def test_reset_a_safety_controller_alarm(self):
        g = Rig(ambient_k=C0 + 22.0)
        g.on()
        g.keys('up*4', 'ok', 'down*260', 'ok', 'back')                                  # safety controller 40 C
        g.keys('up', 'ok', 'up*35', 'ok', 'back')                                       # set-point 60 C
        for _ in range(600):
            g.step(1.0)
            if g.state('alarm.state'):
                break
        self.assertEqual(g.state('alarm.state'), 'limit_active')                        # the air overshoots 40 C: heating off
        self.assertEqual(g.lcd()[1], '!TLim     40°C')
        self.assertTrue(g.frame()['displays']['lcd']['icons']['alarm']['blink'])       # the alarm icon flashes
        g.keys('up*4', 'ok')                                                            # still above the limit: OK edits, no reset
        self.assertEqual((g.field(), g.state('alarm.state')), ('lim', 'limit_active'))
        g.keys('back', 'back')
        for _ in range(240):
            g.step(30.0)
            if g.state('alarm.state') == 'limit_latched':
                break
        self.assertEqual(g.state('alarm.state'), 'limit_latched')
        self.assertFalse(g.frame()['displays']['lcd']['icons']['alarm']['blink'])      # cooled down: steady icon
        self.assertIn('alarm', g.icons())
        g.keys('up*4', 'ok')                                                            # reset in the safety controller menu with OK
        self.assertIsNone(g.state('alarm.state'))
        self.assertEqual(g.screen(), 'tlim')
        self.assertIsNone(g.field())
        g.keys('back')
        self.assertEqual(g.lcd()[1], 'Setp      60°C')
        DONE_TASKS.add('reset a safety controller alarm')

    def test_start_usb_recording(self):
        g = Rig()
        g.on()
        g.call(g.A, 'usb.set_stick', inserted=True)
        g.keys('down')                                                                  # with a stick the USB page comes first
        self.assertEqual(g.lcd(), ('StoP', 'USB'))
        g.keys('ok')                                                                    # no clock option: the date is shown first
        self.assertEqual((g.screen(), g.lcd()), ('usb_date', ('00:00', '01 Jan2000')))
        g.keys('ok')
        self.assertEqual(g.lcd(), ('2000', 'Year'))
        g.keys('up*26', 'ok', 'up*9', 'ok', 'up*17', 'ok', 'up*9', 'ok')                # 2026-10-18 09
        self.assertEqual(g.lcd(), ('0', 'Minute'))
        g.keys('up*30', 'ok')                                                           # after the minute the clock is set and USB continues
        self.assertEqual(g.state('controller.datetime'), '2026-10-18T09:30')
        self.assertEqual(g.lcd(), ('1', 'Interval'))
        g.keys('up', 'ok')
        self.assertEqual(g.lcd(), ('StoP', 'Record'))
        g.keys('up')
        self.assertEqual(g.lcd()[0], 'Strt')
        self.assertFalse(g.state('usb.state')['recording'])
        g.keys('ok')
        self.assertEqual((g.screen(), g.lcd()), ('usb', ('run', 'USB')))
        u = g.state('usb.state')
        self.assertEqual((u['recording'], u['interval_s']), (True, 120.0))
        g.step(250.0)
        rows = g.call(g.A, 'usb.read')['rows']
        self.assertEqual([r['datetime'] for r in rows], ['2026-10-18T09:30', '2026-10-18T09:32', '2026-10-18T09:34'])
        g.keys('ok')                                                                    # stopping goes straight to the state
        self.assertEqual(g.lcd(), ('run', 'Record'))
        g.keys('down', 'ok')
        self.assertEqual((g.state('usb.state')['recording'], g.lcd()), (False, ('StoP', 'USB')))
        g.keys('down')
        self.assertEqual(g.screen(), 'timer')
        g.keys('up', 'up')
        self.assertEqual(g.screen(), 'home')
        DONE_TASKS.add('start USB recording')


# ---------------------------------------------------------------- user menu and passwords
class UserMenu(unittest.TestCase):
    def test_password_l1_date_unit_language(self):
        g = Rig()
        g.on()
        g.keys('up*5')
        self.assertEqual(g.lcd(), ('', 'User'))
        g.keys('ok')
        self.assertEqual(g.lcd(), ('0000', 'Password'))
        self.assertEqual(g.frame()['displays']['lcd']['fields']['main']['blink_mask'], '1100')   # the left two digits flash first
        g.keys('ok')
        self.assertEqual(g.frame()['displays']['lcd']['fields']['main']['blink_mask'], '0011')
        g.keys('ok')
        self.assertEqual((g.screen(), g.lcd()), ('u_date', ('00:00', '01 Jan2000')))
        g.keys('ok', 'up*26', 'ok', 'up*6', 'ok', 'up*13', 'ok', 'up*10', 'ok', 'up*54', 'ok')
        self.assertEqual(g.state('controller.datetime'), '2026-07-14T10:54')
        self.assertEqual(g.lcd(), ('10:54', '14 Jul2026'))                              # as drawn on p.30
        g.keys('up')
        self.assertEqual(g.lcd(), ('°C', 'Unit'))
        g.keys('ok', 'up', 'ok')
        self.assertEqual((g.state('unit'), g.lcd()), ('F', ('°F', 'Unit')))
        g.keys('up*2')                                                                  # 4x from the date page: language (the chain ends there)
        self.assertFalse(g.press('btn_up'))
        self.assertEqual(g.lcd(), ('EnG', 'Language'))
        g.keys('ok', 'down', 'ok')
        self.assertEqual((g.state('controller.language'), g.lcd()[0]), ('de', 'GEr'))
        g.keys('back', 'back')                                                          # 2x Back to the Normal display
        self.assertEqual(g.screen(), 'home')
        self.assertEqual(g.lcd(), ('72', 'Soll      77°F'))                             # German label, Fahrenheit values
        self.assertEqual(g.icons(), {'unit_f'})
        g.keys('up*4')
        self.assertEqual(g.lcd(), ('572', 'TWB'))
        g.keys('back')
        g.call(g.A, 'language.set', language='fr')
        self.assertEqual(g.lcd()[1], 'Cons      77°F')

    def test_change_passwords_and_l2_protection(self):
        g = Rig()
        g.on()
        g.enter_user_menu()
        g.keys('up*2')
        self.assertEqual(g.lcd(), ('L 1', 'Password'))
        g.keys('ok', 'up')                                                              # choose L2
        self.assertEqual(g.lcd()[0], 'L 2')
        g.keys('ok')                                                                    # current L2 password, left pair flashing
        self.assertEqual((g.field(), g.lcd()[0]), ('hi', '0000'))
        g.keys('up*12', 'ok', 'up*34', 'ok')
        self.assertEqual(g.h.passwords['L2'], 1234)
        self.assertEqual(g.state('access.protection'), {'L1': False, 'L2': True})
        self.assertEqual(g.lcd(), ('L 2', 'Password'))
        g.keys('back', 'back', 'up', 'ok')                                              # set-point page: L2 asked first
        self.assertEqual((g.screen(), g.lcd()), ('pw_l2', ('0000', 'Password')))
        g.keys('ok', 'ok')                                                              # wrong password: back, still locked
        self.assertEqual((g.screen(), g.field()), ('setp', None))
        g.keys('ok')
        self.assertEqual(g.screen(), 'pw_l2')
        g.set_value(0, 12)
        g.set_value(0, 34)
        self.assertEqual((g.screen(), g.field()), ('setp', None))
        g.keys('ok')                                                                    # unlocked: OK edits
        self.assertEqual(g.field(), 'sp')
        g.keys('up*5', 'ok', 'up', 'ok')                                                # still unlocked on the next page
        self.assertEqual((g.state('temperature.set_k'), g.field()), (C0 + 30.0, 'pos'))
        g.keys('back', 'back')
        g.keys('up', 'ok')                                                              # the Normal display locks again
        self.assertEqual(g.screen(), 'pw_l2')
        g.keys('back')
        self.assertEqual(g.screen(), 'setp')

    def test_password_l1_protects_the_user_menu(self):
        g = Rig()
        g.on()
        g.call(g.A, 'access.set_password', level='L1', code=4711)
        g.enter_user_menu(0)
        self.assertEqual(g.screen(), 'user')                                           # wrong password: no access
        g.keys('back')
        g.enter_user_menu(4711)
        self.assertEqual(g.screen(), 'u_date')
        g.keys('up*2', 'ok', 'ok')                                                      # L1: shows 4711, flashing left pair
        self.assertEqual(g.lcd()[0], '4711')
        g.keys('down*47', 'ok', 'down*11', 'ok')
        self.assertEqual(g.state('access.protection'), {'L1': False, 'L2': False})


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def pair(self):
        a, b = Rig(seed=3), Rig(seed=3)
        a.on()
        b.call(b.A, 'power.set', on=True)
        return a, b

    def test_pages(self):
        a, b = self.pair()
        a.keys('up', 'ok', 'up*80', 'ok', 'up', 'ok', 'up*2', 'ok', 'up', 'up', 'ok', 'down*190', 'ok', 'back')
        a.keys('down', 'ok', 'up', 'ok', 'up*3', 'ok', 'up*7', 'ok', 'ok', 'back')
        b.call(b.A, 'temperature.set', temperature_k=C0 + 105.0)
        b.call(b.A, 'flap.set', position=2)
        b.call(b.A, 'safety.set_limit', limit_k=C0 + 110.0)
        b.call(b.A, 'timer.set', days=1, hours=3, minutes=7, fan_after='on')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.keys('up*3', 'ok', 'up*2', 'ok', 'down*65', 'ok', 'back')
        b.call(b.A, 'ramp.set', gradient_k_min=2, target_k=C0 + 40.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.step(300.0)
        b.step(300.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))

    def test_user_menu_and_standby(self):
        a, b = self.pair()
        a.enter_user_menu()
        a.keys('ok', 'up*20', 'ok', 'up*2', 'ok', 'up*4', 'ok', 'up*8', 'ok', 'up*15', 'ok')
        a.keys('up', 'ok', 'up', 'ok', 'up', 'ok', 'ok', 'up', 'ok', 'up*2', 'ok', 'up', 'ok', 'up', 'ok', 'back', 'back')
        b.call(b.A, 'clock.set', year=2020, month=3, day=5, hour=8, minute=15)
        b.call(b.A, 'unit.set', unit='F')
        b.call(b.A, 'access.set_password', level='L1', code=102)
        b.call(b.A, 'language.set', language='fr')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        self.assertTrue(a.standby_key())
        b.call(b.A, 'power.set', on=False)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        self.assertEqual(a.screen(), 'standby')


# ---------------------------------------------------------------- refusals, entries, time-outs, special displays
class Rules(unittest.TestCase):
    def test_standby_and_no_mains_refuse_keys(self):
        g = Rig()
        before = runtime_state(g.h)
        for key in ('btn_up', 'btn_down', 'btn_ok', 'btn_back'):
            self.assertFalse(g.press(key))
        self.assertFalse(g.press('btn_standby'))                                       # short press: nothing
        self.assertEqual(runtime_state(g.h), before)
        self.assertTrue(g.standby_key())
        g.keys('up', 'ok')
        self.assertTrue(g.standby_key())                                                # held from a page while editing: standby
        self.assertEqual((g.screen(), g.state('power.on'), g.lcd(), g.icons()), ('standby', False, ('', ''), {'standby'}))
        self.assertTrue(g.standby_key())
        self.assertEqual(g.screen(), 'home')                                            # back on the Normal display (menus left)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        self.assertEqual(g.screen(), 'off')
        img = g.h.panel.render('lcd')
        self.assertFalse((img.astype(int).sum(axis=2) > 400).any())                     # dark
        before = runtime_state(g.h)
        for key in ('btn_up', 'btn_ok', 'btn_back'):
            self.assertFalse(g.press(key))
        self.assertFalse(g.standby_key())
        self.assertEqual(runtime_state(g.h), before)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        self.assertEqual((g.screen(), g.lcd()[1]), ('home', 'Setp      25°C'))         # same status as before the failure

    def test_discarded_entries_and_impossible_date(self):
        g = Rig()
        g.on()
        g.keys('down', 'ok', 'up', 'ok', 'up*3', 'back')                                # timer: Back before all are confirmed
        self.assertFalse(g.state('timer.state')['set'])
        self.assertEqual((g.screen(), g.field()), ('timer', None))
        g.keys('back', 'up', 'ok', 'up*9', 'back')
        self.assertEqual(g.state('temperature.set_k'), C0 + 25.0)
        g.keys('back')
        g.enter_user_menu()
        g.keys('ok', 'up*26', 'ok', 'up', 'ok', 'up*30', 'ok', 'ok')                    # 31 Feb 2026 is not taken
        self.assertFalse(g.press('btn_ok'))
        self.assertEqual(g.state('controller.datetime'), '2000-01-01T00:00')
        self.assertEqual((g.screen(), g.field()), ('u_date', None))
        g.keys('ok', 'ok', 'up', 'ok', 'up*28', 'ok', 'ok', 'ok')                         # 29 Feb 2000 exists
        self.assertEqual(g.state('controller.datetime')[:10], '2000-02-29')

    def test_values_stop_at_limits_and_flash(self):
        g = Rig()
        g.on()
        g.keys('up', 'ok', 'up*300', 'ok')
        self.assertEqual(g.state('temperature.set_k'), C0 + 300.0)
        g.keys('ok', 'down*400', 'ok')
        self.assertEqual(g.state('temperature.set_k'), C0)
        g.keys('ok')
        self.assertEqual(g.lcd()[0], '0')
        g.step(0.6)                                                                     # flashing: off for half the 1 s period
        self.assertEqual(g.frame()['displays']['lcd']['fields']['main']['shown'].strip(), '')
        self.assertEqual(read_digits(g.h.panel.render('lcd')), '')
        g.step(0.5)
        self.assertEqual(g.lcd()[0], '0')

    def test_no_menu_timeout(self):
        g = Rig()
        g.on()
        g.keys('up*2', 'ok')
        g.step(3600.0)
        self.assertEqual((g.screen(), g.field()), ('flap', 'pos'))

    def test_page_chains_and_stick_removal(self):
        g = Rig()
        g.on()
        g.keys('up*5')
        self.assertFalse(g.press('btn_up'))                                            # end of the chain
        g.keys('down*5')
        self.assertEqual(g.screen(), 'home')
        g.keys('down')
        self.assertEqual(g.screen(), 'timer')
        self.assertFalse(g.press('btn_down'))
        g.keys('up')
        self.assertEqual(g.screen(), 'home')
        self.assertFalse(g.press('btn_back'))                                          # nothing above the Normal display
        g.call(g.A, 'usb.set_stick', inserted=True)
        g.keys('down', 'ok')
        self.assertEqual(g.screen(), 'usb_date')
        g.call(g.A, 'usb.set_stick', inserted=False)                                    # the stick removed: the USB pages leave
        self.assertEqual(g.screen(), 'home')

    def test_overtemperature_sensor_rupture_and_unit_conversion(self):
        g = Rig()
        g.on()
        g.call(g.E, 'fault.inject', fault='sensor_rupture', active=True)
        g.step(1.0)
        self.assertEqual(g.lcd()[0], '1999')
        g.call(g.E, 'fault.inject', fault='sensor_rupture', active=False)
        g.h.class1 = True                                                               # class 1 device tripped (state as the runtime sets it)
        self.assertEqual(g.lcd()[1], 'Overtemperature')
        self.assertIn('alarm', g.icons())

    def test_rejected_and_unknown_gestures(self):
        g = Rig()
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='btn_nowhere')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')                          # the structured frame is for the evaluator only
        h = rt.BinderFd056Runtime('x')
        h.register_principal('a', 'agent')
        r = h.session('a').invoke(instance_id='x', capability_id='panel.press', arguments=dict(input_id='btn_ok'), request_id='1')
        self.assertEqual(r['error']['code'], 'PLANT_REQUIRED')


# ---------------------------------------------------------------- renders, capture, contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig(seed=5)
            g.on()
            g.keys('up', 'ok', 'up*80', 'ok', 'back')
            g.keys('down', 'ok', 'ok', 'up*2', 'ok', 'up*30', 'ok', 'ok', 'back')
            g.step(300.0)
            return g
        a, b = scenario(), scenario()
        ia, ib = a.h.panel.render('lcd'), b.h.panel.render('lcd')
        self.assertEqual((ia.dtype.name, ia.shape), ('uint8', (124, 330, 3)))
        self.assertTrue((ia == ib).all())
        self.assertTrue((ia == a.h.panel.render('lcd')).all())
        self.assertEqual(a.h.panel.png_bytes('lcd'), b.h.panel.png_bytes('lcd'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('lcd', OUT / 'home.png')}
        self.assertEqual(a.lcd()[1], 'Off    02:25')
        a.keys('up*2', 'ok', 'up*2')
        shots['menu'] = a.h.panel.save_png('lcd', OUT / 'menu.png')                     # air flap page, the new position flashing
        self.assertEqual(a.lcd(), ('2', 'Flap'))
        a.keys('ok', 'back')
        a.enter_user_menu()
        shots['user_menu'] = a.h.panel.save_png('lcd', OUT / 'user_menu.png')
        a.keys('back', 'back')
        a.keys('up*4', 'ok', 'down*265', 'ok', 'back')                                  # safety controller 35 C below the chamber: !TLim
        a.step(1.0)
        self.assertEqual(a.state('alarm.state'), 'limit_active')
        shots['error'] = a.h.panel.save_png('lcd', OUT / 'error.png')
        self.assertEqual(a.lcd()[1], '!TLim     35°C')
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
        g.call(g.A, 'usb.record', expect='rejected', on=True)                          # a rejected call restores the snapshot
        self.assertIs(g.h.panel.host, g.h)
        g.keys('up', 'ok', 'up')
        c = copy.deepcopy(g.h)                                                         # a copied runtime gets its own panel
        self.assertIs(c.panel.host, c)
        c.panel.press('btn_ok')
        self.assertNotEqual(c.set_k, g.h.set_k)

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.capture', 'panel.frame'})
        for sid, s in rt.PANEL_SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        m = rt.export_binder_fd056_contract_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in list(rt.PANEL_CAPS) + ['clock.set', 'language.set', 'access.set_password']:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'parameter_cycle', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/binder_fd056.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])                    # the shared engine is not pinned per asset
        spec = importlib.util.spec_from_file_location('fd056_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
