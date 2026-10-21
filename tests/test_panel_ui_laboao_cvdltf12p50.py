"""Front-panel tests for CHEM-063 laboao_cvdltf12p50 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/laboao_cvdltf12p50.py):
python3 -m pytest tests/test_panel_ui_laboao_cvdltf12p50.py -q

The spec is approximate (only the product page is public; see panel_ui.json).  Every key, switch and knob goes through the session API (panel.press /
panel.switch / panel.turn); the displays are read from the structured frame (panel.frame, evaluator).  Themes: switching on, the PV / SV controller
with its parameter list (mode, curve, steps, segment target / ramp / hold, correction, run mode), run / stop by R/S and the green / red buttons, the
MFC selectors and flow displays, the vacuum controller and valves; panel = direct capability (same runtime state); refusals shown as Err with the
state unchanged; time-outs; render determinism and the evidence PNGs; contract entries."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import laboao_cvdltf12p50 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'cvdltf12p50_01'
OUT = ROOT / 'outputs/panel_ui/laboao_cvdltf12p50'
SURVEY_TASKS = []                                                          # the survey lists no key procedures (no manual)
DONE_TASKS = set()


class Rig:
    def __init__(self, on=True, warm=True):
        self.h = rt.LaboaoCvdltf12p50Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **(rt.WARM_EXAMPLE if warm else rt.PLANT_EXAMPLE))
        self.call(self.E, 'gas.install', **rt.GASES_EXAMPLE)
        self.call(self.E, 'sample.install', **(rt.HOT_SAMPLE_EXAMPLE if warm else rt.SAMPLE_EXAMPLE))
        self.call(self.A, 'method.configure', **rt.METHOD_EXAMPLE)
        if on:
            assert self.switch('mains', 'on')
            self.step(1.5)
            assert self.screen() == 'home'

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

    def press(self, key, n=1, hold=0.1):
        ok = True
        for _ in range(n):
            ok = self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted'] and ok
        return ok

    def switch(self, inp, pos):
        return self.call(self.A, 'panel.switch', input_id=inp, position=pos)['accepted']

    def turn(self, inp, n):
        return self.call(self.A, 'panel.turn', input_id=inp, detents=n)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def show(self, did):
        d = self.frame()['displays'][did]
        return d['fields']['main']['text'].strip() if 'fields' in d else d['on']

    def to_param(self, code):
        if self.screen() != 'par':
            self.press('key_set')
        for _ in range(12):
            if self.show('pv') == code:
                return
            self.press('key_set')
        raise AssertionError(code)


class ControllerAndRun(unittest.TestCase):
    def test_switch_on_and_pv_sv(self):
        g = Rig(on=False)
        self.assertEqual((g.show('pv'), g.show('sv'), g.show('led_stop')), ('', '', False))
        self.assertFalse(g.press('key_set'))                                     # no power
        g.switch('mains', 'on')
        self.assertEqual((g.screen(), g.show('pv')), ('boot', '8.8.8.8.'))
        g.step(1.5)
        self.assertEqual((g.show('pv'), g.show('sv')), ('900', '900'))
        self.assertTrue(g.show('led_stop'))

    def test_parameter_list_edits_the_curve(self):
        g = Rig()
        g.to_param('StEP')
        g.press('key_up', 2)
        self.assertEqual((len(g.h.method['segments']), g.show('sv')), (3, '3'))
        g.press('key_set')                                                        # t 1
        self.assertEqual(g.show('pv'), 't  1')
        g.press('key_down', 100)                                                  # 900 -> 800 C
        self.assertAlmostEqual(g.h.method['segments'][0]['target_k'], 1073.15)
        g.press('key_set')                                                        # r 1: 5.0 -> 3.0 C/min
        g.press('key_down', 2)
        self.assertAlmostEqual(g.h.method['segments'][0]['ramp_k_s'] * 60, 3.0)
        g.press('key_set')                                                        # h 1: + 0.5 h
        g.press('key_up', 5)
        self.assertAlmostEqual(g.h.method['segments'][0]['hold_s'], 5.0 + 1800.0)
        self.assertEqual(g.show('sv'), '0.5')
        for _ in range(7):
            g.press('key_set')                                                    # t 2 ... h 3, Pb
        self.assertEqual(g.show('pv'), 'Pb')
        g.press('key_up', 3)
        self.assertEqual(g.val('controller.correction_k'), 3.0)
        self.assertEqual(g.show('pv') if g.screen() == 'par' else None, 'Pb')
        g.press('key_set')
        self.assertEqual((g.show('pv'), g.show('sv')), ('rUn', 'Cvd'))
        g.press('key_up')
        self.assertEqual(g.show('sv'), 'PUrG')
        g.press('key_set')                                                        # end of the list: PV / SV
        self.assertEqual(g.screen(), 'home')
        self.assertEqual(g.show('pv'), str(round(g.h.oven - 273.15 + 3.0)))     # the corrected PV

    def test_fixed_mode_sv_keys(self):
        g = Rig()
        g.to_param('CtrL')
        g.press('key_up')
        self.assertEqual((g.h.method['mode'], g.show('sv')), ('fixed', 'FIX'))
        g.press('key_shift')
        g.press('key_up', 10)
        self.assertAlmostEqual(g.h.method['segments'][0]['target_k'], 1183.15)
        self.assertEqual(g.show('sv'), '910')
        g.to_param('StEP')
        self.assertFalse(g.press('key_up'))                                       # fixed mode: one segment
        self.assertEqual((g.screen(), g.show('pv'), g.show('sv')), ('err', 'Err', 'FIX'))
        g.step(2.5)
        self.assertEqual(g.screen(), 'home')

    def test_run_stop_and_buttons(self):
        g = Rig()
        g.call(g.A, 'vacuum.set', enabled=True, mode='manual', pressure_pa=30.0, manual_valve_fraction=1.0)
        self.assertTrue(g.press('key_rs', hold=2.5))                              # R/S held: run the program
        self.assertEqual(g.val('cycle.phase'), 'running')
        self.assertTrue(g.show('led_run'))
        g.step(12.0)
        self.assertGreater(float(g.show('flow_0') or 0), 0.0)                     # N2 purge flow in sccm
        g.press('key_red')
        self.assertEqual(g.val('cycle.phase'), 'cancelled')
        g.press('key_green')
        self.assertEqual(g.val('cycle.phase'), 'running')
        g.press('key_rs', hold=2.5)
        self.assertEqual(g.val('cycle.phase'), 'cancelled')

    def test_curve_selection(self):
        g = Rig()
        g.call(g.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, curve_slot=1, segments=[dict(target_k=1073.15, ramp_k_s=5 / 60, hold_s=10.0)]))
        g.to_param('CurV')
        self.assertEqual(g.show('sv'), '1')
        g.press('key_down')
        self.assertEqual((g.h.method['curve_slot'], g.show('sv')), (0, '0'))
        g.press('key_down')                                                       # curve 3 is not configured
        self.assertEqual((g.screen(), g.show('sv')), ('err', 'nCur'))

    def test_parameter_time_out(self):
        g = Rig()
        g.press('key_set')
        g.step(9.0)
        self.assertEqual(g.screen(), 'par')
        g.step(1.5)
        self.assertEqual(g.screen(), 'home')


class GasAndVacuum(unittest.TestCase):
    def test_mfc_selectors_and_flow_displays(self):
        g = Rig()
        g.call(g.A, 'vacuum.set', enabled=True, mode='manual', pressure_pa=30.0, manual_valve_fraction=1.0)
        self.assertTrue(g.switch('mfc_1', 'close'))
        self.assertTrue(g.switch('mfc_0', 'purge'))
        g.press('key_green')
        g.step(40.0)
        self.assertEqual(g.val('gas.channel_modes'), ['purge', 'close', 'control'])
        self.assertEqual(g.show('flow_1'), '0')
        self.assertEqual(g.show('flow_0'), '%d' % round(rt.GASES_EXAMPLE['full_scale_std_m3_s'][0] * 6e7))

    def test_vacuum_controller_keys_and_valves(self):
        g = Rig(warm=False)
        g.call(g.A, 'flange.set', opened=False)
        g.call(g.A, 'furnace.set', opened=False)
        self.assertTrue(g.press('vac_pump'))
        self.assertTrue(g.val('pump.enabled'))
        g.step(5.0)
        self.assertTrue(g.show('led_pump'))
        self.assertFalse(g.show('led_valve'))
        g.step(6.0)
        self.assertTrue(g.show('led_valve'))                                      # the solenoid valve opens 10 s after the pump
        g.press('vac_am')
        self.assertTrue(g.show('led_auto'))
        g.press('vac_up', 5)
        self.assertEqual(g.show('pressure'), 'P 35')
        self.assertEqual(g.h.pressure_set, 35.0)
        g.step(3.5)
        self.assertNotEqual(g.show('pressure'), 'P 35')
        self.assertTrue(g.turn('vacuum_valve', -4))
        self.assertAlmostEqual(g.h.manual_valve, 0.8)
        g.press('vac_pump')
        self.assertTrue(g.switch('vent_valve', 'open'))
        self.assertTrue(g.h.vent)
        g.step(30.0)
        self.assertEqual(g.show('pressure'), 'HI')


class RefusalsAndEquivalence(unittest.TestCase):
    def test_refusals_show_err_and_change_nothing(self):
        g = Rig()
        before = copy.deepcopy(g.h.method)
        g.to_param('t  1')
        g.press('key_up', 301)                                                    # 1200 C is the furnace limit
        self.assertEqual((g.screen(), g.show('sv')), ('err', 'rAnG'))
        self.assertAlmostEqual(g.h.method['segments'][0]['target_k'], 1473.15)
        g.call(g.A, 'method.configure', **rt.METHOD_EXAMPLE)
        self.assertEqual(g.h.method, before)
        g.press('key_set')
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='no_such_key')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        g.switch('mains', 'off')
        self.assertFalse(g.press('vac_pump'))
        self.assertTrue(g.switch('mfc_2', 'close'))                               # the selectors are mechanical

    def test_panel_equals_direct(self):
        def script(direct):
            g = Rig()
            if direct:
                g.call(g.A, 'controller.set_steps', steps=2)
                g.call(g.A, 'controller.set_segment', index=0, target_k=1163.15, ramp_k_s=5 / 60, hold_s=5.0)
                g.call(g.A, 'controller.set_correction', offset_k=-2.0)
                g.call(g.A, 'gas.set_channel', channel=2, mode='close')
                g.call(g.A, 'vacuum.set', enabled=True, mode='auto', pressure_pa=30.0, manual_valve_fraction=1.0)
            else:
                g.to_param('StEP')
                g.press('key_up')
                g.press('key_set')
                g.press('key_down', 10)
                g.to_param('Pb')
                g.press('key_down', 2)
                g.press('key_shift')
                g.switch('mfc_2', 'close')
                g.press('vac_pump')
                g.press('vac_am')
            g.step(3.0)
            return g.h.read('evaluator')
        a, b = script(False), script(True)
        for k in a:
            self.assertEqual(json.dumps(a[k], sort_keys=True), json.dumps(b[k], sort_keys=True), k)


class CallerRules(unittest.TestCase):
    """Keys pressed through a session act as that session's caller (1.1.1): a cycle started with R/S or the green button belongs to the caller, and
    another agent can end it neither with the keys (R/S, red button, pump) nor with the direct commands; the vent valve, the furnace lid and the
    mains rocker are hand actions and never refused (sweep 2026-10-19)."""
    def start(self, g):
        g.call(g.A, 'vacuum.set', enabled=True, mode='manual', pressure_pa=30.0, manual_valve_fraction=1.0)
        self.assertTrue(g.press('key_rs', hold=2.5))
        return g.h.operation_id

    def test_panel_started_run_belongs_to_the_caller(self):
        g = Rig()
        op = self.start(g)
        self.assertEqual((g.val('cycle.phase'), g.h._owners.get(op)), ('running', 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        g.h.register_principal('agent2', 'agent')
        b = g.h.session('agent2')
        with self.assertRaises(rt.ActionRejected):
            b.poll(op)
        before = g.h.read('evaluator')
        for cap, args in (('acquisition.stop', {}),
                          ('vacuum.set', dict(enabled=False, mode='manual', pressure_pa=30.0, manual_valve_fraction=1.0))):
            r = b.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        for n, (cap, args) in enumerate((('panel.press', dict(input_id='key_red', duration_s=0.1)), ('panel.press', dict(input_id='key_rs', duration_s=2.5)),
                                         ('panel.press', dict(input_id='vac_pump', duration_s=0.1)))):
            if n:
                g.step(2.1)                                                       # Err times out to the home screen after 2 s
            r = b.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'b{n}')
            self.assertFalse(r['result']['accepted'], args)
            self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'ACCESS_DENIED', args)
            self.assertEqual((g.screen(), g.show('pv'), g.show('sv')), ('err', 'Err', 'Acc'))
        after = g.h.read('evaluator')
        for k in ('cycle.phase', 'power.enabled', 'pump.enabled', 'furnace.opened'):
            self.assertEqual(before[k], after[k], k)
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'succeeded')
        self.assertEqual(g.val('cycle.phase'), 'cancelled')

    def test_hand_actions_are_not_refused(self):
        """Sweep 2026-10-19: the vent valve and the furnace lid are physical; another agent may use them during a run and the device reacts itself."""
        for gesture in ('lid', 'vent_key', 'vent_direct', 'mains', 'mains_direct'):
            g = Rig()
            op = self.start(g)
            g.h.register_principal('agent2', 'agent')
            b = g.h.session('agent2')
            if gesture == 'lid':
                r = b.invoke(instance_id=INST, capability_id='furnace.set', arguments=dict(opened=True), request_id='b-lid')
                self.assertEqual(r['status'], 'succeeded')
                self.assertTrue(g.val('furnace.opened'))
            elif gesture == 'vent_key':
                r = b.invoke(instance_id=INST, capability_id='panel.switch', arguments=dict(input_id='vent_valve', position='open'), request_id='b-vent')
                self.assertTrue(r['result']['accepted'])
                self.assertNotEqual(g.screen(), 'err')
            elif gesture == 'vent_direct':
                r = b.invoke(instance_id=INST, capability_id='vacuum.vent', arguments={}, request_id='b-vent2')
                self.assertEqual(r['status'], 'succeeded')
            elif gesture == 'mains':                                              # the rear mains rocker cuts the power: a power loss
                r = b.invoke(instance_id=INST, capability_id='panel.switch', arguments=dict(input_id='mains', position='off'), request_id='b-mains')
                self.assertTrue(r['result']['accepted'])
                self.assertFalse(g.val('power.enabled'))
            else:
                r = b.invoke(instance_id=INST, capability_id='power.set', arguments=dict(enabled=False), request_id='b-mains2')
                self.assertEqual(r['status'], 'succeeded')
            self.assertEqual(g.val('cycle.phase'), 'cancelled', gesture)            # the open lid / venting stop the run
            self.assertEqual(g.A.poll(op)['status'], 'cancelled', gesture)

    def test_caller_ends_its_own_run_with_the_keys(self):
        g = Rig()
        op = self.start(g)
        g.press('key_red')
        self.assertEqual(g.val('cycle.phase'), 'cancelled')
        self.assertTrue(g.press('key_green'))
        op2 = g.h.operation_id
        self.assertNotEqual(op2, op)
        self.assertEqual((g.h._owners.get(op2), g.A.poll(op2)['status']), ('agent', 'running'))
        g.switch('mains', 'off')
        self.assertEqual((g.val('cycle.phase'), g.val('power.enabled')), ('cancelled', False))

    def test_evaluator_may_stop_any_run(self):
        g = Rig()
        self.start(g)
        g.call(g.E, 'acquisition.stop')
        self.assertEqual(g.val('cycle.phase'), 'cancelled')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.call(g.A, 'vacuum.set', enabled=True, mode='manual', pressure_pa=30.0, manual_valve_fraction=1.0)
            g.press('key_green')
            g.step(30.0)
            return g
        a, b = scenario(), scenario()
        for did in rt.PANEL_SPEC['displays']:
            ia, ib = a.h.panel.render(did), b.h.panel.render(did)
            self.assertEqual(ia.dtype.name, 'uint8')
            self.assertTrue((ia == ib).all(), did)
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('pv', OUT / 'home.png'), 'home_sv': a.h.panel.save_png('sv', OUT / 'home_sv.png'),
                 'flow': a.h.panel.save_png('flow_0', OUT / 'flow.png'), 'pressure': a.h.panel.save_png('pressure', OUT / 'pressure.png')}
        a.press('key_red')
        a.press('key_set')
        a.press('key_set', 2)
        shots['menu'] = a.h.panel.save_png('pv', OUT / 'menu.png')
        shots['menu_sv'] = a.h.panel.save_png('sv', OUT / 'menu_sv.png')
        a.press('key_down', 2)
        a.press('key_up')
        a.to_param('CtrL')
        a.press('key_up')
        a.to_param('StEP')
        a.press('key_up')
        shots['error'] = a.h.panel.save_png('pv', OUT / 'error.png')
        shots['error_sv'] = a.h.panel.save_png('sv', OUT / 'error_sv.png')
        self.assertEqual(a.show('pv'), 'Err')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_and_copy(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='pv')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.switch('mains', 'on')
        self.assertNotEqual(g.call(g.A, 'panel.capture', display_id='pv')['image']['sha256'], r['image']['sha256'])
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        c = json.loads((rt.ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        m = rt.export_laboao_cvdltf12p50_contract_metadata()
        for k in list(rt.PANEL_CAPS) + sorted(rt.PANEL_DEVICE):
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version'], ext['approximate']), ('panel_ui', 'parameter_cycle', pu.SPEC_VERSION, True))
        for aid, path in (('panel_ui', rt.ASSET / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/laboao_cvdltf12p50.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('ltf_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


if __name__ == '__main__':
    unittest.main()
