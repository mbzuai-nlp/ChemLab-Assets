"""front-panel tests for CHEM-070 jasco_p4200 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/jasco_p4200.py):
python3 -m pytest tests/test_panel_ui_jasco_p4200.py -q

Every gesture goes through the session API (panel.switch / panel.touch, the runtime's normal invoke path); widgets are found by id in the
structured frame (panel.frame, evaluator) and tapped at their centre.  Cells and plates are put into the holder by the evaluator (sample.register /
load, as the host does for a robot), the lid by the agent.  Themes: the six key procedures of the menu survey; the device functions behind the
screens (parameters, data modes, zero clear, analyzer, automatic measurement, cycle interval, temperature start conditions, analysis, files,
daily check and correction, validation, jigs, users, backup, self diagnosis, status); panel = direct capability (same runtime state); refusals
shown as the device's message with the state unchanged; time-outs; render determinism and the evidence PNGs; contract entries."""
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
from asset_runtime import jasco_p4200 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'p4200_01'
OUT = ROOT / 'outputs/panel_ui/jasco_p4200'
SCR = 'screen'
DEG = math.pi / 180.0
SURVEY_TASKS = ['power on and select light source', 'edit a parameter', 'measure optical rotation', 'run a daily check', 'run validation', 'shut down']
DONE_TASKS = set()
BLANK = dict(rt.BLANK_EXAMPLE)
QUARTZ = dict(rt.QUARTZ_EXAMPLE)
SAMPLE = dict(rt.SAMPLE_EXAMPLE)                                                  # sucrose 100 kg/m^3, 100 mm: 6.637 deg


