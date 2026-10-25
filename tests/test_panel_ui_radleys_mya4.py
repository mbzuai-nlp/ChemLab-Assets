"""front-panel tests for CHEM-021 radleys_mya4 (Mya 4 Control Pad; panel_ui.json + scripts/asset_runtime/_panel_ui.py on
scripts/basic40_runtime_thermal.py): python3 -m pytest tests/test_panel_ui_radleys_mya4.py -q

Every gesture goes through the session API (panel.touch on the widget, dialog, keyboard and keypad rectangles of the frame; panel.switch for
the station switch).  Themes: the seven key procedures of the menu survey (Manual run, profile run with Pause / Skip / Stop, safety limits,
export to USB = no stick, copy a profile to another zone, read and handle an error, shut down); System Settings (storage interval, Demo mode,
clock), Service Settings and PID Gains behind the daily password, Network Share, File Manager filter / import / delete; panel = direct
capability; the pad functions behind the panel (ramp and end conditions, trips, 7-day limit, injected faults); refusals; time-outs (contact
lost, double-tap zoom); render determinism and the PNGs of outputs/panel_ui/radleys_mya4/; contract entries."""
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

INST = 'mya_01'
C0 = 273.15
OUT = ROOT / 'outputs/panel_ui/radleys_mya4'
ASSET = ROOT / 'assets/basic40_batch02/t1_thermal/radleys_mya4'
SPEC = rt._mya4_panel()['spec']
SURVEY_TASKS = ['run a manual experiment in one zone', 'create and run a temperature profile', 'set zone safety limits', 'export an experiment to USB',
                'copy a profile to another zone', 'read and handle an error', 'shut down']
DONE_TASKS = set()


