"""front-panel tests for CHEM-040 retsch_pm100 (panel_ui.json + scripts/asset_runtime/_panel_ui.py + the 1.1.0 menu functions of the runtime):
python3 -m pytest tests/test_panel_ui_retsch_pm100.py -q

Every gesture goes through the session API (panel.turn / panel.press / panel.switch, the runtime's normal invoke path).  Each screen check
reads the structured frame (panel.frame) and the rendered pixels: every text row of the display equals the row drawn alone from the frame
text with the spec colours.  Themes: the seven key procedures of the menu survey; programs, basic settings, service information, parameters
while grinding, automatic opening, a new run after the end; panel = direct capability; refusals; long press back to level 1; render
determinism; evidence PNGs; contract entries."""
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import retsch_pm100 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'pm100_01'
OUT = ROOT / 'outputs/panel_ui/retsch_pm100'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['open the hood to load a jar', 'set grinding time and speed in manual mode and start', 'switch operating mode (Manual / Program / Basic settings)',
                'save current parameters as a program', 'delayed start', 'resume after power failure', 'recover the language menu']
DONE_TASKS = set()
KEY_SEEN = set()
KD = rt.PANEL_SPEC['displays']['kd']
RPM = math.tau / 60
PLACES = [36000, 3600, 600, 60, 10, 1]
RADIX = [10, 10, 6, 10, 6, 10]


def _rgb(role):
    return tuple(KD['colors'][role])


def row_image(text, inverse):
    """One text row drawn alone (as the display draws it) at full brightness."""
    from PIL import Image, ImageDraw
    s = KD['text_scale']
    img = Image.new('RGB', (KD['size'][0], 9 * s + 1), _rgb('background'))
    dr = ImageDraw.Draw(img)
    if inverse:
        dr.rectangle([0, 0, KD['size'][0] - 1, 8 * s + 1], fill=_rgb('text'))
    pu._text(dr, 2, 1, text, s, _rgb('background') if inverse else _rgb('text'))
    return np.asarray(img)


class Rig:
    def __init__(self, power=True, language=True, interval=0.0):
        self.h = rt.RetschPm100Runtime(INST)
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
        self.call(self.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, interval_s=interval, pause_s=0.0, reverse=False, duration_s=60.0))
        self.call(self.A, 'lid.set', angle_rad=1.55)
        self.step(5)
        self.call(self.E, 'sample.configure', **rt.example_sample())
        self.call(self.E, 'mount.configure', clamped=True, balanced=True)
        self.call(self.A, 'lid.set', angle_rad=0.0)
        self.step(5)
        if language:
            self.call(self.A, 'settings.configure', language='English')
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

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self):
        """Text rows shown now, from the frame and from the pixels (both must agree)."""
        d = self.frame()['displays']['kd']
        img = self.h.panel.render('kd')
        if d.get('brightness', 100) >= 100:
            s = KD['text_scale']
            for i, (text, inv) in enumerate(zip(d['shown'], d['inverse'])):
                y = 2 + i * 9 * s
                assert np.array_equal(img[y - 1:y + 8 * s + 1], row_image(text, inv)[:9 * s + 1][:img[y - 1:y + 8 * s + 1].shape[0]]), (i, text)
        return [x.rstrip() for x in d['lines']]

    def item(self):
        m = self.frame()['ui']['menu']
        return m and m['item']

    # gestures
    def turn(self, n):
        return self.call(self.A, 'panel.turn', input_id='knob', detents=n)['accepted']

    def push(self, hold=0.1):
        return self.call(self.A, 'panel.press', input_id='knob_push', duration_s=hold)['accepted']

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def switch(self, pos, held=None):
        args = dict(input_id='main_switch', position=pos)
        if held:
            args['held'] = held
        return self.call(self.A, 'panel.switch', **args)['accepted']

    def to(self, item_id, limit=12):
        """Turn the knob until the selection bar is on this item."""
        for direction in (1, -1):
            for _ in range(limit):
                if self.item() == item_id:
                    return True
                if not self.turn(direction):
                    break
        assert self.item() == item_id, (self.item(), item_id)
        return True

    def hms(self, seconds):
        """Enter hh:mm:ss digit by digit in the open editor (each press moves to the next digit; the last press confirms)."""
        for k, (place, radix) in enumerate(zip(PLACES, RADIX)):
            ed = self.frame()['ui']['editor']
            cur = int(ed['buffer'][ed['field']]) // place % radix
            want = int(seconds) // place % radix
            if want != cur:
                assert self.turn(want - cur), (k, cur, want)
            assert self.push()

    def set_hms(self, item_id, seconds):
        self.to(item_id)
        self.push()
        self.hms(seconds)

    def mode(self, option_text):
        self.to('mode')
        self.push()
        for _ in range(12):
            if self.frame()['ui']['editor']['buffer']['mode'] == option_text:
                break
            self.turn(1)
        return self.push()

    def start(self):
        assert self.press('key_start')
        assert self.screen() == 'notice'
        return self.push()


