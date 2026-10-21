"""front-panel tests for CHEM-199 esco_efa5uduvw (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_esco_efa5uduvw.py -q

The panel is APPROXIMATE (no Sentinel XL user manual is saved).  Every gesture goes through the session API (panel.press / panel.switch).  Each
check looks at the runtime state and at the screen: the structured frame (LCD lines, LEDs) and the rendered pixels of the LEDs.  Themes: the three
procedures of the menu survey (light and exhaust on, silence an alarm, check the alarm with the self test); the settings menu (low / high alarm,
units, field calibration, PIN entry and new PIN) done only with keys; panel = direct capability; refusals, the PIN lock, power outage and the
30 s time-out; render determinism and the PNGs of outputs/panel_ui/esco_efa5uduvw/; contract entries of the panel capabilities."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import esco_efa5uduvw as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'esco_efa5uduvw_01'
OUT = ROOT / 'outputs/panel_ui/esco_efa5uduvw'
SPEC = rt.PANEL_SPEC
LEDS = ['led_safe', 'led_unsafe', 'led_mute']
SURVEY_TASKS = ['switch on light and exhaust before work', 'silence a face-velocity alarm', 'check the airflow alarm (maintenance)']
DONE_TASKS = set()


class Rig:
    def __init__(self, seed=1, **plant):
        self.h = rt.EscoEfa5uduvwRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        before = self.h.read('evaluator')
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            if cap != 'sim.step':
                assert self.h.t == t0, cap
                after = self.h.read('evaluator')
                changed = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)}
                assert not changed - set(rt.EFFECTS.get(cap, [])), (cap, changed - set(rt.EFFECTS.get(cap, [])))
        return r['result'] if expect == 'succeeded' else r

    def press(self, key):
        return self.call(self.A, 'panel.press', input_id=key)['accepted']

    def switch(self, name, pos):
        return self.call(self.A, 'panel.switch', input_id=name, position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lcd(self):
        return self.frame()['displays']['lcd']['lines']

    def leds(self):
        """LEDs lit now: frame and pixels must agree."""
        f = self.frame()['displays']
        on = {k for k in LEDS if f[k]['shown']}
        px = {k for k in LEDS if [int(v) for v in self.h.panel.render(k)[12, 12]] == SPEC['displays'][k]['colors']['on']}
        assert on == px, (on, px)
        return on

    def screen(self):
        return self.frame()['screen']

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def state(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def cv(self, v=0.45, h=0.457):
        self.call(self.E, 'exhaust.configure', mode='constant_volume', flow_m3_s=v * rt.a_eff(h))
        self.call(self.A, 'sash.set', height_m=h, hold=h > rt.H_STOP)

    def menu_to(self, label):
        assert self.press('key_menu')
        for _ in range(6):
            if self.lcd()[0] == label:
                return
            assert self.press('key_menu')
        raise AssertionError(label)

    def pin(self, digits):
        for d in digits:
            for _ in range(int(d)):
                assert self.press('key_up')
            self.press('key_set')


def runtime_state(h):
    r = h.read('evaluator')
    r.pop('clock.time_s')
    return json.dumps(dict(read=r, mon=h.mon, light=h.light, fan=h.fan, keylock=h.keylock), sort_keys=True, default=str)


class KeyProcedures(unittest.TestCase):
    def test_switch_on_light_and_exhaust(self):
        g = Rig()
        g.call(g.E, 'exhaust.configure', mode='blower', blower_speed=0.9)
        self.assertEqual(g.screen(), 'home')
        self.assertTrue(g.switch('fan_switch', 'on'))
        self.assertTrue(g.switch('light_switch', 'on'))
        self.assertTrue(g.state('fan.on') and g.state('lamp.lit'))
        g.step(30.0)
        self.assertEqual(g.lcd()[1], 'SAFE')
        self.assertEqual(g.leds(), {'led_safe'})
        self.assertRegex(g.lcd()[0], r'^0\.\d\d m/s$')
        self.assertTrue(g.switch('light_switch', 'off'))
        self.assertFalse(g.state('lamp.lit'))
        DONE_TASKS.add('switch on light and exhaust before work')

    def test_silence_a_face_velocity_alarm(self):
        g = Rig()
        g.cv(0.1)
        g.step(30.0)
        self.assertEqual(g.lcd()[1], 'LOW AIRFLOW')
        self.assertEqual(g.leds(), {'led_unsafe'})
        self.assertTrue(g.state('buzzer.on'))
        self.assertTrue(g.press('key_mute'))
        self.assertFalse(g.state('buzzer.on'))
        self.assertEqual(g.leds(), {'led_unsafe', 'led_mute'})
        DONE_TASKS.add('silence a face-velocity alarm')

    def test_self_test(self):
        g = Rig()
        g.cv(0.45)
        g.step(30.0)
        self.assertTrue(g.press('key_test'))
        self.assertEqual(g.lcd()[1], 'SELF TEST')
        self.assertEqual(g.leds(), set(LEDS))
        g.step(3.5)
        self.assertEqual((g.lcd()[1], g.leds()), ('SAFE', {'led_safe'}))
        DONE_TASKS.add('check the airflow alarm (maintenance)')


class Settings(unittest.TestCase):
    def test_low_high_units_and_time_out(self):
        g = Rig()
        g.cv(0.45)
        g.step(30.0)
        g.menu_to('LOW ALARM SET')
        self.assertEqual(g.lcd()[1], '0.30 m/s')
        for _ in range(5):
            g.press('key_down')
        self.assertEqual(g.lcd()[1], '0.25 m/s')
        self.assertTrue(g.press('key_set'))
        self.assertAlmostEqual(g.state('monitor.settings')['low_alarm_m_s'], 0.25, places=9)
        g.press('key_menu')
        self.assertEqual(g.lcd(), ['HIGH ALARM SET', '0.70 m/s'])
        for _ in range(50):
            g.press('key_down')                                                # stops at 0.20: below the low alarm -> INVALID
        self.assertFalse(g.press('key_set'))
        self.assertEqual(g.lcd()[1], 'INVALID')
        self.assertAlmostEqual(g.state('monitor.settings')['high_alarm_m_s'], 0.7, places=9)
        g.press('key_menu')
        g.step(2.0)                                                            # INVALID shows for 2 s
        self.assertEqual(g.lcd(), ['UNITS', 'METRIC m/s'])
        g.press('key_up')
        g.press('key_set')
        self.assertEqual(g.state('monitor.settings')['units'], 'imperial')
        g.step(31.0)                                                           # no key for 30 s: back to the run display
        self.assertEqual(g.screen(), 'home')
        self.assertRegex(g.lcd()[0], r'^\d+ fpm$')

    def test_field_calibration(self):
        g = Rig()
        g.cv(0.45)
        g.step(30.0)
        ref = g.call(g.A, 'anemometer.measure')['face_velocity_m_s']
        g.menu_to('CALIBRATE')
        shown = g.frame()['ui']['editor']['buffer']['cal']
        steps = int(round((round(ref, 2) - shown) / 0.01))
        for _ in range(abs(steps)):
            g.press('key_up' if steps > 0 else 'key_down')
        self.assertTrue(g.press('key_set'))
        self.assertTrue(g.state('monitor.settings')['calibrated'])
        self.assertAlmostEqual(g.h.v_s, round(ref, 2), places=6)

    def test_pin_entry_and_new_pin(self):
        g = Rig()
        g.cv(0.45)
        g.step(30.0)
        g.menu_to('NEW PIN')
        g.press('key_set')
        self.assertEqual(g.screen(), 'new_pin')
        g.pin('1234')
        self.assertEqual(g.lcd()[1], 'PIN STORED')
        self.assertTrue(g.state('monitor.settings')['pin_set'])
        g.step(3.0)
        self.assertTrue(g.press('key_menu'))                                   # the PIN is asked first
        self.assertEqual((g.screen(), g.lcd()[0]), ('pin_entry', 'ENTER PIN'))
        g.pin('1235')
        self.assertEqual((g.screen(), g.lcd()[1]), ('home', 'WRONG PIN'))
        g.step(3.0)
        g.press('key_menu')
        g.pin('1234')
        self.assertEqual(g.screen(), 'menu')
        g.press('key_up')
        g.press('key_set')
        self.assertAlmostEqual(g.state('monitor.settings')['low_alarm_m_s'], 0.31, places=9)
        for _ in range(4):
            g.press('key_menu')
        g.press('key_set')
        g.pin('0000')                                                          # 0000 removes the PIN
        self.assertFalse(g.state('monitor.settings')['pin_set'])


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_calls(self):
        a, b = Rig(), Rig()
        for g in (a, b):
            g.cv(0.1)
            g.step(30.0)
        a.menu_to('LOW ALARM SET')
        for _ in range(3):
            a.press('key_down')
        a.press('key_set')
        a.press('key_menu')
        a.press('key_menu')
        a.press('key_up')
        a.press('key_set')
        a.press('key_mute')
        a.switch('light_switch', 'on')
        a.switch('keylock', 'limit')
        b.call(b.A, 'monitor.configure', low_alarm_m_s=0.27)
        b.call(b.A, 'monitor.configure', units='imperial')
        b.call(b.A, 'monitor.press', key='mute')
        b.call(b.A, 'light.set', on=True)
        b.call(b.A, 'keylock.set', mode='limit')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.step(60.0)
        b.step(60.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


class Rules(unittest.TestCase):
    def test_outage_gate_and_keylock_refusal(self):
        g = Rig()
        g.cv(0.45)
        g.step(30.0)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('off', ['', ''], set()))
        self.assertFalse(g.press('key_test'))
        self.assertTrue(g.switch('keylock', 'off'))                            # mechanical: works without supply
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        self.assertEqual(g.screen(), 'home')
        g.call(g.A, 'sash.set', height_m=0.3)
        self.assertFalse(g.switch('keylock', 'closed'))                        # the sash is open: the key does not turn
        self.assertEqual(g.state('keylock.mode'), 'off')

    def test_unknown_inputs_rejected(self):
        g = Rig()
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='key_x')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.A.invoke(instance_id=INST, capability_id='panel.frame', arguments={}, request_id='f1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.cv(0.45)
            g.step(30.0)
            return g
        a, b = scenario(), scenario()
        for d in SPEC['displays']:
            self.assertTrue((a.h.panel.render(d) == b.h.panel.render(d)).all())
        self.assertEqual(a.h.panel.png_bytes('lcd'), b.h.panel.png_bytes('lcd'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('lcd', OUT / 'home.png')}
        a.menu_to('LOW ALARM SET')
        shots['menu'] = a.h.panel.save_png('lcd', OUT / 'menu.png')
        a.press('key_menu')
        for _ in range(50):
            a.press('key_down')
        a.press('key_set')
        self.assertEqual(a.lcd()[1], 'INVALID')
        shots['error'] = a.h.panel.save_png('lcd', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())

    def test_spec_and_contract(self):
        meta = rt.export_esco_efa5uduvw_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, set(rt.CAPS)), [])
        self.assertTrue(SPEC['approximate'])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.switch', 'panel.capture', 'panel.frame'})
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in rt.PANEL_CAPS:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'preconditions', 'errors'):
                self.assertEqual(c['capabilities'][k][key], meta['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['approximate']), ('panel_ui', 'parameter_cycle', True))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((rt.ASSET_DIR / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('esco_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 1:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
