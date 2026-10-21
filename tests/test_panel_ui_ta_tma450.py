"""front-panel tests for CHEM-134 ta_tma450 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/ta_tma450.py):
python3 -m pytest tests/test_panel_ui_ta_tma450.py -q

Every gesture goes through the session API (panel.switch / panel.touch); widgets are found by id in the structured frame (panel.frame, evaluator)
and tapped at their centre.  Hands-on and TRIOS steps (installing the probe, the specimen, purge gas, preload force, the method) are direct
capability calls.  Themes: the four key procedures of the menu survey; signals and chart, active method, temperature settings, air cool, probe
keys; panel = direct capability (same runtime state); refusals shown as the device's dialog with the state unchanged; power gate; no
time-outs; force calibration with weights; touch calibration and the unsupported items (other probes, network); render determinism, capture files and the
evidence PNGs; contract entries."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import ta_tma450 as rt  # noqa: E402

SCREEN = 'touchscreen'
OUT = ROOT / 'outputs/panel_ui/ta_tma450'
CONTRACT = rt.ASSET / 'asset.contract.json'
SURVEY_TASKS = ['measure Tg/CTE (predecessor Q400 flow)', 'zero the probe before calibration run', 'measure the standard/sample length',
                'probe calibration']
DONE_TASKS = set()


class Rig:
    """One TMA 450 with an agent and an evaluator session; panel gestures only through panel.* capabilities."""

    def __init__(self, power=True, plant=None):
        self.h = rt.TaTma450Runtime('tma_panel')
        self.A, self.E = self.h.session('agent'), self.h.session('evaluator')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, **(plant or {})))
        self.call(self.E, 'gas.load', **rt.GAS_EXAMPLE)
        if power:
            assert self.switch('I')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=self.h.instance_id, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap                                          # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=self.h.instance_id, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        while dt > 1e-9:
            d = min(600.0, dt)
            self.n += 1
            self.E.invoke(instance_id=self.h.instance_id, capability_id='sim.step', arguments={'dt_s': d}, request_id=f'q{self.n}')
            dt -= d

    def wait(self, pred, limit_s=600.0, dt=0.5):
        t = 0.0
        while not pred():
            assert t < limit_s, 'condition not reached'
            self.step(dt)
            t += dt

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCREEN]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def switch(self, position):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=position)['accepted']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCREEN, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        for b in self.overlay()['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(label)

    def cells(self, wid):
        return {c[0]: c[1] for c in self.widgets()[wid]['cells']}

    def row(self, wid, label):
        w = self.widgets()[wid]
        k = [c[0] for c in w['cells']].index(label)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def enter(self, wid, label, text):
        """Tap a number row, type on the numeric keyboard (the shown value is replaced) and OK."""
        assert self.row(wid, label)
        for ch in text:
            self.ov(ch)
        return self.ov('OK')

    def dialog(self):
        ov = self.overlay()
        return (ov['dialog'], ov['lines']) if ov and ov['kind'] == 'dialog' else None

    def home(self):
        assert self.tap('ft_home')

    def state(self):
        return json.dumps({k: v for k, v in self.h.__dict__.items() if k not in ('revision', '_requests')}, sort_keys=True, default=str)


def furnace(r, opened):
    """The furnace key toggles the furnace; wait for the motion."""
    if bool(r.val('furnace.target_m') > 0) != opened:
        assert r.tap('ft_furnace')
    r.wait(lambda: not r.val('furnace.moving'))


def mount(r):
    """Specimen on the stage (hands), purge gas and preload force (TRIOS), Measure on the screen: the probe lands on the specimen."""
    furnace(r, True)
    r.call(r.E, 'sample.load', **rt.SAMPLE_EXAMPLE)
    r.call(r.A, 'gas.set_flow', flow_m3_s=100e-6 / 60)
    r.call(r.A, 'gas.set_open', opened=True)
    r.call(r.A, 'load.set', motor_force_n=0.05, rate_n_s=0.1)
    r.home()
    assert r.tap('v_utilities')
    assert r.tap('u_measure')
    r.wait(lambda: r.val('probe.engaged'), 120)
    r.step(0.5)


class TestKeyProcedures(unittest.TestCase):
    def test_measure_tg_cte(self):
        r = Rig()
        mount(r)                                                                  # open furnace, probe up, load, Measure
        self.assertEqual(r.text('u_length'), f"Size: {rt.SAMPLE_EXAMPLE['length_m'] * 1000:.3f} mm")
        self.assertTrue(r.tap('u_zero'))                                          # Zero Length
        self.assertTrue(r.val('displacement.zeroed'))
        furnace(r, False)                                                          # close the furnace
        r.call(r.A, 'method.configure', **rt.METHOD_EXAMPLE)                      # the procedure from TRIOS
        self.assertTrue(r.tap('ft_start'))
        self.assertEqual(r.text('hd_status'), 'Status: Running')
        r.step(5)
        r.home()
        assert r.tap('v_signals')
        self.assertEqual(r.cells('f_signals')['Probe'], 'On sample')
        self.assertTrue(r.cells('f_signals')['Dimension Change'].endswith(' um'))
        assert r.tap('t_chart')
        self.assertGreater(len(r.widgets()['chart']['series'][0]['points']), 5)
        r.wait(lambda: r.val('acquisition.phase') != 'running', 600)
        self.assertEqual(r.text('hd_status'), 'Status: Complete')
        self.assertEqual(r.call(r.A, 'acquisition.result')['phase'], 'succeeded')   # the run belongs to the operator who pressed Start
        r.call(r.A, 'thermal.disable')
        r.call(r.A, 'thermal.set_cooling', enabled=True)
        r.wait(lambda: r.h._cold(), 3600, 5.0)
        furnace(r, True)                                                           # open the furnace and lift the probe to remove the sample
        self.assertTrue(r.tap('ft_probe_up'))
        r.wait(lambda: r.text('ft_probe_up') and not r.val('probe.moving'), 120)
        self.assertFalse(r.val('probe.engaged'))
        r.call(r.E, 'sample.remove')
        DONE_TASKS.add('measure Tg/CTE (predecessor Q400 flow)')

    def test_zero_the_probe(self):
        r = Rig()
        mount(r)
        assert r.tap('ft_home')
        assert r.tap('v_signals')
        self.assertEqual(r.cells('f_signals')['Dimension Change'], '---')          # no zero yet
        r.home()
        assert r.tap('v_utilities')
        self.assertTrue(r.tap('u_zero'))                                           # ZERO LENGTH
        r.home()
        assert r.tap('v_signals')
        self.assertEqual(r.cells('f_signals')['Dimension Change'], '0.000 um')
        DONE_TASKS.add('zero the probe before calibration run')

    def test_measure_the_sample_length(self):
        r = Rig()
        assert r.tap('v_utilities')
        self.assertEqual(r.text('u_length'), 'Size: ---')
        mount(r)                                                                   # MEASURE: the size field updates
        self.assertAlmostEqual(float(r.text('u_length').split()[1]), rt.SAMPLE_EXAMPLE['length_m'] * 1000, places=3)
        r.home()
        assert r.tap('v_signals')
        self.assertEqual(r.cells('f_signals')['Sample Length'], f"{rt.SAMPLE_EXAMPLE['length_m'] * 1000:.3f} mm")
        DONE_TASKS.add('measure the standard/sample length')

    def test_probe_calibration(self):
        r = Rig()
        r.call(r.A, 'probe.select', kind='penetration')                             # install the probe (by hand)
        assert r.tap('v_calibration')
        self.assertEqual(r.text('c_status'), 'Probe not\ncalibrated')
        self.assertTrue(r.tap('c_penetration'))                                    # select the installed probe
        self.assertTrue(r.widgets()['c_penetration']['selected'])
        self.assertTrue(r.tap('c_start'))                                          # calibrate (tray and stage empty)
        self.assertEqual(r.dialog(), ('probe_done', ['Calibration complete.']))
        r.ov('OK')
        self.assertEqual(r.val('probe.calibrated'), 'penetration')
        self.assertEqual(r.text('c_status'), 'Calibrated:\nPenetration')
        self.assertTrue(r.tap('c_expansion'))
        self.assertFalse(r.tap('c_start'))                                         # not the installed probe
        self.assertEqual(r.dialog(), ('err_probe_mismatch', ['Select the installed probe.']))
        DONE_TASKS.add('probe calibration')


class TestFunctions(unittest.TestCase):
    def test_probe_calibration_removes_a_force_zero_error(self):
        r = Rig(plant=dict(force_offset_n=0.004))                                  # evaluator: a load-cell zero error (default 0)
        r.step(0.5)
        self.assertAlmostEqual(r.val('force.indicated_n'), 0.004, places=9)
        assert r.tap('v_calibration')
        assert r.tap('c_start')
        r.ov('OK')
        self.assertAlmostEqual(r.val('force.indicated_n'), 0.0, places=12)

    def test_force_calibration_with_weights_removes_gain_and_zero_errors(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', force_gain_error=0.1, force_offset_n=0.02)   # evaluator: load-cell errors (default 0)

        def weight(g):                                                                 # hands: standard weight on the weight tray
            r.call(r.A, 'load.deadweight', force_n=g / 1000 * rt.G_N)
            r.step(2.0)
        weight(100)
        self.assertAlmostEqual(r.val('force.indicated_n'), 1.1 * 0.980665 + 0.02, places=6)   # uncalibrated reading
        weight(0)
        assert r.tap('v_calibration')
        assert r.tap('c_force')
        self.assertEqual(r.page()[1], 'force_cal')
        self.assertEqual(r.text('fc_step'), 'Step 1: Calibrate Zero Weight')
        self.assertTrue(r.tap('fc_calibrate'))                                       # Step 1: tray empty
        self.assertEqual(r.text('fc_step'), 'Step 2: Calibrate First Weight')
        self.assertEqual(r.cells('f_weight')['Weight'], '50.0 g')
        weight(50)
        self.assertTrue(r.tap('fc_calibrate'))                                       # Step 2: 50 g
        self.assertEqual(r.cells('f_weight')['Weight'], '100.0 g')
        weight(100)
        self.assertTrue(r.enter('f_weight', 'Weight', '40'))
        self.assertFalse(r.tap('fc_calibrate'))                                      # the second weight must be heavier
        self.assertEqual(r.dialog(), ('err_force_cal_weight', ['Weight must be 10-100 g, the second heavier.']))
        r.ov('OK')
        self.assertTrue(r.enter('f_weight', 'Weight', '100'))
        self.assertTrue(r.tap('fc_calibrate'))                                       # Step 3: 100 g
        self.assertEqual(r.text('fc_step'), 'Step 4: Results')
        self.assertEqual((r.text('fc_result'), r.text('fc_slope'), r.text('fc_offset')), ('Passed', 'Slope 1.1000 (1 +/- 0.25)', 'Offset 0.0200 N (+/- 0.25)'))
        self.assertEqual(len(r.widgets()['fc_points']['cells']), 3)
        self.assertTrue(r.tap('fc_save'))
        self.assertEqual(r.dialog()[0], 'force_saved')
        r.ov('OK')
        r.step(0.1)
        self.assertAlmostEqual(r.val('force.indicated_n'), 0.980665, places=6)        # calibrated reading
        weight(0)
        self.assertTrue(r.tap('fc_abort'))
        self.assertEqual(r.text('fc_step'), 'Step 1: Calibrate Zero Weight')
        self.assertTrue(r.tap('fc_calibrate'))
        weight(50)
        self.assertTrue(r.tap('fc_calibrate'))
        weight(100)
        self.assertTrue(r.enter('f_weight', 'Weight', '60'))                          # wrong weight entered: fails
        self.assertTrue(r.tap('fc_calibrate'))
        self.assertEqual(r.text('fc_result'), 'Failed')
        self.assertEqual(r.text('fc_help'), 'Failed: Exit and repeat the calibration.')
        self.assertFalse(r.tap('fc_save'))
        self.assertEqual(r.dialog()[0], 'err_force_cal_failed')
        r.ov('OK')
        self.assertTrue(r.tap('fc_exit'))
        self.assertEqual((r.page()[1], r.val('force_calibration.state')['step']), ('calibration', 'zero'))
        self.assertAlmostEqual(r.h.calibration['force_gain_correction'], 1 / 1.1, places=9)   # the saved constants stay

    def test_temperature_settings_air_cool_and_method_page(self):
        r = Rig()
        mount(r)
        furnace(r, False)
        assert r.tap('ft_home')
        assert r.tap('v_utilities')
        self.assertTrue(r.enter('f_temperature', 'Rate', '20'))
        self.assertTrue(r.enter('f_temperature', 'Setpoint', '60'))
        self.assertAlmostEqual(r.val('thermal.final_target_k'), 333.15)
        r.step(30)
        self.assertGreater(r.val('sample.indicated_k'), 305.0)
        self.assertTrue(r.tap('u_temp_off'))
        self.assertFalse(r.val('thermal.enabled'))
        self.assertTrue(r.tap('u_aircool'))
        self.assertTrue(r.val('cooling.enabled'))
        r.home()
        assert r.tap('v_method')
        self.assertEqual(r.widgets()['m_segments']['cells'][0], ['1', 'Ramp 150.00 °C/min to 50.00 °C', 'Force 0.050 N', 'Iso 0.08 min'])
        r.home()
        assert r.tap('v_sysinfo')
        self.assertEqual((r.cells('f_sysinfo')['Instrument'], r.cells('f_sysinfo')['Probe']), ('TMA 450', 'Expansion'))

    def test_unsupported_items_show_the_device_response(self):
        r = Rig()
        assert r.tap('v_calibration')
        self.assertFalse(r.tap('c_flexural'))
        self.assertEqual(r.dialog()[0], 'probe_absent')
        self.assertTrue(r.frame()['ui']['unsupported'])
        r.ov('OK')
        self.assertEqual([u['item'] for u in rt.PANEL_SPEC['unsupported']],
                         ['Macro Expansion / Hemispherical / Flexural / Film/Fiber probes', 'Network Settings'])
        assert r.tap('ft_home')
        assert r.tap('v_settings')
        assert r.tap('s_touch')
        for i in range(3):
            self.assertTrue(r.tap(f'tc_target{i}'))
        r.ov('OK')
        self.assertFalse(r.tap('s_network'))
        self.assertEqual(r.dialog(), ('no_network', ['No network connection.']))


class TestRulesAndRefusals(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(), Rig()
        for x in (a, b):
            x.call(x.E, 'sample.load', **rt.SAMPLE_EXAMPLE)
            x.call(x.A, 'load.set', motor_force_n=0.05, rate_n_s=0.1)
        assert a.tap('ft_probe_down')
        b.call(b.A, 'probe.contact', engaged=True)
        for x in (a, b):
            x.step(20)
        assert a.tap('v_utilities')
        assert a.tap('u_zero')
        b.call(b.A, 'displacement.zero')
        assert a.tap('ft_furnace')
        b.call(b.A, 'furnace.set', opened=False)
        for x in (a, b):
            x.step(10)
        self.assertEqual(a.h.read(), b.h.read())
        self.assertEqual(a.call(a.E, 'ledger.read'), b.call(b.E, 'ledger.read'))

    def test_refusals_show_the_device_dialog_and_change_nothing(self):
        r = Rig()
        before = r.state()
        self.assertFalse(r.tap('ft_probe_down'))                                  # no specimen
        self.assertEqual(r.dialog(), ('err_no_sample', ['No sample on the stage.']))
        self.assertEqual(r.state(), before)
        r.ov('OK')
        assert r.tap('v_utilities')
        self.assertFalse(r.tap('u_zero'))
        self.assertEqual(r.dialog()[0], 'err_zero_requires_contact')
        r.ov('OK')
        mount(r)
        assert r.tap('u_zero')
        furnace(r, False)
        r.call(r.A, 'method.configure', **rt.METHOD_EXAMPLE)
        assert r.tap('ft_start')
        before = r.state()
        self.assertFalse(r.tap('ft_furnace'))                                     # not during an experiment
        self.assertEqual(r.dialog()[0], 'err_busy')
        r.ov('OK')
        self.assertEqual(r.state(), before)
        self.assertTrue(r.tap('ft_stop'))
        self.assertEqual(r.text('hd_status'), 'Status: Stopped')
        self.assertFalse(r.tap('ft_stop'))                                        # nothing running: ignored, no dialog
        self.assertIsNone(r.dialog())

    def test_another_operators_run_is_refused_on_the_panel(self):
        """A run belongs to the operator who pressed Start: a second agent's Stop on the touch screen is refused like its direct call (state
        unchanged, the device's dialog); the starter polls and cancels its own panel-started run."""
        r = Rig()
        r.h.register_principal('agent_b')
        b = r.h.session('agent_b')
        mount(r)
        assert r.tap('u_zero')
        furnace(r, False)
        r.call(r.A, 'method.configure', **rt.METHOD_EXAMPLE)
        assert r.tap('ft_start')
        op = r.h.operation_id
        r.step(1)
        self.assertEqual(r.A.poll(op)['status'], 'running')
        before = r.state()
        self.assertEqual(r.call(b, 'acquisition.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        x, y, w, h = r.widgets()['ft_stop']['rect']
        self.assertFalse(r.call(b, 'panel.touch', display_id=SCREEN, x=x + w // 2, y=y + h // 2)['accepted'])
        self.assertEqual(r.dialog(), ('err_access_denied', ['The running experiment belongs to another operator.']))
        r.ov('OK')
        self.assertEqual(r.state(), before)
        self.assertEqual(r.A.cancel(op, 'cancel')['status'], 'succeeded')
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')

    def test_power_switch_is_a_hard_switch(self):
        """Final sync 2026-10-19: the power switch cuts the power like a plug; another agent's switch-off is not refused, the running experiment
        ends as on a power loss (failed, POWER_LOSS) and the screen goes dark."""
        r = Rig()
        r.h.register_principal('agent_b')
        b = r.h.session('agent_b')
        mount(r)
        assert r.tap('u_zero')
        furnace(r, False)
        r.call(r.A, 'method.configure', **rt.METHOD_EXAMPLE)
        assert r.tap('ft_start')
        op = r.h.operation_id
        r.step(1)
        self.assertEqual(r.A.poll(op)['status'], 'running')
        self.assertTrue(r.call(b, 'panel.switch', input_id='power_switch', position='0')['accepted'])
        self.assertFalse(r.h.power)
        self.assertEqual(r.A.poll(op)['status'], 'failed')
        self.assertEqual(r.A.poll(op)['result']['error_code'], 'POWER_LOSS')
        self.assertEqual(r.page(), ('dark', None))

    def test_sample_length_is_the_lvdt_reading(self):
        """MEASURE size and Sample Length are the LVDT's reading: an evaluator displacement gain/offset error shows on the screen, the stored
        displacement gain calibration corrects the gain (default: the specimen length, unchanged)."""
        r = Rig(plant=dict(displacement_gain=1.05, displacement_offset_m=2e-4))
        mount(r)
        length = rt.SAMPLE_EXAMPLE['length_m']
        self.assertEqual(r.text('u_length'), f'Size: {(1.05 * length + 2e-4) * 1000:.3f} mm')
        r.call(r.A, 'sensor.calibrate', **dict(rt.CALIBRATION_EXAMPLE, displacement_gain_correction=1 / 1.05))
        self.assertEqual(r.text('u_length'), f'Size: {(length + 2e-4 / 1.05) * 1000:.3f} mm')

    def test_power_gate_and_no_timeouts(self):
        r = Rig(power=False)
        self.assertEqual(r.page(), ('dark', None))
        before = r.state()
        self.assertFalse(r.touch(400, 240))
        self.assertEqual(r.state(), before)
        self.assertEqual(int(r.h.panel.render(SCREEN).max()), 0)
        self.assertTrue(r.switch('I'))
        assert r.tap('v_calibration')
        r.step(1800)
        self.assertEqual(r.page(), ('gui', 'calibration'))                       # no screen time-out
        self.assertTrue(r.switch('0'))
        self.assertTrue(r.switch('I'))
        self.assertEqual(r.page(), ('gui', 'home'))

    def test_render_determinism_and_capture(self):
        a, b = Rig(), Rig()
        for x in (a, b):
            assert x.tap('v_calibration')
        self.assertEqual(a.h.panel.png_bytes(SCREEN), b.h.panel.png_bytes(SCREEN))
        cap = a.call(a.A, 'panel.capture', display_id=SCREEN)
        self.assertEqual(hashlib.sha256(Path(cap['image']['path']).read_bytes()).hexdigest(), cap['image']['sha256'])
        self.assertEqual(a.call(a.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        d = a.A.describe(instance_id=a.h.instance_id)
        self.assertIn('panel.touch', d['capabilities'])
        self.assertEqual(d['asset_version'], rt.ASSET_VERSION)

    def test_evidence_renders(self):
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        mount(r)
        assert r.tap('u_zero')
        furnace(r, False)
        r.call(r.A, 'method.configure', **rt.METHOD_EXAMPLE)
        assert r.tap('ft_start')
        r.step(8)
        r.home()
        shots = {'home': r.h.panel.save_png(SCREEN, OUT / 'home.png')}
        assert r.tap('v_signals')
        shots['signals'] = r.h.panel.save_png(SCREEN, OUT / 'signals.png')
        assert r.tap('t_chart')
        shots['chart'] = r.h.panel.save_png(SCREEN, OUT / 'chart.png')
        r.home()
        assert not r.tap('ft_furnace')
        shots['error'] = r.h.panel.save_png(SCREEN, OUT / 'error.png')
        r.ov('OK')
        assert r.tap('v_calibration')
        shots['menu'] = r.h.panel.save_png(SCREEN, OUT / 'menu.png')
        r.home()
        assert r.tap('v_utilities')
        shots['utilities'] = r.h.panel.save_png(SCREEN, OUT / 'utilities.png')
        r.home()
        assert r.tap('v_calibration')
        assert r.tap('c_force')
        shots['force_cal'] = r.h.panel.save_png(SCREEN, OUT / 'force_cal.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_ta_tma450_contract_metadata()
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for name, cap in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        for k, v in list(rt.PANEL_SPEC['inputs'].items()) + list(rt.PANEL_SPEC['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        self.assertIn('panel.touch', c['components']['touchscreen']['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['approximate']), ('panel_ui', 'touch_gui', True))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        from asset_tool import validate_contract
        self.assertEqual(validate_contract(c, ROOT, check_files=True), [])


if __name__ == '__main__':
    unittest.main()