class SurveyProcedures(unittest.TestCase):
    def test_open_the_hood_to_load_a_jar(self):
        r = Rig()
        self.assertEqual(r.lines()[:2], ['MANUAL OPERATING MODE', 'Grinding time            00:01:00'])
        self.assertTrue(r.press('key_open'))
        r.step(5)
        self.assertGreaterEqual(r.val('lid.angle_rad'), 1.5)
        r.call(r.A, 'lid.set', angle_rad=0.0)
        r.step(5)
        r.start()
        r.step(2)
        self.assertFalse(r.press('key_open'))                                      # locked while the sun wheel turns
        self.assertLess(r.val('lid.target_rad'), 0.1)
        DONE_TASKS.add('open the hood to load a jar')

    def test_set_grinding_time_and_speed_and_start(self):
        r = Rig()
        r.set_hms('time', 90)
        self.assertEqual(r.val('method.configured'), True)
        self.assertEqual(r.lines()[1], 'Grinding time            00:01:30')
        r.to('speed')
        r.push()
        r.turn(10)
        self.assertTrue(r.push())
        self.assertAlmostEqual(r.val('milling.target_rad_s'), 350 * RPM)
        self.assertEqual(r.lines()[2], 'Speed                     350 rpm')
        self.assertTrue(r.press('key_start'))
        self.assertEqual(r.screen(), 'notice')
        self.assertEqual(r.lines()[:3], ['SAFETY NOTICE', 'Grinding jar clamped', 'correctly?'])
        self.assertTrue(r.push())
        self.assertEqual((r.screen(), r.val('cycle.phase')), ('manual', 'running'))
        r.step(30)
        self.assertEqual(r.lines()[1][-8:], '00:01:00')                             # the remaining time while grinding
        r.step(80)
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        self.assertAlmostEqual(r.val('cycle.active_s'), 90.0, places=6)
        r.step(5)
        self.assertGreaterEqual(r.val('lid.angle_rad'), 1.5)                       # automatic opening at the end
        r.call(r.A, 'lid.set', angle_rad=0.0)
        r.step(5)
        r.start()                                                                  # START begins a new grinding with the set time
        self.assertEqual((r.val('cycle.phase'), r.val('cycle.active_s')), ('running', 0.0))
        r.step(5)
        self.assertTrue(r.press('key_stop'))
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        DONE_TASKS.add('set grinding time and speed in manual mode and start')

    def test_switch_operating_mode(self):
        r = Rig()
        self.assertTrue(r.mode(1))
        self.assertEqual((r.screen(), r.val('program.selected')), ('program', 1))
        self.assertEqual(r.lines()[:3], ['PROGRAM 01', 'Grinding time            --:--:--', 'Speed                         ---'])
        self.assertTrue(r.mode('basic'))
        self.assertEqual(r.screen(), 'basic')
        self.assertEqual(r.lines()[:2], ['BASIC SETTINGS', 'Automatic opening              on'])
        self.assertIn('Date                   2026-01-01', r.lines())
        self.assertFalse(r.press('key_start'))                                     # no grinding in the basic settings
        r.to('service')
        r.push()
        self.assertEqual(r.item(), 'hours')
        self.assertTrue(r.push(hold=2.5))                                          # long press: back to level 1
        self.assertEqual((r.screen(), r.item()), ('basic', 'mode'))
        self.assertTrue(r.mode(0))
        self.assertEqual((r.screen(), r.val('program.selected')), ('manual', 0))
        DONE_TASKS.add('switch operating mode (Manual / Program / Basic settings)')

    def test_save_current_parameters_as_a_program(self):
        r = Rig()
        r.set_hms('time', 150)
        r.to('save')
        r.push()
        self.assertEqual(r.item(), 'save_prog')
        r.push()
        r.turn(2)
        r.push()
        r.to('save_do')
        self.assertTrue(r.push())
        self.assertEqual(r.val('program.store'), [dict(slot=3, duration_s=150.0, speed_rad_s=300 * RPM)])
        self.assertEqual(r.item(), 'save')                                         # back to the parameter level
        r.set_hms('time', 40)
        r.mode(3)
        self.assertEqual(r.lines()[1:3], ['Grinding time            00:02:30', 'Speed                     300 rpm'])
        self.assertAlmostEqual(r.h.method['duration_s'], 150.0)
        r.to('change')
        r.push()
        self.assertEqual(r.item(), 'ch_prog')
        r.to('ch_speed')
        r.push()
        r.turn(20)
        r.push()
        r.to('ch_save')
        r.push()
        self.assertAlmostEqual(r.val('program.store')[0]['speed_rad_s'], 400 * RPM)
        self.assertAlmostEqual(r.val('milling.target_rad_s'), 400 * RPM)           # the active program changed
        r.start()
        r.step(160)
        self.assertAlmostEqual(r.val('cycle.active_s'), 150.0, places=6)
        r.call(r.A, 'lid.set', angle_rad=0.0)
        r.step(5)
        r.to('delete')
        r.push()
        r.to('del_do')
        r.push()
        self.assertEqual(r.val('program.store'), [])
        self.assertFalse(r.press('key_start'))                                     # a program that is not saved cannot be started
        self.assertEqual(r.screen(), 'program')
        r.mode(0)
        self.assertAlmostEqual(r.h.method['duration_s'], 40.0)                     # the manual parameters come back
        DONE_TASKS.add('save current parameters as a program')

    def test_delayed_start(self):
        r = Rig()
        r.call(r.A, 'settings.configure', safety_notice=False)
        r.to('start_in')
        r.push()
        self.assertEqual(r.item(), 'start_in_time')
        r.push()
        r.hms(30)
        self.assertAlmostEqual(r.val('cycle.start_in_s'), 30.0)
        r.step(10)
        self.assertTrue(r.press('key_stop'))                                       # STOP cancels the countdown
        self.assertIsNone(r.val('cycle.start_in_s'))
        r.step(30)
        self.assertEqual(r.val('cycle.phase'), 'idle')
        r.to('start_in_time')
        r.push()
        r.hms(20)
        r.step(10)
        self.assertAlmostEqual(r.val('cycle.start_in_s'), 10.0)
        r.push(hold=2.5)
        self.assertIn('Start in                 00:00:10', r.lines())               # the countdown on the Start in line
        r.step(10.5)
        self.assertEqual(r.val('cycle.phase'), 'running')
        DONE_TASKS.add('delayed start')

    def test_resume_after_power_failure(self):
        r = Rig()
        r.start()
        r.step(20)
        r.switch('off')
        self.assertTrue(r.val('cycle.interrupted'))
        r.step(5)
        r.switch('on')
        self.assertEqual(r.screen(), 'h45')
        self.assertEqual(r.lines()[:4], ['H45', 'MAINS INTERRUPTION', '', 'Remaining 00:00:40'])
        self.assertFalse(r.press('key_start'))                                    # inspect first
        self.assertEqual(r.frame()['ui']['message']['text'], 'H42 OPEN AND CLOSE LID')
        r.press('key_open')
        r.step(5)
        r.call(r.A, 'lid.set', angle_rad=0.0)
        r.step(5)
        self.assertTrue(r.val('lid.inspected'))
        self.assertTrue(r.press('key_start'))
        self.assertEqual((r.screen(), r.val('cycle.phase')), ('manual', 'running'))
        r.step(45)
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        self.assertAlmostEqual(r.val('cycle.active_s'), 60.0, places=6)            # the original grinding time
        DONE_TASKS.add('resume after power failure')

    def test_recover_the_language_menu(self):
        r = Rig(language=False)
        self.assertEqual(r.screen(), 'language')                                   # first switch-on
        r.push()
        r.turn(1)
        self.assertTrue(r.push())
        self.assertEqual((r.screen(), r.val('settings.current')['language']), ('manual', 'Deutsch'))
        self.assertEqual(r.lines()[:2], ['MANUAL OPERATING MODE', 'Grinding time            00:01:00'])   # 1.2.0: no translations in the manuals
        self.assertEqual(r.frame()['ui']['message']['text'], 'open lid')
        r.switch('off')
        r.switch('on')
        self.assertEqual(r.screen(), 'manual')
        r.switch('off')
        self.assertTrue(r.switch('on', held=['key_open', 'key_start', 'key_stop']))
        self.assertEqual(r.screen(), 'language')
        r.push()
        r.turn(-1)
        r.push()
        self.assertEqual(r.lines()[0], 'MANUAL OPERATING MODE')
        DONE_TASKS.add('recover the language menu')


