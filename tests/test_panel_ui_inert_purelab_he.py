"""front-panel tests for CHEM-189 inert_purelab_he_2gb (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_vacuum.py):
python3 -m pytest tests/test_panel_ui_inert_purelab_he.py -q

Every gesture goes through the session API (panel.touch / panel.press / panel.turn / panel.switch); the screen is checked in the structured frame
(panel.frame, evaluator) and in the rendered pixels.  Themes: the seven key procedures of the menu survey; the other HMI functions (pump timer,
blower O2 speed and timer, manual and O2-level purge, regeneration abort and blower restart, alarms with banner / acknowledge / history, trend,
configuration, EVAC / REFILL, light switch); login gating and refusals; panel = direct commands; render determinism and the evidence PNGs;
contract entries.  O2 / H2O / pressure readings are host-supplied observations, as in the CHEM-189 runtime."""
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

INST = 'box'
OUT = ROOT / 'outputs/panel_ui/inert_purelab_he'
CONTRACT = rt.INERT_PURELAB_ASSET_DIR / 'asset.contract.json'
D = 'screen'
SURVEY_TASKS = ['log in to the hmi', 'set the working box pressure window', 'start circulation and set blower speed',
                'bring an item in with the automatic antechamber', 'regenerate a single purifier column', 'run a timed purge',
                'adjust box pressure with the foot pedals']
DONE_TASKS = set()


