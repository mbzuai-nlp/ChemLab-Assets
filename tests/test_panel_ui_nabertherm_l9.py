"""front-panel tests for CHEM-185 nabertherm_l9 (Nabertherm B510 touch controller; panel_ui.json + scripts/asset_runtime/_panel_ui.py on
scripts/basic40_runtime_thermal.py): python3 -m pytest tests/test_panel_ui_nabertherm_l9.py -q

Every gesture goes through the session API (panel.touch on the widget, dialog, keyboard and picker rectangles of the frame; panel.switch for the
power switch).  Themes: the seven key procedures of the menu survey (switch on, start a stored program, enter a new program, stop, change the
language, power-outage behaviour, operation lock); the B510 functions behind the panel (ramps / rates / dwells, holdback AUTO and MANUAL, apply
actual value, pause, segment jump, change active program, REPEAT, delayed start, door, 04-01 / 04-02 / 04-03, alarms, gradient monitoring,
calibration, control parameters and self-optimization, start-up circuit, solar mode, extra functions, users and locks, unit and formats,
categories, copy / delete, NTLog records, statistics, USB = no stick, start-up wizard); panel = direct capability; refusals; the log-off time-out;
render determinism and the PNGs of outputs/panel_ui/nabertherm_l9/; contract entries."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_thermal as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'l9_01'
C0 = 273.15
OUT = ROOT / 'outputs/panel_ui/nabertherm_l9'
ASSET = ROOT / 'assets/basic40_batch02/t1_thermal/nabertherm_l9'
SPEC = rt._b510_panel()['spec']
SURVEY_TASKS = ['switch on the furnace', 'start a stored program', 'enter a new program', 'stop a running program', 'change the language',
                'set power-outage behaviour', 'activate the operation lock']
DONE_TASKS = set()
PW = {'operator': '000011', 'supervisor': '000021', 'administrator': '000031'}


def seg(target, time_min=60.0, mode='time', rate=100.0, hb=0.0, extras=(False, False)):
    return dict(target_C=float(target), mode=mode, time_min=time_min, rate_K_h=float(rate), hb_K=float(hb), extras=list(extras))


class Rig:
    def __init__(self, on=True):
        self.h = rt.NaberthermL9Runtime(scene_adapter=lambda key, values: None)
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if on:
            assert self.switch('I')

    # ---- session calls
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if expect == 'succeeded' else r

    def do(self, cap, **args):
        return self.call(self.A, cap, **args)

    def code(self, cap, **args):
        r = self.call(self.A, cap, expect='rejected', **args)
        return r['error']['code']

    def as_user(self, user):
        return self.do('access.login', user=user, password=PW[user])

    def store(self, slot, segments, **prog):
        """A program entered by the direct commands (supervisor)."""
        self.as_user('supervisor')
        self.do('program.edit', slot=slot)
        if prog:
            self.do('program.edit_set', **prog)
        for k, s in enumerate(segments):
            if k:
                self.do('program.edit_insert', index=k)
            self.do('program.edit_segment', index=k, **s)
        self.do('program.edit_save')
        self.do('program.edit_discard')

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def state(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def s(self):
        return self.h.debug_snapshot()['instances'][INST]['state']

    def temp_c(self):
        return self.s()['thermal.temperature_k'] - C0

    def step(self, seconds, dt=60.0):
        left = float(seconds)
        while left > 1e-9:
            d = min(dt, left)
            self.h.step(d)
            left -= d

    # ---- the screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def scr(self):
        return self.frame()['displays']['screen']

    def page(self):
        return self.scr().get('page')

    def widgets(self):
        return {w['id']: w for w in self.scr().get('widgets', [])}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def tap_xy(self, x, y, d=0.1):
        return self.call(self.A, 'panel.touch', display_id='screen', x=int(x), y=int(y), duration_s=d)['accepted']

    def tap(self, wid, row=None, d=0.1):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        if row is None:
            return self.tap_xy(x + ww / 2, y + hh / 2, d)
        rh = w.get('row_h', 40)
        return self.tap_xy(x + 20, y + row * rh + rh / 2, d)

    def overlay(self):
        return self.scr().get('overlay')

    def dialog(self):
        ov = self.frame()['ui']['overlay']
        return ov and ov.get('dialog')

    def tap_ov(self, bid, d=0.1):
        for b in self.overlay()['buttons']:
            if b['id'] == bid or b.get('label') == bid:
                x, y, w, hh = b['rect']
                return self.tap_xy(x + w / 2, y + hh / 2, d)
        raise AssertionError((bid, [(b['id'], b.get('label')) for b in self.overlay()['buttons']]))

    def keys(self, text):
        for ch in str(text):
            self.tap_ov('key:' + ch)
        return self.tap_ov('key:{ok}')

    def type_text(self, t):
        for ch in t:
            if ch.isupper():
                self.tap_ov('key:{shift}')
            self.tap_ov('key:{space}' if ch == ' ' else 'key:' + ch)
            if ch.isupper():
                self.tap_ov('key:{shift}')
        return self.tap_ov('key:{ok}')

    def keypad(self, wid, value):
        self.tap(wid)
        return self.keys(value)

    def choose(self, row, label):
        """A form row of the open dialog (or of the page form) with a choice list: pick the option."""
        self.tap_ov('row:' + row)
        return self.tap_ov(label)

    def row_value(self, row, value):
        self.tap_ov('row:' + row)
        return self.keys(value)

    def form_row(self, form_wid, field_id):
        """Tap a row of a page form widget (settings pages)."""
        w = self.widgets()[form_wid]
        return self.tap(form_wid, row=w['row_ids'].index(field_id) - w.get('scroll', 0))

    def login(self, user):
        self.tap('status_tab')
        self.tap_ov(self.overlay()['buttons'][0]['label'])
        assert self.dialog() == 'd_login'
        self.choose('user', user.upper())
        self.tap_ov('row:pw')
        self.keys(PW[user])
        return self.tap_ov('Log in')

    def settings(self, tile_index, sub=None):
        self.tap('mb_settings')
        self.tap(f'se_{tile_index}')
        if sub is not None:
            self.tap(sub)

    def open_program(self, slot=0):
        self.tap('mb_programs')
        k = next(i for i, r in enumerate(self.widgets()['pr_list']['cells']) if r[0] == f'P{slot + 1:02d}')
        self.tap('pr_list', row=k)
        assert self.page() == 'progview', self.page()


class KeyProcedures(unittest.TestCase):
    def test_switch_on_shows_start_screen_and_off_blanks(self):
        g = Rig(on=False)
        self.assertEqual(g.frame()['screen'], 'off')
        self.assertFalse(g.tap_xy(600, 200))                                        # nothing on a dark screen
        self.assertTrue(g.switch('I'))
        f = g.frame()
        self.assertEqual((f['screen'], f['ui']['page']), ('gui', 'furnace'))
        self.assertEqual(g.text('f_name'), 'Nabertherm 01')
        self.assertEqual(g.text('f_cur'), '25 °C')
        self.assertEqual(g.text('f_lastp'), '-')
        self.assertTrue(g.state('power.enabled'))
        g.tap('status_tab')
        self.assertEqual(g.dialog(), 'd_status')
        self.assertEqual(g.overlay()['lines'][0], '00:00  01.01.2026')
        self.assertEqual(g.overlay()['buttons'][0]['label'], 'OPERATOR')
        self.assertTrue(g.switch('O'))
        self.assertEqual(g.frame()['screen'], 'off')
        self.assertFalse(g.switch('O'))                                             # already off
        DONE_TASKS.add('switch on the furnace')

    def test_enter_a_new_program_by_gestures_equals_direct_commands(self):
        g = Rig()
        g.tap('mb_programs')
        self.assertTrue(g.tap('pr_new'))
        self.assertEqual(g.dialog(), 'd_login')                                     # OPERATOR may not enter programs (p.72): log in first
        g.tap_ov('X')
        self.assertEqual(g.page(), 'programs')
        self.assertTrue(g.login('supervisor'))
        self.assertEqual(g.state('b510.user'), 'supervisor')
        g.tap('pr_new')
        self.assertEqual(g.page(), 'editor')
        self.assertEqual(g.text('ed_name'), 'P01')
        g.tap('ed_name')
        g.tap_ov('key:{bksp}')
        g.type_text('BISQUE')
        g.keypad('e0_t', 600)
        g.tap('e0_d')
        g.row_value('h', 2)
        g.row_value('m', 0)
        g.tap_ov('OK')
        g.tap('ed_plus1')                                                           # "+" adds a segment (copy of the one before)
        g.tap('e1_d')
        g.row_value('h', 0)
        g.row_value('m', 30)
        g.tap_ov('OK')
        g.tap('e1_x')
        g.tap_ov('row:x0')
        g.tap_ov('On')
        g.tap_ov('Close')
        g.tap('ed_plus2')
        g.keypad('e2_t', 900)
        g.tap('e2_d')
        g.choose('mode', 'Rate')
        g.row_value('rate', 150)
        g.tap_ov('OK')
        g.tap('ed_e_b')                                                             # END -> REPEAT
        g.tap('ed_s_t')
        g.choose('hb', 'MANUAL')
        g.tap_ov('OK')
        g.keypad('e1_hb', 5)                                                        # the holdback band shows in MANUAL dwells
        self.assertTrue(g.state('b510.editing')['modified'])
        self.assertTrue(g.tap('ed_close'))
        self.assertEqual(g.dialog(), 'd_leave')                                     # leaving a changed program asks (p.27, p.44)
        g.tap_ov('Save')
        self.assertEqual(g.page(), 'programs')
        self.assertIsNone(g.state('b510.editing'))
        prog = g.state('b510.programs')[0]
        self.assertEqual([r[2] for r in g.widgets()['pr_list']['cells']], ['BISQUE'])
        d = Rig()
        d.store(0, [seg(600, 120.0), seg(600, 30.0, hb=5.0, extras=(True, False)), seg(900, 30.0, mode='rate', rate=150.0, extras=(True, False))], name='BISQUE', end='repeat',
                holdback='manual')                                                  # "+" copies the segment before it, extra functions included
        self.assertEqual(prog, d.state('b510.programs')[0])
        self.assertEqual(prog['segments'][2]['time_min'], 30.0)                     # the rate entry keeps the earlier duration
        DONE_TASKS.add('enter a new program')

    def test_start_stored_program_hold_and_stop_hold(self):
        g = Rig()
        g.store(0, [seg(500, 50.0), seg(500, 20.0)], name='FIRST FIRING')
        g.do('access.logout')
        g.open_program(0)
        self.assertEqual(g.text('pv_name'), 'FIRST FIRING')
        self.assertEqual(g.text('pv_dur'), '(t) 1h 10min')
        g.tap('pv_start')
        self.assertEqual(g.dialog(), 'd_start')
        self.assertFalse(g.tap_ov('Yes (hold)', d=0.6))                             # released early: nothing (p.33)
        self.assertIsNone(g.state('b510.run'))
        self.assertTrue(g.tap_ov('Yes (hold)', d=1.8))
        self.assertEqual(g.page(), 'furnace')                                       # the overview with the small segment player (p.23)
        self.assertTrue(g.state('program.running'))
        self.assertEqual(g.text('rs_n'), '01 / 02')
        g.step(1500)
        self.assertAlmostEqual(g.state('b510.run')['setpoint_C'], 25 + 475 * 0.5, places=6)
        self.assertAlmostEqual(g.temp_c(), 25 + 475 * 0.5, delta=0.2)            # the PID follows the ramp within one controller cycle
        self.assertEqual(g.text('r_times'), 'remaining -00:45')
        g.tap('rs_stop')
        self.assertEqual(g.dialog(), 'd_stop')
        self.assertFalse(g.tap_ov('Yes (hold)', d=1.5))                             # stop needs 2-3 s
        self.assertTrue(g.state('program.running'))
        g.tap_ov('Yes (hold)', d=2.8)
        self.assertFalse(g.state('program.running'))
        self.assertEqual(g.state('program.phase'), 'stopped')
        self.assertEqual(g.text('f_lastp'), 'FIRST FIRING')
        self.assertTrue(g.widgets()['f_restart']['enabled'])
        self.assertFalse(g.tap('f_restart', d=0.5))                                 # Repeat is also press-and-hold
        self.assertTrue(g.tap('f_restart', d=2.0))
        self.assertTrue(g.state('program.running'))
        DONE_TASKS.update({'start a stored program', 'stop a running program'})

    def test_change_the_language(self):
        g = Rig()
        g.settings(8)
        self.assertEqual(g.page(), 'set_system')
        g.tap('sy_g2')
        g.form_row('sy_form2', 'language')
        g.tap_ov('Deutsch')
        self.assertEqual(g.state('b510.settings')['language'], 'de')
        self.assertEqual(g.text('mb_furnace'), 'Ofen')
        self.assertEqual(g.text('mb_settings'), 'Einstellungen')
        g.form_row('sy_form2', 'language')
        g.tap_ov('English')
        self.assertEqual(g.text('mb_furnace'), 'Furnace')
        DONE_TASKS.add('change the language')

    def test_power_outage_behaviour_modes(self):
        g = Rig()
        g.settings(7)
        self.assertEqual(g.dialog(), 'd_login')                                     # ADMINISTRATOR only (p.73)
        g.tap_ov('X')
        g.login('administrator')
        g.settings(7)
        self.assertEqual(g.page(), 'set_outage')
        g.form_row('so_form0', 'power_outage_mode')
        g.tap_ov('CANCEL')
        self.assertEqual(g.state('b510.settings')['power_outage_mode'], 'cancel')
        DONE_TASKS.add('set power-outage behaviour')

        def run(mode, outage_s, cool=None):
            r = Rig()
            r.store(0, [seg(600, 30.0), seg(600, 600.0)])
            r.as_user('administrator')
            r.do('settings.configure', power_outage_mode=mode)
            r.do('program.start', slot=0)
            r.step(2400)
            r.elapsed = r.state('b510.run')['seg_elapsed_s']
            r.call(r.E, 'diagnostics.set_mains', present=False)
            self.assertEqual(r.frame()['screen'], 'off')
            self.assertFalse(r.tap_xy(600, 200))
            r.step(outage_s, dt=min(60.0, outage_s))
            r.call(r.E, 'diagnostics.set_mains', present=True)
            return r

        r = run('cancel', 60)
        self.assertFalse(r.state('program.running'))
        self.assertEqual(r.state('b510.error')['code'], '04-03')
        self.assertEqual(r.text('msg_bar'), '! 04-03 Power failure')
        r = run('time', 60)
        self.assertTrue(r.state('program.running'))
        self.assertEqual([w['code'] for w in r.state('b510.warnings')], ['W05'])
        self.assertEqual(r.state('b510.graph'), [])                                 # graph history lost (p.30)
        self.assertEqual(r.state('b510.run')['seg_elapsed_s'], r.elapsed)          # the dwell waited during the outage
        r = run('time', 180)
        self.assertEqual(r.state('b510.error')['code'], '04-03')
        r = run('cancel', 3)
        self.assertTrue(r.state('program.running'))                                 # under 5 s always continues, no warning (p.87)
        self.assertEqual(r.state('b510.warnings'), [])
        r = run('delta_t', 300)                                                     # 600 C: cools about 31 K in 5 min (< 50 K)
        self.assertTrue(r.state('program.running'))
        r = run('delta_t', 7200)
        self.assertEqual(r.state('b510.error')['code'], '04-03')
        r = run('continue', 7200)
        self.assertTrue(r.state('program.running'))
        self.assertEqual(r.state('b510.user'), 'operator')                         # the standard user after the outage

    def test_activate_the_operation_lock(self):
        g = Rig()
        g.login('administrator')
        g.settings(4, 'su_g1')
        g.form_row('su_form1', 'operation_lock')
        g.tap_ov('On')
        self.assertTrue(g.state('b510.settings')['operation_lock'])
        g.tap('su_g0')
        g.form_row('su_form0', 'logout')
        self.assertIsNone(g.state('b510.user'))                                     # nobody logged in: every step asks for the password (p.76)
        g.tap('mb_programs')
        self.assertEqual(g.dialog(), 'd_login')
        self.assertEqual(g.page(), 'set_users')                                     # the touch was refused
        g.tap_ov('X')
        self.assertFalse(g.tap('mb_programs'))
        g.tap_ov('row:pw')
        g.keys('999999')
        g.tap_ov('Log in')
        self.assertEqual(g.dialog(), 'd_pw_wrong')                                  # [PASSWORD INCORRECT] (p.74)
        g.tap_ov('OK')
        self.assertIsNone(g.state('b510.user'))
        g.tap('mb_programs')
        g.choose('user', 'OPERATOR')
        g.tap_ov('row:pw')
        g.keys('000011')
        g.tap_ov('Log in')
        self.assertEqual(g.state('b510.user'), 'operator')
        self.assertTrue(g.tap('mb_programs'))
        self.assertEqual(g.page(), 'programs')
        g.switch('O')
        g.switch('I')
        self.assertIsNone(g.state('b510.user'))                                     # active again at every power-on
        DONE_TASKS.add('activate the operation lock')


class ProgramEngine(unittest.TestCase):
    def test_ramps_rates_dwells_and_end(self):
        g = Rig()
        g.store(0, [seg(525, 50.0), seg(825, mode='rate', rate=600.0), seg(825, 10.0), seg(300, 0.0)], start_C=25.0)
        g.do('program.start', slot=0)
        g.step(1500)
        self.assertAlmostEqual(g.state('b510.run')['setpoint_C'], 275.0, places=6)
        g.step(1500 + 900)                                                          # 50 min ramp, then 300 K at 600 K/h = 30 min
        self.assertEqual(g.state('program.segment_index'), 1)
        self.assertAlmostEqual(g.state('b510.run')['setpoint_C'], 675.0, places=6)
        g.step(900)
        self.assertEqual((g.state('program.segment_index'), g.state('program.phase')), (2, 'hold'))
        g.step(601)
        self.assertEqual(g.state('program.phase'), 'completed')                     # the 0:00 step jumps to 300 and the program ends
        self.assertAlmostEqual(g.state('thermal.target_k'), g.h.ambient_temperature_K)
        st = g.state('b510.statistics')
        self.assertEqual(st['starts'], 1)
        self.assertEqual(st['starts_200'], 1)
        self.assertGreater(st['kwh_total'], 0.5)
        self.assertAlmostEqual(st['max_last_C'], 825.0, places=0)

    def test_auto_holdback_waits_and_manual_band_freezes_the_dwell(self):
        g = Rig()
        g.as_user('administrator')
        g.do('settings.configure', startup_on=True, startup_limit_C=1100.0, startup_max_power_percent=30.0)
        g.store(0, [seg(300, 1.0), seg(300, 10.0)])
        g.do('program.start', slot=0)
        g.step(120, dt=10)
        run = g.state('b510.run')
        self.assertEqual(run['segment'], 0)                                         # AUTO: at the end of the ramp the program waits (p.46)
        self.assertTrue(run['waiting'])
        self.assertTrue(g.frame()['ui'] and g.text('r_state') == 'Holdback: waiting')
        self.assertLess(g.temp_c(), 300 - rt._B510_AUTO_HB_K)
        g.step(4200, dt=60)
        run = g.state('b510.run')
        self.assertEqual(run['segment'], 1)
        h = Rig()
        h.store(0, [seg(400, 30.0), seg(400, 20.0, hb=5.0)], holdback='manual')
        h.do('program.start', slot=0)
        h.step(1800 + 300)
        self.assertAlmostEqual(h.state('b510.run')['seg_elapsed_s'], 300.0)
        h.do('door.open')
        self.assertIn('W19', [w['code'] for w in h.state('b510.warnings')])          # door opened during a program (p.123)
        self.assertFalse(h.state('heater.enabled'))
        h.step(600, dt=10)
        run = h.state('b510.run')
        self.assertTrue(run['waiting'])                                             # outside the band: the dwell time stands (p.41, p.47)
        frozen = run['seg_elapsed_s']
        self.assertLess(frozen, 400.0)
        h.step(300, dt=10)
        self.assertEqual(h.state('b510.run')['seg_elapsed_s'], frozen)
        h.do('door.close')
        h.step(1800, dt=10)
        self.assertGreater(h.state('b510.run')['seg_elapsed_s'], frozen)            # back in the band: the dwell time runs again

    def test_apply_actual_value_on_and_off(self):
        g = Rig()
        g.do('thermal.set_temperature', temperature_K=240 + C0)
        g.step(3600)
        g.do('thermal.set_temperature', temperature_K=g.h.ambient_temperature_K)
        g.store(0, [seg(1000, 980.0)], start_C=20.0)
        g.do('program.start', slot=0)
        run = g.state('b510.run')
        self.assertAlmostEqual(run['setpoint_C'], 240.0, places=6)                 # starts at the higher actual value (p.60)
        self.assertAlmostEqual(run['profile'][2], 760 * 60.0, places=6)            # the timed ramp keeps its slope
        g.do('program.stop')
        g.as_user('administrator')
        g.do('settings.configure', apply_actual=False)
        g.do('program.start', slot=0)
        self.assertAlmostEqual(g.state('b510.run')['setpoint_C'], 20.0, places=6)
        g.do('program.stop')
        g.store(1, [seg(200, 30.0), seg(200, 10.0), seg(600, 60.0)], start_C=20.0)
        g.as_user('administrator')
        g.do('settings.configure', apply_actual=True)
        g.do('program.start', slot=1)
        run = g.state('b510.run')
        self.assertEqual(run['segment'], 1)                                         # the ramp to 200 is already passed: skipped

    def test_pause_jump_change_active_and_repeat_by_gestures(self):
        g = Rig()
        g.store(0, [seg(400, 40.0), seg(400, 60.0), seg(500, 10.0)], end='repeat')
        g.do('program.start', slot=0)
        g.do('access.logout')
        g.step(1200)
        g.tap('mb_furnace')
        g.tap('r_tab_small')                                                        # the large segment player (p.17)
        self.assertEqual(g.text('lp_n'), '01 / 03')
        self.assertEqual(g.text('lp_times'), '00:20 / 00:40')
        self.assertFalse(g.tap('lp_pause', d=1.0))                                  # a longer press is needed (p.24)
        self.assertTrue(g.tap('lp_pause', d=3.0))
        self.assertTrue(g.state('b510.run')['paused'])
        sp = g.state('b510.run')['setpoint_C']
        g.step(600)
        self.assertEqual(g.state('b510.run')['setpoint_C'], sp)                    # the ramp setpoint is frozen (p.18)
        g.tap('lp_pause', d=3.0)
        g.step(60)
        self.assertGreater(g.state('b510.run')['setpoint_C'], sp)
        g.tap('lp_next')
        self.assertEqual(g.text('lp_n'), '2 / 3')                                   # browsing changes only the view
        g.tap('r_dots')
        g.tap_ov('Segment jump')
        self.assertEqual(g.dialog(), 'd_login')                                     # SUPERVISOR (p.48)
        g.tap_ov('X')
        g.login('supervisor')
        g.tap('r_dots')
        g.tap_ov('Segment jump')
        g.row_value('seg', 3)
        g.tap_ov('OK')
        self.assertEqual(g.state('b510.run')['segment'], 2)
        g.tap('r_dots')
        g.tap_ov('Change active program')
        self.assertEqual(g.page(), 'active')
        self.assertFalse(g.widgets()['a0_t']['enabled'])                            # past segments only after a jump back (p.47)
        self.assertFalse(g.tap('a0_t'))
        g.keypad('a2_t', 550)
        self.assertEqual(g.state('b510.run')['program']['segments'][2]['target_C'], 550.0)
        self.assertEqual(g.state('b510.programs')[0]['segments'][2]['target_C'], 500.0)   # the stored program stays as it was
        self.assertEqual(g.state('b510.run')['profile'][1], 550.0)                  # the current ramp goes on from the actual value (p.48)
        g.step(2400)
        self.assertEqual(g.state('b510.run')['cycles'], 1)                          # REPEAT: the program again (p.43)
        self.assertEqual(g.state('b510.run')['segment'], 0)

    def test_delayed_start_waits_without_heating(self):
        g = Rig()
        g.store(0, [seg(300, 30.0)])
        g.do('access.logout')
        g.open_program(0)
        g.tap('pv_delay')
        self.assertEqual(g.dialog(), 'd_delay')
        g.tap_ov('row:time')
        self.assertEqual(g.overlay()['kind'], 'spinner')
        g.tap_ov('inc:0')                                                           # 00:00 -> 01:00
        g.tap_ov('OK')
        g.tap_ov('OK')
        self.assertEqual(g.text('pv_delay'), '[x] 2026-01-01 01:00')
        g.tap('pv_start')
        g.tap_ov('Yes (hold)', d=2.0)
        run = g.state('b510.run')
        self.assertEqual((run['phase'], run['delayed_until']), ('delayed', '2026-01-01 01:00'))
        self.assertEqual(g.text('r_state'), 'Delayed start 2026-01-01 01:00')
        g.step(3000)
        self.assertLess(g.temp_c(), 25.5)                                           # no heating while waiting (p.124)
        g.step(660)
        run = g.state('b510.run')
        self.assertEqual(run['phase'], 'running')
        g.step(600)
        self.assertGreater(g.temp_c(), 100)

    def test_door_open_in_a_ramp_raises_no_heating_power(self):
        g = Rig()
        g.store(0, [seg(900, 120.0)])
        g.do('program.start', slot=0)
        g.step(600)
        g.do('door.open')
        g.step(11 * 60, dt=30)
        self.assertTrue(g.state('program.running'))
        g.step(120, dt=30)
        self.assertEqual(g.state('b510.error')['code'], '04-01')                    # no rise for 12 min (p.120)
        self.assertFalse(g.state('program.running'))
        self.assertEqual(g.text('msg_bar'), '! 04-01 No heating power')
        g.tap('msg_bar')
        self.assertEqual(g.dialog(), 'd_msg')
        g.tap_ov('Acknowledge')
        self.assertIsNone(g.state('b510.error'))
        self.assertEqual(g.state('b510.error_log')[-1]['code'], '04-01')
        self.assertTrue(g.widgets()['msg_bar']['text'].startswith('! W18'))         # heating blocked while the door is open

    def test_excess_temperature_and_main_switch_off(self):
        g = Rig()
        g.do('thermal.set_temperature', temperature_K=600 + C0)
        g.step(3600)
        g.do('thermal.set_temperature', temperature_K=g.h.ambient_temperature_K)
        g.store(0, [seg(300, 10.0), seg(300, 600.0)], start_C=20.0)
        g.as_user('administrator')
        g.do('settings.configure', apply_actual=False)
        g.do('program.start', slot=0)
        g.step(170, dt=10)
        self.assertTrue(g.state('program.running'))
        g.step(20, dt=10)
        self.assertEqual(g.state('b510.error')['code'], '04-02')                    # 50 K above the program maximum for 3 min (p.120)
        self.assertEqual(g.code('program.start', slot=0), 'PRECONDITION_FAILED')    # not before the error is acknowledged
        g.do('messages.acknowledge')
        g.do('program.start', slot=0)
        g.switch('O')
        g.switch('I')
        self.assertEqual(g.state('b510.error')['code'], '04-03')                    # main switch off during a program (p.14)
        g.call(g.E, 'diagnostics.inject_error', code='03-01')
        g.do('messages.acknowledge')
        self.assertEqual(g.state('b510.error')['code'], '03-01')                    # (1): only switching off clears it (p.121)
        g.switch('O')
        g.switch('I')
        self.assertIsNone(g.state('b510.error'))


class ControllerSettings(unittest.TestCase):
    def test_alarms_reactions_and_types(self):
        g = Rig()
        g.login('administrator')
        g.settings(6)
        g.form_row('sa_form0', 'source')
        g.tap_ov('MAX')
        g.form_row('sa_form0', 'max')
        g.keys(300)
        g.form_row('sa_form0', 'delay')
        g.keys(30)
        g.form_row('sa_form0', 'type')
        g.tap_ov('SAVE+REPORT')
        g.form_row('sa_form0', 'reaction')
        g.tap_ov('HOLD')
        al = g.state('b510.alarms')[0]
        self.assertEqual((al['source'], al['max_K'], al['delay_s'], al['type'], al['reaction']), ('max', 300.0, 30.0, 'save_report', 'hold'))
        g.do('alarms.configure', index=1, source='program_end', type='going_report')
        g.store(0, [seg(400, 50.0), seg(400, 5.0)])
        g.as_user('administrator')
        g.do('program.start', slot=0)
        g.step(2700, dt=30)                                                        # passes 300 C at about 34 min, alarm after 30 s
        self.assertIn('W08', [w['code'] for w in g.state('b510.warnings')])
        self.assertTrue(g.state('b510.run')['alarm_hold'])
        sp = g.state('b510.run')['setpoint_C']
        g.step(600)
        self.assertEqual(g.state('b510.run')['setpoint_C'], sp)                    # HOLD: the program pauses (p.83)
        g.do('alarms.configure', index=0, max_K=500.0)
        g.step(60)
        g.step(1200)
        self.assertEqual(g.state('program.phase'), 'completed')
        self.assertIn('W25', [w['code'] for w in g.state('b510.warnings')])          # PROGRAM END alarm 2 (p.81, p.123)
        g.do('alarms.configure', index=2, source='max', max_K=50.0, delay_s=0.0, reaction='program_stop')
        g.do('messages.acknowledge')
        g.do('program.start', slot=0)
        g.step(60)
        self.assertEqual(g.state('b510.error')['code'], '04-04')
        self.assertIn('W20', [w['code'] for w in g.state('b510.warnings')])

    def test_gradient_monitoring_interrupts_and_restarts(self):
        g = Rig()
        g.as_user('administrator')
        g.do('settings.configure', gradient_on=True, gradient_min_C=100.0, gradient_max_K_h=600.0, gradient_interval_s=60.0, gradient_delay_n=2)
        g.store(0, [seg(800, 20.0), seg(800, 30.0)])
        g.do('program.start', slot=0)
        g.step(390, dt=30)
        run = g.state('b510.run')
        self.assertTrue(run['grad_hold'])                                          # over 600 K/h for 2 intervals: interrupted (p.84-85)
        self.assertIn('W00', [w['code'] for w in g.state('b510.warnings')])
        sp = run['setpoint_C']
        g.step(300, dt=30)
        self.assertEqual(g.state('b510.run')['setpoint_C'], sp)
        g.step(180, dt=30)
        run = g.state('b510.run')
        self.assertFalse(run['grad_hold'])                                         # an interval without exceedance: the furnace runs again
        self.assertGreater(run['setpoint_C'], sp)

    def test_calibration_table_shifts_reading_and_control(self):
        g = Rig()
        g.login('administrator')
        g.settings(1)
        self.assertEqual(g.page(), 'set_cal')
        g.tap('sc_list', row=0)
        self.assertEqual(g.dialog(), 'd_cal')
        g.row_value('temp', 400)
        g.row_value('off', 4)
        g.tap_ov('OK')
        self.assertEqual(g.state('b510.calibration')[0], [400.0, 4.0])
        self.assertEqual(g.widgets()['sc_list']['cells'][0], ['1', '400.0 °C', '+4.0 °C'])
        g.store(0, [seg(400, 30.0), seg(400, 60.0)])
        g.do('program.start', slot=0)
        g.step(2400)
        self.assertAlmostEqual(g.temp_c(), 400 / 1.01, places=4)                   # corrected value = measured + offset is held at 400 (p.49)
        self.assertAlmostEqual(g.state('b510.reading_k') - C0, 400.0, delta=0.1)
        self.assertEqual(g.text('sp_temp'), '400 °C')
        # the same table on the direct set point (2026-10-19 sync fix): the controller holds its own corrected reading at 400 degC, and the
        # legacy display slot carries the same correction as the B510 reading
        d = Rig()
        d.login('administrator')
        d.do('calibration.set_point', index=0, temp_C=400.0, offset_K=4.0)
        d.do('thermal.set_temperature', temperature_K=400 + C0)
        d.step(4800)
        self.assertAlmostEqual(d.temp_c(), 400 / 1.01, places=3)
        self.assertAlmostEqual(d.state('thermal.indicated_temperature_k') - C0, 400.0, delta=0.05)
        self.assertEqual(d.state('b510.reading_k'), d.state('thermal.indicated_temperature_k'))
        self.assertEqual(d.h._b510_panel_read(INST)['host.temp'], 400.0)
        # the legacy one-program API arrives and dwells on the corrected reading too
        e = Rig()
        e.login('administrator')
        e.do('calibration.set_point', index=0, temp_C=400.0, offset_K=4.0)
        e.do('thermal.configure_program', segments=[dict(temperature_K=400 + C0, ramp_rate_K_s=1.0, hold_s=600.0)])
        e.do('thermal.start_program')
        held = []
        for _ in range(400):
            e.step(10, dt=10.0)
            if e.state('program.phase') == 'hold':
                held.append((e.state('thermal.indicated_temperature_k') - C0, e.temp_c()))
            if e.state('program.phase') == 'completed':
                break
        self.assertEqual(e.state('program.phase'), 'completed')
        self.assertGreater(len(held), 50)
        self.assertAlmostEqual(held[-1][0], 400.0, delta=0.15)
        self.assertAlmostEqual(held[-1][1], 400 / 1.01, delta=0.15)

    def test_control_parameters_drive_the_heating_w01_and_self_optimization(self):
        def heat(rig, seconds=2500):
            rig.do('thermal.set_temperature', temperature_K=500 + C0)
            temps = []
            for _ in range(seconds // 10):
                rig.step(10, dt=10)
                temps.append(rig.temp_c())
            return temps

        f = Rig()
        temps = heat(f)                                                             # factory base point: on the set point without overshoot
        self.assertEqual(max(temps), 500.0)
        self.assertEqual(temps[-1], 500.0)
        g = Rig()
        g.login('administrator')
        g.settings(2)
        g.tap('sp_list', row=0)
        g.row_value('xp', 7)                                                        # the p.55 example values (p.53-55)
        g.row_value('ti', 450)
        g.row_value('td', 70)
        g.tap_ov('OK')
        self.assertEqual(g.state('b510.pid')[0], [0.0, 1100.0, 7.0, 450.0, 70.0])
        temps = heat(g)
        self.assertLess(max(temps), 500.0)                                          # larger XP and TI: a slower approach from below
        self.assertLess(temps[-1], 499.9)
        g.tap('mb_settings')
        g.tap('se_2')
        g.tap('sp_actual')
        lines = g.overlay()['lines']
        self.assertTrue(lines[0].startswith('Temperature  500.0 -> 499.'), lines)  # the P / I / D shares now in use (p.56 scr)
        self.assertTrue(lines[4].endswith('base point 1'), lines)
        g.tap_ov('Close')
        h = Rig()
        h.as_user('administrator')
        h.do('pid.set_point', index=0, from_C=0.0, to_C=0.0, xp=0.0, ti=0.0, td=0.0)
        self.assertEqual(h.text('msg_bar'), '! W01 No control parameters')        # no P value (p.121): no heating
        self.assertEqual(heat(h, 600)[-1], 25.0)
        h.settings(3, 'sk_g2')
        h.form_row('sk_form2', 'opt')
        h.keys(400)
        h.form_row('sk_form2', 'start')
        self.assertEqual(h.state('b510.autotune')['state'], 'running')
        self.assertEqual(h.code('program.start', slot=0), 'BUSY')
        for _ in range(48):
            h.step(300, dt=60)
            if h.state('b510.autotune')['state'] != 'running':
                break
        tune = h.state('b510.autotune')
        self.assertEqual(tune['state'], 'done')
        self.assertEqual(h.code('autotune.start', opt_C=400.0), 'PRECONDITION_FAILED')   # only with the furnace cold (< 60 C, p.64)
        for key, factory in zip(('xp', 'ti', 'td'), rt._B510_PID_DEFAULT[0][2:]):  # the step responses identify the plant: the factory gains again
            self.assertAlmostEqual(tune['result'][key], factory, delta=2e-3 * max(factory, 1.0))
        h.form_row('sk_form2', 'copy')
        h.row_value('row', 4)
        h.tap_ov('Copy')
        self.assertEqual(h.state('b510.pid')[3][2:], [tune['result']['xp'], tune['result']['ti'], tune['result']['td']])
        h.step(7200)
        self.assertEqual(heat(h, 2000)[-1], 500.0)                                  # base point 4 (the nearest with a P value) controls now
        self.assertEqual(h.state('b510.pid_out')['row'], 3)

    def test_regulator_damping_caps_the_integral_in_rising_ramps(self):
        lags = []
        for on in (False, True):
            g = Rig()
            g.as_user('administrator')
            g.do('settings.configure', damping_on=on, damping_limit_C=1100.0, damping_max_integrator_percent=10.0)
            g.store(0, [seg(800, 240.0), seg(800, 10.0)])
            g.do('program.start', slot=0)
            g.step(3600)
            lags.append(g.state('b510.run')['setpoint_C'] - g.temp_c())
            self.assertLessEqual(g.state('b510.pid_out')['i'], 10.0 + 1e-9 if on else 100.0)
        self.assertGreater(lags[1], lags[0] + 0.03)                                 # the I share limited to 10 %: the P share carries the rest (p.69-70)

    def test_startup_circuit_and_solar_mode(self):
        g, h = Rig(), Rig()
        for r in (g, h):
            r.store(0, [seg(600, 10.0), seg(600, 60.0)])
        g.as_user('administrator')
        g.do('settings.configure', startup_on=True, startup_limit_C=300.0, startup_max_power_percent=25.0)
        for r in (g, h):
            r.do('program.start', slot=0)
            r.step(300, dt=10)
        self.assertLess(g.temp_c(), h.temp_c())                                     # power limited below the limit temperature (p.63)
        self.assertLessEqual(g.state('b510.output_percent'), 25.0 + 1e-6)
        s = Rig()
        s.store(0, [seg(300, 5.0), seg(300, 120.0)])
        s.as_user('administrator')
        s.do('settings.configure', solar_mode='ramps_holds', solar_on_hold_K=-5.0, solar_off_hold_K=0.0)
        s.do('program.start', slot=0)
        s.step(1800, dt=10)
        temps = []
        for _ in range(60):
            s.step(20, dt=20)
            temps.append(s.temp_c())
        self.assertGreater(max(temps) - min(temps), 3.0)                            # two-point control around the setpoint (p.70-71)
        self.assertLessEqual(max(temps), 300.5)                                    # off at the threshold, checked every second

    def test_extra_functions_segments_manual_and_end(self):
        g = Rig()
        g.login('administrator')
        g.settings(5)
        g.form_row('sx_form0', 'name0')
        for _ in range(20):
            g.tap_ov('key:{bksp}')
        g.type_text('Fan')
        self.assertEqual(g.state('b510.extras')['names'][0], 'Fan')
        g.store(0, [seg(150, 10.0, extras=(True, False)), seg(150, 10.0)], end_extras=[False, True])
        g.do('program.start', slot=0)
        self.assertEqual(g.state('b510.extras')['outputs'], [True, False])
        g.tap('mb_furnace')
        g.tap('r_dots')
        g.tap_ov('Control extra functions')
        g.tap('xc0_off')
        g.tap('xc1_on')
        self.assertEqual(g.state('b510.extras')['outputs'], [False, True])
        g.step(660)
        self.assertEqual(g.state('b510.extras')['manual'], ['auto', 'auto'])        # manual settings end at the next segment (p.80)
        self.assertEqual(g.state('b510.extras')['outputs'], [False, False])
        g.step(600)
        self.assertEqual(g.state('program.phase'), 'completed')
        self.assertEqual(g.state('b510.extras')['outputs'], [False, True])          # end-segment extras stay on after the end (p.27)
        g.do('program.stop')
        self.assertEqual(g.state('b510.extras')['outputs'], [False, False])         # until stop is pressed again

    def test_levels_controller_lock_logoff_timeout_and_password(self):
        g = Rig()
        g.store(0, [seg(300, 60.0)])
        g.do('program.start', slot=0)
        g.tap('mb_furnace')
        g.tap('r_dots')
        g.tap_ov('Lock controller')
        self.assertTrue(g.state('b510.locked'))
        self.assertEqual(g.code('program.pause', on=True), 'PRECONDITION_FAILED')
        g.tap('rs_stop')
        g.tap_ov('Yes (hold)', d=3.0)
        self.assertEqual(g.dialog(), 'd_locked')                                    # input blocked (p.77)
        self.assertTrue(g.state('program.running'))
        g.tap_ov('OK')
        g.tap('r_dots')
        g.tap_ov('Unlock controller')
        g.choose('user', 'SUPERVISOR')
        g.tap_ov('row:pw')
        g.keys('000021')
        g.tap_ov('Unlock')
        self.assertFalse(g.state('b510.locked'))
        g.as_user('administrator')
        g.do('settings.configure', logoff_min=1.0)
        g.tap('mb_programs')
        g.step(59)
        self.assertEqual(g.state('b510.user'), 'administrator')
        g.frame()
        g.step(2)
        g.frame()
        self.assertEqual(g.state('b510.user'), 'operator')                          # automatic log-off after the logoff time (p.74)
        g.do('access.set_password', user='operator', password='123456')
        g.settings(4, 'su_g2')
        g.form_row('su_form2', 'change')
        self.assertEqual(g.dialog(), 'd_pw')
        g.tap_ov('row:new')
        g.keys('222222')
        g.tap_ov('row:again')
        g.keys('333333')
        g.tap_ov('OK')
        self.assertEqual(g.dialog(), 'd_pw_mismatch')
        self.assertEqual(g.state('b510.passwords')['operator'], '123456')
        self.assertEqual(g.code('access.set_password', user='supervisor', password='111111'), 'PRECONDITION_FAILED')   # only one's own (p.75)
        self.assertEqual(g.code('access.login', user='operator', password='000011'), 'PRECONDITION_FAILED')

    def test_fahrenheit_formats_brightness_and_name(self):
        g = Rig()
        g.as_user('administrator')
        g.do('settings.configure', unit='F', date_format='MM-DD-YYYY', time_format='12h', brightness_percent=50)
        self.assertEqual(g.text('f_cur'), '77 °F')
        bright = Rig()
        self.assertNotEqual(g.h.panel(INST).png_bytes('screen'), bright.h.panel(INST).png_bytes('screen'))
        g.store(0, [seg(500, 60.0)])
        g.do('program.edit', slot=0)
        g.tap('mb_programs')
        g.open_program(0)
        g.tap('pv_edit')
        self.assertEqual(g.text('e0_t'), '932 °F')
        g.keypad('e0_t', 752)
        self.assertAlmostEqual(g.state('b510.editing')['program']['segments'][0]['target_C'], 400.0, places=6)   # entries in the display unit (p.90)
        g.tap('ed_save')
        g.tap('ed_close')
        g.tap('status_tab')
        self.assertEqual(g.overlay()['lines'][0], '12:00 AM  01-01-2026')
        g.tap_ov('Close')
        g.tap('mb_furnace')
        g.tap('f_dots')
        g.tap_ov('Edit furnace name')
        for _ in range(20):
            g.tap_ov('key:{bksp}')
        g.type_text('Lab Kiln')
        self.assertEqual(g.text('f_name'), 'Lab Kiln')

    def test_categories_favourites_copy_and_delete(self):
        g = Rig()
        g.store(0, [seg(300, 10.0)], name='A')
        g.store(1, [seg(400, 10.0)], name='B')
        g.tap('mb_programs')
        g.tap('pr_cat')
        g.tap('ct_dots')
        g.tap_ov('New Category')
        g.tap_ov('row:name')
        g.type_text('Drying')
        g.tap_ov('OK')
        self.assertEqual(g.state('b510.categories')[-1], 'Drying')
        g.do('program.set_category', slot=1, category='Drying')
        g.tap('ct_cat4')
        self.assertEqual([r[2] for r in g.widgets()['pr_list']['cells']], ['B'])
        g.tap('pr_cat')
        g.tap('ct_dots')
        g.tap_ov('Edit Category')
        g.tap_ov('row:name')
        g.tap_ov('key:{bksp}')
        g.type_text('Drying 2')
        g.tap_ov('OK')
        self.assertEqual(g.state('b510.programs')[1]['category'], 'Drying 2')     # renamed: the program follows (p.35)
        g.tap('ct_dots')
        g.tap_ov('Delete Category')
        self.assertIsNone(g.state('b510.programs')[1]['category'])
        g.open_program(0)
        g.tap('pv_fav')
        self.assertTrue(g.state('b510.programs')[0]['favourite'])
        g.tap('pv_dots')
        g.tap_ov('Copy Program')
        self.assertEqual(g.state('b510.programs')[2]['name'], 'A')
        for k in (3, 4):
            g.do('program.copy', slot=0)
        self.assertEqual(g.code('program.copy', slot=0), 'PRECONDITION_FAILED')     # no empty slot (p.46)
        self.assertNotIn('pr_new', {k for k, w in g.widgets().items() if w.get('visible', True)})
        g.open_program(4)
        g.tap('pv_dots')
        g.tap_ov('Delete Program')
        g.tap_ov('Yes')
        self.assertIsNone(g.state('b510.programs')[4])
        g.do('program.start', slot=0)
        self.assertEqual(g.code('program.delete', slot=0), 'BUSY')

    def test_documentation_statistics_info_and_usb(self):
        g = Rig()
        g.as_user('administrator')
        g.do('settings.configure', documentation_on=True, documentation_interval_s=60.0, documentation_end='below_limit', documentation_limit_C=200.0)
        g.store(0, [seg(400, 20.0)])
        g.do('program.start', slot=0)
        g.step(1200)
        files = g.state('b510.records')
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['records'], 20)
        self.assertTrue(files[0]['file'].endswith('_15020030_0001.csv'))
        self.assertTrue(g.state('b510.recording'))                                  # cooling recorded down to the limit (p.105)
        g.step(3600 * 6)
        self.assertFalse(g.state('b510.recording'))
        self.assertGreater(g.state('b510.records')[0]['records'], 25)
        g.tap('mb_furnace')
        g.tap('f_dots')
        g.tap_ov('Info menu')
        self.assertEqual(g.page(), 'info')
        self.assertEqual(g.text('in_6_v'), '1 / 1 / 0')
        self.assertEqual(g.text('in_0_v'), 'B510')
        g.tap('in_export')
        self.assertEqual(g.dialog(), 'd_usb')
        self.assertIn('W16', [w['code'] for w in g.state('b510.warnings')])
        g.tap_ov('OK')
        self.assertEqual(g.state('b510.warnings'), [])
        g.tap('mb_settings')
        self.assertFalse(g.tap('se_9'))                                             # Service: Nabertherm only
        self.assertEqual(g.frame()['ui']['unsupported'], 'Service area: Nabertherm service password only (p.49, p.72)')
        g.tap('mb_furnace')
        g.tap('f_dots')
        g.tap_ov('Access the app TAN')
        self.assertEqual(g.dialog(), 'd_no_wifi')

    def test_start_up_wizard(self):
        g = Rig()
        g.login('administrator')
        g.settings(8, 'sy_g7')
        g.form_row('sy_form7', 'wizard')
        self.assertEqual(g.page(), 'wiz_lang')
        g.tap('wz_l_fr')
        g.tap('wz_l_ok')
        g.tap('wz_w_yes')
        self.assertEqual(g.dialog(), 'd_wiz_nowifi')
        g.tap_ov('Next')
        self.assertEqual(g.page(), 'wiz_unit')
        g.tap('wz_u_f')
        g.tap('wz_u_ok')
        g.tap('wz_d_ok')
        self.assertEqual((g.state('b510.settings')['language'], g.state('b510.settings')['unit']), ('fr', 'F'))
        self.assertEqual(g.page(), 'furnace')
        self.assertEqual(g.text('mb_furnace'), 'Four')


class LegacyAndRefusals(unittest.TestCase):
    def test_legacy_commands_with_a_b510_run(self):
        g = Rig()
        g.store(0, [seg(300, 30.0)])
        g.do('program.start', slot=0)
        self.assertEqual(g.code('thermal.set_temperature', temperature_K=500.0), 'BUSY')
        self.assertEqual(g.code('thermal.start_program'), 'BUSY')
        g.do('thermal.stop_program')
        self.assertIsNone(g.state('b510.run'))
        g.do('thermal.configure_program', segments=[{'temperature_K': 400.0, 'ramp_rate_K_s': 0.1, 'hold_s': 60}])
        g.do('thermal.start_program')
        self.assertEqual(g.code('program.start', slot=0), 'BUSY')
        d = g.A.describe(instance_id=INST)
        for k in ('panel.touch', 'panel.switch', 'panel.capture', 'program.start', 'settings.configure'):
            self.assertIn(k, d['capabilities'])
        self.assertNotIn('panel.frame', d['capabilities'])
        self.assertNotIn('diagnostics.inject_error', d['capabilities'])
        self.assertNotIn('thermal.program_library', d['capabilities'])
        self.assertNotIn('b510.passwords', d['state_slots'])
        self.assertIn('diagnostics.set_mains', g.E.describe(instance_id=INST)['capabilities'])
        self.assertEqual(g.call(g.A, 'diagnostics.inject_error', expect='rejected', code='02-01')['error']['code'], 'ACCESS_DENIED')

    def test_refusals_leave_state_unchanged(self):
        g = Rig()
        g.store(0, [seg(300, 30.0)])
        g.do('access.logout')
        before = g.h.debug_snapshot()
        for cap, args, code in (('program.edit', {'slot': 0}, 'PRECONDITION_FAILED'), ('settings.configure', {'unit': 'F'}, 'PRECONDITION_FAILED'),
                                ('program.start', {'slot': 1}, 'PRECONDITION_FAILED'), ('program.start', {'slot': 0, 'delayed_date': '2026-01-01'}, 'INVALID_ARGUMENT'),
                                ('settings.configure', {}, 'INVALID_ARGUMENT'), ('settings.configure', {'brightness_percent': 5}, 'INVALID_ARGUMENT'),
                                ('program.start', {'slot': 0, 'delayed_date': '2025-12-31', 'delayed_time': '10:00'}, 'INVALID_ARGUMENT'),
                                ('access.login', {'user': 'administrator', 'password': '000011'}, 'PRECONDITION_FAILED'), ('program.stop', {}, 'PRECONDITION_FAILED'),
                                ('program.pause', {'on': True}, 'PRECONDITION_FAILED'), ('clock.set', {'date': '2026-02-30'}, 'PRECONDITION_FAILED')):
            self.assertEqual(g.code(cap, **args), code, (cap, args))
        self.assertEqual(before, g.h.debug_snapshot())
        g.as_user('supervisor')
        self.assertEqual(g.code('clock.set', date='2026-02-30'), 'INVALID_ARGUMENT')
        self.assertEqual(g.code('program.edit_save'), 'PRECONDITION_FAILED')        # no program in the editor
        g.do('access.logout')
        g.do('door.open')
        self.assertEqual(g.code('program.start', slot=0), 'PRECONDITION_FAILED')
        g.do('door.close')
        g.switch('O')
        self.assertEqual(g.code('program.start', slot=0), 'PRECONDITION_FAILED')
        g.switch('I')
        g.tap('mb_programs')
        g.tap('pr_list', row=0)
        g.do('door.open')
        g.tap('pv_start')
        g.tap_ov('Yes (hold)', d=2.0)
        self.assertEqual(g.dialog(), 'd_refused')
        self.assertIsNone(g.state('b510.run'))

    def test_another_callers_run_cannot_be_stopped_or_switched_off(self):
        """Lead rule 2026-10-19: B cannot stop or change A's run (panel or direct); A stops its own; the evaluator is unrestricted.  The
        furnace power switch is a hard switch: never refused, the controller reacts as to a power failure (04-03, p.14, p.120; lead
        clarification 2026-10-19)."""
        g = Rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        g.store(0, [seg(300, 30.0)])
        g.open_program(0)
        g.tap('pv_start')
        g.tap_ov('Yes (hold)', d=2.0)                                                 # A starts the program on the panel
        self.assertIsNotNone(g.state('b510.run'))
        before = g.h.debug_snapshot()['instances'][INST]['state']
        for cap, args in (('program.stop', {}), ('program.pause', {'on': True}), ('thermal.stop_program', {})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        a_session, g.A = g.A, B                                                        # B's own gestures on the panel
        g.tap('rs_stop')                                                               # opening the stop dialog is free (viewing)
        self.assertEqual(g.dialog(), 'd_stop')
        self.assertFalse(g.tap_ov('Yes (hold)', d=3.0))                               # B's held Yes: refused
        g.A = a_session
        self.assertEqual(g.dialog(), 'd_denied')                                       # shown: Access denied (another user's program)
        self.assertEqual(g.overlay()['title'], 'Access denied')
        self.assertEqual(g.h.debug_snapshot()['instances'][INST]['state'], before)
        g.do('program.stop')                                                            # A stops its own run
        self.assertIsNone(g.state('b510.run'))
        g.call(B, 'program.start', slot=0)
        g.call(g.E, 'program.stop')                                                     # the evaluator is unrestricted
        self.assertIsNone(g.state('b510.run'))
        g.call(g.A, 'program.start', slot=0)
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='O')['accepted'])   # physical: never refused
        self.assertEqual((g.state('power.enabled'), g.state('b510.run')), (False, None))
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='I')['accepted'])
        self.assertEqual(g.state('b510.error')['code'], '04-03')                       # power failure during a program (p.14, p.120)

    def test_panel_gesture_acts_as_the_calling_session(self):
        """2026-10-19 sync fix: the commands behind a gesture run as the session that made it, not as a fixed panel principal."""
        from unittest import mock
        g = Rig()
        g.store(0, [seg(300, 30.0)])
        callers, real = [], rt.NaberthermL9Session._checked

        def spy(sess, *a, **k):
            callers.append((sess, a[2]))
            return real(sess, *a, **k)
        with mock.patch.object(rt.NaberthermL9Session, '_checked', autospec=True, side_effect=spy):
            g.open_program(0)
            g.tap('pv_start')
            g.tap_ov('Yes (hold)', d=2.0)
        self.assertIsNotNone(g.state('b510.run'))
        inner = [(s, c) for s, c in callers if not c.startswith('panel.')]
        self.assertIn('program.start', [c for _, c in inner])
        self.assertTrue(all(s is g.A for s, c in callers if c != 'panel.frame'))   # the agent's gestures and what they call
        self.assertIsNone(g.h._panel_caller)

    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        self.assertEqual(a.h.panel(INST).png_bytes('screen'), b.h.panel(INST).png_bytes('screen'))
        OUT.mkdir(parents=True, exist_ok=True)
        a.store(0, [seg(600, 40.0), seg(600, 30.0, extras=(True, False)), seg(300, 30.0)], name='GLAZE FIRING')
        a.store(1, [seg(950, 120.0), seg(950, 60.0)], name='SINTERING')
        a.do('program.set_favourite', slot=1, on=True)
        a.do('access.logout')
        shots = {'home': a.h.panel(INST).save_png('screen', OUT / 'home.png')}
        a.tap('mb_programs')
        shots['programs'] = a.h.panel(INST).save_png('screen', OUT / 'programs.png')
        a.tap('mb_settings')
        shots['menu'] = a.h.panel(INST).save_png('screen', OUT / 'menu.png')
        a.do('program.start', slot=0)
        a.step(1800)
        a.tap('mb_furnace')
        a.tap('r_tab_small')
        shots['overview'] = a.h.panel(INST).save_png('screen', OUT / 'overview.png')
        a.as_user('supervisor')
        a.do('program.edit', slot=1)
        a.tap('mb_programs')
        a.open_program(1)
        a.tap('pv_edit')
        shots['editor'] = a.h.panel(INST).save_png('screen', OUT / 'editor.png')
        a.tap('ed_close')
        a.do('door.open')
        a.step(780, dt=30)
        a.tap('mb_furnace')
        a.tap('msg_bar')
        shots['error'] = a.h.panel(INST).save_png('screen', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='screen')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.switch('I')
        self.assertNotEqual(g.call(g.A, 'panel.capture', display_id='screen')['image']['sha256'], r['image']['sha256'])

    def test_spec_and_contract(self):
        meta = rt.export_nabertherm_contract_metadata()['asset_types'][rt.NABERTHERM_TYPE]
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities']) | set(meta['controller_capabilities'])), [])
        self.assertEqual(set(meta['panel_capabilities']), {'panel.touch', 'panel.switch', 'panel.capture', 'panel.frame'})
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['controller_capabilities'], **meta['evaluator_capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        self.assertNotIn('thermal.program_library', c['capabilities'])
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        self.assertIn('power_switch', c['components'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class']), ('panel_ui', 'touchscreen_gui'))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('l9_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
