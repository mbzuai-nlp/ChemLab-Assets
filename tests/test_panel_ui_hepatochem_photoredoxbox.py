"""Front-panel tests for CHEM-017 hepatochem_photoredoxbox (approximate panel_ui.json + scripts/asset_runtime/_panel_ui.py):
python3 -m pytest tests/test_panel_ui_hepatochem_photoredoxbox.py -q

No user manual is saved, so the spec is approximate (brochure + photos); these tests pin the modelled behaviour.  Every gesture goes through the
session API (panel.press / panel.turn); checks look at the runtime state, the structured frame and the rendered display (lit pixels per text
line).  The display is a 320 x 240 graphic LCD (brochure photos; 2026-10-21, was a 2 x 16 character LCD): home rows 'Temp / Set / Light / Stir /
Time', a status line at the bottom that also shows the transient messages, and the 'Set Parameters' set-up page.
Themes: set light, temperature, stirring and time; start / stop the irradiation; power; panel = direct capability; refusals; render determinism
and the PNGs of outputs/panel_ui/hepatochem_photoredoxbox/; contract entries."""
import copy
import hashlib
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import hepatochem_photoredoxbox as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'photoredoxbox_01'
RPM = 2 * math.pi / 60
OUT = ROOT / 'outputs/panel_ui/hepatochem_photoredoxbox'
SURVEY_TASKS = ['set light intensity', 'set temperature', 'set stirring and time', 'start and stop irradiation', 'power on and off']
DONE_TASKS = set()
LCD = rt.PANEL_SPEC['displays']['lcd']


ROWS, S = (LCD['size'][1] - 4) // (9 * LCD['text_scale']), LCD['text_scale']
BG = LCD['colors']['background']


def lit_rows(img):
    """Text rows of the rendered graphic display that show anything (pixels off the background colour inside the row band)."""
    return [bool((abs(img[2 + r * 9 * S:2 + r * 9 * S + 7 * S].astype(int) - BG).sum(axis=2) > 120).any()) for r in range(ROWS)]