class Rig:
    """A runtime with an agent and an evaluator session; gestures only through panel.* capabilities."""

    def __init__(self, gain=1.0, on=True, zero=0.0):
        self.h = rt.JascoP4200Runtime(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.cells = {}
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, instrument_gain=gain, zero_offset_rad=zero))
        if on:
            self.switch('on')
            self.tap('ls_ok')

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap                                          # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        t = 0.0
        while t < dt - 1e-9:
            d = min(1.0, dt - t)
            self.h.step(d)
            t += d

    # ---- the sample compartment (host / evaluator) and the lid (agent)
    def cell(self, kind, sid=None, **kw):
        base = {'blank': BLANK, 'quartz': QUARTZ, 'sample': SAMPLE}[kind]
        sid = sid or base['sample_id']
        self.call(self.A, 'lid.set', opened=True)
        if self.h.active:
            self.call(self.E, 'sample.unload', recipient_id='rack')
        if sid not in self.cells:
            self.cells[sid] = dict(base, sample_id=sid, **kw)
            self.call(self.E, 'sample.register', **self.cells[sid])
        self.call(self.E, 'sample.load', sample_id=sid)
        self.call(self.A, 'lid.set', opened=False)

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCR]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCR, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def tap_as(self, sess, wid):
        """A tap made through another session (the session that gestures is the caller)."""
        x, y, ww, hh = self.widgets()[wid]['rect']
        self.n += 1
        return sess.invoke(instance_id=INST, capability_id='panel.touch', arguments=dict(display_id=SCR, x=x + ww // 2, y=y + hh // 2),
                           request_id=f'q{self.n}')

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def dialog(self):
        ov = self.overlay()
        return None if ov is None else ov.get('dialog')

    def row(self, wid, first, col=0):
        """Tap the row of a list or form widget whose cell `col` is this text (dragging the rows until it shows)."""
        for _ in range(12):
            w = self.widgets()[wid]
            cells = [c[col] for c in w['cells']]
            if first in cells:
                k = cells.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, cells)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def cells_of(self, wid):
        return self.widgets()[wid]['cells']

    def type(self, text):
        """Type on the open pad (the shown text is replaced): the numeric pad, or the text pad with a/A and ABC."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard'
        labels = {b['label'] for b in ov['buttons']}
        if 'CE' in labels:
            self.ov('CE')
            for ch in text:
                assert self.ov(ch), ch
            return
        self.ov('AC')
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('SP')
            elif ch.isdigit() or ch in '/():;+=,#_%&!?@*':
                if mode != 'sym':
                    self.ov('ABC')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('ABC')
                if (self.overlay()['mode'] == 'upper') != ch.isupper() and ch.isalpha():
                    self.ov('a/A')
                self.ov(ch)
        assert self.overlay()['field'] == text, (self.overlay()['field'], text)

    def enter(self, wid, label, text):
        self.row(wid, label)
        self.type(text)
        return self.ov('OK')

    def choose(self, wid, label, option):
        self.row(wid, label)
        return self.ov(option)

    def go_home(self):
        for _ in range(4):
            if self.page() == ('main', 'home'):
                return
            w = self.widgets()
            hid = next((k for k in w if k.endswith('_home') and k != 'home_measure'), None)
            if hid:
                self.tap(hid)
            else:
                self.tap(next(k for k in w if k.endswith('_back')))
        assert self.page() == ('main', 'home')

    def wait(self, limit=900.0):
        """Step until no measurement runs or waits (the device's own sequence, e.g. cycle times with an interval)."""
        t = 0.0
        while (self.h.phase == 'running' or self.h.dev['pending']) and t < limit:
            self.step(1.0)
            t += 1.0

    def display(self, key):
        return self.val('device.display')[key]

    # ---- procedures used by several tests (gestures only, apart from putting cells in the holder)
    def measure_blank(self):
        self.cell('blank')
        if self.page()[1] != 'measure':
            self.go_home()
            self.tap('home_measure')
        assert self.tap('m_blank')
        self.step(12)
        self.wait()

    def register_plate(self, name, deg, unc='0.002'):
        self.go_home()
        self.tap('home_management')
        self.tap('mg_jigs')
        self.tap('jg_new')
        self.enter('f_jig_w', 'Name', name)
        self.enter('f_jig_w', 'Certified value', deg)
        self.enter('f_jig_w', 'Expanded uncertainty', unc)
        assert self.tap('je_ok')

    def daily_check(self, plate='Quartz +10'):
        self.cell('quartz')
        self.go_home()
        self.tap('home_daily')
        self.choose('f_daily_w', 'Quartz plate', plate)
        assert self.tap('dc_start')
        self.step(12)
        self.wait()

    def correct(self):
        assert self.tap('dc_correct')
        assert self.dialog() == 'correct'
        assert self.ov('OK')

    def ready_for_samples(self):
        """Blank, plate registration, daily check and correction: what the runtime needs before a sample (all on the panel)."""
        self.measure_blank()
        self.register_plate('Quartz +10', '10')
        self.daily_check()
        self.correct()


class SurveyProcedures(unittest.TestCase):
    """The six key procedures of the menu survey, done only with panel gestures."""

    def test_power_on_and_select_light_source(self):
        g = Rig(on=False)
        self.assertEqual(g.page(), ('off', None))
        self.assertEqual(g.disp()['brightness'], 0)                               # dark screen while switched off
        self.assertFalse(g.touch(400, 240))                                      # touches do nothing when off
        self.assertTrue(g.switch('on'))
        self.assertEqual(g.page(), ('startup', 'light_source'))
        self.assertTrue(g.tap('ls_nc'))                                          # tick Non-Control Mode, then untick it
        self.assertTrue(g.val('device.settings')['non_control'])
        self.assertTrue(g.tap('ls_nc'))
        self.assertTrue(g.tap('ls_ok'))                                          # OK within 10 s
        self.assertEqual(g.page(), ('main', 'home'))
        s = g.val('device.settings')
        self.assertEqual((s['started'], s['na_lamp'], s['non_control']), (True, True, False))
        self.assertEqual(g.display('status'), 'Ready')
        DONE_TASKS.add('power on and select light source')

    def test_edit_a_parameter(self):
        g = Rig()
        g.tap('home_measure')
        g.tap('m_params')
        self.assertEqual(g.page(), ('main', 'params1'))
        self.assertTrue(g.choose('f_params_1', 'Data Mode', 'Specific O.R.'))
        self.assertEqual(g.cells_of('f_params_1')[0], ['Data Mode', 'Specific O.R.'])
        self.assertTrue(g.enter('f_params_1', 'D.I.T', '2'))
        self.assertTrue(g.enter('f_params_1', 'Cycle Times', '3'))
        self.assertTrue(g.tap('pp0_next'))
        self.assertEqual(g.page(), ('main', 'params2'))
        self.assertTrue(g.enter('f_params_2', 'Concentration', '10'))
        self.assertTrue(g.enter('f_params_2', 'Path Length', '50'))
        self.assertTrue(g.tap('pp1_next'))
        g.enter('f_params_3', 'Sample', 'Sucrose 1')
        self.assertEqual(g.val('device.settings')['params']['data_mode'], 'optical_rotation')   # nothing applies before OK
        self.assertTrue(g.tap('pp2_ok'))
        self.assertEqual(g.page(), ('main', 'measure'))
        s = g.val('device.settings')
        self.assertEqual(s['params']['data_mode'], 'specific_rotation')
        self.assertAlmostEqual(s['params']['concentration_kg_m3'], 100.0)
        self.assertAlmostEqual(s['params']['path_m'], 0.05)
        self.assertEqual(s['params']['sample_name'], 'Sucrose 1')
        self.assertEqual((s['method']['integration_s'], s['method']['repeats']), (2, 3))
        self.assertEqual(g.text('m_mode'), 'Specific O.R.')
        # Cancel discards, an out-of-range entry is refused on the pad
        g.tap('m_params')
        g.row('f_params_1', 'D.I.T')
        g.type('200')
        self.assertFalse(g.ov('OK'))
        self.assertEqual(g.overlay()['error'], 'Input range 1 - 100')
        g.ov('Cancel')
        g.choose('f_params_1', 'Data Mode', 'Sugar Scale')
        g.tap('pp0_cancel')
        self.assertEqual(g.val('device.settings')['params']['data_mode'], 'specific_rotation')
        DONE_TASKS.add('edit a parameter')

    def test_measure_optical_rotation(self):
        g = Rig(gain=1.02)
        g.ready_for_samples()
        self.assertTrue(g.val('calibration.ready'))
        g.cell('sample')
        g.go_home()
        g.tap('home_measure')
        g.tap('m_params')
        g.tap('pp0_next')
        g.tap('pp1_next')
        g.enter('f_params_3', 'Sample', 'Sucrose')
        g.tap('pp2_ok')
        self.assertTrue(g.tap('m_start'))
        self.assertEqual(g.display('status'), 'Analyzer moving')
        self.assertIn('m_stop', g.widgets())
        g.step(15)
        self.assertEqual(g.val('scan.phase'), 'succeeded')
        self.assertEqual(g.text('m_value'), '6.6370')                             # sucrose 100 g/L, 100 mm: 6.637 deg (gain corrected)
        self.assertEqual(g.text('m_unit'), 'deg')
        self.assertEqual(g.display('sample_no'), 2)                              # the sample number advances (iRM p.20)
        g.tap('m_analysis')
        cells = g.cells_of('an_list')
        self.assertEqual([c[2] for c in cells], ['Blank', 'Sucrose'])
        self.assertEqual(cells[1][3], '6.6370 deg')
        DONE_TASKS.add('measure optical rotation')

    def test_run_a_daily_check(self):
        g = Rig(gain=1.02)
        g.measure_blank()
        g.register_plate('Quartz +10', '10', '0.002')
        g.daily_check()
        rows = g.cells_of('dc_list')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:4], ['Quartz +10', '10.0000', '10.2000'])     # 2 % gain error of the simulated instrument
        self.assertEqual(rows[0][6], 'Fail')
        d = g.val('device.data')['daily_last']
        self.assertAlmostEqual(d['ht_v'], 250.0 / math.sqrt(0.8 + 2 * 0.01), places=6)
        g.correct()                                                              # the correction from the plate just checked
        self.assertAlmostEqual(g.val('calibration.gain'), 1.02, places=12)
        g.daily_check()
        self.assertEqual(g.cells_of('dc_list')[1][3:5], ['10.0000', '+0.0000'])
        self.assertEqual(g.cells_of('dc_list')[1][6], 'Pass')
        DONE_TASKS.add('run a daily check')

    def test_run_validation(self):
        g = Rig(gain=1.0)
        g.register_plate('Quartz +10', '10')
        g.go_home()
        g.tap('home_validation')
        g.choose('f_val_w', 'Inspection method', 'Basic')
        g.row('f_val_w', 'Quartz plates')
        g.ov('[ ] Quartz +10')
        g.ov('OK')
        g.enter('f_val_w', 'Repeat measurement', '3')
        self.assertEqual(g.val('device.settings')['validation']['jig_ids'], ['jig_1'])
        self.assertTrue(g.tap('va_start'))
        self.assertIn('Set the blank (solvent) cell', g.text('va_prompt'))
        g.cell('blank')
        self.assertTrue(g.tap('va_ok'))
        g.step(40)                                                               # three blank measurements, one after the other
        self.assertIn('quartz plate Quartz +10', g.text('va_prompt'))
        g.cell('quartz')
        g.tap('va_ok')
        g.step(40)
        res = g.val('device.data')['last_validation']
        self.assertTrue(res['passed'])
        self.assertEqual([c[0] for c in g.cells_of('va_result')], ['Zero repeatability', 'Accuracy of O.R.', 'Repeatability of O.R.'])
        self.assertEqual([c[3] for c in g.cells_of('va_result')], ['Pass', 'Pass', 'Pass'])
        DONE_TASKS.add('run validation')

    def test_shut_down(self):
        g = Rig()
        g.tap('home_measure')
        g.go_home()                                                              # back to the main menu, no sample, switch off
        self.assertTrue(g.switch('off'))
        self.assertEqual(g.page(), ('off', None))
        self.assertFalse(g.val('power.enabled'))
        self.assertFalse(g.val('device.settings')['started'])
        g.switch('on')
        self.assertEqual(g.page(), ('startup', 'light_source'))                  # the light-source window comes back
        DONE_TASKS.add('shut down')


