"""front-panel tests for CHEM-047 multiwave_7101 (approximate touchscreen after the Multiwave 5000 software; panel_ui.json +
scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_thermal.py): python3 -m pytest tests/test_panel_ui_multiwave_7101.py -q

Every gesture goes through the session API (panel.touch / panel.switch): taps land on the widget rectangles of the frame (pages, dialogs,
keyboards, choice lists).  Themes: the survey's key procedures (run a pre-installed method, create a method, Step Editing during a run,
finish a run and read the Data Explorer, log out and log in); copy / delete and Add / Delete Step; users, roles, passwords, auto login / logout,
fan level, cavity pressure and run graph (2026-10-19 sync fixes); panel = direct capability; the program
engine behind the panel (ramp and hold, Temperature is reached at 90 %, power programs, user break and safety cooling, power off); refusals;
render determinism and the PNGs of outputs/panel_ui/multiwave_7101/; contract entries."""
import hashlib
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_thermal as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'mw_01'
C0 = 273.15
OUT = ROOT / 'outputs/panel_ui/multiwave_7101'
ASSET = ROOT / 'assets/basic40_batch02/t1_thermal/multiwave_7101'
SPEC = rt._mw_panel()['spec']
SURVEY_TASKS = ['find and run a pre-installed method', 'create a new method', 'change a step during a run', 'finish a run', 'log out']
DONE_TASKS = set()


class Rig:
    def __init__(self, on=True, scene=True):
        self.h = rt.Multiwave7101Runtime(scene_adapter=(lambda key, values: None) if scene else None)
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if on:
            assert self.call(self.A, 'panel.switch', input_id='mains_switch', position='on')['accepted']

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if expect == 'succeeded' else r

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['displays']['screen']

    def page(self):
        return self.screen().get('page')

    def widgets(self):
        return {w['id']: w for w in self.screen().get('widgets', [])}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def tap_xy(self, x, y):
        return self.call(self.A, 'panel.touch', display_id='screen', x=int(x), y=int(y))['accepted']

    def tap(self, wid, row=None):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        if row is None:
            return self.tap_xy(x + ww / 2, y + hh / 2)
        rh = w.get('row_h', 40)
        return self.tap_xy(x + 10, y + row * rh + rh / 2)

    def overlay(self):
        return self.screen().get('overlay')

    def tap_ov(self, bid):
        for b in self.overlay()['buttons']:
            if b['id'] == bid or b['label'] == bid:
                x, y, w, hh = b['rect']
                return self.tap_xy(x + w / 2, y + hh / 2)
        raise AssertionError((bid, [b['id'] for b in self.overlay()['buttons']]))

    def type_text(self, t):
        for ch in t:
            self.tap_ov('key:{space}' if ch == ' ' else 'key:' + ch)
        self.tap_ov('key:{ok}')

    def type_num(self, t):
        for ch in t:
            self.tap_ov('key:' + ch)
        self.tap_ov('key:{ok}')

    def rows(self, wid):
        return self.widgets()[wid]['rows']

    def state(self, sid, path=None):
        v = self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']
        return v[path] if path is not None else v

    def step(self, s, dt=30.0):
        while s > 1e-9:
            self.h.step(min(s, dt))
            s -= min(s, dt)
            self.frame()

    def run_until(self, status, limit=4 * 3600.0, dt=60.0):
        t = 0.0
        while self.state('run.status') != status and t < limit:
            self.step(dt, dt)
            t += dt
        assert self.state('run.status') == status, self.state('run.status')
        return t


def state_dump(h):
    inst = h.debug_snapshot()['instances'][INST]
    return json.dumps(inst['state'], sort_keys=True, default=float)


