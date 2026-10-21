"""Front-panel tests for CHEM-033 syrris_asiafllex (panel_ui.json + _panel_ui.py on scripts/asset_runtime/syrris_asiafllex.py):
python3 -m pytest tests/test_panel_ui_syrris_asiafllex.py -q

Gestures through the session API (panel.turn / panel.press / panel.switch; panel.frame for the evaluator).  Themes: the six key procedures of the
menu survey with the knob and the switch only, checked on the runtime state, the LED display and the indicator LEDs; panel = direct commands;
limits (gas supply, CMP table) and the 3 s revert; render determinism; contract entries."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
from asset_runtime import syrris_asiafllex as rt  # noqa: E402
from test_asset_syrris_asiafllex import configured  # noqa: E402

OUT = ROOT / 'outputs/panel_ui/syrris_asiafllex'
CONTRACT = ROOT / 'assets/expansion_batch03/t2_vacuum/syrris_asiafllex/asset.contract.json'
SURVEY_TASKS = ['set the main pressure', 'set the cross membrane pressure', 'switch the pressure off', 'find the optimal CMP for a solvent pair',
                'display the firmware version', 'power up']
DONE_TASKS = set()
RENDERS = {}


class Rig:
    def __init__(self, sample=True):
        if sample:
            self.h = configured()
        else:
            self.h = rt.SyrrisAsiaFllexRuntime()
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

    def turn(self, n):
        return self.call(self.A, 'panel.turn', input_id='knob', detents=n)['accepted']

    def push(self, duration_s=0.1):
        return self.call(self.A, 'panel.press', input_id='knob', duration_s=duration_s)['accepted']

    def switch(self, pos, held=()):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos, held=list(held))['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def show(self):
        f = self.frame()
        return f['screen'], f['displays']['display']['fields']['main']['text'], sorted(k for k in ('set_led', 'actual_led', 'cmp_led', 'main_led') if f['displays'][k]['on'])

    def to_main_set(self):
        self.turn(1)
        if self.show()[2] != ['main_led', 'set_led']:
            self.turn(1)
        assert self.show()[2] == ['main_led', 'set_led'], self.show()

    def capture(self, name):
        from PIL import Image
        imgs = [self.h.panel.render_image(d) for d in ('set_led', 'actual_led', 'display', 'cmp_led', 'main_led')]
        canvas = Image.new('RGB', (sum(i.size[0] for i in imgs) + 40, max(i.size[1] for i in imgs)), (230, 120, 40))
        x = 0
        for i in imgs:
            canvas.paste(i, (x, (canvas.size[1] - i.size[1]) // 2))
            x += i.size[0] + 10
        OUT.mkdir(parents=True, exist_ok=True)
        canvas.save(OUT / f'{name}.png')
        RENDERS[name] = dict(file=f'{name}.png', screen=self.frame()['screen'], time_s=self.h.time_s)


class KeyProcedures(unittest.TestCase):
    def test_power_up_firmware_and_main_pressure(self):
        g = Rig(sample=False)
        self.assertEqual(g.show()[0], 'off')
        g.switch('on')
        self.assertEqual(g.show(), ('actual', '0', ['actual_led', 'cmp_led']))     # p.23: actual pressure at switch-on
        DONE_TASKS.add(SURVEY_TASKS[5])
        g.push(duration_s=3.5)                                                   # p.35: knob kept pushed for several seconds
        self.assertEqual(g.show()[:2], ('firmware', '01.01'))
        DONE_TASKS.add(SURVEY_TASKS[4])
        g.h.step(3.5)
        self.assertEqual(g.show()[0], 'actual')
        g.to_main_set()
        g.push()
        g.turn(30)                                                               # from 0.0 (off): 1.0 bar, then 0.1 bar per click
        g.capture('menu')
        g.push()
        self.assertAlmostEqual(g.val('pressure.bpr_target_pa'), 3.9e5)
        g.h.step(4.0)
        self.assertEqual(g.show()[0], 'actual')
        g.call(g.A, 'panel.select', mode='bpr')
        g.h.step(30.0)
        self.assertEqual(g.show(), ('actual', '3.9', ['actual_led', 'main_led']))
        g.capture('home')
        DONE_TASKS.add(SURVEY_TASKS[0])

    def test_cmp_optimisation_and_pressure_off(self):
        g = Rig()
        g.to_main_set()
        g.turn(1)
        self.assertEqual(g.show()[2], ['cmp_led', 'set_led'])
        g.push()
        g.turn(15)                                                               # stops at 220 mbar: the CMP range at 2 bar (p.11)
        g.push()
        self.assertEqual(g.val('pressure.cmp_target_pa'), 220e2)
        DONE_TASKS.add(SURVEY_TASKS[1])
        g.call(g.A, 'acquisition.start')
        g.h.step(20.0)
        g.turn(1)
        g.push()
        g.turn(-5)                                                               # adjust while running (p.25 step 2)
        g.push()
        self.assertEqual(g.val('pressure.cmp_target_pa'), 170e2)
        DONE_TASKS.add(SURVEY_TASKS[3])
        g.call(g.A, 'acquisition.stop')
        g.to_main_set()
        g.push()
        g.turn(-40)
        self.assertEqual(g.show()[1], '0.0')
        g.push()                                                                 # 0.0 bar: pressure off, venting
        g.h.step(40.0)
        self.assertLess(g.val('pressure.bpr_gauge_pa'), 1000.0)
        self.assertEqual(g.val('pressure.bpr_target_pa'), 0.0)
        DONE_TASKS.add(SURVEY_TASKS[2])


class LimitsAndEquivalence(unittest.TestCase):
    def test_limits_and_revert(self):
        g = Rig()
        g.to_main_set()
        g.push()
        g.turn(50)
        self.assertEqual(g.show()[1], '5.0')                                    # gas supply 6 bar minus 1 bar margin
        g.h.step(3.5)                                                            # not validated: dropped after 3 s
        self.assertEqual(g.show()[0], 'actual')
        self.assertEqual(g.val('pressure.bpr_target_pa'), 2e5)
        g.to_main_set()
        g.push()
        g.turn(-10)                                                              # 1.0 bar: CMP range 0-120 mbar
        g.push()
        g.turn(1)
        g.push()
        g.turn(50)
        self.assertEqual(g.show()[1], '120')
        g.push()
        g.capture('error')
        self.assertEqual(g.val('pressure.cmp_target_pa'), 120e2)
        self.assertEqual(g.call(g.A, 'pressure.setpoint', expect='rejected', setpoint='cmp', value_pa=200e2)['error']['code'], 'INVALID_ARGUMENT')

    def test_panel_equals_direct(self):
        g, d = Rig(), Rig()
        g.to_main_set()
        g.push()
        g.turn(10)
        g.push()
        d.call(d.A, 'pressure.setpoint', setpoint='main', value_pa=3e5)
        g.h.step(10.)
        d.h.step(10.)
        a, b = g.h.read('evaluator'), d.h.read('evaluator')
        self.assertEqual({k: v for k, v in a.items() if k != 'panel.mode' and k != 'panel.value'}, {k: v for k, v in b.items() if k != 'panel.mode' and k != 'panel.value'})

    def test_render_and_contract(self):
        g = Rig()
        a = g.h.panel.render('display')
        self.assertTrue((a == g.h.panel.render('display')).all())
        self.assertTrue(Path(g.call(g.A, 'panel.capture', display_id='display')['image']['path']).is_file())
        g.switch('off')
        self.assertFalse(g.turn(1))
        c = json.loads(CONTRACT.read_text())
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for k in list(rt.PANEL_CAPS) + ['pressure.setpoint']:
            self.assertIn(k, c['capabilities'])


class CallerRules(unittest.TestCase):
    """A gesture made through a session acts as that session's caller: another agent's switch or knob may not switch off or change a run
    that belongs to someone else (the direct commands are refused the same way); the owner still adjusts, polls and cancels it."""

    @staticmethod
    def to_cmp_set(g):
        g.to_main_set()
        g.turn(1)
        assert g.show()[2] == ['cmp_led', 'set_led'], g.show()

    def test_foreign_switch_and_knob_refused_owner_keeps_control(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        op = g.call(g.A, 'acquisition.start')['operation_id']
        g.h.step(20.0)
        state = lambda: {k: v for k, v in g.h.read('evaluator').items() if not k.startswith('panel.')}  # noqa: E731
        before = state()
        self.to_cmp_set(g)                                                       # B pushes the CMP set point selected on the shared panel
        g.call(B, 'panel.press', input_id='knob', duration_s=0.1)
        g.call(B, 'panel.turn', input_id='knob', detents=-5)
        self.assertFalse(g.call(B, 'panel.press', input_id='knob', duration_s=0.1)['accepted'])
        for cap, args in (('acquisition.stop', {}), ('pressure.setpoint', dict(setpoint='cmp', value_pa=100e2))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(state(), before)
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        g.h.step(4.0)
        self.to_cmp_set(g)
        g.push()
        g.turn(-5)
        g.push()                                                                 # the owner adjusts its own run from the panel
        self.assertEqual(g.val('pressure.cmp_target_pa'), before['pressure.cmp_target_pa'] - 50e2)
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'cancelled')

    def test_rear_switch_is_physical(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        op = g.call(g.A, 'acquisition.start')['operation_id']
        g.h.step(20.0)
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off', held=[])['accepted'])   # never refused
        self.assertEqual((g.val('power.enabled'), g.A.poll(op)['status']), (False, 'cancelled'))   # as a power loss

    def test_refusal_is_shown_on_the_display(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.call(g.A, 'acquisition.start')
        g.h.step(20.0)
        target = g.val('pressure.cmp_target_pa')
        CallerRules.to_cmp_set(g)
        g.call(B, 'panel.press', input_id='knob', duration_s=0.1)
        g.call(B, 'panel.turn', input_id='knob', detents=-5)
        self.assertFalse(g.call(B, 'panel.press', input_id='knob', duration_s=0.1)['accepted'])   # B validates a set point on A's run
        self.assertEqual((g.show()[1], g.val('pressure.cmp_target_pa'), g.val('cycle.phase')), ('dEnY', target, 'running'))   # assumed code, 2.5 s
        g.h.step(2.6)
        self.assertNotEqual(g.show()[1], 'dEnY')


class Renders(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        old = json.loads((OUT / 'renders.json').read_text()) if (OUT / 'renders.json').exists() else {}
        old.update(RENDERS)
        (OUT / 'renders.json').write_text(json.dumps(old, indent=1) + '\n')

    def test_led_pixels(self):
        g = Rig()
        img = g.h.panel.render('actual_led')
        self.assertTrue(((img == [70, 150, 255]).all(axis=2)).any())


if __name__ == '__main__':
    unittest.main()
