"""front-panel tests for CHEM-096 agilent_gc8890b (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_analytical.py):
python3 -m pytest tests/test_panel_ui_agilent_gc8890b.py -q

Every gesture goes through the public API (panel.touch / panel.switch / panel.press, the runtime's normal invoke path); widgets are found by
id in the structured frame (panel.frame, evaluator) and tapped at their centre.  Themes: the eight key procedures of the menu survey; the
touchscreen functions (method editor, run table, Home status / plot, diagnostics and shutdowns, EMF counters and maintenance procedures, logs,
configuration, scheduler and clock table, calibration, system settings, power saving, tools, power, help); panel = direct command (same
runtime state); refusals shown as the device's own message; time-outs; render determinism and the evidence PNGs; every terminal command once;
contract entries."""
import copy
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

OUT = ROOT / 'outputs/panel_ui/agilent_gc8890b'
CONTRACT = rt.GC8890B_ASSET_DIR / 'asset.contract.json'
TOUCH = 'touch'
ML = 1e-6 / 60
PSI = 6894.757293168
SURVEY_TASKS = ['Start up the GC', 'Edit the active method oven temperature on the touchscreen', 'Isothermal oven run', 'Manual injection run',
                'Clear a shutdown condition', 'Enable gas saver', 'Set the IP address by DHCP', 'Shut down the GC']
DONE_TASKS = set()
TMP = tempfile.mkdtemp(prefix='gc8890b_panel_')


