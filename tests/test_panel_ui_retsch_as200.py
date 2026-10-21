"""front-panel tests for CHEM-041 retsch_as200 (panel_ui.json + scripts/asset_runtime/_panel_ui.py + the 1.1.0 key functions of the runtime):
python3 -m pytest tests/test_panel_ui_retsch_as200.py -q

Every gesture goes through the session API (panel.press / panel.switch, the runtime's normal invoke path).  Each check reads the runtime state
and the screen twice: the structured frame (panel.frame) and the rendered pixels, read back with a seven-segment reader that knows only the
display geometry of the spec, plus the LED colours.  Themes: the seven key procedures of the menu survey; time / continuous, interval,
programmes and their key locks, standby, signal tone, operating hours, software version, calibration reminder and CAL, date after dA, a new
run after the end; panel = direct capability; refusals; time-outs; render determinism; evidence PNGs; contract entries."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import retsch_as200 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'as200_01'
OUT = ROOT / 'outputs/panel_ui/retsch_as200'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['run a manual sieving', 'pause and continue', 'switch to interval operation', 'store a programme', 'run a stored programme',
                'show amplitude in g instead of mm', "set the date after 'dA'"]
DONE_TASKS = set()
KEY_SEEN = set()
DISPLAYS = {k: rt.PANEL_SPEC['displays'][k] for k in ('time', 'amplitude', 'interval', 'memory')}   # one seven-segment display per window of Fig. 9
#  (2026-10-21; until then one 'panel' image held all four fields)
_DIGITS = {v: k for k, v in pu.SEG7.items() if k in '0123456789'}
_LETTERS = {v: k for k, v in pu.SEG7.items() if k not in '0123456789' and v}


def _lit(img, x, y):
    px = img[int(round(y)), int(round(x))].astype(int)
    return px[0] + px[1] > 300 and px[0] > 150                                  # yellow segment, not the dark ghost


def read_field(img, fid):
    """Seven-segment reader: sample the middle of each segment of each cell (geometry from the spec only)."""
    f = DISPLAYS[fid]['fields'][fid]
    n, cw, ch, gap = f['cells'], f['cell_w'], f['cell_h'], f.get('gap', 6)
    t = f.get('thickness', max(2.0, cw * 0.18))
    out = ''
    for i in range(n):
        x = f['x'] + i * (cw + gap)
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2),
                   g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if _lit(img, px, py))
        out += _DIGITS.get(segs, _LETTERS.get(segs, ' ' if not segs else '?'))
        r = max(1.5, t * 0.6)
        if _lit(img, x + cw + gap / 2.0, f['y'] + ch - r):
            out += '.'
    return out.strip()


def save_panel_png(panel, path):
    """Evidence image: the four window displays where they sit on the panel (model window centres at 4 px/mm: interval / memory in the lower row,
    time / amplitude 28.5 mm higher, 41-42 mm apart) on a dark fascia; returns dict(path, format, width, height, sha256) like save_png."""
    import io
    from PIL import Image
    at = {'interval': (8, 122), 'memory': (173, 122), 'time': (338, 8), 'amplitude': (497, 8)}
    img = Image.new('RGB', (629, 188), (40, 40, 44))
    for k, xy in at.items():
        img.paste(panel.render_image(k), xy)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=False)
    data = buf.getvalue()
    Path(path).write_bytes(data)
    return dict(path=str(path), format='png', width=img.size[0], height=img.size[1], sha256=hashlib.sha256(data).hexdigest())


def norm(text):
    return tuple(ch if ch == '.' else pu.SEG7.get(ch, pu.SEG7.get(ch.upper(), pu.SEG7.get(ch.lower(), ''))) or '_' for ch in text)


class Rig:
    def __init__(self, power=True, clamp=True):
        self.h = rt.RetschAs200Runtime(INST)
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
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        self.call(self.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, duration_s=300.0, interval_enabled=False))
        self.call(self.E, 'sample.configure', **rt.example_sample())
        if clamp:
            self.call(self.A, 'cover.set', lift_m=0.0)
            self.h.step(1.5)
            self.call(self.E, 'mount.configure', clamped=True)
        if power:
            assert self.switch('on')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'a{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def step(self, s):
        t = 0.0
        while t < s - 1e-9:
            d = min(86400.0, s - t)
            self.h.step(d)
            t += d

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def shown(self):
        """Fields shown now, from the frame and from the pixels (both must agree)."""
        f = self.frame()['displays']
        shown = {k: v['shown'].strip() for d in DISPLAYS for k, v in f[d]['fields'].items() if v['shown'].strip()}
        read = {k: read_field(self.h.panel.render(k), k) for k in DISPLAYS}
        read = {k: v for k, v in read.items() if v}
        assert {k: norm(v) for k, v in shown.items()} == {k: norm(v) for k, v in read.items()}, (shown, read)
        return shown

    def leds(self):
        f = self.frame()['displays']
        out = {}
        for k in ('led_stop', 'led_start', 'led_pause', 'led_interval'):
            img = self.h.panel.render(k)
            lit = tuple(int(x) for x in img[12, 12]) == tuple(rt.PANEL_SPEC['displays'][k]['colors']['on'])
            assert lit == f[k]['shown'], k
            out[k] = 'blink' if f[k]['blink'] and f[k]['on'] else f[k]['on']
        return out

    def press(self, key, hold=0.1, held=None):
        args = dict(input_id=key, duration_s=hold)
        if held:
            args['held'] = held
        return self.call(self.A, 'panel.press', **args)['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='main_switch', position=pos)['accepted']


class SurveyProcedures(unittest.TestCase):
    def test_run_a_manual_sieving(self):
        r = Rig(power=False)
        self.assertEqual(r.screen(), 'off')
        self.assertTrue(r.switch('on'))
        self.assertEqual(r.screen(), 'setting')
        self.assertEqual(r.shown(), {'memory': 'on', 'time': '05', 'amplitude': '1.20'})          # last values, memory "on"
        self.assertEqual(r.leds(), {'led_stop': True, 'led_start': False, 'led_pause': False, 'led_interval': False})
        for _ in range(5):
            r.press('key_amp_plus')
        self.assertAlmostEqual(r.val('sieving.target_total_lift_m'), 0.00125)
        r.press('key_amp_plus', hold=1.5)                                          # press and hold: 0.1 mm
        self.assertEqual(r.shown()['amplitude'], '1.35')
        r.press('key_time_minus')
        r.press('key_time_minus')
        self.assertEqual(r.shown()['time'], '03')
        self.assertTrue(r.press('key_start'))
        self.assertEqual(r.val('cycle.phase'), 'running')
        self.assertEqual(r.leds()['led_start'], True)
        self.assertFalse(r.leds()['led_stop'])
        r.step(61)
        self.assertEqual(r.shown()['time'], '02')                                  # counts down
        r.step(125)
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        self.assertAlmostEqual(r.val('cycle.elapsed_s'), 180.0)
        self.assertEqual(r.val('buzzer.end_signals'), 1)                           # end of sieving announced
        self.assertTrue(r.leds()['led_stop'])
        r.press('key_start')                                                       # a new sieving with the set time
        self.assertEqual(r.val('cycle.phase'), 'running')
        r.step(10)
        self.assertTrue(r.press('key_stop'))
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        r.step(2)
        self.assertTrue(r.press('key_stop'))                                       # STOP again: standby
        self.assertEqual(r.screen(), 'standby')
        self.assertEqual(r.shown(), {})
        self.assertEqual(r.leds()['led_stop'], True)
        self.assertFalse(r.press('key_amp_plus'))                                  # only START works in standby
        self.assertTrue(r.press('key_start'))
        self.assertEqual((r.screen(), r.val('cycle.phase')), ('setting', 'cancelled'))
        self.assertTrue(r.press('key_start'))                                      # START twice from standby
        self.assertEqual(r.val('cycle.phase'), 'running')
        DONE_TASKS.add('run a manual sieving')

    def test_pause_and_continue(self):
        r = Rig()
        r.press('key_start')
        r.step(20)
        self.assertTrue(r.press('key_pause'))
        self.assertEqual(r.val('cycle.mode'), 'paused')
        self.assertEqual(r.leds()['led_pause'], 'blink')
        self.assertFalse(r.leds()['led_start'])
        r.step(30)
        self.assertAlmostEqual(r.val('cycle.elapsed_s'), 20.0)                    # time stopped
        self.assertTrue(r.press('key_start'))
        self.assertEqual(r.val('cycle.mode'), 'sieving')
        r.step(10)
        self.assertAlmostEqual(r.val('cycle.elapsed_s'), 30.0)
        r.press('key_pause')
        self.assertTrue(r.press('key_stop'))                                       # or end it
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        DONE_TASKS.add('pause and continue')

    def test_switch_to_interval_operation(self):
        r = Rig()
        self.assertNotIn('interval', r.shown())                                    # interval display only with interval on
        self.assertFalse(r.press('key_int_plus'))
        self.assertTrue(r.press('key_interval'))
        self.assertTrue(r.val('method.configured'))
        self.assertTrue(r.leds()['led_interval'])
        self.assertEqual(r.shown()['interval'], '03')
        r.press('key_int_plus')
        r.press('key_int_plus', hold=1.2)
        self.assertEqual(r.shown()['interval'], '14')
        for _ in range(14):
            r.press('key_int_minus')
        self.assertEqual(r.shown()['interval'], '99')                               # below 1: 99
        r.press('key_int_plus')
        self.assertEqual(r.shown()['interval'], '01')
        for _ in range(4):
            r.press('key_int_plus')
        r.press('key_start')
        r.step(6.5)
        self.assertAlmostEqual(r.val('cycle.driven_s'), 5.5, places=6)             # 5 s sieving, 1 s pause
        r.press('key_interval')                                                    # back to continuous while sieving
        self.assertNotIn('interval', r.shown())
        r.step(5)
        self.assertAlmostEqual(r.val('cycle.driven_s'), 10.5, places=6)
        DONE_TASKS.add('switch to interval operation')

    def test_store_a_programme(self):
        r = Rig()
        self.assertTrue(r.press('key_prog'))
        self.assertEqual(r.screen(), 'program')
        self.assertEqual(r.shown()['memory'], '01')
        r.press('key_prog')
        r.press('key_prog', hold=1.5)
        self.assertEqual(r.shown()['memory'], '12')                                 # 01 -> 02 -> +10
        self.assertFalse(r.press('key_amp_plus'))                                   # locked with a programme selected
        self.assertTrue(r.press('key_set'))
        self.assertEqual(r.screen(), 'program_edit')
        self.assertTrue(all(v['blink'] for d in DISPLAYS for v in r.frame()['displays'][d]['fields'].values() if v['text']))
        r.press('key_amp_plus', hold=1.5)
        r.press('key_time_plus')
        r.press('key_interval')
        self.assertTrue(r.press('key_set'))
        self.assertEqual(r.screen(), 'program')
        store = r.val('program.store')
        self.assertAlmostEqual(store[0].pop('total_lift_m'), 0.0013, places=12)
        self.assertEqual(store, [dict(slot=12, minutes=6, interval_enabled=True, interval_drive_s=3.0)])
        r.press('key_set')                                                          # edit again and discard with Pause
        r.press('key_time_plus')
        self.assertEqual(r.frame()['displays']['time']['fields']['time']['text'], '07')   # flashing: the text, whatever the phase
        self.assertTrue(r.press('key_pause'))
        self.assertEqual((r.screen(), r.shown()['time']), ('program', '06'))
        self.assertTrue(r.press('key_pause'))                                       # Pause leaves programme mode
        self.assertEqual((r.screen(), r.shown()['memory']), ('setting', 'on'))
        DONE_TASKS.add('store a programme')

    def test_run_a_stored_programme(self):
        r = Rig()
        r.call(r.A, 'sieving.set_time', minutes=2)
        r.call(r.A, 'program.save', slot=7)
        r.call(r.A, 'sieving.set_time', minutes=9)
        r.call(r.A, 'program.select', slot=7)
        r.call(r.A, 'program.select', slot=0)
        r.press('key_prog')                                                          # the last selected programme
        self.assertEqual(r.shown(), {'memory': '07', 'time': '02', 'amplitude': '1.20'})
        self.assertTrue(r.press('key_start'))
        self.assertEqual(r.val('cycle.phase'), 'running')
        self.assertFalse(r.press('key_prog'))                                       # while a programme runs only STOP and Pause
        self.assertFalse(r.press('key_time_plus'))
        self.assertTrue(r.press('key_pause'))
        self.assertEqual(r.val('cycle.mode'), 'paused')
        r.press('key_start')
        r.step(125)
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        r.press('key_pause')
        self.assertEqual(r.screen(), 'setting')
        DONE_TASKS.add('run a stored programme')

    def test_show_amplitude_in_g(self):
        r = Rig()
        self.assertTrue(r.press('key_amp_minus', held=['key_amp_plus']))
        self.assertEqual(r.val('panel.amplitude_unit'), 'g')
        g = (2 * 3.141592653589793 * 47.5) ** 2 * 0.0012 / 2 / 9.81
        self.assertEqual(r.shown()['amplitude'], f'{g:.1f}')
        r.press('key_amp_plus')
        self.assertAlmostEqual(r.h._values()['sieving.target_total_lift_m'] * (2 * 3.141592653589793 * 47.5) ** 2 / 2 / 9.81, round(g, 1) + 0.1, places=6)
        for _ in range(60):
            r.press('key_amp_plus')
        self.assertLessEqual(r.val('sieving.target_total_lift_m'), 0.003)           # the 3 mm stroke is the limit
        r.press('key_amp_plus', held=['key_amp_minus'])
        self.assertEqual(r.val('panel.amplitude_unit'), 'mm')
        self.assertEqual(r.shown()['amplitude'][0], '2')
        DONE_TASKS.add('show amplitude in g instead of mm')

    def test_set_the_date_after_da(self):
        r = Rig()
        r.switch('off')
        r.step(31 * 86400)
        r.switch('on')
        self.assertTrue(r.val('clock.date_lost'))
        self.assertEqual(r.screen(), 'date_lost')
        self.assertEqual(r.shown(), {'amplitude': 'dA'})
        self.assertFalse(r.press('key_time_plus'))
        self.assertFalse(r.press('key_start'))                                     # 1.2.0: the manual table draws the set key (p.42)
        self.assertTrue(r.press('key_set'))
        self.assertEqual(r.shown(), {'time': '01', 'amplitude': '-00'})
        for _ in range(3):
            r.press('key_time_plus')
        for _ in range(26):
            r.press('key_amp_plus')
        self.assertEqual(r.shown(), {'time': '04', 'amplitude': '-26'})
        self.assertTrue(r.press('key_stop'))
        self.assertEqual(r.val('clock.date'), '2026-04')
        self.assertFalse(r.val('clock.date_lost'))
        self.assertEqual(r.screen(), 'setting')
        DONE_TASKS.add("set the date after 'dA'")


class Functions(unittest.TestCase):
    def test_time_keys_and_continuous_operation(self):
        r = Rig()
        r.press('key_time_plus', hold=1.5)
        self.assertEqual(r.shown()['time'], '15')
        for _ in range(14):
            r.press('key_time_minus')
        self.assertEqual(r.shown()['time'], '01')
        r.press('key_time_minus')
        self.assertEqual(r.shown()['time'], '--')                                   # continuous
        self.assertTrue(r.val('sieving.continuous'))
        r.press('key_start')
        r.step(7000)
        self.assertEqual(r.val('cycle.phase'), 'running')                           # until STOP
        r.press('key_time_plus')                                                    # time can be changed during operation
        self.assertEqual(r.shown()['time'], '01')
        r.step(60.5)
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        r.call(r.A, 'sieving.set_time', minutes=99)
        r.press('key_time_plus')
        self.assertEqual(r.shown()['time'], '--')

    def test_signal_tone_hours_version_and_locks(self):
        r = Rig()
        self.assertTrue(r.press('key_stop', held=['key_interval']))
        self.assertFalse(r.val('panel.signal_tone'))
        self.assertEqual(r.frame()['ui']['beeps'], 1)
        r.call(r.A, 'sieving.set_time', minutes=1)
        r.press('key_start')
        r.step(61)
        self.assertEqual(r.val('buzzer.end_signals'), 0)                           # tone off
        self.assertTrue(r.press('key_start', held=['key_interval']))
        self.assertTrue(r.val('panel.signal_tone'))
        self.assertEqual(r.frame()['ui']['beeps'], 3)
        r.h.hours_s = 123.4 * 3600
        self.assertTrue(r.press('key_time_minus', held=['key_pause']))
        self.assertEqual((r.screen(), r.shown()), ('hours', {'time': 'bS', 'amplitude': '123'}))
        self.assertFalse(r.press('key_start'))                                     # all keys but STOP locked
        r.press('key_stop')
        self.assertTrue(r.press('key_pause', held=['key_time_plus']))
        self.assertEqual(r.shown(), {'time': 'S', 'amplitude': '1.00'})
        r.press('key_stop')
        self.assertEqual(r.screen(), 'setting')

    def test_calibration_reminder_and_cal(self):
        r = Rig()
        self.assertTrue(r.press('key_interval', hold=5.0))
        self.assertEqual((r.screen(), r.shown()), ('reminder', {'time': '01', 'amplitude': '-27'}))
        r.press('key_time_minus')
        r.press('key_amp_minus')
        self.assertEqual(r.shown(), {'time': '12', 'amplitude': '-26'})
        for _ in range(9):
            r.press('key_time_minus')
        r.press('key_stop')
        self.assertEqual(r.val('calibration.reminder'), '2026-03')
        r.step(80 * 86400)                                                          # the device clock reaches March 2026
        r.switch('off')
        r.switch('on')
        self.assertEqual((r.screen(), r.shown()['amplitude']), ('cal', 'CAL'))
        r.step(2.9)
        self.assertEqual(r.screen(), 'cal')
        r.step(0.2)
        self.assertEqual(r.screen(), 'setting')                                     # about three seconds
        r.switch('off')
        r.switch('on')
        self.assertTrue(r.press('key_stop'))
        self.assertEqual(r.screen(), 'setting')


class Refusals(unittest.TestCase):
    def test_off_unclamped_limits_and_invalid(self):
        r = Rig(power=False)
        self.assertFalse(r.press('key_start'))
        self.assertEqual(r.leds(), {'led_stop': False, 'led_start': False, 'led_pause': False, 'led_interval': False})
        u = Rig(clamp=False)
        self.assertFalse(u.press('key_start'))                                     # sieve stack not clamped: the runtime refuses
        self.assertEqual(u.val('cycle.phase'), 'idle')
        self.assertEqual(u.frame()['ui']['last_gesture']['rejection'], 'PRECONDITION_FAILED')
        r.switch('on')
        r.call(r.A, 'sieving.set_lift', total_lift_m=0.003)
        r.press('key_amp_plus')
        self.assertAlmostEqual(r.val('sieving.target_total_lift_m'), 0.003)
        r.call(r.A, 'sieving.set_lift', total_lift_m=0.0002)
        r.press('key_amp_minus', hold=2)
        self.assertAlmostEqual(r.val('sieving.target_total_lift_m'), 0.0002)
        self.assertEqual(r.call(r.A, 'panel.press', expect='rejected', input_id='key_menu')['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        r.press('key_start')
        self.assertEqual(r.call(r.A, 'program.select', expect='rejected', slot=3)['error']['code'], 'BUSY')
        self.assertEqual(r.call(r.A, 'cycle.rearm', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')


class Faults(unittest.TestCase):
    """1.2.0: error messages of manual 10.1 by the evaluator fault injection: interruption, display, key lock, the 30 s power-off measure."""

    def test_error_interrupts_and_needs_the_30_s_power_off(self):
        for code in rt.FAULT_CODES:
            r = Rig()
            r.press('key_start')
            r.step(20)
            r.call(r.E, 'fault.inject', code=code)
            self.assertEqual((r.val('cycle.phase'), r.val('fault.code'), r.screen()), ('cancelled', code, 'error'))
            self.assertEqual(r.shown(), {'amplitude': code})
            self.assertEqual(r.leds(), {'led_stop': True, 'led_start': False, 'led_pause': False, 'led_interval': False})
            r.step(5)
            self.assertAlmostEqual(r.val('sieving.indicated_total_lift_m'), 0.0)         # the drive has run down
            before = r.h.debug_snapshot()
            for key in ('key_start', 'key_stop', 'key_amp_plus', 'key_time_plus', 'key_prog', 'key_interval'):
                self.assertFalse(r.press(key), key)                                   # keys locked
            self.assertEqual(r.call(r.A, 'acquisition.start', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')
            self.assertEqual(r.call(r.A, 'cycle.rearm', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')
            self.assertEqual(r.call(r.A, 'sieving.set_time', expect='rejected', minutes=3)['error']['code'], 'PRECONDITION_FAILED')
            after = r.h.debug_snapshot()
            for s in (before, after):
                for k in ('_requests', 'revision', 'panel'):
                    s.pop(k, None)
            self.assertEqual(before, after)
            r.switch('off')                                                          # cause still there: the code comes back
            r.step(31)
            r.switch('on')
            self.assertEqual((r.screen(), r.val('fault.code')), ('error', code))
            r.call(r.E, 'fault.clear')
            self.assertEqual(r.val('fault.code'), code)                               # shown until the documented measure
            r.switch('off')
            r.step(10)                                                               # less than 30 s off: still there
            r.switch('on')
            self.assertEqual(r.screen(), 'error')
            r.switch('off')
            r.step(30)
            r.switch('on')
            self.assertEqual((r.screen(), r.val('fault.code')), ('setting', None))
            self.assertTrue(r.press('key_start'))                                    # a new sieving
            self.assertEqual(r.val('cycle.phase'), 'running')

    def test_error_injected_while_off_shows_at_power_on(self):
        r = Rig(power=False)
        r.call(r.E, 'fault.inject', code='E45')
        self.assertIsNone(r.val('fault.code'))
        r.switch('on')
        self.assertEqual((r.screen(), r.shown()), ('error', {'amplitude': 'E45'}))
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='E10')['error']['code'], 'ACCESS_DENIED')   # evaluator only
        self.assertEqual(r.call(r.E, 'fault.inject', expect='rejected', code='E99')['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual([d['code'] for d in rt.PANEL_SPEC['errors']['device']], rt.FAULT_CODES)


class CallerRules(unittest.TestCase):
    """1.2.0 (sync audit, rule 2): a sieving started from the panel belongs to the caller; another agent's keys are refused as its direct calls."""

    def test_panel_run_owned_and_guarded(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        self.assertTrue(r.press('key_start'))
        op = r.h.operation_id
        self.assertEqual(r.h._owners[op], 'agent')
        self.assertEqual(r.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        r.step(5)
        run = ('power.enabled', 'cycle.phase', 'cycle.mode', 'sieving.target_total_lift_m', 'sieving.continuous', 'program.selected')
        settings = {k: r.val(k) for k in run}
        remaining = r.val('cycle.remaining_s')
        for key in ('key_stop', 'key_pause', 'key_amp_plus', 'key_time_plus'):
            self.assertFalse(r.call(B, 'panel.press', input_id=key, duration_s=0.1)['accepted'], key)
            self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'ACCESS_DENIED', key)
            f = r.frame()                                                           # the refusal shows on the device: Acc for 2 s (sweep)
            self.assertEqual((f['screen'], f['displays']['amplitude']['fields']['amplitude']['text'].strip()), ('denied', 'Acc'), key)
            r.h.step(2.1)
            self.assertNotEqual(r.screen(), 'denied')
        self.assertEqual({k: r.val(k) for k in run}, settings)                   # the run and its settings unchanged
        self.assertAlmostEqual(r.val('cycle.remaining_s'), remaining - 4 * 2.1, places=6)
        r.step(5)
        before = r.h.read('debug')
        for cap, args in (('acquisition.stop', {}), ('acquisition.pause', {}), ('sieving.set_time', dict(minutes=2))):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h.read('debug'), before)                               # state unchanged
        self.assertIsNone(r.h._caller)
        self.assertEqual(r.A.cancel(op, 'cancel-1')['status'], 'cancelled')        # the owner cancels its panel-started run
        r.step(3)
        self.assertTrue(r.call(B, 'panel.press', input_id='key_start', duration_s=0.1)['accepted'])   # a new sieving by B is B's
        self.assertEqual(r.h._owners[r.h.operation_id], 'agent_b')
        self.assertEqual(B.poll(r.h.operation_id)['status'], 'running')


