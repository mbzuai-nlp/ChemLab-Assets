"""front-panel tests for CHEM-132 ta_tga5500 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/ta_tga5500.py):
python3 -m pytest tests/test_panel_ui_ta_tga5500.py -q

Every gesture goes through the session API (panel.switch / panel.touch); widgets are found by id in the structured frame (panel.frame, evaluator)
and tapped at their centre.  Hands-on and TRIOS steps (the pan with the sample, gas flows, the method) are direct capability calls.  Themes: the
four key procedures of the menu survey; signals and chart, active method and Next Segment, autosampler pan to front, air cool and gas switch,
system info; panel = direct capability (same runtime state); refusals shown as the device's dialog with the state unchanged; power gate; no
time-outs; touch calibration and the unsupported items (load / unload, network); render determinism, capture files and the evidence PNGs;
contract entries."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import ta_tga5500 as rt  # noqa: E402

SCREEN = 'touchscreen'
OUT = ROOT / 'outputs/panel_ui/ta_tga5500'
CONTRACT = rt.ASSET / 'asset.contract.json'
SURVEY_TASKS = ['tare pans', 'bring a pan position to the front', 'start or stop a run on the instrument', 'open or close the furnace (predecessor Q500)']
DONE_TASKS = set()
QUICK = dict(rt.METHOD_EXAMPLE, segments=[dict(target_k=300.15, rate_k_s=0.5, hold_s=1.0, gas='N2')])
TWO = dict(rt.METHOD_EXAMPLE, segments=[dict(target_k=300.15, rate_k_s=0.01, hold_s=50.0, gas='N2'), dict(target_k=301.15, rate_k_s=0.5, hold_s=1.0, gas='O2')])


class Rig:
    """One TGA 5500 with an agent and an evaluator session; panel gestures only through panel.* capabilities."""

    def __init__(self, power=True):
        self.h = rt.TaTga5500Runtime('tga_panel')
        self.A, self.E = self.h.session('agent'), self.h.session('evaluator')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        for g in ('N2', 'O2'):
            self.call(self.E, 'gas.load', **dict(rt.GAS_EXAMPLE, gas=g))
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


def ready(r, method=QUICK):
    """Tared empty pan, the sample (hands / autosampler outside the model), gas flows and the method from TRIOS; furnace closed on the screen."""
    furnace(r, True)
    r.call(r.E, 'sample.load', **rt.SAMPLE_EXAMPLE)
    r.call(r.A, 'gas.set_flow', sample_flow_m3_s=25e-6 / 60, balance_flow_m3_s=10e-6 / 60)
    r.call(r.A, 'gas.select', gas='N2')
    r.call(r.A, 'gas.set_open', opened=True)
    r.call(r.A, 'method.configure', **method)
    furnace(r, False)


class TestKeyProcedures(unittest.TestCase):
    def test_tare_pans(self):
        r = Rig()
        self.assertAlmostEqual(r.val('mass.indicated_kg'), rt.PLANT_EXAMPLE['pan_mass_kg'], places=12)   # the empty pan weighs
        self.assertTrue(r.tap('ft_tare'))                                         # tare the empty pan
        self.assertAlmostEqual(r.val('mass.indicated_kg'), 0.0, places=12)
        assert r.tap('v_signals')
        self.assertEqual(r.cells('f_signals')['Weight'], '0.000 mg')
        r.call(r.E, 'sample.load', **rt.SAMPLE_EXAMPLE)
        r.step(5)                                                                 # the balance settles
        self.assertEqual(r.cells('f_signals')['Weight'], '10.000 mg')
        r.home()
        self.assertFalse(r.tap('ft_tare'))                                        # not with a sample on the pan
        self.assertEqual(r.dialog(), ('err_tare_requires_empty_stable', ['Tare needs an empty, cold and stable pan.']))
        DONE_TASKS.add('tare pans')

    def test_bring_a_pan_position_to_the_front(self):
        r = Rig()
        assert r.tap('v_autosampler')
        self.assertTrue(r.widgets()['pos_1']['selected'])
        self.assertTrue(r.tap('pos_7'))                                           # Pan 7 to front
        self.assertEqual(r.val('carousel.target_index'), 7)
        self.assertEqual(r.text('as_state'), 'Rotating')
        r.wait(lambda: not r.val('carousel.moving'), 30)
        self.assertEqual(r.text('as_state'), 'In position')
        self.assertTrue(r.widgets()['pos_7']['selected'])
        self.assertEqual(r.text('as_front'), 'Front: 7')
        DONE_TASKS.add('bring a pan position to the front')

    def test_start_or_stop_a_run(self):
        r = Rig()
        ready(r, TWO)
        self.assertTrue(r.tap('ft_start'))
        self.assertEqual(r.text('hd_status'), 'Status: Test')
        r.step(10)
        assert r.tap('v_signals')
        c = r.cells('f_signals')
        self.assertEqual((c['Sample Gas Flow Rate'], c['Purge Flow Rate'], c['Segment']), ('25 mL/min', '10 mL/min', '1 Ramp'))
        self.assertTrue(c['Weight %'].endswith(' %'))
        assert r.tap('t_chart')
        self.assertGreater(len(r.widgets()['chart']['series'][0]['points']), 10)
        r.home()
        assert r.tap('v_method')
        self.assertEqual(r.widgets()['m_segments']['selected_rows'], [0])
        self.assertTrue(r.tap('m_next'))                                          # advance method segment
        self.assertEqual(r.val('acquisition.segment_index'), 1)
        self.assertEqual(r.val('gas.selected'), 'O2')
        r.step(2)
        self.assertTrue(r.tap('ft_stop'))                                         # stop the run
        self.assertEqual(r.val('acquisition.phase'), 'cancelled')
        self.assertEqual(r.text('hd_status'), 'Status: Stopped')
        self.assertEqual(r.call(r.A, 'acquisition.result')['phase'], 'cancelled')   # the run belongs to the operator who pressed Start
        DONE_TASKS.add('start or stop a run on the instrument')

    def test_open_or_close_the_furnace(self):
        r = Rig()
        self.assertTrue(r.val('furnace.opened'))
        self.assertTrue(r.tap('ft_furnace'))                                       # close (up)
        self.assertEqual(r.text('ft_furnace'), 'Furnace\nmoving')
        r.wait(lambda: not r.val('furnace.moving'))
        self.assertEqual(r.text('ft_furnace'), 'Furnace\nclosed')
        self.assertTrue(r.tap('ft_furnace'))                                       # open (down)
        r.wait(lambda: not r.val('furnace.moving'))
        self.assertTrue(r.val('furnace.opened'))
        DONE_TASKS.add('open or close the furnace (predecessor Q500)')


class TestFunctions(unittest.TestCase):
    def test_utilities_air_cool_and_switch_gas(self):
        r = Rig()
        assert r.tap('v_utilities')
        self.assertTrue(r.tap('u_aircool'))
        self.assertTrue(r.val('cooling.enabled'))
        self.assertTrue(r.widgets()['u_aircool']['value'])
        self.assertTrue(r.tap('u_gas'))
        self.assertEqual(r.val('gas.selected'), 'O2')
        self.assertEqual(r.text('u_gas'), 'Switch Gas: O2')
        self.assertTrue(r.tap('u_gas'))
        self.assertEqual(r.val('gas.selected'), 'N2')

    def test_system_info_and_method_page(self):
        r = Rig()
        assert r.tap('v_sysinfo')
        c = r.cells('f_sysinfo')
        self.assertEqual((c['Instrument'], c['Balance range'], c['Autosampler']), ('TGA 5500', '100 mg', '25 positions'))
        r.home()
        assert r.tap('v_method')
        self.assertEqual(r.widgets()['m_segments']['cells'][0], ['1', 'Ramp 300.00 °C/min to 500.00 °C', 'Iso 0.17 min', 'N2'])
        self.assertFalse(r.tap('m_next'))
        self.assertEqual(r.dialog(), ('err_not_running', ['No method running.']))

    def test_unsupported_items_show_the_device_response(self):
        r = Rig()
        self.assertEqual([u['item'] for u in rt.PANEL_SPEC['unsupported']], ['Network Settings'])
        assert r.tap('v_settings')
        assert r.tap('s_touch')
        for i in range(3):
            self.assertTrue(r.tap(f'tc_target{i}'))
        r.ov('OK')
        self.assertEqual(r.cells('f_settings'), {'Serial Number': 'tga_panel', 'IP Address': 'Not connected'})
        self.assertFalse(r.tap('s_network'))
        self.assertEqual(r.dialog(), ('no_network', ['No network connection.']))


    def test_load_and_unload_pans_with_the_autosampler(self):
        r = Rig()
        assert r.tap('ft_tare')
        r.call(r.E, 'tray.place', position=3, sample=rt.SAMPLE_EXAMPLE)     # a prepared pan on the carousel (by hand)
        assert r.tap('v_autosampler')
        self.assertEqual(r.widgets()['pos_3']['fill'], 'warn')
        assert r.tap('pos_3')                                                  # pan 3 to front
        r.wait(lambda: not r.val('carousel.moving'), 30)
        self.assertTrue(r.tap('ft_load'))                                     # LOAD: onto the balance
        r.wait(lambda: r.val('autosampler.transfer') is None, 30)
        self.assertTrue(r.val('sample.present'))
        self.assertEqual(r.val('autosampler.balance_origin'), 3)
        self.assertEqual(r.text('as_balance'), 'Pan on balance')
        r.call(r.A, 'gas.set_flow', sample_flow_m3_s=25e-6 / 60, balance_flow_m3_s=10e-6 / 60)
        r.call(r.A, 'gas.select', gas='N2')
        r.call(r.A, 'gas.set_open', opened=True)
        r.call(r.A, 'method.configure', **QUICK)
        furnace(r, False)
        assert r.tap('ft_start')
        r.wait(lambda: r.val('acquisition.phase') != 'running', 120)
        self.assertEqual(r.val('acquisition.phase'), 'succeeded')             # the run weighed the loaded pan
        r.call(r.A, 'thermal.disable')
        r.call(r.A, 'thermal.set_cooling', enabled=True)
        r.wait(lambda: r.h._cold(), 1200, 1.0)
        furnace(r, True)
        self.assertTrue(r.tap('ft_load'))                                     # position 3 (in front) is empty: unload to it
        r.wait(lambda: r.val('autosampler.transfer') is None, 30)
        self.assertFalse(r.val('sample.present'))
        self.assertTrue(r.val('autosampler.tray_occupied')[2])
        self.assertFalse(r.tap('ft_load'))                                    # a run pan is not loaded again
        self.assertEqual(r.dialog(), ('err_pan_already_run', ['This pan has already been run.']))


class TestRulesAndRefusals(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(), Rig()
        assert a.tap('ft_tare')
        b.call(b.A, 'balance.tare')
        assert a.tap('v_autosampler')
        assert a.tap('pos_12')
        b.call(b.A, 'carousel.index', position=12)
        for x in (a, b):
            x.step(20)
        assert a.tap('ft_furnace')
        b.call(b.A, 'furnace.set', opened=False)
        assert a.tap('ft_home')
        assert a.tap('v_utilities')
        assert a.tap('u_aircool')
        b.call(b.A, 'thermal.set_cooling', enabled=True)
        for x in (a, b):
            x.step(5)
        self.assertEqual(a.h.read(), b.h.read())
        self.assertEqual(a.call(a.E, 'ledger.read'), b.call(b.E, 'ledger.read'))

    def test_refusals_show_the_device_dialog_and_change_nothing(self):
        r = Rig()
        before = r.state()
        self.assertFalse(r.tap('ft_start'))                                       # furnace open, no sample
        self.assertEqual(r.dialog(), ('err_not_ready', ['Not ready: close the furnace and wait for the motion.']))
        self.assertEqual(r.state(), before)
        r.ov('OK')
        ready(r)
        assert r.tap('ft_start')
        before = r.state()
        self.assertFalse(r.tap('ft_furnace'))                                     # not during an experiment
        self.assertEqual(r.dialog(), ('err_busy', ['Not available while an experiment is running.']))
        r.ov('OK')
        assert r.tap('v_autosampler')
        self.assertFalse(r.tap('pos_3'))
        r.ov('OK')
        self.assertEqual(r.state(), before)
        r.wait(lambda: r.val('acquisition.phase') != 'running', 120)
        self.assertEqual(r.text('hd_status'), 'Status: Complete')
        self.assertFalse(r.tap('ft_stop'))                                        # nothing running: ignored, no dialog
        self.assertIsNone(r.dialog())

    def test_another_operators_run_is_refused_on_the_panel(self):
        """A run belongs to the operator who pressed Start: a second agent's Stop or Next Segment on the touch screen is refused like its direct
        calls (state unchanged, the device's dialog); the starter polls and cancels its own panel-started run."""
        r = Rig()
        r.h.register_principal('agent_b')
        b = r.h.session('agent_b')

        def tap_b(wid):
            x, y, w, h = r.widgets()[wid]['rect']
            return r.call(b, 'panel.touch', display_id=SCREEN, x=x + w // 2, y=y + h // 2)['accepted']
        ready(r, TWO)
        assert r.tap('ft_start')
        op = r.h.operation_id
        r.step(5)
        self.assertEqual(r.A.poll(op)['status'], 'running')
        before = r.state()
        self.assertEqual(r.call(b, 'acquisition.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertFalse(tap_b('ft_stop'))
        self.assertEqual(r.dialog(), ('err_access_denied', ['The running experiment belongs to another operator.']))
        r.ov('OK')
        assert tap_b('v_method')                                                 # the screen itself shows everything
        self.assertFalse(tap_b('m_next'))
        self.assertEqual(r.dialog()[0], 'err_access_denied')
        r.ov('OK')
        self.assertEqual(r.state(), before)
        self.assertEqual(r.val('acquisition.segment_index'), 0)
        self.assertTrue(r.tap('m_next'))                                         # the owner may
        self.assertEqual(r.val('acquisition.segment_index'), 1)
        self.assertEqual(r.A.cancel(op, 'cancel')['status'], 'succeeded')
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')

    def test_power_switch_is_a_hard_switch(self):
        """Final sync 2026-10-19: the power switch cuts the power like a plug; another agent's switch-off is not refused, the running experiment
        ends as on a power loss (failed, POWER_LOSS) and the screen goes dark."""
        r = Rig()
        r.h.register_principal('agent_b')
        b = r.h.session('agent_b')
        ready(r, TWO)
        assert r.tap('ft_start')
        op = r.h.operation_id
        r.step(1)
        self.assertEqual(r.A.poll(op)['status'], 'running')
        self.assertTrue(r.call(b, 'panel.switch', input_id='power_switch', position='0')['accepted'])
        self.assertFalse(r.h.power)
        self.assertEqual(r.A.poll(op)['status'], 'failed')
        self.assertEqual(r.A.poll(op)['result']['error_code'], 'POWER_LOSS')
        self.assertEqual(r.page(), ('dark', None))

    def test_air_cool_during_another_operators_run_is_refused(self):
        """Air Cool acts on the furnace during a run: a second agent's toggle (touch screen or direct call) is refused with the device dialog and
        the state unchanged; the owner may switch it; navigation stays free; after the run anyone may."""
        r = Rig()
        r.h.register_principal('agent_b')
        b = r.h.session('agent_b')

        def tap_b(wid):
            x, y, w, h = r.widgets()[wid]['rect']
            return r.call(b, 'panel.touch', display_id=SCREEN, x=x + w // 2, y=y + h // 2)['accepted']
        ready(r, TWO)
        assert r.tap('ft_start')
        r.step(5)
        before = r.state()
        self.assertEqual(r.call(b, 'thermal.set_cooling', enabled=True, expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertTrue(tap_b('v_utilities'))                                    # page change: free
        self.assertEqual(r.page(), ('gui', 'utilities'))
        self.assertFalse(tap_b('u_aircool'))
        self.assertEqual(r.dialog(), ('err_access_denied', ['The running experiment belongs to another operator.']))
        self.assertIn('The running experiment belongs to another operator.', json.dumps(r.disp()))
        r.ov('OK')
        self.assertEqual(r.state(), before)
        self.assertFalse(r.val('cooling.enabled'))
        self.assertTrue(r.tap('u_aircool'))                                      # the owner may
        self.assertTrue(r.val('cooling.enabled'))
        self.assertTrue(r.tap('u_aircool'))
        r.call(r.A, 'acquisition.stop')
        self.assertTrue(tap_b('u_aircool'))                                      # no run: no owner
        self.assertTrue(r.val('cooling.enabled'))

    def test_power_gate_and_no_timeouts(self):
        r = Rig(power=False)
        self.assertEqual(r.page(), ('dark', None))
        before = r.state()
        self.assertFalse(r.touch(400, 240))
        self.assertEqual(r.state(), before)
        self.assertEqual(int(r.h.panel.render(SCREEN).max()), 0)
        self.assertTrue(r.switch('I'))
        assert r.tap('v_method')
        r.step(1800)
        self.assertEqual(r.page(), ('gui', 'method'))                            # no screen time-out
        self.assertTrue(r.switch('0'))
        self.assertTrue(r.switch('I'))
        self.assertEqual(r.page(), ('gui', 'home'))

    def test_render_determinism_and_capture(self):
        a, b = Rig(), Rig()
        for x in (a, b):
            assert x.tap('v_autosampler')
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
        assert r.tap('ft_tare')
        ready(r, TWO)
        assert r.tap('ft_start')
        r.step(20)
        shots = {'home': r.h.panel.save_png(SCREEN, OUT / 'home.png')}
        assert r.tap('v_signals')
        shots['signals'] = r.h.panel.save_png(SCREEN, OUT / 'signals.png')
        assert r.tap('t_chart')
        shots['chart'] = r.h.panel.save_png(SCREEN, OUT / 'chart.png')
        r.home()
        assert r.tap('v_method')
        shots['menu'] = r.h.panel.save_png(SCREEN, OUT / 'menu.png')
        r.home()
        assert not r.tap('ft_furnace')
        shots['error'] = r.h.panel.save_png(SCREEN, OUT / 'error.png')
        r.ov('OK')
        assert r.tap('v_autosampler')
        shots['autosampler'] = r.h.panel.save_png(SCREEN, OUT / 'autosampler.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_ta_tga5500_contract_metadata()
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for name, cap in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
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
