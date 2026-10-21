"""Front-panel tests for CHEM-029 gilson_cpc250 (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_gilson_cpc250.py -q

Every gesture goes through the session API (panel.press / panel.touch, the runtime's normal invoke path); the host steps the clock.  Each check
looks at the runtime state and at the screen: the structured frame (panel.frame) and the rendered pixels (lamp colours, the highlighted setpoint
field).  Themes: the key procedures of the menu survey; panel = direct capability (same runtime state); refusals (RUN without setpoint or under
remote control, power-off while spinning, keypad range); the 30 s start-up and no menu time-out; driver errors and Driver Reset; language and hours
counters; render determinism and the PNGs of outputs/panel_ui/gilson_cpc250/; contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import gilson_cpc250 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'cpc250_01'
RPM = math.pi / 30
OUT = ROOT / 'outputs/panel_ui/gilson_cpc250'
SURVEY_TASKS = ['start up for local control', 'spin the rotor at loading speed', 'change to elution speed while spinning', 'stop the rotor',
                'recover from a driver error under remote control', 'power down']
NOT_APPLICABLE = {'inject a sample with the manual valve': 'the asset is the CPC 250 coupled to a PLC 2050: the CPC has no manual injection valve '
                                                           '(manual p.34 note), the injection is part of the GGC method (sample.configure / acquisition.start)'}
DONE_TASKS = set()
SPEC = rt.PANEL_SPEC
COLS = SPEC['displays']['screen']['colors']


def lamp_color(img):
    h, w = img.shape[:2]
    return tuple(int(x) for x in img[h // 2, w // 2])


class Rig:
    def __init__(self, configured=False):
        self.h = rt.GilsonCpc250Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if configured:
            self.call(self.E, 'plant.configure', **rt.EXAMPLE)
            self.call(self.A, 'method.configure', **rt.METHOD_EXAMPLE)
            self.call(self.E, 'sample.configure', **rt.SAMPLE_EXAMPLE)

    def call(self, sess, cap, expect=('succeeded', 'accepted'), **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        expect = (expect,) if isinstance(expect, str) else expect
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        if r['status'] != 'rejected':
            schema = rt.PANEL_CAPS[cap]['output_schema'] if cap in rt.PANEL_CAPS else rt.CAPS[cap][1]
            rt.validate(schema, r['result'])
            assert self.h.time_s == t0, cap                                             # gestures and commands take no simulated time
            return r['result']
        return r

    # gestures (agent session)
    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def touch(self, x, y):
        return self.call(self.A, 'panel.touch', display_id='screen', x=x, y=y)['accepted']

    def tap(self, wid):
        """Tap the middle of a widget (rect from the current frame)."""
        w = self.widget(wid)
        assert w is not None, wid
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def key(self, label):
        """Tap a key of the open keyboard overlay by its label."""
        ov = self.frame()['displays']['screen']['overlay']
        b = next(b for b in ov['buttons'] if b['label'] == label)
        x, y, w, h = b['rect']
        return self.touch(x + w // 2, y + h // 2)

    def type(self, text):
        for ch in text:
            assert self.key(ch)

    # screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        f = self.frame()
        return f['screen'], f['displays']['screen'].get('page')

    def widget(self, wid):
        return next((w for w in self.frame()['displays']['screen']['widgets'] if w['id'] == wid), None)

    def text(self, wid):
        w = self.widget(wid)
        return None if w is None else w['text']

    def lamps(self):
        """Lamp states from the frame and from the pixels (both must agree)."""
        f = self.frame()['displays']
        out = {}
        for k in ('run_lamp', 'stop_lamp', 'power_lamp'):
            c = SPEC['displays'][k]['colors']
            px = lamp_color(self.h.panel.render(k))
            assert px == tuple(c['on'] if f[k]['shown'] else c['off']), (k, px, f[k])
            out[k] = 'flash' if f[k]['on'] and f[k]['blink'] else 'on' if f[k]['on'] else 'off'
        return out

    def fill(self, wid):
        """Colour at the left inner edge of a widget on the rendered screen."""
        x, y, w, h = self.widget(wid)['rect']
        return tuple(int(v) for v in self.h.panel.render('screen')[y + 3, x + 3])

    def state(self, *ids):
        o = self.E.read(instance_id=INST, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def step(self, s):
        self.h.step(float(s))

    def start_local(self):
        assert self.press('key_power')
        self.step(30)
        assert self.touch(160, 120)
        assert self.screen() == ('gui', 'control')


def runtime_state(h):
    r = h.read('evaluator')
    return json.dumps(dict(read=r, source=h.source, setpoint=h.setpoint, mode=h.setpoint_mode, run=h.local_run, err=h.driver_error, lang=h.language,
                           phase=h.phase, speed=h.speed, t=h.time_s), sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_start_up_for_local_control(self):
        g = Rig()
        self.assertEqual(g.screen(), ('off', None))
        self.assertEqual(g.lamps(), dict(run_lamp='off', stop_lamp='off', power_lamp='off'))
        self.assertEqual(set(map(int, g.h.panel.render('screen').reshape(-1))), {0})          # dark screen
        self.assertTrue(g.press('key_power'))                                             # green light, red STOP light, Siemens logo
        self.assertEqual(g.screen(), ('boot', 'logo'))
        self.assertEqual(g.text('siemens'), 'SIEMENS')
        self.assertEqual(g.lamps(), dict(run_lamp='off', stop_lamp='on', power_lamp='on'))
        self.assertFalse(g.touch(160, 120))                                               # PLC still initialising
        g.step(29.0)
        self.assertEqual(g.screen()[0], 'boot')
        g.step(1.0)
        self.assertEqual(g.screen(), ('welcome', 'welcome'))                              # after 30 s: Touch the Screen to Start
        self.assertEqual(g.text('w_msg'), 'Touch the Screen to Start')
        self.assertEqual(g.state('control.source'), 'welcome')
        self.assertTrue(g.touch(10, 200))                                                 # touch anywhere: Control Interface
        self.assertEqual(g.screen(), ('gui', 'control'))
        self.assertEqual(g.state('control.source'), 'local')
        self.assertEqual(g.text('sp_speed'), 'Enter Value (rpm)\n(100 - 3000)')
        self.assertEqual(g.text('sp_accel'), 'Enter Value (g)\n(1 - 685)')
        self.assertEqual((g.text('ind_speed'), g.text('ind_accel')), ('0 rpm', '0 g'))
        DONE_TASKS.add('start up for local control')

    def test_spin_at_loading_speed_with_the_key_and_the_keypad(self):
        g = Rig()
        g.start_local()
        self.assertTrue(g.press('key_loading'))                                           # 500 rpm / 19 g for CPC 250
        self.assertAlmostEqual(g.state('rotor.setpoint_rad_s'), 500 * RPM, places=9)
        self.assertEqual((g.text('sp_speed'), g.text('sp_accel')), ('500 rpm', '19 g'))
        self.assertEqual(g.fill('sp_speed'), tuple(COLS['selection']))                   # the speed setpoint is the highlighted one
        self.assertEqual(g.fill('sp_accel'), tuple(COLS['panel']))
        self.assertTrue(g.press('key_run'))
        g.step(1.0)
        self.assertEqual(g.state('rotor.state'), 'ramping')
        self.assertEqual(g.lamps()['run_lamp'], 'flash')                                  # RUN flashes blue while accelerating
        self.assertEqual(g.lamps()['stop_lamp'], 'off')
        w = g.widget('progress')
        self.assertGreater(w['fraction'], 0.0)
        self.assertLess(w['fraction'], 1.0)
        g.step(10.0)
        self.assertEqual(g.state('rotor.state'), 'at_setpoint')
        self.assertEqual(g.lamps()['run_lamp'], 'on')                                     # steady at the setpoint, progress bar full
        self.assertEqual(g.widget('progress')['fraction'], 1.0)
        self.assertEqual((g.text('ind_speed'), g.text('ind_accel')), ('500 rpm', '19 g'))
        # the setpoint typed on the keypad: Setpoint field, 1000, Enter
        self.assertTrue(g.tap('sp_speed'))
        ov = g.frame()['ui']['overlay']
        self.assertEqual((ov['kind'], ov['layout'], ov['buffer'], ov['selected']), ('keyboard', 'cpc_keypad', '500', True))
        g.type('1000')
        self.assertTrue(g.key('Enter'))
        self.assertIsNone(g.frame()['ui']['overlay'])
        self.assertAlmostEqual(g.state('rotor.setpoint_rad_s'), 1000 * RPM, places=9)
        self.assertEqual(g.state('rotor.state'), 'ramping')                              # takes effect at once, RUN flashes again
        g.step(10.0)
        self.assertEqual(g.text('ind_speed'), '1000 rpm')
        DONE_TASKS.add('spin the rotor at loading speed')

    def test_elution_speed_while_spinning_and_acceleration_setpoint(self):
        g = Rig()
        g.start_local()
        g.press('key_loading')
        g.press('key_run')
        g.step(10.0)
        self.assertTrue(g.press('key_elution'))                                           # 1600 rpm, RUN flashes until reached
        self.assertAlmostEqual(g.state('rotor.setpoint_rad_s'), 1600 * RPM, places=9)
        g.step(1.0)
        self.assertEqual(g.lamps()['run_lamp'], 'flash')
        g.step(10.0)
        self.assertEqual((g.text('ind_speed'), g.text('ind_accel')), ('1600 rpm', '195 g'))
        self.assertEqual(g.lamps()['run_lamp'], 'on')
        # acceleration setpoint: the g field, 19, Enter; the keys then set g values (manual p.27)
        self.assertTrue(g.tap('sp_accel'))
        g.type('19')
        self.assertTrue(g.key('Enter'))
        self.assertEqual(g.state('rotor.setpoint_mode'), 'acceleration')
        self.assertAlmostEqual(g.state('rotor.setpoint_rad_s'), 1600 * RPM * math.sqrt(19 / 195.0), places=9)
        self.assertEqual(g.fill('sp_accel'), tuple(COLS['selection']))
        g.step(1.0)
        self.assertIsNotNone(g.widget('progress_arrow'))                                  # setpoint below the speed: right arrow
        g.step(20.0)
        self.assertIsNone(g.widget('progress_arrow'))
        self.assertEqual(g.text('ind_accel'), '19 g')
        self.assertTrue(g.press('key_elution'))
        self.assertAlmostEqual(g.state('rotor.setpoint_rad_s'), 1600 * RPM, places=6)      # 195 g = 1600 rpm
        self.assertEqual(g.state('rotor.setpoint_mode'), 'acceleration')
        DONE_TASKS.add('change to elution speed while spinning')

    def test_stop_the_rotor(self):
        g = Rig()
        g.start_local()
        g.press('key_elution')
        g.press('key_run')
        g.step(20.0)
        self.assertTrue(g.press('key_stop'))
        self.assertFalse(g.state('rotor.local_run'))
        g.step(1.0)
        self.assertEqual(g.state('rotor.state'), 'stopping')
        self.assertEqual(g.lamps(), dict(run_lamp='off', stop_lamp='flash', power_lamp='on'))   # STOP flashes red while slowing
        g.step(20.0)
        self.assertEqual(g.state('rotor.state'), 'stopped')
        self.assertEqual(g.lamps()['stop_lamp'], 'on')                                    # steady red when stopped
        self.assertEqual(g.text('ind_speed'), '0 rpm')
        DONE_TASKS.add('stop the rotor')

    def test_driver_error_under_remote_control(self):
        g = Rig(configured=True)
        g.press('key_power')
        g.step(30.0)
        g.call(g.A, 'acquisition.start')                                                 # the GGC software runs the method
        self.assertEqual(g.screen(), ('remote', 'remote'))
        self.assertEqual(g.text('r_msg'), 'Under Remote Control')
        self.assertFalse(g.touch(160, 120))                                               # the touchscreen is not used
        self.assertFalse(g.press('key_run'))
        g.step(40.0)
        g.call(g.E, 'driver.inject_error', code=7)                                        # Over Speed (Error 7)
        self.assertEqual(g.state('cycle.phase'), 'failed')
        self.assertEqual(g.state('cycle.fault'), 'DRIVER_ERROR_7')
        f = g.frame()['displays']['stop_lamp']
        self.assertTrue(f['on'])
        g.step(0.125)                                                                     # STOP flashes rapidly (1/8 s phases)
        self.assertFalse(g.frame()['displays']['stop_lamp']['on'])
        g.step(0.125)
        self.assertTrue(g.frame()['displays']['stop_lamp']['on'])
        self.assertTrue(g.press('key_stop', 2.0))                                        # hold STOP 2 s: control interface
        self.assertEqual(g.screen(), ('gui', 'control'))
        self.assertEqual(g.text('drv_err'), 'Driver Error\nOver Speed (Error 7)')
        g.press('key_loading')
        self.assertFalse(g.press('key_run'))                                              # rotor does not start
        self.assertTrue(g.press('key_menu'))
        self.assertEqual(g.screen(), ('gui', 'menu'))
        self.assertTrue(g.tap('b_driver_reset'))                                          # Menu > Driver Reset
        self.assertIsNone(g.state('driver.error_code'))
        self.assertIsNone(g.widget('b_driver_reset'))
        self.assertTrue(g.tap('b_back'))
        self.assertIsNone(g.widget('drv_err'))
        g.step(60.0)
        self.assertTrue(g.press('key_run'))
        g.step(30.0)
        self.assertEqual(g.text('ind_speed'), '500 rpm')
        DONE_TASKS.add('recover from a driver error under remote control')

    def test_the_mains_key_cuts_the_power_while_the_rotor_turns(self):
        """Sweep 2026-10-19: the MAINS key is a hard switch: never refused; the rotor loses its drive and runs down."""
        g = Rig()
        g.start_local()
        g.press('key_loading')
        g.press('key_run')
        g.step(10.0)
        self.assertTrue(g.press('key_power'))
        self.assertFalse(g.state('power.enabled'))
        g.step(60.0)
        self.assertEqual(g.h.speed, 0.0)

    def test_power_down(self):
        g = Rig()
        g.start_local()
        g.press('key_loading')
        g.press('key_run')
        g.step(10.0)
        g.press('key_stop')
        g.step(30.0)                                                                      # normal shutdown: wait for the rotor to stop
        self.assertTrue(g.press('key_power'))
        self.assertFalse(g.state('power.enabled'))
        self.assertEqual(g.screen(), ('off', None))
        self.assertEqual(g.lamps(), dict(run_lamp='off', stop_lamp='off', power_lamp='off'))
        self.assertFalse(g.press('key_run'))
        DONE_TASKS.add('power down')


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def test_local_spin(self):
        a, b = Rig(), Rig()
        a.start_local()
        b.call(b.A, 'power.set', enabled=True)
        b.step(30.0)
        b.call(b.A, 'control.take_local')
        a.press('key_elution')
        a.press('key_run')
        b.call(b.A, 'rotor.set_speed', speed_rad_s=1600 * RPM)
        b.call(b.A, 'rotor.start')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.step(25.0)
        b.step(25.0)
        a.tap('sp_accel')
        a.type('100')
        a.key('Enter')
        b.call(b.A, 'rotor.set_acceleration', acceleration_m_s2=100 * 9.80665)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.press('key_stop')
        b.call(b.A, 'rotor.stop')
        a.step(40.0)
        b.step(40.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))

    def test_menu_language_and_remote_stop(self):
        a, b = Rig(configured=True), Rig(configured=True)
        for g in (a, b):
            g.call(g.A, 'power.set', enabled=True)
            g.step(30.0)
        a.touch(5, 5)
        a.press('key_menu')
        a.tap('b_language')
        b.call(b.A, 'control.take_local')
        b.call(b.A, 'display.set_language', language='fr')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        for g in (a, b):
            g.call(g.A, 'acquisition.start')
            g.step(50.0)
        a.press('key_stop', 2.0)
        b.call(b.A, 'acquisition.stop')
        b.call(b.A, 'control.take_local')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


# ---------------------------------------------------------------- refusals, time-outs, settings
class Rules(unittest.TestCase):
    def test_refusals(self):
        g = Rig()
        self.assertFalse(g.press('key_run'))                                              # power off: only the power key
        self.assertFalse(g.touch(100, 100))
        g.start_local()
        before = runtime_state(g.h)
        self.assertFalse(g.press('key_run'))                                              # no setpoint yet
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'PRECONDITION_FAILED')
        self.assertEqual(runtime_state(g.h), before)
        g.tap('sp_speed')
        g.type('3500')                                                                    # above 3000 rpm
        self.assertFalse(g.key('Enter'))
        ov = g.frame()['ui']['overlay']
        self.assertEqual(ov['error'], 'Out of range')
        self.assertIsNone(g.state('rotor.setpoint_rad_s'))
        self.assertTrue(g.key('Esc'))
        self.assertIsNone(g.frame()['ui']['overlay'])
        self.assertTrue(g.press('key_setup'))                                             # service login
        self.assertEqual(g.frame()['ui']['overlay']['dialog'], 'login')
        self.assertFalse(g.press('key_loading'))                                          # function keys inactive under a dialog
        dlg = g.frame()['displays']['screen']['overlay']
        ok = next(b for b in dlg['buttons'] if b['label'] == 'OK')
        x, y, w, h = ok['rect']
        self.assertFalse(g.touch(x + w // 2, y + h // 2))                                 # unsupported: stays open
        self.assertEqual(g.frame()['ui']['overlay']['dialog'], 'login')
        self.assertTrue(g.frame()['ui']['unsupported'])
        row = next(b for b in dlg['buttons'] if b['id'] == 'row:user')
        x, y, w, h = row['rect']
        self.assertTrue(g.touch(x + w // 2, y + h // 2))                                  # on-screen keyboard (figure 17)
        self.assertEqual(g.frame()['ui']['overlay']['kind'], 'keyboard')
        g.type('svc')
        g.key('OK')
        self.assertEqual(g.frame()['ui']['forms']['login']['user'], 'svc')
        cancel = next(b for b in g.frame()['displays']['screen']['overlay']['buttons'] if b['label'] == 'Cancel')
        x, y, w, h = cancel['rect']
        self.assertTrue(g.touch(x + w // 2, y + h // 2))
        self.assertIsNone(g.frame()['ui']['overlay'])
        self.assertEqual(runtime_state(g.h), before)

    def test_remote_control_refuses_local_commands(self):
        g = Rig(configured=True)
        g.start_local()
        g.press('key_loading')
        g.press('key_run')
        g.call(g.A, 'acquisition.start', expect='rejected')                              # rotor under local RUN
        g.press('key_stop')
        g.step(30.0)
        g.call(g.A, 'acquisition.start')
        self.assertEqual(g.screen()[0], 'remote')
        g.call(g.A, 'rotor.set_speed', expect='rejected', speed_rad_s=600 * RPM)         # BUSY while the method runs
        self.assertFalse(g.press('key_loading'))
        self.assertTrue(g.press('key_stop', 2.0))
        self.assertEqual(g.state('cycle.phase'), 'cancelled')                             # STOP held: method stopped, local control
        self.assertEqual(g.screen()[0], 'gui')

    def test_short_stop_under_remote_stops_the_method(self):
        g = Rig(configured=True)
        g.call(g.A, 'power.set', enabled=True)
        g.step(30.0)
        g.call(g.A, 'acquisition.start')
        g.step(50.0)
        self.assertTrue(g.press('key_stop'))
        self.assertEqual(g.state('cycle.phase'), 'cancelled')
        self.assertEqual(g.screen()[0], 'remote')                                         # still under remote control
        self.assertFalse(g.press('key_stop'))                                             # nothing more to stop

    def test_another_agent_cannot_stop_the_method(self):
        """Sync audit 2026-10-19: STOP (short or held) of another agent and its direct acquisition.stop are refused (state unchanged); the
        owner may poll, cancel and stop it."""
        g = Rig(configured=True)
        g.call(g.A, 'power.set', enabled=True)
        g.step(30.0)
        op = g.call(g.A, 'acquisition.start')['operation_id']
        g.step(50.0)
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        before = runtime_state(g.h)
        self.assertFalse(g.call(B, 'panel.press', input_id='key_stop')['accepted'])
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'ACCESS_DENIED')
        toast = next(w for w in g.frame()['displays']['screen']['widgets'] if w['id'] == 'r_refused')
        self.assertEqual(toast['text'], 'Not allowed: method of another user')                   # the refusal shows on the screen
        self.assertFalse(g.call(B, 'panel.press', input_id='key_stop', duration_s=2.0)['accepted'])
        self.assertEqual(g.call(B, 'acquisition.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        with self.assertRaises(rt.ActionRejected):
            B.cancel(op, 'b1')
        self.assertEqual(runtime_state(g.h), before)
        self.assertEqual(g.A.poll(op)['status'], 'running')
        self.assertTrue(g.press('key_stop'))                                              # the owner's STOP
        self.assertEqual(g.state('cycle.phase'), 'cancelled')

    def test_no_menu_timeout_and_counters(self):
        g = Rig()
        g.start_local()
        g.press('key_loading')
        g.press('key_run')
        g.press('key_menu')
        g.step(3600.0)
        self.assertEqual(g.screen(), ('gui', 'menu'))                                     # no time-out documented
        self.assertTrue(g.tap('b_counters'))
        self.assertEqual(g.screen(), ('gui', 'counters'))
        self.assertEqual(g.text('c_on_h'), '1.0 h')
        self.assertEqual(g.text('c_rotor_h'), '1.0 h')
        self.assertAlmostEqual(g.state('counters.power_on_s'), 3630.0, places=6)
        self.assertTrue(g.tap('c_back'))
        self.assertEqual(g.screen(), ('gui', 'menu'))

    def test_language_switch(self):
        g = Rig()
        g.start_local()
        g.press('key_menu')
        self.assertEqual(g.text('b_language'), 'Français')
        self.assertTrue(g.tap('b_language'))
        self.assertEqual(g.state('display.language'), 'fr')
        self.assertEqual(g.text('b_language'), 'English')
        self.assertEqual(g.text('b_counters'), 'Compteurs horaires')
        g.tap('b_back')
        self.assertEqual(g.text('l_speed'), 'Vitesse')
        self.assertEqual(g.text('sp_speed'), 'Entrer valeur (rpm)\n(100 - 3000)')
        g.step(1.0)
        g.call(g.A, 'power.set', enabled=False)
        g.call(g.A, 'power.set', enabled=True)
        g.step(30.0)
        self.assertEqual(g.text('w_msg'), "Touchez l'ecran pour demarrer")             # the language is kept over a power cycle
        self.assertIsNone(g.state('rotor.setpoint_rad_s'))                               # the setpoint is not

    def test_rejected_and_unknown_gestures(self):
        g = Rig()
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='key_nope')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.touch', expect='rejected', display_id='screen', x=480, y=10)     # just outside the 480 x 272 screen (320 until 2026-10-21)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.frame', expect='rejected')                                # evaluator only
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = g.call(g.A, 'driver.inject_error', expect='rejected', code=7)
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')


# ---------------------------------------------------------------- rendering and contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.start_local()
            g.press('key_elution')
            g.press('key_run')
            g.step(12.0)
            return g
        a, b = scenario(), scenario()
        for d in SPEC['displays']:
            ia, ib = a.h.panel.render(d), b.h.panel.render(d)
            self.assertEqual(ia.dtype.name, 'uint8')
            self.assertEqual(ia.shape[2], 3)
            self.assertTrue((ia == ib).all())
            self.assertTrue((ia == a.h.panel.render(d)).all())
        self.assertEqual(a.h.panel.png_bytes('screen'), b.h.panel.png_bytes('screen'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('screen', OUT / 'home.png')}
        a.press('key_menu')
        shots['menu'] = a.h.panel.save_png('screen', OUT / 'menu.png')
        a.tap('b_back')
        a.tap('sp_speed')
        shots['keypad'] = a.h.panel.save_png('screen', OUT / 'keypad.png')
        a.key('Esc')
        a.call(a.E, 'driver.inject_error', code=14)
        a.step(2.0)
        self.assertEqual(a.text('drv_err'), 'Driver Error\nBraking Overload (Error 14)')
        shots['error'] = a.h.panel.save_png('screen', OUT / 'error.png')
        c = Rig()
        c.call(c.A, 'power.set', enabled=True)
        c.step(30.0)
        shots['welcome'] = c.h.panel.save_png('screen', OUT / 'welcome.png')
        for name, s in shots.items():
            data = (OUT / f'{name}.png').read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), s['sha256'])
            self.assertEqual((s['width'], s['height']), (480, 272))                       # the manual's screen captures (320 x 240 until 2026-10-21)
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='screen')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.press('key_power')
        r2 = g.call(g.A, 'panel.capture', display_id='screen')
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        self.assertEqual(g.call(g.A, 'panel.capture', display_id='screen'), r2)          # same screen, same file

    def test_copied_runtime_keeps_its_own_panel(self):
        g = Rig()
        g.start_local()
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        c.panel.press('key_loading')
        self.assertIsNone(g.h.setpoint)
        self.assertAlmostEqual(c.setpoint, 500 * RPM)

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.touch', 'panel.capture', 'panel.frame'})
        m = rt.export_gilson_cpc250_contract_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in list(rt.PANEL_CAPS) + [k for k in rt.CAPS]:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/gilson_cpc250.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])                    # the shared engine is not pinned per asset
        spec = importlib.util.spec_from_file_location('cpc_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
