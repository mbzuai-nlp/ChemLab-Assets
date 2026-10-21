"""Front-panel tests for CHEM-002 mt_hx204 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/mt_hx204.py):
python3 -m pytest tests/test_panel_ui_mt_hx204.py -q

Every gesture goes through the session API (panel.touch / panel.press, the runtime's normal invoke path); the host (evaluator session) only
places objects (pan, sample, test weights, temperature kit) and sets sensor errors.  Themes: the five key procedures of the menu survey; the
terminal functions of the reference manual (drying programs and switch-off criteria, display modes, start weight, control limits, free factor,
QuickPredict, workflow, test measurement and AutoMet, results and filters, shortcuts, method selection, tests and adjustments with injected
sensor errors, settings, users / groups / login / lock-out, setup wizard after reset); panel = direct command; refusals shown as the device's
message with the state unchanged; render determinism; contract entries."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import mt_hx204 as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'hx204_01'
TERM = 'terminal'
OUT = ROOT / 'outputs/panel_ui/mt_hx204'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['create a method at 105 C', 'perform a measurement', 'stop drying early', 'set switch-off criterion 5', 'internal weight adjustment']
DONE_TASKS = set()


class Rig:
    """An HX204 with an agent (panel gestures) and an evaluator (host: sample, pan, weights, kit, sensor errors)."""

    def __init__(self, on=True):
        self.h = rt.MtHx204Runtime()
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        if on:
            assert self.press('key_onoff')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def host(self, cap, **args):
        return self.call(self.E, cap, **args)

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt, until=None):
        t = 0.0
        while t < dt - 1e-9:
            d = min(30.0, dt - t)
            self.h.step(d)
            t += d
            if until and until():
                break

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][TERM]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TERM, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def press(self, key):
        return self.call(self.A, 'panel.press', input_id=key)['accepted']

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label and b['id'] != 'field':
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def ov_row(self, label):
        for b in self.overlay()['buttons']:
            if b.get('style') == 'row' and b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(label)

    def msg(self):
        return ' '.join(self.overlay()['lines'])

    def row(self, wid, first):
        """Tap the row of a list / form whose first cell is this text (scrolling down until it shows)."""
        for _ in range(30):
            w = self.widgets()[wid]
            firsts = [c[0] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, firsts)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def value(self, wid, first):
        x, y, ww, hh = self.widgets()[wid]['rect']
        for _ in range(30):
            if self.widgets()[wid]['scroll'] == 0:
                break
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
        for _ in range(30):
            w = self.widgets()[wid]
            for c in w['cells']:
                if c[0] == first:
                    return c[1]
            assert w['scroll'] + len(w['cells']) < w['count'], first
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def type(self, text):
        ov = self.overlay()
        assert ov['kind'] == 'keyboard'
        labels = {b['label'] for b in ov['buttons']}
        if 'q' not in labels and 'Q' not in labels and 'a' not in labels and '7' in labels:
            for ch in text:
                assert self.ov(ch), ch
            return
        if ov['field']:
            self.ov('Del')
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit() or ch in '$@#%&/():;_+=\'"!?,':
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if ch.isupper() != (self.overlay()['mode'] == 'upper'):
                    self.ov('Shift')
                self.ov(ch)
        assert self.overlay()['field'] in (text, '*' * len(text)), (self.overlay()['field'], text)

    def enter(self, wid, first, text):
        self.row(wid, first)
        self.type(text)
        return self.ov('OK')

    def choose(self, wid, first, option):
        self.row(wid, first)
        return self.ov(option)

    def home(self):
        return self.press('key_home')

    # ---- procedures
    def new_method(self, name, **params):
        """Method Definition > New... > Define method manually > name > OK > (parameters) > Save."""
        self.home()
        assert self.tap('t_methods')
        assert self.tap('md_new')
        assert self.ov('Define method manually')
        self.ov_row('Method name')
        self.type(name)
        self.ov('OK')
        assert self.ov('OK'), self.overlay()
        assert self.page() == ('main', 'medit')
        if params:
            self.tap('me_main')
            for label, value in params.items():
                self.set_row('mp_main_rows', label, value)
            self.tap('mpm_back')
        assert self.tap('me_save')
        return next(m['id'] for m in self.h.dev['methods'] if m['name'] == name)

    def set_row(self, wid, label, value):
        """Set a form row: options by their text, numbers / text through the keyboard."""
        self.row(wid, label)
        ov = self.overlay()
        if ov['kind'] == 'keyboard':
            self.type(str(value))
            assert self.ov('OK'), self.overlay()
        else:
            assert self.ov(value), (label, value)

    def measure(self, method_name, sample=None, pan_g=4.5, start=True):
        """Measurement > method (chamber opens) > pan > ->0/T<- > sample > Start drying."""
        self.home()
        assert self.tap('t_meas')
        if self.page()[1] != 'work':
            assert self.row('ml_list', method_name)
        assert self.page() == ('main', 'work')
        self.host('sample.remove')                                           # the previous sample is taken out with its pan
        self.host('pan.load', mass_kg=pan_g / 1000.0)
        assert self.tap('w_tare')
        self.host('sample.configure', species_kg=sample or {'water': .002}, residue_kg=.003)
        if start:
            assert self.tap('w_start'), self.overlay()

    def run_to_end(self, limit=7200):
        self.step(limit, until=lambda: self.h.phase != 'running')


class SurveyProcedures(unittest.TestCase):
    """The key procedures of the menu survey, done only with panel gestures."""

    def test_create_a_method_at_105_c(self):
        r = Rig()
        mid = r.new_method('My first method', **{'Drying temperature': '105'})
        m = rt._method(r.h.dev, mid)
        self.assertEqual((m['name'], m['version'], m['params']['program'], m['params']['temperature'], m['params']['soc']),
                         ('My first method', 1, 'Standard', 105, '3'))
        self.assertEqual(r.page(), ('main', 'methods'))
        self.assertEqual(r.widgets()['md_list']['cells'], [['My first method', 'v1']])
        DONE_TASKS.add('create a method at 105 C')

    def test_perform_a_measurement(self):
        r = Rig()
        r.new_method('Water', **{'Drying temperature': '105'})
        r.home()
        r.tap('t_meas')
        r.row('ml_list', 'Water')
        self.assertEqual(r.val('lid.position_m'), 0.1085)                         # the chamber opens on selection (p.61)
        self.assertIn('Load the accessories (Sample pan)', r.text('w_instr'))
        r.host('pan.load', mass_kg=.0045)
        self.assertTrue(r.tap('w_tare'))
        self.assertEqual((r.val('balance.tare_kg'), r.val('lid.position_m')), (.0045, .1085))
        r.host('sample.configure', species_kg={'water': .002}, residue_kg=.003)
        self.assertEqual(r.text('w_value'), '5.000 g')
        self.assertTrue(r.tap('w_start'))
        self.assertEqual((r.val('cycle.phase'), r.val('lid.position_m')), ('running', 0.0))  # the chamber closes and drying starts
        r.step(60)
        self.assertTrue(r.text('w_value').endswith(' %MC'))
        self.assertIn('w_stop', r.widgets())
        r.run_to_end()
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        res = r.val('results.list')[-1]
        self.assertAlmostEqual(res['value'], (5.0 - res['dry_g']) / 5.0 * 100, places=1)
        self.assertEqual(r.text('w_value'), res['text'])
        self.assertEqual(r.val('lid.position_m'), .1085)                           # opens after the measurement (p.32)
        self.assertTrue(r.tap('w_next'))
        self.assertEqual(r.val('work.state')['stage'], 'tare')
        DONE_TASKS.add('perform a measurement')

    def test_stop_drying_early(self):
        r = Rig()
        r.new_method('Stop me')
        r.measure('Stop me')
        r.step(90)
        self.assertTrue(r.tap('w_stop'))
        self.assertEqual(r.overlay()['title'], 'Stop drying')
        self.assertTrue(r.ov('Cancel'))                                             # Cancel: the process continues
        self.assertEqual(r.val('cycle.phase'), 'running')
        r.tap('w_stop')
        self.assertTrue(r.ov('Abort measurement and save data'))
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        res = r.val('results.list')[-1]
        self.assertTrue(res['aborted'])
        self.assertAlmostEqual(res['duration_s'], 90.0)
        DONE_TASKS.add('stop drying early')

    def test_set_switch_off_criterion_5(self):
        r = Rig()
        mid = r.new_method('Slow')
        r.row('md_list', 'Slow')
        r.tap('me_main')
        r.choose('mp_main_rows', 'Switch-off criterion', '5 (1 mg / 140 s)')
        self.assertEqual(rt._method(r.h.dev, mid)['params']['soc'], '3')            # not yet saved
        r.tap('mpm_back')
        r.tap('me_save')
        m = rt._method(r.h.dev, mid)
        self.assertEqual((m['params']['soc'], m['version']), ('5', 2))             # a new version (p.72)
        r.measure('Slow')
        r.run_to_end()
        t5 = r.h.elapsed_s
        curve = [(c['time_s'], c['mass_kg']) for c in r.h.curve]
        self.assertLess((rt._interp(curve, t5 - 140) - rt._interp(curve, t5)) * 1e6, 1.0)       # less than 1 mg in the last 140 s
        self.assertGreaterEqual((rt._interp(curve, t5 - 141) - rt._interp(curve, t5 - 1)) * 1e6, 1.0 - 1e-6)
        DONE_TASKS.add('set switch-off criterion 5')

    def test_internal_weight_adjustment(self):
        r = Rig()
        r.host('sensor.configure_error', span_rel=2e-4)
        r.host('lid.set_position', position_m=.1085)
        r.host('pan.load', mass_kg=.05)
        self.assertAlmostEqual(r.val('balance.indicated_kg'), .05001, places=9)    # span error before the adjustment
        r.host('pan.load', mass_kg=0.0)
        r.home()
        r.tap('t_test')
        r.tap('ta_adj')
        r.tap('aa_weight_adjustment_internal')
        self.assertEqual(r.page(), ('main', 'proc'))
        self.assertTrue(r.tap('pr_start'))
        self.assertEqual(r.text('pr_result'), 'passed')
        r.host('pan.load', mass_kg=.05)
        self.assertAlmostEqual(r.val('balance.indicated_kg'), .05, places=9)
        self.assertEqual(r.val('tests.history')['weight_adjustment'][-1]['kind'], 'Weight adjustment - internal')
        DONE_TASKS.add('internal weight adjustment')


class MethodParameters(unittest.TestCase):
    def test_programs_reach_their_targets(self):
        for prog, check in (('Rapid', lambda t: t > 105 + 20), ('Gentle', lambda t: t < 60), ('Standard', lambda t: 100 < t < 106)):
            r = Rig()
            r.new_method(prog, **{'Drying program': prog})
            r.measure(prog)
            r.step(150)
            self.assertTrue(check(r.h.temperature_k - 273.15), (prog, r.h.temperature_k - 273.15))

    def test_steps_program(self):
        r = Rig()
        mid = r.new_method('Steps', **{'Drying program': 'Steps'})
        r.row('md_list', 'Steps')
        r.tap('me_main')
        self.assertEqual(r.value('mp_main_rows', 'Drying steps'), '2 steps')
        r.row('mp_main_rows', 'Drying steps')
        self.assertEqual(r.widgets()['st_list']['cells'], [['Step 1', '50 °C', '5:00 min'], ['Step 2', '105 °C', '3 (1 mg / 50 s)']])
        r.row('st_list', 'Step 1')
        r.ov_row('Drying time')
        r.type('2')
        r.ov('OK')
        r.ov('OK')
        r.tap('st_add')
        self.assertEqual(r.val('method.draft')['params']['steps'][0]['soc_time_s'], 120)
        self.assertEqual(len(r.val('method.draft')['params']['steps']), 3)
        r.tap('st_remove')
        r.tap('st_back')
        r.tap('mpm_back')
        r.tap('me_save')
        r.measure('Steps')
        r.step(110)
        self.assertEqual(r.text('w_temp').split('step ')[1], '1')
        self.assertLess(abs(r.h.temperature_k - 273.15 - 50), 3)
        r.step(30)
        self.assertEqual(r.val('measurement.live')['step'], 2)                     # after 2:00 min the second step starts
        self.assertEqual(rt._method(r.h.dev, mid)['params']['steps'][0]['soc_time_s'], 120)

    def test_display_modes_free_factor_and_limits(self):
        r = Rig()
        r.new_method('Lim', **{'Display mode': '%DC'})
        r.row('md_list', 'Lim')
        r.tap('me_result')
        r.choose('mp_result_rows', 'Control limits', 'ON')
        r.set_row('mp_result_rows', 'T2+ (Upper intervention limit)', '70')
        r.set_row('mp_result_rows', 'T1+ (Upper warning limit)', '50')
        r.choose('mp_result_rows', 'Free factor', 'ON')
        r.set_row('mp_result_rows', 'Factor', '1')
        r.set_row('mp_result_rows', 'Offset', '0.5')
        r.choose('mp_result_rows', 'Format', 'x.x')
        r.tap('mpr_back')
        r.tap('me_save')
        r.measure('Lim')
        r.run_to_end()
        res = r.val('results.list')[-1]
        dc = res['dry_g'] / 5.0 * 100 + 0.5
        self.assertEqual(res['text'], f'{dc:.1f} %DC*')                         # free factor: 1 decimal and an asterisk (p.47)
        self.assertEqual(res['status'], 'warned')                                  # between T1+ and T2+ (p.46)
        self.assertEqual(r.widgets()['w_status']['text'], 'warned (!)')

    def test_start_weight_active_aid_refuses(self):
        r = Rig()
        r.new_method('Aid', **{'Start weight': 'ON'})
        r.row('md_list', 'Aid')
        r.tap('me_main')
        r.set_row('mp_main_rows', 'Target weight', '2')
        r.choose('mp_main_rows', 'Weigh-in aid', 'Active')
        r.tap('mpm_back')
        r.tap('me_save')
        r.measure('Aid', start=False)                                              # 5 g sample, target 2 g +- 10 %
        self.assertFalse(r.widgets()['w_aid']['color'] == 'ok')
        before = r.val('cycle.phase')
        self.assertFalse(r.tap('w_start'))
        self.assertEqual(r.msg(), 'The start weight is outside the tolerance.')
        self.assertEqual(r.val('cycle.phase'), before)

    def test_manual_starting_mode(self):
        r = Rig()
        r.new_method('Manual')
        r.row('md_list', 'Manual')
        r.tap('me_workflow')
        r.choose('mp_workflow_rows', 'Starting mode', 'Manual')
        r.tap('mpw_back')
        r.tap('me_save')
        r.measure('Manual')
        self.assertEqual((r.val('work.state')['stage'], r.val('cycle.phase')), ('armed', 'idle'))
        self.assertEqual(r.val('lid.position_m'), .1085)                           # start weight recorded with the chamber open (p.46)
        self.assertTrue(r.press('key_chamber'))
        self.assertEqual((r.val('cycle.phase'), r.val('lid.position_m')), ('running', 0.0))
        r.step(60)
        self.assertTrue(r.press('key_chamber'))                                    # manual mode: opening interrupts, does not stop
        self.assertEqual(r.val('cycle.phase'), 'running')

    def test_quickpredict(self):
        r = Rig()
        mid = r.new_method('QP', **{'Start weight': 'ON'})
        r.row('md_list', 'QP')
        r.tap('me_main')
        r.set_row('mp_main_rows', 'Target weight', '5')
        r.choose('mp_main_rows', 'Weigh-in aid', 'Active')
        r.tap('mpm_back')
        r.tap('me_result')
        r.row('mp_result_rows', 'QuickPredict')
        r.row('mp_qp_rows', 'QuickPredict')
        self.assertEqual(r.overlay()['title'], 'QuickPredict')                     # disclaimer first (p.58)
        r.ov('I accept the terms above')
        self.assertTrue(r.val('method.draft')['params']['qp'])
        r.tap('mpq_back')
        r.tap('mpr_back')
        r.tap('me_save')
        r.row('md_list', 'QP')
        r.tap('me_result')
        r.row('mp_result_rows', 'QuickPredict')
        self.assertTrue(r.tap('mpq_refs'))                                           # expert mode: Reference Curves (p.61)
        r.ov('OK')
        r.tap('mpq_back')
        r.tap('mpr_back')
        r.tap('me_save')
        for i in range(6):
            r.measure('QP')
            r.run_to_end()
            r.tap('w_next')
        m = rt._method(r.h.dev, mid)
        self.assertEqual((len(m['qp_refs']), m['params']['qp_usage']), (6, 'Active'))
        r.tap('w_back')
        r.home()
        r.tap('t_methods')
        r.row('md_list', 'QP')
        r.tap('me_result')
        r.row('mp_result_rows', 'QuickPredict')
        r.choose('mp_qp_rows', 'Allow stopping of measurement', 'activated')
        r.tap('mpq_back')
        r.tap('mpr_back')
        r.tap('me_save')
        r.measure('QP')
        r.step(60)
        self.assertEqual(r.text('w_pred'), 'QuickPredict: Prediction pending (1)')
        r.step(120)
        self.assertTrue(r.text('w_pred').startswith('QuickPredict: '))
        self.assertIn('±', r.text('w_pred'))
        self.assertTrue(r.tap('w_qp_stop'))                                        # Save and Stop (p.67)
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        self.assertIsNotNone(r.val('results.list')[-1]['predicted'])

    def test_qp_incompatible_settings_refused(self):
        r = Rig()
        r.new_method('NoQP')
        r.row('md_list', 'NoQP')
        r.tap('me_result')
        r.row('mp_result_rows', 'QuickPredict')
        r.row('mp_qp_rows', 'QuickPredict')
        self.assertFalse(r.ov('I accept the terms above'))
        self.assertIn('QuickPredict needs', r.msg())
        self.assertFalse(r.val('method.draft')['params']['qp'])

    def test_test_measurement_and_automet(self):
        r = Rig()
        r.new_method('Dev')
        r.row('md_list', 'Dev')
        r.tap('me_test')
        self.assertFalse(r.widgets()['mt_automet']['enabled'])
        r.tap('mt_testmeas')
        r.ov('Start')
        self.assertEqual(r.page(), ('main', 'work'))
        self.assertTrue(r.val('work.state')['test'])
        r.host('pan.load', mass_kg=.0045)
        r.tap('w_tare')
        r.host('sample.configure', species_kg={'water': .002}, residue_kg=.003)
        r.tap('w_start')
        r.run_to_end(9000)
        res = r.val('results.list')[-1]
        self.assertTrue(res['test'])
        self.assertEqual(sorted(res['soc_points']), ['1', '2', '3', '4', '5'])    # all five criteria reached (p.52)
        t = [res['soc_points'][k]['time_s'] for k in '12345']
        self.assertEqual(t, sorted(t))
        r.tap('w_back')
        self.assertTrue(r.widgets()['mt_automet']['enabled'])
        r.tap('mt_automet')
        r.ov_row('Measurement reference (display unit)')
        r.type('39.7')
        r.ov('OK')
        r.ov('OK')
        d = r.val('method.draft')['params']
        self.assertEqual((d['soc'], d['soc_free_mg']), ('Free (mg / s)', 1.0))
        self.assertTrue(20 <= d['soc_free_mg_dt'] <= 140)

    def test_preheating_manual(self):
        r = Rig()
        r.new_method('Pre')
        r.row('md_list', 'Pre')
        r.tap('me_workflow')
        r.choose('mp_workflow_rows', 'Preheating', 'Manual')
        r.set_row('mp_workflow_rows', 'Temperature', '80')
        r.tap('mpw_back')
        r.tap('me_save')
        r.home()
        r.tap('t_meas')
        r.row('ml_list', 'Pre')
        r.press('key_chamber')
        self.assertTrue(r.tap('w_preheat_btn'))
        r.step(400)
        self.assertEqual(r.text('w_preheat'), 'Preheated to 80 °C')
        r.step(3600)
        self.assertIsNone(r.val('work.state')['preheat'])                          # off after 1 h at most (p.47)


class ResultsShortcutsSelection(unittest.TestCase):
    def test_results_journal_filter_chart_delete(self):
        r = Rig()
        r.new_method('R')
        for _ in range(3):
            r.measure('R')
            r.run_to_end()
            r.tap('w_next')
        r.home()
        r.tap('t_results')
        r.row('rs_methods', 'R')
        self.assertEqual(r.text('jr_filter'), 'Filter: Last 10 measurements / method tests')
        self.assertEqual(len(r.widgets()['jr_list']['cells']), 3)
        r.tap('jr_flt')
        r.ov_row('Filter')
        r.ov('Advanced filters')
        r.ov_row('Measurement types')
        r.ov('Only method tests')
        r.ov('Close (X)')
        self.assertEqual(r.widgets()['jr_list']['cells'], [])
        r.tap('jr_flt')
        r.ov_row('Filter')
        r.ov('All')
        r.ov('Close (X)')
        r.tap('jr_chart')
        self.assertEqual(len(r.widgets()['rc_plot']['series'][0]['points']), 3)
        r.tap('rc_back')
        r.touch(r.widgets()['jr_list']['rect'][0] + 5, r.widgets()['jr_list']['rect'][1] + 10)
        self.assertEqual(r.page(), ('main', 'result_view'))
        r.tap('rv_delete')
        r.ov('Delete')
        self.assertEqual(len(r.val('results.list')), 2)
        self.assertFalse(r.tap('jr_export'))
        self.assertEqual(r.msg(), 'No storage device connected.')

    def test_zero_results_and_auto_delete(self):
        r = Rig()
        r.home()
        r.tap('t_settings')
        r.tap('s_sys')
        r.tap('sy_results')
        r.set_row('set_results_rows', 'Result limit', '0')
        r.tap('set_results_save')
        self.assertEqual(r.val('device.settings')['results.limit'], 0)
        r.new_method('Z')
        r.measure('Z')
        r.run_to_end()
        self.assertEqual(r.val('results.list'), [])                                # Zero Results (p.107)

    def test_shortcuts(self):
        r = Rig()
        r.new_method('Fast')
        r.measure('Fast', start=False)
        r.tap('w_shortcut')
        r.ov('Add a shortcut to my home for this method...')
        r.ov_row('Shortcut name')
        r.type('F1')
        r.ov('OK')
        r.ov('Save')
        r.tap('w_back')
        r.home()
        self.assertEqual(r.widgets()['h_shortcuts']['cells'], [['F1']])
        r.tap('t_meas')
        r.tap('ml_shortcuts')
        r.row('sc_list', '[x]')
        self.assertEqual(r.val('users.state')['users'][0]['shortcuts'], [])
        r.home()
        r.tap('t_meas')
        r.tap('ml_shortcuts')
        r.row('sc_list', '[ ]')
        r.home()
        r.row('h_shortcuts', 'Fast')                                               # a shortcut opens the work screen
        self.assertEqual(r.page(), ('main', 'work'))

    def test_method_selection_per_user(self):
        r = Rig()
        r.new_method('A')
        r.new_method('B')
        r.home()
        r.tap('t_meas')
        r.tap('ml_select')
        r.row('ms_users', 'Administrator')
        r.row('ms_methods', '[x]')                                                 # disable A
        self.assertEqual(r.val('users.state')['users'][0]['methods'], [2])
        r.home()
        r.tap('t_meas')
        self.assertEqual(r.widgets()['ml_list']['cells'], [['B']])


class TestsAndAdjustments(unittest.TestCase):
    def test_external_weight_test_and_adjustment(self):
        r = Rig()
        r.host('sensor.configure_error', span_rel=-3e-4)
        r.host('lid.set_position', position_m=.1085)
        r.home()
        r.tap('t_test')
        r.tap('ta_tests')
        r.tap('tt_weight_test_external')
        r.tap('pr_start')
        self.assertEqual(r.text('pr_prompt'), 'Load 100.00 g')
        r.host('pan.load', mass_kg=.1)
        r.step(3)
        self.assertEqual(r.text('pr_prompt'), 'Remove the weight  000.00')
        r.host('pan.load', mass_kg=0.0)
        r.step(1)
        self.assertEqual(r.text('pr_result'), 'failed')                            # -30 mg against 1 mg tolerance
        r.tap('pr_back')
        r.tap('tt_back')
        r.tap('ta_adj')
        r.tap('aa_weight_adjustment_external')
        r.tap('pr_start')
        r.host('pan.load', mass_kg=.1)
        r.step(3)
        r.host('pan.load', mass_kg=0.0)
        r.step(1)
        self.assertEqual(r.text('pr_result'), 'passed')
        r.host('pan.load', mass_kg=.1)
        self.assertAlmostEqual(r.val('balance.indicated_kg'), .1, places=9)
        hist = r.val('tests.history')
        self.assertEqual((hist['weight_test'][-1]['passed'], hist['weight_adjustment'][-1]['kind']), (False, 'Weight adjustment - external'))

    def test_external_adjustment_time_out(self):
        r = Rig()
        r.home()
        r.tap('t_test')
        r.tap('ta_adj')
        r.tap('aa_weight_adjustment_external')
        r.tap('pr_start')
        r.step(80)
        self.assertEqual(r.text('pr_result'), 'Weight out of range. Adjustment aborted.')   # p.82
        self.assertEqual(r.val('tests.history')['weight_adjustment'][-1]['kind'], 'FACT')

    def test_temperature_adjustment_and_test(self):
        r = Rig()
        r.host('sensor.configure_error', temp_offset_k=4.0)
        r.home()
        r.tap('t_test')
        r.tap('ta_adj')
        r.tap('aa_temperature_adjustment')
        self.assertFalse(r.tap('pr_start'))
        self.assertEqual(r.msg(), 'Insert the temperature kit.')
        r.ov('OK')
        r.host('temperature_kit.set', installed=True)
        r.tap('pr_start')
        r.step(1800)
        self.assertEqual(r.text('pr_result'), 'passed')
        self.assertAlmostEqual(r.h._sensor_c(), r.h.temperature_k - 273.15, places=6)   # the sensor reads the kit temperature now
        r.tap('pr_back')
        r.tap('aa_back')
        r.step(1800)                                                              # cool down below 50 degC
        r.tap('ta_tests')
        r.tap('tt_temperature_test')
        r.tap('pr_start')
        r.step(1800)
        self.assertEqual(r.text('pr_result'), 'passed')
        r.tap('pr_history')
        r.tap('hi_temperature_test')
        self.assertEqual(len(r.widgets()['hl_list']['cells']), 1)

    def test_repeatability(self):
        r = Rig()
        r.home()
        r.tap('t_test')
        r.tap('ta_tests')
        r.tap('tt_repeatability_external')
        r.tap('pr_start')
        for m in (.1, .10001, .09999):
            r.host('pan.load', mass_kg=m)
            r.step(3)
            r.host('pan.load', mass_kg=0.0)
            r.step(1)
        self.assertTrue(r.tap('pr_finish'))
        self.assertEqual(r.text('pr_result'), 'n = 3   s = 0.0100 g')


class SettingsUsers(unittest.TestCase):
    def test_date_format_and_clock(self):
        r = Rig()
        r.home()
        r.tap('t_settings')
        r.tap('s_instr')
        r.tap('si_region')
        r.choose('set_region_rows', 'Date format', 'YYYY-MM-DD')
        r.choose('set_region_rows', 'Time format', '12:MM')
        r.tap('set_region_save')
        self.assertEqual(r.text('sb_time'), '2026-10-18 09:00 AM')
        r.tap('si_region')
        r.row('f_clock_rows', 'Time')
        r.ov('+')
        r.ov('OK')
        self.assertTrue(r.val('device.clock').startswith('2026-10-18T10:00'))

    def test_preferences_language_brightness_keyboard(self):
        r = Rig()
        r.home()
        r.tap('t_settings')
        r.tap('s_prefs')
        r.choose('sp_rows', 'Display language', 'Deutsch')
        r.choose('sp_rows', 'Keyboard language', 'Deutsch')
        r.set_row('sp_rows', 'Brightness', '50')
        self.assertEqual(r.disp()['brightness'], 50)
        r.home()
        self.assertEqual(r.text('t_meas'), 'Messung')
        r.tap('t_methods')
        r.tap('md_new')
        r.ov('Define method manually')
        r.ov_row('Method name')
        labels = [b['label'] for b in r.overlay()['buttons']]
        self.assertEqual(labels[6], 'z')                                            # QWERTZ (p.86)

    def test_users_groups_login_rights(self):
        r = Rig()
        r.home()
        r.tap('t_settings')
        r.tap('s_um')
        r.tap('su_users')
        r.tap('us_new')
        r.row('ue_rows', 'User name')
        r.type('Op')
        r.ov('OK')
        r.choose('ue_rows', 'Account active', 'ON')
        r.choose('ue_rows', 'Group', 'Operators')
        r.tap('ue_save')
        r.row('us_list', 'Op')
        r.tap('ue_pw')
        r.ov_row('New password')
        r.type('pw1')
        r.ov('OK')
        r.ov_row('Confirm password')
        r.type('pw1')
        r.ov('OK')
        r.ov('OK')
        self.assertTrue(r.val('users.state')['users'][1]['has_password'])
        r.call(r.A, 'session.logout')                                             # the startup user logs out: the login dialog
        self.assertEqual(r.page(), ('login', 'login'))
        r.row('lg_list', 'Op')
        r.ov_row('Password')
        r.type('bad')
        r.ov('OK')
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.msg(), 'Wrong user name or password.')
        r.ov('OK')
        r.ov_row('Password')
        r.type('pw1')
        r.ov('OK')
        r.ov('OK')
        self.assertEqual((r.page(), r.val('users.state')['current']), (('main', 'home'), 'Op'))
        r.tap('t_methods')                                                         # Operators have no Method Definition right
        self.assertEqual(r.msg(), 'You do not have access rights for this function.')
        res = r.call(r.A, 'method.create', expect='rejected', name='x')
        self.assertEqual(res['error']['code'], 'PERMISSION_DENIED')
        r.ov('OK')
        r.call(r.A, 'session.logout')                                             # a non-standard user logs out: the startup user is active (p.106)
        self.assertEqual((r.page(), r.val('users.state')['current']), (('main', 'home'), 'Administrator'))
        r.home()
        r.tap('t_settings')
        r.tap('s_um')
        r.tap('su_policies')
        r.choose('set_policies_rows', 'Default startup user', 'OFF')
        r.tap('set_policies_save')
        r.press('key_onoff')
        r.press('key_onoff')
        self.assertEqual(r.page(), ('login', 'login'))                             # no startup user: login at start

    def test_group_rights_edit(self):
        r = Rig()
        r.home()
        r.tap('t_settings')
        r.tap('s_um')
        r.tap('su_groups')
        r.row('gr_list', 'Operators')
        r.choose('ge_rows', 'Run tests', 'granted')
        r.tap('ge_save')
        g = next(g for g in r.val('users.state')['groups'] if g['group_name'] == 'Operators')
        self.assertTrue(g['rights']['run_tests'])
        r.row('gr_list', 'Administrators')
        r.tap('ge_delete')
        self.assertFalse(r.ov('Delete'))
        self.assertEqual(r.msg(), 'This entry cannot be deleted or changed.')

    def test_auto_lockout(self):
        r = Rig()
        r.home()
        r.tap('t_settings')
        r.tap('s_um')
        r.tap('su_policies')
        r.choose('set_policies_rows', 'Auto lock-out', 'ON')
        r.set_row('set_policies_rows', 'Minutes', '2')
        r.tap('set_policies_save')
        r.step(119)
        self.assertEqual(r.page()[0], 'main')
        r.step(2)
        self.assertEqual(r.page(), ('login', 'login'))
        r.row('lg_list', 'Administrator')
        self.assertEqual(r.page()[0], 'main')

    def test_reset_and_setup_wizard(self):
        r = Rig()
        r.new_method('Gone')
        r.home()
        r.tap('t_settings')
        r.tap('s_sys')
        r.tap('sy_reset')
        r.ov('Reset')
        self.assertEqual(r.page(), ('setup', 'su_1'))
        self.assertEqual(r.val('methods.list'), [])
        res = r.call(r.A, 'method.create', expect='rejected', name='x')
        self.assertEqual(res['error']['code'], 'SETUP_REQUIRED')
        r.choose('su1_rows', 'Language', 'Français')
        r.choose('su1_rows', 'Proceed to the Instrument Tutorial', 'OFF')
        r.tap('su1_next')
        r.tap('su2_finish')
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertEqual(r.text('t_meas'), 'Mesure')
        r.tap('t_settings')
        r.tap('s_sys')
        r.tap('sy_reset')
        r.ov('Reinitialiser') if False else r.ov('Reset')
        r.tap('su1_next')
        r.tap('su2_finish')                                                       # p.17: the Instrument tutorial follows the Setup Wizard
        self.assertEqual(r.page(), ('main', 'help'))

    def test_identifications_on_work_screen(self):
        r = Rig()
        r.home()
        r.tap('t_settings')
        r.tap('s_app')
        r.tap('sa_ids')
        r.tap('ids_add')
        r.ov_row('Identification')
        r.type('Batch')
        r.ov('OK')
        r.ov('OK')
        r.choose('set_ids_rows', 'Identifications input', 'Free input')
        r.tap('set_ids_save')
        r.new_method('Id')
        r.measure('Id', start=False)
        r.tap('w_ids')
        r.row('wi_list', 'Batch')
        r.ov_row('Value')
        r.type('A1')
        r.ov('OK')
        r.ov('OK')
        r.tap('wi_back')
        self.assertEqual(r.text('w_ids'), 'IDs: Batch=A1')
        r.tap('w_start')
        r.run_to_end()
        self.assertEqual(r.val('results.list')[-1]['ids'], {'Batch': 'A1'})


class WizardsAndSmartCal(unittest.TestCase):
    """Method Wizard, temperature assistant, QuickPredict Wizard and SmartCal x1 test (p.51-55, p.60-61, p.81-83, p.103-104)."""

    def load_water(self, r, water=.002, residue=.003):
        r.host('sample.remove')
        r.host('pan.load', mass_kg=.0045)
        assert r.tap('w_tare')
        r.host('sample.configure', species_kg={'water': water}, residue_kg=residue)

    def test_method_wizard(self):
        r = Rig()
        r.home()
        r.tap('t_methods')
        r.tap('md_new')
        r.ov('Define using Method Wizard')
        self.assertEqual(r.overlay()['title'], 'Method Wizard')                  # disclaimer (p.51)
        r.ov('Next')
        r.ov_row('Method name')
        r.type('Wiz')
        r.ov('OK')
        r.ov('OK')
        self.assertIn('Step 1: questionnaire', r.msg())
        r.ov('Next')
        self.assertEqual(r.page(), ('main', 'wiz_q'))
        r.choose('wq_rows', 'Expected moisture content', 'Above 10 %')
        r.tap('wq_next')
        d = r.val('method.draft')['params']
        self.assertEqual((d['program'], d['temperature'], d['soc'], d['start_weight_g']), ('Rapid', 105, '4', 2.0))
        self.assertIn('Grind coarse samples', r.text('wp_prep'))
        r.tap('wp_temperature_assistant')
        r.tap('wp_next')
        self.assertEqual(r.page(), ('main', 'tassist'))
        self.assertIn('155 °C', r.text('ta_warn'))                                # drying temperature + 50 °C offered (assumption)
        r.set_row('ta_rows', 'Reference value (display unit)', '40')
        r.tap('ta_next')
        self.assertEqual(r.page(), ('main', 'work'))
        self.assertIn('Temperature assistant', r.text('tb_bar'))
        self.load_water(r)
        r.tap('w_start')
        r.run_to_end()
        a = r.val('assistant.result')
        self.assertTrue(a['passed'], a)
        self.assertEqual(r.text('w_purpose'), f"Proposed drying temperature: {a['proposed_c']} °C")
        self.assertEqual(r.val('method.draft')['params']['temperature'], a['proposed_c'])
        self.assertEqual(r.val('results.list'), [])                                # assistant runs are not stored as results
        r.tap('w_pnext')
        self.assertEqual(r.page(), ('main', 'wiz_prop2'))
        r.tap('wp2_next')                                                        # continue with the test measurement (recommended)
        r.ov('Start')
        self.load_water(r)
        r.tap('w_start')
        r.run_to_end(9000)
        r.tap('w_pnext')
        self.assertEqual(r.page(), ('main', 'wiz_soc'))
        rec = r.val('wizard.state')['test']['recommended']
        self.assertFalse(r.widgets()['ws_finish']['enabled'])
        r.row('ws_list', f'SOC {rec}')
        r.tap('ws_finish')
        self.assertEqual(r.page(), ('main', 'medit'))
        self.assertIsNone(r.val('wizard.state'))
        r.tap('me_save')
        m = next(m for m in r.val('methods.list') if m['name'] == 'Wiz')
        self.assertEqual((m['params']['temperature'], m['params']['soc'], m['params']['program']), (a['proposed_c'], rec, 'Rapid'))
        self.assertTrue(m['params']['sample_preparation'])

    def test_wizard_accept_after_questionnaire_and_assistant_failure(self):
        r = Rig()
        r.home()
        r.tap('t_methods')
        r.tap('md_new')
        r.ov('Define using Method Wizard')
        r.ov('Next')
        r.ov_row('Method name')
        r.type('Quick')
        r.ov('OK')
        r.ov('OK')
        r.ov('Next')
        r.choose('wq_rows', 'Temperature sensitivity', 'Sensitive (melts, decomposes, burns)')
        r.tap('wq_next')
        r.tap('wp_accept')
        r.tap('wp_next')
        self.assertEqual(r.page(), ('main', 'medit'))
        r.tap('me_save')
        self.assertEqual(rt._method(r.h.dev, 1)['params']['temperature'], 80)
        r.row('md_list', 'Quick')                                                 # Test method > Temperature assistant on a stored method
        r.tap('me_test')
        r.tap('mt_tassist')
        r.set_row('ta_rows', 'Reference value (display unit)', '60')
        r.choose('ta_rows', 'Maximum drying temperature', 'Enter another maximum')
        r.set_row('ta_rows', 'Maximum temperature', '120')
        r.tap('ta_next')
        self.load_water(r)
        r.tap('w_start')
        r.run_to_end()
        self.assertFalse(r.val('assistant.result')['passed'])
        self.assertIn('reference value was not reached', r.text('w_purpose'))
        self.assertEqual(r.val('method.draft')['params']['temperature'], 80)
        r.tap('w_pnext')
        self.assertEqual(r.page(), ('main', 'mp_main'))

    def test_quickpredict_wizard(self):
        r = Rig()
        mid = r.new_method('QPW', **{'Start weight': 'ON'})
        r.row('md_list', 'QPW')
        r.tap('me_main')
        r.set_row('mp_main_rows', 'Target weight', '5')
        r.choose('mp_main_rows', 'Weigh-in aid', 'Active')
        r.tap('mpm_back')
        r.tap('me_result')
        r.row('mp_result_rows', 'QuickPredict')
        r.row('mp_qp_rows', 'QuickPredict')
        r.ov('I accept the terms above')
        r.tap('mpq_wizard')
        r.ov('Start recording of reference curves: Next')
        self.assertEqual(r.page(), ('main', 'work'))
        self.assertEqual(r.val('qp_wizard.state')['schedule'], ['cold', 'warm', 'warm', 'warm', 'cold', 'cold'])
        self.assertIn('Reference [1 / 6]', r.text('tb_bar'))
        aborted = False
        for i in range(7):
            q = r.val('qp_wizard.state')
            if q['stage'] == 'calculated':
                break
            if r.val('work.state')['stage'] == 'done':
                r.tap('w_next')
            r.step(900, until=lambda: r.val('qp_wizard.state')['stage'] == 'ready')
            self.assertEqual(r.val('qp_wizard.state')['stage'], 'ready')
            r.host('sample.remove')
            r.host('pan.load', mass_kg=.0045)
            r.tap('w_tare')
            r.host('sample.configure', species_kg={'water': .0005}, residue_kg=.0045)
            if i == 1 and not aborted:                                           # a warm start that is stopped is rejected
                self.assertTrue(r.tap('w_start'))
                r.step(60)
                r.tap('w_stop')
                r.ov('Abort measurement and save data')
                aborted = True
                self.assertEqual(r.text('w_purpose'), 'Reference curve rejected: Measurement aborted.')
                continue
            if r.val('qp_wizard.state')['current'] == 'warm' and i == 2:
                r.h.dev['qp_wizard']['stage'] = 'conditioning'                    # cooled on purpose: start refused until warm again
                self.assertFalse(r.tap('w_start'))
                self.assertIn('warming up or cooling down', r.msg())
                r.ov('OK')
                r.step(900, until=lambda: r.val('qp_wizard.state')['stage'] == 'ready')
            self.assertTrue(r.tap('w_start'))
            r.run_to_end()
        self.assertEqual(r.val('qp_wizard.state')['stage'], 'calculated')
        self.assertEqual(r.text('w_purpose'), 'Prediction knowledge calculated successfully. Tap OK.')
        r.tap('w_qp_ok')
        r.ov('Finish')
        self.assertEqual(r.page(), ('main', 'medit'))                               # the method settings (p.61)
        self.assertEqual(r.val('method.draft')['params']['qp_usage'], 'Active')
        self.assertEqual(rt._method(r.h.dev, mid)['params']['qp_usage'], 'Inactive')  # not stored before Save (p.61)
        r.tap('me_save')
        m = rt._method(r.h.dev, mid)
        self.assertEqual((m['params']['qp_usage'], len(m['qp_refs'])), ('Active', 6))
        self.assertEqual(sorted(x['start'] for x in m['qp_refs']), ['cold'] * 3 + ['warm'] * 3)

    def smartcal(self, r, test_name='SmartCal 100 °C', rh=50.0, room_rh=50.0, lot='L2026'):
        r.home()
        r.tap('t_test')
        r.tap('ta_tests')
        r.tap('tt_smartcal')
        r.row('sc_methods', test_name)
        self.assertEqual(r.page(), ('main', 'work'))
        r.tap('w_sc_amb')
        r.set_row(None, 'Relative humidity', str(int(rh))) if False else None
        r.ov_row('Relative humidity')
        r.type(str(int(rh)))
        r.ov('OK')
        r.ov('OK')
        if lot is not None:
            r.tap('w_sc_lot')
            r.ov_row('Lot number')
            r.type(lot)
            r.ov('OK')
            r.ov('OK')
        r.host('sample.remove')
        r.host('pan.load', mass_kg=.0045)
        r.tap('w_tare')
        r.host('smartcal.place', mass_kg=.0025, room_rh_pct=room_rh)
        ok = r.tap('w_start')
        if ok:
            r.run_to_end()
        return ok

    def test_smartcal_pass_fail_and_history(self):
        r = Rig()
        r.home()
        r.tap('t_test')
        r.tap('ta_tests')
        r.tap('tt_smartcal')
        self.assertEqual([c[0] for c in r.widgets()['sc_methods']['cells']][:4], ['SmartCal 70 °C', 'SmartCal 100 °C', 'SmartCal 130 °C', 'SmartCal 160 °C'])
        r.row('sc_methods', 'SmartCal 100 °C')
        r.host('sample.remove')
        r.host('pan.load', mass_kg=.0045)
        r.tap('w_tare')
        r.host('smartcal.place', mass_kg=.0025, room_rh_pct=50.0)
        self.assertFalse(r.tap('w_start'))
        self.assertEqual(r.msg(), 'Enter the room temperature and humidity first.')
        r.ov('OK')
        r.tap('w_back')
        self.assertTrue(self.smartcal(r))
        res = r.val('smartcal.state')['result']
        self.assertTrue(res['passed'])
        self.assertAlmostEqual(res['result_mcn'], 5.8, delta=0.05)
        self.assertIn('passed', r.text('w_purpose'))
        self.assertEqual(r.val('results.list'), [])
        r.tap('w_sc_hist')
        self.assertEqual(r.page(), ('main', 'hist_list'))
        self.assertEqual(len(r.widgets()['hl_list']['cells']), 1)
        r.row('hl_list', r.widgets()['hl_list']['cells'][0][0])
        self.assertIn('Result: 5.8 %MCN', r.msg())
        self.assertIn('SmartCal identification: L2026', r.msg())
        r.ov('OK')
        r.host('sensor.configure_error', temp_offset_k=9.0, temp_gain=0.08)        # the sensor reads high: the sample is about 16 K colder
        r.step(1800)
        self.assertTrue(self.smartcal(r))
        self.assertFalse(r.val('smartcal.state')['result']['passed'])
        self.assertIn('failed', r.text('w_purpose'))
        r.host('sensor.configure_error', temp_offset_k=0.0, temp_gain=0.0)
        r.step(1800)
        self.assertTrue(self.smartcal(r, rh=80.0, room_rh=80.0))                   # humid storage, normalised with the entered humidity
        self.assertAlmostEqual(r.val('smartcal.state')['result']['result_mcn'], 5.8, delta=0.05)
        self.assertEqual(len(r.val('tests.history')['smartcal']), 3)

    def test_smartcal_settings_and_30_results(self):
        r = Rig()
        r.home()
        r.tap('t_settings')
        r.tap('s_qm')
        r.tap('sq_smartcal')
        r.choose('set_smartcal_rows', 'Use cSmartCal', 'ON')
        r.choose('set_smartcal_rows', 'Lot number input', 'OFF')
        r.tap('set_smartcal_save')
        st_ = r.val('device.settings')
        self.assertEqual((st_['sc.m2.lo'], st_['sc.m2.hi']), (5.3, 6.3))
        r.call(r.A, 'settings.configure', settings={'sc.m5.limits': True, 'sc.m5.lo': 1.0, 'sc.m5.hi': 2.0})
        r.call(r.A, 'settings.configure', settings={'sc.m5.temp': 90})
        self.assertFalse(r.val('device.settings')['sc.m5.limits'])                # p.104: a custom temperature resets the limits
        for _ in range(31):
            self.assertTrue(self.smartcal(r, lot=None))
            r.step(1800)
        h = r.val('tests.history')['smartcal']
        self.assertEqual(len(h), 30)                                              # p.82: at most 30 results per test


class SyncAuditFixes(unittest.TestCase):
    """1.2.1: rule 1 (the 1.0.0 direct run heats on the corrected sensor and reports loss against the indicated start weight), rule 3 (default
    settings, time zone) and rule 2 (a drying started on the terminal belongs to the caller; another agent cannot stop it)."""

    def direct_run(self, r, target_c=105, duration=1800):
        for cap, args in (('lid.set_position', {'position_m': .1085}), ('sample.configure', {'species_kg': {'water': .002}, 'residue_kg': .003}),
                          ('lid.set_position', {'position_m': 0.0}), ('thermal.set_temperature', {'temperature_k': target_c + 273.15}),
                          ('method.configure', {'mode': 'standard', 'duration_s': duration, 'basis': 'wet'})):
            r.call(r.A if cap != 'sample.configure' else r.E, cap, **args)
        return r.call(r.A, 'acquisition.start')['operation_id']

    def test_direct_run_heats_on_the_corrected_sensor(self):
        for err, cal in (({'temp_offset_k': 3.0}, None), ({}, {'temp_b': -3.0})):
            r = Rig()
            if err:
                r.host('sensor.configure_error', **err)
            if cal:
                r.h.cal.update(cal)                                                   # a stored temperature adjustment
            self.direct_run(r)
            r.step(900)
            self.assertAlmostEqual(r.val('thermal.indicated_temperature_k') - 273.15, 105.0, delta=0.1)
            self.assertAlmostEqual(r.h.temperature_k - 273.15, 102.0 if err else 108.0, delta=0.1)

    def test_direct_loss_uses_the_indicated_start_weight(self):
        r = Rig()
        r.host('sensor.configure_error', zero_kg=0.0005)
        self.direct_run(r)
        start = r.h.start_kg
        self.assertAlmostEqual(start, r.val('balance.indicated_kg'))
        self.assertAlmostEqual(start, 0.0055, places=7)                             # 5 g sample + the 0.5 mg zero error, no tare
        r.step(1800)
        res = r.call(r.A, 'moisture.measure')
        self.assertAlmostEqual(res['loss_fraction'], (start - res['mass_kg']) / start, places=12)
        self.assertAlmostEqual(r.h.curve[-1]['loss_fraction'], res['loss_fraction'], places=12)

    def test_default_settings_for_logged_out_and_new_users(self):
        r = Rig()
        r.call(r.A, 'settings.configure', settings={'default.language': 'Deutsch', 'default.keyboard': 'Français', 'default.brightness': 30,
                                                    'default.scheme': 'Green'})
        self.assertEqual(r.val('user.preferences')['language'], 'English')         # the logged-in Administrator keeps his own
        r.call(r.A, 'user.save', user_name='Neu', values={'user_name': 'Neu', 'group': 'Operators', 'active': True}, create=True)
        u = next(x for x in r.h.dev['users'] if x['user_name'] == 'Neu')
        self.assertEqual((u['prefs']['language'], u['prefs']['keyboard'], u['prefs']['brightness'], u['prefs']['scheme']), ('Deutsch', 'Français', 30, 'Green'))
        r.call(r.A, 'session.logout')
        p = r.val('user.preferences')
        self.assertEqual((p['language'], p['brightness'], p['scheme']), ('Deutsch', 30, 'Green'))
        self.assertEqual(r.frame()['displays'][TERM].get('brightness'), 30)

    def test_time_zone_shifts_the_shown_clock(self):
        r = Rig()
        self.assertTrue(r.val('device.clock').startswith('2026-10-18T09:00'))
        r.call(r.A, 'settings.configure', settings={'region.time_zone': 'Asia/Tokyo'})
        self.assertTrue(r.val('device.clock').startswith('2026-10-18T16:00'))
        r.call(r.A, 'clock.set', time='10:30')                                       # entered as Tokyo time
        self.assertTrue(r.val('device.clock').startswith('2026-10-18T10:30'))
        r.call(r.A, 'settings.configure', settings={'region.time_zone': 'UTC'})
        self.assertTrue(r.val('device.clock').startswith('2026-10-18T01:30'))

    def test_terminal_drying_belongs_to_the_caller(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.new_method('Mine')
        r.measure('Mine')
        op = r.val('work.state')['operation_id']
        self.assertEqual((r.h._owners.get(op), r.A.poll(op)['status']), ('agent', 'running'))
        r.step(60)
        before = r.h.read('evaluator')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        r.tap('w_stop')
        x, y, w, h = next(b for b in r.overlay()['buttons'] if b['label'] == 'Abort measurement and save data')['rect']
        r.call(B, 'panel.touch', display_id=TERM, x=x + w // 2, y=y + h // 2)
        self.assertEqual(r.msg(), 'This drying was started by another user: it cannot be stopped or interrupted from here.')
        r.ov('OK')
        r.call(B, 'panel.press', input_id='key_onoff')
        r.ov('OK')
        for cap, args in (('acquisition.stop', {}), ('measurement.stop', {'save': True}), ('power.set', {'enabled': False})):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h.read('evaluator'), before)
        self.assertEqual(r.A.cancel(op, 'c1')['status'], 'cancelled')                # the owner can cancel


class ErrorMessages(unittest.TestCase):
    """Sweep 2026-10-19 (1.3.0): the error messages of manual 9.1 by evaluator fault.inject / fault.clear."""
    def test_weighing_errors_replace_the_weight_and_refuse_tare_and_start(self):
        for code, shown, err in (('underload', 'Underload', 'Underload: insert the sample pan holder.'),
                                 ('overload', 'Overload', 'Overload: reduce the weight of the sample.'),
                                 ('zero_range', '0.0000', 'Out of the zero range: remove the weight from the sample pan and retry.')):
            r = Rig()
            r.new_method('Mine')
            r.home()
            r.tap('t_meas')
            r.row('ml_list', 'Mine')
            r.host('pan.load', mass_kg=.0045)
            r.host('fault.inject', code=code)
            self.assertEqual(r.text('w_value'), shown, code)                       # the work screen shows the message, not the weight
            self.assertFalse(r.tap('w_tare'))
            self.assertEqual(r.msg(), err)
            r.ov('OK')
            r.host('fault.clear', code=code)                                        # the remedy is done
            self.assertTrue(r.tap('w_tare'))
            self.assertEqual(r.text('w_value'), '0.000 g')

    def test_no_sensor_ends_a_drying(self):
        r = Rig()
        r.new_method('Mine')
        r.measure('Mine')
        op = r.val('work.state')['operation_id']
        r.step(60)
        r.host('fault.inject', code='no_sensor')
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')                    # no weight values: the drying ends (result saved, aborted)
        self.assertEqual(r.text('w_value'), 'No sensor connected.')
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='overload')['error']['code'], 'ACCESS_DENIED')   # evaluator only


class PanelEqualsDirect(unittest.TestCase):
    STRIP = ('device.clock',)

    def state(self, r):
        return {k: v for k, v in r.h.read('evaluator').items() if k not in self.STRIP}

    def test_method_and_measurement_equal_commands(self):
        a, b = Rig(), Rig()
        a.new_method('Same', **{'Drying temperature': '120'})
        b.call(b.A, 'method.create', name='Same')
        b.call(b.A, 'method.draft_set', values={'temperature': 120})
        b.call(b.A, 'method.save')
        a.measure('Same')
        b.call(b.A, 'measurement.open', method_id=1)
        b.host('pan.load', mass_kg=.0045)
        b.call(b.A, 'measurement.tare')
        b.host('sample.configure', species_kg={'water': .002}, residue_kg=.003)
        b.call(b.A, 'measurement.start')
        a.step(300)
        b.step(300)
        sa, sb = self.state(a), self.state(b)
        self.assertEqual(sa, sb)

    def test_keys_equal_commands(self):
        a, b = Rig(on=False), Rig(on=False)
        a.press('key_onoff')
        b.call(b.A, 'power.set', enabled=True)
        a.press('key_chamber')
        b.call(b.A, 'lid.set_position', position_m=.1085)
        self.assertEqual(self.state(a), self.state(b))


class RefusalsStandbyRendering(unittest.TestCase):
    def test_standby_gate(self):
        r = Rig(on=False)
        self.assertEqual(r.page(), ('standby', 'dark'))
        self.assertFalse(r.press('key_chamber'))
        self.assertFalse(r.touch(100, 100))
        self.assertTrue(r.press('key_onoff'))
        self.assertEqual(r.val('tests.history')['weight_adjustment'][-1]['kind'], 'FACT')   # FACT after switching on

    def test_refusal_leaves_state_and_shows_message(self):
        r = Rig()
        r.new_method('X')
        r.home()
        r.tap('t_meas')
        r.row('ml_list', 'X')
        before = copy.deepcopy(r.h.dev['work'])
        r.host('sample.configure', species_kg={'water': .0001}, residue_kg=.0001)  # sample placed before taring: the tare takes it in
        r.tap('w_tare')
        self.assertFalse(r.tap('w_start'))
        self.assertEqual(r.msg(), 'Sample weight too low (minimum 0.1 g).')
        self.assertEqual(r.val('cycle.phase'), 'idle')
        self.assertEqual(before['method_id'], r.h.dev['work']['method_id'])
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'SAMPLE_TOO_LIGHT')

    def test_duplicate_method_name(self):
        r = Rig()
        r.new_method('Dup')
        r.tap('md_new')
        r.ov('Define method manually')
        r.ov_row('Method name')
        r.type('Dup')
        r.ov('OK')
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.msg(), 'This name exists already. The name must be unique.')

    def test_unsupported_items(self):
        r = Rig()
        r.home()
        r.tap('t_methods')
        self.assertFalse(r.tap('md_import'))
        self.assertTrue(r.frame()['ui']['unsupported'])
        r.ov('OK')
        r.home()
        r.tap('t_settings')
        r.tap('s_sys')
        self.assertFalse(r.tap('sy_update'))
        self.assertIn('storage', r.frame()['ui']['unsupported'])

    def test_render_determinism_and_evidence(self):
        a, b = Rig(), Rig()
        for r in (a, b):
            r.new_method('Pic')
            r.measure('Pic')
            r.run_to_end()
        pa = a.call(a.A, 'panel.capture', display_id=TERM)
        pb = b.call(b.A, 'panel.capture', display_id=TERM)
        self.assertEqual(pa['image']['sha256'], pb['image']['sha256'])
        self.assertEqual(validate_value(pa, rt.CAPS['panel.capture'][1]), [])
        a.home()
        a.h.panel.save_png(TERM, OUT / 'home.png')
        a.tap('t_meas')
        a.h.panel.save_png(TERM, OUT / 'work.png')
        a.home()
        a.tap('t_methods')
        a.row('md_list', 'Pic')
        a.tap('me_main')
        a.h.panel.save_png(TERM, OUT / 'menu.png')
        a.tap('mpm_back')
        a.tap('me_save')
        a.home()
        a.tap('t_test')
        a.tap('ta_tests')
        a.tap('tt_smartcal')
        a.row('sc_methods', 'SmartCal 100 °C')
        a.host('sample.remove')
        a.host('pan.load', mass_kg=.0045)
        a.tap('w_tare')
        a.host('smartcal.place', mass_kg=.0025, room_rh_pct=50.0)
        a.tap('w_start')                                                         # room conditions not entered yet (p.82)
        a.h.panel.save_png(TERM, OUT / 'error.png')
        (OUT / 'renders.json').write_text(json.dumps({k: str(OUT.relative_to(ROOT) / f'{k}.png') for k in ('home', 'work', 'menu', 'error')}, indent=1) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_has_panel_layer(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + list(rt.TERMINAL):
            self.assertIn(k, c['capabilities'])
            self.assertEqual(c['capabilities'][k]['input_schema'], rt.CAPS[k][0])
        self.assertIn('button_home', c['components'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])

    def test_slot_values_match_schemas(self):
        r = Rig()
        r.new_method('S')
        r.measure('S')
        r.step(100)
        for k, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, rt.SLOTS[k][0]), [], k)


if __name__ == '__main__':
    unittest.main()
