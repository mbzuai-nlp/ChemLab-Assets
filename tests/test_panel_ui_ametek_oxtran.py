"""front-panel tests for CHEM-147 ametek_oxtran (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/ametek_oxtran.py):
python3 -m pytest tests/test_panel_ui_ametek_oxtran.py -q

Every gesture goes through the session API (panel.touch / panel.press / panel.switch, the runtime's normal invoke path); widgets are found by id or
label in the structured frame (panel.frame, evaluator) and tapped at their centre.  Themes: the six key procedures of the menu survey (load / unload a
cell, Auto Test, Advanced Test, monitor and override, sensor over-range recovery, shut-down); the other screens (sequential test table, saved methods,
OTR Adjustment, Instrument Status, reports, Help / About); panel = direct capability (same runtime state); refusals shown as the instrument's Error
Message dialog; unsupported printing / export; no screen time-out; render determinism and the evidence PNGs; contract entries."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.asset_runtime import ametek_oxtran as rt  # noqa: E402

pu = rt._panel_ui
INST = 'oxtran_01'
SCREEN = 'screen'
OUT = ROOT / 'outputs/panel_ui/ametek_oxtran'
PET = 'poly_ethylene_terephthalate'
C0 = 273.15
SECTION = {'adv': 'test', 'methods': 'test', 'seq_a': 'test', 'seq_b': 'test', 'cs_a': 'status', 'cs_b': 'status', 'is': 'status', 'report_a': 'status',
           'report_b': 'status', 'otr': 'tools', 'about': 'help'}                # the Icon-bar button ids of a page: ib_<section>_<icon>
SURVEY_TASKS = ['load or unload a test cell', 'start an Auto Test on a film', 'start an Advanced Test', 'monitor or override a test',
                'recover from sensor over-range', 'shut down for storage']
DONE_TASKS = set()


class Rig:
    """A runtime with an agent and an evaluator session; the operator's actions only through panel.* (the drawer and the film are host actions)."""

    def __init__(self, power=True):
        self.h = rt.AmetekOxtranRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        self.call(self.E, 'gas.configure', **rt.GAS_EXAMPLE)
        if power:
            self.switch('I')

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        if r['status'] in ('succeeded', 'accepted'):
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            assert cap == 'sim.step' or self.h.time_s == t0, cap                   # commands and gestures take no simulated time
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        self.call(self.E, 'sim.step', dt_s=float(dt))

    def run_until_done(self, cell='A', dt=1800.0, limit_h=200):
        t = 0.0
        while self.h._test_active(cell) and t < limit_h * 3600:
            self.step(dt)
            t += dt

    # ---- host / robot actions (not panel)
    def mount(self, cell='A', **kw):
        self.call(self.E, 'drawer.move', cell=cell, open=True)
        self.step(3.0)
        self.call(self.E, 'sample.mount', **dict(rt.MOUNT_EXAMPLE, cell=cell, template_id=PET, thickness_m=100e-6, **kw))
        self.call(self.E, 'drawer.move', cell=cell, open=False)
        self.step(3.0)

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCREEN]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        return self.frame()['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCREEN, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        """Tap a button of the top overlay (keyboard key, option, dialog button or row) by its label."""
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def row(self, wid, first):
        """Tap the row of a list / form widget whose first cell is this text (dragging the rows until it shows)."""
        for _ in range(12):
            w = self.widgets()[wid]
            firsts = [c[0] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, firsts)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def cells(self, wid):
        return {c[0]: (c[1] if len(c) > 1 else None) for c in self.widgets()[wid]['cells']}

    def type(self, text):
        """Type on the open keyboard (the shown text is replaced): upper case through Shift, digits through 12@ on the alpha keyboard."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard'
        labels = {b['label'] for b in ov['buttons']}
        if '7' in labels and 'q' not in labels and 'Q' not in labels:           # numeric keyboard
            if ov['field']:
                self.ov('Del')
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
            elif ch.isupper():
                if mode == 'sym':
                    self.ov('abc')
                if self.overlay()['mode'] != 'upper':
                    self.ov('Shift')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if self.overlay()['mode'] == 'upper':
                    self.ov('Shift')
                self.ov(ch)
        assert self.overlay()['field'] == text, (self.overlay()['field'], text)

    def enter(self, wid, label, text):
        self.row(wid, label)
        self.type(text)
        return self.ov('OK')

    def choose(self, wid, label, option):
        self.row(wid, label)
        return self.ov(option)

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power', position=pos)['accepted']

    def dialog(self):
        """(title, text) of the dialog on top (its lines joined: long messages wrap over several lines)."""
        ov = self.overlay()
        return None if ov is None or ov.get('kind') != 'dialog' else (ov['title'], ' '.join(ov['lines']))

    # ---- navigation shortcuts (all by taps)
    def icon(self, name):
        page = self.page()
        return self.tap(f'ib_{SECTION.get(page, page)}_{name}')

    def advanced(self, tab):
        self.icon('test')
        self.tap('tab_auto_tab_adv')
        assert self.page() == 'adv'
        self.tap(f'tab_adv_tab_{tab}')

    def load(self, cell='A', settle=3600.0):
        assert self.press(f'load_{cell.lower()}')
        self.step(settle)
        assert self.val(f'cell_{cell.lower()}.clamped')


def film_ready(cell='A', settle=3600.0):
    r = Rig()
    r.mount(cell)
    r.load(cell, settle)
    return r


# ---------------------------------------------------------------- the key procedures of the menu survey, panel gestures only
class SurveyProcedures(unittest.TestCase):
    def test_load_or_unload_a_test_cell(self):
        r = Rig()
        r.mount('A')
        self.assertTrue(r.press('load_a'))                                      # step 4: press Cell Load/Unload to clamp
        self.assertTrue(r.val('cell_a.clamp_moving'))
        r.step(5.0)
        self.assertTrue(r.val('cell_a.clamped'))
        lamp = r.frame()['displays']['lamp_a']
        self.assertEqual((lamp['on'], lamp['color']), (True, 'green'))
        self.assertFalse(r.frame()['displays']['lamp_b']['on'])
        self.assertTrue(r.press('load_a'))                                      # unload: unclamp, then the tray may be pulled
        r.step(5.0)
        self.assertFalse(r.val('cell_a.clamped'))
        self.assertFalse(r.frame()['displays']['lamp_a']['on'])
        r.call(r.E, 'drawer.move', cell='A', open=True)
        r.step(1.0)
        self.assertFalse(r.press('load_a'))                                     # tray not closed: the press is refused, no message
        self.assertIsNone(r.overlay())
        self.assertFalse(r.val('cell_a.clamp_moving'))
        DONE_TASKS.add('load or unload a test cell')

    def test_start_an_auto_test_on_a_film(self):
        r = film_ready()
        self.assertTrue(r.icon('test'))                                         # 1 the Test icon
        self.assertEqual(r.page(), 'test')
        self.assertTrue(r.enter('auto_cell_a', 'Sample ID', 'PET 100'))         # 2 sample identification and thickness on the Cell screen
        self.assertTrue(r.enter('auto_cell_a', 'Sample Thickness', '100'))
        self.assertEqual(r.val('cell_a.identification')['sample_id'], 'PET 100')
        self.assertAlmostEqual(r.h.cells['A']['cfg']['declared_thickness_m'], 100e-6)
        self.assertTrue(r.tap('tab_auto_tab_I'))                                # 3 temperature and RH on the Instrument screen
        self.assertTrue(r.enter('auto_inst', 'Cell Temperature', '23'))
        self.assertTrue(r.enter('auto_inst', 'Test Gas RH', '0'))
        self.assertTrue(r.enter('auto_inst', 'Carrier Gas RH', '0'))
        self.assertEqual(r.cells('auto_inst'), {'Cell Temperature': '23 C', 'Test Gas RH': '0 %', 'Carrier Gas RH': '0 %'})
        self.assertTrue(r.tap('start_start_auto_a'))                            # 4 start the Auto Test
        self.assertEqual(r.page(), 'cs_a')
        info = r.val('cell_a.test_info')
        self.assertEqual((info['method'], info['sample_id']), ('auto', 'PET 100'))
        self.assertEqual(r.text('cs_a_method'), 'Method: Auto Test')
        r.run_until_done()
        self.assertEqual(r.text('cs_a_state'), 'State: Test Complete')
        self.assertEqual(r.text('cs_a_validity'), 'Result: Valid')
        res = r.call(r.A, 'test.result', cell='A')
        self.assertTrue(res['converged'])
        self.assertEqual(r.text('cs_a_final'), f'Final OTR: {res["final_otr_cc_m2_day"]:.3f}')
        DONE_TASKS.add('start an Auto Test on a film')

    def test_start_an_advanced_test(self):
        r = film_ready()
        r.advanced('A')                                                         # 1-2 Test icon, page to the Advanced Test screen
        for label, text in (('Test ID', 'T-01'), ('Sample ID', 'S-01'), ('Material ID', 'PET')):
            self.assertTrue(r.enter('adv_cell_a', label, text))                  # 3 Cell parameters
        self.assertTrue(r.choose('adv_cell_a', 'Package / Film', 'Film'))
        self.assertTrue(r.enter('adv_cell_a', 'Area', '50'))
        self.assertTrue(r.enter('adv_cell_a', 'Sample Thickness', '100'))
        self.assertTrue(r.choose('adv_cell_a', 'Conditioning', 'On'))
        self.assertTrue(r.enter('adv_cell_a', 'Conditioning Hours', '0.5'))
        r.tap('tab_adv_tab_I')                                                  # Instrument parameters
        self.assertTrue(r.enter('adv_inst', 'Permeant Concentration', '100'))
        self.assertTrue(r.choose('adv_inst', 'Instrument ReZero', 'On'))
        self.assertTrue(r.enter('adv_inst', 'ReZero Frequency', '2'))
        self.assertTrue(r.enter('adv_inst', 'ReZero Exam Minutes', '30'))
        self.assertTrue(r.choose('adv_inst', 'Barometer', 'Auto'))
        r.tap('tab_adv_tab_TA')                                                 # Test parameters
        self.assertTrue(r.choose('adv_test_a', 'Test Mode', 'Standard'))
        self.assertTrue(r.enter('adv_test_a', 'Number of Cycles', '3'))
        self.assertTrue(r.enter('adv_test_a', 'Exam Minutes', '30'))
        self.assertTrue(r.choose('adv_test_a', 'Individual Zero', 'On'))
        self.assertTrue(r.choose('adv_test_a', 'Individual Zero Mode', 'End'))
        cfg = r.h.cells['A']['cfg']
        self.assertEqual({k: cfg[k] for k in ('test_mode', 'cycles', 'exam_minutes', 'conditioning_hours', 'individual_zero', 'report_basis', 'declared_area_m2')},
                         dict(test_mode='standard', cycles=3, exam_minutes=30.0, conditioning_hours=0.5, individual_zero='end', report_basis='area',
                              declared_area_m2=50e-4))
        self.assertEqual(r.h.instrument, dict(rt.DEFAULT_INSTRUMENT))
        self.assertTrue(r.tap('adv_save'))                                      # 4 Save Method
        self.assertEqual(r.dialog()[0], 'Save Method')
        self.assertTrue(r.ov('Method Name'))
        r.type('PET std')
        r.ov('OK')
        self.assertTrue(r.ov('Save'))
        self.assertIsNone(r.overlay())
        self.assertEqual(r.val('methods.saved'), ['PET std'])
        self.assertTrue(r.tap('start_start_a'))                                 # 5 start the test on the desired cell
        self.assertEqual((r.page(), r.val('cell_a.phase')), ('cs_a', 'conditioning'))
        r.run_until_done()
        res = r.call(r.A, 'test.result', cell='A')
        self.assertEqual((res['validity'], res['exam_count']), ('valid', 3))
        self.assertIsNotNone(res['individual_zero_mol_m2_s'])
        info = r.val('cell_a.test_info')
        self.assertEqual((info['test_id'], info['sample_id'], info['material_id'], info['method']), ('T-01', 'S-01', 'PET', 'advanced'))
        DONE_TASKS.add('start an Advanced Test')

    def test_monitor_or_override_a_test(self):
        r = film_ready()
        r.advanced('TA')
        r.choose('adv_test_a', 'Test Mode', 'Continuous')
        r.tap('tab_adv_tab_A')
        r.choose('adv_cell_a', 'Conditioning', 'On')
        r.tap('start_start_a')
        self.assertTrue(r.icon('status'))                                       # 1 Status icon: Cell Status / Instrument Status
        self.assertEqual(r.dialog()[0], 'Status')
        self.assertTrue(r.ov('Cell Status'))
        self.assertEqual(r.page(), 'cs_a')
        self.assertEqual(r.text('cs_a_state'), 'State: Conditioning')
        self.assertTrue(r.tap('cs_a_advance'))                                  # 2 Advance: conditioning -> testing
        self.assertEqual(r.val('cell_a.phase'), 'testing')
        r.step(4 * 3600.0)
        n = r.val('cell_a.exam_count')
        self.assertGreater(n, 1)
        self.assertTrue(r.tap('cs_a_goto'))                                     # GoTo ByPass
        self.assertTrue(r.ov('ByPass'))
        self.assertTrue(r.val('sequence.held'))
        r.icon('status')
        r.ov('Instrument Status')
        self.assertEqual(r.text('is_route'), 'Sensor: Bypass (GoTo)')
        r.step(2 * 3600.0)
        self.assertEqual(r.val('cell_a.exam_count'), n)
        self.assertTrue(r.tap('is_goto'))                                       # GoTo ReZero
        self.assertTrue(r.ov('ReZero'))
        self.assertEqual(r.text('is_route'), 'Sensor: ReZero')
        r.step(3 * 3600.0)
        self.assertGreater(r.val('cell_a.exam_count'), n)
        r.tap('is_cell_a')
        self.assertTrue(r.tap('cs_a_advance'))                                  # Advance: continuous test -> Test Complete
        self.assertEqual(r.text('cs_a_state'), 'State: Test Complete')
        self.assertFalse(r.tap('cs_a_abort'))                                   # nothing to abort now
        r.tap('cs_a_report')
        self.assertEqual(r.page(), 'report_a')
        res = r.call(r.A, 'test.result', cell='A')
        self.assertIn(f'{res["final_otr_cc_m2_day"]:.4f}', r.text('rp_a_final'))
        self.assertEqual(len(r.widgets()['rp_a_table']['cells'][0]), 5)
        r.tap('rp_a_back')
        r.tap('ib_status_test')
        r.tap('tab_auto_tab_adv')
        r.tap('start_start_a')
        self.assertTrue(r.tap('cs_a_abort'))                                    # Abort
        self.assertEqual(r.text('cs_a_state'), 'State: Aborted')
        self.assertEqual(r.text('cs_a_validity'), 'Result: Aborted')
        DONE_TASKS.add('monitor or override a test')

    def test_recover_from_sensor_over_range(self):
        r = Rig()
        r.load('A', settle=5.0)                                                 # clamped with no barrier present
        r.advanced('TA')
        r.choose('adv_test_a', 'Test Mode', 'Continuous')
        r.tap('start_start_a')
        r.step(3 * 3600.0)
        self.assertEqual(r.val('cell_a.phase'), 'failed')
        lamp = r.frame()['displays']['lamp_a']                                  # 1 red Cell Status indicator and the Home error indicator
        self.assertEqual((lamp['on'], lamp['color']), (True, 'red'))
        r.icon('home')
        self.assertIn('home_error', r.widgets())
        self.assertEqual(r.text('home_tile_a'), 'A   Failed')
        r.tap('home_tile_a')                                                    # 2 examine the Cell Status screen
        self.assertEqual(r.text('cs_a_reason'), 'Failed: sensor over-range')
        self.assertEqual(r.text('cs_a_validity'), 'Result: Sensor over-range')
        self.assertTrue(r.press('load_a'))                                      # 3 fix the cause: unload, mount a film, reload
        r.step(5.0)
        r.mount('A')
        r.load('A')
        self.assertTrue(r.icon('test'))
        r.tap('tab_auto_tab_adv')
        self.assertTrue(r.tap('start_start_a'))                                 # restart the failed cell
        r.step(3 * 3600.0)
        self.assertEqual(r.val('cell_a.phase'), 'testing')
        self.assertEqual(r.frame()['displays']['lamp_a']['color'], 'green')
        r.icon('home')
        self.assertNotIn('home_error', r.widgets())
        DONE_TASKS.add('recover from sensor over-range')

    def test_shut_down_for_storage(self):
        r = film_ready()
        r.advanced('I')
        r.enter('adv_inst', 'Cell Temperature', '35')
        r.icon('test')
        r.tap('tab_auto_tab_adv')
        r.tap('start_start_a')
        r.step(3 * 3600.0)
        r.tap('cs_a_advance')                                                   # 1 end all tests; the sensor goes to Bypass
        self.assertNotIn(r.val('cell_a.phase'), ('zero_start', 'conditioning', 'testing', 'zero_end'))
        r.step(60.0)
        r.icon('status')
        r.ov('Instrument Status')
        self.assertEqual(r.text('is_route'), 'Sensor: Bypass')
        r.advanced('I')                                                         # 2 set point to ambient and wait
        r.enter('adv_inst', 'Cell Temperature', f'{rt.PLANT_EXAMPLE["ambient_k"] - C0:.1f}')
        self.assertAlmostEqual(r.val('thermal.target_k'), rt.PLANT_EXAMPLE['ambient_k'])
        r.step(4 * 3600.0)
        self.assertLess(abs(r.val('truth.block_temperature_k') - rt.PLANT_EXAMPLE['ambient_k']), 0.3)
        self.assertTrue(r.switch('O'))                                          # 4 power off (3: draining the humidifiers is not modelled)
        self.assertFalse(r.val('power.enabled'))
        f = r.frame()
        self.assertEqual((f['screen'], f['displays'][SCREEN]['brightness']), ('dark', 0))
        self.assertFalse(r.touch(500, 300))
        self.assertFalse(r.press('load_a'))
        self.assertEqual(int(r.h.panel.render(SCREEN).max()), 0)
        DONE_TASKS.add('shut down for storage')


# ---------------------------------------------------------------- the other screens
class Screens(unittest.TestCase):
    def test_sequential_test_table(self):
        r = film_ready()
        r.advanced('TA')
        r.choose('adv_test_a', 'Test Mode', 'Standard')
        r.enter('adv_test_a', 'Number of Cycles', '2')
        self.assertTrue(r.choose('adv_test_a', 'Sequential Test', 'On'))
        self.assertTrue(r.val('cell_a.sequence')['enabled'])
        self.assertTrue(r.row('adv_test_a', 'Sequential Test Parameters'))
        self.assertEqual(r.page(), 'seq_a')
        for temp in ('23', '30', '35'):
            self.assertTrue(r.tap('seq_a_add'))
            self.assertEqual(r.overlay()['title'], f'Sequence test {len(r.val("cell_a.sequence")["steps"]) + 1}')
            r.ov('Test Temperature')
            r.type(temp)
            r.ov('OK')
            self.assertTrue(r.ov('Save'))
        self.assertEqual([s['target_k'] for s in r.val('cell_a.sequence')['steps']], [296.15, 303.15, 308.15])
        r.row('seq_a_list', '2')                                                # edit row 2: carrier RH 50 %
        r.ov('Carrier Gas RH')
        r.type('50')
        r.ov('OK')
        r.ov('Save')
        self.assertEqual(r.val('cell_a.sequence')['steps'][1]['carrier_rh_fraction'], 0.5)
        r.row('seq_a_list', '3')
        self.assertTrue(r.ov('Delete'))
        self.assertEqual(len(r.val('cell_a.sequence')['steps']), 2)
        r.tap('seq_a_back')
        r.tap('start_start_a')
        self.assertEqual(r.text('cs_a_method'), 'Method: Advanced  sequence 1')
        op1 = r.val('cell_a.test_info')['operation_id']
        while r.val('cell_a.test_info')['operation_id'] == op1:
            r.step(1800.0)
        self.assertEqual(r.text('cs_a_state'), 'State: Waiting (Sequence)')        # row 2: waiting for 30 C and 50 % carrier RH
        self.assertEqual((r.val('thermal.target_k'), r.val('humidity.carrier_target_fraction')), (303.15, 0.5))
        r.run_until_done()
        self.assertEqual(r.call(r.A, 'test.result', cell='A')['conditions']['target_k'], 303.15)
        self.assertIsNone(r.val('cell_a.sequence')['current_step'])

    def test_saved_methods(self):
        r = Rig()
        r.advanced('TA')
        r.enter('adv_test_a', 'Exam Minutes', '45')
        r.tap('tab_adv_tab_I')
        r.enter('adv_inst', 'Cell Temperature', '30')
        r.tap('adv_save')
        r.ov('Method Name')
        r.type('M1')
        r.ov('OK')
        r.ov('Save')
        r.enter('adv_inst', 'Cell Temperature', '23')
        self.assertTrue(r.tap('adv_methods'))
        self.assertEqual(r.page(), 'methods')
        self.assertFalse(r.tap('methods_recall_b'))                             # nothing selected yet
        r.row('methods_list', 'M1')
        self.assertTrue(r.tap('methods_recall_b'))
        self.assertEqual((r.page(), r.h.cells['B']['cfg']['exam_minutes'], r.val('thermal.target_k')), ('adv', 45.0, 303.15))
        r.tap('adv_methods')
        r.row('methods_list', 'M1')
        self.assertFalse(r.tap('methods_export'))                               # no USB memory: the device's message, nothing exported
        self.assertEqual(r.dialog(), ('Export', 'No USB memory device is connected.'))
        self.assertTrue(r.frame()['ui']['unsupported'])
        r.ov('Dismiss')
        self.assertTrue(r.tap('methods_delete'))
        self.assertEqual(r.val('methods.saved'), [])
        self.assertEqual(r.widgets()['methods_list'].get('empty_text'), 'No saved methods')

    def test_otr_adjustment_screens(self):
        r = film_ready(settle=4 * 3600.0)
        r.advanced('TA')
        r.choose('adv_test_a', 'Test Mode', 'Convergence By Cycles')
        r.tap('start_start_a')
        r.run_until_done()
        measured = r.call(r.A, 'test.result', cell='A')['final_otr_cc_m2_day']
        r.icon('tools')
        self.assertTrue(r.tap('tools_otr'))
        self.assertEqual(r.page(), 'otr')
        r.tap('otr_create')
        r.ov('Record Name')
        r.type('Cert 1')
        r.ov('OK')
        r.ov('Certified Value')
        r.type(f'{measured * 1.03:.4f}')
        r.ov('OK')
        self.assertTrue(r.ov('Save'))
        rec = r.val('sensor.adjustment_records')[0]
        self.assertEqual((rec['name'], rec['assigned']), ('Cert 1', False))
        self.assertAlmostEqual(rec['gain'], round(measured * 1.03, 4) / measured, places=9)
        self.assertEqual(r.cells('otr_list')['Cert 1'], f'{rec["gain"]:.4f}')
        r.row('otr_list', 'Cert 1')
        self.assertTrue(r.tap('otr_assign'))
        self.assertEqual(r.val('sensor.adjustment')['name'], 'Cert 1')
        self.assertFalse(r.tap('otr_delete'))                                   # assigned: Error Message
        self.assertEqual(r.dialog()[1], 'Error RECORD_ASSIGNED The record is assigned. Inactivate it before deleting it.')
        r.ov('Dismiss')
        self.assertTrue(r.tap('otr_inactivate'))
        self.assertEqual(r.val('sensor.adjustment'), dict(name=None, gain=1.0))
        self.assertTrue(r.tap('otr_delete'))
        self.assertEqual(r.val('sensor.adjustment_records'), [])
        r.tap('otr_create')                                                     # a name is required
        self.assertFalse(r.ov('Save'))
        self.assertEqual(r.dialog(), ('Error Message', 'Enter a name.'))

    def test_instrument_status_help_and_about(self):
        r = film_ready()
        r.icon('status')
        r.ov('Instrument Status')
        self.assertEqual(r.text('is_temp'), f'Cell Temperature: {r.val("thermal.indicated_k") - C0:.2f} C')
        self.assertEqual(r.text('is_gas'), 'Gas supply: OK')
        self.assertEqual(r.text('is_adjust'), 'Sensor adjustment: None')
        self.assertTrue(r.tap('tb_help'))
        self.assertEqual(r.dialog()[0], 'Help')
        r.ov('Help')
        self.assertEqual(r.page(), 'help')
        w = r.widgets()['help_topics']
        x, y, ww, hh = w['rect']
        r.touch(x + 10, y + 5 * 40 + 20)                                        # topic 6
        self.assertTrue(r.text('help_text').startswith('Troubleshooting:'))
        r.tap('tb_help')
        r.ov('About')
        self.assertEqual((r.text('about_model'), r.text('about_id')), ('OX-TRAN Model 2/22 L', f'Instrument: {INST}'))
        self.assertTrue(r.tap('tb_close'))                                      # Close: back to Home
        self.assertEqual(r.page(), 'home')

    def test_every_page_renders(self):
        r = film_ready()
        for pid in rt.PANEL_SPEC['screens']['main']['nav']['pages']:
            r.h.panel.ui['page'] = pid
            img = r.h.panel.render(SCREEN)
            self.assertEqual(img.shape, (600, 1024, 3), pid)
            self.assertTrue(r.frame()['displays'][SCREEN]['title'], pid)


# ---------------------------------------------------------------- panel = direct command
class Equivalence(unittest.TestCase):
    def test_parameter_screens_equal_direct_commands(self):
        a, b = Rig(), Rig()
        a.advanced('A')
        a.enter('adv_cell_a', 'Test ID', 'X1')
        a.choose('adv_cell_a', 'Package / Film', 'Package')
        a.enter('adv_cell_a', 'Sample Thickness', '25')
        a.tap('tab_adv_tab_I')
        a.enter('adv_inst', 'Cell Temperature', '28.5')
        a.enter('adv_inst', 'Test Gas RH', '60')
        a.enter('adv_inst', 'Carrier Gas RH', '40')
        a.choose('adv_inst', 'Barometer', 'Manual')
        a.enter('adv_inst', 'Barometric Pressure', '740')
        a.tap('tab_adv_tab_TB')
        a.choose('adv_test_b', 'Test Mode', 'Convergence By Hours')
        a.enter('adv_test_b', 'Convergence Hours', '6')
        a.choose('adv_test_b', 'Individual Zero', 'On')
        a.choose('adv_test_b', 'Individual Zero Mode', 'Beginning')
        b.call(b.A, 'cell.set_identification', cell='A', test_id='X1', sample_id='', material_id='')
        b.call(b.A, 'test.configure', **dict(rt.DEFAULT_CFG, cell='A', report_basis='package'))
        b.call(b.A, 'test.configure', **dict(rt.DEFAULT_CFG, cell='A', report_basis='package', declared_thickness_m=25e-6))
        b.call(b.A, 'thermal.set_temperature', target_k=301.65)
        b.call(b.A, 'humidity.set_streams', test_rh_fraction=0.6, carrier_rh_fraction=0.4)
        b.call(b.A, 'instrument.configure', **dict(rt.DEFAULT_INSTRUMENT, barometer_mode='manual', manual_barometer_pa=a.h.instrument['manual_barometer_pa']))
        b.call(b.A, 'test.configure', **dict(rt.DEFAULT_CFG, cell='B', test_mode='convergence_hours', convergence_count=6, individual_zero='start'))
        self.assertAlmostEqual(a.h.instrument['manual_barometer_pa'], 740 * 133.322368, delta=0.06)
        ra, rb = a.h.read('agent'), b.h.read('agent')
        for k in ('clock.time_s',):
            ra.pop(k), rb.pop(k)
        self.assertEqual(ra, rb)
        self.assertEqual((a.h.cells['A']['cfg'], a.h.cells['B']['cfg'], a.h.instrument), (b.h.cells['A']['cfg'], b.h.cells['B']['cfg'], b.h.instrument))

    def test_start_abort_and_goto_equal_direct_commands(self):
        a, b = film_ready(), film_ready()
        a.advanced('A')
        a.tap('start_start_a')                                                  # never-edited parameters: the defaults on the screens
        b.call(b.A, 'instrument.configure', **rt.DEFAULT_INSTRUMENT)
        b.call(b.A, 'test.configure', **dict(rt.DEFAULT_CFG, cell='A'))
        b.call(b.A, 'test.start', cell='A')
        for r in (a, b):
            r.step(2 * 3600.0)
        a.tap('cs_a_goto')
        a.ov('ByPass')
        b.call(b.A, 'sequence.goto', target='bypass')
        for r in (a, b):
            r.step(1800.0)
        a.tap('cs_a_abort')
        b.call(b.A, 'test.abort', cell='A')
        self.assertEqual(a.call(a.A, 'test.result', cell='A'), b.call(b.A, 'test.result', cell='A'))
        ra, rb = a.h.read('evaluator'), b.h.read('evaluator')
        self.assertEqual(ra, rb)


# ---------------------------------------------------------------- refusals, unsupported functions, time-outs
class Rules(unittest.TestCase):
    def test_refusals_show_the_error_message_dialog(self):
        r = Rig()
        r.advanced('A')
        self.assertFalse(r.tap('start_start_a'))                                # cell not clamped (the screens' parameters are written first)
        self.assertTrue(r.val('instrument.configured') and r.val('cell_a.method_configured'))
        r.ov('Dismiss')
        before = r.h.read('debug')
        self.assertFalse(r.tap('start_start_a'))
        self.assertEqual(r.dialog(), ('Error Message', 'Error CELL_NOT_CLAMPED The test cell is not loaded. Press the Cell Load/Unload button to clamp it.'))
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'CELL_NOT_CLAMPED')
        self.assertEqual(r.page(), 'adv')                                       # stays on the screen
        self.assertTrue(r.ov('Dismiss'))
        self.assertIsNone(r.overlay())
        r.tap('tab_adv_tab_I')
        self.assertFalse(r.enter('adv_inst', 'Test Gas RH', '3'))               # inside the uncontrolled 0-5 % band
        self.assertTrue(r.dialog()[1].startswith('Error RH_OUTSIDE_CONTROLLED_RANGE '))
        r.ov('Dismiss')
        after = r.h.read('debug')
        for k in ('clock.time_s',):
            before.pop(k), after.pop(k)
        self.assertEqual(before, after)
        self.assertFalse(r.enter('adv_inst', 'Cell Temperature', '50'))         # out of the field's range: the keyboard says so, nothing written
        self.assertEqual(r.overlay()['error'], 'Out of range')
        r.ov('Cancel')
        self.assertEqual(r.val('thermal.target_k'), rt.PLANT_EXAMPLE['ambient_k'])

    def test_busy_while_a_test_runs(self):
        r = film_ready()
        r.advanced('A')
        r.tap('start_start_a')
        r.advanced('TA')
        exam = r.h.cells['A']['cfg']['exam_minutes']
        self.assertFalse(r.enter('adv_test_a', 'Exam Minutes', '60'))
        self.assertTrue(r.dialog()[1].startswith('Error BUSY '))
        r.ov('Dismiss')
        self.assertEqual(r.h.cells['A']['cfg']['exam_minutes'], exam)
        self.assertEqual(r.cells('adv_test_a')['Exam Minutes'], f'{exam:g} min')

    def test_another_agents_test_is_view_only_on_the_panel(self):
        r = film_ready()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.advanced('A')
        r.tap('start_start_a')                                                  # agent A starts the test on the screen: the test is A's
        r.step(600.0)
        self.assertIn(r.val('cell_a.phase'), ('zero_start', 'conditioning', 'testing', 'zero_end'))

        def b_tap(wid):
            x, y, w, h = r.widgets()[wid]['rect']
            res = B.invoke(instance_id=INST, capability_id='panel.touch', arguments=dict(display_id=SCREEN, x=x + w // 2, y=y + h // 2), request_id=f'b{wid}')
            return res['result']['accepted']
        self.assertEqual(r.page(), 'cs_a')
        before = r.h.read('debug')
        self.assertFalse(b_tap('cs_a_abort'))                                   # agent B: Abort of A's test is refused with the Error Message dialog
        self.assertEqual(r.dialog(), ('Error Message', 'Error ACCESS_DENIED This test was started by another user. It can be viewed, not changed.'))
        r.ov('Dismiss')
        self.assertFalse(b_tap('cs_a_advance'))
        r.ov('Dismiss')
        self.assertEqual(r.h.read('debug'), before)                             # state unchanged
        self.assertEqual(B.invoke(instance_id=INST, capability_id='test.abort', arguments=dict(cell='A'), request_id='b_direct')['error']['code'],
                         'ACCESS_DENIED')                                       # the same rule as a direct call
        self.assertTrue(b_tap('cs_a_report'))                                   # viewing is allowed
        self.assertEqual(r.page(), 'report_a')
        r.tap('rp_a_back')
        self.assertTrue(r.tap('cs_a_abort'))                                    # agent A may abort its own test
        self.assertEqual(r.text('cs_a_state'), 'State: Aborted')

    def test_power_switch_is_physical_for_everyone(self):
        """Lead clarification 10-19: the I/O power switch is a hard switch: another agent may switch it off; the running test is cancelled
        (POWER_OFF) as on a power loss."""
        r = film_ready()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.advanced('A')
        r.tap('start_start_a')                                                  # agent A's test
        r.step(600.0)
        self.assertTrue(r.h._test_active('A'))
        res = B.invoke(instance_id=INST, capability_id='panel.switch', arguments=dict(input_id='power', position='O'), request_id='b_power')
        self.assertTrue(res['result']['accepted'])
        self.assertFalse(r.val('power.enabled'))
        self.assertFalse(r.h._test_active('A'))                                 # cancelled as on a power loss

    def test_another_agents_test_cannot_seed_an_adjustment_record(self):
        """Sync audit 2026-10-19: an OTR Adjustment record returns the cell's measured value, so it follows test.result (owner only)."""
        r = film_ready(settle=4 * 3600.0)
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.advanced('TA')
        r.choose('adv_test_a', 'Test Mode', 'Convergence By Cycles')
        r.tap('start_start_a')                                                  # agent A's test
        r.run_until_done()
        measured = r.call(r.A, 'test.result', cell='A')['final_otr_cc_m2_day']
        self.assertEqual(r.call(B, 'test.result', expect='rejected', cell='A')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call(B, 'sensor.adjustment_create', expect='rejected', name='B 1', cell='A', reference_value=measured)['error']['code'],
                         'ACCESS_DENIED')                                       # direct
        A, r.A = r.A, B                                                         # agent B on the screen
        r.icon('tools')
        r.tap('tools_otr')
        r.tap('otr_create')
        r.ov('Record Name')
        r.type('B 1')
        r.ov('OK')
        r.ov('Certified Value')
        r.type(f'{measured:.4f}')
        r.ov('OK')
        self.assertFalse(r.ov('Save'))                                          # panel: the same rule, Error Message
        self.assertEqual(r.dialog()[0], 'Error Message')
        self.assertIn('ACCESS_DENIED', r.dialog()[1])
        self.assertEqual(r.val('sensor.adjustment_records'), [])                 # nothing stored
        r.ov('Dismiss')
        r.A = A
        rec = r.call(A, 'sensor.adjustment_create', name='A 1', cell='A', reference_value=measured * 1.02)
        self.assertAlmostEqual(rec['gain'], 1.02, places=9)                     # the owner may

    def test_printing_needs_a_printer(self):
        r = film_ready()
        self.assertFalse(r.tap('tb_print'))
        self.assertEqual(r.dialog(), ('Print', 'No printer is connected.'))
        self.assertTrue(r.frame()['ui']['unsupported'])
        r.ov('Dismiss')
        r.tap('home_tile_a')
        r.tap('cs_a_report')
        self.assertFalse(r.tap('rp_a_print'))
        self.assertEqual(r.dialog()[0], 'Print')
        r.ov('Dismiss')
        self.assertFalse(r.tap('rp_a_export'))
        self.assertEqual(r.dialog()[1], 'No USB memory device is connected.')

    def test_no_screen_time_out_and_power_cycle(self):
        r = Rig()
        r.advanced('I')
        r.step(3 * 3600.0)
        self.assertEqual((r.page(), r.frame()['ui']['vars']['adv_tab']), ('adv', 'I'))   # no time-out back to Home
        self.assertTrue(r.switch('O'))
        self.assertTrue(r.switch('I'))
        self.assertEqual((r.frame()['screen'], r.page()), ('main', 'home'))     # start-up shows Home
        self.assertTrue(r.switch('I'))                                          # already on: nothing changes
        self.assertEqual(r.val('power.enabled'), True)

    def test_unknown_gestures_are_invalid(self):
        r = Rig()
        for cap, args in (('panel.touch', dict(display_id=SCREEN, x=1024, y=10)), ('panel.press', dict(input_id='power')),
                          ('panel.switch', dict(input_id='power', position='X'))):
            res = r.call(r.A, cap, expect='rejected', **args)
            self.assertEqual(res['error']['code'], 'INVALID_ARGUMENT')
        res = r.call(r.A, 'panel.frame', expect='rejected')
        self.assertEqual(res['error']['code'], 'ACCESS_DENIED')                 # the structured frame is for the evaluator
        h = rt.AmetekOxtranRuntime('x')
        with self.assertRaises(rt.ActionRejected) as e:
            h.invoke('panel.press', input_id='load_a')
        self.assertEqual(str(e.exception), 'PLANT_REQUIRED')


# ---------------------------------------------------------------- rendering, capture, contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = film_ready()
            g.advanced('A')
            g.enter('adv_cell_a', 'Sample ID', 'PET 100')
            g.tap('start_start_a')
            g.step(6 * 3600.0)
            g.icon('home')
            return g
        a, b = scenario(), scenario()
        for d in rt.PANEL_SPEC['displays']:
            ia, ib = a.h.panel.render(d), b.h.panel.render(d)
            self.assertEqual((ia.dtype.name, ia.shape[2]), ('uint8', 3))
            self.assertTrue((ia == ib).all(), d)
            self.assertTrue((ia == a.h.panel.render(d)).all(), d)
        self.assertEqual(a.h.panel.png_bytes(SCREEN), b.h.panel.png_bytes(SCREEN))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = dict(home=a.h.panel.save_png(SCREEN, OUT / 'home.png'))
        a.advanced('I')
        shots['menu'] = a.h.panel.save_png(SCREEN, OUT / 'menu.png')            # Advanced Test, Instrument screen
        a.tap('tab_adv_tab_TA')
        shots['test_screen'] = a.h.panel.save_png(SCREEN, OUT / 'test_screen.png')
        a.icon('status')
        a.ov('Cell Status')
        shots['cell_status'] = a.h.panel.save_png(SCREEN, OUT / 'cell_status.png')
        a.tap('tab_view_a_table')
        shots['cell_status_table'] = a.h.panel.save_png(SCREEN, OUT / 'cell_status_table.png')
        a.icon('test')
        a.tap('tab_auto_tab_adv')
        a.tap('start_start_b')                                                  # cell B is not loaded
        self.assertTrue(a.dialog()[1].startswith('Error CELL_NOT_CLAMPED '))
        shots['error'] = a.h.panel.save_png(SCREEN, OUT / 'error.png')
        a.ov('Dismiss')
        a.tap('tab_adv_tab_I')
        a.row('adv_inst', 'Cell Temperature')
        shots['keyboard'] = a.h.panel.save_png(SCREEN, OUT / 'keyboard.png')
        for name, s in shots.items():
            data = (OUT / f'{name}.png').read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), s['sha256'])
            self.assertEqual((s['width'], s['height']), (1024, 600))
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(power=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=SCREEN)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.switch('I')
        r2 = g.call(g.A, 'panel.capture', display_id=SCREEN)
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        self.assertEqual(g.call(g.A, 'panel.capture', display_id=SCREEN)['image'], r2['image'])   # same screen, same file
        r3 = g.call(g.A, 'panel.capture', display_id='lamp_a')
        self.assertEqual((r3['image']['width'], r3['image']['height']), (48, 16))

    def test_copied_runtime_keeps_its_own_panel(self):
        g = Rig()
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        c.panel.ui['page'] = 'tools'
        self.assertEqual(g.page(), 'home')

    def test_spec_and_contract(self):
        spec = rt.PANEL_SPEC
        self.assertEqual(pu.check_spec(spec, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.switch', 'panel.touch', 'panel.capture', 'panel.frame'})
        self.assertTrue(spec['approximate'])
        for fid, f in spec['forms'].items():
            for fld in f['fields']:
                self.assertTrue(fld.get('evidence') or fld.get('assumption'), (fid, fld['id']))
                for w in pu._writes(fld.get('write')):
                    self.assertIn(w['capability'], rt.CAPS)
        for pid, p in spec['screens']['main']['nav']['pages'].items():
            self.assertTrue(p.get('evidence'), pid)
        for did, d in spec['dialogs'].items():
            self.assertTrue(d.get('evidence'), did)
        text = (rt.ASSET / 'panel_ui.json').read_text()
        self.assertNotIn(str(Path.home()), text)
        c = json.loads((rt.ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        meta = rt.export_panel_contract_metadata()
        for k in rt.PANEL_CAPS:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], meta[k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        m = rt.export_ametek_oxtran_contract_metadata()
        for k in rt.CAPS:
            self.assertIn(k, c['capabilities'])
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/ametek_oxtran.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])            # the shared engine is not pinned per asset
        self.assertIsNone(c['artifacts']['panel_engine_tests']['sha256'])
        spec_ = importlib.util.spec_from_file_location('oxtran_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        self.assertEqual(sorted(set(SURVEY_TASKS) - DONE_TASKS), [])


if __name__ == '__main__':
    unittest.main()