class Functions(unittest.TestCase):
    def test_basic_settings(self):
        r = Rig()
        r.mode('basic')
        r.to('auto_open')
        r.push()
        r.turn(-1)
        r.push()
        r.to('brightness')
        r.push()
        r.turn(-5)
        r.push()
        self.assertEqual(r.val('settings.current')['brightness_pct'], 50.0)
        self.assertEqual(r.frame()['displays']['kd']['brightness'], 50.0)
        self.assertLess(int(r.h.panel.render('kd').max()), 160)
        r.to('brightness')
        r.push()
        r.turn(5)
        r.push()
        r.to('date')
        r.push()
        r.turn(17)                                                                 # day 1 -> 18
        r.push()
        r.turn(9)                                                                  # month 1 -> 10
        r.push()
        r.push()
        self.assertTrue(r.val('clock.datetime').startswith('2026-10-18'))
        r.to('clock')
        r.push()
        r.turn(10)
        r.push()
        r.turn(30)
        r.push()
        self.assertEqual(r.val('clock.datetime')[11:16], '10:30')
        r.to('signal')
        r.push()
        r.turn(-1)
        r.push()
        r.to('notice')
        r.push()
        r.turn(-1)
        r.push()
        self.assertEqual(r.val('settings.current'), dict(auto_open=False, language='English', brightness_pct=100.0, acoustic_signal=False, safety_notice=False))
        self.assertIn('MUTE', [w['text'] for w in r.frame()['displays']['kd']['widgets']])
        r.to('service')
        r.push()
        lines = r.lines()
        self.assertTrue(lines[0].startswith('Operating hours') and lines[0].endswith(' 0 h'), lines)
        self.assertTrue(lines[1].startswith('Software display') and lines[1].endswith('1.00'), lines)
        r.to('update')
        r.push()
        self.assertFalse(r.push())                                                 # service update: not available
        self.assertEqual(r.frame()['ui']['message']['text'], 'NO UPDATE AVAILABLE')
        r.push(hold=2.5)
        r.mode(0)
        self.assertTrue(r.press('key_start'))                                      # notice faded out: starts at once
        self.assertEqual(r.val('cycle.phase'), 'running')
        r.step(70)
        self.assertLess(r.val('lid.angle_rad'), 0.1)                               # no automatic opening
        self.assertGreater(r.val('panel.operating_hours_h'), 60 / 3600)

    def test_parameters_while_grinding_and_interval(self):
        r = Rig()
        self.assertFalse(any(x.startswith('Direction reversal') for x in r.lines()))    # no interval: no direction reversal
        r.set_hms('interval', 10)
        self.assertTrue(any(x.startswith('Direction reversal') for x in r.lines()))
        r.to('reverse')
        r.push()
        r.turn(1)
        r.push()
        r.set_hms('pause', 3)
        self.assertEqual((r.h.method['interval_s'], r.h.method['reverse'], r.h.method['pause_s']), (10.0, True, 3.0))
        r.start()
        r.step(5)
        r.to('speed')
        r.push()
        r.turn(-20)
        r.push()
        self.assertAlmostEqual(r.h.method['speed_rad_s'], 200 * RPM)              # changed during grinding
        r.step(6)
        self.assertLess(abs(r.val('milling.speed_rad_s')), 1e-6)                  # pause after the interval
        r.set_hms('interval', 0)
        self.assertEqual((r.h.method['interval_s'], r.h.method['reverse'], r.h.method['pause_s']), (0.0, False, 0.0))