class Rig:
    def __init__(self, on=True, login=True, source=True):
        self.h = rt.InertPureLabHost()
        self.h.create_instance(INST)
        self.h.register_principal('agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        if source:
            self.h.connect_vacuum_source(INST, 'shared_pc3001')
        if on:
            assert self.switch('main_power', 'on')
            if login:
                self.login()

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

    def boundary(self, pressure=101325.0, source=True, gas=True):
        self.h.set_external_boundary(INST, pressure_pa=pressure, source_running=source, gas_supply_ready=gas, evidence='test boundary')

    def readings(self, o2_ppm=0.8, h2o_ppm=0.5, mbar=8.2):
        self.h.set_observations(INST, {'atmosphere.oxygen_mole_fraction': o2_ppm * 1e-6, 'atmosphere.water_mole_fraction': h2o_ppm * 1e-6,
                                       'pressure.indicated_gauge_pa': mbar * 100.0}, evidence='test analyzers')

    # ---- gestures (session API only)
    def switch(self, which, pos):
        return self.call(self.A, 'panel.switch', input_id=which, position=pos)['accepted']

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

    def text(self, wid):
        return self.widget(wid)['text']

    def touch_at(self, rect):
        x, y, w, h = rect
        return self.call(self.A, 'panel.touch', display_id=D, x=x + w // 2, y=y + h // 2)['accepted']

    def tap(self, wid):
        return self.touch_at(self.widget(wid)['rect'])

    def overlay(self):
        return self.frame()['displays'][D].get('overlay')

    def key(self, label):
        for b in self.overlay()['buttons']:
            if b['label'] == label and b['id'] != 'field':
                return self.touch_at(b['rect'])
        raise AssertionError(label)

    def enter(self, wid, text):
        assert self.tap(wid), wid
        for ch in text:
            self.key(ch)
        return self.key('Enter')

    def ok(self):
        return self.key('OK')

    def login(self):
        self.tap('to_login')
        assert self.enter('login_user', str(rt.INERT_LOGIN_CODE))
        self.tap('to_main')
        assert self.page() == 'main'

    def go(self, *buttons):
        for b in buttons:
            assert self.tap(b), b


class KeyProcedures(unittest.TestCase):
    def test_log_in_to_the_hmi(self):
        r = Rig(login=False)
        self.assertEqual(r.page(), 'status')                                   # status screen, no interaction before login
        self.assertFalse(r.widget('pump_btn')['enabled'])
        self.assertFalse(r.tap('pump_btn'))
        self.assertFalse(r.tap('alarms_btn'))
        r.tap('to_login')
        r.enter('login_user', '1234')
        self.assertEqual(r.overlay()['lines'], ['Wrong password.'])
        r.ok()
        self.assertEqual(r.val('hmi.user'), 'none')
        r.enter('login_user', '7990')
        self.assertEqual(r.val('hmi.user'), 'user')
        self.assertEqual(r.text('login_user'), 'Accepted')                     # the button turns green and shows Accepted
        r.tap('to_main')
        self.assertEqual(r.page(), 'main')
        self.assertTrue(r.widget('to_regen')['enabled'])
        r.go('to_login', 'logout')
        self.assertEqual((r.page(), r.val('hmi.user')), ('status', 'none'))
        DONE_TASKS.add('log in to the hmi')

    def test_set_the_working_box_pressure_window(self):
        r = Rig()
        r.go('to_config')
        r.enter('max_sp', '12')
        r.enter('min_sp', '10')
        self.assertEqual((r.val('system.config', 'min_sp_pa'), r.val('system.config', 'max_sp_pa')), (1000.0, 1200.0))
        self.assertEqual(r.val('pressure.target_gauge_pa'), 1100.0)
        self.assertEqual(r.text('min_sp'), '10.0')
        r.enter('min_sp', '13')                                                # above the maximum: refused
        self.assertEqual(r.overlay()['lines'], ['The minimum must be below the maximum.'])
        r.ok()
        r.enter('max_sp', '20')                                                # outside +/-15 mbar: the keypad says so
        self.assertEqual(r.overlay()['error'], 'Out of range')
        r.key('Cancel')
        self.assertEqual(r.val('system.config', 'max_sp_pa'), 1200.0)
        r.h.set_process_truth(INST, {'pressure.box_gauge_pa': 300.0}, evidence='test truth')
        r.boundary()
        r.step(10)                                                              # the PLC adds working gas up to the minimum
        self.assertAlmostEqual(r.val('pressure.box_gauge_pa'), 1000.0)
        DONE_TASKS.add('set the working box pressure window')

    def test_start_circulation_and_set_blower_speed(self):
        r = Rig()
        self.assertTrue(r.tap('blower_btn'))                                   # Blower START on the main screen
        self.assertTrue(r.val('purification.enabled'))
        self.assertEqual(r.text('v_blower'), 'ON')
        self.assertEqual(r.text('blower_btn'), 'STOP')
        r.go('to_blower')
        r.enter('speed', '70')
        self.assertEqual(r.val('blower.state', 'speed_set_pct'), 70.0)
        self.assertEqual(r.text('v_cur'), '70')
        r.enter('speed', '50')
        self.assertEqual(r.val('blower.state', 'speed_pct'), 50.0)
        DONE_TASKS.add('start circulation and set blower speed')

    def test_bring_an_item_in_with_the_automatic_antechamber(self):
        r = Rig()
        r.go('to_antech', 'to_ante_cfg')
        r.enter('evac_sp', '3')                                                # above the 2 mbar floor of the shared source
        self.assertEqual(r.val('antechamber.config', 'evacuate_sp_pa'), 300.0)
        r.enter('cycles', '3')
        self.assertEqual(r.val('antechamber.target_cycles'), 3)
        r.tap('to_antech')
        r.call(r.A, 'antechamber.outer_door.set', open=True)                  # outer door open, item in, door closed
        r.call(r.A, 'antechamber.outer_door.set', open=False)
        r.boundary(101325.0)
        self.assertTrue(r.tap('auto'))
        self.assertTrue(r.val('antechamber.control', 'auto'))
        self.assertEqual(r.widget('auto_lamp')['color'], 'green')
        self.assertTrue(r.val('pump.state', 'running'))                       # the pump is powered on as required
        for cycle in range(1, 4):
            r.boundary(250.0)
            r.step(1)
            self.assertEqual(r.val('antechamber.phase'), 'backfilling')
            need = 60 if cycle == 3 else 20
            r.boundary(101325.0)
            r.step(need - 2)
            self.assertEqual(r.val('antechamber.completed_cycles'), cycle - 1)
            r.step(2)
            self.assertEqual(r.val('antechamber.completed_cycles'), cycle)
            self.assertEqual(r.text('v_cycle'), str(cycle))
        self.assertEqual(r.val('antechamber.phase'), 'completed')
        self.assertFalse(r.val('antechamber.control', 'auto'))
        r.call(r.A, 'antechamber.inner_door.set', open=True)                  # inner door open: take the item
        DONE_TASKS.add('bring an item in with the automatic antechamber')

    def test_regenerate_a_single_purifier_column(self):
        r = Rig()
        r.tap('blower_btn')
        r.go('to_regen')
        r.tap('regen_start')
        self.assertEqual(r.overlay()['lines'], ['Turn off the Blower on the Main screen first.'])
        r.ok()
        r.go('to_main', 'blower_btn', 'to_regen')                             # turn off the blower
        self.assertFalse(r.val('purification.enabled'))
        self.assertFalse(r.widget('valves_yes')['enabled'])
        self.assertTrue(r.tap('regen_start'))
        self.assertTrue(r.tap('valves_yes'))                                   # Are the Column Valves Closed? YES
        self.assertTrue(r.call(r.A, 'panel.turn', input_id='regen_needle', detents=56)['accepted'])
        self.assertEqual(r.val('regen.state', 'flow_scfh'), 28.0)
        self.assertTrue(r.tap('flow_yes'))                                     # Is Flow OK? YES: Step 1 Heating
        self.assertEqual(r.val('regen.state', 'status'), 'heat')
        self.assertEqual(r.text('v_state'), 'Heating')
        r.step(185 * 60)
        self.assertEqual(r.val('regen.state', 'status'), 'heat_purge')
        self.assertEqual(r.text('v_step'), '5m 00s')
        r.step((30 + 180) * 60)
        self.assertEqual(r.val('regen.state', 'status'), 'evacuate')
        self.assertTrue(r.val('pump.state', 'running'))
        r.tap('to_main')
        self.assertEqual(r.text('v_regen'), 'Evacuating 5m')
        r.step(480 * 60)
        self.assertEqual(r.val('regen.state', 'status'), 'idle')
        self.assertAlmostEqual(r.val('regen.state', 'last_end_s'), r.val('regen.state', 'last_end_s'))
        self.assertIsNotNone(r.val('regen.state', 'last_end_s'))
        self.assertFalse(r.val('purification.enabled'))                       # Regen Blower Restart disabled
        self.assertEqual(r.call(r.A, 'regeneration.start', expect='unsupported')['error']['code'], 'UNSUPPORTED')   # CHEM-189 command unchanged
        DONE_TASKS.add('regenerate a single purifier column')

    def test_run_a_timed_purge(self):
        r = Rig()
        r.tap('blower_btn')
        r.go('to_purge')
        r.enter('timed_min', '2')
        r.boundary()
        self.assertTrue(r.tap('timed'))
        self.assertTrue(r.val('purge.enabled'))
        self.assertFalse(r.val('purification.enabled'))                       # the blower stops during a purge
        self.assertEqual(r.widget('valve_lamp')['color'], 'green')
        r.step(61)
        self.assertEqual(r.text('v_count'), '1m 01s')
        r.step(60)
        self.assertFalse(r.val('purge.enabled'))                               # the valve closes at the limit
        self.assertTrue(r.val('purification.enabled'))                        # and the blower restarts
        self.assertEqual(r.text('timed'), 'START')
        DONE_TASKS.add('run a timed purge')

    def test_adjust_box_pressure_with_the_foot_pedals(self):
        r = Rig()
        r.h.set_process_truth(INST, {'pressure.box_gauge_pa': 900.0}, evidence='test truth')
        r.boundary()
        self.assertTrue(r.press('pedal_right', 2.0))                           # add gas
        self.assertAlmostEqual(r.val('pressure.box_gauge_pa'), 1100.0)
        r.press('pedal_right', 5.0)                                            # not above the working maximum
        self.assertAlmostEqual(r.val('pressure.box_gauge_pa'), 1200.0)
        self.assertFalse(r.press('pedal_left', 2.0))                           # the vacuum pump is off
        r.tap('pump_btn')
        self.assertTrue(r.press('pedal_left', 3.0))
        self.assertAlmostEqual(r.val('pressure.box_gauge_pa'), 900.0)
        r.press('pedal_left', 10.0)                                            # not below the working minimum
        self.assertAlmostEqual(r.val('pressure.box_gauge_pa'), 500.0)
        self.assertIsNone(r.val('pressure.indicated_gauge_pa'))               # readings stay host-supplied
        DONE_TASKS.add('adjust box pressure with the foot pedals')


class HmiFunctions(unittest.TestCase):
    def test_pump_timer_and_pump_on_as_required(self):
        r = Rig()
        r.go('to_config', 'pump_timer')
        self.assertTrue(r.val('system.config', 'pump_timer'))
        r.go('to_main', 'pump_btn')
        r.step(599)
        self.assertTrue(r.val('pump.state', 'running'))
        r.step(2)
        self.assertFalse(r.val('pump.state', 'running'))                      # powered down after 10 minutes
        r.go('to_antech', 'evac')                                              # EVAC needs the pump: on again
        self.assertTrue(r.val('antechamber.control', 'evac'))
        self.assertTrue(r.val('pump.state', 'running'))
        r.step(700)
        self.assertTrue(r.val('pump.state', 'running'))
        r.tap('refill')                                                        # REFILL closes EVAC
        self.assertEqual((r.val('antechamber.control', 'evac'), r.val('antechamber.control', 'refill')), (False, True))
        r.tap('refill')
        r.call(r.A, 'antechamber.outer_door.set', open=True)
        r.tap('evac')
        self.assertEqual(r.overlay()['lines'], ['Close both antechamber doors and check the vacuum and the gas supply.'])

    def test_blower_o2_speed_and_timer(self):
        r = Rig()
        r.tap('blower_btn')
        r.go('to_blower')
        r.readings(o2_ppm=25.0)
        r.step(1)
        self.assertEqual((r.text('v_cur'), r.text('v_mode')), ('100', 'AUTO'))  # O2 above 10 ppm: faster
        r.tap('o2_speed')
        self.assertEqual((r.text('v_cur'), r.text('v_mode')), ('50', 'MANUAL'))
        r.tap('timer')
        r.enter('speed', '90')
        r.step(1799)
        self.assertEqual(r.val('blower.state', 'speed_set_pct'), 90.0)
        r.step(2)
        self.assertEqual(r.val('blower.state', 'speed_set_pct'), 50.0)        # back to 50 % after 30 minutes

    def test_manual_and_o2_level_purge(self):
        r = Rig()
        r.go('to_purge')
        r.tap('manual')
        self.assertEqual(r.overlay()['lines'], ['No inert gas supply: the purge valve cannot open.'])
        r.ok()
        r.boundary()
        r.tap('manual')
        self.assertTrue(r.val('purge.enabled'))
        r.step(30)
        r.tap('manual')
        self.assertFalse(r.val('purge.enabled'))
        r.tap('o2_level')
        r.enter('o2_sp', '20')
        r.readings(o2_ppm=35.0)
        r.step(1)
        self.assertTrue(r.val('purge.control', 'o2_active'))
        self.assertTrue(r.val('purge.enabled'))
        r.readings(o2_ppm=15.0)
        r.step(1)
        self.assertFalse(r.val('purge.enabled'))

    def test_regen_abort_window_and_blower_restart(self):
        r = Rig()
        r.go('to_blower', 'regen_restart', 'to_main', 'to_regen')
        r.tap('regen_start')
        r.step(120)
        self.assertTrue(r.tap('regen_abort'))                                  # within 5 minutes
        self.assertEqual(r.val('regen.state', 'status'), 'idle')
        r.go('regen_start', 'valves_yes', 'flow_yes')
        r.step(360)
        r.tap('regen_abort')
        self.assertEqual(r.overlay()['lines'], ['A regeneration can be aborted only within the first 5 minutes.'])
        r.ok()
        r.tap('to_timers')
        r.enter('v_heat', '100')
        self.assertEqual(r.overlay()['lines'], ['A regeneration is already running.'])
        r.ok()
        r.step(870 * 60)
        self.assertEqual(r.val('regen.state', 'status'), 'idle')
        self.assertTrue(r.val('purification.enabled'))                        # Regen Blower Restart enabled
        r.enter('v_heat', '100')
        self.assertEqual(r.val('regen.timers_min', 'heat'), 100.0)

    def test_alarms_banner_acknowledge_history(self):
        r = Rig()
        r.go('alarms_btn', 'o2_enable')
        r.enter('o2_sp', '10')
        r.tap('ante_enable')
        r.readings(o2_ppm=15.0)
        r.step(1)
        self.assertEqual(r.val('alarms.state', 'o2.state'), 'ALARM')
        self.assertEqual(r.text('alarm_banner'), 'O2 Level Alarm')            # pop-up banner on the current screen
        self.assertEqual(r.text('o2_state'), 'ALARM')
        r.tap('ack')
        self.assertIsNone(r.val('alarms.state', 'banner'))
        self.assertEqual(r.val('alarms.state', 'o2.state'), 'ACKNOWLEDGED')
        r.readings(o2_ppm=2.0)
        r.step(1)
        self.assertEqual(r.val('alarms.state', 'o2.state'), 'NORMAL')
        r.tap('to_history')
        rows = r.widget('history')['cells']
        self.assertEqual(rows[0][1], 'O2 Level Alarm')
        self.assertTrue(rows[0][0].startswith('2026-01-01'))

    def test_trend_points_clear_and_stop(self):
        r = Rig()
        r.readings(o2_ppm=3.0, h2o_ppm=1.0)
        r.step(300)
        r.go('alarms_btn', 'to_trend')
        plot = r.widget('trend_plot')
        self.assertEqual([p for p in plot['series'][0]['points']][-1], [5.0, 3.0])
        self.assertEqual(len(plot['series'][1]['points']), 5)
        r.tap('t_stop')
        self.assertEqual(r.text('t_stop'), 'RUN')
        r.step(600)
        self.assertEqual(r.widget('trend_plot')['x_range'][1], 10.0)          # frozen at minute 5 (10 min window)
        r.tap('t_stop')
        self.assertEqual(r.widget('trend_plot')['x_range'][1], 15.0)
        r.tap('t_left')
        self.assertEqual(r.widget('trend_plot')['x_range'][1], 10.0)
        r.tap('t_clr')
        self.assertEqual(r.val('trend.log', 'o2'), [])

    def test_configuration_contrast_light_and_power_restart(self):
        r = Rig()
        r.go('to_config', 'gas')
        self.assertEqual(r.val('system.config', 'gas'), 'argon')
        r.enter('column', '2')
        self.assertEqual(r.overlay()['lines'], ['Single-column system: column 1 only.'])
        r.ok()
        r.go('blower_restart', 'to_screen', 'contrast_down', 'contrast_down')
        self.assertEqual(r.frame()['displays'][D]['brightness'], 80.0)
        r.go('to_main', 'blower_btn')
        self.assertTrue(r.switch('light_switch', 'on'))
        self.assertTrue(r.val('light.on'))
        r.switch('main_power', 'off')                                          # power failure
        self.assertEqual(r.frame()['screen'], 'off')
        r.switch('main_power', 'on')
        self.assertTrue(r.val('purification.enabled'))                        # Blower Restart Status enabled
        self.assertEqual((r.page(), r.val('hmi.user')), ('status', 'none'))   # log in again after power-on

    def test_factory_login_and_second_antechamber_are_refused(self):
        r = Rig()
        r.go('to_login', 'login_factory')
        self.assertEqual(r.overlay()['lines'], ['Factory login is reserved for service.'])
        r.ok()
        r.go('to_main', 'to_antech')
        r.enter('ante_num', '2')
        self.assertEqual(r.overlay()['lines'], ['Antechamber 2 has no automatic control.'])
        r.ok()
        self.assertEqual(r.val('antechamber.config', 'number'), 1)


class RefusalsAndRender(unittest.TestCase):
    def test_power_off_and_logout_lock_the_panel(self):
        r = Rig(on=False)
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertFalse(r.press('pedal_right', 1.0))
        r.switch('main_power', 'on')
        r.login()
        r.call(r.A, 'hmi.logout')                                              # a direct logout disables the buttons in place
        self.assertFalse(r.tap('to_regen'))
        self.assertEqual(r.call(r.A, 'hmi.login', expect='rejected', code=1)['error']['code'], 'INVALID_ARGUMENT')

    def test_auto_antechamber_refusals(self):
        r = Rig()
        r.go('to_antech')
        r.tap('auto')                                                          # no fresh boundary sample
        self.assertEqual(r.overlay()['lines'], ['Close both antechamber doors and check the vacuum and the gas supply.'])
        r.ok()
        r.boundary(101325.0)
        r.tap('auto')
        r.tap('evac')
        self.assertEqual(r.overlay()['lines'], ['The automatic sequence is running.'])
        r.ok()
        r.tap('auto')                                                          # AUTO again stops
        self.assertEqual(r.val('antechamber.phase'), 'stopped')

    def test_render_determinism_and_evidence(self):
        a, b = Rig(), Rig()
        a.readings()
        b.readings()
        self.assertEqual(a.h.panel(INST).png_bytes(D)[0], b.h.panel(INST).png_bytes(D)[0])
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        r.readings()
        r.tap('blower_btn')
        r.step(1)
        p = r.h.panel(INST)
        renders = {'home': p.save_png(D, OUT / 'home.png')}
        r.go('to_config')
        renders['menu'] = p.save_png(D, OUT / 'menu.png')
        r.go('to_main', 'alarms_btn', 'o2_enable')
        r.readings(o2_ppm=40.0)
        r.step(1)
        renders['error'] = p.save_png(D, OUT / 'error.png')
        r.go('ack', 'to_main', 'to_regen')
        renders['regen'] = p.save_png(D, OUT / 'regen.png')
        r.go('to_main', 'to_config')
        r.tap('max_sp')
        renders['keypad'] = p.save_png(D, OUT / 'keypad.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')


class PanelEqualsDirectAndContract(unittest.TestCase):
    def test_panel_equals_direct(self):
        a, b = Rig(), Rig(login=False)
        b.call(b.A, 'hmi.login', code=7990)
        a.go('to_config')
        a.enter('max_sp', '12')
        a.enter('min_sp', '10')
        a.go('to_main', 'blower_btn', 'to_blower')
        a.enter('speed', '70')
        a.go('to_main', 'to_purge')
        a.enter('timed_min', '30')
        b.call(b.A, 'config.set', min_sp_pa=1000.0, max_sp_pa=1200.0)
        b.call(b.A, 'purification.set', enabled=True)
        b.call(b.A, 'blower.configure', speed_pct=70.0)
        b.call(b.A, 'purge.configure', timed_min=30.0)
        for sid in ('system.config', 'pressure.target_gauge_pa', 'purification.enabled', 'blower.state', 'purge.control', 'hmi.user'):
            self.assertEqual(a.val(sid), b.val(sid), sid)

    def test_metadata_and_contract(self):
        meta = rt.export_inert_purelab_contract_metadata()['asset_types'][rt.INERT_PURELAB_ASSET_TYPE_ID]
        self.assertEqual(set(meta['device_capabilities']), set(rt.INERT_DEVICE_ARGS))
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.turn', 'panel.switch', 'panel.touch', 'panel.capture', 'panel.frame'})
        for name, cap in {**meta['device_capabilities'], **meta['panel_capabilities']}.items():
            self.assertEqual(validate_value(cap['example_arguments'][0], cap['input_schema']), [], name)
        c = json.loads(CONTRACT.read_text())
        if 'chemistry_lab.panel_ui' in c['extensions']:
            for name in list(meta['device_capabilities']) + list(meta['panel_capabilities']):
                self.assertIn(name, c['capabilities'], name)
            self.assertEqual(c['asset_version'], rt.INERT_PURELAB_ASSET_VERSION)
        r = Rig()
        caps = r.A.describe(instance_id=INST)['capabilities']
        self.assertIn('panel.touch', caps)
        self.assertIn('regen.control', caps)
        self.assertNotIn('pressure.box_gauge_pa', json.dumps(r.A.describe(instance_id=INST)))


class SyncAudit20261019(unittest.TestCase):
    """2026-10-19 front-panel sync audit: the regeneration needle valve acts (suspect confirmed: flow meter and forming gas only while the regen
    gas flows), the working gas type stays a stored selection (effect not documented), panel gestures act as the calling session."""

    def test_regen_needle_valve_sets_the_meter_and_the_gas_used(self):
        r = Rig()
        r.call(r.A, 'regen.control', action='start')
        r.call(r.A, 'regen.control', action='valves_closed')                  # the solenoid valves open: regen gas flows (p.48 step 4)
        self.assertTrue(r.call(r.A, 'panel.turn', input_id='regen_needle', detents=56)['accepted'])
        self.assertEqual((r.val('regen.state', 'flow_scfh'), r.val('regen.state', 'meter_scfh')), (28.0, 28.0))
        r.step(60)
        r.call(r.A, 'regen.control', action='flow_ok')                         # the gas stops flowing; Step 1 Heating
        self.assertEqual(r.val('regen.state', 'meter_scfh'), 0.0)
        r.step(180 * 60)
        self.assertEqual((r.val('regen.state', 'status'), r.val('regen.state', 'meter_scfh')), ('heat_purge', 28.0))
        r.step((30 + 180) * 60)
        self.assertEqual((r.val('regen.state', 'status'), r.val('regen.state', 'meter_scfh')), ('evacuate', 0.0))
        self.assertAlmostEqual(r.val('regen.state', 'gas_used_scf'), 28.0 * (60 + (30 + 180) * 60) / 3600.0, places=9)
        # 'Is Flow OK?' is the operator's confirmation: the PLC has no flow sensor and accepts it at 0 scfh, as on the real HMI
        r2 = Rig()
        r2.call(r2.A, 'regen.control', action='start')
        r2.call(r2.A, 'regen.control', action='valves_closed')
        r2.call(r2.A, 'regen.control', action='flow_ok')
        self.assertEqual(r2.val('regen.state', 'status'), 'heat')
        r2.step(240 * 60)
        self.assertEqual(r2.val('regen.state', 'gas_used_scf'), 0.0)

    def test_working_gas_type_is_a_stored_selection(self):
        r = Rig()
        before = {sid: r.val(sid) for sid in ('pressure.target_gauge_pa', 'purification.enabled', 'purge.enabled', 'blower.state')}
        r.call(r.A, 'config.set', gas='argon')
        self.assertEqual(r.val('system.config', 'gas'), 'argon')
        self.assertEqual({sid: r.val(sid) for sid in before}, before)
        meta = rt.export_inert_purelab_contract_metadata()
        self.assertTrue(any('Working Gas Type' in x and 'not documented' in x for x in meta['limitations']))

    def test_panel_gesture_acts_as_the_calling_session(self):
        r = Rig()
        seen = []
        orig = rt.InertPureLabSession._invoke_checked

        def spy(sess, inst, cap, args, rid):
            if not cap.startswith('panel.'):
                seen.append((sess._InertPureLabSession__principal_id, cap))
            return orig(sess, inst, cap, args, rid)
        with mock.patch.object(rt.InertPureLabSession, '_invoke_checked', spy):
            r.call(r.A, 'panel.switch', input_id='light_switch', position='on')
        self.assertEqual(seen, [('agent', 'light.set')])
        self.assertIsNone(r.h._panel_caller)
        self.assertTrue(r.val('light.on'))


class Sweep20261019(unittest.TestCase):
    """2026-10-19 consistency sweep: an antechamber cycle (or regeneration, purge) started by one agent cannot be stopped, powered off or changed
    by another agent (panel and direct path, ACCESS_DENIED, state unchanged, message box); doors and the evaluator are not restricted by
    ownership; the owner stops its own cycle."""

    def test_another_agents_antechamber_cycle_cannot_be_stopped_or_changed(self):
        r = Rig()
        r.h.register_principal('agent_b')
        B = r.h.session('agent_b')
        r.go('to_antech')
        r.boundary(101325.0)
        self.assertTrue(r.tap('auto'))                                           # agent A starts the automatic antechamber cycle
        r.boundary(250.0)
        r.step(1)
        self.assertEqual(r.h._instances[INST].g03['run_owners'], {'antechamber': 'agent'})
        before = copy.deepcopy(r.h._instances[INST].state)
        x, y, w, h = r.widget('auto')['rect']
        res = r.call(B, 'panel.touch', display_id=D, x=x + w // 2, y=y + h // 2)  # B's Auto button would stop it: refused
        self.assertFalse(res['accepted'])
        self.assertEqual(r.overlay()['dialog'], 'err_access')
        self.assertIn('started by another user', ' '.join(r.overlay()['lines']))
        r.ok()
        for cap, args in (('antechamber.cycle.stop', {}), ('antechamber.auto', dict(run=False)), ('pump.set', dict(running=False)),
                          ('antechamber.set_cycles', dict(cycles=2))):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h._instances[INST].state, before)                     # state unchanged
        res = B.invoke(instance_id=INST, capability_id='antechamber.inner_door.set', arguments=dict(open=True), request_id='b_door')
        self.assertNotEqual((res.get('error') or {}).get('code'), 'ACCESS_DENIED')   # a door is physical: never refused for ownership
        r.call(B, 'antechamber.inner_door.set', open=False) if res['status'] == 'succeeded' else None
        self.assertTrue(r.tap('auto'))                                           # A stops its own cycle
        self.assertFalse(r.val('antechamber.control', 'auto'))
        r.boundary(101325.0)
        self.assertTrue(r.tap('auto'))                                           # A starts again ...
        self.assertTrue(r.val('antechamber.control', 'auto'))
        self.assertTrue(r.call(B, 'panel.switch', input_id='main_power', position='off')['accepted'])   # ... B's main power switch is
        self.assertFalse(r.val('power.enabled'))                                 # physical (lead clarification 10-19): PLC outputs off,
        self.assertFalse(r.val('antechamber.control', 'auto'))                  # the cycle ends as on a power loss
        r.call(r.E, 'power.set', enabled=True)                                   # the evaluator is not restricted


if __name__ == '__main__':
    unittest.main()
