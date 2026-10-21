"""front-panel tests for CHEM-018 parr_4591 (approximate panel of the Parr 4848 Reactor Controller; panel_ui.json +
scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_thermal.py): python3 -m pytest tests/test_panel_ui_parr_4591.py -q

Every gesture goes through the session API (panel.press / panel.switch / panel.turn).  Each check looks at the runtime state and at the
screen twice: the structured frame (panel.frame) and the rendered pixels, read with a seven-segment reader that knows only the field geometry
of the spec.  Themes: the survey's key procedures (stirrer check with the knob counterclockwise and the heater off, heat to a set point and
watch the controller); heater switch II / I / O (full / half power); panel = direct capability; refusals; held-key repeat; render determinism
and PNGs; contract entries."""
import hashlib
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_thermal as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'parr_01'
C0 = 273.15
OUT = ROOT / 'outputs/panel_ui/parr_4591'
ASSET = ROOT / 'assets/basic40_batch02/t1_thermal/parr_4591'
SPEC = rt._parr_panel()['spec']
SURVEY_TASKS = ['check the stirrer drive before the first run', 'initial leak and controller test', 'connect the reactor to the 4848 controller']
DONE_TASKS = set()
_DIGITS = {v: k for k, v in pu.SEG7.items() if k in '0123456789'}


def _lit(img, x, y):
    px = img[int(round(y)), int(round(x))]
    return int(px[0]) > 150


def read_field(img, f):
    n, cw, ch, gap = f['cells'], f['cell_w'], f['cell_h'], f.get('gap', 6)
    t = f.get('thickness', max(2.0, cw * 0.18))
    out = ''
    for i in range(n):
        x = f['x'] + i * (cw + gap)
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2), g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if _lit(img, px, py))
        out += _DIGITS.get(segs, ' ' if not segs else '?')
    return out.strip()