class Faults(unittest.TestCase):
    """1.2.0: fault messages of manual chapter 7 by the evaluator fault injection."""

    def test_stopping_faults(self):
        for code in rt.FAULT_STOPS:
            r = Rig()
            r.start()
            r.step(10)
            r.call(r.E, 'fault.inject', code=code)
            self.assertEqual((r.val('cycle.phase'), r.val('cycle.fault'), r.val('fault.code'), r.screen()), ('failed', code, code, 'fault'))
            self.assertEqual(r.lines()[:2], [code, rt.FAULT_TEXT[code]])
            r.step(15)
            self.assertLess(abs(r.val('milling.speed_rad_s')), 1e-9)               # the drive has run down
            for key in ('key_start', 'key_stop', 'knob_push'):
                self.assertFalse(r.press(key), key)
            self.assertFalse(r.turn(1))
            for cap, args in (('acquisition.start', {}), ('cycle.rearm', {}), ('acquisition.start_in', dict(delay_s=5.0)), ('milling.set_parameters', dict(duration_s=30.0))):
                self.assertEqual(r.call(r.A, cap, expect='rejected', **args)['error']['code'], 'PRECONDITION_FAILED', cap)
            self.assertTrue(r.press('key_open'))                                    # KH: the cover opens once the wheel stands still
            r.step(5)
            r.call(r.A, 'lid.set', angle_rad=0.0)
            r.step(5)
            r.switch('off')
            r.switch('on')
            self.assertEqual(r.screen(), 'fault')                                   # the defect stays until repaired
            r.call(r.E, 'fault.clear')
            self.assertEqual((r.screen(), r.val('fault.code')), ('manual', None))
            r.start()                                                               # a new grinding after the repair
            self.assertEqual(r.val('cycle.phase'), 'running')

    def test_overload_hints(self):
        r = Rig()
        self.assertEqual(r.call(r.E, 'fault.inject', expect='rejected', code='H13')['error']['code'], 'PRECONDITION_FAILED')   # only while grinding
        r.start()
        r.step(5)
        r.call(r.E, 'fault.inject', code='H13')
        hint = [w for w in r.frame()['displays']['kd']['widgets'] if w['id'] == 'hint']
        self.assertEqual(hint[0]['text'], 'H13 OVERLOAD! REDUCE SPEED!')
        self.assertEqual((r.val('cycle.phase'), r.screen()), ('running', 'manual'))   # grinding goes on
        r.to('speed')
        r.push()
        r.turn(-4)
        r.push()
        self.assertIsNone(r.val('fault.code'))                                       # the operator reduced the speed
        self.assertEqual(r.call(r.E, 'fault.inject', expect='rejected', code='H14')['error']['code'], 'INVALID_ARGUMENT')   # the reduced speed must be given
        r.call(r.E, 'fault.inject', code='H14', speed_rad_s=200 * RPM)
        self.assertAlmostEqual(r.val('milling.target_rad_s'), 200 * RPM)
        self.assertIn('H14 OVERLOAD! SPEED HAS BEEN REDUCED!', [w['text'] for w in r.frame()['displays']['kd']['widgets']])
        r.step(80)
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        self.assertIsNone(r.val('fault.code'))
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='E10')['error']['code'], 'ACCESS_DENIED')   # evaluator only


