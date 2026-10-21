"""Front-panel tests for CHEM-014 hach_tl2300epa (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_hach_tl2300epa.py -q

Every gesture goes through the session API (panel.press / panel.touch, the runtime's normal invoke path); the host steps the clock and the evaluator
places samples, kit standards and the lid.  Each check looks at the runtime state and at the screen (the structured frame of panel.frame; the
rendered pixels for the dark sleep / off screen and the green Read key).  Themes: the key procedures of the menu survey; panel = direct capability;
refusals (Read with the lid open, security list, wrong password, duplicate IDs, out-of-range settings); self-check and sleep timer; data log,
filter and comments; USB / printer functions show the device's not-connected messages; render determinism and the PNGs of
outputs/panel_ui/hach_tl2300epa/; contract entries of the panel and terminal capabilities."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import hach_tl2300epa as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'tl2300_01'
OUT = ROOT / 'outputs/panel_ui/hach_tl2300epa'
SURVEY_TASKS = ['measure turbidity of a sample', 'set continuous reading with ratio off', 'StablCal calibration', 'change calibration curve to 0-4000 NTU',
                'verify with Gelex', 'send data to USB']
DONE_TASKS = set()
SPEC = rt.PANEL_SPEC
COLS = SPEC['displays']['screen']['colors']


class Rig:
    def __init__(self, powered_hours=None):
        self.h = rt.HachTl2300epaRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if powered_hours is not None:
            assert self.press('button_power')
            self.step(powered_hours * 3600.0)
            self.wake()
            assert self.screen() == ('gui', 'home')

    def wake(self):
        """After a long warm-up the screen sleeps (sleep timer 1 h): the operator's first touch wakes it."""
        if self.h.read()['display.sleeping']:
            assert self.touch(200, 300)

    def call(self, sess, cap, expect=('succeeded', 'accepted'), **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        expect = (expect,) if isinstance(expect, str) else expect
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        if r['status'] != 'rejected':
            schema = rt.PANEL_CAPS[cap]['output_schema'] if cap in rt.PANEL_CAPS else rt.ALL_CAPS[cap][1]
            rt.validate(schema, r['result'])
            assert self.h.time_s == t0, cap
            return r['result']
        return r

    # host / evaluator actions
    def step(self, s):
        self.h.step(float(s))

    def lid(self, open_):
        self.call(self.E, 'lid.set_angle', angle_rad=1.7 if open_ else 0.0)

    def load_sample(self, **over):
        self.lid(True)
        a = copy.deepcopy(rt.CAPS['sample.configure'][6])
        a.update(over)
        self.call(self.E, 'sample.configure', **a)
        self.lid(False)

    def load_standard(self, value, material='stablcal'):
        self.lid(True)
        if self.h.standard is not None:
            self.call(self.E, 'standard.unload')
        if self.h.sample is not None:
            self.call(self.E, 'sample.unload')
        self.call(self.E, 'standard.load', material=material, value_ntu=float(value))
        self.lid(False)

    # gestures (agent session)
    def press(self, key):
        return self.call(self.A, 'panel.press', input_id=key)['accepted']

    def touch(self, x, y):
        return self.call(self.A, 'panel.touch', display_id='screen', x=x, y=y)['accepted']

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

    def tap(self, wid):
        w = self.widget(wid)
        assert w is not None and w['enabled'], wid
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def pick(self, wid, i=0):
        """Tap row i of a list widget."""
        w = self.widget(wid)
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + i * w['row_h'] + w['row_h'] // 2)

    def side(self, label):
        """Tap a sidebar icon by its label, scrolling with UP / DOWN as needed."""
        while self.widget('sb_up')['enabled']:
            assert self.tap('sb_up')
        for _ in range(8):
            w = next((w for w in self.frame()['displays']['screen']['widgets'] if w['id'].startswith('sb_') and w['text'] == label), None)
            if w is not None:
                x, y, ww, hh = w['rect']
                return self.touch(x + ww // 2, y + hh // 2)
            assert self.tap('sb_down')
        raise AssertionError(label)

    def overlay(self):
        return self.frame()['displays']['screen'].get('overlay')

    def button(self, label):
        """Tap a button of the top overlay (dialog, choice list, keyboard) by its label."""
        b = next((b for b in self.overlay()['buttons'] if b['label'] == label), None)
        assert b is not None, (label, [b['label'] for b in self.overlay()['buttons']])
        x, y, w, h = b['rect']
        return self.touch(x + w // 2, y + h // 2)

    def row(self, form_widget, field):
        """Tap the row of a form field."""
        w = self.widget(form_widget)
        k = w['row_ids'].index(field)
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + k * w['row_h'] + w['row_h'] // 2)

    def cells(self, form_widget):
        w = self.widget(form_widget)
        return {fid: c[1] for fid, c in zip(w['row_ids'], w['cells'])}

    def dialog_row(self, field):
        b = next(b for b in self.overlay()['buttons'] if b['id'] == 'row:' + field)
        x, y, w, h = b['rect']
        return self.touch(x + w // 2, y + h // 2)

    def type(self, text):
        """Type on the open keyboard overlay (keys by their token) and confirm with OK."""
        for ch in text:
            b = next(b for b in self.overlay()['buttons'] if b['id'] == 'key:' + ch)
            x, y, w, h = b['rect']
            assert self.touch(x + w // 2, y + h // 2), ch
        assert self.button('OK')

    def set_field(self, form_widget, field, choice):
        assert self.row(form_widget, field)
        assert self.button(choice)

    def state(self, *ids):
        o = self.E.read(instance_id=INST, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def lists(self, wid):
        return self.widget(wid)['cells']


def runtime_state(h):
    r = h.read('evaluator')
    return json.dumps(dict(read=r, settings=h.settings, ops=h.operators, sids=h.sample_ids, logs=h.logs, curve=h.curve, gain=h.gain), sort_keys=True,
                      default=float)


def calibrate_full(g, standards=(0.04, 20.0, 200.0, 1000.0, 4000.0)):
    for v in standards:
        g.load_standard(v)
        assert g.tap('cal_read')
        g.step(60.0)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_measure_turbidity_of_a_sample(self):
        g = Rig()
        self.assertEqual(g.screen(), ('off', None))
        self.assertTrue(g.press('button_power'))                                         # self-check, then the home screen
        self.assertEqual(g.screen()[0], 'selfcheck')
        g.step(10.0)
        self.assertEqual(g.screen(), ('gui', 'home'))
        g.call(g.A, 'operator.save', operator_id='anna', icon='fish', password='', level='off', replace=None)
        g.call(g.A, 'sample_id.save', sample_id='outlet', collected_date=None, collected_time=None, number=1, color='blue', replace=None)
        g.step(1800.0)                                                                    # 30 min warm-up with Ratio on (p.20)
        self.assertTrue(g.side('Login'))                                                  # Login, select the operator ID, Login
        self.assertEqual(g.screen(), ('gui', 'login'))
        self.assertEqual(g.lists('op_list'), [['anna', 'Fish', 'Off']])
        self.assertTrue(g.pick('op_list'))
        self.assertTrue(g.tap('lg_login'))
        self.assertEqual(g.state('session.operator'), 'anna')
        self.assertEqual(g.screen(), ('gui', 'home'))
        self.assertIn('anna', [w['text'] for w in g.frame()['displays']['screen']['widgets'] if w['id'].startswith('sb_login')])
        self.assertTrue(g.side('Sample ID'))                                              # Sample ID, select, Select
        self.assertTrue(g.pick('sid_list'))
        self.assertTrue(g.tap('sid_select'))
        self.assertEqual(g.text('h_sid'), 'outlet (01)')
        g.load_sample(substance_id='stablcal_200', particle_response_gain=1.0)
        self.assertTrue(g.tap('h_read'))                                                  # Read
        self.assertEqual(g.text('h_instr'), 'Reading...')
        g.step(6.8)
        self.assertEqual(g.text('h_value'), '200.0')
        self.assertEqual((g.text('h_unit'), g.text('h_mode')), ('NTU', 'Single'))
        self.assertEqual(g.text('h_sid'), 'outlet (02)')                                  # the measurement number increases
        log = g.state('datalog.entries')['reading']
        self.assertEqual([(e['sample_id'], e['number'], e['value_ntu'], e['operator']) for e in log], [('outlet', 1, 200.0, 'anna')])
        # Auto Store off: Options > Store
        g.call(g.A, 'settings.configure', settings={'datalog.auto_store': False})
        g.tap('h_read')
        g.step(6.8)
        self.assertEqual(len(g.state('datalog.entries')['reading']), 1)
        self.assertTrue(g.tap('h_options'))
        self.assertTrue(g.button('Store'))
        self.assertEqual(len(g.state('datalog.entries')['reading']), 2)
        DONE_TASKS.add('measure turbidity of a sample')

    def test_continuous_reading_with_ratio_off(self):
        g = Rig(powered_hours=1)
        g.load_sample()
        self.assertTrue(g.tap('h_options'))
        self.assertTrue(g.button('Reading Setup'))
        self.assertEqual(g.screen(), ('gui', 'reading_setup'))
        g.set_field('f_reading_w', 'reading.mode', 'Continuous')
        g.set_field('f_reading_w', 'reading.ratio', 'Off')
        self.assertEqual(g.cells('f_reading_w')['reading.mode'], 'Continuous')
        self.assertEqual(g.state('method.mode'), 'continuous')
        self.assertFalse(g.state('method.ratio_enabled'))
        self.assertTrue(g.tap('rs_close'))
        self.assertEqual(g.text('h_ratio'), 'Ratio: Off')                                 # indicator on the reading window
        self.assertEqual(g.text('h_mode'), 'Continuous')
        self.assertTrue(g.tap('h_read'))
        self.assertEqual(g.text('h_read'), 'Done')
        g.step(6.8 * 3)
        self.assertEqual(g.text('h_value'), '20.00')
        self.assertEqual(g.state('cycle.phase'), 'running')
        self.assertTrue(g.tap('h_read'))                                                  # Done
        self.assertEqual(g.state('cycle.phase'), 'cancelled')
        self.assertEqual(g.text('h_read'), 'Read')
        self.assertEqual([e['mode'] for e in g.state('datalog.entries')['reading']], ['continuous'])
        DONE_TASKS.add('set continuous reading with ratio off')

    def test_stablcal_rapidcal_calibration(self):
        g = Rig(powered_hours=1)
        self.assertTrue(g.side('Calibration'))                                            # standard value of the curve shows
        self.assertEqual(g.screen(), ('gui', 'calibration'))
        f = g.frame()['displays']['screen']
        self.assertEqual(f['title'], 'StablCal RapidCal (0-40 NTU)')
        self.assertEqual(g.widget('cal_rows')['cells'], [['20 NTU', '']])
        self.assertEqual(g.text('cal_instr'), 'Insert the 20 NTU standard and push Read.')
        g.load_sample(substance_id='stablcal_20', particle_response_gain=0.8)            # a 20 NTU StablCal vial that reads 16 on the factory scale
        self.assertTrue(g.tap('cal_read'))
        self.assertEqual(g.text('cal_instr'), 'Reading... wait 1 minute.')
        g.step(59.0)
        self.assertTrue(g.state('procedure.state')['measuring'])
        g.step(1.0)
        self.assertEqual(g.widget('cal_rows')['cells'], [['20 NTU', '16.00']])
        self.assertEqual(g.text('cal_instr'), 'Push Store to save the new calibration data.')
        self.assertTrue(g.tap('cal_store'))
        self.assertAlmostEqual(g.state('calibration.gain'), 1.25)
        self.assertEqual(g.screen(), ('gui', 'home'))
        self.assertEqual(g.state('datalog.entries')['calibration'][-1]['curve'], 'StablCal RapidCal (0-40 NTU)')
        g.tap('h_read')
        g.step(6.8)
        self.assertEqual(g.text('h_value'), '20.00')
        DONE_TASKS.add('StablCal calibration')

    def test_change_curve_to_0_4000_and_full_calibration_with_verify_after_cal(self):
        g = Rig(powered_hours=1)
        g.side('Calibration')
        self.assertTrue(g.tap('cal_options'))                                             # Options > Calibration Setup
        self.assertTrue(g.button('Calibration Setup'))
        g.set_field('f_cal_w', 'calibration.curve', 'StablCal (0-4000 NTU)')
        g.set_field('f_cal_w', 'calibration.verify_after_cal', 'On')
        self.assertTrue(g.tap('cs_close'))                                                 # back to the calibration with the new curve
        self.assertEqual(g.frame()['displays']['screen']['title'], 'StablCal (0-4000 NTU)')
        self.assertEqual([r[0] for r in g.widget('cal_rows')['cells']], ['<0.1 NTU', '20 NTU', '200 NTU', '1000 NTU', '4000 NTU', 'Verification 20 NTU'])
        # a wrong standard first: the 200 NTU vial when the blank is due
        g.load_standard(200.0)
        g.tap('cal_read')
        g.step(60.0)
        self.assertEqual(g.text('cal_msg'), 'The standard value is too high.')
        calibrate_full(g, [0.04, 20.0, 200.0, 1000.0, 4000.0])
        self.assertEqual(g.text('cal_instr'), 'Insert the verification standard and push Read.')
        g.load_standard(20.0, 'gelex')
        g.tap('cal_read')
        g.step(60.0)
        self.assertEqual(g.widget('cal_rows')['cells'][-1], ['Verification 20 NTU', '20.00 Pass'])
        self.assertTrue(g.tap('cal_store'))
        self.assertEqual(g.state('calibration.status'), 'stablcal_full')
        self.assertEqual(len(g.state('datalog.entries')['verification']), 1)
        g.load_standard(1000.0)
        g.tap('h_read')
        g.step(6.8)
        self.assertEqual(g.text('h_value'), '1000')                                       # 0-4000 range: no calibration-range message
        self.assertIsNone(g.text('h_msg') or None)
        DONE_TASKS.add('change calibration curve to 0-4000 NTU')

    def test_verify_with_gelex(self):
        g = Rig(powered_hours=1)
        self.assertTrue(g.side('Verification'))
        self.assertEqual(g.text('v_std'), 'Verification standard: 20.000 NTU')
        self.assertTrue(g.tap('v_options'))                                               # Options > Verification Setup: the Gelex value
        self.assertTrue(g.button('Verification Setup'))
        self.assertTrue(g.row('f_ver_w', 'verification.standard_ntu'))
        self.assertEqual(g.overlay()['kind'], 'keyboard')
        g.type('18.5')
        self.assertEqual(g.state('device.settings')['verification.standard_ntu'], 18.5)
        self.assertTrue(g.tap('vs_close'))
        g.load_standard(18.9, 'gelex')                                                    # within 5 %
        self.assertTrue(g.tap('v_read'))
        g.step(6.8)
        self.assertEqual(g.text('v_result'), '18.900 NTU  Pass')
        g.load_standard(20.0, 'gelex')                                                    # 8 % off
        g.tap('v_read')
        g.step(6.8)
        self.assertEqual(g.text('v_result'), '20.000 NTU  Fail')
        self.assertEqual(g.text('v_msg'), 'Verification failed.')
        self.assertEqual([e['passed'] for e in g.state('datalog.entries')['verification']], [True, False])
        DONE_TASKS.add('verify with Gelex')

    def test_send_data_to_usb_shows_not_connected(self):
        g = Rig(powered_hours=1)
        g.load_sample()
        g.tap('h_read')
        g.step(6.8)
        g.side('Setup')
        g.row('f_setup_w', 'l_peripherals')                                               # Setup > Peripherals: Not Connected
        self.assertEqual(g.text('pe_usb'), 'USB Memory: Not Connected')
        g.tap('pe_close')
        g.tap('su_close')
        g.side('Data Log')
        g.tap('dl_reading')
        self.assertEqual(len(g.lists('ll_reading')), 1)
        g.pick('ll_reading')
        self.assertTrue(g.tap('ll_options'))
        self.assertFalse(g.button('Send Data Log'))                                       # no receiving device
        self.assertEqual(g.overlay()['lines'], ['Connect a receiving device.'])
        self.assertIn('needs', g.frame()['ui']['unsupported'])
        DONE_TASKS.add('send data to USB')


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def test_settings_ids_and_reading(self):
        a, b = Rig(powered_hours=1), Rig(powered_hours=1)
        a.tap('h_options')
        a.button('Reading Setup')
        a.set_field('f_reading_w', 'reading.unit', 'EBC')
        a.set_field('f_reading_w', 'reading.signal_avg', 'On')
        a.row('f_reading_w', 'datalog_setup')
        a.set_field('f_datalog_w', 'datalog.comments', 'On')
        b.call(b.A, 'settings.configure', settings={'reading.unit': 'EBC'})
        b.call(b.A, 'settings.configure', settings={'reading.signal_avg': True})
        b.call(b.A, 'settings.configure', settings={'datalog.comments': True})
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.tap('ds_close')
        a.tap('rs_close')
        a.side('Sample ID')
        a.tap('sid_options')
        a.button('New')
        a.row('f_sample_w', 'sample_id')
        a.type('feed')
        a.set_field('f_sample_w', 'add_number', 'On')
        a.set_field('f_sample_w', 'color', 'Green')
        a.tap('se_ok')
        b.call(b.A, 'sample_id.save', sample_id='feed', collected_date=None, collected_time=None, number=0, color='green', replace=None)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        for g in (a, b):
            g.load_sample()
        a.tap('sid_close')
        a.tap('h_read')
        b.call(b.A, 'acquisition.start')
        for g in (a, b):
            g.step(10.0)
        self.assertEqual(a.text('h_value'), '4.900')                                      # 20 NTU in EBC (x 9.8 / 40), Signal Avg 10 s
        self.assertEqual(json.loads(runtime_state(a.h))['logs'], json.loads(runtime_state(b.h))['logs'])


# ---------------------------------------------------------------- refusals, self-check, sleep, security, data log
class Rules(unittest.TestCase):
    def test_read_refusals_show_the_reason(self):
        g = Rig()
        g.press('button_power')
        g.step(10.0)
        g.load_sample()
        self.assertFalse(g.tap('h_read'))                                                 # still warming up
        self.assertEqual(g.overlay()['lines'], ['Warm-up: 30 min (Ratio on) or 60 min (Ratio off).'])
        g.button('OK')
        g.step(1800.0)
        g.lid(True)
        self.assertFalse(g.tap('h_read'))
        self.assertEqual(g.overlay()['lines'], ['Close the lid until a click is heard.'])
        g.button('OK')
        g.lid(False)
        self.assertTrue(g.tap('h_read'))
        g.tap('h_options')                                                                # settings refused while reading
        g.button('Reading Setup')
        g.row('f_reading_w', 'reading.unit')
        self.assertFalse(g.button('EBC'))
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'BUSY')
        self.assertEqual(g.state('device.settings')['reading.unit'], 'NTU')

    def test_selfcheck_errors_and_power(self):
        g = Rig()
        g.lid(True)
        g.press('button_power')
        g.step(10.0)
        self.assertEqual(g.screen()[0], 'selfcheck_error')                                # dark voltage: the lid is open
        self.assertIn(' Error 3', g.frame()['displays']['screen']['lines'])
        self.assertFalse(g.touch(100, 100))
        g.press('button_power')
        g.lid(False)
        g.load_sample()
        g.press('button_power')
        g.step(10.0)
        self.assertIn(' Error 8', g.frame()['displays']['screen']['lines'])              # a vial in the holder at the self-test
        g.press('button_power')
        self.assertEqual(g.screen(), ('off', None))
        self.assertEqual(int(g.h.panel.render('screen').max()), 0)

    def test_sleep_timer_and_wake(self):
        g = Rig()
        g.press('button_power')
        g.step(10.0)
        g.step(3589.0)
        self.assertGreater(int(g.h.panel.render('screen').max()), 0)
        g.step(1.0)                                                                       # 1 h without activity (default)
        self.assertTrue(g.state('display.sleeping'))
        self.assertEqual(int(g.h.panel.render('screen').max()), 0)
        self.assertTrue(g.touch(200, 400))                                                # the first touch only wakes
        self.assertFalse(g.state('display.sleeping'))
        self.assertEqual(g.screen(), ('gui', 'home'))
        self.assertEqual(g.state('cycle.phase'), 'idle')
        g.side('Setup')
        g.row('f_setup_w', 'l_power')
        g.set_field('f_power_w', 'power.sleep_timer_s', 'OFF')
        g.step(50000.0)
        self.assertFalse(g.state('display.sleeping'))

    def test_security_list_password_and_levels(self):
        g = Rig(powered_hours=1)
        g.side('Setup')
        g.row('f_setup_w', 'l_security')
        g.set_field('f_security_w', 'security.enabled', 'On')
        self.assertFalse(g.tap('sec_close'))                                              # no security password yet
        self.assertEqual(g.overlay()['lines'], ['Set the Security Password first.'])
        g.button('OK')
        g.row('f_security_w', 'sec_password')
        g.dialog_row('password')
        g.type('secret')
        g.button('OK')
        self.assertTrue(g.tap('sec_close'))
        self.assertTrue(g.state('device.settings')['security.enabled'])
        g.call(g.A, 'session.logout')                                                     # logout also ends the unlock
        g.call(g.A, 'settings.configure', expect='rejected', settings={'security.level.calibration': 'two'})
        g.call(g.A, 'security.unlock', password='secret')
        g.call(g.A, 'settings.configure', settings={'security.level.calibration': 'two'})
        g.call(g.A, 'operator.save', operator_id='bob', icon='person', password='pw', level='one', replace=None)
        g.call(g.A, 'session.logout')
        self.assertTrue(g.tap('su_close'))
        self.assertFalse(g.side('Calibration'))                                           # protected: asks for the security password
        self.assertEqual(g.overlay()['dialog'], 'unlock')
        g.dialog_row('password')
        g.type('wrong')
        self.assertFalse(g.button('OK'))
        self.assertEqual(g.overlay()['lines'], ['Invalid password'])
        g.button('OK')
        self.assertTrue(g.side('Login'))                                                  # a one-key operator is not enough for two keys
        g.pick('op_list')
        g.tap('lg_login')
        self.assertEqual(g.overlay()['dialog'], 'login_pw')
        g.dialog_row('password')
        g.type('pw')
        g.button('OK')
        self.assertEqual(g.state('session.operator'), 'bob')
        self.assertFalse(g.side('Calibration'))
        g.button('Cancel')
        g.call(g.A, 'security.unlock', password='secret')
        self.assertTrue(g.side('Calibration'))

    def test_operator_and_sample_id_lists(self):
        g = Rig(powered_hours=1)
        g.side('Login')
        g.tap('lg_options')
        g.button('New')
        self.assertFalse(g.tap('oe_ok'))                                                  # an ID is required
        self.assertEqual(g.overlay()['lines'], ['Enter an ID.'])
        g.button('OK')
        g.row('f_operator_w', 'operator_id')
        g.type('anna')
        g.set_field('f_operator_w', 'icon', 'Butterfly')
        self.assertFalse(g.widget('f_operator_w')['disabled_rows'] == [])                 # security level needs security on
        self.assertTrue(g.tap('oe_ok'))
        g.tap('lg_options')
        g.button('New')
        g.row('f_operator_w', 'operator_id')
        g.type('anna')
        self.assertFalse(g.tap('oe_ok'))
        self.assertEqual(g.overlay()['lines'], ['The ID already exists.'])
        g.button('OK')
        g.tap('oe_cancel')
        g.pick('op_list')
        g.tap('lg_options')
        g.button('Edit')
        g.set_field('f_operator_w', 'icon', 'Fish')
        g.tap('oe_ok')
        self.assertEqual(g.state('operators.list'), [{'operator_id': 'anna', 'icon': 'fish', 'level': 'off', 'has_password': False}])
        g.tap('lg_options')
        g.button('Delete')
        g.button('OK')
        self.assertEqual(g.state('operators.list'), [])
        g.tap('lg_close')
        g.side('Sample ID')
        for name in ('aa', 'bb'):
            g.tap('sid_options')
            g.button('New')
            g.row('f_sample_w', 'sample_id')
            g.type(name)
            g.tap('se_ok')
        self.assertEqual([r[1] for r in g.lists('sid_list')], ['aa', 'bb'])
        g.tap('sid_options')
        self.assertFalse(g.button('Import Sample ID list'))
        self.assertEqual(g.overlay()['lines'], ['USB memory is not connected.'])
        g.button('OK')
        g.tap('sid_options')
        g.button('Delete All Sample IDs')
        g.button('OK')
        self.assertEqual(g.state('sample_ids.list'), [])

    def test_data_log_filter_details_comment_delete(self):
        g = Rig(powered_hours=1)
        g.call(g.A, 'sample_id.save', sample_id='aa', collected_date=None, collected_time=None, number=None, color=None, replace=None)
        g.call(g.A, 'sample_id.save', sample_id='bb', collected_date=None, collected_time=None, number=None, color=None, replace=None)
        g.call(g.A, 'settings.configure', settings={'datalog.comments': True})
        g.load_sample()
        for sid in ('aa', 'bb', 'aa'):
            g.call(g.A, 'sample_id.select', sample_id=sid)
            g.tap('h_read')
            g.step(6.8)
        g.side('Data Log')
        g.tap('dl_reading')
        self.assertEqual([r[1] for r in g.lists('ll_reading')], ['aa', 'bb', 'aa'])
        g.tap('ll_filter')
        g.set_field('f_filter_w', 'datalog.filter', 'On')
        g.set_field('f_filter_w', 'datalog.filter_sample', 'bb')
        g.tap('lf_close')
        self.assertEqual(g.text('ll_filter'), 'Filter: On')
        self.assertEqual([r[1] for r in g.lists('ll_reading')], ['bb'])
        g.pick('ll_reading')
        g.tap('ll_details')
        self.assertIn('Sample ID: bb', g.overlay()['lines'])
        g.button('Comment')
        g.dialog_row('comment')
        g.type('rerun')
        g.button('OK')
        self.assertEqual([e['comment'] for e in g.state('datalog.entries')['reading']], ['', 'rerun', ''])
        g.tap('ll_options')
        g.button('Delete Data')
        g.button('Filtered data')
        self.assertEqual([e['sample_id'] for e in g.state('datalog.entries')['reading']], ['aa', 'aa'])
        g.tap('ll_options')
        g.button('Delete Data')
        g.button('All data')
        self.assertEqual(g.state('datalog.entries')['reading'], [])

    def test_bubble_reject_rst_reminders_timer_and_diagnostics(self):
        g = Rig(powered_hours=1)
        g.load_sample(bubble_excess_ntu=10.0, bubble_tau_s=30.0)
        g.tap('h_read')
        g.step(6.8)
        self.assertFalse(g.state('turbidity.stable'))
        self.assertEqual(g.text('h_value'), '----')                                       # Bubble Reject: not shown, not stored
        self.assertEqual(g.state('datalog.entries')['reading'], [])
        g.call(g.A, 'settings.configure', settings={'reading.mode': 'rst'})
        g.lid(True)
        g.call(g.E, 'sample.unload')
        g.lid(False)
        g.load_sample()
        g.tap('h_read')
        g.step(6.8 * 3)
        self.assertEqual(g.text('h_mode'), 'RST')
        self.assertEqual(g.text('h_value'), '20.00')
        g.tap('h_read')
        g.call(g.A, 'settings.configure', settings={'calibration.reminder_days': 1})
        self.assertEqual(g.text('h_msg'), 'Next calibration is due!')
        self.assertTrue(g.text('h_cal').endswith(' ?'))
        g.side('Timer')
        g.row('f_timer_w', 'minutes')
        g.type('1')
        g.row('f_timer_w', 'seconds')
        g.type('30')
        g.tap('tm_start')
        self.assertEqual(g.text('tm_remaining'), '01:30')
        g.step(90.0)
        self.assertEqual((g.text('tm_remaining'), g.text('tm_state')), ('00:00', 'Timer expired'))
        g.tap('tm_close')
        g.side('Diagnostics')
        g.tap('dg_update')
        self.assertFalse(g.button('OK'))
        self.assertEqual(g.overlay()['lines'], ['USB memory is not connected.'])
        g.button('OK')
        self.assertFalse(g.tap('dg_factory'))
        self.assertEqual(g.overlay()['lines'], ['For factory/service use only.'])

    def test_rejected_and_unknown_gestures(self):
        g = Rig()
        self.assertFalse(g.touch(100, 100))                                               # off: only the power button
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='key_nope')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.touch', expect='rejected', display_id='screen', x=800, y=0)   # just outside the 800 x 480 screen (640 until 2026-10-21)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.call(g.A, 'standard.load', expect='rejected', material='gelex', value_ntu=1.0)['error']['code'], 'ACCESS_DENIED')


# ---------------------------------------------------------------- rendering and contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig(powered_hours=1)
            g.call(g.A, 'sample_id.save', sample_id='outlet', collected_date=None, collected_time=None, number=1, color='blue', replace=None)
            g.call(g.A, 'sample_id.select', sample_id='outlet')
            g.load_sample(substance_id='stablcal_200')
            g.tap('h_read')
            g.step(6.8)
            return g
        a, b = scenario(), scenario()
        ia, ib = a.h.panel.render('screen'), b.h.panel.render('screen')
        self.assertEqual(ia.dtype.name, 'uint8')
        self.assertTrue((ia == ib).all())
        self.assertEqual(tuple(int(v) for v in ia[400, 200]), tuple(COLS['primary']))   # the green Read key
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('screen', OUT / 'home.png')}
        a.tap('h_options')
        a.button('Reading Setup')
        shots['menu'] = a.h.panel.save_png('screen', OUT / 'menu.png')
        a.tap('rs_close')
        a.side('Calibration')
        a.load_standard(200.0)
        a.tap('cal_read')
        a.step(60.0)
        self.assertEqual(a.text('cal_msg'), 'The standard value is too high.')
        shots['calibration'] = a.h.panel.save_png('screen', OUT / 'calibration.png')
        a.tap('cal_exit')
        a.lid(True)
        self.assertFalse(a.tap('h_read'))
        shots['error'] = a.h.panel.save_png('screen', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
            self.assertEqual((s['width'], s['height']), (800, 480))   # 5:3 as the manual's figure 3 (640 x 480 until 2026-10-21)
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_and_copies(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='screen')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.press('button_power')
        self.assertNotEqual(g.call(g.A, 'panel.capture', display_id='screen')['image']['sha256'], r['image']['sha256'])
        g.step(10.0)
        c = copy.deepcopy(g.h)                                                             # a copied runtime gets its own panel
        self.assertIs(c.panel.host, c)
        self.assertIsNot(c.panel, g.h.panel)
        self.assertNotIn('panel', vars(g.h))

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(SPEC, set(rt.ALL_CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.touch', 'panel.capture', 'panel.frame'})
        m = rt.export_hach_tl2300epa_contract_metadata()
        t = rt.export_hach_tl2300epa_terminal_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k, e in list(t['capabilities'].items()) + list(m['capabilities'].items()):
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'errors'):
                self.assertEqual(c['capabilities'][k][key], e[key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/hach_tl2300epa.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('tl2300_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class CallerRule(unittest.TestCase):
    def test_a_run_started_on_the_panel_belongs_to_the_caller(self):
        g = Rig(powered_hours=1)
        g.load_sample()
        self.assertTrue(g.tap('h_read'))                                                  # Read started on the panel by the agent
        op = g.h.operation_id
        self.assertEqual((g.state('cycle.phase'), g.h._owners.get(op)), ('running', 'agent'))
        g.A.poll(op)                                                                      # the caller may follow it
        g.h.register_principal('agent_b', 'agent')
        for act in (lambda: g.h.session('agent_b').poll(op), lambda: g.h.session('agent_b').cancel(op, 'b1')):
            with self.assertRaises(rt.ActionRejected):                                    # another agent may not follow or cancel it
                act()
        self.assertEqual(g.state('cycle.phase'), 'running')
        g.A.cancel(op, 'a1')                                                              # the caller may cancel it
        self.assertNotEqual(g.state('cycle.phase'), 'running')

    def test_another_agent_cannot_stop_or_switch_off_the_reading(self):
        """Sync audit 2026-10-19: Done and the power key of another agent are refused like its direct acquisition.stop / power.set (state unchanged)."""
        g = Rig(powered_hours=1)
        g.load_sample()
        g.call(g.A, 'method.configure', ratio_enabled=True, average_count=1, mode='continuous')
        self.assertTrue(g.tap('h_read'))
        g.step(7.0)
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        snap = lambda: json.dumps(g.h.read('evaluator'), sort_keys=True, default=float)  # noqa: E731
        before = snap()
        x, y, ww, hh = g.widget('h_read')['rect']
        self.assertFalse(g.call(B, 'panel.touch', display_id='screen', x=x + ww // 2, y=y + hh // 2)['accepted'])   # B taps Done
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'ACCESS_DENIED')
        self.assertEqual(g.overlay()['lines'], ["Not allowed: another user's reading is in progress."])            # the refusal shows on the screen
        self.assertTrue(g.button('OK'))
        self.assertFalse(g.call(B, 'panel.press', input_id='button_power')['accepted'])                           # B presses the power key
        self.assertEqual(g.overlay()['lines'], ["Not allowed: another user's reading is in progress."])
        self.assertTrue(g.button('OK'))
        for cap, args in (('acquisition.stop', {}), ('power.set', dict(enabled=False))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(snap(), before)
        self.assertTrue(g.tap('h_read'))                                                  # the owner's Done
        self.assertEqual(g.state('cycle.phase'), 'cancelled')

    def test_another_agent_may_open_the_lid_and_the_instrument_cancels_the_reading(self):
        """Sweep 2026-10-19: opening the lid is a physical action (not refused); the instrument's own reaction cancels the reading."""
        g = Rig(powered_hours=1)
        g.load_sample()
        self.assertTrue(g.tap('h_read'))
        g.h.register_principal('agent_b', 'agent')
        g.call(g.h.session('agent_b'), 'lid.set_angle', angle_rad=1.7)
        self.assertEqual((g.state('cycle.phase'), g.state('lid.angle_rad')), ('cancelled', 1.7))
        self.assertIsNone(g.overlay())


class Faults(unittest.TestCase):
    def test_injected_hardware_fault_stops_the_reading_and_the_self_check(self):
        """Manual section 8: self-check error numbers of internal hardware (evaluator fault.inject); remedy power off / on; repaired by fault.clear."""
        g = Rig(powered_hours=1)
        g.load_sample()
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code=7)['error']['code'], 'ACCESS_DENIED')   # evaluator only
        self.assertTrue(g.tap('h_read'))
        self.assertEqual(g.call(g.E, 'fault.inject', code=7), {'fault': 7})                # lamp voltage
        self.assertEqual(g.state('cycle.phase'), 'failed')
        self.assertIn('Hardware error', g.state('display.reading')['messages'])
        self.assertFalse(g.tap('h_read'))                                                 # Read refused, the screen says why
        self.assertEqual(g.overlay()['lines'], ['Hardware error'])
        g.button('OK')
        self.assertEqual(g.call(g.A, 'acquisition.start', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')
        for _ in range(2):                                                                # the fault persists: every self-check stops with it
            g.press('button_power')
            g.press('button_power')
            g.step(10.0)
            self.assertEqual(g.screen()[0], 'selfcheck_error')
            self.assertIn(' Error 7', g.frame()['displays']['screen']['lines'])
        self.assertEqual(g.call(g.E, 'fault.clear'), {'fault': None})                     # repaired; the error stays until the next power-up
        self.assertEqual(g.screen()[0], 'selfcheck_error')
        g.press('button_power')
        g.lid(True)                                                                       # (no vial in the holder at the self-test: error 8)
        g.call(g.E, 'sample.unload')
        g.lid(False)
        g.press('button_power')
        g.step(10.0)
        self.assertEqual(g.state('selfcheck.state'), {'phase': 'passed', 'error': None})
        self.assertNotIn('Hardware error', g.state('display.reading')['messages'])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