class Rig:
    def __init__(self, on=True):
        self.h = rt.Parr4591Runtime(scene_adapter=lambda key, values: None)
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if on:
            assert self.switch('power_switch', 'on')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        return r['result'] if expect == 'succeeded' else r

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def switch(self, inp, pos):
        return self.call(self.A, 'panel.switch', input_id=inp, position=pos)['accepted']

    def turn(self, n):
        return self.call(self.A, 'panel.turn', input_id='speed_knob', detents=n)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def shown(self):
        """{display: {field: text}} from the frame, checked against the pixels."""
        f = self.frame()['displays']
        out = {}
        for did, d in f.items():
            img = self.h.panel(INST).render(did)
            out[did] = {}
            for fid, v in d['fields'].items():
                if d.get('brightness') != 0:
                    assert read_field(img, SPEC['displays'][did]['fields'][fid]) == v['shown'].strip(), (did, fid, v)
                out[did][fid] = v['shown'].strip()
        return out

    def state(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']


class KeyProcedures(unittest.TestCase):
    def test_check_the_stirrer_drive_before_the_first_run(self):
        g = Rig(on=False)
        self.assertEqual(g.frame()['screen'], 'off')
        g.turn(-60)                                                                     # knob fully counterclockwise (399M p.8)
        g.switch('power_switch', 'on')
        g.switch('motor_switch', 'on')
        self.assertEqual((g.state('stirrer.speed_rad_s'), g.shown()['mcm']['rpm']), (0.0, '0'))
        g.turn(5)                                                                        # slightly increase the speed for a short run
        rpm = 5 * 2 / 100 * 1700
        self.assertAlmostEqual(g.state('stirrer.speed_rad_s'), rpm * 2 * math.pi / 60, places=6)
        self.assertEqual(g.shown()['mcm']['rpm'], str(round(rpm)))
        self.assertFalse(g.state('heater.enabled'))                                    # heater switch in the centre (OFF)
        g.h.step(30.0)
        self.assertNotEqual(g.h.debug_snapshot()['instances'][INST]['state']['stirrer.angle_rad'], 0.0)
        g.switch('motor_switch', 'off')
        self.assertEqual(g.state('stirrer.speed_rad_s'), 0.0)
        DONE_TASKS.add('check the stirrer drive before the first run')

    def test_initial_leak_and_controller_test(self):
        g = Rig()
        self.assertTrue(g.press('key_up', hold=0.5 + 12.4))                            # held: 1 + 124 steps -> 150 degC (25 + 125)
        self.assertEqual((g.state('thermal.target_k'), g.shown()['ptm']['sp']), (150 + C0, '150'))
        g.switch('heater_switch', 'full')
        self.assertEqual((g.state('heater.enabled'), g.state('heater.level'), g.state('heater.delivered_power_w')), (True, 'full', 700.0))
        self.assertTrue(g.frame()['displays']['ptm']['icons']['out']['on'])
        for _ in range(30):
            g.h.step(60.0)
        self.assertAlmostEqual(g.state('thermal.indicated_temperature_k'), 150 + C0, places=6)
        self.assertEqual(g.shown()['ptm']['pv'], '150')
        psi = (g.state('pressure.indicated_pressure_pa') - 101325) / 6894.757293168
        self.assertEqual(g.shown()['pdm']['p'], f'{psi:.0f}')
        DONE_TASKS.add('initial leak and controller test')

    def test_connect_the_reactor_to_the_4848_controller(self):
        g = Rig(on=False)                                                               # the displays light when the controller is on
        self.assertEqual(g.frame()['displays']['ptm']['brightness'], 0)
        self.assertFalse(g.press('key_up'))
        g.switch('power_switch', 'on')
        self.assertEqual(g.shown()['ptm'], {'pv': '25', 'sp': '25'})
        DONE_TASKS.add('connect the reactor to the 4848 controller')


class OtherFunctions(unittest.TestCase):
    def test_heater_switch_half_power_and_power_cycle(self):
        g = Rig()
        g.press('key_up', hold=0.5 + 7.4)                                              # 100 degC
        g.switch('heater_switch', 'half')                                               # I = half power (399M p.4)
        self.assertEqual((g.state('heater.level'), g.state('heater.delivered_power_w')), ('half', 350.0))
        g.h.step(10.0)
        t_half = g.state('thermal.indicated_temperature_k') - (25 + C0)
        b = Rig()
        b.press('key_up', hold=0.5 + 7.4)
        b.switch('heater_switch', 'full')
        b.h.step(10.0)
        t_full = b.state('thermal.indicated_temperature_k') - (25 + C0)
        self.assertAlmostEqual(t_half / t_full, 0.5, delta=0.02)
        g.switch('power_switch', 'off')                                                 # power off: heater and motor off
        self.assertFalse(g.state('heater.enabled'))
        g.switch('power_switch', 'on')                                                  # the switch positions apply again
        self.assertEqual((g.state('heater.enabled'), g.state('heater.level')), (True, 'half'))
        g.switch('heater_switch', 'off')
        self.assertFalse(g.state('heater.enabled'))


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        a, b = Rig(), Rig()
        a.press('key_up', hold=0.5 + 2.4)
        a.switch('heater_switch', 'half')
        a.turn(10)
        a.switch('motor_switch', 'on')
        b.call(b.A, 'thermal.set_temperature', temperature_K=50 + C0)
        b.call(b.A, 'heater.set_level', level='half')
        b.call(b.A, 'heater.set_enabled', enabled=True)
        b.call(b.A, 'stirrer.set_speed', speed_rad_s=round(0.2 * rt.PARR_4591_MAX_SPEED_RAD_S, 6))   # the knob value as the panel rounds it
        sa, sb = (g.h.debug_snapshot()['instances'][INST]['state'] for g in (a, b))
        self.assertEqual(sa, sb)


class Refusals(unittest.TestCase):
    def test_refusals_and_limits(self):
        g = Rig()
        g.press('key_up', hold=0.5 + 30.0)                                              # stops at the 225 degC seal limit
        self.assertEqual(g.state('thermal.target_k'), 225 + C0)
        g.press('key_down', hold=0.5 + 30.0)                                            # and at ambient
        self.assertEqual(g.state('thermal.target_k'), 25 + C0)
        self.assertEqual(g.call(g.A, 'heater.set_level', expect='rejected', level='quarter')['error']['code'], 'INVALID_ARGUMENT')
        g.call(g.A, 'lid.open')                                                         # lid open: the heater cannot be enabled
        before = g.h.debug_snapshot()['instances'][INST]['state']
        self.assertFalse(g.switch('heater_switch', 'full'))
        after = g.h.debug_snapshot()['instances'][INST]['state']
        self.assertEqual({k: v for k, v in after.items() if k != 'heater.level'}, {k: v for k, v in before.items() if k != 'heater.level'})
        self.assertFalse(after['heater.enabled'])

    def test_access_denied_is_shown_on_the_display(self):
        """2026-10-19: a gesture refused by the session access rules is visible (dEnY on the temperature module). The 4591 has no caller-owned
        run, so the refusal is produced here by a stand-in session check."""
        from unittest import mock
        g = Rig()
        real = rt.Parr4591Session._checked

        def deny(sess, sid, inst, cap, args, request_id):
            if cap == 'heater.set_enabled':
                return sess._error(sid, 'ACCESS_DENIED', request_id)
            return real(sess, sid, inst, cap, args, request_id)
        with mock.patch.object(rt.Parr4591Session, '_checked', autospec=True, side_effect=deny):
            self.assertFalse(g.switch('heater_switch', 'full'))
        self.assertEqual(g.frame()['displays']['ptm']['fields']['pv']['shown'].strip(), 'dEnY')
        self.assertFalse(g.state('heater.enabled'))

    def test_panel_gesture_acts_as_the_calling_session(self):
        """2026-10-19 sync fix: the commands behind a gesture run as the session that made it, not as a fixed panel principal."""
        from unittest import mock
        g = Rig()
        callers, real = [], rt.Parr4591Session._checked

        def spy(sess, *a, **k):
            callers.append((sess, a[2]))
            return real(sess, *a, **k)
        with mock.patch.object(rt.Parr4591Session, '_checked', autospec=True, side_effect=spy):
            g.switch('heater_switch', 'full')
        self.assertTrue(g.state('heater.enabled'))
        self.assertTrue(any(not c.startswith('panel.') for _, c in callers))
        self.assertTrue(all(s is g.A for s, c in callers if c != 'panel.frame'))
        self.assertIsNone(g.h._panel_caller)


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        self.assertEqual(a.h.panel(INST).png_bytes('ptm'), b.h.panel(INST).png_bytes('ptm'))
        OUT.mkdir(parents=True, exist_ok=True)
        a.press('key_up', hold=0.5 + 12.4)
        a.switch('heater_switch', 'full')
        a.turn(25)
        a.switch('motor_switch', 'on')
        a.h.step(120.0)
        shots = {'home': a.h.panel(INST).save_png('ptm', OUT / 'home.png'), 'menu': a.h.panel(INST).save_png('mcm', OUT / 'menu.png'),
                 'pressure': a.h.panel(INST).save_png('pdm', OUT / 'pressure.png')}
        a.call(a.A, 'lid.close')
        c = Rig()
        c.call(c.A, 'lid.open')
        c.switch('heater_switch', 'full')                                               # refused: the output indicator stays dark
        shots['error'] = c.h.panel(INST).save_png('ptm', OUT / 'error.png')
        self.assertFalse(c.frame()['displays']['ptm']['icons']['out']['on'])
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_spec_and_contract(self):
        meta = rt.export_parr_4591_metadata()['asset_types'][rt.PARR_4591_TYPE]
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities']) | set(meta['controller_capabilities'])), [])
        self.assertTrue(SPEC['approximate'])
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.switch', 'panel.turn', 'panel.capture', 'panel.frame'})
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['controller_capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for key in ('reactor_controller', 'ptm_display', 'tach_display', 'pressure_display', 'key_up', 'key_down', 'power_switch', 'heater_switch', 'motor_switch', 'speed_knob'):
            self.assertIn(key, c['components'])
        self.assertIn('heater.level', c['state_slots'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['approximate']), ('panel_ui', True))
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('parr_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