class DeviceFunctions(unittest.TestCase):
    def test_data_modes_follow_the_manual_formulas(self):
        g = Rig()
        g.ready_for_samples()
        g.cell('sample')
        g.go_home()
        g.tap('home_measure')
        g.tap('m_params')
        g.tap('pp0_next')
        g.enter('f_params_2', 'Concentration', '10')
        g.tap('pp1_ok')
        g.tap('m_start')
        g.step(15)
        a = 6.637
        expect = {'Optical Rotation': f'{a:.4f}', 'Specific O.R.': f'{a * 10000 / (10 * 100):.2f}', 'Concentration': f'{a * 10000 / (66.37 * 100):.4f}',
                  'Sugar Scale': f'{a * 10000 / (17.313 * 100):.3f}', 'Optical Purity': '100.00'}
        sg = rt.BRIX_SG[20]
        expect['Brix Purity'] = f'{26 * (a * 10000 / (17.313 * 100)) / (99.718 * sg) / 20 * 100:.2f}'
        for mode, text in expect.items():
            g.tap('m_params')
            g.choose('f_params_1', 'Data Mode', mode)
            g.tap('pp0_ok')
            self.assertEqual(g.text('m_value'), text, mode)
        g.tap('m_params')                                                        # Factor multiplies the result (iRM p.20)
        g.choose('f_params_1', 'Data Mode', 'Optical Rotation')
        g.tap('pp0_next')
        g.enter('f_params_2', 'Factor', '2')
        g.tap('pp1_ok')
        self.assertEqual(g.text('m_value'), f'{2 * a:.4f}')
        g.tap('m_params')                                                        # temperature correction to 20 C: the sample is at 20 C
        g.tap('pp0_next')
        g.enter('f_params_2', 'Factor', '1')
        g.tap('pp1_next')
        g.enter('f_params_3', 'Temp. Correct.', '0.001')
        g.tap('pp2_ok')
        self.assertEqual(g.text('m_value'), f'{a:.4f}')                         # the sample is at 20 C: no correction
        p = g.val('device.settings')['params']                                   # at 25 C: alpha20 = alpha_t / (1 + k (t - 20)) (iRM p.82)
        self.assertAlmostEqual(g.h._alpha(a * DEG, 298.15, p), a * DEG / (1 + 0.001 * 5) - p['blank_rad'], places=12)

    def test_recalculation_and_statistics(self):
        g = Rig()
        g.ready_for_samples()
        g.cell('sample')
        g.go_home()
        g.tap('home_measure')
        for _ in range(2):
            g.tap('m_start')
            g.step(15)
        g.tap('m_analysis')
        g.row('an_list', '2', col=1)
        g.row('an_list', '3', col=1)
        self.assertEqual([c[0] for c in g.cells_of('an_list')], ['', '*', '*'])
        st = g.val('device.data')['stats']
        self.assertEqual(st['n'], 2)
        self.assertAlmostEqual(st['avg'], 6.637, places=9)
        self.assertAlmostEqual(st['sd'], 0.0, places=12)
        g.tap('an_stat')
        self.assertEqual(g.overlay()['lines'][1], 'Avg. = 6.6370')
        g.ov('OK')
        g.tap('an_recalc')
        g.ov('Specific O.R.')
        rows = g.cells_of('an_list')
        c = g.val('device.settings')['params']['concentration_kg_m3'] / 10.0
        self.assertEqual(rows[-1][3], f'{6.637 * 10000 / (c * 100):.2f} deg')
        self.assertEqual(rows[-1][4], 'Specific O.R.')
        self.assertEqual(rows[0][4], 'Optical Rotation')                         # rows not selected keep their mode
        g.tap('an_delete')
        g.ov('OK')
        self.assertEqual(len(g.val('device.data')['results']), 1)
        g.tap('an_save')
        self.assertEqual(g.dialog(), 'save')
        g.ov('File name')
        g.type('RUN1')
        g.ov('OK')
        g.ov('Save')
        self.assertEqual(g.val('device.data')['files'][0]['name'], 'RUN1')
        g.tap('an_save')                                                         # the same name again: the device's message
        g.ov('File name')
        g.type('RUN1')
        g.ov('OK')
        self.assertFalse(g.ov('Save'))
        self.assertEqual(g.dialog(), 'err_file_exists')

    def test_zero_clear_analyzer_and_blank_subtraction(self):
        g = Rig()
        g.ready_for_samples()
        g.cell('sample')
        g.go_home()
        g.tap('home_measure')
        g.tap('m_start')
        g.step(15)
        self.assertTrue(g.tap('m_zero'))                                         # Zero Clear: the current reading becomes 0
        self.assertEqual(g.text('m_value'), '0.0000')
        self.assertAlmostEqual(g.val('device.settings')['zero_rad'], 6.637 * DEG, places=12)
        self.assertAlmostEqual(g.val('scan.rotation_rad'), 6.637 * DEG, places=12)   # a point already recorded keeps its value ...
        g.tap('m_start')                                                         # ... the next measurement is zero-referenced on every path
        g.step(15)
        self.assertEqual(g.text('m_value'), '0.0000')
        self.assertAlmostEqual(g.val('scan.rotation_rad'), 0.0, places=12)
        self.assertAlmostEqual(g.call(g.A, 'result.read')['points'][-1]['rotation_rad'], 0.0, places=12)
        self.assertEqual(g.val('device.data')['results'][-1]['value_text'], '0.0000')
        g.switch('off')
        g.switch('on')
        g.tap('ls_ok')
        self.assertEqual(g.val('device.settings')['zero_rad'], 0.0)
        # analyzer on: the light path stays open and the analyzer follows a new plate in real time
        g.tap('home_measure')
        g.cell('quartz')
        self.assertTrue(g.tap('m_analyzer'))
        self.assertTrue(g.val('lamp.enabled'))
        self.assertTrue(g.widgets()['m_analyzer']['selected'])
        g.step(3)
        self.assertEqual(g.text('m_live'), 'Analyzer: real time')
        self.assertAlmostEqual(g.val('optics.analyzer_rad'), 10 * DEG, places=12)
        self.assertEqual(g.text('m_value'), '10.0000')
        g.tap('m_analyzer')
        self.assertFalse(g.val('lamp.enabled'))
        # subtract blank off: the blank value is added back
        g.tap('m_params')
        g.choose('f_params_1', 'Subtract Blank', 'Off')
        g.tap('pp0_ok')
        self.assertFalse(g.val('device.settings')['params']['subtract_blank'])

    def test_automatic_measurement_on_lid_close(self):
        g = Rig()
        g.ready_for_samples()
        g.go_home()
        g.tap('home_measure')
        g.tap('m_auto')
        self.assertTrue(g.val('device.settings')['auto_meas'])
        g.cell('sample')                                                         # closing the lid starts the measurement (iRM p.17)
        self.assertEqual(g.val('scan.phase'), 'running')
        g.step(15)
        self.assertEqual(g.text('m_value'), '6.6370')
        g.call(g.A, 'lid.set', opened=True)                                      # a refused automatic start leaves a message, the lid still closes
        g.call(g.E, 'sample.unload', recipient_id='rack')
        g.call(g.A, 'lid.set', opened=False)
        self.assertEqual(g.display('message'), 'SAMPLE_METHOD_REQUIRED')
        self.assertFalse(g.val('lid.opened'))
        self.assertEqual(g.text('msg_text'), 'Automatic measurement: no cell in the holder.')
        g.tap('msg_ok')
        self.assertEqual(g.display('message'), '')

    def test_cycle_times_and_interval(self):
        g = Rig()
        g.ready_for_samples()
        g.cell('sample')
        g.go_home()
        g.tap('home_measure')
        g.tap('m_params')
        g.enter('f_params_1', 'Cycle Times', '3')
        g.enter('f_params_1', 'Cycle Interval', '20')
        g.tap('pp0_ok')
        self.assertFalse(g.val('blank.ready'))                                   # a new method needs a new blank (runtime rule)
        g.measure_blank()
        g.daily_check()
        g.correct()
        g.cell('sample')
        g.go_home()
        g.tap('home_measure')
        g.tap('m_start')
        t0 = g.h.time_s
        while g.val('scan.phase') == 'running':
            g.step(1)
        pts = g.call(g.A, 'result.read')['points']
        self.assertEqual(len(pts), 3)
        self.assertAlmostEqual(pts[1]['sample_time_s'] - pts[0]['sample_time_s'], 20.0, places=6)
        self.assertAlmostEqual(pts[2]['sample_time_s'] - pts[1]['sample_time_s'], 20.0, places=6)
        self.assertGreater(g.h.time_s - t0, 44)
        self.assertEqual(g.display('cycle_text'), '[3/3]')

    def test_temperature_start_conditions(self):
        g = Rig()
        g.ready_for_samples()
        g.cell('sample')
        g.go_home()
        g.tap('home_management')
        g.tap('mg_temp')
        self.assertEqual(g.page(), ('main', 'temperature'))
        g.choose('f_temp_w', 'Start Conditions', 'Type 2 (stabilized)')
        g.enter('f_temp_w', 'Stabiliza. Time', '30')
        g.enter('f_temp_w', 'Tolerance', '0.1')
        g.tap('tc_ok')
        c = g.val('device.settings')['temp']
        self.assertEqual((c['start_type'], c['stabilization_s'], c['tolerance_k']), (2, 30, 0.1))
        self.assertTrue(g.val('blank.ready'))                                    # the set temperature did not change: blank kept
        g.go_home()
        g.tap('home_measure')
        g.tap('m_start')
        self.assertEqual(g.text('m_status'), 'Waiting for temperature')
        self.assertIn('m_stop', g.widgets())
        g.step(29)
        self.assertEqual(g.val('scan.phase'), 'idle')                            # still waiting (the new cell cleared the old result)
        g.step(2)
        self.assertEqual(g.val('scan.phase'), 'running')
        g.step(15)
        self.assertEqual(g.text('m_value'), '6.6370')
        g.tap('m_start')                                                         # Stop while waiting cancels the start
        self.assertTrue(g.tap('m_stop'))
        self.assertIsNone(g.h.dev['pending'])
        self.assertEqual(g.text('m_status'), 'Ready')

    def test_temperature_timeout_and_crossings(self):
        g = Rig()
        g.ready_for_samples()
        g.cell('sample')
        g.go_home()
        g.tap('home_management')
        g.tap('mg_temp')
        g.choose('f_temp_w', 'Start Conditions', 'Type 3 (crossings)')
        g.enter('f_temp_w', 'No. of Attain', '2')
        g.enter('f_temp_w', 'Time-Out', '25')
        g.tap('tc_ok')
        g.go_home()
        g.tap('home_measure')
        g.tap('m_start')
        g.step(30)                                                               # the holder sits on the set point: no crossings
        self.assertEqual(g.display('message'), 'TEMPERATURE_TIMEOUT')
        self.assertEqual(g.text('msg_text'), 'Time-out: the start temperature condition was not met.')
        self.assertNotEqual(g.val('scan.phase'), 'running')

    def test_users_rights(self):
        g = Rig()
        g.tap('home_management')
        g.tap('mg_users')
        g.tap('us_new')
        g.ov('User name')
        g.type('OP1')
        g.ov('OK')
        g.ov('Rights')
        g.ov('Operator')
        g.ov('OK')
        self.assertEqual(g.val('device.admin')['users'][1], {'name': 'OP1', 'role': 'operator'})
        g.row('us_list', 'OP1')
        g.tap('us_login')
        self.assertEqual(g.val('device.settings')['role'], 'operator')
        g.go_home()
        self.assertTrue(g.tap('home_management'))
        self.assertEqual(g.dialog(), 'access_denied')                            # operators cannot open Management
        g.ov('OK')
        g.tap('home_validation')
        self.assertFalse(g.widgets()['f_val_w']['enabled'])                     # nor change the validation settings
        g.call(g.A, 'users.edit', op='login', name='Administrator')
        g.go_home()
        g.tap('home_management')
        self.assertEqual(g.page(), ('main', 'management'))
        g.tap('mg_users')
        g.row('us_list', 'Administrator')
        g.tap('us_delete')
        self.assertFalse(g.ov('OK'))                                             # the logged-in user cannot be deleted
        self.assertEqual(g.dialog(), 'err_in_use')

    def test_backup_diagnosis_status_and_light_source(self):
        g = Rig()
        g.tap('home_management')
        g.tap('mg_backup')
        g.tap('bk_now')
        g.tap('bk_auto')
        self.assertTrue(g.val('device.settings')['auto_backup'])
        g.switch('off')
        g.switch('on')
        b = g.val('device.admin')['backups']
        self.assertEqual([x['trigger'] for x in b], ['manual', 'power-off'])
        g.step(11)                                                               # the light-source window applies itself after 10 s
        self.assertEqual(g.page(), ('main', 'home'))
        g.tap('home_management')
        g.tap('mg_diag')
        g.tap('dg_run')
        self.assertEqual([c[1] for c in g.cells_of('dg_list')], ['OK', 'OK', 'OK', 'OK', 'OK'])
        g.tap('dg_back')
        g.tap('mg_status')
        self.assertEqual(g.text('is_2'), 'Lamp on time:   0.0 h')
        g.step(3600)
        self.assertEqual(g.text('is_2'), 'Lamp on time:   1.0 h')
        g.tap('is_back')
        g.tap('mg_light')                                                        # light source off: measurements refused
        g.ov('Na lamp (589 nm)')
        g.ov('Off')
        g.ov('OK')
        self.assertFalse(g.val('device.settings')['na_lamp'])
        g.go_home()
        g.tap('home_measure')
        self.assertFalse(g.tap('m_blank'))
        self.assertEqual(g.dialog(), 'err_light_source_off')

    def test_validation_usp_linearity_and_temperature(self):
        g = Rig(gain=1.0)
        for name, deg in (('Q+10', '10'), ('Q+5', '5'), ('Q-10', '-10')):
            g.register_plate(name, deg)
        g.go_home()
        g.tap('home_validation')
        g.choose('f_val_w', 'Inspection method', 'USP')
        g.row('f_val_w', 'Quartz plates')
        for name in ('Q+10', 'Q+5', 'Q-10'):
            g.ov('[ ] ' + name)
        g.ov('OK')
        g.enter('f_val_w', 'Repeat measurement', '2')
        g.enter('f_val_w', 'Reference thermometer', '20.3')
        g.tap('va_start')
        g.cell('blank')
        g.tap('va_ok')
        g.step(12)
        for sid, deg in (('qa', 10), ('qb', 5), ('qc', -10)):
            self.assertIn('quartz plate', g.text('va_prompt'))
            g.cell('quartz', sid, quartz_angle_rad=deg * DEG)
            g.tap('va_ok')
            g.step(30)
        res = g.val('device.data')['last_validation']
        self.assertEqual([i['item'] for i in res['items']], ['Temperature control', 'Accuracy of O.R.', 'Repeatability of O.R.', 'Linearity of O.R.'])
        self.assertTrue(res['passed'])
        self.assertEqual(res['items'][0]['value_text'], '-0.30 C')
        self.assertEqual(res['items'][3]['value_text'], '3/3 plates within accuracy')

    def test_validation_refusals_and_cancel(self):
        g = Rig()
        g.tap('home_validation')
        g.choose('f_val_w', 'Inspection method', 'EP')
        self.assertFalse(g.tap('va_start'))                                      # no plates selected
        self.assertEqual(g.dialog(), 'err_validation_setup')
        g.ov('OK')
        g.register_plate('Quartz +10', '10')
        g.go_home()
        g.tap('home_validation')
        g.choose('f_val_w', 'Inspection method', 'JP')
        g.row('f_val_w', 'Quartz plates')
        g.ov('[ ] Quartz +10')
        g.ov('OK')
        g.tap('va_start')
        g.cell('quartz')                                                         # the wrong cell for the blank step
        self.assertFalse(g.tap('va_ok'))
        self.assertEqual(g.dialog(), 'err_sample_kind_mismatch')
        g.ov('OK')
        g.cell('blank')
        g.tap('va_ok')
        g.tap('va_cancel')
        g.ov('Yes')
        self.assertIsNone(g.val('device.data')['validation'])
        self.assertNotEqual(g.val('scan.phase'), 'running')


