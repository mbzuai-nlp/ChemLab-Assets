"""Front-panel tests for CHEM-145 zwick_hit5p (approximate panel_ui.json: the brochure describes the touch operating unit, not its screens;
_panel_ui.py on scripts/asset_runtime/zwick_hit5p.py):  python3 -m pytest tests/test_panel_ui_zwick_hit5p.py -q

Gestures through the session API (panel.touch / panel.press / panel.switch; panel.frame for the evaluator).  Themes: the survey's procedures
with touches, the keys, the release lever and the power switch only (test setup with the specimen dimensions, friction correction, an impact
with the failure type and the series, the pendulum / fixture change shown by the pendulum coding), units, panel = direct commands, refusals as
messages, power off, render determinism and the contract entries.  Pendulum raising, catching, the shield and the specimen stay host actions."""
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_asset_zwick_hit5p as T  # noqa: E402

rt = sys.modules[T.ZwickHit5pRuntime.__module__]
OUT = ROOT / 'outputs/panel_ui/zwick_hit5p'
CONTRACT = ROOT / 'assets/expansion_batch04/t1_thermal/zwick_hit5p/asset.contract.json'
SURVEY_TASKS = ['configure a test on the instrument', 'after each impact', 'change the pendulum', 'change the impact fixture']
DONE_TASKS = set()
RENDERS = {}