class Rig:
    def __init__(self, on=True, cooling=True):
        self.h = rt.RadleysMya4Runtime(scene_adapter=lambda key, values: None)
        self.h.create_instance(INST)
        if cooling:
            self.h.configure_cooling_source(available=True, temperature_K=278.15, flow_m3_s=0.003 / 60)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if on:
            assert self.switch('on')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if expect == 'succeeded' else r

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='station_switch', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def pad(self):
        return self.frame()['displays']['pad']

    def page(self):
        return self.pad().get('page')

    def widgets(self):
        return {w['id']: w for w in self.pad().get('widgets', [])}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def tap_xy(self, x, y):
        return self.call(self.A, 'panel.touch', display_id='pad', x=int(x), y=int(y))['accepted']

    def tap(self, wid, row=None):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        if row is None:
            return self.tap_xy(x + ww / 2, y + hh / 2)
        rh = w.get('row_h', 40)
        return self.tap_xy(x + 10, y + row * rh + rh / 2)

    def overlay(self):
        return self.pad().get('overlay')

    def tap_ov(self, bid):
        for b in self.overlay()['buttons']:
            if b['id'] == bid or b.get('label') == bid:
                x, y, w, hh = b['rect']
                return self.tap_xy(x + w / 2, y + hh / 2)
        raise AssertionError((bid, [b['id'] for b in self.overlay()['buttons']]))

    def keypad(self, wid, value):
        """Tap a number box, type the value on the keypad, Apply."""
        self.tap(wid)
        for ch in str(value):
            self.tap_ov('key:' + ch)
        return self.tap_ov('key:{ok}')

    def type_text(self, t):
        for ch in t:
            if ch.isupper():
                self.tap_ov('key:{shift}')
            self.tap_ov('key:{space}' if ch == ' ' else 'key:' + ch)
            if ch.isupper():
                self.tap_ov('key:{shift}')
        self.tap_ov('key:{ok}')

    def state(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def exp(self, zone):
        return self.state(f'zone_{zone}.pad.experiment')

    def step(self, s, dt=10.0):
        while s > 1e-9:
            self.h.step(min(s, dt))
            s -= min(s, dt)
            self.frame()


def state_dump(h):
    s = h.debug_snapshot()['instances'][INST]['state']
    return json.dumps(s, sort_keys=True, default=float)


class KeyProcedures(unittest.TestCase):
    def test_run_a_manual_experiment_in_one_zone(self):
        g = Rig()
        self.assertEqual((g.frame()['screen'], g.page(), g.text('sb_status')), ('gui', 'ov_app', 'READY'))
        self.assertFalse(g.tap('app1_tb'))                                              # the Overview accepts no input (OM p.83)
        g.tap('sb_zone1')
        self.assertEqual(g.page(), 'z1_graph')
        g.tap('z1_new')                                                                 # New experiment: keyboard with a default name (OM p.107)
        self.assertEqual(g.overlay()['title'], 'Save experiment as')
        g.tap_ov('row:name')
        g.type_text('Hydrolysis 1')
        g.tap_ov('Save')
        self.assertEqual((g.exp(1)['name'], g.text('z1_title')), ('Hydrolysis 1', 'Hydrolysis 1*'))
        g.tap('z1_mode_man')
        g.tap('z1_ctl_tb')
        g.keypad('z1_sp', 60)
        g.keypad('z1_rpm', 300)
        self.assertEqual(g.exp(1)['manual'], {'setpoint_C': 60, 'control': 'tb', 'speed_rpm': 300})
        g.tap('z1_temp_on')
        g.tap('z1_stir_on')
        self.assertEqual((g.exp(1)['status'], g.text('sb_status'), g.widgets()['sb_zone1']['fill']), ('running', 'CONTROL', 'green'))
        self.assertFalse(g.widgets()['z1_mode_prof']['enabled'])                       # Run Mode locked once started (OM p.89)
        g.step(600.0, dt=30.0)
        self.assertAlmostEqual(g.state('zone_1.thermal.indicated_temperature_k'), 60 + C0, delta=0.3)
        self.assertEqual(g.text('z1_tb'), f"{g.state('zone_1.thermal.indicated_temperature_k') - C0:.1f} °C")
        self.assertAlmostEqual(g.state('zone_1.stirring.actual_rad_s'), 300 * 2 * 3.141592653589793 / 60, places=9)
        data = g.exp(1)['data']
        self.assertEqual((data[0][0], data[1][0], len(data)), (0.0, 10.0, 61))       # stored every 10 s (OM p.126)
        g.keypad('z1_sp', 45)                                                           # changed while running (OM p.91)
        self.assertEqual(g.state('zone_1.thermal.target_k'), 45 + C0)
        g.tap('z1_temp_on')
        g.tap('z1_stir_on')                                                             # both off: the experiment ends (OM p.91-92)
        self.assertEqual((g.exp(1)['status'], g.exp(1)['result'], g.state('zone_1.thermal.enabled')), ('finished', 'completed', False))
        self.assertFalse(g.widgets()['z1_temp_on']['enabled'])
        DONE_TASKS.add('run a manual experiment in one zone')

    def test_create_and_run_a_temperature_profile(self):
        g = Rig()
        g.tap('sb_zone2')
        g.tap('z2_mode_prof')
        g.tap('sb_zone2')
        self.assertEqual(g.page(), 'z2_profile')
        self.assertEqual(g.widgets()['z2_steps']['rows'], ['30  0  20  20  Tb  Value'])   # the default step (OM p.94 scr)
        g.keypad('z2_e_dur', 10)
        g.keypad('z2_e_rpm', 250)
        g.keypad('z2_e_to', 60)
        g.tap('z2_e_end')
        g.tap('z2_e_add')                                                               # + duplicates the step (OM p.94)
        g.tap('z2_steps', row=1)
        g.keypad('z2_e_from', 60)
        g.keypad('z2_e_dur', 5)
        self.assertEqual(g.widgets()['z2_steps']['rows'], ['10  250  20  60  Tb  Time', '5  250  60  60  Tb  Time'])
        g.tap('z2_save')
        self.assertTrue(g.exp(2)['saved'])
        g.tap('z2_start')
        self.assertEqual((g.exp(2)['status'], g.text('z2_start')), ('running', '||'))
        g.step(300.0, dt=10.0)                                                          # half the 10 min ramp: 40 degC (OM p.97)
        self.assertAlmostEqual(g.state('zone_2.thermal.target_k'), 40 + C0, places=6)
        self.assertEqual(g.text('z2_psp'), '40 °C')
        g.tap('z2_start')                                                               # Pause holds the temperature (OM p.98)
        g.step(120.0, dt=10.0)
        self.assertEqual((g.state('zone_2.pad.run')['step_elapsed_s'], g.state('zone_2.thermal.target_k')), (300.0, 40 + C0))
        g.tap('z2_start')
        g.step(300.0, dt=10.0)
        self.assertEqual(g.state('zone_2.pad.run')['step'], 1)
        self.assertFalse(g.widgets()['z2_e_dur']['enabled'] and g.state('zone_2.pad.run')['step'] == 0)
        g.tap('z2_skip')                                                                # Skip: the last step -> the experiment ends
        self.assertEqual((g.exp(2)['status'], g.exp(2)['result']), ('finished', 'completed'))
        DONE_TASKS.add('create and run a temperature profile')

    def test_set_zone_safety_limits(self):
        g = Rig()
        g.tap('sb_zone1')
        g.tap('z1_tab_set')
        g.tap('z1_opt_B')
        self.assertEqual(g.exp(1)['option'], 'B')
        g.keypad('z1_lim_max_tb_C', 100)                                               # Max Tr (175) above Max Tb: refused (OM p.105)
        self.assertEqual(g.overlay()['dialog'], 'd_refused')
        g.tap_ov('OK')
        g.keypad('z1_lim_max_tr_C', 95)
        g.keypad('z1_lim_max_tb_C', 100)
        g.keypad('z1_lim_max_speed_rpm', 600)
        g.keypad('z1_lim_max_dt_K', 1)
        self.assertEqual({k: g.exp(1)['limits'][k] for k in ('max_tb_C', 'max_speed_rpm', 'max_dt_K')}, {'max_tb_C': 100, 'max_speed_rpm': 600, 'max_dt_K': 1})
        g.keypad('z1_lim_min_tb_C', 120)                                                # Min Tb above Max Tb: refused (OM p.104)
        self.assertEqual(g.overlay()['dialog'], 'd_refused')
        g.tap_ov('OK')
        g.tap('z1_tab_graph')
        g.keypad('z1_sp', 120)                                                          # outside the limits: refused (OM p.104)
        self.assertEqual((g.overlay()['dialog'], g.exp(1)['manual']['setpoint_C']), ('d_refused', 20))
        g.tap_ov('OK')
        g.keypad('z1_rpm', 800)
        self.assertEqual(g.exp(1)['manual']['speed_rpm'], 0)
        g.tap_ov('OK')
        g.tap('z1_ctl_tr')
        g.keypad('z1_sp', 60)
        g.tap('z1_temp_on')                                                             # Tb - Tr limit 1 C (+2 C tolerance): T9 trips (OM p.137)
        g.step(120.0, dt=5.0)
        self.assertEqual([e['code'] for e in g.state('zone_1.pad.errors')], ['T9'])
        self.assertFalse(g.state('zone_1.thermal.enabled'))
        g.tap('z1_tab_set')
        self.assertFalse(g.widgets()['z1_lim_max_dt_K']['enabled'])                    # locked once started (OM p.102)
        DONE_TASKS.add('set zone safety limits')

    def test_export_an_experiment_to_usb(self):
        g = Rig()
        g.call(g.A, 'pad.save_experiment', zone_index=0)
        g.tap('sb_files')
        g.tap('fm_lib_name', row=0)
        g.tap('fm_tab_usb')                                                             # no stick: the warning (OM p.116)
        self.assertEqual(g.overlay()['lines'], ['No USB storage device found.'])
        g.tap_ov('Ok')
        g.tap('fm_export')
        self.assertEqual(g.overlay()['dialog'], 'd_usb')
        g.tap_ov('Ok')
        self.assertEqual(g.call(g.A, 'pad.file_export', expect='rejected', name='Experiment 1', target='usb')['error']['code'], 'PRECONDITION_FAILED')
        g.tap('fm_tab_share')                                                           # the Share area inside the pad works (OM p.115)
        g.tap('fm_export')
        self.assertEqual(g.widgets()['fm_share']['rows'], ['Experiment 1.expc', 'Experiment 1.csv'])
        DONE_TASKS.add('export an experiment to USB')

    def test_copy_a_profile_to_another_zone(self):
        g = Rig()
        g.call(g.A, 'pad.set_mode', zone_index=0, mode='profile')
        g.call(g.A, 'pad.set_step', zone_index=0, index=0, duration_min=20, speed_rpm=400, from_C=25, to_C=80, control='tb', end='value')
        g.call(g.A, 'pad.save_experiment_as', zone_index=0, name='Master')
        g.tap('sb_zone3')
        g.tap('z3_open')                                                                # Open: select the master, a new name (OM p.108)
        g.tap_ov('row:source')
        g.tap_ov('Master')
        g.tap_ov('row:name')
        g.type_text('Copy 3')
        g.tap_ov('Open')
        e = g.exp(3)
        self.assertEqual((e['name'], e['mode'], e['steps']), ('Copy 3', 'profile', g.exp(1)['steps']))
        g.tap('sb_zone3')
        self.assertEqual(g.page(), 'z3_profile')
        DONE_TASKS.add('copy a profile to another zone')

    def test_read_and_handle_an_error(self):
        g = Rig(cooling=False)
        g.tap('sb_zone4')
        g.keypad('z4_sp', 50)
        g.tap('z4_temp_on')                                                             # no coolant: T1, heating off (OM p.16, p.160)
        self.assertEqual((g.widgets()['sb_zone4']['fill'], g.exp(4)['status']), ('yellow', 'running'))
        g.tap('z4_warn')
        ov = g.overlay()
        self.assertEqual((ov['title'], ov['lines']), ('Error - Zone 4', ['T1: Coolant flow rate too low.', 'Heating/cooling disabled', '',
                                                                        'Check coolant is connected and restart experiment']))
        g.tap_ov('OK')
        g.h.configure_cooling_source(available=True, temperature_K=278.15, flow_m3_s=0.003 / 60)
        g.tap('z4_temp_on')                                                             # restart temperature control after clearing (OM p.155)
        self.assertEqual((g.state('zone_4.pad.errors'), g.state('zone_4.thermal.enabled')), ([], True))
        g.call(g.E, 'pad.inject_error', code='Z11', zone_index=3)                      # a Peltier fault: yellow, cannot restart
        self.assertFalse(g.tap('z4_temp_on'))
        self.assertEqual(g.overlay()['dialog'], 'd_refused')
        g.tap_ov('OK')
        self.assertFalse(g.state('zone_4.thermal.enabled'))
        self.assertEqual(g.call(g.A, 'pad.set_temperature_control', expect='rejected', zone_index=3, on=True)['error']['code'], 'PRECONDITION_FAILED')
        g.call(g.E, 'pad.inject_error', code='M7')                                     # Red System: all zones end (OM p.158)
        self.assertEqual((g.text('sb_status'), g.exp(4)['status'], g.exp(4)['result']), ('ERROR', 'finished', 'error M7'))
        g.tap('z4_warn')
        self.assertEqual(g.overlay()['title'], 'System Error')
        g.tap_ov('OK')
        g.switch('off')
        g.switch('on')                                                                  # a mains cycle clears the red errors (OM p.157)
        self.assertEqual([e['code'] for e in g.state('zone_4.pad.errors')], ['Z11'])
        self.assertEqual(len(g.state('pad.error_log')), 3)
        DONE_TASKS.add('read and handle an error')

    def test_shut_down(self):
        g = Rig()
        g.tap('sb_zone1')
        g.tap('z1_temp_on')
        self.assertTrue(g.switch('off'))                                                # the station off switch (OM p.132)
        self.assertEqual((g.frame()['screen'], g.text('lost_1')), ('comms', 'Contact has been lost with the reaction station.'))
        self.assertEqual(g.text('lost_2'), 'The control panel will shutdown in 15 seconds.')
        self.assertEqual((g.exp(1)['status'], g.exp(1)['result']), ('finished', 'contact lost'))
        g.step(5.0, dt=1.0)
        self.assertEqual(g.text('lost_2'), 'The control panel will shutdown in 10 seconds.')
        g.step(10.0, dt=1.0)
        self.assertEqual((g.frame()['screen'], g.pad()['brightness']), ('pad_off', 0))
        self.assertFalse(g.tap_xy(400, 240))
        g.switch('on')                                                                  # restart: the pad boots with the station (OM p.81)
        self.assertEqual((g.frame()['screen'], g.page()), ('gui', 'ov_app'))
        DONE_TASKS.add('shut down')


class OtherFunctions(unittest.TestCase):
    def test_system_settings_demo_and_clock(self):
        g = Rig()
        g.tap('sb_settings')
        g.keypad('sys_int', 11)                                                         # odd values round up to the next even one (OM p.126)
        self.assertEqual(g.state('pad.settings')['storage_interval_s'], 12)
        for key, value in zip(('year', 'month', 'day', 'hour', 'minute', 'second'), (2026, 11, 16, 11, 15, 48)):
            g.keypad(f'sys_clk_{key}', value)
        self.assertTrue(g.state('pad.clock').startswith('2026-11-16T11:15:48'))
        g.tap('sys_demo_on')
        self.assertEqual(g.text('sb_status'), 'DEMO')
        g.tap('sb_zone1')
        g.keypad('z1_sp', 90)
        g.tap('z1_temp_on')                                                             # Demo: the pad simulates, the station is not driven (OM p.127)
        g.step(20.0)
        self.assertEqual((g.text('z1_tb'), g.state('zone_1.thermal.enabled')), ('90.0 °C', False))
        g.tap('sb_settings')
        self.assertFalse(g.widgets()['sys_demo_off']['enabled'])                        # locked while running (OM p.124)
        g.tap('sb_zone1')
        g.tap('z1_temp_on')
        g.tap('sb_settings')
        g.tap('sys_demo_off')
        self.assertEqual(g.text('sb_status'), 'READY')

    def test_service_pid_and_network_behind_the_password(self):
        g = Rig()
        g.call(g.A, 'pad.set_clock', month=11, day=16)
        g.tap('sb_settings')
        g.tap('sys_tab_srv')                                                            # password = day x month = 176 (OM p.124)
        g.tap_ov('row:pw')
        for ch in '175':
            g.tap_ov('key:' + ch)
        g.tap_ov('key:{ok}')
        g.tap_ov('Apply')
        self.assertEqual((g.overlay()['dialog'], g.page()), ('d_pw_wrong', 'sys'))
        g.tap_ov('OK')
        g.tap('sys_tab_srv')
        g.tap_ov('row:pw')
        for ch in '176':
            g.tap_ov('key:' + ch)
        g.tap_ov('key:{ok}')
        g.tap_ov('Apply')
        self.assertEqual(g.page(), 'sys_service')
        self.assertEqual(g.text('srv_sw'), 'Controller Software Version  1.0.1.1')
        g.tap('srv_export_log')
        self.assertIn('Error Log.csv', [f['name'] for f in g.state('pad.share')])
        g.tap('sys_tab_pid')                                                            # the password stays valid in this visit
        self.assertEqual(g.page(), 'sys_pid')
        g.keypad('pid_A_block_P', 30)
        self.assertEqual(g.state('pad.pid')['A']['block'], [30.0, 0.9, 10.0])
        g.tap('pid_restore')
        self.assertEqual(g.state('pad.pid'), rt._PAD_PID_DEFAULT)
        g.tap('sys_tab_net')
        g.tap('net_form', row=3)
        for ch in '20':
            g.tap_ov('key:' + ch)
        g.tap_ov('key:{ok}')
        self.assertEqual(g.state('pad.network')['ip'], [192, 168, 1, 10])               # applied only with Apply (OM p.116)
        g.tap('net_apply')
        self.assertEqual(g.state('pad.network')['ip'], [192, 168, 1, 20])
        g.tap('net_reset')
        self.assertEqual(g.state('pad.network'), rt._PAD_NETWORK_DEFAULT)

    def test_file_manager_filter_import_delete(self):
        g = Rig()
        for zone, name in ((0, 'Alpha 1'), (1, 'Beta 2')):
            g.call(g.A, 'pad.save_experiment_as', zone_index=zone, name=name)
        g.call(g.A, 'pad.new_experiment', zone_index=1, name='Gamma 3')                # Beta 2 is no longer loaded
        g.tap('sb_files')
        self.assertEqual(g.widgets()['fm_lib_name']['rows'], ['Alpha 1  2026-01-01', 'Beta 2  2026-01-01'])
        g.tap('fm_filter')
        g.tap_ov('row:name')
        g.type_text('Be')
        g.tap_ov('Apply')
        self.assertEqual(g.widgets()['fm_lib_name']['rows'], ['Beta 2  2026-01-01'])
        g.tap('fm_lib_name', row=0)
        g.tap('fm_export')
        g.tap('fm_delete')                                                             # Beta 2 is not loaded: deleted (OM p.119)
        self.assertEqual([e['name'] for e in g.state('pad.library')], ['Alpha 1'])
        g.tap('fm_share', row=0)
        g.tap('fm_import')                                                             # back from Share (OM p.118)
        self.assertEqual(sorted(e['name'] for e in g.state('pad.library')), ['Alpha 1', 'Beta 2'])
        g.tap('fm_filter')
        g.tap_ov('row:name')
        g.tap_ov('key:{bksp}')
        g.tap_ov('key:{ok}')
        g.tap_ov('Apply')
        g.tap('fm_lib_name', row=0)
        self.assertFalse(g.widgets()['fm_delete']['enabled'])                          # Alpha 1 is loaded in zone 1


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        a, b = Rig(), Rig()
        a.tap('sb_zone1')
        a.keypad('z1_sp', 55)
        a.keypad('z1_rpm', 200)
        a.tap('z1_temp_on')
        b.call(b.A, 'pad.set_manual', zone_index=0, setpoint_C=55)
        b.call(b.A, 'pad.set_manual', zone_index=0, speed_rpm=200)
        b.call(b.A, 'pad.set_temperature_control', zone_index=0, on=True)
        self.assertEqual(state_dump(a.h), state_dump(b.h))
        for g in (a, b):
            g.step(60.0)
        self.assertEqual(state_dump(a.h), state_dump(b.h))


class DeviceFunctions(unittest.TestCase):
    def test_value_end_condition_waits_for_the_temperature(self):
        g = Rig()
        g.call(g.A, 'pad.set_mode', zone_index=0, mode='profile')
        g.call(g.A, 'pad.set_step', zone_index=0, index=0, duration_min=1, speed_rpm=0, from_C=90, to_C=90, control='tb', end='value')
        g.call(g.A, 'pad.profile_start', zone_index=0)
        g.h.step(60.0)
        self.assertEqual(g.exp(1)['status'], 'running')                                # 90 degC not reached after 1 min (OM p.97)
        t = 60.0
        while g.exp(1)['status'] == 'running' and t < 3600:
            g.h.step(10.0)
            t += 10.0
        self.assertGreater(t, 120.0)                                                    # it ended only once 90 degC was reached
        self.assertEqual(g.exp(1)['result'], 'completed')
        self.assertGreaterEqual(max(p[1] for p in g.exp(1)['data']), 88.5)                # logged every 10 s; the end came within 0.5 K of To

    def test_seven_day_limit(self):
        g = Rig()
        g.call(g.A, 'pad.set_stirrer_control', zone_index=0, on=True)
        inst = g.h._instances[INST]
        inst['state']['zone_1.pad.run']['elapsed_s'] = 6 * 86400 - 5.0               # host fast-forward of the experiment clock (white box)
        inst['state']['zone_1.pad.run']['next_log_s'] = 6 * 86400 + 1000.0
        g.h.step(10.0)
        self.assertEqual([e['code'] for e in g.state('zone_1.pad.errors')], ['Z18'])   # warning after 6 days (OM p.159)
        inst['state']['zone_1.pad.run']['elapsed_s'] = 7 * 86400 - 5.0
        g.h.step(10.0)
        self.assertEqual((g.exp(1)['status'], g.exp(1)['result']), ('finished', 'error Z17'))

    def test_set_limits_contract_example_from_factory_limits(self):
        # 1.2.2: the example only lowered Max Tb to 120 C, below the factory Max Tr of 175 C (OM p.105: Max Tr cannot exceed Max Tb) - always refused
        g = Rig()
        example = json.loads((ASSET / 'asset.contract.json').read_text())['capabilities']['pad.set_limits']['example_arguments']
        before = state_dump(g.h)
        self.assertEqual(g.call(g.A, 'pad.set_limits', expect='rejected', zone_index=0, max_tb_C=120)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(state_dump(g.h), before)
        g.call(g.A, 'pad.set_limits', **example)
        self.assertEqual((g.exp(1)['limits']['max_tb_C'], g.exp(1)['limits']['max_tr_C']), (120, 115))

    def test_pad_safety_limits_bound_the_direct_setpoints(self):
        # chemlab finding (2026-10-22): thermal.set_temperature ignored the zone's safety limits (OM p.101: once safety limits are
        # set for a zone, values outside them cannot be entered)
        g = Rig()
        g.call(g.A, 'thermal.set_temperature', zone_index=0, temperature_K=273.15 + 150)       # factory limits: the full range
        g.call(g.A, 'pad.set_limits', zone_index=0, max_tb_C=120, max_tr_C=115)
        before = state_dump(g.h)
        r = g.call(g.A, 'thermal.set_temperature', expect='rejected', zone_index=0, temperature_K=273.15 + 121)
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')
        self.assertIn('safety limits', r['error']['message'])
        r = g.call(g.A, 'thermal.configure_program', expect='rejected', zone_index=0, segments=[{'temperature_K': 273.15 + 60, 'hold_s': 60}, {'temperature_K': 273.15 + 130, 'hold_s': 60}])
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')
        r = g.call(g.A, 'thermal.set_enabled', expect='rejected', zone_index=0, enabled=True)       # the 150 C target set before the limits
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(state_dump(g.h), before)
        g.call(g.A, 'thermal.set_temperature', zone_index=0, temperature_K=273.15 + 118)
        r = g.call(g.A, 'thermal.set_probe_mode', expect='rejected', zone_index=0, mode='liquid')    # Max Tr 115 C
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')
        g.call(g.A, 'thermal.set_temperature', zone_index=0, temperature_K=273.15 + 110)
        g.call(g.A, 'thermal.set_probe_mode', zone_index=0, mode='liquid')
        g.call(g.A, 'thermal.set_enabled', zone_index=0, enabled=True)
        g.call(g.A, 'thermal.set_temperature', zone_index=1, temperature_K=273.15 + 150)       # another zone keeps its own (factory) limits

    def test_refusals_leave_state_unchanged(self):
        g = Rig()
        before = state_dump(g.h)
        for cap, args, code in [('pad.new_experiment', {'zone_index': 0, 'name': 'Experiment 2'}, 'INVALID_ARGUMENT'),
                                ('pad.new_experiment', {'zone_index': 0, 'name': 'bad/name'}, 'INVALID_ARGUMENT'),
                                ('pad.set_step', {'zone_index': 0, 'index': 0, 'duration_min': 10, 'speed_rpm': 0, 'from_C': 20, 'to_C': 30, 'control': 'tb', 'end': 'time'}, 'PRECONDITION_FAILED'),
                                ('pad.profile_start', {'zone_index': 0}, 'PRECONDITION_FAILED'), ('pad.open_experiment', {'zone_index': 0, 'source': 'none', 'name': 'x1'}, 'PRECONDITION_FAILED'),
                                ('pad.set_limits', {'zone_index': 0, 'min_tr_C': -28}, 'INVALID_ARGUMENT'), ('pad.set_limits', {'zone_index': 0}, 'INVALID_ARGUMENT'),
                                ('pad.set_manual', {'zone_index': 0, 'speed_rpm': 50}, 'INVALID_ARGUMENT'), ('pad.file_import', {'file': 'x.expc'}, 'PRECONDITION_FAILED'),
                                ('pad.inject_error', {'code': 'Z11', 'zone_index': 0}, 'ACCESS_DENIED')]:
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], code, cap)
            self.assertEqual(state_dump(g.h), before, cap)
        g.call(g.A, 'pad.set_temperature_control', zone_index=0, on=True)
        for cap, args in [('thermal.set_temperature', {'zone_index': 0, 'temperature_K': 330.0}), ('stirring.set_enabled', {'zone_index': 0, 'enabled': True})]:
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'BUSY', cap)
        for cap, args in [('pad.set_mode', {'zone_index': 0, 'mode': 'profile'}), ('pad.configure', {'storage_interval_s': 20}), ('pad.set_option', {'zone_index': 0, 'option': 'C'})]:
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'PRECONDITION_FAILED', cap)