class KeyProcedures(unittest.TestCase):
    def test_find_and_run_a_pre_installed_method(self):
        g = Rig(on=False)
        self.assertEqual(g.frame()['screen'], 'off')
        self.assertEqual(g.screen()['brightness'], 0)
        g.call(g.A, 'panel.switch', input_id='mains_switch', position='on')
        self.assertEqual((g.page(), g.text('status')), ('home', 'Ready'))
        g.tap('fav_methods')                                                           # Menu > Methods (p.48)
        self.assertEqual(g.page(), 'methods')
        self.assertEqual(g.rows('method_list')[2], 'EPA 3052 HNO3+HF  Temperature  pre-installed')
        g.tap('method_list', row=2)
        self.assertEqual(g.state('methods.selected'), 'EPA 3052 HNO3+HF')
        g.tap('m_info2')                                                               # More info: the program
        self.assertEqual((g.page(), g.screen()['title']), ('method_info', 'EPA 3052 HNO3+HF'))
        self.assertEqual(g.rows('step_list'), ['1  180 °C  05:30  Time is reached  1', '2  180 °C  09:30  Time is reached  1'])
        self.assertFalse(g.widgets()['mi_edit']['enabled'])                           # pre-installed: cannot be modified (p.48)
        g.tap('start')                                                                  # START: MyData view (p.50)
        self.assertEqual((g.page(), g.state('run.status'), g.state('run.method'), g.state('door.closed')), ('mydata', 'running', 'EPA 3052 HNO3+HF', True))
        self.assertEqual(g.text('md_status'), 'Running: EPA 3052 HNO3+HF   step 1 / 2')
        g.step(165.0, dt=5.0)                                                           # half the ramp: half way from 25 to 180 degC
        self.assertAlmostEqual(g.state('thermal.target_k'), 25 + C0 + 0.5 * 155, places=6)
        self.assertEqual(g.text('v_md_sp'), f'{25 + 0.5 * 155:.1f} °C')
        self.assertEqual(g.call(g.A, 'door.open', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')   # door locked during the run
        g.step(165.0, dt=5.0)
        self.assertEqual(g.state('run.step_index'), 1)
        self.assertAlmostEqual(g.state('thermal.indicated_temperature_k'), 180 + C0, places=6)
        self.assertEqual(g.text('v_md_rem'), '09:30')
        DONE_TASKS.add('find and run a pre-installed method')

    def test_create_a_new_method(self):
        g = Rig()
        g.tap('fav_methods')
        g.tap('m_create')                                                              # Create Method (p.49)
        g.tap_ov('row:name')
        g.type_text('ash test')
        g.tap_ov('row:max_t')
        g.type_num('200')
        g.tap_ov('Create')
        self.assertEqual((g.page(), g.state('methods.selected')), ('method_info', 'ash test'))
        m = g.state('methods.library')[-1]
        self.assertEqual((m['name'], m['program'], m['max_temperature_C'], m['preinstalled'], len(m['steps'])), ('ash test', 'temperature', 200.0, False, 1))
        g.tap('mi_edit')                                                               # Step Editor: temperature, time, step type, fan (p.50)
        g.tap_ov('row:temp')
        g.type_num('150')
        g.tap_ov('row:time')
        g.type_num('2')
        g.tap_ov('row:type')
        g.tap_ov('Temperature is reached')
        g.tap_ov('Save')
        self.assertIsNone(g.overlay())
        g.tap('mi_add')
        g.tap('step_list', row=1)
        g.tap('mi_edit')
        g.tap_ov('row:time')
        g.type_num('3')
        g.tap_ov('Save')
        self.assertEqual(g.rows('step_list'), ['1  150 °C  02:00  Temperature is reached  1', '2  150 °C  03:00  Temperature is reached  1'])
        g.tap('mi_edit')                                                               # above the method's limit: refused, the step stays
        g.tap_ov('row:temp')
        g.type_num('250')
        g.tap_ov('Save')
        self.assertEqual(g.overlay()['dialog'], 'd_refused')
        g.tap_ov('OK')
        g.tap_ov('Cancel')
        self.assertEqual(g.state('methods.library')[-1]['steps'][1]['temperature_C'], 150.0)
        DONE_TASKS.add('create a new method')

    def test_change_a_step_during_a_run(self):
        g = Rig()
        g.tap('start')                                                                  # EPA 3051A HNO3: 175 degC ramp 5.5 min, hold 4.5 min
        g.step(60.0, dt=5.0)
        g.tap('md_steps')                                                              # Step Editing (p.51)
        self.assertEqual(g.page(), 'run_steps')
        g.tap('run_list', row=1)
        g.tap('rs_edit')
        g.tap_ov('row:temp')
        g.type_num('170')
        g.tap_ov('row:time')
        g.type_num('6')
        g.tap_ov('Save')
        self.assertEqual(g.state('run.steps')[1], {'temperature_C': 170.0, 'power_W': None, 'time_s': 360.0, 'step_type': 'time', 'fan': 1})
        self.assertEqual(g.state('methods.library')[0]['steps'][1]['time_s'], 270.0)   # the stored method is unchanged
        g.tap('rs_add')
        self.assertEqual(len(g.state('run.steps')), 3)
        g.tap('run_list', row=2)
        g.tap('rs_del')
        self.assertEqual(len(g.state('run.steps')), 2)
        g.tap('run_list', row=0)                                                        # the current step itself: its ramp changes at once
        g.tap('rs_edit')
        g.tap_ov('row:temp')
        g.type_num('160')
        g.tap_ov('Save')
        self.assertAlmostEqual(g.state('thermal.target_k'), C0 + 25 + (160 - 25) * 60 / 330, places=6)
        g.step(600.0, dt=10.0)                                                          # 660 s: 330 s into step 2 (160 -> 170 degC over 6 min)
        self.assertEqual(g.state('run.step_index'), 1)
        self.assertAlmostEqual(g.state('thermal.target_k'), 160 + C0 + 10 * 330 / 360, places=6)
        DONE_TASKS.add('change a step during a run')

    def test_finish_a_run(self):
        g = Rig()
        g.tap('start')
        t = g.run_until('cooling')
        self.assertAlmostEqual(t, 600.0, delta=60.0)                                   # 5.5 + 4.5 min
        self.assertEqual(g.text('md_status')[:14], 'Safety cooling')
        self.assertEqual(g.call(g.A, 'door.open', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')
        g.run_until('finished')
        self.assertLessEqual(g.state('thermal.indicated_temperature_k'), 60 + C0 + 0.05)
        self.assertEqual(g.text('md_status'), 'Finished: unload the vessels')
        g.tap('md_home')
        g.tap('door')                                                                   # unload: the door opens again
        self.assertEqual((g.state('door.closed'), g.state('run.status')), (False, 'idle'))
        g.tap('fav_data')                                                              # Data Explorer (p.52)
        self.assertTrue(g.rows('record_list')[0].startswith('001  EPA 3051A HNO3  2026-01-01 00:00  completed'))
        g.tap('record_list', row=0)
        self.assertEqual(g.overlay()['dialog'], 'd_record')
        g.tap_ov('OK')
        g.tap('d_export')
        self.assertEqual(g.overlay()['lines'], ['No USB storage device found.'])
        rec = g.state('data.experiments')[0]
        self.assertEqual((rec['result'], rec['max_temperature_C'], rec['steps']), ('completed', 175.0, 2))
        DONE_TASKS.add('finish a run')

    def test_log_out(self):
        """2026-10-19 sync fix: system security and user management (mw5000 p.38-41) instead of an unsupported button."""
        g = Rig()
        self.assertEqual((g.state('users.current'), g.text('v_user')), ('administrator', 'A  Administrator (administrator)'))   # auto login (p.38)
        g.tap('menu')
        g.tap('m_logout')
        self.assertEqual(g.page(), 'user')
        g.tap('u_logout')                                                              # LOG OUT: the login screen appears
        self.assertEqual((g.state('users.current'), g.frame()['screen'], g.page()), (None, 'login', 'login'))
        self.assertEqual([c[0] for c in g.widgets()['login_list']['cells']], ['Administrator', 'Chemist', 'Operator'])
        self.assertFalse(g.widgets()['lg_login']['enabled'])
        g.tap('login_list', row=1)
        g.tap('lg_login')
        self.assertEqual((g.state('users.current'), g.frame()['screen'], g.page()), ('chemist', 'gui', 'home'))
        DONE_TASKS.add('log out')


class OtherFunctions(unittest.TestCase):
    def test_copy_delete_and_settings(self):
        g = Rig()
        g.tap('fav_methods')
        g.tap('m_copy')                                                                # a copy gets a timestamp (p.50)
        name = g.state('methods.selected')
        self.assertTrue(name.startswith('EPA 3051A HNO3 20260101_0000'))
        self.assertTrue(g.widgets()['m_delete']['enabled'])
        g.tap('m_delete')
        self.assertEqual(len(g.state('methods.library')), 5)
        g.tap('m_back')
        g.tap('fav_settings')
        g.tap('w_settings', row=0)
        g.type_num('40')
        self.assertEqual(g.state('settings.brightness_percent'), 40.0)
        self.assertEqual(g.screen()['brightness'], 40.0)

    def test_power_program_and_temperature_is_reached(self):
        g = Rig()
        g.call(g.A, 'methods.create', name='power', program='power', max_power_W=1500.0, max_temperature_C=120.0)
        g.call(g.A, 'methods.set_step', name='power', index=0, power_W=1000.0, time_s=1200.0, fan=2)
        g.call(g.A, 'run.start')
        g.step(30.0, dt=30.0)
        self.assertEqual((g.state('microwave.power_limit_w'), g.state('run.controller_state')), (1000.0, 'P'))
        g.step(600.0)
        self.assertAlmostEqual(g.state('thermal.indicated_temperature_k'), 120 + C0, places=6)   # limited by the method's temperature limit
        self.assertEqual(g.state('run.controller_state'), 'T')
        g.call(g.A, 'run.abort')                                                        # user break
        self.assertEqual((g.state('run.status'), g.state('run.controller_state'), g.state('microwave.enabled')), ('cooling', 'U', False))
        g.run_until('finished')
        self.assertEqual(g.state('data.experiments')[-1]['result'], 'aborted')
        g.call(g.A, 'methods.create', name='reach', program='temperature', max_power_W=1500.0, max_temperature_C=300.0)
        g.call(g.A, 'methods.set_step', name='reach', index=0, temperature_C=200.0, time_s=900.0, step_type='temperature', fan=1)
        g.call(g.A, 'methods.add_step', name='reach')
        g.call(g.A, 'methods.set_step', name='reach', index=1, temperature_C=200.0, time_s=60.0, step_type='time', fan=1)
        t0_k = g.state('thermal.temperature_k')
        g.call(g.A, 'run.start')
        t = 0.0
        while g.state('run.step_index') == 0:
            g.step(1.0, dt=1.0)
            t += 1.0
        c, ua, p, ta = rt.MULTIWAVE_7101_CAPACITY_J_K, rt.MULTIWAVE_7101_LOSS_W_K, 1500.0, 298.15
        t90 = c / ua * math.log((ta + p / ua - t0_k) / (ta + p / ua - (0.9 * 200 + C0)))   # 90 % of 200 degC at full power (p.50)
        self.assertAlmostEqual(t, math.ceil(t90), delta=1.0)


class SyncFixes(unittest.TestCase):
    """2026-10-19 sync fixes: roles, passwords, auto login / logout, the fan level, the cavity pressure and graph, the panel caller."""

    def login(self, g, user, password=None):
        g.tap('login_list', row=[c[1] for c in g.widgets()['login_list']['cells']].index(user))
        g.tap('lg_login')
        if password is not None:
            self.assertEqual(g.overlay()['dialog'], 'd_login')
            g.tap_ov('row:pw')
            g.type_text(password)
            g.tap_ov('Login')

    def test_roles_gate_the_functions_and_stop_needs_no_login(self):
        g = Rig()
        g.call(g.A, 'user.logout')
        g.frame()
        self.login(g, 'operator')
        g.tap('fav_settings')
        g.tap('w_settings', row=0)
        g.type_num('40')
        self.assertEqual(g.overlay()['dialog'], 'd_refused')                           # operators may not change settings
        self.assertEqual(g.state('settings.brightness_percent'), 100.0)
        g.tap_ov('OK')
        for cap, args in (('methods.create', dict(name='OPS', program='temperature', max_power_W=1500.0, max_temperature_C=300.0)),
                          ('methods.copy', dict(name='EPA 3051A HNO3')), ('settings.configure', dict(brightness_percent=50.0)),
                          ('users.add', dict(user_name='analyst1', full_name='Lab Analyst', role='operator'))):
            before = state_dump(g.h)
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'PRECONDITION_FAILED', cap)
            self.assertEqual(before, state_dump(g.h))
        g.call(g.A, 'run.start')                                                       # operators perform experiments
        g.call(g.A, 'run.set_step', expect='rejected', index=1, temperature_C=170.0, time_s=300.0, step_type='time', fan=1)
        g.call(g.A, 'user.logout')
        g.step(60.0)
        self.assertEqual((g.frame()['screen'], g.state('run.status')), ('login', 'running'))   # the run goes on (p.38)
        g.tap('lg_stop')                                                               # STOP without a login (p.38 notice)
        self.assertEqual(g.state('run.status'), 'cooling')
        self.login(g, 'chemist')
        g.run_until('finished')
        g.call(g.A, 'methods.create', name='CHEM', program='temperature', max_power_W=1500.0, max_temperature_C=300.0)   # chemists edit methods
        g.call(g.A, 'settings.configure', expect='rejected', brightness_percent=50.0)

    def test_password_rules_users_and_auto_logout(self):
        g = Rig()
        g.call(g.A, 'security.configure', password_required=True, auto_logout=True, logout_timeout_min=5)
        g.call(g.A, 'users.add', expect='rejected', user_name='short', full_name='Too Short', role='operator', password='secretpw')   # 6 characters (table 6)
        g.call(g.A, 'users.add', expect='rejected', user_name='analyst1', full_name='Lab Analyst', role='operator', password='short')   # 8 characters
        g.call(g.A, 'users.add', user_name='analyst1', full_name='Lab Analyst', role='operator', password='secretpw')
        self.assertEqual(g.state('users.accounts')[-1]['initials'], 'LA')
        self.assertNotIn('users.passwords', g.A.read(instance_id=INST)['observations'])
        g.call(g.A, 'users.set_active', expect='rejected', user_name='administrator', active=False)   # never lock yourself out
        g.step(299.0, dt=299.0)
        self.assertEqual(g.state('users.current'), 'administrator')
        g.step(2.0, dt=2.0)                                                             # 5 min without an operation: logged out (p.39)
        self.assertEqual((g.state('users.current'), g.frame()['screen']), (None, 'login'))
        self.login(g, 'analyst1', 'wrongpw')
        self.assertEqual(g.state('users.current'), None)                               # wrong password
        g.tap_ov('OK') if g.overlay() and g.overlay().get('dialog') == 'd_refused' else None
        self.login(g, 'analyst1', 'secretpw')
        self.assertEqual(g.state('users.current'), 'analyst1')
        g.tap('v_user')
        g.tap('u_pw')
        g.tap_ov('row:old')
        g.type_text('secretpw')
        g.tap_ov('row:new')
        g.type_text('newsecret')
        g.tap_ov('Change')
        self.assertEqual(g.h._instances[INST]['state']['users.passwords']['analyst1'], 'newsecret')
        g.call(g.A, 'user.logout')
        g.call(g.A, 'user.login', user_name='administrator', password='administrator')
        g.call(g.A, 'users.remove', user_name='analyst1')
        g.call(g.A, 'users.add', expect='rejected', user_name='analyst1', full_name='Lab Analyst', role='operator', password='secretpw')   # names stay used (p.40)
        g.call(g.A, 'security.configure', auto_login=False)
        g.call(g.A, 'panel.switch', input_id='mains_switch', position='off')
        g.call(g.A, 'panel.switch', input_id='mains_switch', position='on')
        self.assertEqual((g.state('users.current'), g.frame()['screen']), (None, 'login'))

    def test_fan_level_scales_the_cooling_and_the_pressure_is_shown(self):
        def cooled(fan):
            g = Rig()
            g.call(g.A, 'methods.create', name='FAN', program='temperature', max_power_W=1500.0, max_temperature_C=300.0)
            g.call(g.A, 'methods.set_step', name='FAN', index=0, temperature_C=180.0, time_s=600.0, step_type='time', fan=fan)
            g.call(g.A, 'run.start')
            g.step(600.0)
            peak = g.state('thermal.temperature_k')
            g.run_until('cooling', dt=10.0)
            g.step(300.0)
            return g, peak, g.state('thermal.temperature_k')
        g0, p0, t0 = cooled(0)
        g3, p3, t3 = cooled(3)
        self.assertEqual(g3.state('run.fan_level'), 3)                                # cooling keeps the last step level
        self.assertLess(t3 - 298.15, (t0 - 298.15) * 0.95)                            # fan 3 cools faster than fan 0
        self.assertLessEqual(p3, p0 + 1e-9)
        g = Rig()
        g.tap('start')
        g.step(600.0)
        p = g.state('cavity.pressure_pa')
        self.assertGreater(p, 5e5)                                                     # 175 C: water saturation ~8.9 bar (IAPWS table)
        self.assertEqual(g.text('v_md_pr'), f'{p / 1e5:.1f} bar')
        g.tap('md_graph')
        self.assertEqual(g.page(), 'graph')
        self.assertGreater(len(g.state('run.trace')), 15)
        g.run_until('finished')
        self.assertGreater(g.state('data.experiments')[-1]['max_pressure_bar'], 5.0)
        g.call(g.A, 'door.open')
        self.assertEqual(g.state('cavity.pressure_pa'), 101325.0)

    def test_another_callers_run_cannot_be_stopped_or_switched_off(self):
        """Lead rule 2026-10-19: B cannot STOP or edit A's run (panel or direct); A stops its own; the evaluator is unrestricted.  The mains
        switch is a hard switch: never refused, the run ends as on a power loss (lead clarification 2026-10-19)."""
        g = Rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        g.tap('start')                                                                 # A starts the method on the panel
        self.assertEqual((g.state('run.status'), g.page()), ('running', 'mydata'))
        before = state_dump(g.h)
        for cap, args in (('run.abort', {}), ('run.add_step', {}), ('run.set_step', {'index': 1, 'temperature_C': 170.0, 'time_s': 300.0, 'step_type': 'time', 'fan': 1})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        x, y, w, h = g.widgets()['md_stop']['rect']
        self.assertFalse(g.call(B, 'panel.touch', display_id='screen', x=int(x + w / 2), y=int(y + h / 2))['accepted'])   # B's STOP: refused
        self.assertEqual((g.overlay()['dialog'], g.overlay()['title']), ('d_denied', 'Access denied'))   # shown as a message box
        self.assertEqual(state_dump(g.h), before)
        g.tap_ov('OK')
        g.tap('md_stop')                                                               # A stops its own run (user break)
        self.assertEqual(g.state('run.status'), 'cooling')
        g.run_until('finished')
        g.call(B, 'run.start')
        g.call(g.E, 'run.abort')                                                       # the evaluator is unrestricted
        self.assertEqual(g.state('run.status'), 'cooling')
        self.assertTrue(g.call(B, 'panel.switch', input_id='mains_switch', position='off')['accepted'])   # physical: never refused
        self.assertEqual((g.state('run.status'), g.state('data.experiments')[-1]['result']), ('idle', 'aborted (power off)'))

    def test_panel_gesture_acts_as_the_calling_session(self):
        from unittest import mock
        g = Rig()
        callers, real = [], rt.Multiwave7101Session._checked

        def spy(sess, *a, **k):
            callers.append((sess, a[2]))
            return real(sess, *a, **k)
        with mock.patch.object(rt.Multiwave7101Session, '_checked', autospec=True, side_effect=spy):
            g.tap('start')
        self.assertEqual(g.state('run.status'), 'running')
        self.assertIn('run.start', [c for _, c in callers])
        self.assertTrue(all(s is g.A for s, c in callers if c != 'panel.frame'))
        self.assertIsNone(g.h._panel_caller)


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        a, b = Rig(), Rig()
        a.tap('fav_methods')
        a.tap('method_list', row=3)
        a.tap('start')
        b.call(b.A, 'methods.select', name='EPA 3015A HNO3')
        b.call(b.A, 'run.start')
        self.assertEqual(state_dump(a.h), state_dump(b.h))
        for g in (a, b):
            g.step(120.0, dt=10.0)
        a.tap('md_stop')
        b.call(b.A, 'run.abort')
        self.assertEqual(state_dump(a.h), state_dump(b.h))


class Refusals(unittest.TestCase):
    def test_refusals_leave_state_unchanged(self):
        g = Rig()
        g.tap('door')
        before = state_dump(g.h)
        g.tap('start')                                                                  # door open: message, nothing starts
        self.assertEqual(g.overlay()['dialog'], 'd_door')
        g.tap_ov('OK')
        self.assertEqual(state_dump(g.h), before)
        for cap, args, code in [('run.start', {}, 'PRECONDITION_FAILED'), ('methods.delete', {'name': 'EPA 3051A HNO3'}, 'PRECONDITION_FAILED'),
                                ('methods.set_step', {'name': 'EPA 3051A HNO3', 'index': 0, 'temperature_C': 100.0, 'time_s': 60.0, 'fan': 1}, 'PRECONDITION_FAILED'),
                                ('methods.select', {'name': 'nothing'}, 'PRECONDITION_FAILED'), ('run.abort', {}, 'PRECONDITION_FAILED'),
                                ('methods.create', {'name': 'EPA 3051A HNO3', 'program': 'power', 'max_power_W': 1.0, 'max_temperature_C': 1.0}, 'INVALID_ARGUMENT'),
                                ('settings.configure', {'brightness_percent': 5.0}, 'INVALID_ARGUMENT')]:
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], code, cap)
            self.assertEqual(state_dump(g.h), before, cap)
        g.tap('door')
        g.tap('start')
        for cap, args in [('thermal.set_temperature', {'temperature_K': 400.0}), ('microwave.set_power', {'power_W': 100.0}), ('microwave.set_enabled', {'enabled': False}),
                          ('methods.select', {'name': 'EPA 3052 HNO3+HF'}), ('run.start', {}), ('run.delete_step', {'index': 0})]:
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'PRECONDITION_FAILED', cap)
        g.step(60.0)
        g.call(g.A, 'panel.switch', input_id='mains_switch', position='off')           # power off ends the experiment
        self.assertEqual((g.state('run.status'), g.state('data.experiments')[-1]['result']), ('idle', 'aborted (power off)'))
        self.assertFalse(g.call(g.A, 'panel.touch', display_id='screen', x=400, y=200)['accepted'])

    def test_dialogs_stay_until_confirmed(self):
        g = Rig()
        g.tap('door')
        g.tap('start')
        g.step(600.0)
        self.assertEqual(g.overlay()['dialog'], 'd_door')                              # no time-out documented: the message waits for OK


class ErrorCodes(unittest.TestCase):
    """Sweep 2026-10-19 (lead rule 3): the error codes of Appendix A (mw5000 p.57-62) that do not depend on Multiwave 5000 hardware, by the
    evaluator-only fault.inject / fault.clear; the error page, OK, refused START and the clearing classes."""

    def test_error_aborts_the_run_and_ends_with_ok(self):
        g = Rig()
        g.tap('start')
        g.step(120.0)
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='102')['error']['code'], 'ACCESS_DENIED')   # evaluator only
        self.assertNotIn('fault.inject', g.A.describe(instance_id=INST)['capabilities'])
        self.assertEqual(g.call(g.E, 'fault.inject', expect='rejected', code='038')['error']['code'], 'INVALID_ARGUMENT')   # IR sensor (5000 hardware): not offered
        g.call(g.E, 'fault.inject', code='102')                                        # Significant Pressure Limit Overrange (p.60)
        self.assertEqual((g.state('run.status'), g.state('microwave.enabled'), g.state('error.code')), ('cooling', False, '102'))
        self.assertEqual((g.frame()['screen'], g.screen()['title']), ('error', 'Error 102'))   # the message page over every page
        self.assertEqual(g.text('er_msg'), 'Significant Pressure Limit Overrange! The process has to be aborted.')
        self.assertEqual(g.text('er_handling'), 'Handling: Adjust the method parameter to the sample and weight.')
        g.h.panel(INST).save_png('screen', OUT / 'fault.png')
        self.assertEqual(g.call(g.A, 'run.start', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')
        g.tap('er_ok')                                                                 # OK: the method error ends, back on Home
        self.assertEqual((g.frame()['screen'], g.page(), g.state('error.code'), g.state('error.popup')), ('gui', 'home', None, False))
        self.assertEqual(g.text('status')[:14], 'Safety cooling')
        g.run_until('finished')
        self.assertEqual(g.state('data.experiments')[-1]['result'], 'aborted (error 102)')

    def test_restart_fix_and_service_classes(self):
        g = Rig()
        g.call(g.E, 'fault.inject', code='001')                                        # Sensor value failure: restart the instrument (p.57)
        g.tap('er_ok')
        self.assertEqual((g.page(), g.state('error.code')), ('home', '001'))           # the cause persists: code on the status line
        self.assertEqual(g.text('status'), 'Error 001: Sensor value failure. Restart the instrument.')
        g.tap('start')
        self.assertEqual((g.overlay()['dialog'], g.overlay()['title']), ('d_error', 'Error 001'))   # START: the error again
        g.tap_ov('OK')
        self.assertEqual(g.call(g.A, 'microwave.set_enabled', expect='rejected', enabled=True)['error']['code'], 'PRECONDITION_FAILED')
        for pos in ('off', 'on'):                                                     # switched off and on: cleared
            g.call(g.A, 'panel.switch', input_id='mains_switch', position=pos)
        self.assertEqual((g.state('error.code'), g.state('fault.injected'), g.page()), (None, [], 'home'))
        g.call(g.E, 'fault.inject', code='020')                                        # Magnetron 1 too hot: check the cooling air inlets (p.58)
        g.tap('er_ok')
        for pos in ('off', 'on'):                                                     # a restart does not remove the cause: shown again
            g.call(g.A, 'panel.switch', input_id='mains_switch', position=pos)
        self.assertEqual((g.frame()['screen'], g.state('error.code')), ('error', '020'))
        g.call(g.E, 'fault.clear', code='020')                                         # inlets checked: OK ends the error
        g.tap('er_ok')
        self.assertEqual((g.state('error.code'), g.text('status')), (None, 'Ready'))
        self.assertEqual(g.call(g.E, 'fault.clear', expect='rejected', code='020')['error']['code'], 'PRECONDITION_FAILED')
        g.tap('start')
        self.assertEqual(g.state('run.status'), 'running')

    def test_warning_keeps_the_run_and_error_while_switched_off(self):
        g = Rig()
        g.tap('start')
        g.step(60.0)
        g.call(g.E, 'fault.inject', code='10A')                                        # heating rate insufficient: a warning (p.51, p.61)
        self.assertEqual((g.state('run.status'), g.screen()['title']), ('running', 'Warning 10A'))
        self.assertIn('error 10A', g.state('run.warnings')[-1])
        g.tap('er_ok')
        self.assertEqual((g.state('error.code'), g.state('run.status')), (None, 'running'))
        h = Rig(on=False)
        self.assertEqual(h.call(h.E, 'fault.inject', expect='rejected', code='102')['error']['code'], 'PRECONDITION_FAILED')   # no run while off
        h.call(h.E, 'fault.inject', code='026')                                        # injected while off: shown at switch-on
        h.call(h.A, 'panel.switch', input_id='mains_switch', position='on')
        self.assertEqual((h.frame()['screen'], h.screen()['title']), ('error', 'Error 026'))


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        self.assertEqual(a.h.panel(INST).png_bytes('screen'), b.h.panel(INST).png_bytes('screen'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel(INST).save_png('screen', OUT / 'home.png')}
        a.tap('menu')
        shots['menu'] = a.h.panel(INST).save_png('screen', OUT / 'menu.png')
        a.tap('m_methods')
        a.tap('m_info2')
        shots['method'] = a.h.panel(INST).save_png('screen', OUT / 'method.png')
        a.tap('start')
        a.step(400.0, dt=10.0)
        shots['mydata'] = a.h.panel(INST).save_png('screen', OUT / 'mydata.png')
        a.tap('md_graph')                                                              # run graph: temperature and cavity pressure (2026-10-19)
        shots['graph'] = a.h.panel(INST).save_png('screen', OUT / 'graph.png')
        a.tap('g_back')
        a.tap('md_home')
        a.tap('door')
        a.tap('start')
        shots['error'] = a.h.panel(INST).save_png('screen', OUT / 'error.png')
        a.call(a.A, 'user.logout')                                                     # the login screen (2026-10-19)
        a.frame()
        shots['login'] = a.h.panel(INST).save_png('screen', OUT / 'login.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='screen')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.call(g.A, 'panel.switch', input_id='mains_switch', position='on')
        self.assertNotEqual(g.call(g.A, 'panel.capture', display_id='screen')['image']['sha256'], r['image']['sha256'])

    def test_spec_and_contract(self):
        meta = rt.export_multiwave_7101_metadata()['asset_types'][rt.MULTIWAVE_7101_TYPE]
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities']) | set(meta['controller_capabilities'])), [])
        self.assertTrue(SPEC['approximate'])
        self.assertEqual(set(meta['panel_capabilities']), {'panel.touch', 'panel.switch', 'panel.capture', 'panel.frame'})
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['controller_capabilities'], **meta['evaluator_capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        self.assertIn('mains_switch', c['components'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['approximate']), ('panel_ui', 'touchscreen_gui', True))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime', 'template_tests'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('mw_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
