"""front-panel tests for CHEM-198 binder_kbf115 (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_binder_kbf115.py -q

Every gesture goes through the session API (panel.switch / panel.touch, the runtime's normal invoke path) and every check looks at the runtime
state and at the screen (the structured frame of panel.frame; colours and brightness on the rendered pixels).  Themes: the key procedures of
the menu survey; the locked start with the factory passwords, log-in and authorization levels; the program editor (time and week programs,
sections, copy / insert / delete, names), program start with delay, pause, cancel and the end message; settings forms (date and time, display
brightness and screen saver, measurement chart, Ethernet), alarm list and reset, event list, information pages, chart recorder view, touchscreen
calibration, factory reset; panel = direct capability (same runtime state); refusals (locked, out-of-range entries, wrong passwords, a week
section without weekday); render determinism and the PNGs of outputs/panel_ui/binder_kbf115/; contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import binder_kbf115 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'binder_kbf115_01'
T0 = 273.15
SCR = 'screen'
OUT = ROOT / 'outputs/panel_ui/binder_kbf115'
SURVEY_TASKS = ['turn on the chamber', 'enter fixed-value setpoints', 'set the safety controller', 'start a time program',
                'pause / cancel a running time program', 'acknowledge an alarm', 'set tolerance-range alarm', 'export data to USB']
DONE_TASKS = set()


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.BinderKbf115Runtime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        before = self.h.read('evaluator')
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            assert cap == 'sim.step' or self.h.t == t0, cap                              # gestures take no simulated time
            if cap.startswith('panel.'):                                                 # a gesture changes only what its commands change
                after = self.h.read('evaluator')
                changed = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)}
                assert not changed - set(rt.EFFECTS.get(cap, [])), (cap, sorted(changed - set(rt.EFFECTS.get(cap, []))))
        return r['result'] if expect == 'succeeded' else r

    # ---- gestures
    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCR, x=int(x), y=int(y), **kw)['accepted']

    def wake(self):
        if self.frame()['screen'] == 'saver':
            assert self.touch(320, 240)

    def tap(self, wid):
        self.wake()
        w = self.widgets()[wid]
        assert w['enabled'], (wid, 'disabled')
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def tap_text(self, text):
        self.wake()
        for w in self.disp()['widgets']:
            if w['text'] == text and w['type'] == 'button':
                x, y, ww, hh = w['rect']
                return self.touch(x + ww // 2, y + hh // 2)
        raise AssertionError(f'{text!r} not on {self.page()}: {[w["text"] for w in self.disp()["widgets"]]}')

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def row(self, wid, first, col=0):
        """Tap the row of a list or form widget whose cell (col) is this text (dragging the rows until it shows)."""
        for _ in range(20):
            w = self.widgets()[wid]
            firsts = [c[col] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, firsts)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def type(self, text):
        """Type on the open keyboard: the shown text is replaced; digits through 12@ on the alpha keyboard."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard'
        labels = {b['label'] for b in ov['buttons']}
        if '7' in labels and 'q' not in labels:                                          # numeric keyboard
            while self.overlay()['field']:
                self.ov('Del')
            for ch in text:
                assert self.ov(ch), ch
            return
        while self.overlay()['field']:
            self.ov('Del')
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit() or ch in '$@#%&/():;_+=\'"!?,':
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if (self.overlay()['mode'] == 'upper') != ch.isupper():
                    self.ov('Shift')
                self.ov(ch)

    def enter(self, wid, label, text):
        """A form row: tap it, type the value, OK."""
        self.row(wid, label)
        self.type(text)
        return self.ov('OK')

    def choose(self, wid, label, option):
        self.row(wid, label)
        return self.ov(option)

    def spin(self, col, n):
        """Date / time picker: + (n > 0) or - on one column (date: day, month, year; time: hour, minute)."""
        bid = f'inc:{col}' if n > 0 else f'dec:{col}'
        b = next(b for b in self.overlay()['buttons'] if b['id'] == bid)
        x, y, w, h = b['rect']
        for _ in range(abs(n)):
            self.touch(x + w // 2, y + h // 2)

    def login(self, level='Admin', password='2'):
        self.tap('ft_menu')
        self.tap_text('User management')
        self.tap_text('Log in')
        self.choose('f_login_rows', 'Authorization', level)
        self.enter('f_login_rows', 'Password', password)
        self.tap_text('Confirm')

    def on(self):
        assert self.switch('on')
        assert self.page() == ('gui', 'home'), self.page()

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCR]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def cells(self, wid):
        return [c for c in self.widgets()[wid]['cells']]

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def overlay(self):
        return self.disp().get('overlay')

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def state(self, *ids):
        o = self.E.read(instance_id=self.inst, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}


