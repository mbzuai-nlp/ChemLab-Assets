"""front-panel tests for CHEM-010 eppendorf_5910ri (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_analytical.py):
python3 -m pytest tests/test_panel_ui_eppendorf_5910ri.py -q

The Centrifuge 5910 Ri has a capacitive touchscreen (display 'touch') and the mains switch.  Every gesture goes through the public API
(panel.switch / panel.touch); widgets are found by id in the structured frame (panel.frame, evaluator).  Themes: the six key procedures of
the menu survey; changes during a run, Continuous and count from speed, favorites, programs, events and run records, settings, user
management with rights and automatic logout, recurring tasks, clean lock, standby and the 90 s start; panel = direct command; refusals;
render determinism and evidence PNGs; every terminal command once; contract entries."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
import basic40_runtime_analytical as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

OUT = ROOT / 'outputs/panel_ui/eppendorf_5910ri'
CONTRACT = rt.EPP5910_ASSET_DIR / 'asset.contract.json'
TOUCH = 'touch'
RPM = rt._EPP_RPM
SURVEY_TASKS = ['set 10 min, 4 C, 4000 x g and run', 'set gentle ramps', 'stop early and open lid', 'short spin', 'pre-cool with Fast Temp',
                'save current parameters as a program']
DONE_TASKS = set()
TMP = tempfile.mkdtemp(prefix='epp5910_panel_')


class Rig:
    """A powered 5910 Ri (software started) with two synthetic rotors (large: slots at 0.2 m, 4500 rpm; fixed angle: 0.1 m, 14000 rpm), the
    large rotor selected and two equal samples loaded opposite each other, lid closed.  Curve samples every 60 s (Continuous runs need >= 36 s
    with the 10000-sample curve cap)."""

    def __init__(self, on=True, loaded=True):
        self.h = h = rt.Eppendorf5910Ri('cf', scene_adapter=lambda i, s: None)
        h.panel_capture_dir = TMP
        for conf, r, lim in (('large_capacity', 0.2, 4500), ('fixed_angle', 0.1, 14000)):
            h.configure_synthetic_rotor(conf, slot_positions_xy_m={'slot_1': [r, 0], 'slot_2': [-r, 0], 'slot_3': [0, r], 'slot_4': [0, -r]},
                                        operating_speed_limit_rad_s=lim * RPM, imbalance_threshold_kg_m=1e-4, evidence='synthetic')
        h.configure_synthetic_process(acceleration_rad_s2=[2.0 + i * 3 for i in range(10)], braking_rad_s2=[1.0 + i * 3 for i in range(10)],
                                      poweroff_deceleration_rad_s2=2.0, temperature_time_constant_s=300.0, ambient_temperature_k=295.15,
                                      initial_temperature_k=295.15, sample_interval_s=60.0, evidence='synthetic')
        for s in ('a', 'b', 'c'):
            h.register_sample(s, mass_kg=0.05)
        h.register_principal('judge', visibility='evaluator')
        self.A, self.E = h.session('__agent__'), h.session('judge')
        self.n = 0
        if on:
            assert self.switch('on')
            self.step(91)
            if loaded:
                self.call(self.A, 'lid.set_angle', angle_rad=1.5)
                self.call(self.A, 'rotor.select', configuration='large_capacity')
                self.call(self.A, 'sample.load', slot_id='slot_1', sample_id='a')
                self.call(self.A, 'sample.load', slot_id='slot_2', sample_id='b')
                self.call(self.A, 'lid.set_angle', angle_rad=0.0)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r.get('error'))
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(state_ids=[sid])['observations'][sid]['value']

    def sp(self):
        return self.val('centrifuge.set_points')

    def step(self, dt):
        self.h.host_advance(self.h._now + dt)

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][TOUCH]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='mains_switch', position=pos)['accepted']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TOUCH, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid, **kw):
        x, y, ww, hh = self.widgets()[wid]['rect']
        return self.touch(x + ww // 2, y + hh // 2, **kw)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        for b in self.overlay()['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in self.overlay()["buttons"]]}')

    def type(self, text):
        if self.overlay()['field']:
            self.ov('Del')
        for ch in text:
            assert self.ov('Space' if ch == ' ' else ch), ch

    def fill(self, label, text):
        """A form row of the open dialog: tap it, type, OK."""
        assert self.ov(label)
        self.type(text)
        return self.ov('OK')

    def pick(self, label, option):
        assert self.ov(label)
        return self.ov(option)

    def row(self, wid, k):
        w = self.widgets()[wid]
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def cells(self, wid):
        return self.widgets()[wid]['cells']

    def png(self):
        return Path(self.call(self.A, 'panel.capture', display_id=TOUCH)['image']['path']).read_bytes()

    def set_time(self, minutes):
        self.tap('h_time')
        self.fill('Hours', str(minutes // 60))
        self.fill('Minutes', str(minutes % 60))
        self.fill('Seconds', '0')
        self.ov('Confirm')

    def run_until_idle(self, limit=4000):
        for _ in range(limit):
            if self.val('run.status') not in ('running', 'stopping'):
                return
            self.step(5)
        raise AssertionError('run did not end')


class SurveyProcedures(unittest.TestCase):
    def test_run_10min_4c_4000g(self):
        r = Rig()
        r.set_time(10)
        self.assertEqual(r.text('h_time'), '10 min')
        r.tap('h_temp')
        r.fill('Temperature', '4')
        r.ov('Confirm')
        r.tap('h_speed')
        r.pick('Mode', 'rcf')
        r.fill('g-force', '4000')
        r.ov('Confirm')
        sp = r.sp()
        self.assertEqual((sp['duration_s'], sp['temperature_k'], sp['speed_mode']), (600.0, 277.15, 'rcf'))
        self.assertAlmostEqual(sp['speed_rad_s'] ** 2 * 0.2 / 9.80665, 4000.0, places=6)
        self.assertAlmostEqual(r.val('method.speed_rad_s'), sp['speed_rad_s'])
        self.assertEqual(r.text('h_speed'), '4000 x g')
        self.assertEqual(r.text('h_radius'), 'Radius 20.0 cm')
        self.assertTrue(r.tap('tb_start'))
        self.assertEqual(r.val('run.status'), 'running')
        self.assertEqual(r.text('tb_start'), 'Stop')
        self.assertEqual(r.text('sb_status'), 'Centrifugation')
        self.assertEqual(r.widgets()['h_speed_cur']['color'], 'run')                    # current values in blue during the run (p.51)
        r.step(300)
        self.assertEqual(r.text('h_speed_cur'), '4000 x g')
        self.assertAlmostEqual(r.widgets()['h_progress']['fraction'], 300 / 600, places=3)
        self.assertEqual(r.text('h_time_cur'), '5 min')
        r.run_until_idle()
        self.assertEqual(r.val('run.status'), 'succeeded')
        rec = r.val('runs.records')[0]
        self.assertEqual((rec['kind'], rec['result'], rec['rotor']), ('run', 'Completed', 'S-4x Universal-Large'))
        self.assertEqual(r.val('rotor.health')['cycles']['large_capacity'], 1)
        self.assertLess(r.val('display.temperature_k'), 283.15)
        DONE_TASKS.add('set 10 min, 4 C, 4000 x g and run')

    def test_gentle_ramps(self):
        r = Rig()
        r.tap('h_ramps')
        r.pick('Acceleration', '2')
        r.pick('Brake', '1')
        r.ov('Confirm')
        self.assertEqual((r.sp()['accel'], r.sp()['brake']), (2, 1))
        self.assertEqual((r.val('method.acceleration_level'), r.val('method.braking_level')), (3, 2))
        self.assertEqual(r.text('h_ramps'), 'Ramps   / 2   \\ 1')
        r.call(r.A, 'centrifuge.set_point', settings={'speed_rad_s': 3000 * RPM})
        r.tap('tb_start')
        r.step(10)
        self.assertAlmostEqual(r.val('display.speed_rad_s'), 10 * 8.0, places=6)                 # level 2 = 8 rad/s^2 (synthetic)
        DONE_TASKS.add('set gentle ramps')

    def test_stop_early_and_open(self):
        r = Rig()
        r.tap('tb_start')
        r.step(30)
        self.assertGreater(r.val('display.speed_rad_s'), 0)
        r.tap('tb_open')                                                               # rotor turning: the lid stays locked
        self.assertEqual(r.overlay()['dialog'], 'err_lid_locked')
        r.ov('OK')
        self.assertTrue(r.tap('tb_start'))                                             # Stop
        self.assertEqual(r.val('run.status'), 'stopping')
        self.assertTrue(r.val('lid.locked'))
        r.run_until_idle()
        self.assertEqual(r.text('tb_start'), 'Start')
        r.tap('tb_open')
        self.assertFalse(r.val('lid.closed'))
        self.assertAlmostEqual(r.val('lid.angle_rad'), rt._EPP_LID_LIFT_RAD)
        self.assertEqual(r.val('runs.records')[0]['result'], 'Completed with warnings')
        self.assertIn('stopped by the user', r.val('runs.records')[0]['events'][-1])
        DONE_TASKS.add('stop early and open lid')

    def test_short_spin(self):
        r = Rig()
        r.tap('tb_short')                                                              # a short tap only explains the gesture
        self.assertEqual(r.overlay()['dialog'], 'short_spin_hint')
        r.ov('OK')
        self.assertEqual(r.val('run.status'), 'idle')
        r.tap('tb_short', duration_s=8.0)                                              # press and hold 8 s
        self.assertEqual(r.val('centrifuge.status')['special'], 'short_spin')
        r.step(4)
        self.assertAlmostEqual(r.val('display.speed_rad_s'), 4 * 29.0, places=6)          # ramp 10 (no set ramps), rising
        self.assertEqual(r.text('sb_status'), 'Short Spin')
        r.step(4)
        self.assertEqual(r.val('run.status'), 'stopping')                               # released after 8 s
        r.run_until_idle()
        self.assertEqual(r.val('runs.records')[0]['kind'], 'short_spin')
        self.assertIsNone(r.val('centrifuge.status')['special'])
        r.tap('tb_open')
        self.assertFalse(r.val('lid.closed'))
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        r.tap('tb_short', duration_s=1.0)
        self.assertEqual(r.val('run.status'), 'running')
        r.run_until_idle()
        DONE_TASKS.add('short spin')

    def test_fast_temp(self):
        r = Rig()
        r.tap('fav_temperature_0')                                                    # 4 °C favorite
        self.assertEqual(r.sp()['temperature_k'], 277.15)
        r.tap('tb_fast')
        self.assertEqual(r.text('sb_status'), 'Preparing Temperature')
        self.assertFalse(r.widgets()['h_temp']['enabled'])                              # nothing can be changed during Fast Temp (p.53)
        self.assertEqual(r.call(r.A, 'centrifuge.set_point', expect='rejected', settings={'temperature_k': 280.0})['error']['code'], 'BUSY')
        for _ in range(200):
            r.step(10)
            if r.val('device.notice'):
                break
        self.assertEqual(r.val('device.notice')['text'], 'Fast Temp finished')
        self.assertLessEqual(r.val('display.temperature_k'), 278.15 + 1e-9)
        self.assertEqual(r.text('h_temp_cur'), 'Ready')
        self.assertEqual(r.text('nt_text'), 'Fast Temp finished')
        r.run_until_idle()
        r.tap('nt_confirm')
        self.assertIsNone(r.val('device.notice'))
        self.assertNotIn('nt_confirm', r.widgets())
        self.assertEqual(r.val('runs.records')[0]['kind'], 'fast_temp')
        DONE_TASKS.add('pre-cool with Fast Temp')

    def test_save_program(self):
        r = Rig()
        r.set_time(15)
        r.tap('fav_speed_1')                                                          # 1000 rcf
        r.tap('h_prog_btn')
        self.assertEqual(r.cells('prog_list')[0], ['01  --  empty --'])
        r.row('prog_list', 0)
        self.assertEqual(r.overlay()['dialog'], 'program_name')
        r.fill('Program name', 'pellet')
        r.ov('Confirm')
        p = r.val('programs.list')[0]
        self.assertEqual((p['number'], p['name'], p['set_points']['duration_s'], p['set_points']['speed_mode']), (1, 'pellet', 900.0, 'rcf'))
        self.assertEqual(r.cells('prog_list')[0], ['01  pellet'])
        DONE_TASKS.add('save current parameters as a program')

    @classmethod
    def tearDownClass(cls):
        missing = set(SURVEY_TASKS) - DONE_TASKS
        assert not missing or len(DONE_TASKS) < len(SURVEY_TASKS), missing


class Functions(unittest.TestCase):
    def test_program_options_load_rename_lock_delete(self):
        r = Rig()
        r.call(r.A, 'centrifuge.set_point', settings={'duration_s': 120.0, 'temperature_k': 283.15})
        r.call(r.A, 'program.manage', action='save', number=2, name='WASH')
        r.set_time(5)
        r.tap('h_prog_btn')
        r.row('prog_list', 1)
        self.assertEqual(r.overlay()['dialog'], 'program_options')
        r.ov('Load')
        self.assertEqual((r.sp()['duration_s'], r.sp()['temperature_k']), (120.0, 283.15))
        self.assertAlmostEqual(r.val('method.duration_s'), 120.0)
        r.row('prog_list', 1)
        r.ov('Rename')
        r.fill('Program name', 'rinse')
        r.ov('Confirm')
        self.assertEqual(r.val('programs.list')[0]['name'], 'rinse')
        r.row('prog_list', 1)
        r.ov('Lock')
        self.assertTrue(r.val('programs.list')[0]['locked'])
        r.row('prog_list', 1)
        r.ov('Delete')
        self.assertEqual(r.overlay()['dialog'], 'err_program_locked')
        r.ov('OK')
        r.row('prog_list', 1)
        r.ov('Unlock')
        r.row('prog_list', 1)
        r.ov('Delete')
        self.assertEqual(r.val('programs.list'), [])

    def test_favorites_apply_and_store(self):
        r = Rig()
        r.tap('fav_time_3')
        self.assertEqual(r.sp()['duration_s'], 3600.0)
        r.tap('h_speed')
        r.fill('Speed', '2500')
        r.ov('Add favorite')
        self.assertEqual(r.overlay()['dialog'], 'fav_position')
        r.ov('Position 2')
        self.assertEqual(r.val('favorites.list')['speed'][1], {'mode': 'rpm', 'value': 2500})
        self.assertEqual(r.text('fav_speed_1'), '2500 rpm')
        self.assertAlmostEqual(r.sp()['speed_rad_s'], 2500 * RPM)
        r.tap('fav_speed_3')                                                           # 6000 x g exceeds the large rotor (4528 x g)
        self.assertEqual(r.overlay()['dialog'], 'err_rotor_limit')
        r.ov('OK')
        self.assertAlmostEqual(r.sp()['speed_rad_s'], 2500 * RPM)

    def test_changes_during_run(self):
        r = Rig()
        r.call(r.A, 'centrifuge.set_point', settings={'speed_rad_s': 3000 * RPM, 'duration_s': 600.0})
        r.tap('tb_start')
        r.step(120)
        self.assertAlmostEqual(r.val('display.speed_rad_s'), 3000 * RPM)
        r.tap('h_speed')
        r.fill('Speed', '2000')
        r.ov('Confirm')
        r.step(60)
        self.assertAlmostEqual(r.val('display.speed_rad_s'), 2000 * RPM)
        r.tap('h_temp')
        r.fill('Temperature', '10')
        r.ov('Confirm')
        self.assertEqual(r.val('method.temperature_k'), 283.15)
        r.set_time(20)                                                                 # the run is extended
        r.step(600)
        self.assertEqual(r.val('run.status'), 'running')
        r.step(600)
        r.run_until_idle()
        rec = r.val('runs.records')[0]
        self.assertEqual(rec['result'], 'Completed')
        self.assertEqual(sum('set point changed' in e for e in rec['events']), 3)
        self.assertGreater(r.val('measurement.curve')['time_s'][-1], 1200)

    def test_continuous_and_count_from_speed(self):
        r = Rig()
        r.tap('h_time')
        r.pick('Mode', 'Continuous')
        r.ov('Confirm')
        self.assertEqual(r.text('h_time'), 'Continuous')
        r.tap('tb_start')
        r.step(3000)
        self.assertEqual(r.val('run.status'), 'running')
        self.assertEqual(r.text('h_time_cur'), 'Continuous')
        r.tap('tb_start')
        r.run_until_idle()
        r.tap('h_time')
        r.pick('Mode', 'Timed')
        r.fill('Minutes', '1')
        r.pick('Start of time counting', 'Selected Speed Reached')
        r.ov('Confirm')
        r.call(r.A, 'centrifuge.set_point', settings={'speed_rad_s': 4000 * RPM, 'accel': 0})
        r.tap('tb_start')
        reach = 0.95 * 4000 * RPM / 2.0                                                # level 0 = 2 rad/s^2: about 199 s to 95 %
        r.step(reach + 59)
        self.assertEqual(r.val('run.status'), 'running')
        r.step(2)
        self.assertEqual(r.val('run.status'), 'stopping')

    def test_events_runs_filters_and_export(self):
        r = Rig()
        r.call(r.A, 'lid.set_angle', angle_rad=1.0)
        r.tap('tb_start')
        self.assertEqual(r.overlay()['dialog'], 'err_lid_open')
        r.ov('OK')
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        self.assertEqual(r.text('sb_events'), 'Msg 1')
        r.tap('sb_events')
        self.assertEqual(r.page(), ('gui', 'events'))
        self.assertIn('lid is not closed.  (open)', r.cells('ev_list')[0][2])
        r.tap('ev_filter')
        r.ov('Open')
        self.assertEqual(len(r.cells('ev_list')), 1)
        r.tap('ev_confirm')
        self.assertEqual(r.cells('ev_list'), [])
        self.assertNotIn('sb_events', r.widgets())
        r.tap('ev_export')
        self.assertEqual(r.overlay()['dialog'], 'err_no_usb')
        r.ov('OK')
        r.call(r.A, 'centrifuge.set_point', settings={'duration_s': 10.0})
        r.tap('tb_start')
        r.run_until_idle()
        r.tap('tb_menu')
        r.tap('mn_run')
        self.assertEqual(r.cells('rr_list')[0][1], 'Completed')
        r.row('rr_list', 0)
        self.assertIn('Result: Completed', r.overlay()['lines'])
        r.ov('Close')
        r.tap('rr_filter')
        r.ov('Failed')
        self.assertEqual(r.cells('rr_list'), [])
        r.tap('rr_export')
        self.assertEqual(r.frame()['ui']['unsupported'], 'no USB stick')

    def test_settings(self):
        r = Rig()
        r.tap('tb_menu')
        r.tap('mn_settings')
        r.tap('st_set_display')
        self.assertEqual(r.page(), ('gui', 'set_display'))
        x, y, w, h = r.widgets()['set_display_form']['rect']
        r.touch(x + 20, y + 20)                                                        # brightness row
        r.type('50')
        r.ov('OK')
        r.tap('set_display_confirm')
        self.assertEqual(r.val('device.settings')['display.brightness'], 50)
        self.assertEqual(r.frame()['displays'][TOUCH]['brightness'], 50)
        r.tap('st_set_language')
        r.touch(x + 20, y + 20)
        r.ov('Deutsch')
        r.tap('set_language_confirm')
        self.assertEqual(r.overlay()['dialog'], 'language_restart')
        self.assertTrue(r.val('device.system')['restart_needed'])
        r.ov('OK')
        r.tap('st_set_datetime')
        r.touch(x + 20, y + 20)                                                        # Automatic date and time
        r.ov('On')
        r.tap('set_datetime_confirm')
        self.assertEqual(r.overlay()['dialog'], 'err_no_network')
        r.ov('OK')
        r.call(r.A, 'clock.set', date='2026-10-19', time='09:30')
        self.assertTrue(r.val('device.datetime').startswith('2026-10-19T09:30'))
        r.tap('tb_home')
        self.assertEqual(r.text('sb_time'), '09:30')
        r.tap('tb_menu')
        r.tap('mn_settings')
        r.tap('st_set_network')
        self.assertEqual(dict(map(tuple, r.widgets()['set_network_form']['cells']))['Enable DHCP'], 'On')
        x, y, w, h = r.widgets()['set_network_form']['rect']
        r.touch(x + 20, y + 3 * 40 + 20)                                               # Self-signed certificate: service personnel only
        self.assertEqual(r.overlay()['dialog'], 'service_only')
        self.assertEqual(r.frame()['ui']['unsupported'], 'service only')
        r.ov('OK')
        r.touch(x + 20, y + 40 + 20)                                                   # DHCP off: the manual address fields appear
        r.ov('Off')
        self.assertIn('IP Address', dict(map(tuple, r.widgets()['set_network_form']['cells'])))
        r.tap('set_network_confirm')
        self.assertFalse(r.val('device.settings')['net.dhcp'])
        r.switch('off')
        r.switch('on')
        r.step(91)
        self.assertEqual(r.val('device.system')['language'], 'Deutsch')

    def test_user_management_rights_and_logout(self):
        r = Rig()
        r.tap('tb_menu')
        r.tap('mn_settings')
        r.tap('st_set_users')
        r.tap('um_switch')
        self.assertEqual(r.overlay()['dialog'], 'um_login_mode')                     # the Login Mode window comes first (p.58-59)
        r.ov('PIN')
        r.fill('Enter User Name', 'admin')
        r.fill('Enter PIN', '2468')
        r.fill('Repeat PIN', '2469')
        r.ov('Confirm')
        self.assertEqual(r.overlay()['dialog'], 'pin_mismatch')
        r.ov('OK')
        r.fill('Repeat PIN', '2468')
        r.ov('Confirm')
        us = r.val('users.state')
        self.assertTrue(us['enabled'])
        self.assertEqual((us['current'], us['role']), (1, 'Administrator'))
        r.tap('tb_menu')
        r.tap('mn_user')
        r.tap('us_add')
        r.fill('User Name', 'tech')
        r.ov('Confirm')
        self.assertEqual(r.overlay()['dialog'], 'credentials')
        pin = r.h._device['credentials']['pin']
        self.assertIn(f'PIN: {pin}', r.overlay()['lines'])
        r.ov('OK')
        r.row('us_list', 1)
        r.pick('Role', 'User')
        r.ov('Save')
        self.assertEqual(r.val('users.state')['users'][1]['role'], 'User')
        r.tap('tb_menu')
        r.tap('mn_logout')
        self.assertIsNone(r.val('users.state')['current'])
        r.tap('tb_home')
        self.assertEqual(r.text('tb_standby'), 'Login')
        r.tap('h_time')
        self.assertEqual(r.overlay()['dialog'], 'no_permission')                       # no extra privileges without login
        r.ov('OK')
        r.tap('tb_standby')
        r.fill('User ID or name', 'tech')
        r.fill('PIN', '0000')
        r.ov('Confirm')
        self.assertEqual(r.overlay()['dialog'], 'err_wrong_pin')
        r.ov('OK')
        r.tap('tb_standby')
        r.fill('User ID or name', 'tech')
        r.fill('PIN', pin)
        r.ov('Confirm')
        self.assertEqual(r.val('users.state')['role'], 'User')
        r.tap('h_temp')
        self.assertEqual(r.overlay()['dialog'], 'set_temp')
        r.ov('Back')
        r.tap('tb_menu')
        r.tap('mn_settings')
        r.tap('st_set_about')
        self.assertFalse(r.call(r.A, 'settings.configure', expect='rejected', settings={'about.name': 'X'})['error']['code'] != 'PERMISSION_DENIED')
        r.step(899)
        self.assertEqual(r.val('users.state')['current'], 2)
        r.step(2)
        self.assertIsNone(r.val('users.state')['current'])                             # automatic logout after 15 min
        self.assertEqual(r.val('events.list')[0]['message'], 'Automatic logout')

    def form_row(self, r, wid, k):
        x, y, w, h = r.widgets()[wid]['rect']
        return r.touch(x + 20, y + k * 40 + 20)

    def test_remaining_menu_functions(self):
        r = Rig()
        r.tap('tb_menu')
        r.tap('mn_export')
        self.assertIn('No USB storage medium is connected.', r.overlay()['lines'])
        r.ov('OK')
        r.tap('mn_contacts')
        self.assertEqual(r.overlay()['dialog'], 'contacts')
        r.ov('OK')
        r.tap('mn_events')
        r.tap('ev_filter')
        r.ov('info')
        self.assertTrue(all(c[1] == 'info' for c in r.cells('ev_list')))
        r.tap('ev_filter')
        r.ov('warning')
        self.assertEqual(r.cells('ev_list'), [])
        r.tap('ev_filter')
        r.ov('All')
        r.row('ev_list', 0)
        self.assertEqual(r.overlay()['dialog'], 'event_detail')
        r.ov('Close')
        r.call(r.A, 'program.manage', action='save', number=1, name='old')         # Save current parameters / Show info
        r.tap('tb_home')
        r.set_time(3)
        r.tap('h_prog_btn')
        r.row('prog_list', 0)
        r.ov('Save current parameters')
        self.assertEqual(r.val('programs.list')[0]['set_points']['duration_s'], 180.0)
        r.row('prog_list', 0)
        r.ov('Show info')
        self.assertEqual(r.overlay()['dialog'], 'program_info')
        r.ov('Close')
        r.tap('tb_menu')                                                                # Short Spin with the entered speed
        r.tap('mn_settings')
        r.tap('st_set_short_spin')
        self.form_row(r, 'set_short_spin_form', 0)
        r.ov('Entered')
        self.form_row(r, 'set_short_spin_form', 1)
        r.type('2000')
        r.ov('OK')
        r.tap('set_short_spin_confirm')
        self.assertEqual((r.val('device.settings')['device.short_spin'], r.val('device.settings')['device.short_spin_rpm']), ('Entered', 2000.0))
        r.tap('st_set_sound')
        self.form_row(r, 'set_sound_form', 1)
        r.ov('Off')
        r.tap('set_sound_confirm')
        self.assertFalse(r.val('device.settings')['sound.end'])
        r.tap('st_set_about')
        self.form_row(r, 'set_about_form', 0)
        r.type('lab b centrifuge')
        r.ov('OK')
        r.tap('set_about_confirm')
        self.assertEqual(r.val('device.settings')['about.name'], 'lab b centrifuge')
        r.tap('tb_home')
        r.tap('tb_short', duration_s=60.0)
        r.step(59)
        self.assertAlmostEqual(r.val('display.speed_rad_s'), 2000 * RPM)
        r.run_until_idle()

    def test_user_admin_functions(self):
        r = Rig()
        r.call(r.A, 'users.manage', action='enable', name='admin', pin='1357')
        r.call(r.A, 'users.manage', action='add', name='op')
        r.tap('tb_menu')
        r.tap('mn_settings')
        r.tap('st_set_users')
        self.form_row(r, 'set_users_form', 0)                                           # below the on / off switch: Automatic Logout
        r.type('5')
        r.ov('OK')
        r.tap('set_users_confirm')
        self.assertEqual(r.val('device.settings')['users.auto_logout_s'], 300.0)
        r.tap('tb_menu')
        r.tap('mn_user')
        r.row('us_list', 1)
        r.ov('Reset Password/PIN')
        self.assertEqual(r.overlay()['dialog'], 'credentials')
        new_pin = r.h._device['credentials']['pin']
        r.ov('OK')
        self.assertEqual(r.h._device['users']['list'][1]['pin'], new_pin)
        r.row('us_list', 1)
        r.ov('Delete')
        self.assertEqual([u['name'] for u in r.val('users.state')['users']], ['admin'])
        r.tap('tb_menu')
        r.tap('mn_settings')
        r.tap('st_set_users')
        r.tap('um_switch')
        self.assertEqual(r.overlay()['dialog'], 'um_disable')
        r.fill('PIN', '1357')
        r.ov('Confirm')
        self.assertFalse(r.val('users.state')['enabled'])
        self.assertEqual(r.text('um_switch'), 'User Management: 0 (off)')

    def test_task_reminder_and_delete(self):
        r = Rig()
        r.call(r.A, 'maintenance.task', action='add', name='Service', interval_s=3600.0)
        r.tap('tb_menu')
        r.tap('mn_settings')
        r.tap('st_set_maint')
        r.row('mq_list', 0)
        r.ov('Disable reminder')
        self.assertFalse(r.val('maintenance.tasks')[0]['reminder'])
        r.step(3601)
        self.assertTrue(r.val('maintenance.tasks')[0]['due'])
        self.assertFalse(any('Recurring task due' in e['message'] for e in r.val('events.list')))
        r.row('mq_list', 0)
        r.ov('Delete')
        self.assertEqual(r.val('maintenance.tasks'), [])

    def test_recurring_task(self):
        r = Rig()
        r.tap('tb_menu')
        r.tap('mn_settings')
        r.tap('st_set_maint')
        r.tap('mq_add')
        r.fill('Task name', 'check buckets')
        r.fill('Interval', '1')
        r.ov('Confirm')
        self.assertEqual(r.cells('mq_list')[0][:2], ['check buckets', '1 days'])
        r.step(86400)
        self.assertTrue(r.val('maintenance.tasks')[0]['due'])
        self.assertEqual(r.cells('mq_list')[0][2], 'due')
        self.assertIn('Recurring task due: check buckets', r.val('events.list')[0]['message'])
        r.row('mq_list', 0)
        r.ov('Task done')
        self.assertFalse(r.val('maintenance.tasks')[0]['due'])
        self.assertEqual(r.cells('mq_health')[0], ['S-4x Universal-Large', '0'])

    def test_clean_screen_standby_power(self):
        r = Rig()
        r.tap('tb_menu')
        r.tap('mn_clean')
        self.assertEqual(r.frame()['screen'], 'clean')
        self.assertEqual(r.call(r.A, 'run.start', expect='rejected')['error']['code'], 'LOCKED')
        for c in (1, 3):                                                               # wrong order starts over
            r.tap(f'cl_{c}')
        self.assertEqual(r.h._device['clean_lock']['next'], 1)
        for c in (1, 2, 3, 4):
            r.tap(f'cl_{c}')
        self.assertEqual(r.page(), ('gui', 'home'))
        r.tap('tb_standby')
        self.assertEqual(r.frame()['screen'], 'standby')
        self.assertEqual(r.disp()['brightness'], 0)
        r.touch(400, 240)
        self.assertEqual(r.frame()['screen'], 'gui')
        r.switch('off')
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertFalse(r.touch(400, 440))
        r.switch('on')
        self.assertEqual(r.frame()['screen'], 'boot')
        self.assertEqual(r.call(r.A, 'run.start', expect='rejected')['error']['code'], 'BOOTING')
        r.step(89)
        self.assertEqual(r.frame()['screen'], 'boot')
        r.step(2)
        self.assertEqual(r.page(), ('gui', 'home'))

    def test_power_loss_during_run_is_failed(self):
        r = Rig()
        r.tap('tb_start')
        r.step(60)
        r.switch('off')
        r.run_until_idle()
        r.switch('on')
        r.step(91)
        self.assertEqual(r.val('runs.records')[0]['result'], 'Failed')

    def test_energy_save_dims(self):
        r = Rig()
        r.call(r.A, 'settings.configure', settings={'display.energy_save': True, 'display.timeout_s': 60.0})
        r.tap('tb_home')
        r.step(61)
        self.assertEqual(r.frame()['displays'][TOUCH]['brightness'], 20)
        r.tap('tb_home')
        self.assertEqual(r.frame()['displays'][TOUCH]['brightness'], 80)


class SyncAudit(unittest.TestCase):
    """Sync audit 2026-10-19: panel / terminal runs belong to the calling session, owner guards on Stop and run changes, the direct start
    during the software start and standby, Login Mode, time zone, language."""

    def two(self):
        r = Rig()
        r.h.register_principal('agent_b')
        return r, r.h.session('agent_b')

    def gesture(self, r, sess, wid):
        x, y, w, h = r.widgets()[wid]['rect']
        r.n += 1
        return sess.invoke(capability_id='panel.touch', arguments=dict(display_id=TOUCH, x=x + w // 2, y=y + h // 2), request_id=f'g{r.n}')

    def test_panel_started_run_belongs_to_the_caller(self):
        r, b = self.two()
        self.assertTrue(r.tap('tb_start'))                                           # agent A taps Start
        op = r.val('run.operation_id')
        self.assertEqual(r.h._operations[op]['owner'], '__agent__')
        self.assertEqual(r.A.poll(operation_id=op)['status'], 'running')              # A polls its own panel-started run
        self.assertEqual(b.poll(operation_id=op)['error']['code'], 'ACCESS_DENIED')
        r.step(30)
        before = r.h.debug_snapshot()['state']
        self.assertFalse(self.gesture(r, b, 'tb_start')['result']['accepted'])         # B taps Stop on A's run: refused, run unchanged
        self.assertEqual(r.overlay()['dialog'], 'no_permission')
        after = r.h.debug_snapshot()['state']
        self.assertEqual((after['run.status'], after['run.operation_id']), (before['run.status'], before['run.operation_id']))
        self.assertEqual(r.h._operations[op]['outcome'], 'succeeded')
        r.ov('OK')
        self.assertEqual(r.call(b, 'run.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')   # terminal command of B
        self.assertEqual(r.call(b, 'centrifuge.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call(b, 'centrifuge.set_point', expect='rejected', settings={'speed_rad_s': 1000 * RPM})['error']['code'],
                         'ACCESS_DENIED')                                              # B cannot change A's running run
        self.assertEqual(r.h._operations[op]['outcome'], 'succeeded')
        r.n += 1
        self.assertEqual(r.A.cancel(operation_id=op, request_id=f'c{r.n}')['status'], 'succeeded')   # A cancels its own run
        self.assertEqual(r.val('run.status'), 'stopping')
        r.run_until_idle()
        self.assertEqual(r.A.poll(operation_id=op)['status'], 'cancelled')

    def test_mains_switch_is_physical(self):
        """Lead clarification 2026-10-19: the mains switch is a hard switch - B may switch it off under A's run; the rotor coasts down as at a
        power loss and the run is not completed."""
        r, b = self.two()
        self.assertTrue(r.tap('tb_start'))
        op = r.val('run.operation_id')
        r.step(30)
        self.assertTrue(r.call(b, 'panel.switch', input_id='mains_switch', position='off')['accepted'])
        self.assertFalse(r.val('power.enabled'))
        r.run_until_idle()
        self.assertNotEqual(r.A.poll(operation_id=op)['status'], 'succeeded')

    def test_owner_stops_from_the_panel_and_special_runs(self):
        r, b = self.two()
        self.assertEqual(self.gesture(r, b, 'tb_start')['status'], 'succeeded')       # B starts from the panel: B owns the run
        op = r.val('run.operation_id')
        self.assertEqual(r.h._operations[op]['owner'], 'agent_b')
        self.assertEqual(b.poll(operation_id=op)['status'], 'running')
        r.step(20)
        self.assertFalse(r.tap('tb_start'))                                          # A's Stop is refused
        self.assertEqual(r.overlay()['dialog'], 'no_permission')
        r.ov('OK')
        self.gesture(r, b, 'tb_start')                                               # B stops its own run on the panel
        self.assertEqual(r.val('run.status'), 'stopping')
        r.run_until_idle()
        res = r.call(b, 'run.short_spin', hold_s=5.0)                                 # Short Spin and Fast Temp runs belong to the caller
        self.assertEqual(r.h._operations[res['operation_id']]['owner'], 'agent_b')
        r.run_until_idle()
        res = r.call(r.A, 'run.fast_temp')
        self.assertEqual(r.h._operations[res['operation_id']]['owner'], '__agent__')
        self.assertEqual(r.call(b, 'run.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        r.call(r.E, 'centrifuge.stop')                                               # the evaluator is unrestricted
        self.assertEqual(r.h._operations[res['operation_id']]['outcome'], 'cancelled')

    def test_direct_start_follows_the_software_start_and_standby(self):
        r = Rig(on=False)
        r.call(r.A, 'power.set', enabled=True)
        r.call(r.A, 'lid.set_angle', angle_rad=1.5)
        r.call(r.A, 'rotor.select', configuration='large_capacity')
        r.call(r.A, 'sample.load', slot_id='slot_1', sample_id='a')
        r.call(r.A, 'sample.load', slot_id='slot_2', sample_id='b')
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        r.call(r.A, 'centrifuge.set_method', speed_rad_s=1000 * RPM, temperature_k=295.15, duration_s=60.0, acceleration_level=9, braking_level=9)
        before = r.h.debug_snapshot()
        self.assertEqual(r.call(r.A, 'centrifuge.measure', expect='rejected')['error']['code'], 'BOOTING')   # as Start (p.27)
        self.assertEqual(before, r.h.debug_snapshot())
        r.step(91)
        r.call(r.A, 'standby.set', active=True)
        self.assertEqual(r.call(r.A, 'centrifuge.measure', expect='rejected')['error']['code'], 'STANDBY')
        r.call(r.A, 'standby.set', active=False)
        r.call(r.A, 'centrifuge.measure', expect='accepted')

    def test_login_mode_password(self):
        r = Rig()
        r.tap('tb_menu')
        r.tap('mn_settings')
        r.tap('st_set_users')
        r.tap('um_switch')
        r.ov('Password')                                                             # Login Mode for all users
        self.assertEqual(r.val('device.settings')['users.login_mode'], 'Password')
        r.fill('Enter User Name', 'admin')
        r.ov('Enter Password')
        self.assertEqual(r.overlay()['mode'], 'lower')                               # text keyboard instead of the number pad
        r.type('lab')
        r.ov('12@')
        r.ov('4')
        r.ov('2')
        r.ov('OK')
        r.fill('Repeat Password', 'labx')
        r.ov('Confirm')
        self.assertEqual(r.overlay()['dialog'], 'pin_mismatch')
        r.ov('OK')
        r.ov('Repeat Password')
        r.type('lab')
        r.ov('12@')
        r.ov('4')
        r.ov('2')
        r.ov('OK')
        r.ov('Confirm')
        self.assertTrue(r.val('users.state')['enabled'])
        self.assertEqual(r.h._device['users']['list'][0]['pin'], 'lab42')
        add = r.call(r.A, 'users.manage', action='add', name='tech')                  # generated credentials follow the mode
        self.assertFalse(add['pin'].isdigit())
        self.assertEqual(len(add['pin']), 8)
        r.call(r.A, 'session.login', action='logout')
        self.assertEqual(r.call(r.A, 'session.login', expect='rejected', action='login', user='tech', pin='0000')['error']['code'], 'WRONG_PIN')
        r.call(r.A, 'session.login', action='login', user='tech', pin=add['pin'])
        r.call(r.A, 'session.login', action='logout')
        r.call(r.A, 'session.login', action='login', user='admin', pin='lab42')
        r.call(r.A, 'settings.configure', settings={'users.login_mode': 'PIN'})       # PIN mode: a text password cannot be entered any more
        r.call(r.A, 'session.login', action='logout')
        self.assertEqual(r.call(r.A, 'session.login', expect='rejected', action='login', user='admin', pin='lab42')['error']['code'], 'WRONG_PIN')
        r.tap('tb_standby')
        r.ov('PIN')
        self.assertEqual(r.overlay()['mode'], 'num')                                 # the number pad (p.39)
        self.assertEqual(sorted(b['label'] for b in r.overlay()['buttons'] if b['label'].isdigit()), list('0123456789'))
        self.assertNotIn('a', [b['label'] for b in r.overlay()['buttons']])

    def test_change_own_pin(self):
        r = Rig()
        r.call(r.A, 'users.manage', action='enable', name='admin', pin='1357')
        r.call(r.A, 'users.manage', action='add', name='op')
        r.tap('tb_menu')
        r.tap('mn_user')
        r.tap('us_change')
        r.fill('Enter current PIN', '1357')
        r.fill('Enter new PIN', '8642')
        r.fill('Repeat new PIN', '8642')
        r.ov('Confirm')
        self.assertEqual(r.overlay()['dialog'], 'pin_changed')
        self.assertEqual(r.h._device['users']['list'][0]['pin'], '8642')
        self.assertEqual(r.call(r.A, 'users.manage', expect='rejected', action='change_pin', pin='1357', new_pin='1111')['error']['code'],
                         'WRONG_PIN')
        self.assertEqual(r.call(r.A, 'users.manage', expect='rejected', action='change_pin', pin='8642', new_pin='12ab')['error']['code'],
                         'INVALID_ARGUMENT')                                           # PIN mode: digits only

    def test_time_zone_moves_the_shown_time(self):
        r = Rig()
        r.call(r.A, 'clock.set', date='2026-10-19', time='09:30')
        self.assertTrue(r.val('device.datetime').startswith('2026-10-19T09:30'))
        r.call(r.A, 'settings.configure', settings={'time.zone': 'UTC+02'})
        self.assertTrue(r.val('device.datetime').startswith('2026-10-19T11:30'))
        r.call(r.A, 'settings.configure', settings={'time.zone': 'UTC-05'})
        self.assertTrue(r.val('device.datetime').startswith('2026-10-19T04:30'))

    def test_language_is_stored_and_reported(self):
        r = Rig()
        r.call(r.A, 'settings.configure', settings={'device.language': 'Deutsch'})
        self.assertEqual(r.val('device.system')['language'], 'English')               # loaded at the next switch-on (p.72)
        r.switch('off')
        r.switch('on')
        r.step(91)
        self.assertEqual(r.val('device.system')['language'], 'Deutsch')
        r.tap('tb_menu')
        self.assertEqual(r.text('mn_title'), 'Menu')                                 # texts stay English (no translations in our manuals)


class EquivalenceRefusals(unittest.TestCase):
    def test_panel_equals_direct(self):
        a, b = Rig(), Rig()
        a.set_time(10)
        a.tap('h_speed')
        a.fill('Speed', '3500')
        a.ov('Confirm')
        a.tap('h_ramps')
        a.pick('Brake', '4')
        a.ov('Confirm')
        b.call(b.A, 'centrifuge.set_point', settings={'duration_s': 600.0, 'continuous': False, 'count_from': 'Immediately'})
        b.call(b.A, 'centrifuge.set_point', settings={'speed_mode': 'rpm', 'speed_rad_s': 3500 * RPM})
        b.call(b.A, 'centrifuge.set_point', settings={'accel': 9, 'brake': 4})
        self.assertEqual(a.sp(), b.sp())                                               # the panel writes the same SI values
        for k in ('method.speed_rad_s', 'method.duration_s', 'method.braking_level'):
            self.assertAlmostEqual(a.val(k), b.val(k))
        a.tap('tb_start')
        b.call(b.A, 'run.start')
        a.step(100)
        b.step(100)
        self.assertEqual(a.val('display.speed_rad_s'), b.val('display.speed_rad_s'))

    def test_direct_method_updates_set_points(self):
        r = Rig()
        r.call(r.A, 'centrifuge.set_method', speed_rad_s=1000 * RPM, temperature_k=280.15, duration_s=60.0, acceleration_level=5, braking_level=6)
        self.assertEqual(r.text('h_speed'), '1000 rpm')
        self.assertEqual((r.sp()['accel'], r.sp()['brake']), (4, 5))
        r.call(r.A, 'centrifuge.measure', expect='accepted')
        r.step(1)
        self.assertEqual(r.text('tb_start'), 'Stop')
        r.run_until_idle()
        self.assertEqual(r.val('runs.records')[0]['result'], 'Completed')

    def test_every_terminal_command_once(self):
        r = Rig()
        meta = rt.export_eppendorf_5910ri_contract_metadata()
        order = ['centrifuge.set_point', 'favorites.use', 'program.manage', 'settings.configure', 'clock.set', 'maintenance.task', 'events.manage',
                 'run.start', 'run.stop', 'run.short_spin', 'run.fast_temp', 'message.confirm', 'lid.open', 'users.manage', 'session.login',
                 'standby.set', 'screen.clean']
        self.assertEqual(set(order), set(rt._EPP_TERMINAL))
        for cap in order:
            m = meta['capabilities'][cap]
            args = dict(m['example_arguments'][0])
            if cap == 'run.start' or cap == 'run.short_spin' or cap == 'run.fast_temp' or cap == 'lid.open':
                r.run_until_idle()
            if cap == 'message.confirm':
                for _ in range(300):
                    r.step(10)
                    if r.val('device.notice'):
                        break
                r.run_until_idle()
            if cap == 'session.login':
                args = dict(action='login', user='1', pin='1234')
            if cap == 'users.manage':
                args = dict(action='enable', name='admin', pin='1234')
            if cap == 'standby.set':
                r.run_until_idle()
            before = r.E.read()['observations']
            res = r.call(r.A, cap, **args)
            self.assertEqual(validate_value(res, m['output_schema']), [], cap)
            after = r.E.read()['observations']
            changed = {k for k in after if after[k]['value'] != before[k]['value']} - {'device.datetime'}
            self.assertTrue(changed <= set(m['state_ids']) | {'events.list', 'runs.records', 'rotor.health', 'centrifuge.status', 'device.notice'},
                            (cap, changed - set(m['state_ids'])))

    def test_refusals_leave_state_unchanged(self):
        r = Rig()
        before = r.h.debug_snapshot()
        for cap, args, code in (('centrifuge.set_point', dict(settings={'speed_rad_s': 1.0}), 'INVALID_ARGUMENT'),
                                ('run.stop', {}, 'PRECONDITION_FAILED'), ('message.confirm', {}, 'PRECONDITION_FAILED'),
                                ('program.manage', dict(action='load', number=7), 'NOT_FOUND'),
                                ('events.manage', dict(action='export'), 'NO_USB'), ('session.login', dict(action='logout'), 'PRECONDITION_FAILED'),
                                ('settings.configure', dict(settings={'time.automatic': True}), 'NO_NETWORK'),
                                ('maintenance.task', dict(action='done', index=3), 'NOT_FOUND')):
            self.assertEqual(r.call(r.A, cap, expect='rejected', **args)['error']['code'], code, cap)
        self.assertEqual(before, r.h.debug_snapshot())
        self.assertEqual(r.call(r.A, 'centrifuge.set_point', expect='rejected', settings={'speed_rad_s': 6000 * RPM})['error']['code'],
                         'ROTOR_LIMIT')                                                # above the large rotor (4500 rpm)
        self.assertEqual(before, r.h.debug_snapshot())
        r.call(r.A, 'lid.set_angle', angle_rad=1.5)
        r.call(r.A, 'sample.load', slot_id='slot_3', sample_id='c')                     # three equal tubes: imbalance
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        r.tap('tb_start')
        self.assertEqual(r.overlay()['dialog'], 'err_imbalance')
        self.assertEqual(r.val('run.status'), 'idle')
        self.assertEqual(r.val('events.list')[0]['priority'], 'alarm')


class RenderEvidence(unittest.TestCase):
    def test_render_determinism_and_evidence(self):
        r = Rig()
        a, b = r.png(), r.png()
        self.assertEqual(a, b)
        self.assertEqual(hashlib.sha256(Rig().png()).hexdigest(), hashlib.sha256(a).hexdigest())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {}
        r.set_time(10)
        r.tap('tb_start')
        r.step(120)
        r.h.panel.save_png(TOUCH, OUT / 'home.png')
        shots['home.png'] = 'Home during a run (current values in blue, progress bar)'
        r.tap('h_speed')
        r.h.panel.save_png(TOUCH, OUT / 'set_speed.png')
        shots['set_speed.png'] = 'Set Speed input window'
        r.ov('Back')
        r.tap('tb_menu')
        r.h.panel.save_png(TOUCH, OUT / 'menu.png')
        shots['menu.png'] = 'Menu'
        r.tap('mn_settings')
        r.h.panel.save_png(TOUCH, OUT / 'settings.png')
        shots['settings.png'] = 'Settings'
        r.tap('tb_open')
        r.h.panel.save_png(TOUCH, OUT / 'error.png')
        shots['error.png'] = 'Open refused while the rotor turns'
        (OUT / 'renders.json').write_text(json.dumps({k: dict(caption=v, sha256=hashlib.sha256((OUT / k).read_bytes()).hexdigest())
                                                      for k, v in shots.items()}, indent=1) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_has_the_panel(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_eppendorf_5910ri_contract_metadata()
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for cap in list(rt._EPP_TERMINAL) + sorted(rt.EPP5910_PANEL_CAPS):
            self.assertEqual(c['capabilities'][cap]['input_schema'], meta['capabilities'][cap]['input_schema'], cap)
        for comp in ('mains_switch', 'display'):
            self.assertIn(comp, c['components'])
        self.assertTrue(c['asset_version'].startswith('1.2.'))


if __name__ == '__main__':
    unittest.main()
