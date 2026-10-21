"""front-panel tests for CHEM-166 eppendorf_cellxpertc170i (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_eppendorf_cellxpertc170i.py -q

Every gesture goes through the session API (panel.touch on the touch screen, panel.switch on the mains switch).  Each check looks at the runtime state
and at the screen: the structured frame (panel.frame) and, for colours and brightness, the rendered pixels.  Themes: the eight key procedures of the
menu survey (set point with the number pad, CO2 control, alarm limit, mute / acknowledge, high-temperature disinfection, event export, screen lock,
performance check); the other menu functions and the device functions behind them (slider, fine adjustment and last used values, control restart,
user management with rights and login, recurring tasks, operation records, charts, export, date / time, network, acoustic, brightness, home screen
configuration, relays and relay test, screen lock timer, contamination status); panel = direct capability; refusals and locks; render determinism and
the PNGs of outputs/panel_ui/eppendorf_cellxpertc170i/; contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import eppendorf_cellxpertc170i as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'incubator_01'
SCR = 'screen'
OUT = ROOT / 'outputs/panel_ui/eppendorf_cellxpertc170i'
SPEC = rt.PANEL_SPEC
SURVEY_TASKS = ['set the chamber temperature setpoint', 'switch CO2 control off or on', 'change an alarm limit', 'silence and acknowledge an alarm message',
                'start high-temperature disinfection', 'export the event log to USB', 'lock and unlock the touch screen', 'performance check (temperature)']
DONE_TASKS = set()


class Rig:
    """A configured incubator with an agent and an evaluator session; gestures only through panel.* capabilities."""

    def __init__(self, on=True, settle=600.0, seed=1, **plant):
        self.h = rt.EppendorfCellxpertC170iRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
        if on:
            assert self.switch('on')
            self.step(settle)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        before = self.h.read('evaluator')
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        if r['status'] in ('succeeded', 'accepted') and cap != 'sim.step':
            assert self.h.t == t0, cap                                           # commands and gestures take no simulated time
            after = self.h.read('evaluator')
            changed = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)}
            assert not changed - set(rt.EFFECTS.get(cap, [])), (cap, sorted(changed - set(rt.EFFECTS.get(cap, []))))
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='switch_mains', position=pos)['accepted']

    def state(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCR]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCR, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid, hold=None):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2, **({'duration_s': hold} if hold else {}))

    def tap_text(self, text):
        for w in self.disp()['widgets']:
            if w['text'] == text and w['type'] == 'button':
                x, y, ww, hh = w['rect']
                return self.touch(x + ww // 2, y + hh // 2)
        raise AssertionError(f'{text!r} not on {self.page()}: {[w["text"] for w in self.disp()["widgets"]]}')

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def row(self, wid, first):
        w = self.widgets()[wid]
        firsts = [c[0] for c in w['cells']]
        assert first in firsts, (first, firsts)
        k = firsts.index(first)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def ov_row(self, field_id):
        """Tap a form row inside the top dialog."""
        return self.ov_id('row:' + field_id)

    def ov_id(self, bid):
        for b in self.overlay()['buttons']:
            if b['id'] == bid:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(bid)

    def form_row(self, wid, field_id):
        w = self.widgets()[wid]
        k = w['row_ids'].index(field_id)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def type(self, text, ok=True):
        """Type on the open keyboard (the shown text is replaced)."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov['kind']
        labels = {b['label'] for b in ov['buttons']}
        if ov['field']:
            for _ in range(len(ov['field'])):
                self.ov('Del')
        for ch in text:
            if '7' in labels and 'q' not in labels:
                assert self.ov(ch), ch
                continue
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit():
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if ch.isupper() != (self.overlay()['mode'] == 'upper'):
                    self.ov('Shift')
                self.ov(ch)
        if ok:
            return self.ov('OK')
        return True

    def menu(self, item):
        assert self.tap('tb_menu')
        return self.tap_text(item)

    def pixel(self, x, y):
        return [int(v) for v in self.h.panel.render(SCR)[y, x]]


def runtime_state(h):
    r = h.read('evaluator')
    r.pop('clock.time_s')
    r.pop('device.clock')
    return json.dumps(dict(read=r, setp=h.setp, ctrl=h.ctrl, alarms=h.alarm_cfg, dev=h.dev, tasks=h.tasks, users={k: v for k, v in h.users.items()}),
                      sort_keys=True, default=str)


