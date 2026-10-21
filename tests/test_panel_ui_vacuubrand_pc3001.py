"""front-panel tests for CHEM-188 vacuubrand_pc3001_vario_select (panel_ui.json + scripts/asset_runtime/_panel_ui.py on
scripts/basic40_runtime_vacuum.py): python3 -m pytest tests/test_panel_ui_vacuubrand_pc3001.py -q

Every gesture goes through the session API (panel.touch / panel.press / panel.turn / panel.switch); the screen is checked in the structured frame
(panel.frame, evaluator) and in the rendered pixels.  Themes: the seven key procedures of the menu survey; the other controller functions
(lock, reboot, graph and main-menu swipes, favorites and context menu, multi-step applications, the VARIO speed limit, application editor,
settings incl. unit / orientation / handedness / energy saving / autostart / venting valve, administration, sensor adjustment against an evaluator
sensor error, data logger, service counter and maintenance reminder); refusals; panel = direct commands; render determinism and the evidence PNGs;
contract entries."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
import basic40_runtime_vacuum as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'pc'
OUT = ROOT / 'outputs/panel_ui/vacuubrand_pc3001'
CONTRACT = rt.PC3001_ASSET_DIR / 'asset.contract.json'
D = 'screen'
SURVEY_TASKS = ['switch on the pumping unit', 'select and start an application', 'change the pressure set-point during operation', 'vent',
                'stop the application and switch off', 'adjust a parameter in the parameter list', 'acknowledge an error']
DONE_TASKS = set()


class Rig:
    def __init__(self, on=True, chamber=True, volume=0.001, leak=0.0):
        self.h = rt.VacuumHost()
        self.h.create_instance(INST)
        self.h.register_principal('agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        if chamber:
            self.h.create_chamber('flask', volume_m3=volume, leak_throughput_pa_m3_s=leak, evidence='test flask')
            self.h.connect_source(INST, 'flask')
        if on:
            self.power_up()

    def power_up(self):
        assert self.switch('I')
        assert self.press('onoff')
        assert self.frame()['screen'] == 'boot'
        self.h.step(31)
        assert self.overlay()['title'] == 'Data storage'
        self.ok('Confirm')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid, path=None):
        v = self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']
        for k in ([] if path is None else path.split('.')):
            v = v[k]
        return v

    def step(self, dt):
        t = 0.0
        while t < dt - 1e-9:
            d = min(3600.0, dt - t)
            self.h.step(d)
            t += d

    # ---- gestures (session API only)
    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='rocker', position=pos)['accepted']

    def press(self, key, duration_s=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=duration_s)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def page(self):
        return self.frame()['ui']['page']

    def widget(self, wid):
        for w in self.frame()['displays'][D]['widgets']:
            if w['id'] == wid:
                return w
        raise AssertionError(f'{wid} not on page {self.page()}')

    def has(self, wid):
        return any(w['id'] == wid for w in self.frame()['displays'][D]['widgets'])

    def text(self, wid):
        return self.widget(wid)['text']

    def touch(self, x, y, duration_s=0.1, to=None):
        """A touch at a point of the drawn (portrait) layout, given on the screen as turned by the orientation setting."""
        r = self.frame()['displays'][D].get('rotation', 0)
        W, H = 480, 800
        fwd = {0: lambda a, b: (a, b), 90: lambda a, b: (H - 1 - b, a), 180: lambda a, b: (W - 1 - a, H - 1 - b), 270: lambda a, b: (b, W - 1 - a)}[r]
        x, y = fwd(int(x), int(y))
        args = dict(display_id=D, x=x, y=y, duration_s=duration_s)
        if to is not None:
            tx, ty = fwd(int(to[0]), int(to[1]))
            args.update(to_x=tx, to_y=ty)
        return self.call(self.A, 'panel.touch', **args)['accepted']

    def tap(self, wid, duration_s=0.1):
        x, y, w, h = self.widget(wid)['rect']
        return self.touch(x + w // 2, y + h // 2, duration_s)

    def row(self, wid, text, duration_s=0.1):
        w = self.widget(wid)
        rows = [' '.join(c) for c in w['cells']] if 'cells' in w else w['rows']
        for i, r in enumerate(rows):
            if text in r:
                x, y, ww, _ = w['rect']
                return self.touch(x + ww // 2, y + i * w.get('row_h', 40) + 10, duration_s)
        raise AssertionError((text, rows))

    def swipe(self, direction):
        return self.touch(240, 500, to=(440, 500) if direction == 'right' else (40, 500))

    def overlay(self):
        return self.frame()['displays'][D].get('overlay')

    def lines(self):
        return ' '.join(self.overlay()['lines']).replace('\n', ' ')

    def ok(self, label='OK'):
        for b in self.overlay()['buttons']:
            if b['label'] == label and b['id'] != 'field':
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError((label, [b['label'] for b in self.overlay()['buttons']]))

    def key(self, label):
        return self.ok(label)

    def enter(self, wid, text):
        assert self.tap(wid), wid
        return self.type_in(text)

    def type_in(self, text):
        for ch in text:
            self.key(ch)
        return self.key('OK')

    def form_row(self, label):
        """Tap a row of the page's form widget."""
        w = self.widget('form')
        for i, c in enumerate(w['cells']):
            if c[0] == label:
                x, y, ww, _ = w['rect']
                return self.touch(x + ww // 2, y + i * w['row_h'] + 10)
        raise AssertionError((label, w['cells']))

    def choose(self, label, option):
        self.form_row(label)
        return self.ok(option)

    def select_app(self, name):
        self.tap('app_menu')
        self.row('app_list', name)
        assert self.page() == 'process'


class KeyProcedures(unittest.TestCase):
    def test_switch_on_the_pumping_unit(self):
        r = Rig(on=False)
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertFalse(r.press('onoff'))                                     # no mains: nothing
        r.switch('I')
        self.assertEqual(r.frame()['screen'], 'off')                           # rocker on, controller still off
        r.press('onoff')
        self.assertEqual(r.frame()['screen'], 'boot')
        r.h.step(29)
        self.assertEqual(r.frame()['screen'], 'boot')                          # start screen ~30 s
        self.assertFalse(r.val('power.enabled'))
        r.h.step(2)
        self.assertEqual((r.frame()['screen'], r.page()), ('gui', 'process'))
        self.assertEqual(r.overlay()['title'], 'Data storage')                # data storage pop-up after every start
        r.ok('Confirm')
        self.assertIsNone(r.overlay())
        self.assertTrue(r.val('power.enabled'))
        self.assertEqual(r.text('title'), 'Vacuum control')
        DONE_TASKS.add('switch on the pumping unit')

    def test_select_and_start_an_application(self):
        r = Rig()
        r.select_app('Filtration')
        self.assertEqual(r.val('app.selected'), 5)
        self.assertEqual(r.text('title'), 'Filtration')
        self.assertTrue(r.tap('op_start'))
        self.assertTrue(r.val('app.run', 'running'))
        self.assertEqual((r.val('vacuum.mode'), r.val('vacuum.target_pressure_pa')), ('pressure_control', 50000.0))
        r.step(120)
        self.assertAlmostEqual(r.val('vacuum.indicated_pressure_pa'), 50000.0)
        self.assertEqual(r.text('actual'), '500')
        self.assertFalse(r.widget('op_start')['enabled'])
        DONE_TASKS.add('select and start an application')

    def test_change_the_pressure_set_point_during_operation(self):
        r = Rig()
        r.tap('op_start')
        r.step(60)
        r.enter('actual', '80')                                                # digital value: keypad, confirm
        self.assertEqual(r.val('vacuum.target_pressure_pa'), 8000.0)
        self.assertEqual(r.text('sp_text'), 'set 80.0')
        r.tap('sp_up')                                                          # step buttons
        self.assertEqual(r.val('vacuum.target_pressure_pa'), 8100.0)
        r.tap('sp_down', 2.5)                                                   # held: larger steps
        self.assertEqual(r.val('vacuum.target_pressure_pa'), 7100.0)
        x, y, w, h = r.widget('sp_slider')['rect']                              # drag the marker
        r.touch(x + 10, y + h // 2, to=(x + w // 2, y + h // 2))
        self.assertAlmostEqual(r.val('vacuum.target_pressure_pa') / 100, 2 + 1011 * (w // 2) / (w - 1), delta=1.0)
        r.enter('actual', '2000')                                               # outside the range: refused
        self.assertEqual(r.overlay()['error'], 'Out of range')
        r.key('X')
        r.step(200)
        self.assertAlmostEqual(r.val('vacuum.pressure_pa'), 10000.0)          # a higher set-point does not vent (option off)
        app = next(a for a in r.val('app.library') if a['id'] == 6)
        self.assertEqual(app['steps'][0]['params']['setpoint_pa'], r.val('vacuum.target_pressure_pa'))
        DONE_TASKS.add('change the pressure set-point during operation')

    def test_vent(self):
        r = Rig()
        r.tap('op_start')
        r.step(120)
        p0 = r.val('vacuum.pressure_pa')
        r.tap('op_vent')                                                        # brief venting: slight increase, control continues
        r.step(0.5)
        self.assertGreater(r.val('vacuum.pressure_pa'), p0 + 1000)
        self.assertTrue(r.val('app.run', 'running'))
        r.step(60)
        self.assertAlmostEqual(r.val('vacuum.pressure_pa'), 10000.0)
        r.tap('op_vent', 3.0)                                                   # hold ~3 s: vent to atmosphere, the pump stops
        self.assertFalse(r.val('app.run', 'running'))
        self.assertEqual(r.val('vent.state', 'mode'), 'atmosphere')
        r.step(2)
        r.tap('op_vent')                                                        # a tap during venting stops it
        self.assertEqual(r.val('vent.state', 'mode'), 'closed')
        p1 = r.val('vacuum.pressure_pa')
        r.step(10)
        self.assertAlmostEqual(r.val('vacuum.pressure_pa'), p1)
        r.tap('op_vent', 3.0)
        r.step(120)
        self.assertAlmostEqual(r.val('vacuum.pressure_pa'), 101325.0, delta=1.0)
        self.assertEqual(r.val('vent.state', 'mode'), 'closed')
        DONE_TASKS.add('vent')

    def test_stop_the_application_and_switch_off(self):
        r = Rig()
        r.tap('op_start')
        r.step(30)
        self.assertTrue(r.tap('op_stop'))
        self.assertFalse(r.val('vacuum.running'))
        r.press('onoff', 3.0)                                                   # hold ~3 s and confirm
        self.assertEqual(r.overlay()['title'], 'Switch off')
        r.ok('Confirm')
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertEqual(r.val('unit.power', 'controller'), 'off')
        r.switch('0')
        self.assertFalse(r.val('unit.power', 'mains'))
        DONE_TASKS.add('stop the application and switch off')

    def test_adjust_a_parameter_in_the_parameter_list(self):
        r = Rig()
        r.tap('app_icon')                                                       # application icon: parameter list
        self.assertEqual(r.page(), 'params')
        r.row('param_list', 'Speed')
        self.assertIn('Speed', r.overlay()['title'])
        r.type_in('50')
        self.assertEqual(next(a for a in r.val('app.library') if a['id'] == 6)['steps'][0]['params']['speed_pct'], 50.0)
        self.assertIn('50 %', ' '.join(' '.join(c) for c in r.widget('param_list')['cells']))
        r.row('param_list', 'Duration')
        r.key('OFF')
        r.key('OK')                                                             # OFF: no duration
        r.row('param_list', 'Pressure set-point')
        r.type_in('1')                                                          # below the 2 mbar limit: refused by the controller
        self.assertIn('outside the permissible', r.lines())
        r.ok()
        r.tap('confirm')
        self.assertEqual(r.page(), 'process')
        r.tap('op_start')
        r.step(2)
        slow = r.val('vacuum.pressure_pa')
        b = Rig()
        b.tap('op_start')
        b.step(2)
        self.assertGreater(slow, b.val('vacuum.pressure_pa') * 1.5)          # the motor runs at the adjusted speed
        DONE_TASKS.add('adjust a parameter in the parameter list')

    def test_acknowledge_an_error(self):
        r = Rig()
        r.tap('op_start')
        r.call(r.E, 'fault.inject', code='pump')
        r.step(1)
        self.assertFalse(r.val('app.run', 'running'))                          # red error: the process stops
        self.assertEqual(r.widget('fault_ind')['fill'], 'red')
        self.assertEqual(r.text('fault_pop'), 'Error at pump')
        r.tap('fault_ack')                                                      # cause still present: the error stays
        self.assertEqual(r.val('faults.list')[0]['code'], 'pump')
        r.tap('op_start')
        self.assertIn('Start not possible', r.lines())
        r.ok()
        r.call(r.E, 'fault.inject', code='none')                               # remove the cause
        r.tap('fault_ind')
        self.assertIn('Error at pump', r.lines())
        r.ok('Acknowledge')
        self.assertEqual(r.val('faults.list'), [])
        self.assertEqual(r.widget('fault_ind')['fill'], 'neutral')
        self.assertTrue(r.tap('op_start'))
        DONE_TASKS.add('acknowledge an error')


class ControllerFunctions(unittest.TestCase):
    def test_lock_and_reboot(self):
        r = Rig()
        r.press('onoff')                                                        # short press while on: lock
        self.assertTrue(r.val('unit.power', 'locked'))
        self.assertEqual(r.text('lock'), 'LOCK')
        self.assertFalse(r.tap('op_start'))
        r.press('onoff')
        self.assertTrue(r.tap('op_start'))
        r.press('onoff', 10.0)                                                  # ~10 s: reboot, the application stops
        self.assertEqual(r.frame()['screen'], 'boot')
        self.assertFalse(r.val('vacuum.running'))
        r.h.step(31)
        self.assertEqual(r.overlay()['title'], 'Data storage')

    def test_graph_menu_swipes_and_legend(self):
        r = Rig()
        r.tap('op_start')
        r.step(120)
        r.swipe('left')
        self.assertEqual(r.page(), 'graph')
        plot = r.widget('graph_plot')
        self.assertGreater(len(plot['series'][0]['points']), 20)
        self.assertEqual(plot['series'][0]['points'][0], [0.0, 1013.25])
        r.tap('legend_vsc1')
        self.assertEqual(r.widget('graph_plot')['series'], [])
        r.swipe('right')
        self.assertEqual(r.page(), 'process')
        r.swipe('right')
        self.assertEqual(r.page(), 'menu')
        r.tap('to_process')
        self.assertEqual(r.page(), 'process')

    def test_favorites_and_context_menu(self):
        r = Rig()
        r.tap('app_menu')
        r.row('app_list', 'Gel drying', duration_s=3.0)                        # press and hold ~3 s: context menu
        self.assertEqual(r.overlay()['title'], 'Gel drying')
        r.ok('Add to favorites')
        self.assertTrue(next(a for a in r.val('app.library') if a['id'] == 9)['favorite'])
        r.row('app_list', 'Gel drying', duration_s=3.0)
        r.ok('Show description')
        self.assertIn('Application ID: 9', r.lines())
        r.ok()
        r.tap('cancel')
        r.tap('to_process')
        r.tap('op_fav')
        self.assertEqual(r.page(), 'favorites')
        r.row('fav_list', 'Gel drying')
        self.assertEqual(r.val('app.selected'), 9)

    def test_multi_step_applications_continue_ramp_vent(self):
        r = Rig()
        r.select_app('Pump down and hold')
        r.tap('op_start')
        self.assertEqual(r.val('vacuum.mode'), 'pump_down')
        r.step(100)
        r.call(r.A, 'app.set_parameter', app_id=4, step=0, param='duration_s', value=None)
        r.step(1)
        self.assertEqual(r.text('continue'), 'Continue with Hold vacuum')
        r.tap('continue')
        self.assertEqual(r.val('app.run', 'step'), 1)
        self.assertEqual((r.val('vacuum.mode'), r.val('vacuum.target_pressure_pa')), ('pressure_control', 1000.0))
        r.tap('op_stop')
        r.select_app('Gel drying')
        p0 = r.val('vacuum.pressure_pa')
        r.tap('op_start')
        r.step(300)
        self.assertAlmostEqual(r.val('vacuum.target_pressure_pa'), p0 + (5000 - p0) * 0.5, delta=20)   # ramp half-way from the start pressure
        r.step(300 + 3600)
        self.assertEqual(r.val('app.run', 'step'), 2)                           # the vent step
        r.step(120)
        self.assertFalse(r.val('app.run', 'running'))                          # process end after venting
        self.assertAlmostEqual(r.val('vacuum.pressure_pa'), 101325.0, delta=1.0)

    def test_application_editor(self):
        r = Rig()
        r.swipe('right')
        r.tap('m_editor')
        r.tap('ed_new')
        r.row('ed_palette', 'Pump down')
        r.row('ed_palette', 'Vacuum control')
        r.row('ed_palette', 'Loop')
        rows = [' '.join(c) for c in r.widget('ed_steps')['cells']]
        self.assertTrue(any('2  Vacuum control' in x for x in rows), rows)
        r.row('ed_steps', 'Pressure set-point')
        r.type_in('300')
        r.row('ed_steps', '1  Pump down')
        r.ok('Remove')
        self.assertEqual([s['type'] for s in r.val('editor.draft', 'steps')], ['vacuum_control', 'loop'])
        r.enter('ed_desc', 'test')
        r.tap('ed_save')
        self.assertIn('Save as', r.lines())                                     # a new application is saved with Save as
        r.ok()
        r.enter('ed_save_as', 'mine')
        lib = r.val('app.library')
        self.assertEqual((lib[-1]['id'], lib[-1]['name'], lib[-1]['description']), (100, 'mine', 'test'))
        self.assertEqual(lib[-1]['steps'][0]['params']['setpoint_pa'], 30000.0)
        r.tap('ed_templates')
        r.row('tpl_list', 'Vacuum control')
        r.tap('ed_save')
        self.assertIn('Save as', r.lines())                                     # standard applications stay unchanged
        r.ok()
        r.tap('cancel')
        r.tap('m_apps')
        r.row('app_list', 'mine')
        self.assertEqual(r.text('title'), 'mine')

    def test_settings_unit_orientation_handedness_energy_saving(self):
        r = Rig()
        r.swipe('right')
        r.tap('m_settings')
        r.tap('s_regional')
        r.choose('Pressure unit', 'Torr')
        r.tap('confirm')
        r.tap('s_display')
        r.choose('Orientation', '180°')
        r.choose('Handedness', 'Left-handed')
        r.choose('Energy saving mode', '5 min')
        r.tap('confirm')
        r.tap('to_process')
        self.assertEqual(r.text('unit'), 'Torr')
        self.assertEqual(r.text('actual'), '760')
        self.assertEqual(r.frame()['displays'][D]['rotation'], 180)
        self.assertTrue(r.has('sp_up_l') and not r.has('sp_up'))
        r.step(301)
        self.assertEqual(r.frame()['displays'][D]['brightness'], 20)           # dimmed after 5 min without a touch
        self.assertFalse(r.tap('op_start'))                                     # the first touch only wakes the display
        self.assertEqual(r.frame()['displays'][D]['brightness'], 80.0)
        self.assertFalse(r.val('app.run', 'running'))

    def test_orientation_and_language_are_stored_and_kept(self):
        r = Rig()
        r.swipe('right')
        r.tap('m_settings')
        r.tap('s_display')
        r.choose('Orientation', '90°')
        self.assertEqual(r.val('settings.values', 'orientation'), 90)
        self.assertEqual(r.frame()['displays'][D]['rotation'], 90)
        self.assertEqual(r.h.panel(INST).render_image(D).size, (800, 480))     # landscape: the frame turned clockwise
        self.assertIn('90°', [c[1] for c in r.widget('form')['cells']])
        r.tap('confirm')                                                        # touches follow the turned screen
        self.assertEqual(r.page(), 'settings')
        with self.assertRaises(AssertionError):                                 # a portrait-only point is off the landscape screen
            r.call(r.A, 'panel.touch', display_id=D, x=300, y=700)
        r.tap('s_regional')
        r.choose('Language', 'Deutsch')
        self.assertEqual(r.val('settings.values', 'language'), 'Deutsch')
        self.assertIn('Deutsch', [c[1] for c in r.widget('form')['cells']])
        self.assertEqual(r.text('title'), 'Regional settings')                 # texts stay English
        r.tap('confirm')
        r.tap('to_process')
        r.tap('op_start')
        self.assertTrue(r.val('app.run', 'running'))
        r.tap('op_stop')
        r.switch('0')                                                           # power-off and on: the settings are kept
        r.power_up()
        self.assertEqual((r.val('settings.values', 'orientation'), r.val('settings.values', 'language')), (90, 'Deutsch'))
        self.assertEqual(r.frame()['displays'][D]['rotation'], 90)
        r.call(r.A, 'settings.configure', orientation=270)
        r.tap('app_menu')
        self.assertEqual(r.page(), 'apps')
        self.assertEqual(r.h.panel(INST).render_image(D).size, (800, 480))
        r.call(r.A, 'settings.factory_reset')
        self.assertEqual((r.val('settings.values', 'orientation'), r.val('settings.values', 'language')), (0, 'English'))
        self.assertEqual(r.h.panel(INST).render_image(D).size, (480, 800))

    def test_autostart_and_venting_valve_settings(self):
        r = Rig()
        r.swipe('right')
        r.tap('m_settings')
        r.tap('s_basic')
        r.choose('Autostart', 'On')
        r.choose('Use venting valve when set-point changes', 'On')
        r.tap('confirm')
        r.tap('to_process')
        r.tap('op_start')
        r.step(120)
        r.enter('actual', '300')                                                # set-point raised: the valve vents up to it
        r.step(30)
        self.assertAlmostEqual(r.val('vacuum.pressure_pa'), 30000.0, delta=300)
        r.switch('0')                                                           # power failure
        r.switch('I')
        self.assertEqual(r.frame()['screen'], 'boot')                          # autostart: the controller starts by itself
        r.h.step(31)
        self.assertTrue(r.val('app.run', 'running'))                           # and the application continues
        r.ok('Confirm')
        r.swipe('right')
        r.tap('m_settings')
        r.tap('s_admin')
        r.tap('a_bus')
        r.form_row('Venting valve')
        r.ok('Deactivated')
        r.tap('to_process')
        self.assertFalse(r.has('op_vent'))                                      # VENT only with an activated valve
        self.assertEqual(r.call(r.A, 'vent.control', expect='rejected', action='brief')['error']['code'], 'UNSUPPORTED')

    def test_administration_clock_reset_usb_components(self):
        r = Rig()
        r.call(r.A, 'favorites.set', app_id=6, favorite=True)
        r.swipe('right')
        r.tap('m_settings')
        r.tap('s_admin')
        r.tap('a_update')
        self.assertIn('No USB flash drive', r.lines())
        r.ok()
        r.tap('a_network')
        self.assertIn('No Ethernet', r.lines())
        r.ok()
        r.tap('a_bus')
        r.tap('b_recog')
        self.assertIn('VS-C 1', r.lines())
        r.ok()
        r.tap('cancel')
        r.tap('a_clock')
        self.assertEqual(r.page(), 'datetime')
        r.call(r.A, 'clock.set', date='2026-03-01', time='08:30')
        self.assertTrue(r.text('clock').startswith('01.03.2026 08:30'))
        r.tap('confirm')
        r.tap('a_reset')
        r.ok('Reset')
        self.assertFalse(next(a for a in r.val('app.library') if a['id'] == 6)['favorite'])

    def test_sensor_adjustment_against_an_evaluator_error(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', gain=0.02, offset_pa=50.0)
        r.step(1)
        self.assertEqual(r.val('vacuum.indicated_pressure_pa'), round(101325 * 1.02 + 50))
        r.swipe('right')
        r.tap('m_settings')
        r.tap('s_admin')
        r.tap('a_bus')
        r.tap('b_adjust')
        self.assertIn('Atmosphere', r.text('adj_hint'))
        r.enter('adj_value', '1013.25')                                         # atmospheric adjustment
        self.assertAlmostEqual(r.val('vacuum.indicated_pressure_pa'), 101325.0, delta=1)
        r.call(r.A, 'app.select', app_id=0)
        r.call(r.A, 'app.start')
        r.step(600)                                                             # pump down to the ultimate (< 20 mbar)
        r.call(r.A, 'app.stop')
        self.assertIn('Vacuum', r.text('adj_hint'))
        r.enter('adj_value', '2')                                               # reference 2 mbar
        self.assertAlmostEqual(r.val('vacuum.indicated_pressure_pa'), 200.0, delta=1)
        self.assertAlmostEqual(r.val('vacuum.pressure_pa'), 200.0, delta=1)

    def test_data_logger_and_service_counter(self):
        r = Rig()
        r.swipe('right')
        r.tap('m_logger')
        r.choose('Storage interval', '10 s')
        r.tap('to_process')
        r.tap('op_start')
        r.step(100)
        self.assertEqual(r.val('logger.state'), {'records': 10, 'files': 1})
        r.swipe('right')
        r.tap('m_service')
        r.form_row('Reminder at (h)')
        r.type_in('1')
        r.choose('Maintenance reminder', 'On')
        r.step(3600)
        self.assertIn('1.0 h', r.text('sv_hours'))
        self.assertEqual(r.widget('fault_ind')['fill'], 'yellow')
        self.assertTrue(r.val('app.run', 'running'))                           # a warning: the process continues
        r.tap('fault_ack')
        self.assertEqual(r.val('faults.list'), [])
        self.assertEqual(r.val('settings.values', 'maintenance_h'), 2.0)


class RefusalsAndRender(unittest.TestCase):
    def test_refusals(self):
        r = Rig(chamber=False)
        r.tap('op_start')
        self.assertIn('Start not possible', r.lines())
        r.ok()
        r.select_app('Pump down')
        self.assertFalse(r.widget('sp_up')['enabled'])                          # no set-point in a pump-down step
        self.assertEqual(r.call(r.A, 'app.set_setpoint', expect='rejected', pressure_pa=5000.0)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='pump')['error']['code'], 'ACCESS_DENIED')
        b = Rig()
        b.tap('op_start')
        self.assertEqual(b.call(b.A, 'app.select', expect='rejected', app_id=0)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(b.call(b.A, 'cooling.set_flow_rate', expect='unsupported', flow_m3_s=1e-6)['error']['code'], 'UNSUPPORTED')

    def test_render_determinism_and_evidence(self):
        a, b = Rig(), Rig()
        self.assertEqual(a.h.panel(INST).png_bytes(D)[0], b.h.panel(INST).png_bytes(D)[0])
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        r.tap('op_start')
        r.step(90)
        p = r.h.panel(INST)
        renders = {'home': p.save_png(D, OUT / 'home.png')}
        r.swipe('right')
        renders['menu'] = p.save_png(D, OUT / 'menu.png')
        r.tap('to_process')
        r.tap('app_icon')
        renders['params'] = p.save_png(D, OUT / 'params.png')
        r.tap('confirm')
        r.swipe('left')
        renders['graph'] = p.save_png(D, OUT / 'graph.png')
        r.swipe('right')
        r.call(r.E, 'fault.inject', code='sensor')
        r.step(1)
        renders['error'] = p.save_png(D, OUT / 'error.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')


class PanelEqualsDirectAndContract(unittest.TestCase):
    def test_panel_equals_direct(self):
        a, b = Rig(), Rig(on=False)
        b.call(b.A, 'mains.set', on=True)
        b.call(b.A, 'controller.power', on=True)
        b.h.step(31)
        a.select_app('Vacuum concentrator')
        a.enter('actual', '25')
        a.tap('op_start')
        b.call(b.A, 'app.select', app_id=8)
        b.call(b.A, 'app.set_setpoint', pressure_pa=2500.0)
        b.call(b.A, 'app.start')
        for h in (a.h, b.h):
            h.step(120)
        for sid in ('app.selected', 'app.run', 'vacuum.target_pressure_pa', 'vacuum.mode', 'vacuum.running', 'vacuum.pressure_pa', 'unit.power'):
            self.assertEqual(a.val(sid), b.val(sid), sid)

    def test_metadata_and_contract(self):
        meta = rt.export_contract_metadata()['asset_types'][rt.ASSET_TYPE_ID]
        self.assertEqual(set(meta['device_capabilities']), set(rt.PC3001_DEVICE_ARGS))
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.turn', 'panel.switch', 'panel.touch', 'panel.capture', 'panel.frame'})
        for name, cap in {**meta['device_capabilities'], **meta['panel_capabilities']}.items():
            self.assertEqual(validate_value(cap['example_arguments'][0], cap['input_schema']), [], name)
        c = json.loads(CONTRACT.read_text())
        if 'chemistry_lab.panel_ui' in c['extensions']:
            for name in list(meta['device_capabilities']) + list(meta['panel_capabilities']):
                self.assertIn(name, c['capabilities'], name)
            self.assertEqual(c['asset_version'], rt.PC3001_ASSET_VERSION)
        r = Rig()
        caps = r.A.describe(instance_id=INST)['capabilities']
        self.assertIn('app.start', caps)
        self.assertNotIn('fault.inject', caps)
        self.assertNotIn('sensor.error', json.dumps(r.A.describe(instance_id=INST)))


class SyncAudit20261019(unittest.TestCase):
    """2026-10-19 front-panel sync audit: the controller regulates on its own reading (rule 1, the sensor error and the stored adjustment act
    on control as on the display), and panel gestures act as the calling session (rule 2)."""

    def controlled(self, gain=0.0, offset=0.0, direct=False):
        r = Rig(leak=1e-4)
        r.call(r.E, 'sensor.configure_error', gain=gain, offset_pa=offset)
        if direct:                                                             # the CHEM-188 commands use the same controller
            r.call(r.A, 'vacuum.set_mode', mode='pressure_control')
            r.call(r.A, 'vacuum.set_pressure', pressure_pa=10000.0)
            r.call(r.A, 'vacuum.start')
        else:
            r.call(r.A, 'app.select', app_id=6)                                # Vacuum control, set point 100 mbar
            r.call(r.A, 'app.start')
        r.step(900)
        return r

    def test_the_controller_holds_its_reading_at_the_set_point(self):
        r = self.controlled()
        self.assertAlmostEqual(r.val('vacuum.pressure_pa'), 10000.0, delta=1e-6)   # defaults: unchanged
        for gain, offset, truth in ((0.1, 0.0, 10000.0 / 1.1), (0.0, 1000.0, 9000.0)):
            for direct in (False, True):
                r = self.controlled(gain, offset, direct)
                self.assertAlmostEqual(r.val('vacuum.indicated_pressure_pa'), 10000.0, delta=1.0)
                self.assertAlmostEqual(r.val('vacuum.pressure_pa'), truth, delta=1.0)

    def test_the_stored_adjustment_moves_the_controlled_pressure(self):
        r = Rig(leak=1e-4)
        inst = r.h._instances[INST]
        inst.state['sensor.adjustment'].update(k=1.0, z_off_pa=500.0)          # as after a vacuum zero adjustment (procedure tested above)
        r.call(r.A, 'app.select', app_id=6)
        r.call(r.A, 'app.start')
        r.step(900)
        self.assertAlmostEqual(r.val('vacuum.indicated_pressure_pa'), 10000.0, delta=1.0)
        self.assertAlmostEqual(r.val('vacuum.pressure_pa'), 10500.0, delta=1.0)

    def test_vent_to_a_set_point_stops_at_the_reading(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', gain=0.0, offset_pa=1000.0)
        r.call(r.A, 'app.select', app_id=0)
        r.call(r.A, 'app.start')
        r.step(600)
        r.call(r.A, 'app.stop')
        inst = r.h._instances[INST]
        inst.state['vacuum.target_pressure_pa'] = 30000.0
        inst.state['vent.state']['mode'] = 'setpoint'
        r.step(60)
        self.assertAlmostEqual(r.val('vacuum.indicated_pressure_pa'), 30000.0, delta=60.0)
        self.assertAlmostEqual(r.val('vacuum.pressure_pa'), 29000.0, delta=60.0)

    def test_panel_gesture_acts_as_the_calling_session(self):
        r = Rig()
        seen = []
        orig = rt.VacuumSession._invoke_checked

        def spy(sess, inst, cap, args, rid):
            if not cap.startswith('panel.'):
                seen.append((sess._VacuumSession__principal_id, cap))
            return orig(sess, inst, cap, args, rid)
        with mock.patch.object(rt.VacuumSession, '_invoke_checked', spy):
            r.press('onoff')
        self.assertTrue(seen)
        self.assertEqual({p for p, _ in seen}, {'agent'})
        self.assertIsNone(r.h._panel_caller)


class Sweep20261019(unittest.TestCase):
    """2026-10-19 consistency sweep: the process started by one agent cannot be stopped, powered off or changed by another agent (panel and
    direct path, ACCESS_DENIED, state unchanged, pop-up; the controller ON/OFF key and power.set are soft and refused); the rocker switch (hard),
    the gas ballast and the evaluator are not restricted; the owner stops its own process;
    the frequency converter error of the manual (p.88) can be injected."""

    def test_another_agents_process_cannot_be_stopped_or_changed(self):
        r = Rig()
        r.h.register_principal('agent_b')
        B = r.h.session('agent_b')
        r.tap('op_start')                                                       # agent A starts the application
        r.step(5)
        self.assertEqual(r.h._instances[INST].g03['run_owners'], {'process': 'agent'})
        before = copy.deepcopy(r.h._instances[INST].state)
        x, y, w, h = r.widget('op_stop')['rect']
        self.assertFalse(r.call(B, 'panel.touch', display_id=D, x=x + w // 2, y=y + h // 2)['accepted'])   # B's Stop: refused
        self.assertEqual(r.overlay()['title'], 'Message')
        self.assertIn('started by another user', r.lines())
        r.ok()
        for cap, args in (('vacuum.stop', {}), ('app.stop', {}), ('vacuum.set_pressure', dict(pressure_pa=5000.0)), ('power.set', dict(enabled=False)),
                          ('controller.power', dict(on=False)), ('vent.control', dict(action='brief'))):
            res = r.call(B, cap, expect='rejected', **args)
            self.assertEqual(res['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h._instances[INST].state, before)                     # state unchanged
        self.assertTrue(r.tap('op_stop'))                                        # A stops its own process
        self.assertFalse(r.val('vacuum.running'))
        self.assertNotIn('run_owners', r.h._instances[INST].g03)
        x, y, w, h = r.widget('op_start')['rect']
        self.assertTrue(r.call(B, 'panel.touch', display_id=D, x=x + w // 2, y=y + h // 2)['accepted'])   # B starts its own process
        self.assertEqual(r.h._instances[INST].g03['run_owners'], {'process': 'agent_b'})
        r.call(r.E, 'vacuum.set_pressure', pressure_pa=5000.0)                   # the evaluator is not restricted
        self.assertTrue(r.switch('0'))                                           # the rocker is a hard switch (lead clarification 10-19):
        self.assertFalse(r.val('unit.power', 'mains'))                           # A may switch it off, B's process ends as on a power loss
        self.assertFalse(r.val('vacuum.running'))
        self.assertNotIn('run_owners', r.h._instances[INST].g03)

    def test_frequency_converter_error(self):
        r = Rig()
        r.tap('op_start')
        r.call(r.E, 'fault.inject', code='frequency_converter')
        r.step(1)
        self.assertFalse(r.val('app.run', 'running'))                          # red error: the process stops (p.86)
        self.assertEqual(r.text('fault_pop'), 'Frequency converter (FC) error')
        r.call(r.E, 'fault.inject', code='none')
        r.tap('fault_ind')
        r.ok('Acknowledge')
        self.assertEqual(r.val('faults.list'), [])


if __name__ == '__main__':
    unittest.main()