class Rig:
    """One GC with a scene callback, an agent and an evaluator session; gestures only through panel.* capabilities."""

    def __init__(self, gases=True, warm=True, sample=True):
        self.scene = []
        self.h = rt.AgilentGC8890B('gc', scene_adapter=lambda i, s: self.scene.append(copy.deepcopy(s)))
        self.h.panel_capture_dir = TMP
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('__agent__'), self.h.session('judge')
        self.n = 0
        if sample:
            self.h.register_sample('fixture')
        if gases:
            self.h.set_gas_supply(carrier=True, hydrogen=True, air=True, makeup=True)
        if warm:
            assert self.switch('on')
            self.step(1800)

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h._time()
        r = sess.invoke(capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h._time() == t0, cap                                          # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(state_ids=[sid])['observations'][sid]['value']

    def status(self):
        return self.val('gc.status')

    def step(self, dt):
        t = 0.0
        while t < dt - 1e-9:
            d = min(60.0, dt - t)
            self.h.host_step(d)
            t += d

    def settle(self, limit=3600):
        """Wait until nothing but a missing Prep Run keeps the GC from ready."""
        t = 0
        while self.status()['reasons'] and t < limit:
            self.step(30)
            t += 30
        assert not self.status()['reasons'], self.status()

    def ready(self, limit=3600):
        t = 0
        while not self.status()['ready'] and t < limit:
            self.step(30)
            t += 30
        assert self.status()['ready'], self.status()

    # ---- screen
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

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TOUCH, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

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

    def row(self, wid, first, col=0):
        """Tap the row of a list or form whose cell col is this text (dragging the rows from the top until it shows)."""
        w = self.widgets()[wid]
        while w['scroll']:
            x, y, ww, hh = w['rect']
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
            w = self.widgets()[wid]
        for _ in range(40):
            w = self.widgets()[wid]
            firsts = [c[col] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, firsts)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def cells(self, wid):
        w = self.widgets()[wid]
        out = {}
        for _ in range(40):
            out.update({c[0]: (c[1] if len(c) > 1 else None) for c in w['cells']})
            if w['scroll'] + len(w['cells']) >= w['count']:
                break
            x, y, ww, hh = w['rect']
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
            w = self.widgets()[wid]
        return out

    def type(self, text):
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov
        labels = {b['label'] for b in ov['buttons']}
        if '7' in labels and 'q' not in labels and 'Q' not in labels:
            if ov['field']:
                self.ov('Del')
            for ch in text:
                assert self.ov(ch), ch
            return
        if ov['field']:
            self.ov('Del')
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit() or ch in '$@#%&/():;_+=\'"!?,':
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            elif ch.isupper():
                if mode == 'sym':
                    self.ov('abc')
                if self.overlay()['mode'] != 'upper':
                    self.ov('Shift')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if self.overlay()['mode'] == 'upper':
                    self.ov('Shift')
                self.ov(ch)

    def enter(self, wid, first, text, ok='OK'):
        assert self.row(wid, first)
        self.type(text)
        return self.ov(ok)

    def choose(self, wid, first, option):
        assert self.row(wid, first)
        return self.ov(option)

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def tab(self, name):
        return self.tap(f'tab_{name}')

    def png(self):
        r = self.call(self.A, 'panel.capture', display_id=TOUCH)
        return Path(r['image']['path']).read_bytes()


def method(r, comp):
    return r.val('gc.method')[comp]


class SurveyProcedures(unittest.TestCase):
    """The eight key procedures of the menu survey, done only with panel gestures."""

    def test_start_up(self):
        r = Rig(warm=False)
        self.assertFalse(r.touch(400, 240))                                         # off: dark screen, touches ignored
        self.assertEqual(r.frame()['displays'][TOUCH]['brightness'], 0)
        self.assertTrue(r.switch('on'))
        self.assertTrue(r.val('power.enabled'))
        self.assertEqual(r.page()[0], 'boot')
        r.step(30)
        self.assertEqual(r.page(), ('gui', 'home_flow'))
        self.assertEqual(r.status()['message'], 'Power on successful')
        self.assertEqual(r.text('msg'), 'Power on successful')
        self.assertIn('Power on successful', [e['text'] for e in r.val('gc.logs')['system']])
        r.step(6)
        self.assertNotIn('msg', r.widgets())
        r.ready()
        self.assertEqual(r.text('tray_status'), 'STATUS: READY')
        self.assertEqual(r.frame()['displays']['status_led']['color'], 'green')
        DONE_TASKS.add('Start up the GC')

    def test_edit_oven_temperature_on_the_fly(self):
        r = Rig()
        self.assertTrue(r.tap('fp_oven_t'))                                        # Flow path: the setpoint opens the method editor with its keypad
        self.assertEqual(r.page()[1], 'method_oven')
        self.assertEqual(r.overlay()['kind'], 'keyboard')
        r.type('120')
        self.assertTrue(r.ov('OK'))
        self.assertAlmostEqual(r.val('thermal.oven.setpoint_k'), 393.15)
        self.assertAlmostEqual(method(r, 'oven')['initial_K'], 393.15)
        self.assertEqual(r.status()['ready'], False)                               # applies at once: the oven heats, not ready
        r.ready()
        self.assertAlmostEqual(r.val('thermal.oven.indicated_k'), 393.2, delta=1.1)
        DONE_TASKS.add('Edit the active method oven temperature on the touchscreen')

    def test_isothermal_oven_run(self):
        r = Rig()
        r.tab('method')
        r.tap('side_method_oven')
        r.enter('method_oven_form', 'Oven temperature', '90')
        r.enter('method_oven_form', 'Initial time', '2.5')
        self.assertEqual(r.cells('method_oven_form')['Rate 1'], '0.00 °C/min')
        self.assertEqual(r.cells('method_oven_form')['Run time'], '2.50 min')
        self.assertAlmostEqual(method(r, 'oven')['initial_time_s'], 150.0)
        r.ready()
        self.assertTrue(r.tap('start'))
        self.assertEqual(r.val('gc.run')['length_s'], 150.0)
        r.step(60)
        self.assertAlmostEqual(r.val('thermal.oven.setpoint_k'), 363.15)           # isothermal: the oven stays at its setpoint
        r.step(100)
        self.assertIsNone(r.val('gc.run'))
        self.assertEqual(r.val('gc.last_run')['length_s'], 150.0)
        DONE_TASKS.add('Isothermal oven run')

    def test_manual_injection_run(self):
        r = Rig()
        r.call(r.A, 'lid.set_angle', angle_rad=1.2)
        r.call(r.A, 'sample.load', sample_id='fixture')
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        r.tab('method')
        r.tap('side_method_inlet')
        r.choose('method_inlet_form', 'Mode', 'Splitless')
        r.tap('side_method_detector')
        for label in ('H2 flow', 'Air flow', 'Makeup flow', 'Flame'):
            r.choose('method_detector_form', label, 'On')
        r.settle()
        self.assertEqual(r.status()['state'], 'WAITING FOR PREP RUN')
        self.assertTrue(r.tap('prep_run'))
        self.assertIn(r.status()['state'], ('READY FOR INJECTION', 'PREP RUN'))
        r.ready()
        self.assertEqual(r.text('tray_status'), 'STATUS: READY FOR INJECTION')
        self.assertEqual(r.val('gc.actuals')['split_vent_m3_s'], 0.0)              # splitless: purge valve closed in Pre Run
        self.assertTrue(r.tap('start'))
        self.assertEqual(r.text('tray_status'), 'STATUS: RUN')
        r.step(40)
        self.assertGreater(r.val('gc.actuals')['split_vent_m3_s'], 0.0)            # purge flow after the purge time (0.5 min)
        r.step(30)
        self.assertGreater(r.val('gc.actuals')['signal_A'], 5e-12 + 1e-12)         # the first fixture peak (60 s) on the FID
        r.tab('home')
        r.tap('side_home_plot')
        self.assertTrue(r.widgets()['pl_chart']['series'][0]['points'])
        r.step(240)
        last = r.val('gc.last_run')
        self.assertFalse(last['aborted'])
        self.assertEqual([p['retention_time_s'] for p in last['peaks']], [60.0, 125.0, 220.0])
        self.assertEqual(r.val('gc.logs')['run'][0]['text'], 'Run completed')
        self.assertEqual(r.val('gc.emf')['instrument.runs']['value'], 1)
        self.assertEqual(r.val('gc.emf')['inlet.septum_inj']['value'], 0)          # manual injections do not count (p.130)
        DONE_TASKS.add('Manual injection run')

    def test_clear_a_shutdown(self):
        r = Rig()
        r.h.set_gas_supply(carrier=False)
        r.step(60)
        self.assertGreater(r.val('gc.system')['beeps'], 0)                          # warning beeps before the shutdown
        r.step(70)
        self.assertEqual(r.status()['state'], 'SHUTDOWN')
        self.assertEqual(r.frame()['displays']['status_led']['color'], 'yellow')
        self.assertIn('Diagnostics 1', r.text('tab_diag'))
        r.h.set_gas_supply(carrier=True)
        r.tab('diag')
        self.assertTrue(r.row('dg_alerts', 'Front Inlet: Inlet flow shutdown', col=1))
        self.assertEqual(r.overlay()['title'], 'Front Inlet: Inlet flow shutdown')
        self.assertTrue(r.ov('Keep Shutdown Zone On'))
        self.assertEqual(r.val('gc.alerts'), [])
        self.assertTrue(method(r, 'column')['on'])
        r.ready()
        DONE_TASKS.add('Clear a shutdown condition')

    def test_enable_gas_saver(self):
        r = Rig()
        r.tab('method')
        r.tap('side_method_inlet')
        r.choose('method_inlet_form', 'Gas saver', 'On')
        r.enter('method_inlet_form', 'Gas saver flow', '20')
        r.enter('method_inlet_form', 'Gas saver time', '3')
        m = method(r, 'inlet')
        self.assertTrue(m['gas_saver'])
        self.assertAlmostEqual(m['gas_saver_flow_m3_s'] / ML, 20.0)
        self.assertAlmostEqual(m['gas_saver_time_s'], 180.0)
        self.assertAlmostEqual(r.val('gc.actuals')['split_vent_m3_s'] / ML, 19.0, places=6)    # reduced split vent flow
        r.enter('method_inlet_form', 'Gas saver flow', '10')                       # below 15 mL/min: out of the field range
        self.assertEqual(r.overlay()['error'], 'Out of range')
        DONE_TASKS.add('Enable gas saver')

    def test_ip_address_by_dhcp(self):
        r = Rig()
        r.tab('settings')
        r.tap('set_system')
        self.assertEqual(r.page()[1], 'sys_network')
        r.choose('sys_network_form', 'Enable DHCP', 'Disabled')
        r.tap('apply')
        self.assertEqual(r.overlay()['dialog'], 'restart_prompt')
        r.ov('Later')
        self.assertTrue(r.val('gc.system')['restart_pending'])
        r.choose('sys_network_form', 'Enable DHCP', 'Enabled')
        r.tap('apply')
        self.assertTrue(r.ov('Restart'))
        self.assertFalse(r.val('gc.system')['restart_pending'])
        self.assertTrue(r.val('gc.settings')['network.dhcp'])
        self.assertEqual(r.page()[0], 'boot')
        r.step(31)
        self.assertEqual(r.page(), ('gui', 'home_flow'))
        DONE_TASKS.add('Set the IP address by DHCP')

    def test_shut_down(self):
        r = Rig()
        r.tab('maint')
        r.tap('mt_instrument')
        r.tap('maint_instrument_perform')
        r.tap('proc_maintenance_mode')
        self.assertTrue(r.ov('Start Maintenance'))
        self.assertEqual(r.page()[1], 'maint_proc')
        self.assertEqual(r.status()['state'], 'MAINTENANCE MODE')
        self.assertFalse(r.widgets()['mp_continue']['enabled'])
        r.step(1800)
        self.assertEqual(r.val('gc.maintenance')['step'], 'service')               # cooled: ready for maintenance
        self.assertTrue(r.switch('off'))
        self.assertFalse(r.val('power.enabled'))
        self.assertIsNone(r.val('gc.maintenance'))
        self.assertAlmostEqual(method(r, 'oven')['initial_K'], 353.15)             # the Active Method is kept for the next start
        DONE_TASKS.add('Shut down the GC')


class MethodEditor(unittest.TestCase):
    def test_inlet_split_and_flows(self):
        r = Rig()
        r.tab('method')
        r.enter('method_inlet_form', 'Split ratio', '50')
        self.assertEqual(method(r, 'inlet')['split_ratio'], 50.0)
        self.assertEqual(r.cells('method_inlet_form')['Split flow'], '50.000 mL/min')
        self.assertEqual(r.h._process.method.split_ratio, 50.0)                    # the process follows the Active Method
        r.enter('method_inlet_form', 'Heater temperature', '280')
        self.assertAlmostEqual(r.val('thermal.inlet.setpoint_k'), 553.15)
        r.enter('method_inlet_form', 'Split ratio', '9000')                       # range 1..7500
        self.assertEqual(r.overlay()['error'], 'Out of range')

    def test_column_modes_units_and_pressure(self):
        r = Rig()
        r.tab('method')
        r.tap('side_method_column')
        c = r.cells('method_column_form')
        self.assertEqual(c['Actual flow'], '1.000 mL/min')
        p_psi = float(c['Actual pressure'].split()[0])
        self.assertAlmostEqual(p_psi, 13.6, delta=0.2)                              # 30 m x 0.25 mm, He, 80 C, FID outlet
        r.choose('method_column_form', 'Control mode', 'Constant Pressure')
        r.enter('method_column_form', 'Pressure', '20')
        self.assertAlmostEqual(method(r, 'column')['pressure_Pa'] / PSI, 20.0, places=4)
        self.assertGreater(r.val('gc.actuals')['column_flow_m3_s'] / ML, 1.3)
        r.tab('settings')
        r.tap('set_configuration')
        r.tap('side_cfg_misc')
        r.choose('cfg_misc_form', 'Pressure Units', 'kPa')
        r.tap('apply')
        r.tab('method')
        r.tap('side_method_column')
        self.assertEqual(r.cells('method_column_form')['Pressure'], '137.90 kPa')
        r.enter('method_column_form', 'Pressure', '100')
        self.assertAlmostEqual(method(r, 'column')['pressure_Pa'], 100000.0, places=3)

    def test_oven_program_ramps_and_max_temperature(self):
        r = Rig()
        r.tab('method')
        r.tap('side_method_oven')
        self.assertNotIn('Final temperature 1', r.cells('method_oven_form'))
        r.enter('method_oven_form', 'Rate 1', '20')
        r.enter('method_oven_form', 'Final temperature 1', '120')
        r.enter('method_oven_form', 'Final time 1', '1')
        c = r.cells('method_oven_form')
        self.assertIn('Rate 2', c)
        self.assertEqual(c['Run time'], '8.00 min')                                 # 5 + (120 - 80) / 20 + 1
        r.ready()
        r.tap('start')
        r.step(300 + 60)
        self.assertAlmostEqual(r.val('thermal.oven.setpoint_k'), 353.15 + 20 / 60 * 59, delta=0.6)   # ramping at 20 C/min
        r.step(150)
        self.assertIsNone(r.val('gc.run'))
        self.assertAlmostEqual(r.val('thermal.oven.setpoint_k'), 353.15)          # back to the initial temperature
        self.assertEqual(r.status()['state'], 'COOLDOWN')
        r.tab('settings')
        r.tap('set_configuration')
        r.tap('side_cfg_oven')
        r.enter('cfg_oven_form', 'Maximum temperature', '100')
        r.tap('apply')
        self.assertAlmostEqual(r.val('gc.settings')['oven.max_temp_K'], 373.15)
        r.tab('home')
        r.tap('fp_oven_t')
        r.type('150')
        r.ov('OK')
        self.assertEqual(r.overlay()['dialog'], 'err_invalid')                    # above the configured maximum: refused
        self.assertAlmostEqual(r.val('thermal.oven.setpoint_k'), 353.15)

    def test_detector_flame_lit_offset_and_shutdown(self):
        r = Rig()
        r.tab('method')
        r.tap('side_method_detector')
        for label in ('H2 flow', 'Air flow', 'Makeup flow', 'Flame'):
            r.choose('method_detector_form', label, 'On')
        r.step(5)
        self.assertTrue(r.val('gc.actuals')['flame_lit'])
        self.assertEqual(r.cells('method_detector_form')['Signal'], '5.0 pA')
        self.assertEqual(r.val('gc.emf')['detector.ignitor']['value'], 1)
        r.h.set_gas_supply(hydrogen=False)                                        # flame-out: three reignition tries, then shutdown (p.95)
        r.step(10)
        self.assertEqual(r.status()['state'], 'SHUTDOWN')
        self.assertIn('Front Detector: Flame out', [a['text'] for a in r.val('gc.alerts')])
        r.tab('diag')
        r.row('dg_alerts', 'Front Detector: Flame out', col=1)
        r.ov('Turn Shutdown Zone Off')
        d = method(r, 'detector')
        self.assertFalse(d['flame_on'] or d['h2_on'] or d['air_on'])
        self.assertTrue(d['makeup_on'])
        r.h.set_gas_supply(hydrogen=True)
        r.tab('settings')
        r.tap('set_configuration')
        r.tap('side_cfg_detectors')
        r.enter('cfg_detectors_form', 'Lit Offset', '9.0')                         # above the lit baseline: ignition is judged failed
        r.tap('apply')
        r.tab('method')
        r.tap('side_method_detector')
        for label in ('H2 flow', 'Air flow', 'Flame'):
            r.choose('method_detector_form', label, 'On')
        r.step(10)
        self.assertIn('Front Detector: Flame failed to ignite', [a['text'] for a in r.val('gc.alerts')])

    def test_run_table_events(self):
        r = Rig()
        r.tab('method')
        r.tap('side_method_detector')
        for label in ('H2 flow', 'Air flow', 'Flame'):
            r.choose('method_detector_form', label, 'On')
        r.tap('side_method_events')
        r.tap('ev_add')
        b = [b for b in r.overlay()['buttons'] if b['id'] == 'row:time'][0]
        r.touch(b['rect'][0] + 5, b['rect'][1] + 5)
        r.type('1')
        r.ov('OK')
        b = [b for b in r.overlay()['buttons'] if b['id'] == 'row:type'][0]
        r.touch(b['rect'][0] + 5, b['rect'][1] + 5)
        r.ov('Front Inlet Septum Purge')
        r.ov('Add')
        self.assertEqual(method(r, 'events'), [dict(time_s=60.0, type='Front Inlet Septum Purge', position='Off', setpoint=None)])
        self.assertEqual(r.widgets()['ev_list']['cells'][0], ['1.00', 'Front Inlet Septum Purge', 'Off'])
        r.ready()
        r.tap('start')
        r.step(70)
        self.assertEqual(r.val('gc.actuals')['septum_purge_m3_s'], 0.0)            # the event switched the septum purge off
        self.assertIn('Event: Front Inlet Septum Purge Off', [e['text'] for e in r.val('gc.logs')['run']])
        r.tap('stop')
        self.assertTrue(method(r, 'inlet')['septum_purge_on'])                    # restored at the end of the run
        r.tab('method')
        r.tap('side_method_events')
        r.row('ev_list', '1.00')
        r.ov('Delete')
        self.assertEqual(method(r, 'events'), [])

    def test_analog_out(self):
        r = Rig()
        r.tab('method')
        r.tap('side_method_analog')
        r.choose('method_analog_form', 'Signal type', 'Oven Temperature')
        r.choose('method_analog_form', 'Zero', 'On')
        r.enter('method_analog_form', 'Zero setpoint', '50')
        r.enter('method_analog_form', 'Range', '1')
        self.assertAlmostEqual(r.val('gc.actuals')['analog_out'], (r.val('gc.actuals')['oven_K'] - 273.15 - 50) / 2, places=6)


class HomeDiagLogs(unittest.TestCase):
    def test_status_page_add_remove(self):
        r = Rig()
        r.tap('side_home_status')
        self.assertEqual([c[0] for c in r.widgets()['st_list']['cells']], ['Oven Temperature', 'Front Inlet Pressure', 'Column 1 Flow',
                                                                           'Front Detector Temperature'])
        r.tap('st_add')
        b = [b for b in r.overlay()['buttons'] if b['id'] == 'row:param'][0]
        r.touch(b['rect'][0] + 5, b['rect'][1] + 5)
        r.ov('Front Detector Signal')
        r.ov('Add')
        self.assertEqual(r.val('gc.status_params')[-1], 'signal')
        r.row('st_list', 'Column 1 Flow')
        self.assertEqual(r.overlay()['lines'], ['Remove Column 1 Flow from the list?'])
        r.ov('Yes')
        self.assertNotIn('column_flow', r.val('gc.status_params'))

    def test_plot_options_zoom_and_stop(self):
        r = Rig()
        r.tap('side_home_plot')
        r.tap('pl_signal')
        b = [b for b in r.overlay()['buttons'] if b['id'] == 'row:signal'][0]
        r.touch(b['rect'][0] + 5, b['rect'][1] + 5)
        r.ov('Oven Temperature (°C)')
        b = [b for b in r.overlay()['buttons'] if b['id'] == 'row:x_axis'][0]
        r.touch(b['rect'][0] + 5, b['rect'][1] + 5)
        r.type('10')
        r.ov('OK')
        r.ov('Apply')
        p = r.val('gc.plot')
        self.assertEqual((p['signal'], p['x_axis_s']), ('oven_temp', 600))
        r.step(30)
        self.assertEqual(r.widgets()['pl_chart']['x_range'][1] - r.widgets()['pl_chart']['x_range'][0], 600)
        r.tap('pl_chart')
        self.assertEqual(r.val('gc.plot')['zoom'], 2)
        self.assertEqual(r.widgets()['pl_chart']['x_range'][1] - r.widgets()['pl_chart']['x_range'][0], 300)
        r.tap('pl_signal')
        r.ov('Stop Plot')
        self.assertFalse(r.val('gc.plot')['running'])
        n = len(r.val('gc.plot')['data'])
        r.step(10)
        self.assertEqual(len(r.val('gc.plot')['data']), n)

    def test_diagnostic_tests_and_health_report(self):
        r = Rig()
        r.tab('diag')
        r.tap('dg_tests')
        r.tap('dt_inlets')
        r.tap('test_leak_restriction')
        r.tap('tp_start')
        self.assertEqual(r.status()['state'], 'DIAGNOSTIC MODE')
        self.assertIn('In Progress', r.text('tp_state'))
        r.step(60)
        self.assertEqual(r.val('gc.diagnostics')['results']['leak_restriction']['result'], 'Passed')
        self.assertIn('Result: Passed', r.text('tp_state'))
        r.call(r.E, 'sensor.configure_error', errors={'inlet_leak_m3_s': 2 * ML})
        r.tap('tp_start')
        r.step(60)
        self.assertEqual(r.val('gc.diagnostics')['results']['leak_restriction']['result'], 'Failed')
        r.tap('tp_start')
        r.tap('tp_cancel')
        r.ov('Yes')
        self.assertEqual(r.val('gc.diagnostics')['results']['leak_restriction']['result'], 'Aborted')
        r.tap('tp_close')
        r.tab('diag')
        r.tap('dg_report')
        lines = [c[0] for c in r.widgets()['rp_lines']['cells']]
        self.assertEqual(lines[0], 'System information')
        self.assertTrue(any('Leak & Restriction Test: Aborted' in x for x in r.cells('rp_lines')))

    def test_logs_pages(self):
        r = Rig()
        r.tab('logs')
        r.tap('lg_system')
        self.assertEqual(r.widgets()['lgsystem_list']['cells'][0][1], 'Power on successful')
        r.tap('lgsystem_close')
        r.tap('lg_run')
        self.assertEqual(r.widgets()['lgrun_list']['empty_text'], 'No entries')


class MaintenanceEMF(unittest.TestCase):
    def test_counter_limits_reset_and_days(self):
        r = Rig()
        r.tab('maint')
        r.tap('mt_inlets')
        r.row('maint_inlets_list', 'Liner (days)')
        self.assertEqual(r.overlay()['dialog'], 'emf_details')
        b = [b for b in r.overlay()['buttons'] if b['id'] == 'row:warn_on'][0]
        r.touch(b['rect'][0] + 5, b['rect'][1] + 5)
        r.ov('Enabled')
        b = [b for b in r.overlay()['buttons'] if b['id'] == 'row:warn'][0]
        r.touch(b['rect'][0] + 5, b['rect'][1] + 5)
        r.type('20')
        r.ov('OK')
        r.ov('Apply')
        c = r.val('gc.emf')['inlet.liner_time']
        self.assertEqual((c['warn_on'], c['warn'], c['due']), (True, 20, 30))
        r.tab('settings')
        r.tap('set_system')
        r.tap('side_sys_datetime')
        self.assertEqual(r.cells('sys_datetime_form')['Current Date'], '2026-01-05')
        r.call(r.A, 'clock.set', date='2026-01-30', time='08:00')                  # 25 days later: warning (time counters follow the clock)
        r.step(1)
        self.assertEqual(r.val('gc.emf')['inlet.liner_time']['level'], 'warning')
        self.assertEqual(r.text('tab_maint'), 'Maintenance !')
        r.tab('maint')
        self.assertIn('[!]', r.text('mt_inlets'))
        r.tap('mt_inlets')
        r.row('maint_inlets_list', 'Liner (days)')
        r.ov('Reset Counter')
        r.ov('Yes')
        c = r.val('gc.emf')['inlet.liner_time']
        self.assertEqual((c['value'], c['level']), (0.0, 'ok'))
        self.assertIn('Inlets: Liner (days) counter reset (User Reset)', [e['text'] for e in r.val('gc.logs')['maintenance']])

    def test_replace_septum_procedure(self):
        r = Rig()
        r.tab('maint')
        r.tap('mt_inlets')
        r.tap('maint_inlets_perform')
        r.tap('proc_septum')
        r.ov('Start Maintenance')
        self.assertEqual(r.page()[1], 'maint_proc')
        self.assertIn('Cooling down', r.text('mp_text'))
        self.assertFalse(method(r, 'column')['on'])                               # inlet pressure off for the septum change
        r.step(1800)
        self.assertEqual(r.text('mp_text'), 'Replace the septum, then select Continue.')
        r.tap('mp_continue')
        self.assertIsNone(r.val('gc.maintenance'))
        self.assertTrue(method(r, 'column')['on'])
        self.assertAlmostEqual(method(r, 'inlet')['temperature_K'], 523.15)
        self.assertEqual(r.val('gc.emf')['inlet.septum_inj']['resets'][-1]['how'], 'Maintenance Reset')
        r.tab('maint')
        r.tap('mt_logs')
        self.assertEqual(r.page()[1], 'log_maintenance')


class SettingsPages(unittest.TestCase):
    def test_configuration_column_lock_and_readiness(self):
        r = Rig()
        r.tab('settings')
        r.tap('set_configuration')
        r.tap('side_cfg_columns')
        r.enter('cfg_columns_form', 'Length', '60')
        r.tap('apply')
        self.assertEqual(r.val('gc.settings')['column.length_m'], 60.0)
        self.assertGreater(r.val('gc.actuals')['column_pressure_Pa'] / PSI, 20)  # longer column: higher head pressure for the same flow
        r.choose('cfg_columns_form', 'Column ID Lock', 'On')
        r.tap('apply')
        r.row('cfg_columns_form', 'Column Type')
        r.ov('Cancel')
        w = r.widgets()['cfg_columns_form']
        self.assertIn([c[0] for c in w['cells']].index('Length'), w['disabled_rows'])   # dimensions locked
        r.call(r.A, 'settings.configure', expect='rejected', settings={'column.length_m': 30.0})
        r.tap('side_cfg_readiness')
        r.choose('cfg_readiness_form', 'Oven', 'Disabled')
        r.tap('apply')
        r.call(r.A, 'thermal.set_temperature', zone='oven', temperature_K=423.15)
        r.step(5)
        self.assertTrue(r.status()['ready'])                                     # oven readiness ignored

    def test_scheduler_sleep_wake_and_clock_table(self):
        r = Rig()
        sleep = copy.deepcopy(r.val('gc.method'))
        sleep['oven']['initial_K'] = 323.15
        r.h.store_method('SLEEP', sleep)
        r.tab('settings')
        r.tap('set_scheduler')
        r.choose('sc_form', 'Wake to last active method before sleep', 'On')
        r.choose('sc_form', 'Monday: Set Sleep Method', 'On')
        r.choose('sc_form', 'Tuesday: Set Wake Method', 'On')
        r.row('sc_form', 'Monday: Sleep Time')
        self.assertEqual(r.overlay()['kind'], 'spinner')                          # 22:00 by default: + / - picker
        r.ov('OK')
        r.tap('sc_apply')
        s = r.val('gc.settings')
        self.assertEqual((s['sched.monday.sleep_on'], s['sched.tuesday.wake_on'], s['sched.monday.sleep_time']), (True, True, '22:00'))
        r.call(r.A, 'clock.set', date='2026-01-05', time='21:57')                  # a Monday evening
        r.step(240)
        self.assertEqual(r.status()['state'], 'SLEEP')
        self.assertEqual(r.text('tray_status'), 'STATUS: SLEEP')
        self.assertAlmostEqual(method(r, 'oven')['initial_K'], 323.15)             # SLEEP method loaded
        r.call(r.A, 'clock.set', date='2026-01-06', time='06:58')
        r.step(180)                                                                # 07:00 Tuesday: wake to the last active method
        self.assertNotEqual(r.status()['state'], 'SLEEP')
        self.assertAlmostEqual(method(r, 'oven')['initial_K'], 353.15)
        r.tap('sc_add')
        b = [b for b in r.overlay()['buttons'] if b['id'] == 'row:type'][0]
        r.touch(b['rect'][0] + 5, b['rect'][1] + 5)
        r.ov('Blank Run')
        r.ov('Add')
        entry = r.val('gc.scheduler')['clock_table'][0]
        self.assertEqual(entry, dict(type='Blank Run', frequency='Daily', time='06:00'))
        r.row('sc_list', '06:00')
        r.ov('Yes')
        self.assertEqual(r.val('gc.scheduler')['clock_table'], [])

    def test_clock_table_blank_run_executes(self):
        r = Rig()
        now = r.val('device.datetime')
        hh, mm = int(now[11:13]), int(now[14:16]) + 2
        r.call(r.A, 'scheduler.clock_add', type='Blank Run', frequency='Daily', time=f'{hh:02d}:{mm:02d}')
        r.step(120)
        self.assertEqual(r.val('gc.run')['kind'], 'blank')

    def test_calibration_zero(self):
        r = Rig()
        total = r.val('gc.actuals')['total_flow_m3_s']
        split = r.h._process.method.split_ratio * r.h._process.method.column_flow_m3_s
        r.call(r.E, 'sensor.configure_error', errors={'inlet_flow_offset_m3_s': 0.5 * ML, 'inlet_pressure_offset_Pa': 300.0})
        self.assertAlmostEqual(r.val('gc.actuals')['total_flow_m3_s'], total)     # the total flow controller holds its reading ...
        m = r.h._process.method
        self.assertAlmostEqual((split - m.split_ratio * m.column_flow_m3_s) / ML, 0.5, places=6)   # ... and delivers 0.5 mL/min less split flow
        r.tab('settings')
        r.tap('set_calibration')
        r.row('cal_inlets_form', 'Flow sensor zero')
        m = r.h._process.method
        self.assertAlmostEqual(m.split_ratio * m.column_flow_m3_s, split)          # zeroed: the split vent flow is the setpoint again
        r.row('cal_inlets_form', 'Pressure sensor zero')                           # needs the supply line disconnected
        self.assertEqual(r.overlay()['dialog'], 'err_supply')
        r.ov('OK')
        r.h.set_gas_supply(carrier=False)
        r.row('cal_inlets_form', 'Pressure sensor zero')
        self.assertIn('Pressure sensor zero', r.cells('cal_inlets_form'))
        self.assertEqual(r.val('gc.calibration')['zero_times'].keys() >= {'inlet_flow', 'inlet_pressure'}, True)
        r.tap('side_cal_detectors')
        r.row('cal_detectors_form', 'H2 flow sensor zero')
        self.assertEqual(r.overlay()['dialog'], 'err_gas')                         # H2 off in the method: not flowing

    def test_power_saving_dim_off_and_preview(self):
        r = Rig()
        r.call(r.A, 'settings.configure', settings={'power.dim_on': True, 'power.dim_after': 30, 'power.dim_unit': 's', 'power.dim_to_pct': 10,
                                                    'power.off_on': True, 'power.off_after': 2, 'power.off_unit': 'min'})
        r.tap('tab_home')                                                           # a touch: the inactivity time starts now
        r.step(31)
        self.assertEqual(r.frame()['displays'][TOUCH]['brightness'], 10)
        r.step(100)
        self.assertEqual(r.frame()['displays'][TOUCH]['brightness'], 0)
        self.assertFalse(r.tap('tab_method'))                                      # the touch only wakes the screen
        self.assertEqual(r.page()[1], 'home_flow')
        self.assertEqual(r.frame()['displays'][TOUCH]['brightness'], 100)
        r.tab('settings')
        r.tap('set_system')
        r.tap('side_sys_power')
        r.enter('sys_power_form', 'Display brightness', '60')
        r.row('sys_power_form', 'Preview (for 5 s)')
        self.assertEqual(r.frame()['displays'][TOUCH]['brightness'], 60)
        r.step(6)
        self.assertEqual(r.frame()['displays'][TOUCH]['brightness'], 100)
        r.enter('sys_power_form', 'Dim to', '80')
        r.tap('apply')
        self.assertEqual(r.overlay()['dialog'], 'err_invalid')                     # dim value must be below the brightness
        r.ov('OK')
        r.enter('sys_power_form', 'Dim to', '20')
        r.tap('apply')
        self.assertEqual(r.val('gc.settings')['power.brightness_pct'], 60)

    def test_locale_datetime_access_storage_setup(self):
        r = Rig()
        r.tab('settings')
        r.tap('set_system')
        r.tap('side_sys_locale')
        r.choose('sys_locale_form', 'Language', 'Deutsch')
        r.tap('apply')
        self.assertEqual(r.text('tab_settings'), 'Einstellungen')
        self.assertEqual(r.text('start'), 'Start')
        r.call(r.A, 'settings.configure', settings={'locale.language': 'English'})
        r.tap('side_sys_datetime')
        r.row('sys_datetime_form', 'Current Time')
        self.assertEqual(r.overlay()['kind'], 'spinner')
        r.ov('+')
        r.ov('OK')
        r.tap('apply')
        self.assertEqual(r.val('device.datetime')[11:13], '09')
        r.tap('side_sys_access')
        r.enter('sys_access_form', 'PIN', '4321')
        r.tap('apply')
        self.assertEqual(r.val('gc.settings')['access.pin'], '4321')
        r.tap('side_sys_storage')
        self.assertIn('\\\\8890GC\\results', r.text('st_path'))
        r.tap('side_sys_setup')
        r.tap('su_run')
        self.assertEqual(r.page()[1], 'setup_wizard')
        r.tap('wz_next')
        r.choose('wz_form2', 'Pressure Units', 'bar')
        for _ in range(4):
            r.tap('wz_next')
        r.tap('wz_finish')
        self.assertEqual(r.val('gc.settings')['misc.pressure_units'], 'bar')
        self.assertEqual(r.page()[1], 'settings_main')

    def test_service_about_tools_power_help(self):
        r = Rig()
        r.tab('settings')
        r.tap('set_service')
        r.tap('sv_2')
        self.assertEqual(r.widgets()['sv_info']['cells'][0][0], 'Temperature')
        r.tap('sv_setup')
        self.assertEqual(r.overlay()['lines'], ['No new device was found.'])
        r.ov('OK')
        r.tap('sv_close')
        r.tap('set_about')
        self.assertEqual(r.widgets()['ab_list']['cells'][1], ['Serial number', 'SIM-8890B-0001'])
        r.tap('ab_close')
        r.tap('set_tools')
        r.tap('tl_cc2_start')
        self.assertEqual(r.val('gc.run')['kind'], 'column_comp')
        r.step(301)
        self.assertIn('2', r.val('gc.column_compensation'))
        r.tap('tl_close')
        r.tap('set_power')
        self.assertEqual(r.overlay()['dialog'], 'power_options')
        r.ov('Shut Down')
        self.assertFalse(r.val('power.enabled'))
        r.switch('on')
        r.step(31)
        r.tab('help')
        r.ov('About This Screen')
        self.assertEqual(r.overlay()['lines'][0][:9], 'Flow path')


class SyncAudit(unittest.TestCase):
    """Sync audit 2026-10-19: EPC sensor errors act on the delivered gas (readings at the setpoints), Slow Cool Down Mode, Fast Peaks, the
    electrometer leakage in the recorded chromatogram, language, panel commands as the gesturing session."""

    def test_epc_holds_the_reading_and_the_true_flow_moves(self):
        r = Rig()
        a0 = r.val('gc.actuals')
        flow0 = r.h._process.method.column_flow_m3_s
        r.call(r.E, 'sensor.configure_error', errors={'inlet_pressure_offset_Pa': 3000.0, 'h2_offset_m3_s': 2 * ML})
        a = r.val('gc.actuals')
        self.assertAlmostEqual(a['inlet_pressure_Pa'], a0['inlet_pressure_Pa'])  # the reading stays at the setpoint (as on the screen)
        self.assertAlmostEqual(a['column_flow_m3_s'], a0['column_flow_m3_s'])
        s = r.val('gc.settings')
        T = r.h._process.zones['oven'].temperature_K
        true = rt._g8_flow_for_pressure(s, a0['inlet_pressure_Pa'] - 3000.0, T)
        self.assertLess(r.h._process.method.column_flow_m3_s, flow0 * 0.99)       # the real head pressure is 3 kPa lower: less column flow
        self.assertAlmostEqual(r.h._process.method.column_flow_m3_s, true, places=15)
        d = r.val('gc.method')['detector']
        self.assertAlmostEqual(r.h._process.method.hydrogen_flow_m3_s, d['h2_flow_m3_s'] - 2 * ML)
        r.h.set_gas_supply(carrier=False)                                         # zero the pressure sensor with the supply line disconnected
        self.assertAlmostEqual(r.val('gc.actuals')['inlet_pressure_Pa'], 3000.0)  # without carrier it reads its own offset
        r.call(r.A, 'calibration.zero', sensor='inlet_pressure')
        r.h.set_gas_supply(carrier=True)
        self.assertAlmostEqual(r.h._process.method.column_flow_m3_s, flow0, places=15)

    def test_slow_cool_down_mode(self):
        temps = {}
        for slow in (False, True):
            r = Rig()
            r.call(r.A, 'settings.configure', settings={'oven.slow_cooldown': slow})
            r.call(r.A, 'thermal.set_temperature', zone='oven', temperature_K=473.15)
            r.step(1800)
            r.call(r.A, 'thermal.set_temperature', zone='oven', temperature_K=323.15)
            r.step(120)
            temps[slow] = r.val('gc.actuals')['oven_K']
        self.assertGreater(temps[True], temps[False] + 5.0)                        # the fan runs slower: the oven cools more slowly

    def test_fast_peaks_sets_the_analog_bandwidth(self):
        lags = {}
        for fast in (False, True):
            r = Rig()
            r.call(r.A, 'method.configure', component='analog_out', settings={'signal': 'Oven Temperature', 'fast_peaks': fast})
            r.call(r.A, 'thermal.set_temperature', zone='oven', temperature_K=473.15)
            T0 = r.h._process.zones['oven'].temperature_K
            r.h.host_step(1.0)
            T1 = r.h._process.zones['oven'].temperature_K
            r.h.host_step(1.0)
            T2 = r.h._process.zones['oven'].temperature_K
            self.assertGreater(T2 - T1, 0.1)                                      # heating ramp
            lags[fast] = (T2 - 273.15) - r.val('gc.actuals')['analog_out']
            tau = 1 / (2 * 3.141592653589793 * (8.0 if fast else 1.6))
            self.assertAlmostEqual(lags[fast], (T2 - T1) * tau, delta=0.01 * (T2 - T1) * tau + 1e-9)   # first order: lag = slope x tau
        self.assertGreater(lags[False], 4 * lags[True])

    def test_leakage_in_the_recorded_chromatogram(self):
        chrom = {}
        for leak in (0.0, 2e-11):
            r = Rig()
            r.call(r.E, 'sensor.configure_error', errors={'fid_leakage_A': leak})
            r.call(r.A, 'lid.set_angle', angle_rad=1.2)
            r.call(r.A, 'sample.load', sample_id='fixture')
            r.call(r.A, 'lid.set_angle', angle_rad=0.0)
            chrom[leak] = r.call(r.A, 'chromatography.measure')['chromatogram']
        self.assertEqual(chrom[0.0]['peak_area_C'], chrom[2e-11]['peak_area_C'])
        for a, b in zip(chrom[0.0]['fid_current_A'], chrom[2e-11]['fid_current_A']):
            self.assertAlmostEqual(b - a, 2e-11, places=20)

    def test_language_without_translations_is_stored_and_shown(self):
        r = Rig()
        r.call(r.A, 'settings.configure', settings={'locale.language': 'Italiano'})
        self.assertEqual(r.val('gc.settings')['locale.language'], 'Italiano')
        r.tab('home')
        self.assertEqual(r.text('tab_settings'), 'Settings')                       # no Italian texts in our manuals: English

    def test_panel_commands_run_as_the_gesturing_session(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        b = r.h.session('agent_b')
        seen = []
        orig = rt._G8_COMMANDS['run.prep']

        def spy(rt_, dev, process, state, a, t):
            seen.append(rt_._caller)
            return orig(rt_, dev, process, state, a, t)
        rt._G8_COMMANDS['run.prep'] = spy
        try:
            r.tab('home')
            x, y, w, h = r.widgets()['prep_run']['rect']
            b.invoke(capability_id='panel.touch', arguments=dict(display_id=TOUCH, x=x + w // 2, y=y + h // 2), request_id='b1')
        finally:
            rt._G8_COMMANDS['run.prep'] = orig
        self.assertEqual(seen, ['agent_b'])
        self.assertIsNone(r.h._caller)


class Callers(unittest.TestCase):
    """Consistency sweep 2026-10-19: runs, tests and maintenance belong to the caller that started them; another caller can neither stop,
    restart / shut down (touchscreen) nor change them, on the touchscreen or the direct path; the refusal shows as a dialog; the power switch is
    a hard switch (never refused); the evaluator is unrestricted."""
    def test_run_belongs_to_its_caller(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        r.ready()
        r.call(r.A, 'run.start')
        self.assertEqual(r.h._device['run']['owner'], '__agent__')
        r.step(30)
        before = r.h._device['run']['start_s'], r.val('power.enabled')
        for cap, args in (('run.stop', {}), ('system.restart', {}), ('system.shutdown', {}),
                          ('method.configure', dict(component='inlet', settings={'split_ratio': 20.0})),
                          ('thermal.set_temperature', dict(zone='inlet', temperature_K=520.0))):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertIsNotNone(r.h._device['run'])
        r.tab('home')
        x, y, w, h = r.widgets()['stop']['rect']                                       # B taps Stop on the touchscreen
        self.assertFalse(B.invoke(capability_id='panel.touch', arguments=dict(display_id=TOUCH, x=x + w // 2, y=y + h // 2),
                                  request_id='b1')['result']['accepted'])
        self.assertEqual((r.h._device['run']['start_s'], r.val('power.enabled')), before)
        self.assertEqual((r.overlay()['title'], r.overlay()['lines']), ('Not allowed', ["Another user's run, test or maintenance is in progress."]))
        r.ov('OK')
        r.call(r.E, 'run.stop')                                                        # the evaluator is unrestricted
        self.assertIsNone(r.h._device['run'])
        r.ready()
        r.call(r.A, 'run.start')
        self.assertTrue(B.invoke(capability_id='panel.switch', arguments=dict(input_id='power_switch', position='off'),
                                 request_id='b2')['result']['accepted'])               # the power switch is physical
        self.assertEqual((r.val('power.enabled'), r.h._device['run']), (False, None))


class RefusalsTimeouts(unittest.TestCase):
    def test_start_not_ready_and_busy(self):
        r = Rig(warm=False)
        r.switch('on')
        r.step(31)
        self.assertTrue(r.tap('start') is False)
        self.assertEqual(r.overlay()['dialog'], 'err_not_ready')
        r.ov('OK')
        r.ready()
        r.tap('start')
        r.tab('diag')
        r.tap('dg_tests')
        r.tap('dt_inlets')
        r.tap('test_gas_supply')
        self.assertFalse(r.tap('tp_start'))
        self.assertEqual(r.overlay()['dialog'], 'err_busy')

    def test_door_button(self):
        r = Rig()
        self.assertTrue(r.call(r.A, 'panel.press', input_id='oven_door_button')['accepted'])
        self.assertAlmostEqual(r.val('lid.angle_rad'), 0.2)
        self.assertIn('Oven door open', r.status()['reasons'])
        self.assertFalse(r.call(r.A, 'panel.press', input_id='oven_door_button')['accepted'])   # already open: nothing happens

    def test_inlet_flow_shutdown_time_out(self):
        r = Rig()
        r.h.set_gas_supply(carrier=False)
        r.step(110)
        self.assertNotEqual(r.status()['state'], 'SHUTDOWN')
        r.step(12)
        self.assertEqual(r.status()['state'], 'SHUTDOWN')
        self.assertEqual(r.status()['message'], 'Inlet flow shutdown')

    def test_power_cycle_clears_shutdown(self):
        r = Rig()
        r.h.set_gas_supply(carrier=False)
        r.step(130)
        r.h.set_gas_supply(carrier=True)
        r.switch('off')
        r.switch('on')
        r.step(31)
        self.assertEqual(r.val('gc.alerts'), [])


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        a, b = Rig(), Rig()
        a.tab('method')
        a.enter('method_inlet_form', 'Split ratio', '35')
        a.tap('side_method_column')
        a.enter('method_column_form', 'Flow', '1.5')
        b.call(b.A, 'method.configure', component='inlet', settings={'split_ratio': 35.0})
        b.call(b.A, 'method.configure', component='column', settings={'flow_m3_s': 1.5 * ML})
        ma, mb = a.val('gc.method'), b.val('gc.method')
        self.assertEqual(ma['inlet'], mb['inlet'])
        self.assertAlmostEqual(ma['column']['flow_m3_s'], mb['column']['flow_m3_s'], places=18)
        self.assertEqual(a.val('gc.actuals')['split_vent_m3_s'], b.val('gc.actuals')['split_vent_m3_s'])

    def test_every_terminal_command_once(self):
        r = Rig()
        meta = rt.export_agilent_gc8890b_contract_metadata()
        r.call(r.A, 'lid.set_angle', angle_rad=1.2)
        r.call(r.A, 'sample.load', sample_id='fixture')
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        order = ['sensor.configure_error', 'settings.configure', 'clock.set', 'status.configure', 'plot.configure', 'method.configure',
                 'method.event_save', 'method.event_delete', 'scheduler.clock_add', 'scheduler.clock_delete', 'calibration.zero', 'display.preview',
                 'emf.configure', 'emf.reset', 'run.prep', 'run.stop', 'diagnostics.start_test', 'diagnostics.cancel_test', 'maintenance.start',
                 'maintenance.abort', 'tools.column_compensation', 'run.stop', 'run.start', 'run.stop', 'system.restart']
        done = set()
        for cap in order:
            m = meta['capabilities'][cap]
            if cap == 'run.start':
                r.step(31)
                r.ready()
            args = dict(m['example_arguments'][0])
            sess = r.E if m['visibility'] != 'agent' else r.A
            before = r.E.read()['observations']
            res = r.call(sess, cap, **args)
            self.assertEqual(validate_value(res, m['output_schema']), [], cap)
            after = r.E.read()['observations']
            changed = {k for k in after if k in before and after[k]['value'] != before[k]['value']} - {'gc.actuals', 'gc.status', 'gc.plot',
                                                                                                        'device.datetime', 'gc.system'}
            self.assertTrue(changed <= set(m['state_ids']) | {'gc.logs'}, (cap, changed - set(m['state_ids'])))
            done.add(cap)
        r.step(31)                                                                 # after the restart
        r.call(r.A, 'run.prep')
        r.call(r.A, 'maintenance.start', procedure='septum')
        r.step(1800)
        r.call(r.A, 'maintenance.continue')
        r.call(r.A, 'diagnostics.start_test', test='gas_supply')
        r.step(31)
        r.h.set_gas_supply(carrier=False)
        r.step(130)
        alert = r.val('gc.alerts')[0]
        r.call(r.A, 'diagnostics.resolve', alert_id=alert['id'], action='turn_off')
        r.h.store_method('SLEEP', r.val('gc.method'))
        rt._g8_sleep(r.h._device, r.h._process, r.h._time())
        r.call(r.A, 'system.wake')
        self.assertEqual(r.call(r.A, 'system.shutdown'), {'shut_down': True})        # consistency sweep 2026-10-19: soft power-off
        self.assertFalse(r.val('power.enabled'))
        done |= {'maintenance.continue', 'diagnostics.resolve', 'system.wake', 'system.shutdown'}
        self.assertEqual(done, set(rt._G8_TERMINAL))
        self.assertEqual(r.call(r.A, 'sensor.configure_error', expect='rejected', errors={'fid_leakage_A': 0.0})['error']['code'], 'ACCESS_DENIED')

    def test_refusals_leave_state_unchanged(self):
        r = Rig()
        before = r.h.debug_snapshot()
        for cap, args in (('method.configure', dict(component='inlet', settings={'split_ratio': 0.5})),
                          ('method.configure', dict(component='oven', settings={'nope': 1})), ('emf.configure', dict(counter='inlet.septum_inj', warn=500)),
                          ('maintenance.continue', {}), ('diagnostics.cancel_test', {}), ('scheduler.clock_delete', dict(index=3)),
                          ('calibration.zero', dict(sensor='h2')), ('settings.configure', dict(settings={'power.dim_to_pct': 99, 'power.brightness_pct': 50})),
                          ('clock.set', dict(date='2026-02-30', time='10:00')), ('system.wake', {})):
            r.call(r.A, cap, expect='rejected', **args)
        self.assertEqual(before, r.h.debug_snapshot())


class RenderEvidence(unittest.TestCase):
    def test_render_determinism_and_evidence(self):
        r = Rig()
        a, b = r.png(), r.png()
        self.assertEqual(a, b)
        self.assertEqual(r.h.panel.render(TOUCH).shape, (480, 800, 3))
        r2 = Rig()
        self.assertEqual(hashlib.sha256(r2.png()).hexdigest(), hashlib.sha256(a).hexdigest())   # same state, same picture
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {}
        r.h.panel.save_png(TOUCH, OUT / 'home.png')
        shots['home.png'] = 'Home > Flow path, status READY'
        r.tab('method')
        r.h.panel.save_png(TOUCH, OUT / 'menu.png')
        shots['menu.png'] = 'Method > Inlets (live method editor)'
        r.tab('home')
        r.h.set_gas_supply(carrier=False)
        r.step(130)
        r.tab('diag')
        r.row('dg_alerts', 'Front Inlet: Inlet flow shutdown', col=1)
        r.h.panel.save_png(TOUCH, OUT / 'error.png')
        shots['error.png'] = 'Diagnostics: inlet flow shutdown with its setpoint actions'
        r.ov('Close')
        r.tab('settings')
        r.h.panel.save_png(TOUCH, OUT / 'settings.png')
        shots['settings.png'] = 'Settings view'
        r.h.panel.save_png('status_led', OUT / 'status_led.png')
        shots['status_led.png'] = 'status indicator (yellow: shutdown)'
        (OUT / 'renders.json').write_text(json.dumps({k: dict(caption=v, sha256=hashlib.sha256((OUT / k).read_bytes()).hexdigest())
                                                      for k, v in shots.items()}, indent=1) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_has_the_panel(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_agilent_gc8890b_contract_metadata()
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for cap in list(rt._G8_TERMINAL) + sorted(rt.GC8890B_PANEL_CAPS):
            self.assertIn(cap, c['capabilities'])
            self.assertEqual(c['capabilities'][cap]['input_schema'], meta['capabilities'][cap]['input_schema'], cap)
        for comp in ('display', 'status_indicator', 'oven_door_button', 'power_switch'):
            self.assertIn(comp, c['components'])
        self.assertEqual(c['asset_version'], rt.GC8890B_ASSET_VERSION)


if __name__ == '__main__':
    unittest.main()
