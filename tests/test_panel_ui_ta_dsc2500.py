"""front-panel tests for CHEM-131 ta_dsc2500 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/ta_dsc2500.py):
python3 -m pytest tests/test_panel_ui_ta_dsc2500.py -q

Every gesture goes through the session API (panel.switch / panel.touch); widgets are found by id in the structured frame (panel.frame, evaluator)
and tapped at their centre.  Hands-on and TRIOS steps (placing pans, the method, the purge gas, equilibrating) are direct capability calls.
Themes: the five key procedures of the menu survey; signals and chart, active method and Next Step, system info, LN Pump; panel = direct
capability (same runtime state); refusals shown as the device's dialog with the state unchanged; power gate; no time-outs; touch calibration and
the unsupported items; render determinism, capture files and the evidence PNGs; contract entries."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import ta_dsc2500 as rt  # noqa: E402

SCREEN = 'touchscreen'
OUT = ROOT / 'outputs/panel_ui/ta_dsc2500'
CONTRACT = rt.ASSET / 'asset.contract.json'
SURVEY_TASKS = ['fill the LN2 dewar', 'reset the autosampler and lid (start of week)', 'start an experiment',
                'find the IP address / serial number for TRIOS connection', 'open/close the AutoLid to load pans manually (predecessor Q2000)']
DONE_TASKS = set()
QUICK = dict(rt.METHOD_EXAMPLE, start_temperature_k=298.15, segments=[dict(target_k=300.15, rate_k_s=0.2, hold_s=1.0)])
TWO = dict(QUICK, segments=[dict(target_k=300.15, rate_k_s=0.02, hold_s=50.0), dict(target_k=301.15, rate_k_s=0.2, hold_s=1.0)])


class Rig:
    """One DSC 2500 with an agent and an evaluator session; panel gestures only through panel.* capabilities."""

    def __init__(self, power=True, cooler='FACS'):
        self.h = rt.TaDsc2500Runtime('dsc_panel')
        self.A, self.E = self.h.session('agent'), self.h.session('evaluator')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, cooler=cooler))
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

    def dialog(self):
        ov = self.overlay()
        return (ov['dialog'], ov['lines']) if ov and ov['kind'] == 'dialog' else None

    def home(self):
        assert self.tap('ft_home')

    def state(self):
        return json.dumps({k: v for k, v in self.h.__dict__.items() if k not in ('revision', '_requests')}, sort_keys=True, default=str)


def lid(r, opened):
    """The lid key toggles the AutoLid; wait for the motion."""
    if bool(r.val('lid.target_m') > 0) != opened:
        assert r.tap('ft_lid')
    r.wait(lambda: not r.val('lid.moving'))


def ready(r, method=QUICK):
    """TRIOS / hands: pans placed with the lid open, purge gas, method; then the lid closed on the touch screen."""
    lid(r, True)
    r.call(r.E, 'sample.load', **dict(rt.SAMPLE_EXAMPLE, initial_temperature_k=298.15))
    r.call(r.A, 'gas.set_flow', flow_m3_s=50e-6 / 60)
    r.call(r.A, 'gas.set_open', opened=True)
    r.call(r.A, 'method.configure', **method)
    lid(r, False)


class TestKeyProcedures(unittest.TestCase):
    def test_fill_the_ln2_dewar(self):
        r = Rig(cooler='LN2P')
        lid(r, False)
        r.call(r.A, 'thermal.set_temperature', temperature_k=250.0, rate_k_s=1.0)  # sub-ambient: the LN Pump draws from the dewar
        r.step(60)
        self.assertLess(r.val('ln2.level_kg'), rt.LN2_DEWAR_KG)
        assert r.tap('v_utilities')                                              # Utilities > LN Pump
        self.assertEqual(r.page(), ('gui', 'utilities'))
        self.assertFalse(r.widgets()['u_fill']['value'])
        self.assertTrue(r.tap('u_fill'))                                        # slide Fill to On: the dewar fills
        self.assertTrue(r.val('ln2.fill'))
        self.assertTrue(r.widgets()['u_fill']['value'])
        r.wait(lambda: not r.val('ln2.fill'), 60)                               # full: the fill stops
        self.assertGreater(r.val('ln2.level_kg'), rt.LN2_DEWAR_KG - 0.01)
        self.assertTrue(r.tap('u_auto'))                                        # Auto Fill enabled for runs
        self.assertTrue(r.val('ln2.auto_fill'))
        self.assertEqual(r.text('u_level'), 'Dewar level: 100 %')
        DONE_TASKS.add('fill the LN2 dewar')

    def test_reset_the_autosampler_and_lid(self):
        r = Rig()
        lid(r, True)
        r.call(r.A, 'sampler.move', x_m=0.165, y_m=0.0, z_m=0.06)               # the arm left out at a tray position (earlier commands)
        r.wait(lambda: not r.val('sampler.moving'))
        r.call(r.A, 'sampler.move', x_m=0.165, y_m=0.0, z_m=0.0)
        r.wait(lambda: not r.val('sampler.moving'))
        assert r.tap('v_autosampler')                                            # the tray graphic
        self.assertEqual(r.text('as_arm'), 'Not parked')
        self.assertEqual(r.text('tray_54'), '54')
        self.assertFalse(r.val('sample.present'))                               # the cell holds no pans
        self.assertTrue(r.tap('ft_reset'))
        self.assertEqual(r.text('as_arm'), 'Moving')
        r.wait(lambda: r.text('as_arm') == 'Parked', 60)
        self.assertEqual(r.h.sampler, rt.SAMPLER_PARK)
        r.home()
        self.assertEqual(r.page(), ('gui', 'home'))
        DONE_TASKS.add('reset the autosampler and lid (start of week)')

    def test_start_an_experiment(self):
        r = Rig()
        ready(r, TWO)
        self.assertTrue(r.tap('ft_start'))
        self.assertEqual(r.val('acquisition.phase'), 'running')
        self.assertEqual(r.text('hd_status'), 'Status: Test')
        r.step(10)
        assert r.tap('v_signals')
        c = r.cells('f_signals')
        self.assertEqual(c['Segment'], '1 Ramp')
        self.assertEqual(c['Purge Flow'], '50 mL/min')
        self.assertTrue(c['Remaining Run Time'].endswith(' min'))
        assert r.tap('t_chart')
        chart = r.widgets()['chart']
        self.assertGreater(len(chart['series'][0]['points']), 10)
        r.home()
        assert r.tap('v_method')
        self.assertEqual(r.widgets()['m_segments']['selected_rows'], [0])
        self.assertTrue(r.tap('m_next'))                                        # advance to the next step of the method
        self.assertEqual(r.val('acquisition.segment_index'), 1)
        self.assertEqual(r.widgets()['m_segments']['selected_rows'], [1])
        r.wait(lambda: r.val('acquisition.phase') != 'running', 120)
        self.assertEqual(r.text('hd_status'), 'Status: Complete')
        self.assertEqual(r.call(r.A, 'acquisition.result')['phase'], 'succeeded')   # the run belongs to the operator who pressed Start
        DONE_TASKS.add('start an experiment')

    def test_find_the_ip_address_and_serial_number(self):
        r = Rig()
        assert r.tap('v_settings')
        self.assertEqual(r.cells('f_settings'), {'Serial Number': 'dsc_panel', 'IP Address': 'Not connected'})
        DONE_TASKS.add('find the IP address / serial number for TRIOS connection')

    def test_open_close_the_autolid_to_load_pans(self):
        r = Rig()
        self.assertTrue(r.val('lid.opened'))
        lid(r, False)
        self.assertEqual(r.text('ft_lid'), 'Lid\nclosed')
        self.assertTrue(r.tap('ft_lid'))                                         # LID OPEN
        self.assertEqual(r.text('ft_lid'), 'Lid\nmoving')
        r.wait(lambda: not r.val('lid.moving'))
        self.assertTrue(r.val('lid.opened'))
        r.call(r.E, 'sample.load', **rt.SAMPLE_EXAMPLE)                          # pans placed by hand
        self.assertTrue(r.tap('ft_lid'))                                         # LID CLOSED
        r.wait(lambda: not r.val('lid.moving'))
        self.assertFalse(r.val('lid.opened'))
        DONE_TASKS.add('open/close the AutoLid to load pans manually (predecessor Q2000)')


class TestFunctions(unittest.TestCase):
    def test_system_info_and_method_page(self):
        r = Rig(cooler='RCS90')
        assert r.tap('v_sysinfo')
        c = r.cells('f_sysinfo')
        self.assertEqual((c['Instrument'], c['Cooling Accessory'], c['Purge Gas']), ('DSC 2500', 'RCS 90', 'Nitrogen'))
        r.home()
        assert r.tap('v_method')
        self.assertTrue(r.text('m_start').startswith('Equilibrate at 151.60'))
        self.assertEqual(r.widgets()['m_segments']['cells'][0], ['1', 'Ramp 10.00 °C/min to 163.60 °C', 'Iso 0.33 min'])
        self.assertFalse(r.tap('m_next'))                                       # nothing running
        self.assertEqual(r.dialog(), ('err_not_running', ['No method running.']))

    def test_ln_pump_not_installed(self):
        r = Rig(cooler='FACS')
        assert r.tap('v_utilities')
        self.assertEqual(r.text('u_level'), 'LN Pump: not installed')
        self.assertFalse(r.tap('u_fill'))
        self.assertEqual(r.dialog(), ('err_no_ln2_pump', ['No LN Pump connected.']))

    def test_touch_calibration_and_unsupported_network(self):
        r = Rig()
        assert r.tap('v_settings')
        assert r.tap('s_touch')
        for i in range(3):
            self.assertTrue(r.tap(f'tc_target{i}'))
        self.assertEqual(r.dialog(), ('tcal_done', ['Calibration complete.']))
        r.ov('OK')
        self.assertEqual(r.page(), ('gui', 'settings'))
        self.assertFalse(r.tap('s_network'))
        self.assertEqual(r.dialog(), ('no_network', ['No network connection.']))
        self.assertTrue(r.frame()['ui']['unsupported'])
        self.assertEqual([u['item'] for u in rt.PANEL_SPEC['unsupported']], ['Network Settings'])


    def test_load_and_unload_a_pan_with_the_autosampler(self):
        r = Rig()
        lid(r, True)
        r.call(r.E, 'tray.place', position=12, sample=rt.SAMPLE_EXAMPLE)     # a prepared pan on the tray (by hand)
        assert r.tap('v_autosampler')
        self.assertEqual(r.widgets()['tray_12']['fill'], 'warn')
        self.assertTrue(r.tap('tray_12'))                                      # select position 12
        self.assertTrue(r.widgets()['tray_12']['selected'])
        self.assertTrue(r.tap('as_load'))                                      # LOAD: the arm picks the pan and places it in the cell
        self.assertEqual(r.text('as_arm'), 'Loading')
        r.wait(lambda: r.val('autosampler.transfer') is None, 60)
        self.assertTrue(r.val('sample.present'))
        self.assertEqual(r.val('autosampler.cell_origin'), 12)
        self.assertEqual(r.widgets()['as_s']['fill'], 'warn')
        self.assertEqual(r.text('as_arm'), 'Parked')
        self.assertFalse(r.tap('as_load'))                                     # the selected position is empty now
        self.assertEqual(r.dialog()[0], 'err_tray_position_empty')
        r.ov('OK')
        r.call(r.A, 'gas.set_flow', flow_m3_s=50e-6 / 60)                       # run it (TRIOS: gas, method; lid closed on the screen)
        r.call(r.A, 'gas.set_open', opened=True)
        r.call(r.A, 'method.configure', **QUICK)
        lid(r, False)
        assert r.tap('ft_start')
        r.wait(lambda: r.val('acquisition.phase') != 'running', 120)
        self.assertEqual(r.val('acquisition.phase'), 'succeeded')              # the measurement used the pan in the cell
        r.call(r.A, 'thermal.disable')
        r.wait(lambda: r.h._cold(), 600)
        lid(r, True)
        assert r.tap('tray_20')
        self.assertTrue(r.tap('as_unload'))                                    # UNLOAD to position 20
        r.wait(lambda: r.val('autosampler.transfer') is None, 60)
        self.assertFalse(r.val('sample.present'))
        self.assertTrue(r.val('autosampler.tray_occupied')[19])
        self.assertFalse(r.tap('as_unload'))                                   # position 20 is occupied again
        self.assertEqual(r.dialog()[0], 'err_tray_position_occupied')
        r.ov('OK')
        assert r.tap('tray_21')
        self.assertFalse(r.tap('as_unload'))
        self.assertEqual(r.dialog(), ('err_cell_empty', ['No pan in the cell.']))


class TestRulesAndRefusals(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(cooler='LN2P'), Rig(cooler='LN2P')
        assert a.tap('ft_lid')
        b.call(b.A, 'lid.set', opened=False)
        assert a.tap('v_utilities')
        assert a.tap('u_auto')
        b.call(b.A, 'ln2.set', fill=False, auto_fill=True)
        for x in (a, b):
            x.step(5)
        assert a.tap('ft_lid')
        b.call(b.A, 'lid.set', opened=True)
        for x in (a, b):
            x.step(5)
        assert a.tap('ft_reset')
        b.call(b.A, 'autosampler.reset')
        self.assertEqual(a.h.read(), b.h.read())
        self.assertEqual(a.call(a.E, 'ledger.read'), b.call(b.E, 'ledger.read'))

    def test_refusals_show_the_device_dialog_and_change_nothing(self):
        r = Rig()
        before = r.state()
        self.assertFalse(r.tap('ft_start'))                                      # lid open, no pans
        self.assertEqual(r.dialog(), ('err_not_ready', ['Not ready: close the lid and wait for the autosampler.']))
        self.assertEqual(r.state(), before)
        r.ov('OK')
        r.call(r.A, 'sampler.move', x_m=0.165, y_m=0.0, z_m=0.06)
        r.wait(lambda: not r.val('sampler.moving'))
        before = r.state()
        self.assertFalse(r.tap('ft_lid'))                                         # arm out: the lid cannot close
        self.assertEqual(r.dialog()[0], 'err_sampler_not_parked')
        self.assertEqual(r.state(), before)
        r.ov('OK')
        assert r.tap('ft_reset')
        r.wait(lambda: not r.val('sampler.moving'))
        ready(r)
        assert r.tap('ft_start')
        before = r.state()
        self.assertFalse(r.tap('ft_lid'))                                         # not during an experiment
        self.assertEqual(r.dialog(), ('err_busy', ['Not available while an experiment is running.']))
        r.ov('OK')
        self.assertFalse(r.tap('ft_reset'))
        r.ov('OK')
        self.assertEqual(r.state(), before)
        self.assertTrue(r.tap('ft_stop'))
        self.assertEqual(r.text('hd_status'), 'Status: Stopped')
        self.assertFalse(r.tap('ft_stop'))                                       # nothing running: ignored, no dialog
        self.assertIsNone(r.dialog())

    def test_another_operators_run_is_refused_on_the_panel(self):
        """A run belongs to the operator who pressed Start: a second agent's Stop or Next Step on the touch screen is refused like its direct
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

    def test_power_gate_and_no_timeouts(self):
        r = Rig(power=False)
        self.assertEqual(r.page(), ('dark', None))
        before = r.state()
        self.assertFalse(r.touch(400, 240))
        self.assertEqual(r.state(), before)
        self.assertEqual(int(r.h.panel.render(SCREEN).max()), 0)
        self.assertTrue(r.switch('I'))
        assert r.tap('v_signals')
        r.step(1800)
        self.assertEqual(r.page(), ('gui', 'signals'))                           # no screen time-out
        self.assertTrue(r.switch('0'))
        self.assertFalse(r.val('power.enabled'))
        self.assertTrue(r.switch('I'))
        self.assertEqual(r.page(), ('gui', 'home'))                              # power cycle: home page

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
        self.assertNotIn('panel.press', d['capabilities'])                       # no keys besides the touch screen
        self.assertEqual(d['asset_version'], rt.ASSET_VERSION)

    def test_evidence_renders(self):
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
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
        assert not r.tap('ft_lid')
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
        meta = rt.export_ta_dsc2500_contract_metadata()
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
