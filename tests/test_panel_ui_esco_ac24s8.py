"""front-panel tests for CHEM-193 esco_ac24s8 (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_esco_ac24s8.py -q

Every gesture goes through the session API (panel.press / panel.switch).  Each check looks at the runtime state and at the screen: the structured
frame (4-line LCD, LEDs) and the rendered pixels of the LEDs.  Themes: the seven procedures of the menu survey (start, shut down with UV, motorized
sash with the PIN from closed, set the date, maintenance mode with the ADMIN PIN, mute the sash alarm, diagnostics); every menu item (SETTINGS,
SET MODE, FIELD CALIB, ADMIN SETTINGS) done only with keys; warm-up and standby restrictions; panel = direct capability; refusals, wrong PIN,
power cut; render determinism and the PNGs of outputs/panel_ui/esco_ac24s8/; contract entries of the panel capabilities."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import esco_ac24s8 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'esco_ac24s8_01'
OUT = ROOT / 'outputs/panel_ui/esco_ac24s8'
SPEC = rt.PANEL_SPEC
LEDS = ['alarm_led', 'fan_led', 'light_led', 'uv_led']
SURVEY_TASKS = ['Start the cabinet', 'Shut down and UV-decontaminate', 'Move the motorized sash', 'Set the date', 'Put the cabinet in maintenance mode',
                'Mute the sash-open alarm', 'View diagnostics']
DONE_TASKS = set()


class Rig:
    def __init__(self, seed=1, **plant):
        self.h = rt.EscoAc24s8Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        before = self.h.read('evaluator')
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            if cap != 'sim.step':
                assert self.h.t == t0, cap
                after = self.h.read('evaluator')
                changed = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)}
                assert not changed - set(rt.EFFECTS.get(cap, [])), (cap, changed - set(rt.EFFECTS.get(cap, [])))
        return r['result'] if expect == 'succeeded' else r

    def press(self, key, d=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=d)['accepted']

    def presses(self, key, n):
        for _ in range(n):
            assert self.press(key), key

    def switch(self, name, pos):
        return self.call(self.A, 'panel.switch', input_id=name, position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lcd(self):
        return self.frame()['displays']['lcd']['lines']

    def leds(self):
        """LEDs lit now: frame and pixels must agree."""
        f = self.frame()['displays']
        on = {k for k in LEDS if f[k]['shown']}
        px = {k for k in LEDS if [int(v) for v in self.h.panel.render(k)[12, 12]] == SPEC['displays'][k]['colors']['on']}
        assert on == px, (on, px)
        return on

    def screen(self):
        return self.frame()['screen']

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def state(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    # ---- procedures
    def key_on(self):
        assert self.switch('key_switch', 'on')

    def sash(self, key, hold=8.0):
        assert self.press(key, hold)
        self.step(hold + 2.0)

    def working(self):
        self.key_on()
        self.sash('up_button')
        assert self.press('fan_button')
        self.step(200.0)
        assert self.state('fan.state') == 'on'

    def pin(self, digits):
        """PIN digit by digit: up / down change the flashing digit, SET moves on (manual p.15, p.31)."""
        assert self.screen() == 'pin', self.screen()
        for d in digits:
            d = int(d)
            if d <= 5:
                self.presses('up_button', d)
            else:
                self.presses('down_button', 10 - d)
            self.press('set_button')

    def enter_menu(self, digits='0000'):
        assert self.press('menu_button')
        self.pin(digits)

    def cursor(self):
        return (self.frame()['ui']['menu'] or {}).get('label')

    def go(self, label):
        """Cursor to the list item of this label (from the top; the frame names the item under the cursor), then SET."""
        self.presses('up_button', 16)
        for _ in range(16):
            if self.cursor() == label:
                assert self.press('set_button')
                return
            assert self.press('down_button')
        raise AssertionError((label, self.lcd()))


def runtime_state(h):
    r = h.read('evaluator')
    r.pop('clock.time_s')
    return json.dumps(dict(read=r, pins=h.pins, menu=h.menu, events=[(e['time_s'], e['code']) for e in h.events]), sort_keys=True, default=str)


class KeyProcedures(unittest.TestCase):
    def test_start_and_shut_down_with_uv(self):
        g = Rig()
        g.key_on()
        self.assertEqual(g.lcd()[:2], ['UV MODE                ' [:19] + '00:00', 'Sash: CLOSED'])
        g.sash('up_button')                                                      # READY: the lamp turns on at the safe height
        self.assertEqual((g.lcd()[0][:5], g.lcd()[1]), ('READY', 'Sash: OK'))
        self.assertEqual(g.leds(), {'light_led'})
        self.assertTrue(g.press('fan_button'))
        self.assertEqual((g.lcd()[0][:7], g.lcd()[2]), ('WARM UP', 'Remaining 03:00'))
        self.assertFalse(g.press('socket_button'))                               # all buttons but FAN, LIGHT and MENU disabled in warm-up
        self.assertTrue(g.press('lamp_button'))
        self.assertTrue(g.press('lamp_button'))
        g.step(200.0)
        self.assertEqual(g.lcd()[2:], ['I:0.48 m/s D:0.35 m/s', 'AIRFLOW: OK'])
        self.assertEqual(g.leds(), {'fan_led', 'light_led'})
        DONE_TASKS.add('Start the cabinet')
        g.call(g.A, 'settings.configure', post_purge_s=60.0)
        self.assertTrue(g.press('fan_button'))
        self.assertEqual((g.lcd()[0][:10], g.lcd()[2]), ('POST PURGE', 'Remaining 01:00'))
        g.sash('down_button')                                                    # the sash can be lowered during the post purge
        self.assertEqual(g.state('sash.status'), 'closed')
        self.assertFalse(g.press('uv_button'))                                   # UV only after the post purge
        g.step(60.0)
        self.assertEqual((g.lcd()[0][:7], g.lcd()[1]), ('UV MODE', 'Sash: CLOSED'))
        self.assertTrue(g.press('uv_button'))
        self.assertEqual((g.lcd()[0][:5], g.lcd()[2]), ('UV ON', 'UV TIMER 60:00'))
        self.assertEqual(g.leds(), {'uv_led'})
        DONE_TASKS.add('Shut down and UV-decontaminate')

    def test_motorized_sash_and_fan_pin(self):
        g = Rig()
        g.key_on()
        g.call(g.A, 'menu.enter', pin='0009')
        g.call(g.A, 'pin.set', which='fan', pin='1234')
        g.call(g.A, 'menu.exit')
        self.assertTrue(g.press('up_button', 8.0))                               # from fully closed the password is asked first
        self.assertEqual((g.screen(), g.lcd()[:2]), ('pin', ['ENTER PIN', '0000']))
        g.pin('1111')
        self.assertEqual((g.screen(), g.lcd()[3]), ('home', 'WRONG PIN'))
        g.step(10.0)
        self.assertEqual(g.state('sash.status'), 'closed')
        self.assertTrue(g.press('up_button'))
        g.pin('1234')
        g.step(10.0)
        self.assertEqual(g.state('sash.status'), 'safe')
        self.assertTrue(g.press('down_button', 1.0))                             # released mid-way: lights off, sash alarm
        g.step(5.0)
        self.assertEqual((g.lcd()[0][:10], g.lcd()[1]), ('SASH ALARM', 'Sash: NO!'))
        self.assertFalse(g.state('lamp.on'))
        g.sash('up_button')
        self.assertEqual(g.state('sash.status'), 'safe')
        g.sash('up_button')
        self.assertEqual((g.state('sash.status'), g.lcd()[0][:10]), ('full_open', 'FULLY OPEN'))
        self.assertFalse(g.press('up_button'))                                   # at the top stop
        self.assertTrue(g.press('fan_button'))                                   # FAN asks the FAN PIN
        self.assertEqual(g.screen(), 'pin')
        g.pin('1234')
        self.assertEqual(g.state('fan.state'), 'warm_up')
        DONE_TASKS.add('Move the motorized sash')

    def test_set_the_date(self):
        g = Rig()
        g.key_on()
        g.enter_menu('0000')
        self.assertEqual((g.screen(), g.lcd()[0][:12], g.lcd()[1]), ('menu', 'MENU OPTIONS', '>SETTINGS'))
        self.assertEqual(g.lcd()[2:], [' SET MODE', ''])                         # FAN PIN: no FIELD CALIB / ADMIN SETTINGS
        self.assertTrue(g.state('alarm.audible'))                                # the buzzer sounds on entry
        g.step(3.0)
        self.assertFalse(g.state('alarm.audible'))
        g.go('SETTINGS')
        g.go('SET DATE')
        self.assertEqual(g.lcd(), ['SET DATE', 'YYYY 2026', 'MM 01', 'DD 01'])
        g.press('up_button')
        g.press('set_button')
        g.presses('up_button', 2)
        g.press('set_button')
        g.presses('down_button', 2)                                              # 01 -> 31 (wrap) -> 30
        g.press('set_button')
        self.assertEqual(g.lcd()[3], 'DATE SET')
        self.assertTrue(g.state('device.clock').startswith('2027-03-30T'))
        g.press('menu_button')
        g.press('menu_button')
        self.assertEqual((g.screen(), g.state('menu.state')['open']), ('home', False))
        g.enter_menu('0000')
        g.go('SETTINGS')
        g.go('SET DATE')
        g.press('set_button')
        g.presses('down_button', 1)                                              # 2027-02-30 does not exist
        g.press('set_button')
        g.press('set_button')
        self.assertEqual(g.lcd()[3], 'INVALID DATE')
        self.assertTrue(g.state('device.clock').startswith('2027-03-30T'))
        DONE_TASKS.add('Set the date')

    def test_maintenance_mode_with_admin_pin(self):
        g = Rig()
        g.working()
        assert g.press('menu_button')
        g.press('set_button')                                                    # SET with 0, 00, 000 flashing, then down to 9, SET
        g.press('set_button')
        g.press('set_button')
        g.press('down_button')
        self.assertEqual(g.lcd()[1], '0009')
        g.press('set_button')
        self.assertEqual(g.lcd()[1:], ['>SETTINGS', ' SET MODE', ' FIELD CALIB'])
        g.step(3.0)
        g.go('SET MODE')
        self.assertEqual(g.lcd()[1:], ['>NORMAL MODE           *', ' QUICKSTART MODE', ' MAINTENANCE MODE'])
        g.go('MAINTENANCE MODE')
        g.press('menu_button')
        g.press('menu_button')
        f = g.frame()['displays']['lcd']
        self.assertEqual((f['lines'][0][:11], f['blink'][0]), ('MAINTENANCE', True))   # blinks to show the unsafe condition
        g.sash('up_button')                                                      # fully open: no alarm in maintenance mode
        self.assertEqual((g.state('alarms.active'), 'alarm_led' in g.leds()), ([], False))
        self.assertTrue(g.press('menu_button'))                                  # no PIN in maintenance mode
        self.assertEqual(g.screen(), 'menu')
        g.go('SET MODE')
        g.go('NORMAL MODE')
        g.press('menu_button')
        g.press('menu_button')
        self.assertIn('SASH_ALARM', g.state('alarms.active'))
        DONE_TASKS.add('Put the cabinet in maintenance mode')

    def test_mute_the_sash_open_alarm(self):
        g = Rig()
        g.working()
        g.sash('up_button')
        self.assertTrue(g.state('alarm.audible'))
        self.assertIn('alarm_led', g.leds())
        self.assertTrue(g.press('set_button'))
        self.assertFalse(g.state('alarm.audible'))
        self.assertEqual(g.screen(), 'home')
        g.step(31.0)                                                             # sounds again after the mute duration (30 s)
        self.assertTrue(g.state('alarm.audible'))
        DONE_TASKS.add('Mute the sash-open alarm')

    def test_diagnostics(self):
        g = Rig()
        g.working()
        seen = {}
        self.assertTrue(g.press('set_button'))
        for _ in range(15):
            lines = g.lcd()
            self.assertEqual(lines[0], 'DIAGNOSTIC')
            seen[lines[1]] = lines[2]
            g.press('set_button')
        self.assertEqual(g.screen(), 'home')
        self.assertEqual(len(seen), 15)
        self.assertEqual((seen['MODE'], seen['VERSION'], seen['TYPE']), ('NORMAL MODE', 'CP104D V 1.0', 'AC2-4S8'))
        self.assertEqual(seen['TEMPERATURE'], f"{g.state('cabinet.temperature_k') - 273.15:.1f} C")
        self.assertEqual((seen['M_SWITCH1'], seen['M_SWITCH2'], seen['M_SWITCH3']), ('OFF', 'ON', 'OFF'))
        self.assertEqual((seen['UV TIMER'], seen['MUTE TIMER'], seen['FILTER LIFE']), ('60 MIN', '30 SEC', '99% OF 10000 H'))
        self.assertEqual(seen['ADC IFF..ADC TEMP'], 'SERVICE DATA')
        DONE_TASKS.add('View diagnostics')


class Menus(unittest.TestCase):
    def test_settings_items(self):
        g = Rig()
        g.working()
        g.enter_menu('0000')
        g.go('SETTINGS')
        g.go('UV TIMER')
        self.assertEqual(g.lcd()[:2], ['UV TIMER', '60 MIN'])
        g.presses('down_button', 30)
        g.press('set_button')
        g.go('MEASUREMENT UNIT')
        g.press('down_button')
        g.press('set_button')
        g.go('WARM UP TIME')
        g.presses('up_button', 2)
        g.press('set_button')
        g.go('POST PURGE TIME')
        g.presses('up_button', 3)
        g.press('set_button')
        g.go('DATA LOG PERIOD')
        g.press('up_button')
        g.press('set_button')
        g.go('SET CLOCK')
        self.assertEqual(g.lcd()[1:3], ['HH 00', 'MM 03'])
        g.presses('up_button', 8)
        g.press('set_button')
        g.presses('up_button', 27)
        g.press('set_button')
        self.assertEqual(g.lcd()[3], 'TIME SET')
        s = g.state('settings.current')
        self.assertEqual((s['uv_timer_s'], s['warm_up_s'], s['post_purge_s'], s['data_log_period_s']), (1800.0, 300.0, 180.0, 70.0))
        self.assertTrue(g.state('device.clock')[11:16] == '08:30')
        self.assertEqual(g.state('menu.state')['unit'], 'imperial')
        g.press('menu_button')
        g.press('menu_button')
        g.step(5.0)
        self.assertEqual(g.lcd()[0][-5:], '08:30')
        inflow, down = g.state('display.inflow_m_s'), g.state('display.downflow_m_s')
        self.assertEqual(g.lcd()[2], f'I:{inflow / 0.00508:.0f} fpm D:{down / 0.00508:.0f} fpm')

    def test_admin_settings(self):
        g = Rig(filter_hours_h=10000.0, uv_hours_h=10.0)
        g.working()
        self.assertEqual(g.lcd()[3], 'FILTER LIFE 0%')
        g.enter_menu('0009')
        g.go('ADMIN SETTINGS')
        g.go('RESET F/H/M')
        self.assertEqual(g.lcd()[3], 'F/H/M RESET')
        g.go('RESET UV/H/M')
        g.go('RESET B/H/M')
        m = g.state('meters')
        self.assertEqual((m['filter_h'], m['uv_h'], m['blower_h']), (0.0, 0.0, 0.0))
        g.go('NEW FAN PIN')
        g.pin_digits = None
        for d in '4321':
            g.presses('up_button', int(d))
            g.press('set_button')
        self.assertEqual(g.lcd()[3], 'NEW PIN SET')
        g.go('NEW ADMIN PIN')
        for d in '0019':
            g.presses('up_button', int(d)) if int(d) <= 5 else g.presses('down_button', 10 - int(d))
            g.press('set_button')
        g.go('AIRFLOW MONITOR')
        g.press('down_button')
        g.press('set_button')
        self.assertFalse(g.state('settings.current')['airflow_monitor'])
        g.go('AIRFLOW MONITOR')
        g.press('up_button')
        g.press('set_button')
        g.go('FILTER LIFE DISPLAY')
        g.press('up_button')
        g.press('set_button')
        g.go('AIRFLOW OUT TEMP')
        g.press('up_button')
        g.press('set_button')
        g.go('ALARM MUTE DURATION')
        g.presses('up_button', 30)
        g.press('set_button')
        g.go('SET SASH CYCLE')
        self.assertEqual(g.lcd()[1], '00001')                                     # one sash cycle so far (raised to the safe height)
        g.press('up_button')
        g.press('set_button')
        g.presses('up_button', 5)
        for _ in range(3):
            g.press('set_button')
        g.press('down_button')
        g.press('set_button')
        self.assertEqual(g.state('meters')['sash_cycles'], 15000)
        g.go('DATE CERTIFIED')
        g.press('down_button')                                                   # 2025-01-01: expired a year later (clock 2026-01-01)
        for _ in range(3):
            g.press('set_button')
        g.go('SET LANGUAGE')
        g.press('up_button')
        g.press('set_button')
        st = g.state('menu.state')
        self.assertEqual((st['fan_pin_set'], st['filter_life_display'], st['airflow_out_temp'], st['language'], st['date_certified']),
                         (True, True, True, 'german', '2025-01-01'))
        self.assertEqual(g.state('settings.current')['mute_s'], 60.0)
        g.press('menu_button')
        g.press('menu_button')
        g.step(2.5)                                                              # the DATE SET message has gone
        self.assertEqual(g.lcd()[0][:6], 'BEREIT')                                # German main display
        self.assertEqual(g.lcd()[1], 'Fenster: OK')
        self.assertEqual(set(g.state('alarms.active')), {'REPLACE_SASH_MOTOR', 'RECERTIFICATION'})
        self.assertEqual(g.lcd()[3], 'Replace Sash Motor')
        self.assertTrue(g.press('menu_button'))
        g.pin('0019')                                                            # the new ADMIN PIN
        g.go('ADMIN SETTINGS')
        g.go('SET SASH CYCLE')
        g.press('down_button')
        g.press('set_button')
        g.presses('down_button', 5)
        for _ in range(4):
            g.press('set_button')
        self.assertEqual(g.state('meters')['sash_cycles'], 0)
        g.go('RESET DEFAULT')
        self.assertEqual(g.lcd()[3], 'DEFAULT SET')
        g.press('menu_button')
        g.press('menu_button')
        g.step(2.5)
        lines = set()
        for _ in range(2):                                                       # Call Service for / re-certification alternate every 2 s
            lines.add(g.lcd()[3])
            g.step(2.0)
        self.assertEqual(lines, {'Call Service for', 're-certification'})
        st = g.state('menu.state')
        self.assertEqual((st['fan_pin_set'], st['filter_life_display'], st['language']), (False, False, 'german'))   # language is not reset (2.1.4.9)
        self.assertEqual(g.call(g.A, 'pin.check', pin='0009')['level'], 'admin')

    def test_field_calibration_with_keys(self):
        g = Rig()
        g.working()
        g.call(g.E, 'fault.inject', fault='airflow_sensor_uncalibrated', active=True)
        g.step(2.0)
        self.assertEqual(g.lcd()[3], 'AIRFLOW: NO!')
        g.enter_menu('0009')
        g.go('FIELD CALIB')
        g.go('AIRFLOW CALIBRATION')
        shown = g.lcd()[1]
        self.assertTrue(shown.startswith('INFLOW 0.6'), shown)                   # the uncalibrated display reads high
        for _ in range(80):
            if g.lcd()[1] == 'INFLOW 0.45 m/s':
                break
            g.press('down_button')
        g.press('set_button')
        self.assertEqual(g.lcd()[3], 'CALIBRATION DONE')
        g.go('AIRFLOW CALIBRATION')
        g.presses('up_button', 60)
        g.press('set_button')
        self.assertEqual(g.lcd()[3], 'CALIBRATION ERROR')
        g.press('menu_button')
        g.press('menu_button')
        g.step(5.0)
        self.assertAlmostEqual(g.state('display.inflow_m_s'), 0.45, delta=0.02)
        self.assertEqual(g.lcd()[3], 'AIRFLOW: OK')
        g.enter_menu('0009')
        g.go('FIELD CALIB')
        g.go('RESET CALIBRATION')
        self.assertEqual((g.lcd()[3], g.state('menu.state')['calibration_gain']), ('CALIBRATION RESET', 1.0))

    def test_warm_up_menu_restrictions(self):
        g = Rig()
        g.key_on()
        g.sash('up_button')
        g.press('fan_button')
        g.step(30.0)
        g.enter_menu('0000')
        self.assertEqual((g.screen(), g.lcd()[3]), ('home', 'ADMIN PIN REQUIRED'))
        g.enter_menu('0009')
        left = g.state('fan.remaining_s')
        g.step(60.0)
        self.assertEqual(g.state('fan.remaining_s'), left)                       # the warm-up pauses in the menu
        self.assertEqual(g.lcd()[1:], ['>SETTINGS', ' SET MODE', ' FIELD CALIB'])
        g.presses('down_button', 3)
        self.assertEqual(g.lcd()[1:], [' SET MODE', ' FIELD CALIB', '>ADMIN SETTINGS'])   # FIELD CALIB skipped during warm-up
        g.go('SETTINGS')
        g.presses('up_button', 8)
        g.presses('down_button', 3)
        self.assertEqual(g.lcd()[2:], [' WARM UP TIME      3 MIN', '>POST PURGE TIME   0 MIN'])   # WARM UP TIME shown but skipped
        g.press('set_button')
        g.press('up_button')
        g.press('set_button')
        self.assertEqual(g.state('settings.current')['post_purge_s'], 60.0)
        g.press('menu_button')
        g.press('menu_button')
        g.step(30.0)
        self.assertAlmostEqual(g.state('fan.remaining_s'), left - 30.0, places=6)

    def test_standby_and_interlocks(self):
        g = Rig()
        g.working()
        self.assertTrue(g.press('fan_button', 3.0))                              # FAN held: standby
        self.assertEqual((g.state('fan.state'), g.lcd()[0][:7]), ('standby', 'STANDBY'))
        self.assertFalse(g.press('lamp_button'))                                 # only the FAN key works
        self.assertFalse(g.press('menu_button'))
        self.assertFalse(g.press('up_button', 5.0))
        self.assertTrue(g.press('fan_button'))
        self.assertEqual(g.state('fan.state'), 'on')
        self.assertTrue(g.press('fan_button'))
        self.assertEqual(g.state('fan.state'), 'off')


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_calls(self):
        a, b = Rig(), Rig()
        a.key_on()
        a.press('up_button', 6.0)
        a.step(10.0)
        a.press('fan_button')
        a.step(200.0)
        a.enter_menu('0009')
        a.go('SETTINGS')
        a.go('MEASUREMENT UNIT')
        a.press('down_button')
        a.press('set_button')
        a.press('menu_button')
        a.go('ADMIN SETTINGS')
        a.go('AIRFLOW OUT TEMP')
        a.press('up_button')
        a.press('set_button')
        a.press('menu_button')
        a.press('menu_button')
        a.press('lamp_button')
        b.call(b.A, 'power.set', on=True)
        b.call(b.A, 'sash.move', direction='up', hold_s=6.0)
        b.step(10.0)
        b.call(b.A, 'fan.toggle')
        b.step(200.0)
        b.call(b.A, 'menu.enter', pin='0009')
        b.call(b.A, 'menu.configure', unit='imperial')
        b.call(b.A, 'menu.configure', airflow_out_temp=True)
        b.call(b.A, 'menu.exit')
        b.call(b.A, 'lamp.toggle')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.step(60.0)
        b.step(60.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


class Rules(unittest.TestCase):
    def test_power_gate_and_refusals(self):
        g = Rig()
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('off', ['', '', '', ''], set()))
        self.assertFalse(g.press('fan_button'))
        self.assertFalse(g.press('menu_button'))
        g.key_on()
        self.assertEqual(g.screen(), 'home')
        self.assertFalse(g.press('fan_button'))                                  # sash closed: the fan does not start
        g.sash('up_button')
        self.assertFalse(g.press('uv_button'))                                   # UV only with the sash closed
        self.assertFalse(g.state('uv.on'))
        g.enter_menu('1111')
        self.assertEqual((g.screen(), g.lcd()[3]), ('home', 'WRONG PIN'))
        self.assertFalse(g.state('menu.state')['open'])

    def test_power_cut_in_the_menu_and_api_sessions(self):
        g = Rig()
        g.working()
        g.enter_menu('0000')
        self.assertTrue(g.switch('key_switch', 'off'))
        self.assertEqual((g.screen(), g.state('menu.state')['open'], g.leds()), ('off', False, set()))
        g.key_on()
        self.assertEqual(g.screen(), 'home')
        g.call(g.A, 'menu.enter', pin='0000')                                    # a menu session opened through the API shows on the LCD
        self.assertEqual(g.screen(), 'menu')
        g.call(g.A, 'menu.exit')
        self.assertEqual(g.screen(), 'home')

    def test_unknown_inputs_rejected(self):
        g = Rig()
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='key_x')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.A.invoke(instance_id=INST, capability_id='panel.frame', arguments={}, request_id='f1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.working()
            return g
        a, b = scenario(), scenario()
        for d in SPEC['displays']:
            self.assertTrue((a.h.panel.render(d) == b.h.panel.render(d)).all())
        self.assertEqual(a.h.panel.png_bytes('lcd'), b.h.panel.png_bytes('lcd'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('lcd', OUT / 'home.png')}
        a.enter_menu('0009')
        shots['menu'] = a.h.panel.save_png('lcd', OUT / 'menu.png')
        a.go('SET MODE')
        shots['menu_set_mode'] = a.h.panel.save_png('lcd', OUT / 'menu_set_mode.png')
        a.press('menu_button')
        a.press('menu_button')
        a.step(3.0)
        a.enter_menu('1111')
        self.assertEqual(a.lcd()[3], 'WRONG PIN')
        shots['error'] = a.h.panel.save_png('lcd', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())

    def test_spec_and_contract(self):
        meta = rt.export_esco_ac24s8_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, set(rt.CAPS)), [])
        self.assertFalse(SPEC.get('approximate', False))
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.switch', 'panel.capture', 'panel.frame'})
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in list(rt.PANEL_CAPS) + list(rt._MENU_FUNCTIONS):
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'preconditions', 'errors'):
                self.assertEqual(c['capabilities'][k][key], meta['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        self.assertEqual(c['capabilities']['maintenance.field_calibration']['availability'], 'implemented')
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class']), ('panel_ui', 'text_menu'))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((rt.ASSET_DIR / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('esco_ac2_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 1:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