class Rig:
    def __init__(self, configured=True, lid_open=False):
        self.h = rt.HepatochemPhotoredoxboxRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if configured:
            self.call(self.A, 'lid.set_lift', lift_m=0.12)
            self.call(self.E, 'rack.configure', mode='8x4ml')
            self.call(self.E, 'thermal.configure', **rt.CAPS['thermal.configure'][6])
            self.call(self.E, 'led.configure', **rt.LED_EXAMPLE)
            self.call(self.E, 'sample.configure', **rt.EXAMPLE)
            if not lid_open:
                self.call(self.A, 'lid.set_lift', lift_m=0.0)

    def call(self, sess, cap, expect=('succeeded', 'accepted'), **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        expect = (expect,) if isinstance(expect, str) else expect
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        if r['status'] != 'rejected':
            assert self.h.time_s == t0, cap
            return r['result']
        return r

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def turn(self, n):
        return self.call(self.A, 'panel.turn', input_id='knob', detents=n)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lcd(self):
        f = self.frame()['displays']['lcd']
        lines = [x.rstrip() for x in f['lines']]
        assert lit_rows(self.h.panel.render('lcd')) == [bool(x.strip()) for x in f['shown']], (lines, f['shown'])
        return lines

    def screen(self):
        return self.frame()['screen']

    def state(self, k):
        return self.E.read(instance_id=INST, state_ids=[k])['observations'][k]['value']

    def step(self, s):
        self.h.step(float(s))


def runtime_state(h):
    r = h.read('evaluator')
    return json.dumps(dict(read=r, leds=h.leds, powered=h.powered), sort_keys=True, default=float)


class Procedures(unittest.TestCase):
    def test_power_on_and_off(self):
        g = Rig()
        self.assertEqual(g.lcd(), [''] * 8)
        self.assertFalse(g.turn(1))                                                      # off: only the button
        self.assertTrue(g.press('button'))
        self.assertTrue(g.state('power.enabled'))
        self.assertEqual(g.lcd(), ['PhotoRedOx Box', 'Temp    25.0 C', 'Set     25 C', 'Light   50 %', 'Stir    0 rpm', 'Time    0 min', '', 'Ready'])
        self.assertTrue(g.press('button', 2.0))                                          # hold 2 s: off
        self.assertFalse(g.state('power.enabled'))
        self.assertEqual(g.screen(), 'off')
        DONE_TASKS.add('power on and off')

    def test_set_light_temperature_stirring_time_and_run(self):
        g = Rig()
        g.press('button')
        self.assertTrue(g.press('knob'))                                                 # push: set-up, Light flashes
        self.assertEqual(g.lcd()[:4], ['Set Parameters', '', 'Light', '50 %'])
        self.assertTrue(g.frame()['displays']['lcd']['blink'][3])
        g.turn(-2)
        self.assertEqual(g.lcd()[3], '40 %')
        g.press('knob')
        self.assertEqual(g.state('optics.intensities'), [0.4] * 4)
        DONE_TASKS.add('set light intensity')
        self.assertEqual(g.lcd()[2:4], ['Temp set', '30 C'])
        g.turn(10)
        g.press('knob')
        self.assertAlmostEqual(g.state('temperature.target_k'), 273.15 + 40)
        DONE_TASKS.add('set temperature')
        g.turn(12)                                                                         # 600 rpm
        g.press('knob')
        self.assertAlmostEqual(g.state('stir.target_rad_s'), 600 * RPM)
        self.assertEqual(g.lcd()[2:4], ['Time', '1 min'])
        g.turn(29)
        g.press('knob')                                                                    # 30 min, back home
        self.assertEqual(g.state('method.duration_s'), 1800.0)
        self.assertEqual(g.screen(), 'home')
        self.assertEqual(g.lcd()[1:6], ['Temp    25.0 C', 'Set     40 C', 'Light   40 %', 'Stir    600 rpm', 'Time    30 min'])
        DONE_TASKS.add('set stirring and time')
        self.assertTrue(g.press('button'))                                               # start
        self.assertEqual(g.state('cycle.phase'), 'running')
        g.step(60.0)
        self.assertEqual((g.lcd()[5], g.lcd()[7]), ('Left    00:29:00', 'RUN'))
        g.press('knob')                                                                    # dim during the run
        g.turn(2)
        g.press('button')                                                                  # leave the set-up without storing
        self.assertEqual(g.state('optics.intensities'), [0.4] * 4)
        self.assertTrue(g.press('button'))                                               # stop
        self.assertEqual(g.state('cycle.phase'), 'cancelled')
        DONE_TASKS.add('start and stop irradiation')


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct(self):
        a, b = Rig(), Rig()
        a.press('button')
        b.call(b.A, 'power.set', enabled=True)
        a.press('knob')
        a.turn(4)
        for _ in range(4):
            a.press('knob')
        b.call(b.A, 'led.set_intensity', intensity=0.7)
        b.call(b.A, 'temperature.set', target_k=303.15)
        b.call(b.A, 'stir.set_speed', speed_rad_s=0.0)
        b.call(b.A, 'method.configure', duration_s=60.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.press('button')
        b.call(b.A, 'acquisition.start')
        a.step(30.0)
        b.step(30.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


class Rules(unittest.TestCase):
    def test_refusals(self):
        g = Rig(lid_open=True)
        g.press('button')
        self.assertFalse(g.press('button'))                                              # lid open: no start
        self.assertEqual(g.lcd()[7], 'CHECK LID/VIALS')                                 # the status line
        g.step(2.0)
        self.assertEqual(g.lcd()[7], 'Ready')
        u = Rig(configured=False)
        u.press('button')
        u.press('knob')
        self.assertFalse(u.press('knob'))                                                # no LED modules configured: the dimmer is refused
        self.assertEqual(u.screen(), 'home')
        self.assertEqual(u.lcd()[7], 'NOT READY')
        r = u.call(u.A, 'panel.press', expect='rejected', input_id='nope')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(u.call(u.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_no_timeout_and_overtemperature_text(self):
        g = Rig()
        g.press('button')
        g.press('knob')
        g.step(600.0)
        self.assertEqual(g.screen(), 'setup')                                            # no time-out
        g.press('button')
        h = Rig()
        h.call(h.A, 'lid.set_lift', lift_m=0.12)
        h.call(h.E, 'sample.unload', slot=0)
        h.call(h.E, 'sample.configure', **dict(rt.EXAMPLE, initial_temperature_k=333.149, heat_capacity_j_k=0.1, sample_ua_w_k=0.0))
        h.call(h.A, 'lid.set_lift', lift_m=0.0)
        h.press('button')
        h.press('button')
        h.step(0.2)
        self.assertEqual(h.lcd()[7], 'FAULT OVERTEMP')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.press('button')
            g.call(g.A, 'method.configure', duration_s=600.0)
            g.press('button')
            g.step(30.0)
            return g
        a, b = scenario(), scenario()
        self.assertTrue((a.h.panel.render('lcd') == b.h.panel.render('lcd')).all())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('lcd', OUT / 'home.png')}
        a.press('button')
        a.press('knob')
        shots['menu'] = a.h.panel.save_png('lcd', OUT / 'menu.png')
        a.press('button')
        a.call(a.A, 'lid.set_lift', lift_m=0.12)
        a.press('button')
        shots['error'] = a.h.panel.save_png('lcd', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_and_copies(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        self.assertNotIn('panel', vars(g.h))

    def test_spec_and_contract(self):
        self.assertTrue(rt.PANEL_SPEC['approximate'])
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.ALL_CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.turn', 'panel.capture', 'panel.frame'})
        t = rt.export_hepatochem_photoredoxbox_panel_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k, e in t['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'errors', 'state_effects'):
                self.assertEqual(c['capabilities'][k][key], e[key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version'], ext['approximate']), ('panel_ui', 'parameter_cycle', pu.SPEC_VERSION, True))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/hepatochem_photoredoxbox.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('prb_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class CallerRule(unittest.TestCase):
    def test_a_run_started_on_the_panel_belongs_to_the_caller(self):
        g = Rig()
        self.assertTrue(g.press('button'))                                               # power on
        g.call(g.A, 'method.configure', duration_s=600.0)
        self.assertTrue(g.press('button'))                                               # irradiation started on the panel by the agent
        op = g.h.operation_id
        self.assertEqual((g.state('cycle.phase'), g.h._owners.get(op)), ('running', 'agent'))
        g.A.poll(op)                                                                      # the caller may follow it
        g.h.register_principal('agent_b', 'agent')
        for act in (lambda: g.h.session('agent_b').poll(op), lambda: g.h.session('agent_b').cancel(op, 'b1')):
            with self.assertRaises(rt.ActionRejected):                                    # another agent may not follow or cancel it
                act()
        self.assertEqual(g.state('cycle.phase'), 'running')
        g.A.cancel(op, 'a1')                                                              # the caller may cancel it
        self.assertNotEqual(g.state('cycle.phase'), 'running')

    def test_another_agent_cannot_stop_or_switch_off_the_irradiation(self):
        """Sync audit 2026-10-19: another agent's button press / hold and its direct acquisition.stop / power.set are refused (state unchanged)."""
        g = Rig()
        self.assertTrue(g.press('button'))
        g.call(g.A, 'method.configure', duration_s=600.0)
        self.assertTrue(g.press('button'))
        g.h.step(5.0)
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        snap = lambda: json.dumps(g.h.read('evaluator'), sort_keys=True, default=float)  # noqa: E731
        before = snap()
        self.assertFalse(g.call(B, 'panel.press', input_id='button')['accepted'])                    # stop
        self.assertEqual(g.lcd()[7], 'NOT ALLOWED')                                                  # the refusal shows on the display
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'ACCESS_DENIED')
        self.assertFalse(g.call(B, 'panel.press', input_id='button', duration_s=2.5)['accepted'])    # hold: power off
        for cap, args in (('acquisition.stop', {}), ('power.set', dict(enabled=False)), ('temperature.set', dict(target_k=313.15)),
                          ('stir.set_speed', dict(speed_rad_s=10.0)), ('led.set_intensity', dict(intensity=0.5))):   # changing the run too
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(snap(), before)
        self.assertTrue(g.press('button'))                                               # the owner may stop it
        self.assertEqual(g.state('cycle.phase'), 'cancelled')


class ZCoverage(unittest.TestCase):
    def test_all_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