class PanelEqualsDirect(unittest.TestCase):
    def test_same_runtime_state(self):
        g = Rig()
        g.ready_for_samples()
        g.cell('sample')
        g.go_home()
        g.tap('home_measure')
        g.tap('m_params')
        g.choose('f_params_1', 'Data Mode', 'Concentration')
        g.tap('pp0_ok')
        g.tap('m_start')
        g.step(15)
        d = Rig(on=False)                                                        # the same commands called directly
        d.call(d.A, 'power.set', enabled=True)
        d.call(d.A, 'device.light_source', apply=True)
        d.cell('blank')
        d.call(d.A, 'measure.start', kind='blank')
        d.step(12)
        d.call(d.A, 'jig.edit', op='add', name='Quartz +10', certified_rad=10 * DEG, uncertainty_rad=0.002 * DEG)
        d.cell('quartz')
        d.call(d.A, 'daily_check.start', jig_id='jig_1')
        d.step(12)
        d.call(d.A, 'calibration.quartz', certified_angle_rad=10 * DEG, accept_simulated_standard=True)
        d.cell('sample')
        d.call(d.A, 'device.parameters', settings=dict(g.val('device.settings')['params'], sample_no=1))
        d.call(d.A, 'measure.start', kind='sample')
        d.step(15)
        a, b = g.h.read('evaluator'), d.h.read('evaluator')
        for k in a:
            self.assertEqual(json.dumps(a[k], sort_keys=True), json.dumps(b[k], sort_keys=True), k)
        self.assertEqual(g.text('m_value'), '10.0000')                           # 6.637 deg / (66.37 x 100 mm) x 10000 = 10 w/v%


