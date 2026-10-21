"""Front-panel tests for CHEM-106 knauer_k7400s (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/knauer_k7400s.py):
python3 -m pytest tests/test_panel_ui_knauer_k7400s.py -q

Every key goes through the session API (panel.press / panel.switch); the LCD is read from the structured frame (panel.frame, evaluator).  The tube
and the head are handled by the host (sample.load, evaluator) and the operator's hand (head.position).  Themes: the seven key procedures of the menu
survey; units, date, relative mode, output log; panel = direct capability (same runtime state); refusals shown as > ERROR < with the state
unchanged; time-outs; render determinism and the evidence PNGs; contract entries."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import knauer_k7400s as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'osmometer_01'
OUT = ROOT / 'outputs/panel_ui/knauer_k7400s'
SURVEY_TASKS = ['measure a sample', 'two-point calibration', 'pre-calibration after changing the measuring head', 'change freeze / cooling limit',
                'switch output to PC for EuroOsmo', 'test the stirring wire', 'clear an error']
DONE_TASKS = set()


class Rig:
    def __init__(self, on=True, warm=True, plant=None, seed=None):
        self.h = rt.KnauerK7400sRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **(plant or rt.PLANT_EXAMPLE))
        self.call(self.A, 'method.configure', **rt.METHOD_EXAMPLE)
        if on:
            assert self.switch('on')
            self.step(2.5)
            assert self.screen() == 's1'
            if warm:
                self.step(300.0)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        self.h.step(dt)

    def press(self, key, n=1):
        ok = True
        for _ in range(n):
            ok = self.call(self.A, 'panel.press', input_id=key, duration_s=0.1)['accepted'] and ok
        return ok

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self):
        return [x.rstrip() for x in self.frame()['displays']['lcd']['lines']]

    def go(self, screen):
        """Vertical arrows on the rhombus field (endless loop, down = next); the cursor is first brought back to the rhombus."""
        for _ in range(8):
            if self.frame()['ui']['vars']['fld'] == 0:
                break
            self.press('key_right')
        for _ in range(8):
            if self.screen() == screen:
                return
            self.press('key_down')
        assert self.screen() == screen

    def load(self, sample):
        self.call(self.A, 'head.position', position='raised')
        self.call(self.E, 'sample.load', **sample)
        self.call(self.A, 'head.position', position='measuring')

    def unload(self):
        self.call(self.A, 'head.position', position='raised')
        self.call(self.E, 'sample.unload')

    def calibrate_field(self, field, value, presses=0):
        """CALIBRATE screen: cursor to the field, scroll its value, Start, wait, o.k.? -> Start."""
        self.go('s6')
        self.press('key_right', field)
        if presses:
            self.press('key_up', presses)
            self.press('key_right')
            self.press('key_left')
        self.load(rt.reference(value))
        assert self.press('key_start_stop')
        self.step(130.0)
        assert self.lines()[1].endswith('o.k.? <start>'), self.lines()
        assert self.press('key_start_stop')
        self.unload()

    def two_point(self):
        self.calibrate_field(1, 0.0)
        self.calibrate_field(2, 0.4, presses=9)                                  # ---- -> %%%% -> 0050 ... 0400


class SurveyProcedures(unittest.TestCase):
    def test_two_point_calibration(self):
        g = Rig()
        g.go('s6')
        self.assertEqual(g.lines(), ['CALIBRATE         mOsmol', '◆ 0000 ---- ----'])
        g.press('key_right')                                                      # first field: 0000
        g.load(rt.reference(0.0))
        g.press('key_start_stop')
        g.step(60.0)
        self.assertTrue(g.lines()[0].startswith('0000 mOsmol > '))                # the sample temperature and the elapsed time during the run
        self.assertRegex(g.lines()[1].strip(), r'^\d+:\d\d$')
        g.step(70.0)
        self.assertEqual(g.lines()[1].strip(), 'o.k.? <start>')
        self.assertIn('0000 mOsmol > ', g.lines()[0])
        g.press('key_start_stop')                                                 # accept
        self.assertEqual(g.val('calibration.points'), 1)
        g.unload()
        g.press('key_right')                                                      # second field: scroll to 0400 and leave it (stored)
        g.press('key_up', 9)
        self.assertIn('0400', g.lines()[1])
        self.assertEqual(g.val('calibration.fields')[1], None)                    # not stored before the cursor leaves the field
        g.press('key_right')
        g.press('key_left')
        self.assertEqual(g.val('calibration.fields')[1], 0.4)
        g.load(rt.reference(0.4))
        g.press('key_start_stop')
        g.step(130.0)
        g.press('key_down')                                                       # another key abandons the new value
        self.assertEqual(g.val('calibration.points'), 1)
        self.assertEqual(g.lines(), ['CALIBRATE         mOsmol', '◆ 0000 0400 ----'])
        g.unload()
        g.load(rt.reference(0.4))
        g.press('key_start_stop')
        g.step(130.0)
        g.press('key_start_stop')
        self.assertEqual((g.val('calibration.points'), g.val('calibration.valid')), (2, True))
        self.assertEqual([e['lines'][0] for e in g.val('device.output_log')], ['Calibrated 0000 mOsmol', 'Calibrated 0400 mOsmol'])
        DONE_TASKS.add('two-point calibration')

    def test_measure_a_sample(self):
        g = Rig()
        g.two_point()
        g.go('s1')
        g.load(rt.SAMPLE_EXAMPLE)
        self.assertTrue(g.press('key_start_stop'))
        g.step(30.0)
        self.assertTrue(g.lines()[0].startswith('RUN') and g.lines()[0].endswith('°'), g.lines())
        g.step(100.0)
        x = round(g.val('osmolality.display_mosm_kg'))
        self.assertEqual(g.lines()[0], '>>>    %04d mOsmol   <<<' % x)
        self.assertTrue(g.lines()[1].endswith('<next sample>'))
        self.assertAlmostEqual(x, 187, delta=2)
        self.assertEqual(g.val('device.output_log')[-1]['lines'][0], 'Measured %04d mOsmol' % x)
        DONE_TASKS.add('measure a sample')

    def test_pre_calibration(self):
        g = Rig(plant=dict(rt.PLANT_EXAMPLE, sensor_bias_k=0.3))
        g.two_point()
        g.go('s8')
        self.assertEqual(g.lines(), ['CALIBRATE-T      sample?', '◆                <start>'])
        g.load(rt.reference(0.0))
        g.press('key_start_stop')
        g.step(40.0)
        self.assertTrue(g.lines()[0].startswith('thermistor  > '))
        g.step(90.0)
        self.assertTrue(g.lines()[1].endswith('>successful<'))
        g.press('key_start_stop')                                                 # confirm: the old calibration values are deleted
        self.assertAlmostEqual(g.val('calibration.head_offset_k'), -0.3, places=2)
        self.assertEqual((g.val('calibration.points'), g.val('calibration.valid')), (0, False))
        self.assertEqual(g.lines()[1], '◆                <start>')
        g.unload()
        g.load(rt.reference(0.85))
        g.press('key_start_stop')
        g.step(130.0)
        self.assertTrue(g.lines()[1].endswith('not successful'))
        DONE_TASKS.add('pre-calibration after changing the measuring head')

    def test_change_freeze_and_cooling_limit(self):
        g = Rig()
        g.two_point()
        g.go('s7')
        self.assertEqual(g.lines(), ['COOL     freeze:  -6.0°C', '◆ cooling limit: -15.0°C'])
        g.press('key_right')
        g.press('key_down', 4)                                                    # -6.0 -> -8.0 in 0.5 steps
        g.press('key_right')                                                      # leaving the field stores it
        self.assertAlmostEqual(g.h.method['freeze_k'], 265.15)
        g.press('key_up', 2)
        g.press('key_right')
        self.assertAlmostEqual(g.h.method['cooling_limit_k'], 259.15)
        self.assertEqual(g.lines(), ['COOL     freeze:  -8.0°C', '◆ cooling limit: -14.0°C'])
        self.assertFalse(g.val('calibration.valid'))                              # a new setting demands a new calibration
        g.load(rt.reference(0.0))
        g.press('key_right')
        g.press('key_up')
        self.assertFalse(g.press('key_right'))                                   # refused with a sample in place
        self.assertEqual(g.lines(), ['       > ERROR <', 'REMOVE SAMPLE FIRST'])
        self.assertAlmostEqual(g.h.method['freeze_k'], 265.15)
        DONE_TASKS.add('change freeze / cooling limit')

    def test_switch_output_to_pc(self):
        g = Rig()
        g.go('s2')
        self.assertEqual(g.lines()[0], 'Data Output      PRINTER')
        g.press('key_right')
        g.press('key_up')
        self.assertEqual(g.val('device.settings')['data_output'], 'PRINTER')
        g.press('key_right')
        self.assertEqual(g.val('device.settings')['data_output'], 'PC')
        self.assertEqual(g.lines()[0], 'Data Output           PC')
        g.two_point()
        self.assertEqual(g.val('device.output_log')[-1]['port'], 'PC')
        DONE_TASKS.add('switch output to PC for EuroOsmo')

    def test_stirring_wire(self):
        g = Rig()
        self.assertFalse(g.press('key_vib'))                                      # no tube: refused
        self.assertEqual(g.lines()[1], 'NO SAMPLE TUBE')
        g.press('key_left')
        g.load(rt.reference(0.0))
        self.assertTrue(g.press('key_vib'))
        self.assertTrue(g.val('vibrator.active'))
        g.step(5.1)
        self.assertFalse(g.val('vibrator.active'))
        g.go('s6')
        g.press('key_right')
        g.press('key_start_stop')
        g.step(10.0)
        self.assertFalse(g.press('key_vib'))                                      # during CALIBRATE: no function
        self.assertFalse(g.val('vibrator.active'))
        DONE_TASKS.add('test the stirring wire')

    def test_clear_an_error(self):
        g = Rig(plant=dict(rt.PLANT_EXAMPLE, fault='no_freeze'))
        g.go('s6')
        g.press('key_right')
        g.load(rt.reference(0.0))
        g.press('key_start_stop')
        g.step(90.0)
        self.assertEqual(g.screen(), 'error')
        self.assertEqual(g.lines(), ['       > ERROR <', 'NO FREEZE'])
        self.assertEqual(g.val('device.output_log')[-1]['lines'][0], 'NO FREEZE')  # the printer gets the message without the error type
        g.press('key_up')                                                         # any key: the main screen
        self.assertEqual(g.screen(), 's1')
        g.step(10.0)
        self.assertEqual(g.screen(), 's1')
        DONE_TASKS.add('clear an error')


class DeviceFunctions(unittest.TestCase):
    def test_result_unit_temperature_unit_and_date(self):
        g = Rig()
        g.two_point()
        g.go('s1')
        g.press('key_right')
        g.press('key_up')
        g.press('key_left')                                                       # unit field: °C
        self.assertEqual(g.val('device.settings')['result_unit'], 'degC')
        g.load(rt.SAMPLE_EXAMPLE)
        g.press('key_start_stop')
        g.step(130.0)
        self.assertEqual(g.lines()[0], '>>>  %+7.3f °C      <<<' % (g.val('freezing.temperature_k') - 273.15))
        g.go('s3')
        g.press('key_right')
        g.press('key_up')
        g.press('key_right')
        self.assertIn('°F', g.lines()[0])
        self.assertRegex(g.lines()[1], r'cooler: +\d+\.\d°F$')
        g.go('s5')
        g.press('key_right', 3)                                                    # year
        g.press('key_down', 11)                                                    # 26 -> 15
        g.press('key_left')
        g.press('key_down', 1)                                                     # month 01 -> 12 (wraps)
        g.press('key_left')
        self.assertTrue(g.val('device.datetime').startswith('01.12.15'))
        g.go('s4')
        self.assertEqual(g.lines(), ['INFO S/N: 000136429', '◆ Firmware: 01.18'])

    def test_relative_mode(self):
        g = Rig()
        g.calibrate_field(1, 0.0)
        g.calibrate_field(2, 0.4, presses=1)                                      # %%%%: results in % of this point
        g.go('s6')
        self.assertEqual(g.lines(), ['CALIBRATE              %', '◆ 0000 %%%% ----'])
        g.go('s1')
        g.load(rt.reference(0.2))
        g.press('key_start_stop')
        g.step(130.0)
        self.assertRegex(g.lines()[0], r'^>>>\s+\d+\.\d %\s+<<<$')
        self.assertAlmostEqual(g.val('osmolality.relative_percent'), 50.0, delta=1.5)

    def test_cooling_cavity_follows_the_run(self):
        g = Rig()
        g.go('s6')
        g.press('key_right')
        g.load(rt.reference(0.0))
        g.press('key_start_stop')
        g.step(20.0)
        g.press('key_start_stop')                                                 # Stop during the run
        self.assertEqual(g.val('acquisition.phase'), 'cancelled')
        self.assertLess(g.val('cooler.temperature_k'), 285.0)


class RefusalsTimeoutsAndEquivalence(unittest.TestCase):
    def test_refusals_show_errors_and_change_nothing(self):
        g = Rig(warm=False)
        g.load(rt.reference(0.0))
        before = g.h.read('evaluator')
        g.press('key_start_stop')                                                 # warming up
        self.assertEqual(g.lines(), ['       > ERROR <', 'NOT READY'])
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'NOT_READY')
        g.press('key_left')
        g.step(300.0)
        g.press('key_start_stop')                                                 # no calibration
        self.assertEqual(g.lines(), ['       > ERROR <', 'NOT CALIBRATED'])
        after = g.h.read('evaluator')
        for k in ('calibration.points', 'acquisition.phase', 'sample.loaded', 'device.settings'):
            self.assertEqual(before[k], after[k], k)
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='no_such_key')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_power_off_and_boot_time_out(self):
        g = Rig(on=False)
        self.assertEqual(g.lines(), ['', ''])
        self.assertFalse(g.press('key_start_stop'))
        g.switch('on')
        self.assertEqual(g.lines(), ['KNAUER            V01.18', 'Freezing Point Osmometer'])
        g.step(1.0)
        self.assertEqual(g.screen(), 'boot')
        g.step(1.5)
        self.assertEqual(g.screen(), 's1')
        g.switch('off')
        self.assertEqual(g.screen(), 'off')
        self.assertFalse(g.val('power.enabled'))

    def test_cooler_timeout_shows_the_error(self):
        g = Rig(plant=dict(rt.PLANT_EXAMPLE, cooling_rate_k_s=0.0))
        g.go('s6')
        g.press('key_right')
        g.load(rt.reference(0.0))
        g.press('key_start_stop')
        g.step(299.0)
        self.assertEqual(g.screen(), 's6')
        g.step(2.0)
        self.assertEqual(g.lines(), ['       > ERROR <', 'COOLER TIMEOUT'])

    def test_panel_equals_direct(self):
        def script(direct):
            g = Rig()
            if direct:
                g.call(g.A, 'device.configure', data_output='PC', result_unit='mOsmol', temp_unit='degF')
                g.call(g.A, 'calibration.select_field', field=2, value=0.4)
                g.call(g.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, freeze_k=266.65))
            else:
                g.go('s2')
                g.press('key_right')
                g.press('key_up')
                g.press('key_right')
                g.go('s3')
                g.press('key_right')
                g.press('key_up')
                g.press('key_right')
                g.go('s6')
                g.press('key_right', 2)
                g.press('key_up', 9)
                g.press('key_right')
                g.go('s7')
                g.press('key_right')
                g.press('key_down')
                g.press('key_right')
            g.step(3.0)
            return g.h.read('evaluator')
        a, b = script(False), script(True)
        for k in a:
            self.assertEqual(json.dumps(a[k], sort_keys=True), json.dumps(b[k], sort_keys=True), k)


class CallerRules(unittest.TestCase):
    """Keys pressed through a session act as that session's caller (1.1.1): a run started with Start/Stop belongs to the caller, and another agent can
    stop it neither through the keys nor directly; the power switch is a hard switch and never refused (1.1.2)."""
    def start_calibration_run(self, g):
        g.go('s6')
        g.press('key_right')
        g.load(rt.reference(0.0))
        self.assertTrue(g.press('key_start_stop'))
        return g.h.operation_id

    def test_panel_started_run_belongs_to_the_caller(self):
        g = Rig()
        op = self.start_calibration_run(g)
        self.assertEqual((g.h.phase, g.h._owners.get(op)), ('running', 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        g.h.register_principal('agent2', 'agent')
        b = g.h.session('agent2')
        with self.assertRaises(rt.ActionRejected):
            b.poll(op)
        before = g.h.read('evaluator')
        r = b.invoke(instance_id=INST, capability_id='panel.press', arguments=dict(input_id='key_start_stop', duration_s=0.1), request_id='b1')
        self.assertFalse(r['result']['accepted'])
        self.assertEqual(g.lines(), ['       > ERROR <', 'ACCESS DENIED'])
        for cap, args in (('acquisition.stop', {}),):
            r = b.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        after = g.h.read('evaluator')
        for k in ('acquisition.phase', 'power.enabled', 'sample.loaded', 'calibration.points'):
            self.assertEqual(before[k], after[k], k)
        self.assertEqual(g.h.operation_id, op)
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'succeeded')
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')

    def test_power_switch_is_physical(self):
        """Sweep 2026-10-19: the power switch cuts the power on the real osmometer; another agent's switch-off is not refused, the run ends."""
        for direct in (False, True):
            g = Rig()
            op = self.start_calibration_run(g)
            g.h.register_principal('agent2', 'agent')
            b = g.h.session('agent2')
            if direct:
                r = b.invoke(instance_id=INST, capability_id='power.set', arguments=dict(enabled=False), request_id='b-off')
                self.assertEqual(r['status'], 'succeeded')
            else:
                r = b.invoke(instance_id=INST, capability_id='panel.switch', arguments=dict(input_id='power_switch', position='off'), request_id='b-sw')
                self.assertTrue(r['result']['accepted'])
            self.assertEqual((g.A.poll(op)['status'], g.val('power.enabled')), ('cancelled', False))

    def test_caller_stops_its_own_run_with_the_key(self):
        g = Rig()
        op = self.start_calibration_run(g)
        g.step(5.0)
        self.assertTrue(g.press('key_start_stop'))
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')
        g = Rig()
        op = self.start_calibration_run(g)
        g.switch('off')
        self.assertEqual((g.A.poll(op)['status'], g.val('power.enabled')), ('cancelled', False))

    def test_evaluator_may_stop_any_run(self):
        g = Rig()
        op = self.start_calibration_run(g)
        g.call(g.E, 'acquisition.stop')
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.two_point()
            g.go('s1')
            g.load(rt.SAMPLE_EXAMPLE)
            g.press('key_start_stop')
            g.step(130.0)
            return g
        a, b = scenario(), scenario()
        ia, ib = a.h.panel.render('lcd'), b.h.panel.render('lcd')
        self.assertEqual(ia.dtype.name, 'uint8')
        self.assertTrue((ia == ib).all())
        self.assertEqual(a.h.panel.png_bytes('lcd'), b.h.panel.png_bytes('lcd'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('lcd', OUT / 'home.png')}
        a.go('s6')
        shots['menu'] = a.h.panel.save_png('lcd', OUT / 'menu.png')
        a.go('s7')
        shots['cool'] = a.h.panel.save_png('lcd', OUT / 'cool.png')
        a.go('s1')
        a.unload()
        a.press('key_start_stop')
        shots['error'] = a.h.panel.save_png('lcd', OUT / 'error.png')
        self.assertEqual(a.lines()[0], '       > ERROR <')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_and_copy(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.switch('on')
        self.assertNotEqual(g.call(g.A, 'panel.capture', display_id='lcd')['image']['sha256'], r['image']['sha256'])
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        self.assertIs(g.h.panel.host, g.h)

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.turn', 'panel.switch', 'panel.touch', 'panel.capture', 'panel.frame'} & set(rt.PANEL_CAPS))
        c = json.loads((rt.ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        m = rt.export_knauer_k7400s_contract_metadata()
        for k in list(rt.PANEL_CAPS) + sorted(rt.PANEL_DEVICE):
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/knauer_k7400s.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('k7400s_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