class PidControl(unittest.TestCase):
    """The PID gains of the option drive the Peltier power (2026-10-19): block PID for Tb control, reaction PID + block PID (cascade) for Tr."""

    @staticmethod
    def run_zone(option='A', control='tb', sp=90, seconds=1200, block_p=None, limits=None):
        g = Rig()
        if block_p is not None:
            g.call(g.A, 'pad.set_pid', option=option, loop='block', term='P', value=float(block_p))
        g.call(g.A, 'pad.set_option', zone_index=0, option=option)
        if limits:
            g.call(g.A, 'pad.set_limits', zone_index=0, **limits)
        g.call(g.A, 'pad.set_manual', zone_index=0, setpoint_C=sp, control=control)
        g.call(g.A, 'pad.set_temperature_control', zone_index=0, on=True)
        out = []
        for k in range(int(seconds / 10)):
            g.h.step(10.0)
            s = g.h._instances[INST]['state']
            out.append((10.0 * (k + 1), s['zone_1.thermal.temperature_k'] - C0, s['zone_1.thermal.liquid_temperature_k'] - C0))
        return g, out

    def test_factory_option_a_tb_and_weaker_block_gain(self):
        g, out = self.run_zone()
        self.assertLessEqual(max(t for _, t, _ in out), 90.5)                           # steady control about +-0.5 C (OM p.139)
        self.assertAlmostEqual(out[59][1], 90.0, delta=0.1)
        self.assertAlmostEqual(out[-1][1], 90.0, delta=0.01)
        self.assertGreater(abs(g.state('zone_1.thermal.pid')['u']), 0.0)
        _, weak = self.run_zone(block_p=8)                                              # PID Gains: block P 32 -> 8, the same I: overshoot
        self.assertLess(weak[17][1], out[17][1])
        self.assertGreater(max(t for _, t, _ in weak), 95.0)

    def test_tr_cascade_options_and_the_175_c_range(self):
        _, a = self.run_zone(control='tr')
        _, d = self.run_zone(option='D', control='tr')                                  # option D: gentle reaction gains (OM p.103, p.140)
        self.assertAlmostEqual(a[-1][2], 90.0, delta=0.1)
        self.assertLess(d[-1][2], 89.0)
        self.assertLessEqual(max(r for _, _, r in a), 90.5)
        g, hot = self.run_zone(control='tr', sp=170, seconds=3600, limits={'max_tr_C': 175})   # Tr up to 175 C (OM p.105, p.161)
        self.assertEqual(g.exp(1)['manual']['setpoint_C'], 170)
        self.assertAlmostEqual(hot[-1][2], 170.0, delta=0.05)
        self.assertEqual(g.state('zone_1.pad.errors'), [])
        self.assertLessEqual(g.state('zone_1.thermal.pid')['sp_block_k'] - C0, 180.0)


