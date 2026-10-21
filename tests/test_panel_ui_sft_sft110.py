"""Front-panel tests for CHEM-032 sft_sft110 (approximate panel_ui.json: data sheet only; _panel_ui.py on scripts/asset_runtime/sft_sft110.py):
python3 -m pytest tests/test_panel_ui_sft_sft110.py -q

Gestures through the session API (panel.press / panel.switch; panel.frame for the evaluator).  Themes: the static-extraction key procedure of the
survey with keys only (pressure set point, pumping to it and holding); oven and restrictor set points; heater switches; panel = direct commands;
refusals (no method, pump envelope, isotherm domain with a sample loaded); set-point time-out; HI alarm; render determinism; contract entries.
The survey's flow-meter procedure uses the separate gas flow meter, which is not part of this panel."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
from asset_runtime import sft_sft110 as rt  # noqa: E402
from test_asset_sft_sft110 import configured  # noqa: E402

OUT = ROOT / 'outputs/panel_ui/sft_sft110'
CONTRACT = ROOT / 'assets/expansion_batch03/t2_vacuum/sft_sft110/asset.contract.json'
PSI = 6894.757293168
SURVEY_TASKS = ['pressurise vessel to a set pressure and hold (static extraction)', 'set the oven and restrictor temperatures', 'switch the heaters']
DONE_TASKS = set()
RENDERS = {}


class Rig:
    def __init__(self, sample=True, **method):
        if sample:
            self.h = configured(method=method or None)
        else:
            self.h = rt.SftSft110Runtime()
            self.h.invoke('plant.configure', **rt.EXAMPLE)
        self.h.panel_capture_dir = str(OUT / 'captures')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(instance_id=self.h.instance_id, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def val(self, sid):
        return self.E.read(instance_id=self.h.instance_id, state_ids=[sid])['observations'][sid]['value']

    def press(self, key, duration_s=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=duration_s)['accepted']

    def switch(self, sw, pos):
        return self.call(self.A, 'panel.switch', input_id=sw, position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def show(self):
        return {k: v['fields']['main']['shown'] for k, v in self.frame()['displays'].items() if v['kind'] == 'seven_segment'}

    def capture(self, name):
        img = self.h.panel.render_image('pressure_led')
        from PIL import Image
        others = [self.h.panel.render_image(d) for d in ('oven_led', 'restrictor_led', 'pump_power_led')]
        w = img.size[0] + sum(o.size[0] for o in others) + 30
        canvas = Image.new('RGB', (w, max(o.size[1] for o in [img] + others)), (60, 60, 60))
        x = 0
        for o in [img] + others:
            canvas.paste(o, (x, 0))
            x += o.size[0] + 10
        OUT.mkdir(parents=True, exist_ok=True)
        canvas.save(OUT / f'{name}.png')
        RENDERS[name] = dict(file=f'{name}.png', screen=self.frame()['screen'], time_s=self.h.time_s)


class KeyProcedures(unittest.TestCase):
    def test_static_extraction_at_a_set_pressure(self):
        """Set the pump pressure set point, start pumping: the vessel fills to the set point and holds (static)."""
        g = Rig(static_s=600., dynamic_s=0.)
        g.press('pump_set')
        self.assertEqual(g.frame()['screen'], 'pump_set')
        self.assertEqual(g.frame()['displays']['pressure_led']['fields']['main']['text'], str(round((20e6 - 101325.) / PSI)))
        g.press('pump_down', duration_s=3.0)                                       # held: repeats (0.5 s delay, 10 / s)
        g.press('pump_set')
        want = round((20e6 - 101325.) / PSI) - 26
        self.assertAlmostEqual(g.h.method['pressure_gauge_pa'] / PSI, want, places=6)
        g.capture('menu')
        g.press('pump_run')
        for _ in range(300):
            g.h.step(5.0)
            if g.val('cycle.stage') == 'static':
                break
        self.assertEqual(g.val('cycle.stage'), 'static')
        g.h.step(20.0)
        self.assertAlmostEqual(g.val('pressure.indicated_gauge_pa') / PSI, want, delta=2.0)
        self.assertEqual(g.show()['pressure_led'], str(round(g.val('pressure.indicated_gauge_pa') / PSI)))
        g.capture('home')
        g.press('pump_run')                                                       # STOP
        self.assertEqual(g.val('cycle.phase'), 'cancelled')
        DONE_TASKS.add(SURVEY_TASKS[0])

    def test_temperatures_and_heaters(self):
        g = Rig(sample=False)
        g.call(g.E, 'method.configure', **rt.METHOD_EXAMPLE)
        g.switch('power_switch', 'on')
        g.press('oven_set')
        for _ in range(5):
            g.press('oven_up')
        g.press('oven_set')
        g.press('restr_set')
        g.press('restr_down')
        g.press('restr_set')
        self.assertAlmostEqual(g.val('thermal.target_k'), 318.15)
        self.assertAlmostEqual(g.h.method['restrictor_temperature_k'], 352.15)
        DONE_TASKS.add(SURVEY_TASKS[1])
        g.h.step(300.0)
        self.assertEqual(g.show()['oven_led'], '45.0')
        g.switch('oven_heater', 'off')
        self.assertFalse(g.val('heater.oven_on'))
        g.h.step(600.0)
        self.assertLess(g.val('thermal.temperature_k'), 300.0)                    # relaxes to ambient
        g.switch('oven_heater', 'on')
        DONE_TASKS.add(SURVEY_TASKS[2])


class EquivalenceAndRefusals(unittest.TestCase):
    def test_panel_equals_direct(self):
        g, d = Rig(sample=False), Rig(sample=False)
        for x in (g, d):
            x.call(x.E, 'method.configure', **rt.METHOD_EXAMPLE)
        g.switch('power_switch', 'on')
        g.press('restr_set')
        g.press('restr_up')
        g.press('restr_set')
        g.switch('restrictor_heater', 'off')
        d.call(d.A, 'power.set', enabled=True)
        d.call(d.A, 'setpoint.set', setpoint='restrictor_temperature_k', value=354.15)
        d.call(d.A, 'heater.set', heater='restrictor', on=False)
        g.h.step(60.)
        d.h.step(60.)
        self.assertEqual(g.h.read('evaluator'), d.h.read('evaluator'))

    def test_refusals_and_time_out(self):
        g = Rig(sample=False)
        g.switch('power_switch', 'on')
        self.assertFalse(g.press('pump_set'))                                     # no set points yet
        g.call(g.E, 'method.configure', **rt.METHOD_EXAMPLE)
        g.press('oven_set')
        g.h.step(10.5)
        self.assertEqual(g.frame()['screen'], 'run')                              # no key for 10 s: unchanged
        h = Rig()
        h.press('oven_set')
        h.press('oven_up')
        self.assertFalse(h.press('oven_set'))                                     # outside the measured isotherm with the sample loaded
        self.assertAlmostEqual(h.h.method['temperature_k'], 313.15)
        h.switch('power_switch', 'off')
        self.assertEqual(h.frame()['screen'], 'off')
        self.assertFalse(h.press('pump_run'))

    def test_alarm_render_and_contract(self):
        g = Rig()
        g.h.fault = 'OVERPRESSURE_MODEL_GUARD'
        self.assertEqual(g.show()['pressure_led'][-2:], 'HI')
        g.capture('error')
        a = g.h.panel.render('pressure_led')
        self.assertTrue((a == g.h.panel.render('pressure_led')).all())
        cap = g.call(g.A, 'panel.capture', display_id='oven_led')
        self.assertTrue(Path(cap['image']['path']).is_file())
        c = json.loads(CONTRACT.read_text())
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for k in list(rt.PANEL_CAPS) + ['setpoint.set', 'heater.set']:
            self.assertIn(k, c['capabilities'])


class CallerRules(unittest.TestCase):
    """A key pressed through a session acts as that session's caller: the run it starts belongs to the caller, and another agent's keys
    and switches may not stop, switch off or change it (the direct commands are refused the same way)."""

    def test_panel_run_belongs_to_the_caller_and_is_guarded(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.press('pump_run')
        op = g.h.operation_id
        self.assertEqual((g.val('cycle.phase'), g.h._owners.get(op)), ('running', 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        g.h.step(5.0)
        before = g.h.read('evaluator')
        self.assertFalse(g.call(B, 'panel.press', input_id='pump_run')['accepted'])          # B's STOP: refused
        for cap, args in (('acquisition.stop', {}), ('setpoint.set', dict(setpoint='temperature_k', value=330.0))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.h.read('evaluator'), before)
        self.assertTrue(g.call(B, 'panel.switch', input_id='oven_heater', position='off')['accepted'])   # hard switches: physical
        self.assertFalse(g.val('heater.oven_on'))
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])
        self.assertEqual((g.val('power.enabled'), g.A.poll(op)['status']), (False, 'cancelled'))   # as a power loss

    def test_refusal_is_shown_on_the_operated_display(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.press('pump_run')
        g.h.step(5.0)
        normal = g.show()
        self.assertFalse(g.call(B, 'panel.press', input_id='pump_run')['accepted'])          # pump STOP: dEnY on the pressure display
        self.assertEqual(g.show()['pressure_led'], 'dEnY')
        self.assertEqual(g.show()['oven_led'], normal['oven_led'])
        g.h.step(2.6)
        self.assertNotEqual(g.show()['pressure_led'], 'dEnY')
        target = g.val('thermal.target_k')
        for k in ('oven_set', 'oven_up', 'oven_set'):                                          # oven set point: dEnY on the oven display
            g.call(B, 'panel.press', input_id=k)
        self.assertEqual((g.show()['oven_led'], g.val('thermal.target_k'), g.val('cycle.phase')), ('dEnY', target, 'running'))


class Renders(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        old = json.loads((OUT / 'renders.json').read_text()) if (OUT / 'renders.json').exists() else {}
        old.update(RENDERS)
        (OUT / 'renders.json').write_text(json.dumps(old, indent=1) + '\n')

    def test_power_led(self):
        g = Rig()
        self.assertTrue(g.frame()['displays']['pump_power_led']['on'])


if __name__ == '__main__':
    unittest.main()