def prepare_htd(g):
    """Robot / host steps of the HTD checklist: doors open, cover on the RH sensor, doors closed (tray is empty from the start)."""
    for cap, a in (('door.outer', dict(open=True)), ('door.latch', dict(open=True)), ('door.inner', dict(open=True))):
        g.call(g.A, cap, **a)
        g.step(2.0)
    g.call(g.A, 'rh_sensor.cover', on=True)
    for cap, a in (('door.inner', dict(open=False)), ('door.latch', dict(open=False)), ('door.outer', dict(open=False))):
        g.call(g.A, cap, **a)
        g.step(2.0)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_set_the_chamber_temperature_setpoint(self):
        g = Rig()
        self.assertEqual(g.page(), ('gui', 'home'))
        assert g.row('home_tiles', 'Temperature')
        self.assertEqual(g.page(), ('gui', 'f_temperature'))
        assert g.tap('fn_temperature_value')                                   # tap the set point: the number pad
        self.assertEqual(g.overlay()['kind'], 'keyboard')
        g.type('36.5')
        self.assertAlmostEqual(g.state('setpoint.temperature_k'), 309.65, places=6)
        self.assertEqual(g.widgets()['fn_temperature_value']['value_text'], '36.5 °C')
        assert g.tap('fn_temperature_plus')                                     # fine adjustment +0.1
        self.assertAlmostEqual(g.state('setpoint.temperature_k'), 309.75, places=6)
        w = g.widgets()['fn_temperature_slider']                                # drag the slider to its right end: 50 C
        x, y, ww, hh = w['rect']
        assert g.touch(x + 10, y + hh // 2, to_x=x + ww - 1, to_y=y + hh // 2)
        self.assertAlmostEqual(g.state('setpoint.temperature_k'), 323.15, places=6)
        assert g.tap('fn_temperature_recent')                                   # last used values: 50.0, 36.6, 36.5
        self.assertEqual([b['label'] for b in g.overlay()['buttons']][:3], ['50.0 °C', '36.6 °C', '36.5 °C'])
        g.ov('36.5 °C')
        self.assertAlmostEqual(g.state('setpoint.temperature_k'), 309.65, places=6)
        assert g.tap('fn_temperature_value')
        g.type('60')                                                            # outside 22-50 C: the number pad says so and stays
        self.assertEqual(g.overlay()['error'], 'Out of range')
        g.ov('Cancel')
        self.assertAlmostEqual(g.state('setpoint.temperature_k'), 309.65, places=6)
        DONE_TASKS.add('set the chamber temperature setpoint')

    def test_switch_co2_control_off_and_on(self):
        g = Rig(settle=600.0)
        assert g.row('home_tiles', 'CO2 Concentration')
        self.assertEqual(g.widgets()['fn_co2_actual']['text'], 'Initializing Sensor')          # 30 min after switch-on
        self.assertFalse(g.widgets()['fn_co2_slider']['enabled'])
        self.assertFalse(g.tap('fn_co2_plus'))                                  # set point cannot be changed while initializing
        self.assertTrue(g.state('control.co2_enabled'))
        assert g.tap('fn_co2_status')                                           # the status button
        self.assertFalse(g.state('control.co2_enabled'))
        self.assertFalse(g.widgets()['fn_co2_status']['value'])
        assert g.tap('fn_co2_status')
        self.assertTrue(g.state('control.co2_enabled'))
        g.step(1800.0)
        self.assertTrue(g.widgets()['fn_co2_slider']['enabled'])
        assert g.tap('fn_co2_plus')
        self.assertAlmostEqual(g.state('setpoint.co2_fraction'), 0.051, places=6)
        DONE_TASKS.add('switch CO2 control off or on')

    def test_change_an_alarm_limit(self):
        g = Rig()
        g.menu('Alarms')
        self.assertEqual(g.page(), ('gui', 'alarms'))
        self.assertEqual((g.text('al_temperature_lo'), g.text('al_temperature_hi'), g.text('al_temperature_on')), ('36.5 °C', '37.5 °C', 'v'))
        assert g.tap('al_temperature')
        assert g.tap('a_temperature_high')
        g.type('38.0')
        self.assertEqual(g.h.alarm_cfg['temperature']['high_k'], 1.0)
        assert g.tap('a_temperature_low')
        g.type('36.8')
        self.assertAlmostEqual(g.h.alarm_cfg['temperature']['low_k'], 0.2, places=9)
        assert g.tap('a_temperature_high')
        g.type('45.0')                                                          # 8 K above the set point: refused, the device's message
        self.assertEqual(g.overlay()['dialog'], 'limit_invalid')
        g.ov('OK')
        self.assertEqual(g.h.alarm_cfg['temperature']['high_k'], 1.0)
        assert g.tap('a_temperature_default')
        self.assertEqual((g.h.alarm_cfg['temperature']['low_k'], g.h.alarm_cfg['temperature']['high_k']), (0.5, 0.5))
        assert g.tap('a_temperature_on')
        self.assertFalse(g.h.alarm_cfg['temperature']['enabled'])
        g.tap('tb_back')
        self.assertEqual(g.text('al_temperature_on'), '')
        assert g.tap('al_door')
        assert g.form_row('a_door_form', 'door_signal')
        g.ov('60 s')
        self.assertEqual(g.h.alarm_cfg['door']['signal_s'], 60.0)
        DONE_TASKS.add('change an alarm limit')

    def test_silence_and_acknowledge_an_alarm_message(self):
        g = Rig()
        g.call(g.A, 'door.outer', open=True)
        g.step(40.0)
        self.assertIn('DOOR_OPEN', g.state('alarms.active'))
        w = g.widgets()
        self.assertIn('ib_ack', w)
        self.assertEqual(w['ib_text']['text'], 'Door longer open than 30 s')
        self.assertEqual(g.pixel(400, 1), [int(c * 0.8) for c in pu.ROLE_COLORS['red']])         # red information bar (brightness 80 %)
        self.assertTrue(g.state('alarm.sound'))
        assert g.tap('ib_mute')
        self.assertFalse(g.state('alarm.sound'))
        g.call(g.A, 'door.outer', open=False)
        g.step(5.0)
        self.assertNotIn('DOOR_OPEN', g.state('alarms.active'))
        self.assertIn('ib_ack', g.widgets())                                    # the message stays until acknowledged
        assert g.tap('ib_bell')
        self.assertEqual(g.page(), ('gui', 'msglog'))
        self.assertIn('Door longer open than 30 s', [c[2] for c in g.widgets()['ml_list']['cells']])
        n = len(g.state('messages.unacknowledged'))
        assert g.tap('ib_ack')
        self.assertEqual(len(g.state('messages.unacknowledged')), n - 1)
        while g.state('messages.unacknowledged'):
            assert g.tap('ib_ack')
        self.assertNotIn('ib_ack', g.widgets())
        self.assertIn('sb_right', g.widgets())                                  # the status bar is back
        DONE_TASKS.add('silence and acknowledge an alarm message')

    def test_high_temperature_disinfection(self):
        g = Rig(settle=600.0)
        assert g.tap('tb_disinfect')
        for _ in range(4):
            self.assertTrue(g.tap_text('Continue'))
        self.assertEqual(g.page(), ('gui', 'htd_5'))
        self.assertFalse(g.tap_text('Start Disinfection'))                      # cover not on the RH sensor: refused with the instruction
        self.assertEqual(g.overlay()['lines'], ['Put the cover on the humidity sensor.'])
        g.ov('OK')
        prepare_htd(g)
        assert g.tap_text('Start Disinfection')
        self.assertEqual(g.state('htd.phase'), 'heating')
        self.assertEqual(g.page(), ('htd', 'htd_run'))
        self.assertEqual(g.text('hr_phase'), 'Heating to 180 °C')
        while g.state('htd.phase') == 'heating':
            g.step(300.0)
        self.assertEqual(g.text('hr_phase'), 'Disinfection at 180 °C (2 h)')
        self.assertTrue(g.text('hr_rem').startswith('Estimated time remaining: '))
        g.step(14 * 3600.0)
        self.assertEqual(g.state('htd.last_result'), 'successful')
        self.assertEqual(g.page(), ('htd_done', 'htd_result'))
        self.assertEqual(g.text('hd_res'), 'Result: successful')
        assert g.tap('hd_export')
        g.ov('OK')
        assert g.tap_text('Continue')
        assert g.tap_text('Continue')
        assert g.tap_text('Confirm')
        self.assertEqual(g.page(), ('gui', 'home'))
        DONE_TASKS.add('start high-temperature disinfection')

    def test_export_the_event_log(self):
        g = Rig()
        g.menu('Events')
        self.assertEqual(g.page(), ('gui', 'events'))
        self.assertIn('Device switched on', [c[1] for c in g.widgets()['ev_list']['cells']])
        assert g.tap('ev_export')
        self.assertEqual(g.overlay()['title'], 'Export successful')
        g.ov('OK')
        self.assertEqual(g.h.events[-1]['code'], 'EXPORTED')
        assert g.tap('ev_filter')                                               # filter: only power events
        g.ov('[ ] Power')
        g.ov('Close')
        self.assertTrue(g.widgets()['ev_filter']['selected'])
        self.assertTrue(all(t in ('Device switched on',) for t in [c[1] for c in g.widgets()['ev_list']['cells']]))
        g.row('ev_list', g.widgets()['ev_list']['cells'][0][0])                  # detail window with arrows
        self.assertEqual(g.overlay()['dialog'], 'event_detail')
        DONE_TASKS.add('export the event log to USB')

    def test_lock_and_unlock_the_touch_screen(self):
        g = Rig()
        g.menu('Screen Lock')
        self.assertTrue(g.state('screen.locked'))
        self.assertEqual(g.page(), ('locked', 'locked'))
        self.assertFalse(g.tap('lk_unlock'))                                    # a short tap does nothing
        self.assertTrue(g.state('screen.locked'))
        assert g.tap('lk_unlock', hold=2.0)                                     # press and hold
        self.assertFalse(g.state('screen.locked'))
        self.assertEqual(g.page(), ('gui', 'home'))
        g.menu('Settings')                                                      # automatic screen lock after 1 min without a touch
        g.tap_text('Device Settings')
        g.tap_text('Screen Lock')
        g.form_row('sl_form', 'sl_auto')
        g.ov('I')
        g.form_row('sl_form', 'sl_timer')
        g.ov('1 min')
        g.step(59.0)
        self.assertFalse(g.state('screen.locked'))
        g.step(2.0)
        self.assertTrue(g.state('screen.locked'))
        self.assertEqual(g.page()[0], 'locked')
        assert g.tap('lk_unlock', hold=2.0)
        self.assertEqual(g.page(), ('gui', 'home'))
        DONE_TASKS.add('lock and unlock the touch screen')

    def test_performance_check_temperature(self):
        g = Rig(settle=3 * 3600.0)
        g.call(g.A, 'temperature.set', setpoint_k=310.65)                       # the user's set point (37.5 C) is restored afterwards
        g.step(1800.0)
        g.menu('Maintenance & Qualification')
        g.tap('mq_tasks')
        g.row('tk_list', 'Performance check')
        assert g.tap('tk_perform')
        self.assertEqual(g.page(), ('gui', 'pc_select'))
        assert g.tap('pc_sel_temperature')
        assert g.tap_text('Continue')
        g.form_row('pc2_form', 'pc_instrument')
        g.type('Probe A')
        assert g.tap_text('Continue')
        assert g.tap_text('Start Verification')
        self.assertAlmostEqual(g.state('setpoint.temperature_k'), 310.15, places=6)   # switched to the default 37 C
        self.assertFalse(g.tap('pcr_enter'))                                   # equilibration still running
        g.step(1800.0)
        assert g.tap('pcr_enter')
        g.form_row('pc4_form', 'pc_value')
        g.type('37.05')
        assert g.tap_text('Confirm')
        self.assertEqual(g.page(), ('gui', 'pc_result'))
        rec = [r for r in g.h.records.values() if r['kind'] == 'performance_check'][-1]
        self.assertEqual(rec['details']['instrument'], 'Probe A (calibrated 2026-01-01)')
        self.assertAlmostEqual(rec['details']['reference_temperature_k'], 310.2, places=6)
        self.assertEqual(g.text('pcs_b'), 'Reference: 37.05 °C')
        assert g.tap('pcs_ok')
        assert g.tap('pcx_ok')
        self.assertAlmostEqual(g.state('setpoint.temperature_k'), 310.65, places=6)   # reset to the previously used value
        self.assertEqual(g.page(), ('gui', 'home'))
        DONE_TASKS.add('performance check (temperature)')


# ---------------------------------------------------------------- the other menu functions
class MenuFunctions(unittest.TestCase):
    def test_user_management_rights_login_and_accounts(self):
        g = Rig()
        g.menu('Settings')
        g.tap_text('User Management')
        assert g.tap('um_switch')                                               # wizard: login mode, name, PIN twice
        g.ov('Continue')
        g.ov_row('um_admin')
        g.type('admin')
        g.ov('Continue')
        g.ov_row('um_pin')
        g.type('4711')
        g.ov_row('um_pin2')
        g.type('4711')
        g.ov('Continue')
        self.assertEqual(g.overlay()['title'], 'User Management successfully enabled')
        g.ov('OK')
        us = g.state('users.state')
        self.assertEqual((us['management'], us['logged_in'], us['role']), (True, 1, 'administrator'))
        g.menu('Users')
        assert g.tap('us_add')
        g.ov_row('nu_name')
        g.type('op')
        g.ov('Confirm')
        self.assertEqual(g.overlay()['title'], 'User credentials')
        pin = g.h.users['accounts'][2]['secret']
        self.assertIn('PIN/Password: ' + pin, g.overlay()['lines'])
        g.ov('OK')
        g.row('us_list', '2')
        g.form_row('ue_form', 'ue_role')
        g.ov('User')
        self.assertEqual(g.h.users['accounts'][2]['role'], 'user')
        g.menu('Logout')
        self.assertIsNone(g.state('users.state')['logged_in'])
        g.tap('tb_home')
        assert g.row('home_tiles', 'Temperature')                               # logged out: view only
        self.assertFalse(g.widgets()['fn_temperature_slider']['enabled'])
        g.menu('Login')
        g.form_row('lg_form', 'lg_id')
        g.type('2')
        g.form_row('lg_form', 'lg_pin')
        g.type('0000' if pin != '0000' else '1111')
        g.tap('lg_ok')
        self.assertEqual(g.overlay()['dialog'], 'login_failed')
        g.ov('OK')
        g.form_row('lg_form', 'lg_pin')
        g.type(pin)
        assert g.tap('lg_ok')
        us = g.state('users.state')
        self.assertEqual((us['logged_in'], us['role'], us['rights']['change_parameters'], us['rights']['change_alarm_limits']), (2, 'user', True, False))
        g.menu('Alarms')
        g.tap('al_temperature')
        self.assertFalse(g.tap('a_temperature_high'))                           # a user cannot change alarm limits
        g.row('home_tiles', 'Temperature') if g.tap('tb_home') else None
        assert g.tap('fn_temperature_plus')
        self.assertAlmostEqual(g.state('setpoint.temperature_k'), 310.25, places=6)
        g.step(601.0)                                                            # automatic logout after 10 min without a touch
        self.assertIsNone(g.state('users.state')['logged_in'])

    def test_recurring_tasks_edit_add_confirm_delete(self):
        g = Rig()
        g.menu('Settings')
        g.tap_text('Maintenance & Qualification (Recurring Tasks)')
        g.row('ts_list', 'Water refill')
        assert g.tap('ts_edit')
        g.ov('Continue')
        g.ov_row('tk_warn')
        g.ov('Off')
        g.ov('Continue')
        g.ov_row('tk_interval')
        g.ov('Weekly')
        g.ov('Finish')
        t = g.h.tasks['water_refill']
        self.assertEqual((t['warn'], t['interval_s']), (False, 7 * 86400.0))
        assert g.tap('ts_add')
        g.ov_row('tk_name')
        g.type('CO2 cylinder')
        g.ov('Next')
        g.ov('Continue')
        g.ov_row('tk_interval')
        g.ov('Daily')
        g.ov('Finish')
        self.assertEqual(g.h.tasks['custom_1']['name'], 'CO2 cylinder')
        g.step(86400.0)
        self.assertIn('custom_1', g.state('tasks.due'))
        self.assertEqual(g.text('ib_text'), 'Task due: custom_1')
        g.tap('tb_menu')
        g.tap_text('Maintenance & Qualification')
        g.tap('mq_tasks')
        g.row('tk_list', 'CO2 cylinder')
        assert g.tap('tk_perform')
        g.ov('Confirm')
        self.assertNotIn('custom_1', g.state('tasks.due'))
        g.row('tk_list', 'Routine maintenance by epServices')
        self.assertFalse(g.tap('tk_perform'))                                   # service technician only
        self.assertEqual(g.overlay()['dialog'], 'service_only')
        g.ov('OK')
        g.tap('tb_back')
        g.tap('tb_back')
        g.tap('tb_menu')
        g.tap_text('Settings')
        g.tap_text('Maintenance & Qualification (Recurring Tasks)')
        g.row('ts_list', 'CO2 cylinder')
        assert g.tap('ts_delete')
        g.ov('Delete')
        self.assertNotIn('custom_1', g.h.tasks)

    def test_operation_records_create_filter_detail_export(self):
        g = Rig()
        g.call(g.A, 'door.outer', open=True)
        g.step(40.0)
        g.call(g.A, 'door.outer', open=False)
        g.menu('Operation Records')
        assert g.tap('rec_add')
        g.ov_row('rec_name')
        g.type('Exp1')
        g.ov('Confirm')
        recs = g.call(g.A, 'operation_records.list')['records']
        self.assertEqual((recs[0]['name'], recs[0]['result']), ('Exp1', 'contains_alarm'))
        self.assertEqual(g.widgets()['rec_list']['cells'][0][2], 'Record contains alarm')
        g.tap('rec_filter')                                                     # 1.1.1: Set Filters form (Result, dates, name, user), Confirm
        g.ov_row('rf_filter')
        g.ov('Record contains error')
        g.ov('Confirm')
        self.assertEqual(g.widgets()['rec_list']['cells'], [])
        g.tap('rec_filter')
        g.ov('Reset Filters')
        g.row('rec_list', 'Exp1')
        self.assertEqual(g.overlay()['dialog'], 'record_detail')
        g.ov('Export')
        self.assertEqual(g.overlay()['title'], 'Export successful')

    def test_record_filters_dates_name_user(self):
        """Sync audit 2026-10-19 (R3): the From date, Until date, Operation name and User filters of 8.8.3 (p.99)."""
        g = Rig()
        for name, d0, d1 in (('Exp1', '2026-01-01', '2026-01-03'), ('Run2', '2026-02-01', '2026-02-05')):
            g.call(g.A, 'operation_record.create', name=name, start_date=d0, finish_date=d1)
        g.menu('Operation Records')
        names = lambda: [c[0] for c in g.widgets()['rec_list']['cells']]          # noqa: E731
        self.assertEqual(sorted(names()), ['Exp1', 'Run2'])
        g.tap('rec_filter')
        self.assertNotIn('row:rf_user', [b['id'] for b in g.overlay()['buttons']])  # User only with user management
        g.ov_row('rf_name')
        g.type('exp')
        g.ov('Confirm')
        self.assertEqual(names(), ['Exp1'])
        self.assertTrue(g.widgets()['rec_filter']['selected'])
        g.tap('rec_filter')
        g.ov('Reset Filters')
        g.tap('rec_filter')
        g.ov_row('rf_from')
        g.ov('+')                                                                   # the picker starts at 2026-01-01: day + 1
        g.ov('OK')
        g.ov('Confirm')
        self.assertEqual(g.h.panel.ui['vars']['rec_from'], '2026-01-02')
        self.assertEqual(names(), ['Run2'])                                         # Exp1 started before the From date
        g.h.panel.ui['vars'].update(rec_from=None, rec_until='2026-01-31')
        self.assertEqual(names(), ['Exp1'])
        g.h.panel.ui['vars'].update(rec_until=None, rec_user='Admin')
        self.assertEqual(names(), [])                                               # created without login: no user

    def test_exports_follow_the_selection(self):
        """Sync audit 2026-10-19 (R3): chart export = the selected time span and parameters (8.5.5), event export = the filtered log (8.4.4)."""
        g = Rig(settle=8 * 3600.0)
        seen = []
        orig = g.h._cmd_data_export
        g.h._cmd_data_export = lambda kind, **kw: seen.append((kind, kw, orig(kind, **kw))) or seen[-1][2]
        g.menu('Charts')
        g.tap('ch_span')
        g.ov('6 h')
        assert g.tap('ch_export')
        kind, kw, out = seen[-1]
        self.assertEqual((kind, kw['span_s'], kw['parameters']), ('chart', 6 * 3600.0, ['temperature', 'co2']))
        full = orig('chart')
        self.assertLess(out['rows'], full['rows'])
        self.assertEqual(out['rows'], sum(1 for r in g.h.chart if r['time_s'] >= g.h.t - 6 * 3600.0))
        self.assertLess(out['bytes'] / out['rows'], full['bytes'] / full['rows'])    # two of the four functions
        g.ov('OK')
        g.menu('Events')
        assert g.tap('ev_filter')
        g.ov('[ ] Power')
        g.ov('Close')
        assert g.tap('ev_export')
        kind, kw, out = seen[-1]
        self.assertEqual((kind, [c for c, on in kw['filters'].items() if on]), ('events', ['power']))
        self.assertEqual(out['rows'], sum(1 for e in g.h.events if g.h._event_cat(e) == 'power'))
        self.assertLess(out['rows'], len(g.h.events))
        r = g.call(g.A, 'data.export', expect='rejected', kind='events', span_s=3600.0)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')

    def test_charts_select_span_pick_and_export(self):
        g = Rig(settle=4 * 3600.0)
        g.menu('Charts')
        w = g.widgets()['ch_plot']
        self.assertEqual([s['label'] for s in w['series']], ['Temp', 'CO2'])
        g.tap('ch_fn')
        g.ov('Right: CO2 Concentration')
        g.ov('Relative Humidity')
        g.ov('Confirm')
        self.assertEqual([s['label'] for s in g.widgets()['ch_plot']['series']], ['Temp', 'RH'])
        g.tap('ch_span')
        g.ov('6 h')
        self.assertEqual(g.widgets()['ch_plot']['x_range'][1] - g.widgets()['ch_plot']['x_range'][0], 6.0)
        x, y, ww, hh = g.widgets()['ch_plot']['rect']
        assert g.touch(x + ww - 2, y + hh // 2)
        self.assertEqual(g.overlay()['dialog'], 'chart_values')
        self.assertTrue(g.overlay()['lines'][0].startswith('Temperature: 3'))
        g.ov('Close')
        assert g.tap('ch_export')

    def test_export_menu_selected_data(self):
        g = Rig()
        g.menu('Export')
        g.form_row('ex_form', 'ex_kinds')
        g.ov('[ ] Service data')
        g.ov('OK')
        assert g.tap('ex_go')
        self.assertEqual(g.overlay()['title'], 'Export successful')
        self.assertEqual([e['message_zh'][:22] for e in g.h.events if e['code'] == 'EXPORTED'][-3:],
                         [m[:22] for m in [f'CellXpert_chart_{int(g.h.t)}.csv导出到U盘', f'CellXpert_events_{int(g.h.t)}.csv导出到U盘',
                                           f'CellXpert_service_data_{int(g.h.t)}.zip导出到U盘']])

    def test_device_and_system_settings(self):
        g = Rig()
        g.menu('Settings')
        g.tap_text('About this CellXpert C170i')
        g.form_row('about_form', 'about_name')
        g.type('Incubator 3')
        self.assertEqual(g.state('device.settings')['about.name'], 'Incubator 3')
        self.assertTrue(g.text('sb_right').startswith('Incubator 3 | '))
        g.tap('tb_back')
        g.tap_text('System Settings')
        g.tap_text('Date & Time')
        g.form_row('dt_form', 'dt_zone')
        g.ov('Asia/Tokyo')
        self.assertEqual(g.state('device.clock')[11:13], '09')                   # 2026-01-01 00:10 UTC + 9 h
        g.form_row('dt_form', 'dt_date')
        self.assertEqual(g.overlay()['kind'], 'spinner')
        g.ov_id('inc:0')                                                        # day + 1
        g.ov('OK')
        self.assertEqual(g.state('device.clock')[:10], '2026-01-02')
        g.tap('tb_back')
        g.tap_text('Network')
        g.form_row('net_form', 'net_dhcp')
        g.ov('0')
        g.form_row('net_form', 'net_manual')
        g.form_row('netm_form', 'net_ip')
        g.type('10.0.0.7')
        self.assertEqual(g.state('device.settings')['network.ip'], '10.0.0.7')
        g.tap('tb_back')
        g.form_row('net_form', 'net_cert')
        self.assertEqual(g.overlay()['title'], 'Self Signed Certificate')
        g.ov('OK')
        self.assertIsNotNone(g.state('device.settings')['network.certificate'])
        g.tap('tb_home')
        g.menu('Settings')
        g.tap_text('Device Settings')
        g.tap_text('Acoustic Signals')
        assert g.tap('ac_alarms')
        self.assertEqual(g.state('device.settings')['acoustic.mode'], 'alarms')
        n = g.frame()['ui']['beeps']
        g.tap('ac_test_if')
        self.assertEqual(g.frame()['ui']['beeps'], n + 1)
        g.tap('tb_back')
        g.tap_text('Display Settings')
        x, y, ww, hh = g.widgets()['ds_brightness']['rect']
        g.touch(x + 1, y + hh // 2)                                             # 10 %
        self.assertEqual(g.state('device.settings')['display.brightness'], 10)
        self.assertEqual(g.frame()['displays'][SCR]['brightness'], 10)
        g.touch(x + ww - 1, y + hh // 2)
        g.tap('tb_back')
        g.tap_text('Home Screen Configuration')
        g.tap('hc_o2_on')                                                       # O2 off: the sensor is off and the tile goes
        self.assertEqual(g.state('device.settings')['home.functions'], ['temperature', 'co2', 'rh'])
        self.assertIsNone(g.state('sensor.o2_fraction'))
        g.tap('hc_rh_up')
        self.assertEqual(g.state('device.settings')['home.functions'], ['temperature', 'rh', 'co2'])
        g.tap('hc_o2_on')
        self.assertTrue(g.state('status.o2_initializing'))
        g.tap('tb_home')
        self.assertEqual([c[0] for c in g.widgets()['home_tiles']['cells']], ['Temperature', 'Relative Humidity', 'CO2 Concentration', 'O2 Concentration'])
        g.menu('Settings')
        g.tap_text('Device Settings')
        g.tap_text('Relay Settings')
        g.form_row('relay_form', 'relay_ch4')
        g.ov('Any alarm/Warning/Error')
        self.assertEqual(g.state('device.settings')['relay.ch4'], 'any')
        assert g.tap('relay_test')
        self.assertEqual(g.state('bms.relays'), [True, True, True, True])
        g.step(16.0)
        self.assertNotEqual(g.state('bms.relays'), [True, True, True, True])
        g.tap('tb_back')
        g.tap_text('Gas System')
        g.form_row('gas_form', 'gas_monitor')
        g.ov('O')
        self.assertFalse(g.state('gas.pressure_monitor_enabled'))
        g.tap('tb_back')
        g.tap_text('Operation Records Settings')
        g.form_row('recset_form', 'rec_company')
        g.type('ACME Lab')
        self.assertEqual(g.state('device.settings')['records.company'], 'ACME Lab')

    def test_contamination_status(self):
        g = Rig()
        g.menu('Maintenance & Qualification')
        assert g.tap('mq_contam')
        g.ov('Contaminated')
        g.ov('Confirm')
        self.assertEqual(g.state('contamination.status'), 'contaminated')
        self.assertEqual(g.text('mq_contam'), 'This device is contaminated')
        self.assertEqual(g.text('sb_status'), 'Contaminated')
        g.tap('mq_contam')
        g.ov('Not Contaminated')
        g.ov('Confirm')
        self.assertEqual(g.text('sb_status'), 'Not Contaminated')               # shown for 24 h
        g.step(86401.0)
        g.tap('ib_ack') if 'ib_ack' in g.widgets() else None
        while g.state('messages.unacknowledged'):
            g.tap('ib_ack')
        self.assertEqual(g.text('sb_status'), '')

    def test_gas_control_failure_restart(self):
        g = Rig(settle=3 * 3600.0)
        g.call(g.A, 'gas.pressure_monitor', enabled=False)
        g.call(g.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, co2_supply_pa=0.0))
        for cap in ('door.outer', 'door.latch', 'door.inner'):
            g.call(g.A, cap, open=True)
            g.step(2.0)
        g.step(20.0)
        for cap in ('door.inner', 'door.latch', 'door.outer'):
            g.call(g.A, cap, open=False)
            g.step(2.0)
        g.step(2 * 3600.0)
        self.assertTrue(g.state('status.co2_control_failed'))
        g.row('home_tiles', 'CO2 Concentration')
        self.assertIn('fn_co2_failed', g.widgets())
        self.assertTrue(g.text('fn_co2_red').startswith('Set point 5.0 %'))
        assert g.tap('fn_co2_failed')
        self.assertFalse(g.state('status.co2_control_failed'))
        self.assertNotIn('fn_co2_failed', g.widgets())


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def test_panel_sequence_equals_direct_calls(self):
        a, b = Rig(settle=2400.0), Rig(settle=2400.0)
        a.row('home_tiles', 'Temperature')
        a.tap('fn_temperature_value')
        a.type('37.5')
        a.tap('tab_temperature_co2')
        a.tap('fn_co2_plus')
        a.tap('tb_menu')
        a.tap_text('Alarms')
        a.tap('al_co2')
        a.tap('a_co2_high')
        a.type('6.0')
        a.tap('tb_menu')
        a.tap_text('Settings')
        a.tap_text('Device Settings')
        a.tap_text('Relay Settings')
        a.form_row('relay_form', 'relay_ch1')
        a.ov('Warnings')
        b.call(b.A, 'temperature.set', setpoint_k=310.65)
        b.call(b.A, 'co2.set', setpoint_fraction=0.051)
        b.call(b.A, 'alarm.configure', kind='co2', enabled=True, high=0.009)
        b.call(b.A, 'settings.configure', settings={'relay.ch1': 'warnings'})
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.step(3600.0)
        b.step(3600.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


# ---------------------------------------------------------------- rules, refusals
class Rules(unittest.TestCase):
    def test_power_off_gate_and_switch(self):
        g = Rig(on=False)
        self.assertEqual(g.page()[0], 'off')
        self.assertFalse(g.touch(400, 240))
        assert g.call(g.A, 'panel.switch', input_id='switch_mains', position='on')['accepted']
        self.assertTrue(g.state('power.enabled'))
        self.assertEqual(g.page(), ('gui', 'home'))
        assert g.call(g.A, 'panel.switch', input_id='switch_mains', position='off')['accepted']
        self.assertEqual(g.page()[0], 'off')
        self.assertEqual(g.h.panel.render(SCR).max(), 0)                        # dark screen

    def test_unknown_gestures_and_evaluator_frame(self):
        g = Rig()
        r = g.call(g.A, 'panel.touch', expect='rejected', display_id=SCR, x=900, y=10)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.A.invoke(instance_id=INST, capability_id='panel.frame', arguments={}, request_id='f1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')

    def test_htd_screen_follows_an_api_start_and_door_abort(self):
        g = Rig(settle=600.0)
        prepare_htd(g)
        g.call(g.A, 'htd.start')
        self.assertEqual(g.page(), ('htd', 'htd_run'))
        self.assertFalse(g.tap('tb_menu') if 'tb_menu' in g.widgets() else False)
        g.step(600.0)
        g.call(g.A, 'door.outer', open=True)
        g.step(2.0)
        self.assertEqual(g.state('htd.last_result'), 'failed')
        self.assertEqual(g.page(), ('htd_done', 'htd_result'))
        self.assertEqual(g.text('hd_err'), '6731FW.753 Door opened during HTD')


# ---------------------------------------------------------------- sweep 10-19 (1.2.0)
class SweepFixes(unittest.TestCase):
    """Final consistency sweep (2026-10-19): navigation is free during another agent's HTD and only the invoked command is refused (with a dialog);
    the device errors of manual chapter 11 by evaluator fault.inject; Enable remote access gates the agent's direct commands."""

    def test_another_agents_htd_only_locks_what_the_device_locks(self):
        g = Rig(settle=600.0)
        prepare_htd(g)
        op = g.call(g.A, 'htd.start')['operation_id']
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertEqual(g.page(), ('htd', 'htd_run'))
        before = g.state('htd.phase')
        r = g.call(B, 'panel.touch', display_id=SCR, x=400, y=150)                     # B looks at the screen: never refused
        self.assertEqual(r, {'accepted': False})                                        # (a label: nothing to do)
        self.assertEqual(g.call(B, 'panel.capture', display_id=SCR)['display_id'], SCR)
        w = g.widgets()['hr_export']
        x, y, ww, hh = w['rect']
        self.assertTrue(g.call(B, 'panel.touch', display_id=SCR, x=x + ww // 2, y=y + hh // 2)['accepted'])   # Export does not change the HTD
        self.assertEqual(g.overlay()['title'], 'Export successful')
        g.ov('OK')
        for cap, args in (('temperature.set', dict(setpoint_k=310.15)), ('htd.start', {})):     # locked by the device itself during HTD
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'HTD_RUNNING', cap)
        with self.assertRaises(rt.ActionRejected):                                     # only the owner can poll / cancel the HTD
            B.cancel(op, 'b-cancel')
        self.assertEqual((g.state('power.enabled'), g.state('htd.phase')), (True, before))
        self.assertTrue(g.call(B, 'panel.switch', input_id='switch_mains', position='off')['accepted'])   # the mains switch is physical: never refused
        self.assertEqual(g.state('htd.last_result'), 'failed')                         # the HTD is interrupted as by a power loss (6731FW.751)

    def test_device_errors_need_a_restart(self):
        g = Rig(settle=3600.0)
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', fault='co2_sensor_init_failed', active=True)['error']['code'], 'ACCESS_DENIED')
        g.call(g.E, 'fault.inject', fault='co2_sensor_init_failed', active=True)
        g.step(2.0)
        self.assertIn('E_6731FW203', g.state('alarms.active'))
        self.assertIsNone(g.state('sensor.co2_fraction'))
        self.assertEqual(g.h.u['c'], 0.0)
        self.assertEqual(g.h.panel_read()['host.message_text'], 'Sensor error: CO2 (6731FW.203)')   # information bar
        g.call(g.E, 'fault.inject', fault='co2_sensor_init_failed', active=False)          # the cause is gone: the error stays until a restart
        g.step(5.0)
        self.assertIn('E_6731FW203', g.state('alarms.active'))
        assert g.switch('off')
        g.step(10.0)
        assert g.switch('on')
        g.step(2.0)
        self.assertNotIn('E_6731FW203', g.state('alarms.active'))
        g.call(g.E, 'fault.inject', fault='o2_control_software_error', active=True)        # the cause remains: back after the restart
        g.step(2.0)
        self.assertIn('E_6731FW313', g.state('alarms.active'))
        assert g.switch('off')
        g.step(10.0)
        assert g.switch('on')
        g.step(2.0)
        self.assertIn('E_6731FW313', g.state('alarms.active'))
        for f, code in (('water_level_over_range', 'E_6731FW404H'), ('water_level_sensor_error', 'E_6731FW454'), ('rh_sensor_init_failed', 'E_6731FW403')):
            g.call(g.E, 'fault.inject', fault=f, active=True)
            g.step(2.0)
            self.assertIn(code, g.state('alarms.active'), f)
        self.assertIsNone(g.state('sensor.water_low'))
        self.assertIsNone(g.state('sensor.rh'))

    def test_temperature_sensor_out_of_range_follows_its_cause(self):
        g = Rig(settle=3600.0)
        g.call(g.E, 'fault.inject', fault='temp_sensor_out_of_range', active=True)
        g.step(2.0)
        self.assertIn('E_6731FW107', g.state('alarms.active'))
        self.assertIsNone(g.state('sensor.temperature_k'))
        self.assertEqual(g.h.u['h'], 0.0)                                                  # no valid reading: no heating
        g.call(g.E, 'fault.inject', fault='temp_sensor_out_of_range', active=False)
        g.step(2.0)
        self.assertNotIn('E_6731FW107', g.state('alarms.active'))
        self.assertIsNotNone(g.state('sensor.temperature_k'))

    def test_fatal_error_red_screen(self):
        g = Rig(settle=600.0)
        g.call(g.E, 'fault.inject', fault='fatal_error', active=True)
        g.step(2.0)
        self.assertEqual(g.page(), ('fatal', 'fatal'))
        self.assertEqual(g.text('fe_t'), 'Fatal Error - Restart the device')
        px = g.pixel(20, 20)
        self.assertTrue(px[0] > 3 * px[1] and px[1] == px[2], px)                          # red (dimmed by the brightness setting)
        self.assertTrue(g.state('status.fatal_error'))
        self.assertFalse(g.touch(400, 210))                                               # the red screen takes no touches
        self.assertEqual((g.h.u['h'], g.h.u['c']), (0.0, 0.0))
        OUT.mkdir(parents=True, exist_ok=True)
        g.h.panel.save_png(SCR, OUT / 'fault.png')
        g.call(g.E, 'fault.inject', fault='fatal_error', active=False)
        assert g.switch('off')
        g.step(10.0)
        assert g.switch('on')
        g.step(2.0)
        self.assertEqual(g.page()[0], 'gui')
        self.assertFalse(g.state('status.fatal_error'))

    def test_enable_remote_access_gates_the_agent_commands(self):
        g = Rig(settle=600.0)
        self.assertTrue(g.state('device.settings')['network.remote_access'])               # on in the simulated configuration
        g.call(g.A, 'temperature.set', setpoint_k=310.65)
        g.menu('Settings')
        g.tap_text('System Settings')
        g.tap_text('Network')
        g.form_row('net_form', 'net_remote')
        g.ov('0')
        self.assertFalse(g.state('device.settings')['network.remote_access'])
        for cap, args in (('temperature.set', dict(setpoint_k=311.15)), ('co2.set', dict(setpoint_fraction=0.06)), ('alarm.mute', {}),
                          ('settings.configure', dict(settings={'network.remote_access': True}))):
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'REMOTE_DISABLED', cap)
        self.assertAlmostEqual(g.state('setpoint.temperature_k'), 310.65)
        self.assertIn('records', g.call(g.A, 'records.list'))                               # reads stay allowed
        g.call(g.A, 'door.outer', open=True)                                                # physical handling stays allowed
        g.call(g.A, 'door.outer', open=False)
        g.call(g.E, 'temperature.set', setpoint_k=311.15)                                   # the evaluator is not the remote interface
        g.form_row('net_form', 'net_remote')                                                # switched on again on the touch screen
        g.ov('I')
        g.call(g.A, 'temperature.set', setpoint_k=310.15)
        self.assertAlmostEqual(g.state('setpoint.temperature_k'), 310.15)


# ---------------------------------------------------------------- renders and contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(settle=3 * 3600.0), Rig(settle=3 * 3600.0)
        ia, ib = a.h.panel.render(SCR), b.h.panel.render(SCR)
        self.assertEqual((ia.dtype.name, ia.shape), ('uint8', (480, 800, 3)))
        self.assertTrue((ia == ib).all())
        self.assertEqual(a.h.panel.png_bytes(SCR), b.h.panel.png_bytes(SCR))
        OUT.mkdir(parents=True, exist_ok=True)
        while a.state('messages.unacknowledged'):
            a.tap('ib_ack')
        shots = {'home': a.h.panel.save_png(SCR, OUT / 'home.png')}
        a.tap('tb_menu')
        shots['menu'] = a.h.panel.save_png(SCR, OUT / 'menu.png')
        a.tap_text('Alarms')
        a.tap('al_temperature')
        a.tap('a_temperature_high')
        a.type('45.0')
        self.assertEqual(a.overlay()['dialog'], 'limit_invalid')
        shots['error'] = a.h.panel.save_png(SCR, OUT / 'error.png')
        a.ov('OK')
        a.tap('tb_home')
        a.row('home_tiles', 'Temperature')
        shots['function'] = a.h.panel.save_png(SCR, OUT / 'function.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        self.assertEqual((r['image']['width'], r['image']['height']), (800, 480))

    def test_spec_and_contract(self):
        meta = rt.export_eppendorf_cellxpertc170i_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, rt.DEVICE_CAPS), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.touch', 'panel.switch', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in list(rt.PANEL_CAPS) + list(rt._DEVICE_FUNCTIONS):
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'preconditions', 'errors'):
                self.assertEqual(c['capabilities'][k][key], meta['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((rt.ASSET_DIR / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('cellxpert_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