class Rig:
    def __init__(self, stage='configured'):
        self.h = T.configured() if stage == 'configured' else T.loaded() if stage == 'loaded' else T.calibrated()
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

    def text(self, wid):
        return next(w for w in self.disp()['widgets'] if w['id'] == wid)['text']

    def touch_at(self, rect):
        x, y, w, h = rect
        return self.call(self.A, 'panel.touch', display_id='touch', x=x + w // 2, y=y + h // 2)['accepted']

    def tap(self, wid):
        return self.touch_at(next(w for w in self.disp()['widgets'] if w['id'] == wid)['rect'])

    def tap_row(self, form_wid, row):
        w = next(w for w in self.disp()['widgets'] if w['id'] == form_wid)
        i = w['row_ids'].index(row)
        x, y, ww, _ = w['rect']
        return self.touch_at([x, y + i * w['row_h'], ww, w['row_h']])

    def key(self, label):
        return self.touch_at(next(b for b in self.disp()['overlay']['buttons'] if b['label'] == label)['rect'])

    def type(self, text):
        for c in text:
            self.key(c)
        return self.key('OK')

    def press(self, inp):
        return self.call(self.A, 'panel.press', input_id=inp)['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def swing(self):
        """Release with the lever, let the host catch the pendulum at the first turning point."""
        ok = self.press('release')
        if ok:
            self.call(self.E, 'pendulum.catch')
            self.h.step(2.0)
        return ok

    def capture(self, name):
        img = self.h.panel.render_image('touch')
        OUT.mkdir(parents=True, exist_ok=True)
        img.save(OUT / f'{name}.png')
        RENDERS[name] = dict(file=f'{name}.png', screen=self.frame()['screen'], page=self.disp()['page'], time_s=self.h.time_s)


class KeyProcedures(unittest.TestCase):
    def test_test_setup_and_friction_correction(self):
        g = Rig()
        self.assertEqual(g.text('status'), 'Friction correction required')
        g.tap('to_setup')
        self.assertEqual(g.disp()['page'], 'setup')
        g.tap_row('w_setup', 'h')
        g.type('3.9')
        g.tap_row('w_setup', 'bn')
        g.type('7.95')
        g.capture('menu')
        g.tap('setup_save')                                                      # brochure p.5: dimensions on the touch display
        self.assertEqual(g.disp()['page'], 'results')
        self.assertAlmostEqual(g.h.method['remaining_area_prior_m2'], 3.9 * 7.95e-6)
        self.assertEqual(g.h.method['timeout_s'], T.METHOD_EXAMPLE['timeout_s'])
        g.tap('to_friction')
        self.assertEqual(g.text('v_fr_state'), 'not done')
        g.tap('fr_start')
        self.assertEqual(g.text('fr_hint'), 'Release the pendulum without a specimen')
        self.assertTrue(g.swing())                                               # the empty swing: friction correction (brochure p.14)
        self.assertTrue(g.val('calibration.valid'))
        self.assertTrue(g.text('v_fr_state').endswith(' N m'))
        g.press('home_key')
        self.assertEqual(g.text('status'), 'Raise the pendulum into the release')
        DONE_TASKS.add(SURVEY_TASKS[0])

    def test_impact_failure_type_and_series(self):
        g = Rig('loaded')
        self.assertEqual(g.text('status'), 'Ready: release the pendulum')
        self.assertTrue(g.swing())
        self.assertEqual(g.frame()['screen'], 'failure')                         # brochure p.5: the type of failure right after the test
        g.capture('error')
        g.tap('fail_C')
        self.assertEqual(g.frame()['screen'], 'gui')
        rows = g.val('series.results')
        self.assertEqual((len(rows), rows[0]['failure'], rows[0]['validity']), (1, 'C', 'valid'))
        e, s = rows[0]['energy_j'], rows[0]['strength_j_m2']
        self.assertEqual(g.text('v_energy'), f'{e:.4f} J')
        self.assertEqual(g.text('v_pct'), f'{e / 0.5 * 100:.1f}')
        self.assertEqual(g.text('v_strength'), f'{s * 1e-3:.2f} kJ/m2')
        self.assertEqual(g.text('v_validity'), 'valid')
        g.capture('home')
        g.tap('to_settings')
        g.tap_row('w_settings', 'units')
        g.touch_at(next(b for b in g.disp()['overlay']['buttons'] if b['label'] == 'Anglo-American')['rect'])
        self.assertEqual(g.val('display.units'), 'Anglo-American')
        g.press('home_key')
        self.assertEqual(g.text('v_energy'), f'{e * 0.7375621493:.4f} ft lbf')
        self.assertEqual(g.text('v_strength'), f'{s / 2101.522:.3f} ft lbf/in2')
        g.tap('new_series')
        self.assertEqual(g.val('series.results'), [])
        self.assertEqual(g.text('v_mean'), '0 valid  ---')
        DONE_TASKS.add(SURVEY_TASKS[1])

    def test_pendulum_and_fixture_change(self):
        g = Rig('calibrated')
        g.tap('to_settings')
        self.assertEqual((g.text('v_pd_std'), g.text('v_pd_energy'), g.text('v_pd_angle'), g.text('v_pd_span')),
                         ('ISO 179-1 Charpy', '0.5 J', '135.72 deg', '62 mm'))
        g.call(g.E, 'pendulum.raise')
        g.h.step(5.0)
        g.call(g.E, 'fixture.configure', **T.FIXTURE_EXAMPLE)                    # quick-release pendulum / fixture change: the coding is read again
        g.press('home_key')
        self.assertEqual(g.text('status'), 'Friction correction required')       # the friction data belong to the pendulum
        g.tap('to_friction')
        g.tap('fr_start')
        self.assertTrue(g.swing())                                               # a new empty swing for the new pendulum
        self.assertTrue(g.val('calibration.valid'))
        DONE_TASKS.add(SURVEY_TASKS[2])
        DONE_TASKS.add(SURVEY_TASKS[3])


class EquivalenceAndRefusals(unittest.TestCase):
    def test_panel_equals_direct(self):
        g, d = Rig(), Rig()
        g.tap('to_setup')
        g.tap_row('w_setup', 'h')
        g.type('4')
        g.tap('setup_save')
        g.tap('to_friction')
        g.tap('fr_start')
        g.press('release')
        d.call(d.A, 'method.configure', sample_interval_s=T.METHOD_EXAMPLE['sample_interval_s'], timeout_s=T.METHOD_EXAMPLE['timeout_s'], remaining_area_prior_m2=4.0 * 8.0 * 1e-6)
        d.call(d.A, 'calibration.start')
        for x in (g, d):
            x.call(x.E, 'pendulum.catch')
            x.h.step(2.0)
        a, b = g.h.read('evaluator'), d.h.read('evaluator')
        self.assertEqual(a, b)

    def test_refusals_power_and_render(self):
        g = Rig()
        self.assertFalse(g.press('release'))                                     # no friction correction for a test: message
        self.assertEqual(g.frame()['ui']['message']['text'], 'Place a specimen')
        g.switch('off')
        self.assertEqual(g.frame()['screen'], 'off')
        self.assertFalse(g.press('home_key'))
        self.assertFalse(g.press('release'))
        g.switch('on')
        self.assertEqual(g.disp()['page'], 'results')
        a = g.h.panel.render('touch')
        self.assertTrue((a == g.h.panel.render('touch')).all())
        self.assertTrue(Path(g.call(g.A, 'panel.capture', display_id='touch')['image']['path']).is_file())
        self.assertEqual(g.call(g.A, 'series.set_failure', expect='rejected', failure='C')['error']['code'], 'NO_RESULT')
        c = json.loads(CONTRACT.read_text())
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for k in list(rt.PANEL_CAPS) + rt.FRONT_CAPS:
            self.assertIn(k, c['capabilities'])


class CallerRules(unittest.TestCase):
    """A gesture made through a session acts as that session's caller: the swing released from the panel belongs to the caller (poll, result,
    cancel), and another agent may not switch the instrument off or stop it, through the panel or directly."""

    def test_panel_swing_belongs_to_the_caller_and_is_guarded(self):
        g = Rig('loaded')
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertTrue(g.press('release'))
        op = g.h.operation_id
        self.assertEqual((g.val('acquisition.phase'), g.h._owners.get(op)), ('running', 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        self.assertEqual(g.call(g.A, 'acquisition.result')['operation_id'], op)
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        before = g.h.read('evaluator')
        for cap, args in (('acquisition.stop', {}), ('acquisition.result', {})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(g.h.read('evaluator'), before)
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'succeeded')
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')

    def test_power_switch_is_physical(self):
        g = Rig('loaded')
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertTrue(g.press('release'))
        op = g.h.operation_id
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])   # never refused
        self.assertFalse(g.val('power.enabled'))
        self.assertNotEqual(g.A.poll(op)['status'], 'running')                  # the record ends as on a power loss

    def test_refusal_is_shown_and_failure_type_belongs_to_the_impact_owner(self):
        g = Rig('loaded')
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.swing()                                                                # A's impact; the failure page opens on the shared screen
        rect = next(w for w in g.disp()['widgets'] if w['id'] == 'fail_C')['rect']
        x, y, w, h = rect
        self.assertFalse(g.call(B, 'panel.touch', display_id='touch', x=x + w // 2, y=y + h // 2)['accepted'])
        self.assertEqual(g.call(B, 'series.set_failure', expect='rejected', failure='H')['error']['code'], 'ACCESS_DENIED')
        toast = next(w for w in g.disp()['widgets'] if w['id'] == 'toast')     # the refusal on the title bar (assumed text, 3 s)
        self.assertEqual((toast['text'], g.val('series.results')[-1]['failure']), ('Not permitted: test of another user', None))
        g.h.step(3.1)
        self.assertFalse(any(w['id'] == 'toast' for w in g.disp()['widgets']))
        self.assertTrue(g.tap('fail_C'))                                         # the impact's owner chooses it
        self.assertEqual(g.val('series.results')[-1]['failure'], 'C')


class Renders(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        old = json.loads((OUT / 'renders.json').read_text()) if (OUT / 'renders.json').exists() else {}
        old.update(RENDERS)
        (OUT / 'renders.json').write_text(json.dumps(old, indent=1) + '\n')

    def test_series_statistics(self):
        g = Rig('loaded')
        g.swing()
        g.tap('fail_later')
        self.assertIsNone(g.val('series.results')[0]['failure'])
        st = g.val('series.stats')
        self.assertEqual(st['n'], 1)
        self.assertIsNone(st['strength_sd_j_m2'])
        self.assertTrue(math.isfinite(st['strength_mean_j_m2']))


if __name__ == '__main__':
    unittest.main()