class TimeOuts(unittest.TestCase):
    def test_double_tap_zoom_and_zoom_out(self):
        g = Rig()
        g.tap('sb_zone1')
        g.tap('z1_temp_on')
        g.step(400.0, dt=20.0)
        span = g.widgets()['z1_plot']['x_range']
        g.tap('z1_plot')
        g.step(0.5, dt=0.5)
        g.tap('z1_plot')                                                                # two taps 0.5 s apart: no zoom
        self.assertEqual(g.widgets()['z1_plot']['x_range'], span)
        g.tap('z1_plot')                                                                # a double tap halves the window (OM p.101)
        x0, x1 = g.widgets()['z1_plot']['x_range']
        self.assertAlmostEqual(x1 - x0, (span[1] - span[0]) / 2, places=6)
        g.tap('z1_zoomout')
        self.assertEqual(g.widgets()['z1_plot']['x_range'], span)

    def test_expanded_graph_returns_to_full_view_after_eight_minutes(self):
        """2026-10-19 sync fix: OM p.101, the expanded view returns to the full screen view after approximately eight minutes (480 s)."""
        g = Rig()
        g.tap('sb_zone1')
        g.tap('z1_temp_on')
        g.step(400.0, dt=20.0)
        g.tap('z1_plot')
        g.tap('z1_plot')
        g.tap('z1_plot')
        g.tap('z1_plot')                                                                # zoomed twice: a quarter of the window
        g.step(470.0, dt=10.0)
        self.assertEqual(g.h.panel(INST).frame()['ui']['vars']['zoom1'], 0.25)
        x0, x1 = g.widgets()['z1_plot']['x_range']                                     # still expanded after 470 s (the graph keeps updating)
        self.assertTrue(g.widgets()['z1_zoomout']['enabled'])
        g.step(10.0, dt=10.0)
        y0, y1 = g.widgets()['z1_plot']['x_range']
        self.assertAlmostEqual(y1 - y0, 4 * (x1 - x0), delta=20.0)                     # the full window again (10 s more data)
        self.assertFalse(g.widgets()['z1_zoomout']['enabled'])                         # back to the full view: Zoom Out greyed
        g.tap('z1_plot')
        g.tap('z1_plot')                                                                # a new double tap halves the full window again
        z0, z1 = g.widgets()['z1_plot']['x_range']
        self.assertAlmostEqual(z1 - z0, (y1 - y0) / 2, places=6)


    def test_graph_overview_shows_the_zone_zoom(self):
        """2026-10-19: OM p.101, a zoomed zone graph is shown with the same expanded view on the Graph Overview (and resets with it)."""
        g = Rig()
        g.tap('sb_zone1')
        g.tap('z1_temp_on')
        g.step(400.0, dt=20.0)
        g.tap('z1_plot')
        g.tap('z1_plot')                                                                # zone 1 zoomed: half the window
        x0, x1 = g.widgets()['z1_plot']['x_range']
        g.tap('sb_overview')
        g.tap('ov_tab_graph')
        self.assertEqual(g.page(), 'ov_graph')
        o0, o1 = g.widgets()['ovg1_plot']['x_range']
        self.assertAlmostEqual(o1 - o0, x1 - x0, places=6)
        g.step(480.0, dt=20.0)                                                          # the expanded view ends: full view here too
        o0, o1 = g.widgets()['ovg1_plot']['x_range']
        g.tap('sb_zone1')
        z0, z1 = g.widgets()['z1_plot']['x_range']
        self.assertAlmostEqual(o1 - o0, z1 - z0, places=6)
        self.assertGreater(o1 - o0, 3 * (x1 - x0))