class HardSwitch(unittest.TestCase):
    def test_main_switch_is_a_hard_switch(self):
        """Sweep 2026-10-19: the rear main switch cuts the power; another agent's switch-off is not refused and ends the sieving."""
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        self.assertTrue(r.press('key_start'))
        op = r.h.operation_id
        r.step(3)
        self.assertTrue(r.call(B, 'panel.switch', input_id='main_switch', position='off')['accepted'])
        self.assertEqual((r.val('power.enabled'), r.val('cycle.phase'), r.screen()), (False, 'cancelled', 'off'))
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')


class Equivalence(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(), Rig()
        for _ in range(3):
            a.press('key_amp_plus')
        a.press('key_time_plus')
        a.press('key_interval')
        a.press('key_int_plus')
        a.press('key_prog')
        a.press('key_set')
        a.press('key_set')
        a.press('key_start')
        for _ in range(3):
            b.call(b.A, 'sieving.adjust_lift', direction=1)
        b.call(b.A, 'sieving.set_time', minutes=6)
        b.call(b.A, 'sieving.set_interval', enabled=True, drive_s=3.0)
        b.call(b.A, 'sieving.set_interval', enabled=True, drive_s=4.0)
        b.call(b.A, 'program.select', slot=1)
        b.call(b.A, 'program.save', slot=1)
        b.call(b.A, 'acquisition.start')
        for r in (a, b):
            r.step(400)
        sa, sb = (r.h.debug_snapshot() for r in (a, b))
        for s in (sa, sb):
            for k in ('_requests', '_owners', 'invoke', 'revision'):
                s.pop(k, None)
        self.assertEqual(json.dumps(sa, sort_keys=True, default=str), json.dumps(sb, sort_keys=True, default=str))


class Rendering(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        def run():
            r = Rig()
            r.press('key_prog')
            cap = r.call(r.A, 'panel.capture', display_id='memory')
            return cap, Path(cap['image']['path']).read_bytes()
        (c1, p1), (_, p2) = run(), run()
        self.assertEqual(hashlib.sha256(p1).hexdigest(), hashlib.sha256(p2).hexdigest())
        self.assertEqual((c1['image']['width'], c1['image']['height']), (104, 58))                # the 26 x 14.5 mm memory window at 4 px/mm
        r = Rig(power=False)
        self.assertIn('panel.press', r.A.describe(instance_id=INST)['capabilities'])
        self.assertNotIn('panel.frame', r.A.describe(instance_id=INST)['capabilities'])

    def test_evidence_renders(self):
        """home (setting while sieving) / menu (programme edit, steady phase) / error (dA information note), deterministic, with renders.json."""
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        panel = r.h.panel
        r.press('key_interval')
        r.press('key_start')
        r.step(20.25)
        shots = {'home': save_panel_png(panel, OUT / 'home.png'), 'led_start': panel.save_png('led_start', OUT / 'led_start.png')}
        r.press('key_stop')
        r.step(2)
        r.press('key_prog')
        r.press('key_set')
        r.step(1.1 - r.h.time_s % 1.0)                                            # the 'on' half of the flash
        shots['menu'] = save_panel_png(panel, OUT / 'menu.png')
        d = Rig()
        d.switch('off')
        d.step(31 * 86400)
        d.switch('on')
        shots['error'] = save_panel_png(d.h.panel, OUT / 'error.png')
        f = Rig()
        f.press('key_start')
        f.step(5)
        f.call(f.E, 'fault.inject', code='E10')
        shots['fault'] = save_panel_png(f.h.panel, OUT / 'fault.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_retsch_as200_contract_metadata()
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for name, cap in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'errors'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        spec = rt.PANEL_SPEC
        for k, v in list(spec['inputs'].items()) + list(spec['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        for k in ('led_stop', 'led_start', 'led_pause', 'led_interval'):
            self.assertEqual((c['components'][k]['geometry_status'], c['components'][k]['geometry']), ('absent', []))
        self.assertEqual(set(pu.spec_capabilities(spec)) - set(c['capabilities']), set())
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'parameter_cycle', '0.1'))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        from asset_tool import validate_contract
        self.assertEqual(validate_contract(c, ROOT, check_files=False), [])


class ZKeyCoverage(unittest.TestCase):
    def test_every_key_function_ran(self):
        if len(KEY_SEEN) > 10:
            self.assertEqual((set(rt.KEY_CAPS) | set(rt.FAULT_CAPS)) - KEY_SEEN, set())


if __name__ == '__main__':
    unittest.main()