class CallerRules(unittest.TestCase):
    """1.2.0 (sync audit, rule 2): grindings started from the panel or by a Start-in countdown belong to the caller."""

    def test_panel_and_start_in_runs_owned(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.start()
        op = r.h.operation_id
        self.assertEqual(r.h._owners[op], 'agent')
        self.assertEqual(r.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        r.step(5)
        before = r.h.read('debug')
        self.assertFalse(r.call(B, 'panel.press', input_id='key_stop', duration_s=0.1)['accepted'])
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'ACCESS_DENIED')
        self.assertEqual(r.frame()['ui']['message']['text'], 'RUN OF ANOTHER USER')   # the refusal shows on the display (sweep)
        for cap, args in (('acquisition.stop', {}), ('milling.set_parameters', dict(speed_rad_s=200 * RPM))):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h.read('debug'), before)                               # state unchanged
        self.assertIsNone(r.h._caller)
        self.assertEqual(r.A.cancel(op, 'cancel-1')['status'], 'cancelled')
        r.step(15)
        r.call(r.A, 'settings.configure', safety_notice=False)
        self.assertEqual(r.call(B, 'acquisition.start_in', delay_s=5.0)['start_at_s'], r.h.time_s + 5.0)   # B's countdown
        self.assertFalse(r.press('key_stop'))                                        # A may not cancel it
        r.step(6)
        op = r.h.operation_id
        self.assertEqual((r.val('cycle.phase'), r.h._owners[op]), ('running', 'agent_b'))
        self.assertEqual(B.poll(op)['status'], 'running')
        self.assertEqual(B.cancel(op, 'cancel-2')['status'], 'cancelled')


class HardSwitch(unittest.TestCase):
    def test_main_switch_is_a_hard_switch(self):
        """Sweep 2026-10-19: the main switch on the back cuts the power; another agent's switch-off is not refused and interrupts the grinding
        (remaining time kept, manual H45)."""
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.start()
        r.step(5)
        self.assertTrue(r.call(B, 'panel.switch', input_id='main_switch', position='off')['accepted'])
        self.assertFalse(r.val('power.enabled'))
        self.assertTrue(r.val('cycle.interrupted'))