class SyncFixes(unittest.TestCase):
    """Sync-audit fixes of 2026-10-19 (1.1.1): one rotation model, Meas. Point, run ownership, Help; sweep (1.1.2): the lid is physical."""

    def test_one_rotation_model_for_every_reading_path(self):
        g = Rig(gain=1.02, zero=0.001)                                           # 2 % gain and a 0.001 rad zero of the simulated instrument
        g.call(g.A, 'thermal.set_temperature', temperature_k=298.15)             # 25 C: the temperature correction acts
        g.step(300)
        g.cell('blank', reference_temperature_k=298.15)
        g.call(g.A, 'measure.start', kind='blank')
        g.wait()
        g.call(g.A, 'jig.edit', op='add', name='Quartz +10', certified_rad=10 * DEG, uncertainty_rad=0.002 * DEG)
        g.cell('quartz', reference_temperature_k=298.15)
        g.call(g.A, 'daily_check.start', jig_id='jig_1')
        g.wait()
        g.call(g.A, 'calibration.quartz', certified_angle_rad=10 * DEG, accept_simulated_standard=True)
        g.call(g.A, 'device.parameters', settings={'temp_correct_per_k': 0.001})
        g.cell('sample', 'alanine', template_id='pubchem_5950', concentration_kg_m3=60.0, reference_temperature_k=298.15)
        r = g.call(g.A, 'measure.start', kind='sample')
        self.assertEqual(r, {'started': True, 'waiting': False})
        op = g.h.operation_id
        g.wait()
        pts = g.call(g.A, 'result.read')['points']
        raw, gain, blank = pts[-1]['raw_rotation_rad'], g.val('calibration.gain'), g.val('device.settings')['params']['blank_rad']
        expect = raw / gain / (1 + 0.001 * 5.0) - blank / gain                    # alpha20 = alpha_t / (1 + k (t - 20)) - blank (iRM p.82)
        self.assertAlmostEqual(pts[-1]['rotation_rad'], expect, places=13)
        self.assertGreater(abs(expect - (raw - blank) / gain), 1e-6)            # the direct reading now carries the correction
        self.assertAlmostEqual(g.val('scan.rotation_rad'), expect, places=13)
        self.assertAlmostEqual(g.A.poll(op)['observations']['scan.rotation_rad']['value'], expect, places=13)
        self.assertAlmostEqual(g.call(g.A, 'process.measure')['rotation_rad'], expect, places=13)
        self.assertAlmostEqual(g.val('device.data')['results'][-1]['value'], math.degrees(expect), places=10)
        self.assertEqual(g.display('value_text'), f'{math.degrees(expect):.4f}')
        g.call(g.A, 'device.zero_clear')                                         # the shown rotation becomes 0, also with a blank value subtracted
        self.assertEqual(g.display('value_text'), '0.0000')
        self.assertAlmostEqual(g.val('device.settings')['zero_rad'], expect, places=13)
        g.call(g.A, 'measure.start', kind='sample')
        g.wait()
        self.assertAlmostEqual(g.val('scan.rotation_rad'), 0.0, places=13)
        self.assertEqual(g.display('value_text'), '0.0000')
        self.assertEqual(g.val('device.data')['results'][-1]['value_text'], '0.0000')

    def _run_to_set(self, point):
        """Set 25 C and Meas. Point on the temperature page, then follow holder and in-cell readings for 900 s (1 s steps)."""
        g = Rig()
        g.tap('home_management')
        g.tap('mg_temp')
        g.choose('f_temp_w', 'Meas. point', point)
        g.enter('f_temp_w', 'Temperature', '25')
        g.choose('f_temp_w', 'Start Conditions', 'Type 2 (stabilized)')
        g.enter('f_temp_w', 'Stabiliza. Time', '30')
        g.enter('f_temp_w', 'Tolerance', '0.1')
        g.tap('tc_ok')
        g.go_home()
        g.tap('home_measure')
        g.tap('m_start')                                                         # waits for the start condition on the selected sensor
        self.assertTrue(g.h.dev['pending'])
        trace, fired = [], None
        for i in range(900):
            g.step(1.0)
            trace.append((g.h.temperature_k, g.h.cell_k))
            if fired is None and not g.h.dev['pending']:
                fired = (i + 1, g.h.temperature_k, g.h.cell_k)
        return g, trace, fired

    def test_meas_point_selects_the_sensor(self):
        h, th, fh = self._run_to_set('Holder')
        c, tc, fc = self._run_to_set('Cell')
        self.assertEqual(c.val('device.settings')['params']['meas_point'], 'cell')
        self.assertLessEqual(max(t for t, _ in th), 298.15 + 1e-6)              # Holder: the holder approaches the set point from below
        self.assertGreater(max(t for t, _ in tc), 298.15 + 1.0)                 # Cell: the holder overshoots to bring the lagging cell up
        self.assertAlmostEqual(tc[-1][0], 298.15, places=3)                     # both settle at the set temperature
        self.assertAlmostEqual(tc[-1][1], 298.15, places=3)
        self.assertAlmostEqual(th[-1][1], 298.15, places=3)
        self.assertLess(th[20][1], th[20][0] - 1.0)                             # the in-cell sensor lags the holder
        self.assertGreater(fc[0], fh[0])                                         # Type 2 start on the selected sensor: later with Cell
        self.assertLessEqual(abs(fc[2] - 298.15), 0.1 + 1e-9)
        self.assertEqual(h.display('message'), 'OPTICS_NOT_READY')               # the start was tried when the condition was met (no cell, lid open)
        c.call(c.A, 'thermal.set_temperature', temperature_k=293.15)
        c.step(20)
        d = c.val('device.display')
        self.assertEqual(d['temperature_text'], d['cell_text'])                  # the screen shows the selected sensor
        self.assertNotEqual(d['cell_text'], d['holder_text'])
        self.assertIn(f"Temp. {d['cell_text']} C [Cell]", c.text('m_info1'))
        c.go_home()
        c.tap('home_management')
        c.tap('mg_diag')
        c.tap('dg_run')
        rows = {r[0]: r for r in c.cells_of('dg_list')}
        self.assertEqual(rows['Temperature sensor (cell)'][2], c.val('device.display')['cell_text'] + ' C')

    def test_runs_belong_to_the_caller(self):
        g = Rig()
        g.h.register_principal('agent2', visibility='agent')
        B = g.h.session('agent2')
        g.ready_for_samples()
        g.cell('sample')
        g.go_home()
        g.tap('home_measure')
        self.assertTrue(g.tap('m_start'))                                        # panel Start
        op = g.h.operation_id
        self.assertEqual(g.h._owners[op], 'agent')
        self.assertEqual(g.A.poll(op)['status'], 'running')
        before = g.h.debug_snapshot()
        for f in (lambda: B.poll(op), lambda: B.cancel(op, 'b1')):                # another agent can neither poll nor cancel it
            with self.assertRaisesRegex(rt.ActionRejected, 'ACCESS_DENIED'):
                f()
        self.assertEqual(before, g.h.debug_snapshot())
        self.assertEqual(g.A.cancel(op, 'a1')['result']['phase'], 'cancelled')
        g.call(g.A, 'measure.start', kind='sample')                              # the device Start command returns no operation id
        self.assertEqual(g.h._owners[g.h.operation_id], 'agent')
        g.call(g.A, 'measure.stop')
        r = g.tap_as(B, 'm_start')                                               # B's panel Start belongs to B
        self.assertEqual(r['status'], 'succeeded')
        self.assertEqual(g.h._owners[g.h.operation_id], 'agent2')
        self.assertEqual(B.poll(g.h.operation_id)['status'], 'running')
        with self.assertRaisesRegex(rt.ActionRejected, 'ACCESS_DENIED'):
            g.A.poll(g.h.operation_id)
        g.wait()
        g.tap('m_auto')                                                          # Automatic Meas.: the session closing the lid owns the run
        g.cell('sample')
        self.assertEqual(g.val('scan.phase'), 'running')
        self.assertEqual(g.h._owners[g.h.operation_id], 'agent')
        g.wait()
        g.tap('m_auto')
        g.call(g.A, 'thermal.condition', start_type=2, tolerance_k=0.1, stabilization_s=20.0)
        g.tap_as(B, 'm_start')                                                   # a start that waits for the temperature condition
        self.assertTrue(g.h.dev['pending'])
        g.step(21)
        op = g.h.operation_id
        self.assertEqual(g.val('scan.phase'), 'running')
        self.assertEqual(g.h._owners[op], 'agent2')
        self.assertEqual(B.poll(op)['status'], 'running')
        self.assertEqual(B.cancel(op, 'b2')['result']['phase'], 'cancelled')

    def test_another_agent_cannot_end_the_run(self):
        g = Rig()
        g.h.register_principal('agent2', visibility='agent')
        B = g.h.session('agent2')
        g.ready_for_samples()
        g.cell('sample')
        g.go_home()
        g.tap('home_measure')
        g.tap('m_start')
        op = g.h.operation_id
        keys = ('scan.phase', 'scan.completed_points', 'power.enabled', 'lid.opened', 'lamp.enabled', 'device.settings')
        before = {k: g.val(k) for k in keys}
        r = g.tap_as(B, 'm_stop')                                                # B's Stop key: the device's refusal, the run goes on
        self.assertFalse(r['result']['accepted'])
        self.assertEqual(g.dialog(), 'err_access_denied')
        self.assertEqual(g.overlay()['lines'], ["Another user's measurement is in progress."])
        g.ov('OK')
        for cap, args in (('acquisition.stop', {}), ('measure.stop', {}),
                          ('lamp.set', dict(enabled=False)), ('validation.cancel', {}), ('device.light_source', dict(na_lamp=False))):
            r = B.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        self.assertEqual({k: g.val(k) for k in keys}, before)
        self.assertEqual((g.h.operation_id, g.A.poll(op)['status']), (op, 'running'))
        self.assertTrue(g.tap('m_stop'))                                         # A stops its own run with the key
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')
        g.tap('m_start')                                                         # the evaluator may stop any run
        op2 = g.h.operation_id
        g.call(g.E, 'acquisition.stop')
        self.assertEqual(g.A.poll(op2)['status'], 'cancelled')
        g.call(g.A, 'thermal.condition', start_type=2, tolerance_k=0.1, stabilization_s=20.0)
        g.tap('m_start')                                                         # A's start waiting for the temperature condition
        self.assertFalse(g.tap_as(B, 'm_stop')['result']['accepted'])
        self.assertEqual(g.h.dev['pending']['owner'], 'agent')
        g.ov('OK')
        g.call(g.A, 'measure.stop')
        self.assertIsNone(g.h.dev['pending'])

    def test_lid_opening_is_physical(self):
        """Sweep 2026-10-19: opening the sample-compartment lid is a physical action, never refused; the lid interlock ends the run."""
        g = Rig()
        g.h.register_principal('agent2', visibility='agent')
        B = g.h.session('agent2')
        g.ready_for_samples()
        g.cell('sample')
        g.go_home()
        g.tap('home_measure')
        g.tap('m_start')
        op = g.h.operation_id
        self.assertEqual(g.A.poll(op)['status'], 'running')
        g.call(B, 'lid.set', opened=True)                                        # B opens the lid during A's measurement
        self.assertTrue(g.val('lid.opened'))
        self.assertEqual(g.val('safety.warning'), 'LID_OPEN')
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')                    # the device stops the run through its interlock
        self.assertEqual(g.h._owners[op], 'agent')

    def test_power_switch_is_physical(self):
        """Sweep 2026-10-19: the power switch cuts the power; another agent's switch-off is not refused and ends the run as a power loss."""
        for direct in (False, True):
            g = Rig()
            g.h.register_principal('agent2', visibility='agent')
            B = g.h.session('agent2')
            g.ready_for_samples()
            g.cell('sample')
            g.go_home()
            g.tap('home_measure')
            g.tap('m_start')
            op = g.h.operation_id
            if direct:
                g.call(B, 'power.set', enabled=False)
            else:
                r = B.invoke(instance_id=INST, capability_id='panel.switch', arguments=dict(input_id='power_switch', position='off'), request_id='b-sw')
                self.assertTrue(r['result']['accepted'])
            self.assertFalse(g.val('power.enabled'))
            self.assertEqual((g.val('safety.warning'), g.A.poll(op)['status']), ('POWER_OFF', 'cancelled'))

    def test_validation_repeats_belong_to_the_caller(self):
        g = Rig()
        g.register_plate('Quartz +10', '10')
        g.go_home()
        g.tap('home_validation')
        g.row('f_val_w', 'Quartz plates')
        g.ov('[ ] Quartz +10')
        g.ov('OK')
        g.enter('f_val_w', 'Repeat measurement', '3')
        g.tap('va_start')
        g.cell('blank')
        g.tap('va_ok')
        ops = set()
        for _ in range(40):
            g.step(1.0)
            ops.add(g.h.operation_id)
        self.assertGreaterEqual(len(ops), 3)                                     # three blank repeats, started by the device one after the other
        self.assertEqual({g.h._owners.get(o) for o in ops}, {'agent'})

    def test_help_page_from_the_manual(self):
        g = Rig()
        g.tap('home_measure')
        g.tap('m_menu')
        g.ov('Help')
        self.assertEqual(g.dialog(), 'menu_help')
        g.ov('Help')
        self.assertEqual(g.page(), ('main', 'help'))
        self.assertIsNone(g.frame()['ui'].get('unsupported'))
        self.assertEqual(g.cells_of('hp_list')[0], ['File > Save Result', 'Saves the measurement result.'])
        g.row('hp_list', 'Control > Zero Clear')                                # drag the list until the row shows
        rows = {c[0]: c[1] for c in g.cells_of('hp_list')}
        self.assertEqual(rows['Control > Zero Clear'], 'Sets the current optical rotation to 0.')
        self.assertEqual(g.widgets()['hp_list']['count'], 18)
        self.assertNotIn('Help', [u['item'] for u in rt.PANEL_SPEC['unsupported']])
        g.tap('hp_back')
        self.assertEqual(g.page(), ('main', 'measure'))


