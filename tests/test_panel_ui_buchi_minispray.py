"""front-panel tests for CHEM-026 buchi_minispray (panel_ui.json + scripts/asset_runtime/_panel_ui.py):
python3 -m pytest tests/test_panel_ui_buchi_minispray.py -q

Every gesture goes through the session API (panel.switch / panel.touch / panel.turn / panel.press, the runtime's normal invoke path) and every
check looks at the runtime state and at the screen (the structured frame of panel.frame).  Themes: the key procedures of the menu survey (start
and end an open-mode run on the control screen, pump calibration, run export; methods and auto mode answered as Advanced / Corrosive only), the
numeric entries of the tiles, the run records, settings (units, pressure display, focus parameters, brightness, date and time), notifications
and logs, the navigation control (turn to select, press to confirm), the power gate and the start-up time-out; panel = direct capability (same
runtime state); render determinism and the PNGs of outputs/panel_ui/buchi_minispray/; contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import buchi_minispray as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'minispray_01'
T0 = 273.15
SCR = 'screen'
OUT = ROOT / 'outputs/panel_ui/buchi_minispray'
SURVEY_TASKS = ['Start an open-mode run manually', 'End an open-mode run', 'Create a method (Advanced/Corrosive)', 'Load a method and start in auto mode',
                'Calibrate the peristaltic pump', 'Export run data']
DONE_TASKS = set()


def _effects():
    meta = rt.export_buchi_minispray_contract_metadata()
    return {k: {e['state_id'] for e in v['state_effects']} for k, v in meta['capabilities'].items()}


EFFECTS = _effects()


class Rig:
    def __init__(self, volume=2e-6):
        self.h = rt.BuchiMinisprayRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.EXAMPLE)
        self.call(self.E, 'sample.configure', **dict(rt.SAMPLE_EXAMPLE, volume_m3=volume))
        self.call(self.E, 'method.configure', **rt.METHOD_EXAMPLE)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        before = self.h.read('evaluator')
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        if r['status'] in ('succeeded', 'accepted'):
            rt.validate(rt.CAPS[cap][1], r['result'])
            assert self.h.time_s == t0, cap
            if cap.startswith('panel.'):
                after = self.h.read('evaluator')
                changed = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)}
                assert not changed - EFFECTS[cap], (cap, sorted(changed - EFFECTS[cap]))
            return r['result']
        return r

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='master', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def page(self):
        return self.frame()['ui']['page']

    def widgets(self):
        return {w['id']: w for w in self.frame()['displays'][SCR]['widgets'] if w.get('id')}

    def text(self, wid):
        return self.widgets()[wid].get('text')

    def touch(self, x, y):
        return self.call(self.A, 'panel.touch', display_id=SCR, x=int(x), y=int(y))['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def overlay(self):
        return self.frame()['displays'][SCR].get('overlay')

    def dlg(self):
        ov = self.frame()['ui']['overlay']
        return ov and ov.get('dialog')

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def keys(self, text):
        """Numeric keypad: clear the shown value, type, OK."""
        assert self.overlay()['kind'] == 'keyboard'
        while self.overlay()['field']:
            self.ov('Del')
        for ch in text:
            assert self.ov(ch), ch
        return self.ov('OK')

    def row(self, wid, first, col=0):
        """Tap the row of a list or form widget whose cell (col) is this text."""
        w = self.widgets()[wid]
        firsts = [str(c[col]) for c in w['cells']]
        assert first in firsts, (first, firsts)
        k = firsts.index(first)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def step(self, s):
        self.h.step(s)

    def on(self):
        assert self.switch('on')
        self.step(5.5)
        assert (self.frame()['screen'], self.page()) == ('gui', 'home')

    def control(self):
        if self.page() != 'control':
            self.tap(f'{self.page()}_nav_control')
        assert self.page() == 'control'


class KeyProcedures(unittest.TestCase):
    def test_start_and_end_an_open_mode_run(self):
        g = Rig()
        g.on()
        g.control()
        g.tap('tile_drying_value')
        g.keys('30')
        self.assertAlmostEqual(g.h.method['drying_gas_m3_s'], 30 / 3600, places=12)
        self.assertEqual(g.text('tile_drying_value'), '30.0')
        self.assertTrue(g.tap('tile_drying_start'))                                  # the aspirator is starting up
        self.assertTrue(g.h.airflow)
        g.tap('tile_spray_value')
        g.keys('500')
        g.tap('tile_spray_start')
        self.assertTrue(g.h.spray)
        g.tap('tile_inlet_value')
        g.keys('160')
        self.assertAlmostEqual(g.h.method['inlet_target_k'], T0 + 160, places=9)
        g.tap('tile_inlet_start')
        self.assertTrue(g.h.heating)
        g.tap('tile_pump_start')                                                     # inlet not reached yet: refused (simulation guard)
        self.assertEqual(g.dlg(), 'pump_refused')
        g.ov('OK')
        g.step(20.0)
        self.assertIn(g.text('tile_outlet_value').split()[0], [str(x) for x in range(20, 161)])
        g.tap('tile_pump_value')
        g.keys('4')
        self.assertAlmostEqual(g.h.method['feed_m3_s'], 4e-6 / 60, places=15)
        self.assertTrue(g.tap('tile_pump_start'))
        self.assertEqual(g.h.phase, 'running')
        self.assertEqual(g.text('tile_pump_start'), 'Stop')
        g.step(40.0)
        DONE_TASKS.add('Start an open-mode run manually')
        # end (om p.57): pump stop, spray gas stop, heater stop, save the run, cool down, aspirator stop
        self.assertEqual(g.h.phase, 'succeeded')
        g.tap('tile_spray_start')
        g.tap('tile_inlet_start')
        self.assertFalse(g.h.heating)
        g.tap('tile_drying_start')                                                   # still hot: refused
        self.assertTrue(g.h.airflow)
        self.assertEqual(g.dlg(), 'not_possible')
        g.ov('OK')
        g.tap('control_nav_runs')
        g.tap('runs_options')
        g.ov('Save run')
        self.assertIsNone(g.h.run_open)
        g.step(40.0)
        g.tap('runs_nav_control')
        self.assertTrue(g.tap('tile_drying_start'))
        self.assertFalse(g.h.airflow)
        g.switch('off')
        self.assertEqual(g.frame()['screen'], 'off')
        DONE_TASKS.add('End an open-mode run')

    def test_methods_and_auto_mode_are_advanced_only(self):
        g = Rig()
        g.on()
        g.tap('home_nav_methods')
        self.assertIn('Advanced', g.text('methods_na'))
        g.tap('methods_nav_jobs')
        self.assertIn('Corrosive models only.', g.text('jobs_na2'))
        DONE_TASKS.add('Create a method (Advanced/Corrosive)')
        g.tap('jobs_nav_control')
        g.tap('icon_auto')
        self.assertEqual(g.dlg(), 'not_available')
        DONE_TASKS.add('Load a method and start in auto mode')

    def test_calibrate_the_peristaltic_pump(self):
        g = Rig()
        g.call(g.E, 'pump.configure_error', delivery_factor=0.9)
        g.on()
        g.tap('home_nav_menu')
        g.tap('menu_instrument')
        g.tap('instrument_cal')
        self.assertEqual(g.page(), 'calibration')
        g.tap('cal_start')                                                           # 10 mL in 60 s
        self.assertEqual(g.h.cal['state'], 'running')
        g.step(61.0)
        self.assertEqual(g.h.cal['state'], 'done')
        self.assertIn('10.00 mL', g.text('cal_state'))                              # 1.1.1: the controller shows the target progress ...
        cyl = g.A.read(instance_id=INST, state_ids=['calibration.cylinder_m3'])['observations']['calibration.cylinder_m3']['value']
        self.assertAlmostEqual(cyl, 9e-6, delta=1e-12)                              # ... the agent reads 9 mL on the measuring cylinder
        self.assertEqual(g.h.read()['notifications.active'][0]['code'], 'calibration_done')
        g.row('cal_rows', 'Difference target - actual')
        g.keys('1')
        self.assertAlmostEqual(g.h.pump_corr, 10 / 9, places=9)
        self.assertIn('1.111', g.text('cal_corr'))
        DONE_TASKS.add('Calibrate the peristaltic pump')

    def test_export_run_data(self):
        g = Rig()
        g.on()
        g.control()
        g.tap('tile_drying_start')
        g.step(5.0)
        g.tap('tile_drying_start')
        g.tap('control_nav_runs')
        self.assertEqual(len(g.widgets()['runs_list']['rows']), 1)
        g.tap('runs_options')
        g.ov('Export .csv')
        self.assertEqual(g.dlg(), 'no_storage')                       # no data storage device connected
        g.ov('OK')
        g.row('runs_list', '1')
        g.tap('runs_options')
        g.ov('Delete')
        g.ov('Delete')
        self.assertEqual(g.h.runs, [])
        DONE_TASKS.add('Export run data')


class SettingsAndViews(unittest.TestCase):
    def test_units_pressure_focus_and_brightness(self):
        g = Rig()
        g.on()
        g.tap('home_nav_menu')
        g.tap('menu_settings')
        g.tap('settings_customize')
        g.tap('customize_localization')
        g.row('localization_rows', 'Temperature')
        self.assertEqual(g.overlay()['kind'], 'select')
        g.ov('°F')
        self.assertEqual(g.h.settings['units.temperature'], 'F')
        g.tap('localization_back')
        g.tap('customize_panelopts')
        g.row('panelopts_rows', 'Show Pressure')
        g.ov('Filter Pressure')
        g.tap('panelopts_back')
        g.tap('customize_back')
        g.tap('settings_system')
        g.tap('system_display')
        g.row('display_rows', 'Brightness')
        g.keys('60')
        self.assertEqual(g.frame()['displays'][SCR]['brightness'], 60)
        g.tap('display_back')
        g.tap('system_back')
        g.tap('settings_back')
        g.tap('menu_nav_control')
        self.assertIn('°F', g.text('tile_inlet_unit'))
        self.assertEqual(g.text('tile_inlet_value'), '302')                         # 150 degC
        self.assertIn('mbar', g.text('tile_filter_value'))
        g.tap('control_tab_focus')
        self.assertEqual(g.text('focus_1_name'), 'Inlet Temperature')

    def test_date_time_notifications_logs_and_counters(self):
        g = Rig()
        g.on()
        g.tap('home_nav_menu')
        g.tap('menu_settings')
        g.tap('settings_system')
        g.tap('system_datetime')
        g.row('datetime_rows', 'Set Date')
        self.assertEqual(g.page(), 'clock')
        g.tap('clock_save')
        self.assertEqual(g.h.read()['controller.datetime'][:10], '2026-01-01')
        g.tap('datetime_back')
        g.tap('system_back')
        g.tap('settings_back')
        g.h.pools['filter']['s'] = 3e-4
        g.call(g.A, 'drying_gas.set', enabled=True)
        g.step(1.0)
        self.assertIn('menu_note', g.widgets())                                      # the notification icon appears
        g.tap('menu_note')
        self.assertEqual(g.page(), 'notifications')
        self.assertIn('Filter blockage', json.dumps(g.widgets()['notes_list']['rows']))
        g.tap('notes_ack')
        self.assertEqual(g.h.notices, [])
        g.tap('notes_back')
        g.tap('menu_logs')
        self.assertEqual(len(g.widgets()['logs_list']['rows']), 1)
        g.tap('logs_back')
        g.tap('menu_instrument')
        g.tap('instrument_counters')
        self.assertIn('h', g.text('counter_operating_s'))
        g.tap('counters_back')
        g.tap('instrument_back')
        g.tap('menu_update')
        self.assertEqual(g.dlg(), 'no_update')
        g.ov('OK')
        g.tap('menu_remote')
        self.assertEqual(g.dlg(), 'remote_option')

    def test_navigation_control_and_graphs(self):
        g = Rig()
        g.on()
        self.assertTrue(g.call(g.A, 'panel.turn', input_id='nav_knob', detents=1)['accepted'])
        focus = g.frame()['ui']['focus']
        self.assertIsNotNone(focus)
        for _ in range(30):
            if g.frame()['ui']['focus'] == 'home_nav_control':
                break
            g.call(g.A, 'panel.turn', input_id='nav_knob', detents=1)
        self.assertEqual(g.frame()['ui']['focus'], 'home_nav_control')
        self.assertTrue(g.call(g.A, 'panel.press', input_id='nav_push')['accepted'])   # confirm the selected entry
        self.assertEqual(g.page(), 'control')
        g.tap('tile_drying_start')
        g.tap('tile_inlet_start')
        g.step(30.0)
        g.tap('control_tab_graphs')
        plot = g.widgets()['graph_plot']
        self.assertEqual([s['label'] for s in plot['series']], ['Inlet T', 'Outlet T'])
        self.assertGreater(len(plot['series'][0]['points']), 10)


class RefusalsAndPower(unittest.TestCase):
    def test_heating_needs_drying_gas_and_power_gate(self):
        g = Rig()
        self.assertFalse(g.touch(240, 320))
        g.on()
        g.control()
        self.assertFalse(g.tap('tile_inlet_start'))
        self.assertEqual(g.dlg(), 'not_possible')
        g.ov('OK')
        g.tap('tile_inlet_value')
        self.assertFalse(g.keys('250'))                                             # above 220 degC: range message, value kept
        self.assertIn('outside of limits', g.overlay()['error'])
        g.ov('Cancel')
        self.assertAlmostEqual(g.h.method['inlet_target_k'], rt.METHOD_EXAMPLE['inlet_target_k'])
        g.step(3600.0)
        self.assertEqual(g.page(), 'control')                                         # no time-out
        g.switch('off')
        self.assertFalse(g.touch(240, 320))


class Equivalence(unittest.TestCase):
    def test_panel_and_direct_commands_reach_the_same_state(self):
        a, b = Rig(), Rig()
        a.on()
        a.control()
        a.tap('tile_drying_value')
        a.keys('28')
        a.tap('tile_drying_start')
        a.tap('tile_spray_start')
        a.tap('tile_inlet_start')
        a.tap('tile_deblock_value')
        a.keys('12')
        b.call(b.A, 'power.set', enabled=True)
        b.step(5.5)
        b.call(b.A, 'drying_gas.set', drying_gas_m3_s=28 / 3600)
        b.call(b.A, 'drying_gas.set', enabled=True)
        b.call(b.A, 'dispersion_gas.set', enabled=True)
        b.call(b.A, 'heating.set', enabled=True)
        b.call(b.A, 'deblock.set', per_min=12)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))
        a.step(30.0)
        b.step(30.0)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))


class SyncFixes(unittest.TestCase):
    """Sync audit 2026-10-19: device-side delivery numbers (R1), caller rules (R2), dark mode, dimmer and time zone (R3)."""

    def start_feed(self, g):
        g.control()
        for key in ('drying', 'spray', 'inlet'):
            g.tap(f'tile_{key}_start')
        g.step(20.0)
        self.assertTrue(g.tap('tile_pump_start'))

    def test_run_record_counts_the_set_flow(self):
        g = Rig()
        g.call(g.E, 'pump.configure_error', delivery_factor=1.2)
        g.on()
        self.start_feed(g)
        g.step(5.0)
        rec = g.h.read()['runs.list'][0]
        self.assertAlmostEqual(rec['feed_m3'], rt.METHOD_EXAMPLE['feed_m3_s'] * g.h.elapsed_s, delta=1e-15)   # set flow x pumping time
        fed = g.h.input_mass - g.h.mass(g.h.stock)                                                            # the real feed is 1.2 x larger
        self.assertGreater(fed, 1.1 * rec['feed_m3'] * 1000.0)

    def test_dark_mode_dimmer_and_time_zone(self):
        g = Rig()
        g.on()
        g.tap('home_nav_menu')
        g.tap('menu_settings')
        g.tap('settings_system')
        g.tap('system_display')
        light = [int(v) for v in g.h.panel.render(SCR)[300, 470]]
        g.row('display_rows', 'Dark Mode')
        g.ov('ON')
        self.assertTrue(g.frame()['displays'][SCR]['dark'])
        dark = [int(v) for v in g.h.panel.render(SCR)[300, 470]]
        self.assertLess(sum(dark), sum(light) / 3)                                     # light texts on a dark background
        g.row('display_rows', 'Dimmer')
        g.ov('1 min')
        self.assertEqual(g.frame()['displays'][SCR]['brightness'], 80)
        g.step(61.0)
        self.assertEqual(g.frame()['displays'][SCR]['brightness'], 24)                 # dimmed to 30 % after 1 min without a touch
        g.tap('display_back')
        self.assertEqual(g.frame()['displays'][SCR]['brightness'], 80)                 # a touch restores the brightness
        t0 = g.h._clock_now()
        g.tap('system_datetime')
        g.row('datetime_rows', 'Select Time Zone')
        g.ov('UTC+3')
        self.assertEqual((g.h._clock_now() - t0).total_seconds(), 7200.0)              # UTC+1 -> UTC+3: the shown time moves on 2 h
        g.call(g.A, 'clock.set', date='2026-10-19', time='09:30')
        self.assertEqual(g.h.read()['controller.datetime'], '2026-10-19T09:30')         # Set Date sets the local time of the selected zone

    def test_caller_rules(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.on()
        self.start_feed(g)
        op = g.h.operation_id
        self.assertEqual(g.h._owners, {op: 'agent'})
        self.assertEqual(g.h._run_owners, {1: 'agent'})
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        before = json.dumps(g.h.read('evaluator'), sort_keys=True)
        w = g.widgets()['tile_pump_start']
        x, y, ww, hh = w['rect']
        self.assertFalse(g.call(B, 'panel.touch', display_id=SCR, x=x + ww // 2, y=y + hh // 2)['accepted'])   # B's pump stop: refused
        self.assertEqual(g.dlg(), 'not_permitted')
        g.ov('OK')
        for cap, args in (('acquisition.stop', {}), ('drying.shutdown', {}), ('runs.save', {}),
                          ('runs.delete', {'run_id': 1}), ('pump.set', {'feed_m3_s': 2e-6 / 60}), ('heating.set', {'enabled': False}),
                          ('drying_gas.set', {'drying_gas_m3_s': 20 / 3600})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(json.dumps(g.h.read('evaluator'), sort_keys=True), before)
        g.A.cancel(op, 'c1')                                                           # the owner stops its own panel-started feed
        self.assertEqual(g.h.phase, 'cancelled')
        g.call(g.A, 'runs.save')
        self.assertIsNone(g.h.run_open)
        self.assertTrue(g.call(B, 'panel.switch', input_id='master', position='off')['accepted'])   # 1.2.0: the master switch is physical: never refused
        self.assertFalse(g.h.read('evaluator')['power.enabled'])


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.on()
            g.control()
            g.tap('tile_drying_start')
            g.tap('tile_spray_start')
            g.tap('tile_inlet_start')
            g.step(30.0)
            return g
        a, b = scenario(), scenario()
        ia, ib = a.h.panel.render(SCR), b.h.panel.render(SCR)
        self.assertEqual(ia.dtype.name, 'uint8')
        self.assertTrue((ia == ib).all())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png(SCR, OUT / 'home.png')}
        a.tap('control_nav_menu')
        shots['menu'] = a.h.panel.save_png(SCR, OUT / 'menu.png')
        a.tap('menu_nav_control')
        a.tap('control_tab_graphs')
        shots['graphs'] = a.h.panel.save_png(SCR, OUT / 'graphs.png')
        a.tap('graphs_tab_control')
        a.tap('tile_drying_start')
        shots['error'] = a.h.panel.save_png(SCR, OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')
        self.assertEqual(ia.shape, (640, 480, 3))

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
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.touch', 'panel.press', 'panel.turn', 'panel.switch', 'panel.capture', 'panel.frame'})
        m = rt.export_buchi_minispray_contract_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in list(rt.PANEL_CAPS) + list(rt.DEVICE_CAPS):
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/buchi_minispray.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('ms_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        self.assertEqual(DONE_TASKS, set(SURVEY_TASKS))


if __name__ == '__main__':
    unittest.main()