class Refusals(unittest.TestCase):
    def test_off_lid_open_and_invalid(self):
        r = Rig(power=False)
        self.assertFalse(r.turn(1))
        self.assertEqual(int(r.h.panel.render('kd').max()), 0)
        r.switch('on')
        r.press('key_open')
        r.step(5)
        r.press('key_start')
        self.assertFalse(r.push())                                                 # lid open: the runtime refuses, a beep
        self.assertEqual(r.val('cycle.phase'), 'idle')
        self.assertEqual(r.frame()['ui']['beeps'], 1)
        self.assertEqual(r.call(r.A, 'panel.turn', expect='rejected', input_id='key_start', detents=1)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call(r.A, 'milling.set_parameters', expect='rejected', reverse=True)['error']['code'], 'INTERVAL_REQUIRED')
        self.assertEqual(r.call(r.A, 'program.delete', expect='rejected', slot=5)['error']['code'], 'PROGRAM_EMPTY')


class Equivalence(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(), Rig()
        a.set_hms('time', 75)
        a.to('speed')
        a.push()
        a.turn(4)
        a.push()
        a.to('save')
        a.push()
        a.to('save_do')
        a.push()
        a.start()
        b.call(b.A, 'milling.set_parameters', duration_s=75.0)
        b.call(b.A, 'milling.set_parameters', speed_rad_s=320 * RPM)
        b.call(b.A, 'program.save', slot=1, duration_s=75.0, speed_rad_s=320 * RPM)
        b.call(b.A, 'acquisition.start')
        for r in (a, b):
            r.step(90)
        sa, sb = (r.h.debug_snapshot() for r in (a, b))
        for s in (sa, sb):
            for k in ('_requests', '_owners', 'invoke', 'revision'):
                s.pop(k, None)
        self.assertEqual(json.dumps(sa, sort_keys=True, default=str), json.dumps(sb, sort_keys=True, default=str))


class Rendering(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        def run():
            r = Rig()
            r.to('speed')
            cap = r.call(r.A, 'panel.capture', display_id='kd')
            return cap, Path(cap['image']['path']).read_bytes()
        (c1, p1), (_, p2) = run(), run()
        self.assertEqual(hashlib.sha256(p1).hexdigest(), hashlib.sha256(p2).hexdigest())
        self.assertEqual((c1['image']['width'], c1['image']['height']), (400, 240))
        r = Rig(power=False)
        self.assertIn('panel.turn', r.A.describe(instance_id=INST)['capabilities'])
        self.assertNotIn('panel.frame', r.A.describe(instance_id=INST)['capabilities'])

    def test_evidence_renders(self):
        """home (manual operating mode while grinding) / menu (basic settings) / error (H45 mains interruption), deterministic, with renders.json."""
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        panel = r.h.panel
        r.start()
        r.step(12)
        shots = {'home': panel.save_png('kd', OUT / 'home.png')}
        r.switch('off')
        r.switch('on')
        shots['error'] = panel.save_png('kd', OUT / 'error.png')
        r.step(5)                                                                  # the sun wheel coasts down
        r.press('key_stop')
        r.mode('basic')
        shots['menu'] = panel.save_png('kd', OUT / 'menu.png')
        r.mode(0)
        r.press('key_start')
        shots['notice'] = panel.save_png('kd', OUT / 'notice.png')
        f = Rig()
        f.start()
        f.step(10)
        f.call(f.E, 'fault.inject', code='E47')
        shots['fault'] = f.h.panel.save_png('kd', OUT / 'fault.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_retsch_pm100_contract_metadata()
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for name, cap in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'errors'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        spec = rt.PANEL_SPEC
        for k, v in list(spec['inputs'].items()) + list(spec['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        self.assertEqual(set(pu.spec_capabilities(spec)) - set(c['capabilities']), set())
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', '0.1'))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        from asset_tool import validate_contract
        self.assertEqual(validate_contract(c, ROOT, check_files=False), [])


class ZKeyCoverage(unittest.TestCase):
    def test_every_menu_function_ran(self):
        if len(KEY_SEEN) > 10:
            self.assertEqual((set(rt.KEY_CAPS) | set(rt.FAULT_CAPS)) - KEY_SEEN, set())


if __name__ == '__main__':
    unittest.main()
