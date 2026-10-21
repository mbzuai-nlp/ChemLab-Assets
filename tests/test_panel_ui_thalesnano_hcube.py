"""front-panel tests for CHEM-020 thalesnano_hcube (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/thalesnano_hcube.py):
python3 -m pytest tests/test_panel_ui_thalesnano_hcube.py -q

Every gesture goes through the session API (panel.touch / panel.switch); widgets are found by id in the structured frame (panel.frame,
evaluator) and tapped at their centre.  Hands-on steps (cartridge, water, the external pump and recipe) are direct capability calls.  The menu
survey lists no procedures (no manual is saved): the tasks below are the brochure functions.  Themes: parameters, timer run and stop, valve
switches, data log and export, method storage, connected modules; panel = direct capability (same runtime state); refusals shown as the
device's dialog with the state unchanged; power gate; no time-outs; render determinism, capture files and the evidence PNGs; contract entries."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import thalesnano_hcube as rt  # noqa: E402

SCREEN = 'screen'
OUT = ROOT / 'outputs/panel_ui/thalesnano_hcube'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['set reaction parameters', 'run with the timer and stop', 'switch the inlet and outlet valves', 'view and export the data log',
                'store and recall a method']
DONE_TASKS = set()


class Rig:
    """One H-Cube Pro with an agent and an evaluator session; panel gestures only through panel.* capabilities."""

    def __init__(self, power=True, ready=True):
        self.h = rt.ThalesnanoHcubeRuntime('hcube_panel')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        if ready:                                                                # cartridge in, recipe and pump checked, water filled (by hand)
            self.call(self.A, 'clamp.set_lift', lift_m=0.05)
            self.call(self.E, 'plant.configure', **rt.EXAMPLE)
            self.call(self.E, 'kinetics.configure', **rt.KINETIC_EXAMPLE)
            self.call(self.A, 'clamp.set_lift', lift_m=0.0)
            self.call(self.A, 'water_lid.set_lift', lift_m=0.03)
            self.call(self.E, 'water.fill', volume_m3=100e-6, conductivity_s_m=1e-6)
            self.call(self.A, 'water_lid.set_lift', lift_m=0.0)
        if power:
            assert self.switch('power_switch', 'I')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=self.h.instance_id, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=self.h.instance_id, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        self.h.step(dt)

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

    def switch(self, input_id, position):
        return self.call(self.A, 'panel.switch', input_id=input_id, position=position)['accepted']

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

    def row(self, wid, first):
        w = self.widgets()[wid]
        k = [c[0] for c in w['cells']].index(first)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def enter(self, wid, label, text):
        assert self.row(wid, label)
        for ch in text:
            self.ov(ch)
        return self.ov('OK')

    def dialog(self):
        ov = self.overlay()
        return (ov['dialog'], ov['lines']) if ov and ov['kind'] == 'dialog' else None

    def state(self):
        return json.dumps(self.h.read('evaluator'), sort_keys=True)


class TestFunctions(unittest.TestCase):
    def test_set_reaction_parameters(self):
        r = Rig()
        assert r.tap('ft_params')
        self.assertTrue(r.enter('f_params', 'Temperature', '60'))
        self.assertTrue(r.enter('f_params', 'Pressure', '50'))
        self.assertTrue(r.enter('f_params', 'H2 production', '30'))
        self.assertTrue(r.enter('f_params', 'Flow rate', '1'))
        self.assertTrue(r.enter('f_params', 'Timer', '0.2'))
        self.assertEqual((r.val('temperature.target_k'), r.val('pressure.target_abs_pa')), (333.15, 5e6))
        self.assertAlmostEqual(r.val('hydrogen.target_reference_m3_s'), 30 * rt.MLMIN)
        self.assertAlmostEqual(r.val('flow.target_m3_s'), rt.MLMIN)
        self.assertAlmostEqual(r.val('method.duration_s'), 12.0)
        self.assertFalse(r.enter('f_params', 'Temperature', '200'))             # outside 10-150 °C: keypad refuses
        self.assertEqual(r.overlay()['error'], 'Out of range')
        r.ov('Cancel')
        assert r.tap('ft_home')
        self.assertEqual(r.text('set_temp'), 'set 60 °C')
        DONE_TASKS.add('set reaction parameters')

    def test_run_with_the_timer_and_stop(self):
        r = Rig()
        r.switch('inlet_valve', 'sample')
        r.switch('outlet_valve', 'product')
        self.assertTrue(r.tap('ft_start'))
        self.assertEqual(r.text('hd_status'), 'Running')
        r.step(2)
        self.assertEqual(r.text('val_press'), '40.0 bar')
        self.assertEqual(r.text('val_timer'), '00:02')                          # 4 s timer, 2 s remaining
        r.step(3)
        self.assertEqual(r.text('hd_status'), 'Completed')                      # the timer ends the reaction
        self.assertEqual(r.call(r.A, 'hydrogenation.measure')['phase'], 'succeeded')
        self.assertTrue(r.tap('ft_start'))
        r.step(1)
        self.assertTrue(r.tap('ft_stop'))
        self.assertEqual(r.text('hd_status'), 'Stopped')
        DONE_TASKS.add('run with the timer and stop')

    def test_another_agent_cannot_stop_the_run(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.switch('inlet_valve', 'sample')
        self.assertTrue(r.tap('ft_start'))                                      # the run belongs to the operator who pressed Start
        r.step(1)
        op = r.h.operation_id
        before = r.state()
        x, y, w, h = r.widgets()['ft_stop']['rect']
        self.assertFalse(r.call(B, 'panel.touch', display_id=SCREEN, x=x + w // 2, y=y + h // 2)['accepted'])   # B's Stop: refused
        self.assertEqual(r.dialog(), ('err_access_denied', ['This run was started by another operator.']))      # shown on the screen
        r.ov('OK')
        self.assertEqual(r.call(B, 'acquisition.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.state(), before)
        self.assertEqual(r.text('hd_status'), 'Running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        self.assertEqual(r.A.poll(op)['status'], 'running')                     # A polls and cancels its own screen-started run
        self.assertEqual(r.A.cancel(op, 'c1')['status'], 'cancelled')
        self.assertTrue(r.tap('ft_start'))                                      # the rear power switch is a hard switch (final sync): B's
        op2 = r.h.operation_id                                                  # switch-off is not refused, the run ends as on a power loss
        r.step(1)
        self.assertTrue(r.call(B, 'panel.switch', input_id='power_switch', position='O')['accepted'])
        self.assertEqual(r.A.poll(op2)['status'], 'cancelled')
        self.assertFalse(r.val('power.enabled'))

    def test_switch_the_inlet_and_outlet_valves(self):
        r = Rig(power=False)
        self.assertTrue(r.switch('inlet_valve', 'sample'))                       # manual valves: also without power
        self.assertTrue(r.switch('outlet_valve', 'product'))
        self.assertEqual((r.val('inlet.route'), r.val('outlet.route')), ('sample', 'product'))
        r.switch('power_switch', 'I')
        self.assertEqual(r.text('routes'), 'Inlet: Reaction mixture   Outlet: Product')
        r.switch('inlet_valve', 'solvent')
        self.assertEqual(r.text('routes'), 'Inlet: Solvent   Outlet: Product')
        DONE_TASKS.add('switch the inlet and outlet valves')

    def test_view_and_export_the_data_log(self):
        r = Rig()
        r.switch('inlet_valve', 'sample')
        assert r.tap('ft_start')
        r.step(3.5)
        assert r.tap('ft_log')
        w = r.widgets()['log_list']
        self.assertEqual([c[0] for c in w['cells']], ['00:03', '00:02', '00:01'])  # newest first
        self.assertEqual(w['cells'][0][2], '40.0')
        self.assertFalse(r.tap('log_export'))
        self.assertEqual(r.dialog(), ('no_media', ['No storage device connected.']))
        self.assertTrue(r.frame()['ui']['unsupported'])
        DONE_TASKS.add('view and export the data log')

    def test_store_and_recall_a_method(self):
        r = Rig()
        assert r.tap('ft_params')
        assert r.enter('f_params', 'Temperature', '80')
        assert r.tap('ft_methods')
        assert r.row('m_list', '2')
        self.assertEqual(r.dialog()[0], 'method_slot')
        self.assertTrue(r.ov('Save'))
        self.assertTrue(r.cells('m_list')['2'].startswith('80 °C  40.0 bar'))
        assert r.tap('ft_params')
        assert r.enter('f_params', 'Temperature', '30')
        assert r.tap('ft_methods')
        assert r.row('m_list', '2')
        self.assertTrue(r.ov('Load'))
        self.assertEqual(r.val('temperature.target_k'), 353.15)
        assert r.row('m_list', '4')
        self.assertFalse(r.ov('Load'))                                            # an empty place
        self.assertEqual(r.dialog()[0], 'err_precondition_failed')
        DONE_TASKS.add('store and recall a method')

    def test_connected_modules_are_absent(self):
        r = Rig()
        assert r.tap('b_modules')
        for k in ('phoenix', 'gas', 'changer', 'aim'):
            self.assertFalse(r.tap(f'mod_{k}'))
            self.assertEqual(r.dialog()[1], ['Not connected.'])
            r.ov('OK')


class TestRulesAndRefusals(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(), Rig()
        assert a.tap('ft_params')
        assert a.enter('f_params', 'Flow rate', '1.5')
        b.call(b.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, liquid_flow_m3_s=1.5 * rt.MLMIN))
        a.switch('inlet_valve', 'sample')
        b.call(b.A, 'inlet.set', route='sample')
        assert a.tap('ft_start')
        b.call(b.A, 'acquisition.start')
        for x in (a, b):
            x.step(2)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))

    def test_refusals_show_the_device_dialog_and_change_nothing(self):
        r = Rig(ready=False)
        before = r.state()
        self.assertFalse(r.tap('ft_start'))                                       # no cartridge / recipe
        self.assertEqual(r.dialog(), ('err_precondition_failed', ['Not ready: cartridge, water, pump and settings.']))
        self.assertEqual(r.state(), before)
        r.ov('OK')
        r = Rig()
        assert r.tap('ft_start')
        assert r.tap('ft_params')
        before = r.state()
        self.assertFalse(r.enter('f_params', 'Temperature', '50'))               # not while a reaction runs
        self.assertEqual(r.dialog(), ('err_busy', ['Not available while a reaction is running.']))
        self.assertEqual(r.state(), before)

    def test_power_gate_and_no_timeouts(self):
        r = Rig(power=False)
        self.assertEqual(r.page(), ('dark', None))
        before = r.state()
        self.assertFalse(r.touch(400, 240))
        self.assertEqual(r.state(), before)
        self.assertEqual(int(r.h.panel.render(SCREEN).max()), 0)
        r.switch('power_switch', 'I')
        assert r.tap('ft_log')
        r.step(600)
        self.assertEqual(r.page(), ('gui', 'log'))
        r.switch('power_switch', 'O')
        r.switch('power_switch', 'I')
        self.assertEqual(r.page(), ('gui', 'home'))

    def test_render_determinism_and_capture(self):
        a, b = Rig(), Rig()
        for x in (a, b):
            assert x.tap('ft_params')
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
        r.switch('inlet_valve', 'sample')
        r.switch('outlet_valve', 'product')
        assert r.tap('ft_start')
        r.step(2)
        shots = {'home': r.h.panel.save_png(SCREEN, OUT / 'home.png')}
        assert r.tap('ft_params')
        shots['menu'] = r.h.panel.save_png(SCREEN, OUT / 'menu.png')
        assert not r.enter('f_params', 'Temperature', '50')
        shots['error'] = r.h.panel.save_png(SCREEN, OUT / 'error.png')
        r.ov('OK')
        assert r.tap('ft_log')
        shots['log'] = r.h.panel.save_png(SCREEN, OUT / 'log.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_thalesnano_hcube_contract_metadata()
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for name, cap in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
        for k, v in list(rt.PANEL_SPEC['inputs'].items()) + list(rt.PANEL_SPEC['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        self.assertIn('panel.touch', c['components']['display_screen']['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['approximate']), ('panel_ui', 'touch_gui', True))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        from asset_tool import validate_contract
        self.assertEqual(validate_contract(c, ROOT, check_files=True), [])


if __name__ == '__main__':
    unittest.main()
