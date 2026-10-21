"""front-panel tests for CHEM-156 bruker_x4poseidon (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/bruker_x4poseidon.py):
python3 -m pytest tests/test_panel_ui_bruker_x4poseidon.py -q

APPROXIMATE panel (no public operating manual): the front touch screen is a status display (X-ray tab with the voltage and current bars, status
tab), the multi-function status light, the mains button and the safety button.  Every gesture goes through the session API (panel.touch /
panel.press / panel.switch); 3DxSUITE functions (X-ray, door, acknowledge) are runtime capabilities.  Themes: power-up with the start-up screen,
the tabs and Home, the bars following the tube ramp, the emergency stop latch, the status light, panel = direct capability, gates while off,
render determinism, evidence PNGs, contract entries."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import bruker_x4poseidon as rt  # noqa: E402

INST = 'x4_poseidon_01'
SCR = 'screen'
OUT = ROOT / 'outputs/panel_ui/bruker_x4poseidon'
LAMPS = ('lamp_status_green', 'lamp_status_orange', 'lamp_status_red')


class Rig:
    def __init__(self, on=True):
        self.h = rt.BrukerX4PoseidonRuntime(INST)
        self.h.panel_capture_dir = tempfile.mkdtemp(prefix='x4_panel_')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        if on:
            assert self.press('button_mains')
            self.step(rt.BOOT_S + 1.0)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        if cap != 'sim.step':
            assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        while dt > 1e-9:
            d = min(dt, 600.0)
            self.call(self.E, 'sim.step', dt_s=float(d))
            dt -= d

    def press(self, inp):
        return self.call(self.A, 'panel.press', input_id=inp)['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='button_emergency_stop', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def widgets(self):
        return {w['id']: w for w in self.frame()['displays'][SCR]['widgets']}

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def lamps(self):
        d = self.frame()['displays']
        return tuple(d[k]['on'] for k in LAMPS)

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.call(self.A, 'panel.touch', display_id=SCR, x=x + w // 2, y=y + h // 2)['accepted']

    def png(self):
        return Path(self.call(self.A, 'panel.capture', display_id=SCR)['image']['path']).read_bytes()


def state(h):
    return json.dumps(h.read('evaluator'), sort_keys=True, default=float)


class Panel(unittest.TestCase):
    def test_power_up_start_screen_and_tabs(self):
        g = Rig(on=False)
        self.assertEqual(g.page(), ('off', 'dark'))
        self.assertEqual(g.lamps(), (False, False, False))
        self.assertFalse(g.call(g.A, 'panel.touch', display_id=SCR, x=400, y=240)['accepted'])       # dark screen
        self.assertTrue(g.press('button_mains'))
        self.assertEqual(g.page(), ('boot', 'starting'))
        g.step(rt.BOOT_S - 1.0)
        self.assertEqual(g.page()[0], 'boot')
        g.step(2.0)
        self.assertEqual(g.page(), ('main', 'xray'))
        self.assertEqual(g.lamps(), (True, False, False))                                             # ready, X-ray off
        g.tap('tab_status')
        self.assertEqual(g.page()[1], 'status')
        self.assertIn('Ready', g.widgets()['st_system']['text'])
        self.assertIn('Door: Closed', g.widgets()['st_door']['text'])
        g.tap('home')
        self.assertEqual(g.page()[1], 'xray')
        self.assertTrue(g.press('button_mains'))                                                     # off again
        self.assertEqual(g.page(), ('off', 'dark'))
        self.assertFalse(g.val('power.enabled'))

    def test_bars_follow_the_tube_and_status_light(self):
        g = Rig()
        g.call(g.A, 'xray.set', voltage_v=80.0e3, current_a=200.0e-6)
        g.call(g.A, 'xray.enable', enabled=True)
        g.step(5.0)
        w = g.widgets()
        self.assertAlmostEqual(w['voltage_bar']['fraction'], g.val('xray.voltage_v') / 100e3, places=9)
        g.step(400.0)
        w = g.widgets()
        self.assertEqual((w['voltage_value']['text'], w['current_value']['text']), ('80', '200'))
        self.assertAlmostEqual(w['voltage_bar']['fraction'], 0.8, places=9)
        self.assertAlmostEqual(w['current_bar']['fraction'], 0.4, places=9)
        self.assertEqual(w['xray_icon']['color'], 'warn')
        self.assertEqual(g.lamps(), (False, True, False))                                             # orange: X-ray on
        g.tap('tab_status')
        self.assertIn('X-ray: On', g.widgets()['st_xray']['text'])

    def test_emergency_stop_latch_release_acknowledge(self):
        g = Rig()
        g.call(g.A, 'xray.enable', enabled=True)
        g.step(30.0)
        self.assertTrue(g.switch('pressed'))                                                         # X-ray off, all motion stops
        self.assertTrue(g.val('emergency.stopped'))
        self.assertFalse(g.val('xray.on'))
        self.assertEqual(g.lamps(), (False, False, True))
        self.assertIn('EMERGENCY_STOP', g.widgets()['error']['text'])
        self.assertFalse(g.switch('pressed'))                                                        # already pressed
        r = g.call(g.A, 'system.acknowledge', expect='rejected')                                     # cause persists
        self.assertEqual(r['error']['code'], 'CAUSE_PERSISTS')
        self.assertTrue(g.switch('released'))
        self.assertFalse(g.val('emergency.stopped'))
        self.assertEqual(g.lamps(), (False, False, True))                                             # error stays until acknowledged
        g.call(g.A, 'system.acknowledge')
        self.assertEqual(g.lamps(), (True, False, False))
        self.assertNotIn('error', g.widgets())

    def test_buttons_work_while_off_and_touches_do_not(self):
        g = Rig(on=False)
        before = state(g.h)
        self.assertFalse(g.call(g.A, 'panel.touch', display_id=SCR, x=100, y=100)['accepted'])
        self.assertEqual(before, state(g.h))
        self.assertTrue(g.switch('pressed'))                                                         # the safety button acts without power
        self.assertTrue(g.val('emergency.stopped'))
        self.assertTrue(g.switch('released'))

    def test_panel_and_direct_calls_give_the_same_state(self):
        a, b = Rig(on=False), Rig(on=False)
        a.press('button_mains')
        b.call(b.A, 'power.press_button')
        a.step(70.0)
        b.step(70.0)
        a.switch('pressed')
        b.call(b.A, 'emergency.stop')
        self.assertEqual(state(a.h), state(b.h))

    def test_mains_fault_keeps_the_screen_dark(self):
        g = Rig(on=False)
        g.h.plant['mains_voltage_v'] = 60.0                                                         # host: mains out of range
        self.assertFalse(g.press('button_mains'))
        self.assertEqual(g.page(), ('off', 'dark'))


class CallerRules(unittest.TestCase):
    """Sync audit 2026-10-19, rule 2: panel gestures made through a session act as that caller.  While agent A's acquisition runs, agent B's
    mains button is refused like B's direct power.press_button (state unchanged); the safety button always works; A's own button works."""
    def rig(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        g.B = g.h.session('agent_b')
        g.call(g.A, 'xray.set', voltage_v=60e3, current_a=150e-6)
        g.call(g.A, 'xray.enable', enabled=True)
        g.step(rt.WARMUP_S + 60.0)
        g.op = g.call(g.A, 'calibration.flat_field', expect='accepted')['operation_id']
        g.step(1.0)
        self.assertEqual(g.h.operations[g.op]['phase'], 'running')
        return g

    def test_the_mains_button_is_physical_but_commands_are_guarded(self):
        """Sweep 2026-10-19: commands that change another agent's acquisition are refused; the mains button cuts the power (hard switch):
        never refused, the acquisition fails as at a power loss."""
        g = self.rig()
        before = state(g.h)
        for cap, args in (('xray.enable', dict(enabled=False)), ('stage.rotate', dict(angle_rad=1.0))):
            self.assertEqual(g.call(g.B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.call(g.B, 'scan.start', expect='rejected')['error']['code'], 'MEASUREMENT_RUNNING')   # narrowed: the device's own lock
        self.assertEqual(before, state(g.h))
        self.assertEqual(g.A.poll(g.op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            g.B.poll(g.op)
        self.assertTrue(g.call(g.B, 'panel.press', input_id='button_mains')['accepted'])
        self.assertFalse(g.val('power.enabled'))
        self.assertEqual(g.h.operations[g.op]['phase'], 'failed')

    def test_the_safety_button_always_works(self):
        g = self.rig()
        self.assertTrue(g.call(g.B, 'panel.switch', input_id='button_emergency_stop', position='pressed')['accepted'])
        self.assertTrue(g.val('emergency.stopped'))
        self.assertFalse(g.val('xray.on'))
        self.assertEqual(g.h.operations[g.op]['phase'], 'failed')
        self.assertTrue(g.call(g.B, 'panel.switch', input_id='button_emergency_stop', position='released')['accepted'])   # releasing too

    def test_the_owner_may_switch_off(self):
        g = self.rig()
        self.assertTrue(g.press('button_mains'))
        self.assertFalse(g.val('power.enabled'))
        self.assertEqual(g.h.operations[g.op]['phase'], 'failed')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        g = Rig()
        g.call(g.A, 'xray.enable', enabled=True)
        g.step(400.0)
        a = g.h.panel.png_bytes(SCR)[0]
        self.assertEqual(a, copy.deepcopy(g.h).panel.png_bytes(SCR)[0])
        self.assertEqual(g.png(), a)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'home.png').write_bytes(a)
        g.tap('tab_status')
        (OUT / 'menu.png').write_bytes(g.png())
        g.tap('home')
        g.switch('pressed')
        (OUT / 'error.png').write_bytes(g.png())
        g2 = Rig(on=False)
        g2.press('button_mains')
        (OUT / 'boot.png').write_bytes(g2.png())
        (OUT / 'renders.json').write_text(json.dumps({f.name: dict(sha256=hashlib.sha256(f.read_bytes()).hexdigest(), size=[800, 480])
                                                      for f in sorted(OUT.glob('*.png'))}, indent=1) + '\n')

    def test_spec_and_contract(self):
        spec = rt.PANEL_SPEC
        self.assertTrue(spec['approximate'])
        self.assertEqual(rt._panel_ui.check_spec(spec, set(rt.CAPS)), [])
        self.assertTrue(set(rt._panel_ui.spec_capabilities(spec)) <= set(rt.CAPS))
        meta = rt.export_bruker_x4poseidon_contract_metadata()
        self.assertTrue(set(rt.PANEL_CAPS) <= set(meta['capabilities']))
        c = json.loads((rt.ASSET / 'asset.contract.json').read_text())
        if c['asset_version'] != '1.0.0':
            self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
            self.assertTrue(set(rt.PANEL_CAPS) <= set(c['capabilities']))
            self.assertIn('chemistry_lab.panel_ui', c['extensions'])
            self.assertIsNone(c['artifacts']['panel_engine']['sha256'])


if __name__ == '__main__':
    unittest.main()