def runtime_state(h):
    r = h.read('evaluator')
    for k in ('clock.time_s',):
        r.pop(k)
    return json.dumps(dict(read=r, fixed=h.fixed, functions=h.functions, safety=h.safety, tol=h.tol, programs=h.programs, week=h.week, names=h.prog_names,
                           prog=h.prog, settings=h.settings, passwords=h.passwords, logged=h.logged, clock=h.clock, buzzer=h.buzzer), sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_turn_on_the_chamber(self):
        g = Rig()
        self.assertEqual(g.page()[0], 'off')
        self.assertFalse(g.touch(320, 240))                                            # dark: nothing reacts
        self.assertFalse(g.frame()['displays']['pilot']['on'])
        g.on()
        self.assertTrue(g.frame()['displays']['pilot']['on'])                           # ready-to-use indicator
        self.assertEqual(g.text('hd_mode'), 'Fixed value')
        self.assertEqual(g.text('hd_lock'), '[locked]')                                # factory passwords on every level: locked
        self.assertFalse(g.widgets()['ft_setpoint']['enabled'])
        g.call(g.A, 'water.supply', open=True)
        g.login()
        self.assertEqual((g.text('hd_lock'), g.state('user.session')['authorization']), ('[open]', 'admin'))
        g.tap('ft_setpoint')
        rows = {c[0]: c[1] for c in g.cells('f_setpoints_rows')}
        self.assertEqual(rows['Humidity'], '[x] Humidity')                              # Control on/off: humidity control on
        self.assertEqual(rows['Functions on/off'], '')                                  # no 'Humidity off' function active
        g.tap_text('Close')
        g.step(600.0)
        g.wake()
        self.assertEqual(g.page(), ('gui', 'home'))
        DONE_TASKS.add('turn on the chamber')

    def test_enter_fixed_value_setpoints(self):
        g = Rig()
        g.on()
        g.login()
        g.tap('ft_setpoint')
        self.assertEqual(g.page(), ('gui', 'setpoints'))
        g.enter('f_setpoints_rows', 'Temperature', '40')
        g.enter('f_setpoints_rows', 'Humidity', '85')                                   # outside the setting range
        self.assertEqual(g.overlay()['error'], 'Value outside of limits! (Min: 0, Max: 80)')
        g.type('75')
        g.ov('OK')
        g.enter('f_setpoints_rows', 'Fan', '90')
        self.assertEqual(g.state('setpoint.fixed')['temperature_k'], T0 + 25.0)        # nothing taken before Confirm
        g.tap_text('Confirm')
        fx = g.state('setpoint.fixed')
        self.assertAlmostEqual(fx['temperature_k'], T0 + 40.0, places=9)
        self.assertAlmostEqual(fx['relative_humidity'], 0.75, places=9)
        self.assertAlmostEqual(fx['fan_fraction'], 0.90, places=9)
        self.assertEqual(g.page(), ('gui', 'home'))
        self.assertEqual((g.text('hm_t_set'), g.text('hm_rh_set')), ('40.0', '75.0'))
        g.tap('hm_t_set')                                                               # quick setpoint entry on the Normal display
        g.type('35.5')
        g.ov('OK')
        self.assertAlmostEqual(g.state('setpoint.fixed')['temperature_k'], T0 + 35.5, places=9)
        DONE_TASKS.add('enter fixed-value setpoints')

    def test_set_the_safety_controller(self):
        g = Rig()
        g.on()
        g.login()
        g.tap('ft_setpoint')
        g.choose('f_setpoints_rows', 'Mode', 'Limit')
        g.enter('f_setpoints_rows', 'Limit (high)', '45')
        g.tap_text('Confirm')
        self.assertEqual(g.state('safety.settings')['mode'], 'limit')
        self.assertAlmostEqual(g.state('safety.settings')['value_k'], T0 + 45.0, places=9)
        g.tap('ft_setpoint')
        g.choose('f_setpoints_rows', 'Mode', 'Offset')
        g.enter('f_setpoints_rows', 'Offset', '5')
        g.tap_text('Confirm')
        self.assertEqual((g.state('safety.settings')['mode'], g.state('safety.settings')['value_k']), ('offset', 5.0))
        g.tap('ft_info')
        g.tap_text('Safety controller')
        self.assertEqual(g.text('info_safety_1'), 'Limit 30.0 °C')                      # 25 C set point + 5 K offset
        DONE_TASKS.add('set the safety controller')

    def test_start_a_time_program_and_pause_cancel(self):
        g = Rig()
        g.on()
        g.login()
        g.call(g.A, 'program.write', program=4, sections=[dict(duration_s=3600.0, temperature_k=T0 + 30.0, relative_humidity=0.6, transition='step'),
                                                          dict(duration_s=600.0, temperature_k=T0 + 30.0, relative_humidity=0.6, transition='step')])
        g.call(g.A, 'program.set_name', kind='time', program=4, name='Test')
        g.tap('ft_start')
        self.assertEqual(g.page(), ('gui', 'program_start'))
        g.choose('f_program_start_rows', 'Program', '4 Test')
        g.row('f_program_start_rows', 'Program start time')                            # start in 2 minutes
        ov = g.overlay()
        self.assertEqual(ov['kind'], 'spinner')
        g.spin(1, 2)
        g.ov('OK')
        g.tap_text('Confirm')
        st = g.state('program.status')['running']
        self.assertEqual((st['program'], st['kind']), (4, 'time'))
        self.assertEqual(g.text('hd_mode'), 'Program delay')
        g.step(120.0)
        g.wake()
        self.assertEqual(g.text('hd_mode'), 'Time program')
        self.assertEqual(g.text('hm_prog_name'), 'Test')
        g.step(60.0)
        g.wake()
        self.assertEqual(g.text('hm_prog_rt'), '00:01:00')
        g.tap('ft_pause')                                                               # Program pause: the runtime stops, the time flashes
        self.assertEqual((g.state('state.current'), g.text('hd_mode')), ('program_paused', 'Program pause'))
        g.step(300.0)
        g.wake()
        self.assertEqual(g.text('hm_prog_rt'), '00:01:00')
        self.assertIn('ft_start', g.widgets())
        g.tap('ft_start')                                                               # Program start continues the program
        self.assertEqual(g.state('state.current'), 'program')
        g.tap('ft_cancel')
        self.assertEqual(g.overlay()['title'], 'Program cancelling')
        g.ov('Confirm')
        self.assertIsNone(g.state('program.status')['running'])
        self.assertEqual(g.text('hd_mode'), 'Fixed value')
        DONE_TASKS.update({'start a time program', 'pause / cancel a running time program'})

    def test_acknowledge_an_alarm(self):
        g = Rig()
        g.on()
        g.login()
        g.call(g.A, 'door.move', angle_rad=1.6)
        g.step(301.0)                                                                   # door open 5 min: alarm
        g.wake()
        self.assertEqual(g.state('alarms.active'), ['DOOR_OPEN'])
        self.assertEqual(g.text('hd_mode'), 'Door open')
        self.assertTrue(g.state('alarm.audible'))
        g.tap('ft_alarm')
        self.assertEqual(g.cells('al_list'), [['Door open']])
        g.tap_text('Reset alarm')                                                       # buzzer muted, alarm stays while the door is open
        self.assertFalse(g.state('alarm.audible'))
        self.assertEqual(g.state('alarms.active'), ['DOOR_OPEN'])
        g.call(g.A, 'door.move', angle_rad=0.0)
        g.step(2.0)
        g.wake()
        self.assertEqual(g.state('alarms.active'), [])
        DONE_TASKS.add('acknowledge an alarm')

    def test_set_tolerance_range_alarm(self):
        g = Rig()
        g.on()
        g.tap('ft_menu')
        self.assertNotIn('Settings', [w['text'] for w in g.disp()['widgets']])          # locked: no Settings
        g.tap_text('Back')
        g.login('User', '1')
        g.tap('ft_menu')
        self.assertNotIn('Settings', [w['text'] for w in g.disp()['widgets']])          # User level: no Settings and Service (E7 14.1)
        g.tap_text('Back')
        g.tap('ft_menu')
        g.tap_text('User management')
        g.tap_text('Log out')
        g.login()
        g.tap('ft_menu')
        g.tap_text('Settings')
        g.tap_text('Various')
        g.enter('f_set_various_rows', 'Range alarm delay', '30')
        g.enter('f_set_various_rows', 'Temperature range', '4')
        g.enter('f_set_various_rows', 'Humidity range', '8')
        g.tap_text('Confirm')
        self.assertEqual(g.state('tolerance.settings'), dict(temperature_k=4.0, relative_humidity=0.08, delay_s=1800.0))
        DONE_TASKS.add('set tolerance-range alarm')

    def test_export_data_to_usb(self):
        """The USB menu opens only on a USB stick, which the simulated configuration does not have: declared unsupported; the logger data are
        read with log.read."""
        un = {u['item']: u for u in rt.PANEL_SPEC['unsupported']}
        self.assertIn('USB menu', un)
        g = Rig()
        g.on()
        g.step(180.0)
        self.assertGreaterEqual(len(g.call(g.A, 'log.read', last=10)['records']), 2)
        DONE_TASKS.add('export data to USB')


# ---------------------------------------------------------------- program editor, program operation (manual p.55-79)
class Programs(unittest.TestCase):
    def test_create_a_time_program_in_the_editor(self):
        g = Rig()
        g.on()
        g.login()
        g.tap('ft_menu')
        g.tap_text('Programs')
        g.tap_text('Time program')
        g.row('time_places', '3')                                                       # an empty place: name first (E7 10.4)
        self.assertEqual(g.page(), ('gui', 'time_head'))
        g.enter('f_time_head_rows', 'Program name', 'ICH 30')
        g.tap_text('Confirm')
        self.assertEqual(g.page(), ('gui', 'time_view'))
        self.assertEqual(len(g.cells('time_sections')), 1)
        g.row('time_sections', '1')
        self.assertEqual(g.page(), ('gui', 'time_sec'))
        g.enter('f_time_sec_rows', 'Duration h', '0')
        g.enter('f_time_sec_rows', 'Duration min', '30')
        g.choose('f_time_sec_rows', 'Course', 'Step')
        g.enter('f_time_sec_rows', 'Temperature', '30')
        g.enter('f_time_sec_rows', 'Humidity', '65')
        g.tap_text('Confirm')
        self.assertEqual(g.cells('time_sections'), [['1', '00:30:00', '30.0', '65.0', 'Step']])
        g.tap_text('Edit')                                                              # program editor: create new section (at the bottom)
        g.ov('Create new section')
        self.assertEqual(len(g.cells('time_sections')), 2)
        g.row('time_sections', '1')
        g.tap_text('Edit')                                                              # section editor: copy section 1
        g.ov('Copy section')
        g.row('time_sections', '2')
        g.tap_text('Edit')
        g.ov('Insert section after')                                                    # copy inserted after section 2
        self.assertEqual([c[1] for c in g.cells('time_sections')], ['00:30:00', '01:00:00', '00:30:00'])
        g.row('time_sections', '2')
        g.tap_text('Edit')
        g.ov('Delete section')
        self.assertEqual([c[1] for c in g.cells('time_sections')], ['00:30:00', '00:30:00'])
        self.assertNotIn('3', g.state('program.store')['time'])                         # nothing stored before Confirm (E7 10.7.7)
        g.tap_text('Confirm')
        self.assertEqual(g.page(), ('gui', 'home'))
        store = g.state('program.store')['time']['3']
        self.assertEqual((store['name'], len(store['sections']), store['sections'][0]['transition']), ('ICH 30', 2, 'step'))
        self.assertAlmostEqual(store['sections'][0]['temperature_k'], T0 + 30.0, places=9)
        g.tap('ft_start')                                                               # and run it
        g.choose('f_program_start_rows', 'Program', '3 ICH 30')
        g.tap_text('Confirm')
        self.assertEqual(g.state('program.status')['running']['program'], 3)

    def test_week_program_in_the_editor(self):
        g = Rig()
        g.on()
        g.login()
        g.call(g.A, 'clock.set', date='2026-10-19', time='07:00')
        g.tap('ft_menu')
        g.tap_text('Programs')
        g.tap_text('Week program')
        g.row('week_places', '1')
        g.enter('f_week_head_rows', 'Program name', 'Lab week')
        g.choose('f_week_head_rows', 'Course', 'Step')
        g.tap_text('Confirm')
        self.assertTrue(g.widgets()['week_sections']['selected_rows'] == [0])            # no weekday: marked
        g.tap_text('Confirm')
        self.assertEqual(g.overlay()['title'], 'Week program')                          # cannot be saved (E7 11.3)
        g.ov('Confirm')
        g.row('week_sections', '1')
        g.choose('f_week_sec_rows', 'Weekday', 'Monday')
        g.row('f_week_sec_rows', 'Moment')
        g.spin(0, 1)                                                                     # 00:00 -> 01:00
        g.ov('OK')
        g.enter('f_week_sec_rows', 'Temperature', '32')
        g.tap_text('Confirm')
        g.tap_text('Confirm')
        self.assertEqual(g.page(), ('gui', 'home'))
        sec = g.state('program.store')['week']['1']['sections'][0]
        self.assertEqual((sec['weekday'], sec['time']), ('mon', '01:00'))
        g.tap('ft_start')
        g.choose('f_program_start_rows', 'Program type', 'Week program')
        g.choose('f_program_start_rows', 'Program', '1 Lab week')
        g.tap_text('Confirm')
        self.assertEqual(g.text('hd_mode'), 'Week program')
        self.assertAlmostEqual(g.state('setpoint.current')['temperature_k'], T0 + 32.0, places=9)

    def test_timer_program_and_the_end_message(self):
        g = Rig()
        g.on()
        g.login()
        g.tap('ft_start')
        g.choose('f_program_start_rows', 'Program type', 'Timer program')
        g.enter('f_program_start_rows', 'Program duration h', '0')
        g.enter('f_program_start_rows', 'Program duration min', '5')
        g.tap_text('Confirm')
        self.assertEqual((g.text('hd_mode'), g.state('setpoint.current')['source']), ('Timer program', 'timer program'))
        g.step(301.0)
        self.assertEqual(g.page(), ('program_end', 'end'))                              # E7 8.3
        self.assertEqual(g.text('pe_msg'), 'Device changes to fixed value operation mode.')
        g.tap_text('Confirm')
        self.assertIsNone(g.state('program.status')['end_notice'])
        self.assertEqual(g.page(), ('gui', 'home'))

    def test_program_start_refusals(self):
        g = Rig()
        g.on()
        self.assertFalse(g.tap('ft_start') if g.widgets()['ft_start']['enabled'] else g.touch(*[v + 5 for v in g.widgets()['ft_start']['rect'][:2]]))
        g.login()
        g.tap('ft_start')
        g.tap_text('Confirm')                                                           # time program without a program
        self.assertEqual(g.overlay()['title'], 'Program start')
        g.ov('Confirm')
        self.assertIsNone(g.state('program.status')['running'])


# ---------------------------------------------------------------- settings, users, views
class SettingsAndUsers(unittest.TestCase):
    def test_date_time_zone_and_display(self):
        g = Rig()
        g.on()
        g.login()
        g.tap('ft_menu')
        g.tap_text('Settings')
        g.tap_text('Date and time')
        g.row('f_set_datetime_rows', 'Date')
        g.spin(0, 18)                                                                    # 01 -> 19 January 2026
        g.ov('OK')
        g.row('f_set_datetime_rows', 'Time')
        g.spin(0, 9)
        g.ov('OK')
        g.choose('f_set_datetime_rows', 'Time zone', 'UTC+02:00')
        g.tap_text('Confirm')
        self.assertEqual(g.state('controller.clock')[:16], '2026-01-19T09:00')
        self.assertEqual(g.state('device.settings')['datetime.time_zone'], 'UTC+02:00')
        g.tap_text('Display')
        g.enter('f_set_display_rows', 'Brightness', '40')
        g.enter('f_set_display_rows', 'Wait time for screen saver', '30')
        g.tap_text('Confirm')
        img = g.h.panel.render(SCR)
        self.assertLessEqual(int(img.max()), int(255 * 0.4) + 1)                         # dimmed display
        g.step(31.0)
        self.assertEqual(g.page()[0], 'saver')
        self.assertEqual(int(g.h.panel.render(SCR).max()), 0)                            # dark
        n = len(g.h.events)
        self.assertTrue(g.touch(320, 240))                                              # the touch only wakes the display
        self.assertEqual((g.page(), len(g.h.events)), (('gui', 'home'), n))
        g.call(g.A, 'settings.configure', key='display.continuous', value=True)
        g.call(g.A, 'settings.configure', key='display.continuous_begin', value='00:00')
        g.call(g.A, 'settings.configure', key='display.continuous_end', value='23:59')
        g.step(120.0)
        self.assertEqual(g.page()[0], 'gui')                                            # continuous operation: no screen saver

    def test_unit_language_chart_ethernet_and_factory_reset(self):
        g = Rig()
        g.on()
        g.login()
        g.tap('ft_menu')
        g.tap_text('Settings')
        g.tap_text('Chamber')
        g.choose('f_set_chamber_rows', 'Temperature unit', '°F')
        g.choose('f_set_chamber_rows', 'Language', 'Deutsch')
        g.choose('f_set_chamber_rows', 'Audible alarm', 'off')
        g.tap_text('Confirm')
        self.assertEqual((g.state('device.settings')['chamber.language'], g.h.buzzer), ('de', False))
        g.tap_text('Measurement chart')
        g.enter('f_set_chart_rows', 'Storage interval', '120')
        g.choose('f_set_chart_rows', 'Storage values', 'Mean values')
        g.tap_text('Confirm')
        self.assertEqual((g.state('logger.settings')['interval_s'], g.state('device.settings')['chart.storage_values']), (120.0, 'mean'))
        g.tap_text('Ethernet')
        g.choose('f_set_ethernet_rows', 'IP address assignment', 'Manual')
        g.enter('f_set_ethernet_rows', 'IP address', '192.168.1.300')
        g.tap_text('Confirm')
        self.assertEqual(g.overlay()['title'], 'Entry')                                 # refused: the form stays open
        g.ov('Confirm')
        g.enter('f_set_ethernet_rows', 'IP address', '192.168.1.30')
        g.tap_text('Confirm')
        self.assertEqual(g.state('device.settings')['ethernet.ip_address'], '192.168.1.30')
        g.tap_text('Back')
        g.tap_text('Back')
        self.assertEqual(g.text('hm_t_unit'), '°F')
        self.assertEqual(g.text('hm_t_set'), '77.0')                                    # 25 C
        g.tap('ft_menu')
        g.tap_text('Service')
        g.tap_text('Reset to factory settings')
        g.ov('Confirm')
        self.assertEqual(g.state('device.settings')['chamber.temperature_unit'], 'C')
        self.assertTrue(g.state('user.session')['locked'])

    def test_users_and_passwords(self):
        g = Rig()
        g.on()
        g.login('Admin', '3')
        self.assertEqual(g.overlay()['title'], 'Log in')                                # wrong password: the entry stays open
        g.ov('Confirm')
        self.assertTrue(g.state('user.session')['locked'])
        g.enter('f_login_rows', 'Password', '2')
        g.tap_text('Confirm')
        self.assertEqual(g.page(), ('gui', 'home'))
        g.tap('ft_menu')
        g.tap_text('User management')
        g.tap_text('Password')
        g.choose('f_password_rows', 'Authorization', 'User')
        g.enter('f_password_rows', 'Password', 'ab1')
        g.enter('f_password_rows', 'Confirm password', 'ab2')
        g.tap_text('Confirm')
        self.assertEqual(g.overlay()['title'], 'Password')                              # mismatch: nothing changed, the entry stays
        g.ov('Confirm')
        self.assertEqual((g.h.passwords['user'], g.page()), ('1', ('gui', 'password')))
        g.tap_text('Close')
        g.tap_text('Password')
        g.choose('f_password_rows', 'Authorization', 'User')
        g.tap_text('Confirm')                                                           # nothing entered: the password is deleted (E7 14.5.2)
        self.assertEqual(g.h.passwords['user'], '')
        g.tap('ft_menu')
        g.tap_text('User management')
        g.tap_text('Log out')
        self.assertEqual((g.state('user.session')['authorization'], g.text('hd_lock')), ('user', ''))   # User level free: no lock icon
        self.assertTrue(g.widgets()['ft_setpoint']['enabled'])

    def test_locked_controls_refused(self):
        g = Rig()
        g.on()
        before = runtime_state(g.h)
        w = g.widgets()['hm_t_set']
        self.assertFalse(w['enabled'])
        self.assertFalse(g.touch(w['rect'][0] + 10, w['rect'][1] + 10))
        self.assertFalse(g.touch(*[v + 10 for v in g.widgets()['ft_setpoint']['rect'][:2]]))
        self.assertEqual(runtime_state(g.h), before)


class Views(unittest.TestCase):
    def test_events_info_notes_chart_and_calibration(self):
        g = Rig()
        g.on()
        g.login()
        g.call(g.A, 'door.move', angle_rad=1.6)
        g.step(5.0)
        g.tap('ft_events')
        texts = [c[1] for c in g.cells('ev_list')]
        self.assertEqual(texts[:2], ['Door open', 'Power on'])                          # newest first
        g.tap_text('Back')
        g.tap('hd_notes')                                                               # information texts
        self.assertIn('Door open', g.overlay()['lines'])
        g.ov('Close')
        g.tap('hd_clock')
        self.assertIn('Authorization: Admin', g.overlay()['lines'])
        g.ov('Close')
        g.tap('ft_info')
        g.tap_text('Actual values')
        self.assertTrue(g.text('info_actual_0').startswith('Temperature 2'))
        g.tap_text('Back')
        g.tap_text('Back')
        g.call(g.A, 'door.move', angle_rad=0.0)
        g.step(900.0)
        g.wake()
        g.tap('ft_view')
        self.assertEqual(g.page(), ('gui', 'program_view'))
        g.tap('ft_view')
        self.assertEqual(g.page(), ('gui', 'chart'))
        plot = g.widgets()['ch_plot']
        self.assertEqual([s['label'] for s in plot['series']], ['Temperature', 'Temp. setpoint', 'Humidity', 'Hum. setpoint'])
        self.assertGreater(len(plot['series'][0]['points']), 10)
        g.tap('ch_ind')                                                                 # show the door-open indication
        self.assertIn('Door open', [s['label'] for s in g.widgets()['ch_plot']['series']])
        g.tap('ch_history')
        g.tap('ch_zoom')
        g.choose('f_chart_zoom_rows', 'Zoom factor', '1 h')
        g.tap_text('Confirm')
        self.assertEqual(g.frame()['ui']['vars']['span_h'], 1.0)
        g.tap('ch_legend_btn')
        self.assertNotIn('ch_legend', g.widgets())
        g.tap('ft_home') if 'ft_home' in g.widgets() else g.tap('ft_view')
        g.tap('ft_menu')
        g.tap_text('Calibrate touchscreen')
        g.tap_text('Start')
        self.assertEqual(g.page()[0], 'calibrate_1')
        for i, (x, y) in enumerate([(8, 8), (568, 8), (568, 408), (8, 408)], start=1):
            self.assertEqual(g.page()[0], f'calibrate_{i}')
            self.assertTrue(g.touch(x + 30, y + 30))
        self.assertEqual(g.page(), ('gui', 'home'))
        g.tap('ft_menu')
        g.tap_text('Calibrate touchscreen')
        g.tap_text('Start')
        g.step(11.0)                                                                    # a box not touched in time: aborted
        self.assertEqual(g.page(), ('gui', 'home'))

    def test_language_query_at_restart(self):
        g = Rig()
        g.on()
        g.call(g.A, 'settings.configure', key='chamber.language_query', value=True)
        g.switch('off')
        g.switch('on')
        self.assertEqual(g.page(), ('startup', 'startup'))
        g.choose('f_startup_rows', 'Language', 'Français')
        g.tap_text('Confirm')
        self.assertEqual((g.page()[0], g.state('device.settings')['chamber.language']), ('gui', 'fr'))


class Equivalence(unittest.TestCase):
    def test_setpoints_and_programs(self):
        a, b = Rig(seed=3), Rig(seed=3)
        a.on()
        a.login()
        b.call(b.A, 'power.set', on=True)
        b.call(b.A, 'user.login', level='admin', password='2')
        a.tap('ft_setpoint')
        a.enter('f_setpoints_rows', 'Temperature', '35')
        a.enter('f_setpoints_rows', 'Humidity', '70')
        a.row('f_setpoints_rows', 'Functions on/off')
        a.ov('[ ] Dehumidification off')
        a.ov('OK')
        a.choose('f_setpoints_rows', 'Mode', 'Limit')
        a.enter('f_setpoints_rows', 'Limit (high)', '40')
        a.tap_text('Confirm')
        b.call(b.A, 'setpoint.set', temperature_k=T0 + 35.0, relative_humidity=0.70, fan_fraction=1.0)
        b.call(b.A, 'function.set', humidity_control=True, idle_mode=False, humidification_off=False, dehumidification_off=True)
        b.call(b.A, 'safety.configure', mode='limit', value_k=T0 + 40.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.step(600.0)
        b.step(600.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


# ---------------------------------------------------------------- renders, capture, contract
class SweepOwnerRule(unittest.TestCase):
    """Lead rule 10-19 (1.1.2): a program started through an agent's session belongs to it; another agent can neither stop, pause, replace nor change
    it, on the touch screen or directly (state unchanged, a dialog on the screen); navigation, viewing, doors and the main switch (physical) stay free."""

    def test_another_agents_program_cannot_be_stopped_or_changed(self):
        g = Rig()
        g.on()
        g.login()
        g.call(g.A, 'program.write', program=4, sections=[dict(duration_s=3600.0, temperature_k=T0 + 30.0, relative_humidity=0.6, transition='step')])
        g.call(g.A, 'program.set_name', kind='time', program=4, name='Test')
        g.tap('ft_start')
        g.choose('f_program_start_rows', 'Program', '4 Test')
        g.tap_text('Confirm')
        self.assertEqual((g.state('program.status')['running']['program'], g.h.run_owner), (4, 'agent'))
        g.step(60.0)
        g.wake()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')

        def btouch(x, y):
            return g.call(B, 'panel.touch', display_id=SCR, x=int(x), y=int(y))['accepted']

        def btap(wid):
            x, y, ww, hh = g.widgets()[wid]['rect']
            return btouch(x + ww // 2, y + hh // 2)

        def bov(label):
            for b in g.overlay()['buttons']:
                if b['label'] == label:
                    x, y, w, h = b['rect']
                    return btouch(x + w // 2, y + h // 2)
            raise AssertionError(label)

        before = runtime_state(g.h)
        self.assertTrue(btap('ft_cancel'))                                             # navigation is free: the cancel query opens ...
        self.assertEqual(g.overlay()['title'], 'Program cancelling')
        self.assertFalse(bov('Confirm'))                                               # ... but cancelling A's program is refused
        self.assertEqual((g.overlay()['dialog'], g.overlay()['lines']), ('not_permitted', ['Not permitted:', "another user's program is running."]))
        bov('Confirm')
        self.assertFalse(btap('ft_pause'))
        self.assertEqual(g.overlay()['dialog'], 'not_permitted')
        bov('Confirm')
        for cap, args in (('program.stop', {}), ('program.pause', {}), ('timer_program.start', dict(duration_s=600.0)),
                          ('setpoint.set', dict(temperature_k=T0 + 5.0))):
            r = g.call(B, cap, expect='rejected', **args)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(runtime_state(g.h), before)
        g.call(B, 'door.move', angle_rad=0.5)                                           # doors are physical: not refused
        g.call(B, 'door.move', angle_rad=0.0)
        g.tap('ft_cancel')                                                               # the owner cancels its own program
        g.ov('Confirm')
        self.assertIsNone(g.state('program.status')['running'])
        g.call(B, 'timer_program.start', duration_s=600.0)                               # a new program belongs to whoever starts it
        self.assertEqual(g.h.run_owner, 'agent_b')
        g.call(g.E, 'program.stop')                                                      # the evaluator is not restricted
        self.assertIsNone(g.state('program.status')['running'])
        g.call(g.A, 'timer_program.start', duration_s=600.0)                           # the main switch is physical: B may switch A's run off
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])
        self.assertFalse(g.state('power.on'))


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig(seed=5)
            g.on()
            g.login()
            g.call(g.A, 'setpoint.set', temperature_k=T0 + 40.0, relative_humidity=0.75)
            g.step(1200.0)
            g.wake()
            return g
        a, b = scenario(), scenario()
        for d in rt.PANEL_SPEC['displays']:
            ia, ib = a.h.panel.render(d), b.h.panel.render(d)
            self.assertEqual(ia.dtype.name, 'uint8')
            self.assertTrue((ia == ib).all())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png(SCR, OUT / 'home.png')}
        a.tap('ft_menu')
        shots['menu'] = a.h.panel.save_png(SCR, OUT / 'menu.png')
        a.tap_text('Back')
        a.tap('ft_setpoint')
        shots['setpoints'] = a.h.panel.save_png(SCR, OUT / 'setpoints.png')
        a.tap_text('Close')
        a.call(a.A, 'door.move', angle_rad=1.6)
        a.step(301.0)                                                                   # door alarm: header flashing red
        a.wake()
        if int(a.h.t) % 2:
            a.step(1.0)
        img = a.h.panel.render(SCR)
        self.assertEqual(tuple(img[2, 2]), (210, 40, 40))
        shots['error'] = a.h.panel.save_png(SCR, OUT / 'error.png')
        a.tap('ft_view')
        a.tap('ft_view')
        shots['chart'] = a.h.panel.save_png(SCR, OUT / 'chart.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_and_copy(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.on()
        r2 = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        self.assertIs(g.h.panel.host, g.h)
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.switch', 'panel.touch', 'panel.capture', 'panel.frame'})
        m = rt.export_binder_kbf115_contract_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        new = ['program.pause', 'program.resume', 'program.acknowledge_end', 'program.set_name', 'program.delete', 'program.copy', 'timer_program.start',
               'week_program.write', 'week_program.start', 'clock.set', 'user.login', 'user.logout', 'user.set_password', 'settings.configure',
               'settings.factory_reset']
        for k in list(rt.PANEL_CAPS) + new:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/binder_kbf115.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('kbf_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