class PidLogging(unittest.TestCase):
    def test_pid_data_logging_records_and_exports_the_pid_file(self):
        """2026-10-19 sync fix: System Settings 'PID data logging' (OM p.130, p.153) records the PID settings and controller terms of a run."""
        for on in (False, True):
            g = Rig()
            g.call(g.A, 'pad.configure', pid_logging=on)
            g.call(g.A, 'pad.set_manual', zone_index=0, setpoint_C=60)
            g.call(g.A, 'pad.set_temperature_control', zone_index=0, on=True)
            g.step(600.0, dt=20.0)
            exp = g.exp(1)
            if not on:
                self.assertNotIn('pid_data', exp)
            else:
                rows = exp['pid_data']
                self.assertEqual(len(rows), len(exp['data']))                           # one PID row per data point
                self.assertEqual([r[0] for r in rows], [d[0] for d in exp['data']])
                self.assertEqual(len(rows[-1]), len(rt._PAD_PID_LOG_COLUMNS))
                self.assertEqual(rows[-1][1:8], ['A', 32.0, 0.9, 10.0, 2.9, 0.01, 10.0])   # option A gains (factory)
                self.assertGreater(max(r[11] for r in rows), 0.0)                      # Peltier output while heating
            g.call(g.A, 'pad.set_temperature_control', zone_index=0, on=False)
            name = g.exp(1)['name']
            g.call(g.A, 'pad.save_experiment', zone_index=0)
            files = g.call(g.A, 'pad.file_export', name=name, target='share')['files']
            self.assertEqual(name + '_PID.csv' in files, on)
            self.assertEqual(name + '_PID.csv' in [f['name'] for f in g.state('pad.share')], on)

    def test_another_callers_run_cannot_be_stopped_or_switched_off(self):
        """Lead rule 2026-10-19: B cannot stop or change A's zone experiment (panel or direct); A stops its own; the evaluator is unrestricted.
        The station switch is a hard switch: never refused, the runs end as on a power loss (OM p.132; lead clarification 2026-10-19)."""
        g = Rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        g.tap('sb_zone1')
        g.tap('z1_temp_on')                                                            # A starts zone 1 on the panel
        self.assertEqual(g.exp(1)['status'], 'running')
        before = state_dump(g.h)
        for cap, args in (('pad.set_temperature_control', {'zone_index': 0, 'on': False}), ('pad.set_manual', {'zone_index': 0, 'setpoint_C': 40}),
                          ('pad.profile_stop', {'zone_index': 0})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        x, y, w, h = g.widgets()['z1_temp_on']['rect']
        self.assertFalse(g.call(B, 'panel.touch', display_id='pad', x=int(x + w / 2), y=int(y + h / 2))['accepted'])   # B's toggle: refused
        ov = g.frame()['ui']['overlay']
        self.assertEqual(ov and ov.get('dialog'), 'd_denied')                           # shown as a message box
        self.assertEqual(state_dump(g.h), before)
        g.call(B, 'pad.set_manual', zone_index=1, setpoint_C=40)                       # other zones stay free
        while g.frame()['ui']['overlay']:                                               # A closes the message boxes (one per refused gesture)
            g.tap_ov('OK')
        g.tap('z1_temp_on')                                                            # A stops its own (both controls off: the end)
        self.assertEqual(g.exp(1)['status'], 'finished')
        g.call(B, 'pad.set_temperature_control', zone_index=1, on=True)
        g.call(g.E, 'pad.set_temperature_control', zone_index=1, on=False)            # the evaluator is unrestricted
        self.assertEqual(g.exp(2)['status'], 'finished')
        g.call(g.A, 'pad.set_temperature_control', zone_index=2, on=True)              # A runs zone 3
        self.assertEqual(g.exp(3)['status'], 'running')
        self.assertTrue(g.call(B, 'panel.switch', input_id='station_switch', position='off')['accepted'])   # physical: never refused
        self.assertFalse(g.state('power.enabled'))
        self.assertNotEqual(g.exp(3)['status'], 'running')                             # the run ends (contact lost)

    def test_panel_gesture_acts_as_the_calling_session(self):
        """2026-10-19 sync fix: the commands behind a gesture run as the session that made it, not as a fixed panel principal."""
        from unittest import mock
        g = Rig()
        g.tap('sb_zone1')
        callers, real = [], rt.RadleysMya4Session._checked

        def spy(sess, *a, **k):
            callers.append((sess, a[2]))
            return real(sess, *a, **k)
        with mock.patch.object(rt.RadleysMya4Session, '_checked', autospec=True, side_effect=spy):
            g.tap('z1_temp_on')
        self.assertEqual(g.exp(1)['status'], 'running')
        self.assertTrue(any(not c.startswith('panel.') for _, c in callers))
        self.assertTrue(all(s is g.A for s, c in callers if c != 'panel.frame'))
        self.assertIsNone(g.h._panel_caller)


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        self.assertEqual(a.h.panel(INST).png_bytes('pad'), b.h.panel(INST).png_bytes('pad'))
        OUT.mkdir(parents=True, exist_ok=True)
        a.tap('sb_zone1')
        a.keypad('z1_sp', 60)
        a.keypad('z1_rpm', 300)
        a.tap('z1_temp_on')
        a.tap('z1_stir_on')
        a.step(300.0, dt=30.0)
        shots = {'zone': a.h.panel(INST).save_png('pad', OUT / 'zone.png')}
        a.tap('sb_overview')
        shots['home'] = a.h.panel(INST).save_png('pad', OUT / 'home.png')
        a.tap('sb_settings')
        shots['menu'] = a.h.panel(INST).save_png('pad', OUT / 'menu.png')
        a.call(a.A, 'pad.set_mode', zone_index=1, mode='profile')
        a.call(a.A, 'pad.add_step', zone_index=1, index=0)
        a.tap('sb_zone2')
        shots['profile'] = a.h.panel(INST).save_png('pad', OUT / 'profile.png')
        a.call(a.E, 'pad.inject_error', code='T2', zone_index=0)
        a.tap('sb_zone1')
        a.tap('z1_warn')
        shots['error'] = a.h.panel(INST).save_png('pad', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='pad')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.switch('on')
        self.assertNotEqual(g.call(g.A, 'panel.capture', display_id='pad')['image']['sha256'], r['image']['sha256'])

    def test_spec_and_contract(self):
        meta = rt.export_radleys_mya4_metadata()['asset_types'][rt.MYA4_TYPE]
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities']) | set(meta['controller_capabilities'])), [])
        self.assertEqual(set(meta['panel_capabilities']), {'panel.touch', 'panel.switch', 'panel.capture', 'panel.frame'})
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['controller_capabilities'], **meta['evaluator_capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        self.assertIn('station_switch', c['components'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class']), ('panel_ui', 'touchscreen_gui'))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('mya4_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
