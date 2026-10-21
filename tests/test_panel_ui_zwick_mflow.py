"""Front-panel tests for CHEM-146 zwick_mflow (approximate panel_ui.json: the product information describes the touch operating unit, not its
screens; _panel_ui.py on scripts/asset_runtime/zwick_mflow.py):  python3 -m pytest tests/test_panel_ui_zwick_mflow.py -q

Gestures through the session API (panel.touch / panel.press / panel.switch; panel.frame for the evaluator).  Themes: the stand-alone test of the
survey with touches and the START / STOP / CUT buttons only (setup with the user density, a parameter set saved and loaded, the live MVR and its
graph, the stored results and their USB export - the stick is inserted with usb.set, a hand at the instrument); the three other survey procedures
need options or software this instrument does not have (cleaning function, preload rotary control, testXpert III): checked as absent (up / down
buttons without function, unsupported capabilities, spec unsupported list).  Panel = direct commands, refusals as messages, power off, status
light, render determinism and the contract entries.  Filling, compacting, the piston and the weights stay host actions."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_asset_zwick_mflow as T  # noqa: E402

rt = T.rt
OUT = ROOT / 'outputs/panel_ui/zwick_mflow'
CONTRACT = ROOT / 'assets/expansion_batch04/t1_thermal/zwick_mflow/asset.contract.json'
SURVEY_TASKS = ['run a test stand-alone', 'clean the barrel with the cleaning option', 'set additional preload during preheat (option)', 'run with testXpert III']
DONE_TASKS = set()
RENDERS = {}


class Rig:
    def __init__(self, stage='charged'):
        self.h = T.charged() if stage == 'charged' else T.heated()
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

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays']['touch']

    def widget(self, wid):
        return next(w for w in self.disp()['widgets'] if w['id'] == wid)

    def text(self, wid):
        return self.widget(wid)['text']

    def message(self):
        return (self.frame()['ui'].get('message') or {}).get('text')

    def touch_at(self, rect):
        x, y, w, h = rect
        return self.call(self.A, 'panel.touch', display_id='touch', x=x + w // 2, y=y + h // 2)['accepted']

    def tap(self, wid):
        return self.touch_at(self.widget(wid)['rect'])

    def tap_row(self, form_wid, row):
        w = self.widget(form_wid)
        i = w['row_ids'].index(row) - w.get('scroll', 0)
        x, y, ww, _ = w['rect']
        return self.touch_at([x, y + i * w['row_h'], ww, w['row_h']])

    def key(self, label):
        return self.touch_at(next(b for b in self.disp()['overlay']['buttons'] if b['label'] == label)['rect'])

    def type(self, text):
        for c in text:
            self.key('Space' if c == ' ' else c)
        return self.key('OK')

    def press(self, inp):
        return self.call(self.A, 'panel.press', input_id=inp)['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def capture(self, name):
        from PIL import Image
        scr = self.h.panel.render_image('touch')
        led = self.h.panel.render_image('status_led')
        canvas = Image.new('RGB', (scr.size[0] + led.size[0] + 24, scr.size[1]), (40, 44, 50))
        canvas.paste(scr, (0, 0))
        canvas.paste(led, (scr.size[0] + 12, 12))
        OUT.mkdir(parents=True, exist_ok=True)
        canvas.save(OUT / f'{name}.png')
        RENDERS[name] = dict(file=f'{name}.png', screen=self.frame()['screen'], page=self.disp()['page'], time_s=self.h.time_s)


class KeyProcedures(unittest.TestCase):
    def test_stand_alone_test(self):
        g = Rig()
        self.assertEqual(g.text('status'), 'Ready: press START')
        self.assertEqual(g.frame()['displays']['status_led']['color'], 'green')
        g.tap('to_setup')                                                        # guided setup: the user density (pi p.3)
        self.assertEqual(g.disp()['page'], 'setup')
        g.tap_row('w_setup', 'density')
        g.type('1.11')
        g.capture('menu')
        g.tap('setup_save')
        self.assertAlmostEqual(g.val('method.density_kg_m3'), 1110.0)
        self.assertEqual(g.disp()['page'], 'test')
        g.tap('to_params')                                                       # store the setup as a parameter set (pi p.3)
        g.tap_row('w_params', 'name')
        g.type('pbt melt')
        g.tap('params_save')
        self.assertEqual(g.val('parameters.sets'), ['pbt melt'])
        self.assertEqual(g.message(), 'Parameter set saved')
        g.call(g.A, 'method.set_density', density_kg_m3=None)
        x, y, w, _ = g.widget('param_list')['rect']
        g.touch_at([x, y, w, 36])                                                 # tap the set: loaded again
        self.assertAlmostEqual(g.val('method.density_kg_m3'), 1110.0)
        g.tap('params_back')
        self.assertTrue(g.press('start'))
        g.h.step(60.0)
        self.assertEqual(g.text('status'), 'Test running')
        self.assertTrue(g.text('v_live').endswith(' cm3/10min'))
        plot = g.widget('mvr_plot')
        self.assertGreater(len(plot['series'][0]['points']), 100)                # the live MVR graph (pi p.2)
        self.assertTrue(g.frame()['displays']['status_led']['blink'])
        g.capture('home')
        for _ in range(300):
            if g.val('acquisition.phase') != 'running':
                break
            g.h.step(2.0)
        g.h.step(30.0)                                                            # automatic weighings settle
        mvr = g.val('measurement.mvr_m3_s')
        self.assertIsNotNone(mvr)
        g.tap('to_results')
        self.assertEqual(g.text('v_last_mvr'), f'{mvr * 6e8:.2f} cm3/10min')
        self.assertEqual(g.text('v_last_mfr_d'), f'{mvr * g.h.density * 6e5:.2f} g/10min')
        self.assertEqual(len(g.val('results.list')), 1)
        self.assertFalse(g.tap('export'))                                         # no stick yet
        self.assertEqual(g.message(), 'Insert a USB stick')
        g.capture('error')
        g.call(g.A, 'usb.set', inserted=True)
        self.assertTrue(g.tap('export'))
        self.assertEqual(g.val('usb')['files'], ['mflow_results/test_001.csv'])
        DONE_TASKS.add(SURVEY_TASKS[0])

    def test_options_and_pc_not_fitted(self):
        g = Rig()
        for k in ('up', 'down'):                                                 # lift options not fitted: no function
            self.assertFalse(g.press(k))
            self.assertIn('not fitted', g.frame()['ui']['unsupported'])
        c = json.loads(CONTRACT.read_text())
        for cap in ('cleaning.pneumatic_function', 'preload.function', 'hardware.connect'):
            self.assertEqual(c['capabilities'][cap]['availability'], 'unsupported')
        ids = {u['id'] for u in rt.PANEL_SPEC['unsupported']}
        self.assertLessEqual({'cleaning_function', 'preload', 'testxpert'}, ids)
        DONE_TASKS.update(SURVEY_TASKS[1:])


class EquivalenceAndRefusals(unittest.TestCase):
    def test_panel_equals_direct(self):
        g, d = Rig(), Rig()
        g.press('start')
        d.call(d.A, 'acquisition.start')
        for x in (g, d):
            x.h.step(21.0)                                                       # between two automatic cuts
        g.press('cycle')
        d.call(d.A, 'cutter.cut')
        for x in (g, d):
            x.h.step(10.0)
        g.press('stop')
        d.call(d.A, 'acquisition.stop')
        self.assertEqual(g.h.read('evaluator'), d.h.read('evaluator'))

    def test_refusals_power_and_render(self):
        g = Rig('heated')
        self.assertEqual(g.text('status'), 'Fill the barrel')
        self.assertFalse(g.press('start'))
        self.assertEqual(g.message(), 'Fill the barrel')
        self.assertEqual(g.frame()['displays']['status_led']['color'], 'amber')
        self.assertFalse(g.press('stop'))
        self.assertEqual(g.message(), 'No test running')
        g.switch('off')
        self.assertEqual(g.frame()['screen'], 'off')
        self.assertFalse(g.press('start'))
        self.assertFalse(g.frame()['displays']['status_led']['on'])
        g.switch('on')
        a = g.h.panel.render('touch')
        self.assertTrue((a == g.h.panel.render('touch')).all())
        self.assertTrue(Path(g.call(g.A, 'panel.capture', display_id='touch')['image']['path']).is_file())
        self.assertEqual(g.call(g.A, 'parameters.load', expect='rejected', name='none')['error']['code'], 'UNKNOWN_PARAMETER_SET')
        c = json.loads(CONTRACT.read_text())
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for k in list(rt.PANEL_CAPS) + rt.FRONT_CAPS:
            self.assertIn(k, c['capabilities'])


class CallerRules(unittest.TestCase):
    """A gesture made through a session acts as that session's caller: a test started with the START key belongs to the agent who pressed it
    (poll, result, cancel), and another agent may not stop, cut, re-temper or switch it off, with the panel or directly.  A test started through an
    evaluator / debug session (e.g. a Matterix key press) stays visible to every operator."""

    def test_panel_test_belongs_to_the_caller_and_is_guarded(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertTrue(g.press('start'))
        op = g.h.operation_id
        self.assertEqual(g.h._owners.get(op), 'agent')
        g.h.step(5.0)
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        before = g.h.read('evaluator')
        for inp in ('stop', 'cycle'):
            self.assertFalse(g.call(B, 'panel.press', input_id=inp)['accepted'], inp)
        for cap, args in (('acquisition.stop', {}), ('cutter.cut', {}), ('acquisition.result', {}),
                          ('thermal.set_temperature', dict(target_k=g.val('thermal.target_k')))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(g.h.read('evaluator'), before)
        self.assertEqual(g.call(g.A, 'acquisition.result')['operation_id'], op)
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'succeeded')
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')

    def test_power_switch_is_physical(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertTrue(g.press('start'))
        op = g.h.operation_id
        g.h.step(5.0)
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])   # never refused
        self.assertFalse(g.val('power.enabled'))
        self.assertNotEqual(g.A.poll(op)['status'], 'running')                  # the test ends as on a power loss

    def test_refusal_is_shown_in_the_title_bar(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertTrue(g.press('start'))
        g.h.step(5.0)
        self.assertFalse(any(w['id'] == 'toast' for w in g.disp()['widgets']))
        self.assertFalse(g.call(B, 'panel.press', input_id='stop')['accepted'])
        toast = g.widget('toast')                                                # drawn on the screen (assumed text, 3 s)
        self.assertEqual((toast['text'], g.val('acquisition.phase')), ('Not permitted: test of another user', 'running'))
        g.h.step(3.1)
        self.assertFalse(any(w['id'] == 'toast' for w in g.disp()['widgets']))

    def test_segments_of_another_agents_test_can_be_weighed(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertTrue(g.press('start'))
        for _ in range(120):
            g.h.step(5.0)
            if g.h.segments:
                break
        seg = g.h.segments[0]['segment_id']
        self.assertTrue(seg.startswith(g.h.operation_id + ':segment:'))
        self.assertTrue(g.call(B, 'balance.weigh', segment_id=seg)['requested'])      # placing a segment on the balance is physical
        self.assertEqual(g.call(B, 'balance.weigh', expect='rejected', segment_id=seg)['error']['code'], 'SEGMENT_ALREADY_WEIGHED_OR_QUEUED')   # own preconditions

    def test_evaluator_started_test_is_protected(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertTrue(g.call(g.E, 'panel.press', input_id='start')['accepted'])
        g.h.step(5.0)
        self.assertEqual(g.call(B, 'acquisition.result', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertFalse(g.call(B, 'panel.press', input_id='stop')['accepted'])
        self.assertEqual(g.val('acquisition.phase'), 'running')
        self.assertTrue(g.call(g.E, 'panel.press', input_id='stop')['accepted'])   # the evaluator is not restricted


class Renders(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        old = json.loads((OUT / 'renders.json').read_text()) if (OUT / 'renders.json').exists() else {}
        old.update(RENDERS)
        (OUT / 'renders.json').write_text(json.dumps(old, indent=1) + '\n')

    def test_status_light_follows_the_state(self):
        g = Rig()
        self.assertEqual(g.frame()['displays']['status_led']['color'], 'green')
        g.call(g.E, 'weight.configure', total_load_kg=5.0)                       # the weight no longer matches the setup
        self.assertEqual((g.text('status'), g.frame()['displays']['status_led']['color']), ('Place the test weight', 'amber'))


if __name__ == '__main__':
    unittest.main()