class RefusalsAndTimeouts(unittest.TestCase):
    def test_refused_start_leaves_the_state_unchanged(self):
        g = Rig()
        g.tap('home_measure')
        g.cell('blank')
        g.call(g.A, 'lid.set', opened=True)
        before = g.h.debug_snapshot()
        self.assertFalse(g.tap('m_blank'))
        self.assertEqual(g.dialog(), 'err_optics_not_ready')
        self.assertEqual(g.overlay()['lines'], ['Close the sample compartment lid.'])
        after = g.h.debug_snapshot()
        for snap in (before, after):                                            # the gesture itself: UI state, request cache, revision
            for k in ('panel_ui', '_requests', 'revision'):
                snap.pop(k)
        self.assertEqual(before, after)
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'OPTICS_NOT_READY')
        g.ov('OK')
        g.call(g.A, 'lid.set', opened=False)
        self.assertFalse(g.tap('m_start'))                                       # a sample needs the blank first ... (blank cell: wrong kind)
        self.assertEqual(g.dialog(), 'err_sample_kind_mismatch')

    def test_start_without_blank_and_correction(self):
        g = Rig()
        g.cell('sample')
        g.tap('home_measure')
        self.assertFalse(g.tap('m_start'))
        self.assertEqual(g.dialog(), 'err_blank_required')
        g.ov('OK')
        g.measure_blank()
        g.cell('sample')
        self.assertFalse(g.tap('m_start'))
        self.assertEqual(g.dialog(), 'err_quartz_calibration_required')

    def test_non_control_mode_and_light_source_window_timeout(self):
        g = Rig(on=False)
        g.switch('on')
        g.tap('ls_nc')
        g.step(9.5)
        self.assertEqual(g.page(), ('startup', 'light_source'))
        g.step(0.6)                                                              # 10 s after power-on: the selection applies itself
        self.assertEqual(g.page(), ('main', 'home'))
        self.assertTrue(g.val('device.settings')['non_control'])
        g.tap('home_measure')
        self.assertFalse(g.tap('m_blank'))
        self.assertEqual(g.dialog(), 'err_non_control_mode')
        self.assertEqual(g.text('m_status'), 'Non-Control Mode')

    def test_hardware_absent_messages(self):
        g = Rig()
        g.tap('home_analysis')
        self.assertFalse(g.tap('an_print'))
        self.assertEqual(g.dialog(), 'no_printer')
        self.assertEqual(g.frame()['ui']['unsupported'], 'no printer is connected to the simulated instrument')
        g.ov('OK')
        g.tap('an_export')
        self.assertFalse(g.ov('USB memory'))
        self.assertEqual(g.dialog(), 'no_usb')
        g.ov('OK')
        g.tap('an_export')
        g.ov('LIMS')
        self.assertEqual(g.overlay()['lines'], ['LIMS is not connected.'])

    def test_menus_have_no_time_out(self):
        g = Rig()
        g.tap('home_measure')
        g.tap('m_menu')
        g.ov('Control')
        g.step(600)
        self.assertEqual(g.dialog(), 'menu_control')
        g.ov('Close')                                                            # a submenu replaces the menu; Close leaves it
        self.assertIsNone(g.dialog())
        g.tap('m_menu')
        g.ov('Measure')
        g.ov('Monitor Size: Normal')
        self.assertEqual(g.frame()['ui']['vars']['monitor'], 'zoom')
        self.assertEqual(g.widgets()['m_value']['scale'], 9)

    def test_gestures_rejected_and_unknown(self):
        g = Rig()
        r = g.call(g.A, 'panel.touch', expect='rejected', display_id='lcd', x=1, y=1)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.touch', expect='rejected', display_id=SCR, x=1, y=1, to_x=5)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.switch', expect='rejected', input_id='power_switch', position='standby')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')   # evaluator only
        self.assertFalse(g.touch(799, 479))                                      # empty area of the home page


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig(gain=1.02)
            g.ready_for_samples()
            g.cell('sample')
            g.go_home()
            g.tap('home_measure')
            g.tap('m_start')
            g.step(15)
            return g
        a, b = scenario(), scenario()
        ia, ib = a.h.panel.render(SCR), b.h.panel.render(SCR)
        self.assertEqual((ia.dtype.name, ia.shape), ('uint8', (480, 800, 3)))
        self.assertTrue((ia == ib).all())
        self.assertEqual(a.h.panel.png_bytes(SCR), b.h.panel.png_bytes(SCR))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': None}
        shots['measure'] = a.h.panel.save_png(SCR, OUT / 'measure.png')
        a.tap('m_params')
        shots['menu'] = a.h.panel.save_png(SCR, OUT / 'menu.png')
        a.tap('pp0_cancel')
        a.tap('m_menu')
        a.ov('File')
        shots['menu_file'] = a.h.panel.save_png(SCR, OUT / 'menu_file.png')
        a.ov('Close')
        a.call(a.A, 'lid.set', opened=True)
        a.tap('m_start')
        shots['error'] = a.h.panel.save_png(SCR, OUT / 'error.png')
        a.ov('OK')
        a.go_home()
        shots['home'] = a.h.panel.save_png(SCR, OUT / 'home.png')
        a.tap('home_daily')
        shots['daily'] = a.h.panel.save_png(SCR, OUT / 'daily.png')
        for name, s in shots.items():
            data = (OUT / f'{name}.png').read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), s['sha256'])
            self.assertEqual((s['width'], s['height']), (800, 480))
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.switch('on')
        r2 = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        self.assertEqual(g.call(g.A, 'panel.capture', display_id=SCR), r2)

    def test_snapshot_keeps_the_panel_bound(self):
        g = Rig()
        g.call(g.A, 'lamp.set', expect='rejected', enabled=True)                 # a rejected call restores the snapshot (lid open)
        self.assertIs(g.h.panel.host, g.h)
        self.assertTrue(g.tap('home_measure'))
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        c.panel.touch(SCR, 400, 20)
        self.assertEqual(g.page(), ('main', 'measure'))

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS) | set(rt.DEVICE_CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.switch', 'panel.touch', 'panel.capture', 'panel.frame'})
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        m = rt.export_jasco_p4200_contract_metadata()
        for k in list(rt.PANEL_CAPS) + list(rt.DEVICE_CAPS):
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for sid in ('device.settings', 'device.display', 'device.data', 'device.admin'):
            self.assertIn(sid, c['state_slots'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/jasco_p4200.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('jasco_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
