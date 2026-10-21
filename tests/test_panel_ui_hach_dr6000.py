"""front-panel tests for CHEM-071 hach_dr6000 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_analytical.py):
python3 -m pytest tests/test_panel_ui_hach_dr6000.py -q

The DR6000 has the 7 inch touch screen (display 'touch'), the power switch on the back and the power save key beneath the display.  Every
gesture goes through the public API (panel.switch / panel.press / panel.touch); widgets are found by id in the structured frame (panel.frame,
evaluator).  Themes: the eight key procedures of the menu survey; program options (standard adjust, reagent blank, multiple determination,
continuous readings, chemical form, standard addition), multi wavelength, user programs with calibration and free programming, favorites,
data log with filter and delete, stored scans / time courses, trends, operator and sample IDs, security, setup pages, lamp control and
warm-up, system checks, AQA, timers, service reminder, self-check faults; panel = direct command; refusals; render determinism and evidence
PNGs; every terminal command once; contract entries."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
import basic40_runtime_analytical as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

OUT = ROOT / 'outputs/panel_ui/hach_dr6000'
CONTRACT = rt.DR6000_ASSET_DIR / 'asset.contract.json'
TOUCH = 'touch'
SURVEY_TASKS = ['switch on and reach the Main Menu', 'run a stored program', 'single wavelength absorbance reading', 'wavelength scan',
                'time course (kinetics)', 'set a dilution factor', 'export the data log to USB', 'sleep mode']
DONE_TASKS = set()
TMP = tempfile.mkdtemp(prefix='dr6000_panel_')


class Rig:
    """A DR6000 with a blank cell, a synthetic two-peak sample (275 / 520 nm) and a phosphate cuvette for stored program 490 (1.0 mg/L
    PO4, 880 nm); switched on and through the startup and the self-check (165 s)."""

    def __init__(self, on=True):
        self.t = 0.0
        self.h = h = rt.HachDR6000('dr', scene_adapter=lambda i, s: None, clock=lambda: self.t)
        h.panel_capture_dir = TMP
        h.register_sample('blank', kind='blank')
        h.register_sample('syn')
        h.register_template_sample('po4', template_id='hach_prog_490', concentration_kg_m3=1.0e-3)
        h.register_template_sample('po4b', template_id='hach_prog_490', concentration_kg_m3=1.1e-3)
        h.register_principal('judge', visibility='evaluator')
        self.A, self.E = h.session('__agent__'), h.session('judge')
        self.n = 0
        if on:
            assert self.switch('on')
            self.step(166)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r.get('error'))
        return r['result'] if expect == 'succeeded' else r

    def code(self, cap, **args):
        return self.call(self.A, cap, expect='rejected', **args)['error']['code']

    def val(self, sid):
        return self.E.read(state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        self.t += dt
        self.h.host_update()

    def load(self, sample):
        self.call(self.A, 'lid.set_angle', angle_rad=rt.HACH_DR6000_LID_MAX_RAD)
        if self.val('sample.loaded'):
            self.call(self.A, 'sample.unload')
        if sample:
            self.call(self.A, 'sample.load', sample_id=sample)
        self.call(self.A, 'lid.set_angle', angle_rad=0.0)

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][TOUCH]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def press(self):
        return self.call(self.A, 'panel.press', input_id='power_save_key')['accepted']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TOUCH, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid, **kw):
        x, y, ww, hh = self.widgets()[wid]['rect']
        return self.touch(x + ww // 2, y + hh // 2, **kw)

    def tile(self, label):
        for w in self.disp()['widgets']:
            if w['type'] == 'button' and w['text'] == label:
                x, y, ww, hh = w['rect']
                return self.touch(x + ww // 2, y + hh // 2)
        raise AssertionError(label)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        for b in self.overlay()['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in self.overlay()["buttons"]]}')

    def type(self, text):
        if self.overlay()['field']:
            self.ov('Del')
        for ch in text:
            key = 'Space' if ch == ' ' else ch
            for _ in range(4):                                                        # switch the keypad layout (ABC / abc / 12@) when needed
                labels = [b['label'] for b in self.overlay()['buttons']]
                if key in labels:
                    break
                toggle = next(t for t in ('12@', 'abc', 'ABC', 'Shift') if t in labels)
                self.ov(toggle)
            assert self.ov(key), ch

    def fill(self, label, text):
        assert self.ov(label)
        self.type(text)
        return self.ov('OK')

    def pick(self, label, option):
        assert self.ov(label)
        return self.ov(option)

    def row(self, wid, first, col=0):
        w = self.widgets()[wid]
        while w['scroll']:
            x, y, ww, hh = w['rect']
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
            w = self.widgets()[wid]
        for _ in range(60):
            w = self.widgets()[wid]
            firsts = [c[col] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, firsts)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def form_row(self, wid, label):
        w = self.widgets()[wid]
        k = [c[0] for c in w['cells']].index(label)
        return self.touch(w['rect'][0] + 20, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def cells(self, wid):
        return self.widgets()[wid]['cells']

    def png(self):
        return Path(self.call(self.A, 'panel.capture', display_id=TOUCH)['image']['path']).read_bytes()

    def stored(self, number):
        self.tile('Stored Programs')
        self.tap('sp_bynum')
        self.fill('Program number', str(number))
        self.ov('OK')
        self.tap('sp_start')

    def zero_read(self, zero='blank', sample='po4'):
        self.load(zero)
        assert self.tap('ms_zero_btn')
        self.load(sample)
        return self.tap('ms_read')


class SurveyProcedures(unittest.TestCase):
    def test_switch_on_to_main_menu(self):
        r = Rig(on=False)
        self.assertEqual(r.frame()['screen'], 'off')
        r.switch('on')
        self.assertEqual(r.frame()['screen'], 'startup')
        self.assertEqual(r.code('program.start', kind='single'), 'NOT_READY')
        r.step(46)
        self.assertEqual(r.frame()['screen'], 'selfcheck')
        self.assertEqual(r.cells('scx_list')[0], ['System  ...'])
        r.step(60)
        self.assertEqual(r.cells('scx_list')[1], ['Lamp  OK'])
        r.step(60)
        self.assertEqual(r.page(), ('gui', 'main'))
        self.assertTrue(r.val('dr.status')['ready'])
        DONE_TASKS.add('switch on and reach the Main Menu')

    def test_run_stored_program(self):
        r = Rig()
        r.stored(490)
        self.assertEqual(r.page(), ('gui', 'measure'))
        self.assertEqual(r.text('t_bar'), '490 P React. PP')
        self.assertEqual(r.text('ms_wl'), '880.0 nm')
        self.assertFalse(r.widgets()['ms_read']['enabled'])                         # Read is inactive until zeroed (p.100)
        r.zero_read()
        self.assertEqual((r.text('ms_main'), r.text('ms_unit')), ('1.00', 'mg/L PO4^3-'))
        self.assertEqual(r.text('ms_alt'), '0.500 Abs')
        log = r.val('dr.data_log')
        self.assertEqual((len(log), log[0]['program'], log[0]['conc']), (1, '490 P React. PP', 1.0))
        DONE_TASKS.add('run a stored program')

    def test_single_wavelength_reading(self):
        r = Rig()
        r.tile('Single Wavelength')
        r.tap('ms_options')
        r.tile('Wavelength')
        r.fill('Wavelength', '520')
        r.ov('OK')
        r.tap('op_return')
        self.assertEqual(r.text('ms_wl'), '520.0 nm')
        r.zero_read('blank', 'syn')
        a = r.val('dr.result')['abs'][0]
        self.assertAlmostEqual(a, 0.02 + 1.2 + 0.75 * 2.718281828 ** (-0.5 * (245 / 24) ** 2), places=6)
        self.assertEqual(r.text('ms_main'), f'{a:.3f}')
        r.tap('ms_options')
        r.tile('%T / Conc / Abs')
        r.ov('% Trans')
        r.tap('op_return')
        self.assertEqual((r.text('ms_main'), r.text('ms_unit')), (f'{100 * 10 ** -a:.1f}', '%T'))
        DONE_TASKS.add('single wavelength absorbance reading')

    def test_wavelength_scan(self):
        r = Rig()
        r.tile('Wavelength Scan')
        r.tap('sc_options')
        r.tile('Range & Step')
        r.fill('Lower wavelength', '400')
        r.fill('Upper wavelength', '700')
        r.pick('Step', '5 nm')
        r.ov('OK')
        r.tap('so_return')
        r.load('blank')
        r.tap('sc_zero')
        r.load('syn')
        r.tap('sc_read')
        sc = r.val('dr.scan')
        self.assertEqual(len(sc['wavelength_m']), 61)
        self.assertEqual([round(e['wavelength_m'] * 1e9) for e in sc['extrema']], [520])
        self.assertEqual(len(next(s for s in r.widgets()['sc_plot']['series'] if s['label'] == 'Scan')['points']), 61)
        r.tap('sc_mode')                                                              # Peak / Valley: the cursor jumps to the maximum
        r.tap('sc_right')
        self.assertTrue(r.text('sc_cursor').startswith('520.0 nm  1.220 Abs'))
        r.tap('sc_options')
        r.tile('Store (Folder)')
        r.row('ss_list', '01  ---')
        self.assertIn('1', r.val('dr.stored_data')['scans'])
        r.tap('sc_options')
        r.tile('Integral: Off')
        r.tap('so_return')
        self.assertTrue(r.text('sc_integral').startswith('Integral '))
        DONE_TASKS.add('wavelength scan')

    def test_time_course(self):
        r = Rig()
        r.tile('Time Course')
        r.tap('tc_options')
        r.tile('Time & Interval')
        r.fill('Total time', '60')
        r.fill('Interval', '10')
        r.ov('OK')
        r.tap('to_return')
        r.load('blank')
        r.tap('tc_zero')
        r.load('syn')
        r.tap('tc_read')
        self.assertEqual((r.text('tc_zero'), r.text('tc_read')), ('Mark', 'Stop'))
        r.step(15)
        r.tap('tc_zero')                                                              # Mark the next point
        r.step(50)
        tc = r.val('dr.time_course')
        self.assertFalse(tc['running'])
        self.assertEqual((len(tc['points']), tc['marks']), (7, [2]))
        self.assertEqual(r.text('tc_status'), 'Complete 7 points')
        self.assertEqual(r.text('tc_read'), 'Read')
        r.tap('tc_options')
        r.tile('View Table')
        self.assertTrue(r.cells('tt_list')[2][0].endswith('*'))
        DONE_TASKS.add('time course (kinetics)')

    def test_dilution_factor(self):
        r = Rig()
        r.stored(490)
        r.zero_read()
        r.tap('ms_options')
        r.tile('More...')
        r.tile('Dilution Factor')
        r.pick('Dilution Factor', 'On')
        r.fill('Value', '2')
        r.ov('OK')
        r.tap('mo_return')
        r.tap('op_return')
        self.assertEqual(r.text('ms_main'), '2.00')
        self.assertIn('Dilution x2', r.text('ms_flags'))
        DONE_TASKS.add('set a dilution factor')

    def test_export_data_log(self):
        r = Rig()
        r.stored(490)
        r.zero_read()
        r.tap('ms_menu')
        r.tile('Recall Data')
        r.tile('Data Log')
        self.assertEqual(len(r.cells('lg_list')), 1)
        r.tap('lg_send')
        r.ov('All data')
        self.assertEqual(r.overlay()['lines'], ['Please insert the USB memory stick.'])       # no USB stick in the simulation
        r.ov('OK')
        r.call(r.A, 'settings.configure', settings={'network.on': True, 'network.server': 'Netdrive'})
        r.tap('lg_send')
        r.ov('All data')
        self.assertEqual(r.overlay()['lines'], ['Remote server cannot be reached.'])
        DONE_TASKS.add('export the data log to USB')

    def test_sleep_mode(self):
        r = Rig()
        self.assertTrue(r.press())
        self.assertEqual(r.frame()['screen'], 'sleep')
        self.assertEqual(r.disp()['brightness'], 0)
        self.assertFalse(r.val('dr.lamps')['uv_on'])
        r.press()
        self.assertEqual(r.frame()['screen'], 'selfcheck')
        r.step(121)
        self.assertEqual(r.page(), ('gui', 'main'))
        DONE_TASKS.add('sleep mode')

    @classmethod
    def tearDownClass(cls):
        missing = set(SURVEY_TASKS) - DONE_TASKS
        assert not missing or len(DONE_TASKS) < len(SURVEY_TASKS), missing


class ProgramOptions(unittest.TestCase):
    def test_standard_adjust_blank_multiple_and_form(self):
        r = Rig()
        r.stored(490)
        r.zero_read(sample='po4b')                                                    # standard 1.10 mg/L reads 1.10
        r.tap('ms_options')
        r.tile('More...')
        r.tile('Standard Adjust')
        r.fill('Standard', '1.0')
        r.ov('Adjust')
        self.assertAlmostEqual(r.val('dr.mode')['std_adjust']['factor'], 1.0 / 1.1)
        self.assertEqual(r.val('dr.result')['conc'], 1.0)
        r.tile('Standard Adjust')
        r.fill('Standard', '2.0')
        r.ov('Adjust')
        self.assertEqual(r.overlay()['dialog'], 'err_adjust_limit')
        r.ov('OK')
        r.ov('Cancel')
        r.tile('Reagent Blank')
        r.pick('Reagent Blank', 'On')
        r.fill('Value', '0.05')
        r.ov('OK')
        self.assertAlmostEqual(r.val('dr.result')['conc'], 0.95)
        r.tile('Multiple Determination')
        r.pick('Multiple Determination', 'On')
        r.fill('Value', '3')
        r.ov('OK')
        r.tile('Chemical Form')
        self.assertEqual([b['label'] for b in r.overlay()['buttons']], ['PO4^3-', 'Cancel'])
        r.ov('PO4^3-')
        r.tap('mo_return')
        r.tap('op_return')
        r.tap('ms_read')
        self.assertEqual(r.val('dr.result')['n'], 3)
        self.assertIn('n=3', r.text('ms_alt'))
        r.tap('ms_menu')                                                              # leaving the program ends blank / adjust
        r.stored(490)
        self.assertIsNone(r.val('dr.mode').get('reagent_blank'))

    def test_continuous_readings_and_cover(self):
        r = Rig()
        r.stored(490)
        r.load('blank')
        r.tap('ms_options')
        r.tile('More...')
        r.tile('Reading Mode')
        r.ov('Continuous Readings')
        r.tap('mo_return')
        r.tap('op_return')
        r.tap('ms_zero_btn')
        r.load('po4')
        self.assertEqual(r.text('ms_main'), '1.00')                                   # shown without Read
        r.call(r.A, 'lid.set_angle', angle_rad=1.0)
        self.assertEqual(r.text('ms_warn'), 'Please close the cover.')
        r.tap('ms_read')
        self.assertEqual(r.overlay()['dialog'], 'err_cover_open')

    def test_standard_addition(self):
        r = Rig()
        r.stored(490)
        r.zero_read()
        r.tap('ms_options')
        r.tile('More...')
        r.tile('Standard Addition')
        self.assertEqual(r.page(), ('gui', 'std_add'))
        self.assertEqual(r.cells('sa_list')[0], ['0.0 mL', '1.000', ''])
        r.tap('sa_edit')
        r.fill('Standard concentration', '50')
        r.ov('OK')
        sa = r.val('dr.std_addition')
        added = [50 * v / (10e-6 + v) for v in sa['volumes_m3']]
        for i in range(3):
            r.h.register_template_sample(f'spk{i}', template_id='hach_prog_490', concentration_kg_m3=(1.0 + added[i]) * 1e-3)
            r.row('sa_list', f'{0.1 * (i + 1):.1f} mL')
            r.load(f'spk{i}')
            r.tap('sa_read')
        self.assertEqual([c[2] for c in r.cells('sa_list')[1:]], ['100 %', '100 %', '100 %'])
        fit = r.val('dr.std_addition')['fit']
        self.assertAlmostEqual(fit['intercept'], 1.0, places=6)
        self.assertAlmostEqual(fit['r2'], 1.0, places=9)
        r.tap('sa_graph')
        self.assertTrue(r.text('sg_fit').startswith('Estimated 1.000'))

    def test_multi_wavelength(self):
        r = Rig()
        r.tile('Multi Wavelength')
        r.tap('ms_options')
        r.tile('Absorbance Formula')
        r.form_row('mw_form', 'Absorbance Formula')
        r.ov('K1A1/K2A2')
        r.form_row('mw_form', 'Wavelength 1')
        r.type('520')
        r.ov('OK')
        r.form_row('mw_form', 'Wavelength 2')
        r.type('275')
        r.ov('OK')
        self.assertNotIn('Wavelength 3', [c[0] for c in r.cells('mw_form')])
        r.tap('mw_ok')
        r.tap('op_return')
        self.assertEqual(r.text('ms_wl'), '520.0 nm  275.0 nm')
        r.zero_read('blank', 'syn')
        res = r.val('dr.result')
        self.assertEqual(len(res['abs']), 2)
        self.assertIsNone(res['conc'])
        self.assertEqual(r.text('ms_main'), f"{res['abs'][0] / res['abs'][1]:.3f}")
        r.call(r.A, 'program.options', settings={'conc_factor': 2.0, 'result_mode': 'conc'})
        self.assertAlmostEqual(r.val('dr.result')['conc'], 2.0 * res['abs'][0] / res['abs'][1])


class UserPrograms(unittest.TestCase):
    def test_user_program_measured_standards(self):
        r = Rig()
        r.tile('User Programs')
        r.tap('up_opts')
        r.ov('New')
        self.assertEqual(r.val('dr.programs')['draft']['number'], 9000)
        r.form_row('ue_form', 'Program Name')
        r.type('dye')
        r.ov('OK')
        r.form_row('ue_form', 'Wavelength')
        r.type('520')
        r.ov('OK')
        r.form_row('ue_form', 'Calibration')
        r.ov('Measure standards')
        r.tap('ue_calib')
        for c in ('0', '10'):
            r.tap('uc_add')
            r.fill('Standard concentration', c)
            r.ov('OK')
        r.load('blank')
        r.tap('uc_zero_btn')
        r.row('uc_list', '0')
        r.ov('Read')                                                                  # the blank as the 0 standard
        r.load('syn')
        r.row('uc_list', '10')
        r.ov('Read')
        r.tap('uc_graph')
        self.assertIn('r2 = 1.0000', r.text('ug_fit'))
        r.tap('ug_done')
        r.tap('ue_store')
        self.assertEqual(r.cells('up_list')[0], ['9000 dye'])
        r.row('up_list', '9000 dye')
        r.tap('up_start')
        r.zero_read('blank', 'syn')
        self.assertAlmostEqual(r.val('dr.result')['conc'], 10.0, places=6)
        r.tap('ms_menu')
        r.tile('User Programs')
        r.row('up_list', '9000 dye')
        r.tap('up_opts')
        r.ov('Add to favorites')
        r.tap('up_menu')
        r.tile('Favorites')
        self.assertEqual(r.cells('fv_list')[0], ['9000 dye'])

    def test_free_programming(self):
        r = Rig()
        r.call(r.A, 'user_program.edit', action='new', number=9050)
        r.call(r.A, 'user_program.edit', action='set', settings={'name': 'free', 'type': 'free', 'process': 2, 'wl1_m': 520e-9,
                                                                   'expression': 'F1*(R11-Z21)', 'f1': 4.0})
        self.assertEqual(r.code('user_program.edit', action='set', settings={'expression': 'F1*(R11-'}), 'INVALID_ARGUMENT')
        r.call(r.A, 'user_program.edit', action='store')
        r.tile('User Programs')
        r.row('up_list', '9050 free')
        r.tap('up_start')
        r.load('blank')
        r.tap('ms_zero_btn')                                                          # [Z]Z11 against air
        self.assertEqual(r.text('ms_zero'), 'Read: Z21')
        r.load('blank')
        r.tap('ms_read')                                                              # [R]Z21 the sample blank
        r.load('syn')
        r.tap('ms_read')                                                              # {[R]R11}
        a = r.val('dr.result')['abs'][0]
        self.assertAlmostEqual(r.val('dr.result')['conc'], 4.0 * a)
        r.tap('ms_read')                                                              # repeated sequence
        self.assertEqual(len(r.val('dr.data_log')), 2)

    def test_save_as_user_program_and_favorite_stored(self):
        r = Rig()
        r.stored(490)
        r.tap('ms_options')
        r.tile('More...')
        r.tile('Save as User Program')
        self.assertEqual(r.page(), ('gui', 'up_edit'))
        d = r.val('dr.programs')['draft']
        self.assertEqual((d['wavelength_m'], d['calib_method'], d['coef_b']), (880e-9, 'formula', 2.0))
        r.form_row('ue_form', 'Program Name')
        r.type('po4 copy')
        r.ov('OK')
        r.tap('ue_store')
        self.assertIn('9000', r.val('dr.programs')['user'])
        r.tap('up_menu')
        r.tile('Stored Programs')
        r.row('sp_list', '490 P React. PP')
        r.tap('sp_opts')
        r.ov('Add to Favorites')
        r.tap('sp_menu')
        r.tile('Favorites')
        r.row('fv_list', '490 P React. PP')
        r.tap('fv_remove')
        self.assertEqual(r.val('dr.programs')['favorites'], [])


class DataAndSetup(unittest.TestCase):
    def test_data_log_filter_detail_delete_and_trend(self):
        r = Rig()
        r.call(r.A, 'sample_id.manage', action='new', name='inflow', add_number=True)
        r.stored(490)
        r.zero_read()
        r.call(r.A, 'sample_id.manage', action='off')
        r.tap('ms_read')
        r.tap('ms_menu')
        r.tile('Recall Data')
        r.tile('Data Log')
        self.assertEqual(len(r.cells('lg_list')), 2)
        r.call(r.A, 'sample_id.manage', action='select', name='inflow')
        r.tap('lg_filter_btn')
        r.ov('Filter: Off')
        r.ov('Sample ID: all')
        r.ov('OK')
        self.assertEqual(len(r.cells('lg_list')), 1)
        r.row('lg_list', r.cells('lg_list')[0][0])
        self.assertIn('Sample ID: inflow (01)', r.overlay()['lines'])
        r.ov('Close')
        r.tap('lg_delete')
        r.ov('Filtered data')
        self.assertEqual(len(r.val('dr.data_log')), 1)
        r.tap('tb_trends')
        r.tap('tr_new')
        r.row('tn_list', '490 P React. PP')
        r.fill('Upper control limit', '2')
        r.ov('OK')
        r.row('tr_list', r.cells('tr_list')[0][0])
        r.tap('tr_select')
        self.assertEqual(r.widgets()['tv_plot']['series'][0]['points'], [[0.0, 1.0]])

    def test_stored_scan_recall_and_reference(self):
        r = Rig()
        r.call(r.A, 'program.start', kind='scan')
        r.call(r.A, 'program.options', settings={'start_m': 400e-9, 'stop_m': 600e-9, 'step_m': 5e-9})
        r.load('blank')
        r.call(r.A, 'reading.zero')
        r.load('syn')
        r.call(r.A, 'reading.read')
        r.call(r.A, 'data.store', target='scan', slot=1)
        r.call(r.A, 'data.store', target='scan', slot=2)
        r.call(r.A, 'program.options', settings={'reference': 1})
        r.call(r.A, 'program.exit')
        r.tile('Recall Data')
        r.tile('Wavelength Scan')
        r.row('rec_scans_list', r.cells('rec_scans_list')[1][0])
        r.tap('rec_scans_graph')
        self.assertEqual(len(r.widgets()['rec_scans_plot']['series'][0]['points']), 41)
        r.tap('rec_scans_ref')
        r.row('rr_list', r.cells('rr_list')[0][0])
        self.assertEqual(len(r.widgets()['rec_scans_plot']['series']), 2)
        r.tap('rec_scans_list_btn')
        r.tap('rec_scans_delete')
        self.assertEqual(sorted(r.val('dr.stored_data')['scans']), ['1'])

    def test_operator_login_security_and_password(self):
        r = Rig()
        r.tap('tb_logon')
        r.tap('op_new')
        r.fill('Operator ID', 'ann')
        r.fill('Operator Password', '11')
        r.ov('OK')
        r.row('op_list', 'ann  Off  *')
        r.ov('Login')
        r.fill('Operator Password', '12')
        r.ov('OK')
        self.assertEqual(r.overlay()['dialog'], 'err_wrong_password')
        r.ov('OK')
        r.ov('Operator Password')
        r.type('11')
        r.ov('OK')
        r.ov('OK')
        self.assertEqual(r.val('dr.ids')['operator'], 'ann')
        self.assertEqual(r.text('tb_logon'), 'ann')
        r.call(r.A, 'security.configure', action='on', password='sec')
        r.call(r.A, 'security.configure', action='level', password='sec', function='Instrument Setup', level='one key')
        r.tap('op_back')
        r.tile('Instrument Setup')
        self.assertEqual(r.overlay()['dialog'], 'password')
        r.fill('Password', 'bad')
        r.ov('OK')
        self.assertEqual(r.overlay()['dialog'], 'err_wrong_password')
        r.ov('OK')
        self.assertEqual(r.page(), ('gui', 'main'))
        r.tile('Instrument Setup')
        r.fill('Password', 'sec')
        r.ov('OK')
        self.assertEqual(r.page(), ('gui', 'setup'))
        r.call(r.A, 'operator.manage', action='set_level', name='ann', level='one key', security_password='sec')
        r.tap('su_menu')
        r.tile('Instrument Setup')                                                   # the operator's level is enough
        self.assertEqual(r.page(), ('gui', 'setup'))
        r.tile('Security')
        r.tap('sec_list')
        r.row('sl_list', 'Time Course')
        r.pick('Security level', 'two keys')
        r.fill('Security Password', 'sec')
        r.ov('OK')
        self.assertEqual(r.val('dr.security')['levels']['Time Course'], 'two keys')
        self.assertEqual(r.code('program.start', kind='time'), 'LOCKED')

    def test_sample_ids_numbering(self):
        r = Rig()
        r.tap('tb_sample')
        r.tap('sid_new')
        r.fill('Sample ID', 'inflow')
        r.pick('Add Number', 'On')
        r.ov('OK')
        self.assertEqual(r.cells('sid_list')[0], ['inflow (01)  [active]'])
        r.call(r.A, 'program.start', kind='single')
        r.load('blank')
        r.call(r.A, 'reading.zero')
        r.call(r.A, 'reading.read')
        r.call(r.A, 'reading.read')
        self.assertEqual([e['sample_id'] for e in r.val('dr.data_log')], ['inflow (02)', 'inflow (01)'])
        r.row('sid_list', 'inflow (03)  [active]')
        r.ov('Delete')
        self.assertIsNone(r.val('dr.ids')['sample_id'])

    def test_setup_pages(self):
        r = Rig()
        r.tile('Instrument Setup')
        r.tile('Date & Time')
        r.form_row('dt_form', 'Date Format')
        r.ov('yyyy-mm-dd')
        r.call(r.A, 'clock.set', date='2026-10-19', time='09:30')
        self.assertEqual(r.text('dt_now'), '2026-10-19  09:30')
        r.tap('dt_ok')
        r.tile('Power Management')
        r.form_row('power_mgmt_form', 'Sleep Timer')
        r.ov('10 min')
        self.assertEqual(r.val('dr.settings')['power.sleep_timer_s'], 600.0)
        r.step(601)
        self.assertEqual(r.frame()['screen'], 'sleep')                                # energy-saving sleep after no use (p.41)
        r.press()
        r.step(121)
        self.assertEqual(r.page(), ('gui', 'main'))                                   # after the self-check the Main Menu
        r.call(r.A, 'settings.configure', settings={'power.sleep_timer_s': 0.0})
        r.tile('Instrument Setup')
        r.tile('Data Log Setup')
        r.form_row('datalog_setup_form', 'Auto Store')
        r.ov('Off')
        self.assertFalse(r.val('dr.settings')['log.auto_store'])
        r.tap('datalog_setup_ok')
        r.tile('Sound Settings')
        r.form_row('sound_form', 'Volume')
        r.type('80')
        r.ov('OK')
        self.assertEqual(r.val('dr.settings')['sound.volume'], 80)
        r.tap('sound_ok')
        r.tile('PC & Printer')
        r.tile('Network')
        self.assertEqual([c[0] for c in r.cells('network_form')], ['Network'])
        r.form_row('network_form', 'Network')
        r.ov('On')
        self.assertIn('Network Server', [c[0] for c in r.cells('network_form')])
        r.tap('network_ok')
        r.tile('Printer')
        r.form_row('printer_form', 'Paper')
        r.ov('Letter')
        self.assertEqual(r.val('dr.settings')['printer.paper'], 'Letter')

    def test_manual_save_with_auto_store_off(self):
        r = Rig()
        r.call(r.A, 'settings.configure', settings={'log.auto_store': False})
        r.stored(490)
        r.zero_read()
        self.assertEqual(r.val('dr.data_log'), [])
        r.tap('ms_options')
        r.tile('Save / Recall')
        self.assertEqual(len(r.val('dr.data_log')), 1)
        self.assertEqual(r.code('data.store', target='log'), 'PRECONDITION_FAILED')


class LampsChecksAqa(unittest.TestCase):
    def test_lamp_control_and_warmup(self):
        r = Rig()
        r.tile('Instrument Setup')
        r.tile('Lamp Control')
        r.form_row('lamp_control_form', 'UV-Lamp')
        r.ov('Off')
        self.assertFalse(r.val('dr.lamps')['uv_on'])
        r.tap('lamp_control_ok')
        r.tap('su_menu')
        r.call(r.A, 'program.start', kind='single')
        r.call(r.A, 'program.options', settings={'wavelength_m': 275e-9})
        r.load('blank')
        self.assertEqual(r.code('reading.zero'), 'LAMP_WARMUP')                      # the UV lamp is switched on and warms up
        self.assertTrue(r.val('dr.lamps')['uv_on'])
        r.step(30)
        self.assertEqual(r.code('reading.zero'), 'LAMP_WARMUP')
        r.step(31)
        r.call(r.A, 'reading.zero')
        r.call(r.A, 'settings.configure', settings={'power.uv_save_s': 900.0})
        r.step(901)
        self.assertFalse(r.val('dr.lamps')['uv_on'])                                 # UV Lamp Save
        r.call(r.A, 'settings.configure', settings={'power.vis_save': 'Off'})
        r.call(r.A, 'program.options', settings={'wavelength_m': 520e-9})
        self.assertEqual(r.code('reading.zero'), 'LAMP_OFF')
        r.call(r.A, 'lamp.set', lamp='vis', on=True)
        r.call(r.A, 'reading.zero')
        r.step(3600)
        r.call(r.A, 'program.exit')
        r.tile('System Checks')
        r.tile('Lamp History')
        rows = dict(map(tuple, r.cells('lh_rows')))
        self.assertEqual(rows['VIS lamp hours'], '1.0 h')
        r.tap('lh_uv')
        self.assertEqual(dict(map(tuple, r.cells('lh_rows')))['UV lamp hours'], '0.0 h')

    def test_optical_checks(self):
        r = Rig()
        r.tile('System Checks')
        r.tile('Optical Checks')
        r.tile('Wavelength Check')
        r.tap('cw_start')
        self.assertEqual(r.text('cw_res'), '656.1 nm: 656.1 nm  Passed')
        r.tap('cw_486')
        r.tap('cw_start')
        self.assertEqual(r.text('cw_res'), '486.0 nm: 486.0 nm  Passed')
        r.tap('cw_cancel')
        r.tile('Stray Light Check')
        r.load('syn')
        r.tap('cs_zero')
        self.assertEqual(r.overlay()['dialog'], 'err_cuvette_present')
        r.ov('OK')
        r.load(None)
        r.tap('cs_zero')
        r.load('syn')                                                                 # the synthetic cell is no stray light cell: 0.04 Abs < 3.0
        r.tap('cs_start')
        self.assertTrue(r.text('cs_res').endswith('Failed'))
        r.tap('cs_crit_btn')
        r.fill('Required value', '0.01')
        r.ov('OK')
        self.assertEqual(r.text('cs_crit'), 'Required value: 0.01 Abs')
        r.load(None)
        r.tap('cs_zero')
        r.load('syn')
        r.tap('cs_start')
        self.assertTrue(r.text('cs_res').endswith('Passed'))
        r.tap('cs_cancel')
        r.tile('Absorbance Check')
        r.tap('ca_crit_btn')
        a520 = 0.02 + 1.2 + 0.75 * 2.718281828459045 ** (-0.5 * (245 / 24) ** 2)
        r.fill('Nominal value', f'{a520:.3f}')
        r.ov('OK')
        r.tap('ca_wl_btn')
        r.fill('Wavelength', '520')
        r.ov('OK')
        for _ in range(5):
            r.load(None)
            r.tap('ca_zero')
            r.load('syn')
            r.tap('ca_read')
        self.assertTrue(r.text('ca_res').startswith('Mean '))
        self.assertTrue(r.text('ca_res').endswith('SD 0.0000  Passed'))
        r.tap('ca_cancel')
        r.tile('Noise Check')
        r.load('blank')
        r.tap('cn_zero')
        r.load('syn')
        r.tap('cn_start')
        self.assertTrue(r.text('cn_res').endswith('SD 0.0000  Passed'))
        r.tap('cn_cancel')
        r.tile('Drift Check')
        r.tap('cd_start')
        r.step(3601)
        self.assertEqual(r.val('dr.checks')['drift']['state'], 'done')
        self.assertEqual(len(r.val('dr.checks')['drift']['slopes']), 4)
        self.assertTrue(r.text('cd_res').endswith('Passed'))
        r.tap('cd_cancel')
        r.tile('Check Log')
        self.assertEqual([c[1] for c in r.cells('cl_list')], ['Passed', 'Passed', 'Passed', 'Passed', 'Failed', 'Passed', 'Passed'])
        self.assertTrue(r.cells('cl_list')[0][0].endswith('Drift check'))
        r.tap('cl_send')
        self.assertEqual(r.overlay()['dialog'], 'err_no_usb')

    def test_checks_detect_optics_errors(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', errors={'wavelength_offset_m': 1.5e-9, 'noise_sd_abs': 0.002, 'drift_abs_per_h': 0.02})
        self.assertEqual(r.call(r.A, 'sensor.configure_error', expect='rejected', errors={'noise_sd_abs': 0.0})['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('sensor.configure_error', r.A.describe()['capabilities'])
        self.assertNotIn('dr.optics_errors', r.A.describe()['state_slots'])
        r.tile('System Checks')
        r.tile('Optical Checks')
        r.tile('Wavelength Check')
        r.tap('cw_start')
        self.assertEqual(r.text('cw_res'), '656.1 nm: 654.6 nm  Failed')               # 1.5 nm > +-1 nm
        r.tap('cw_cancel')
        r.tile('Noise Check')
        r.load('blank')
        r.tap('cn_zero')
        r.load('syn')
        r.tap('cn_start')
        sd = r.val('dr.checks')['noise']['sd']
        self.assertTrue(0.0015 < sd < 0.0025, sd)
        self.assertTrue(r.text('cn_res').endswith('Failed'))
        r.tap('cn_cancel')
        r.tile('Drift Check')
        r.tap('cd_start')
        r.step(3601)
        self.assertAlmostEqual(r.val('dr.checks')['drift']['slope_per_h'], 0.02, delta=0.003)
        self.assertTrue(r.text('cd_res').endswith('Failed'))
        log = r.val('dr.check_log')
        self.assertEqual([e['result'] for e in log], ['Failed', 'Failed', 'Failed'])

    def test_verification_kit(self):
        r = Rig()
        r.tile('System Checks')
        r.tile('Optical Checks')
        r.tile('Verification Kit')
        r.tile('Nominal Values')
        self.assertEqual(r.cells('vn_list')[1], ['NG05  Photometric', '546 nm 0.5 +-0.005'])
        r.row('vn_list', 'NG05  Photometric')
        r.fill('Tolerance', '0.006')
        r.ov('OK')
        self.assertEqual(r.val('dr.verification')['nominal'][1]['tolerance'], 0.006)
        r.tap('vn_ok')

        def run():
            r.tile('Verification')
            r.tap('vr_start')
            while r.val('dr.verification')['run']['state'] == 'filter':
                step = r.val('dr.verification')['run']['step']
                f = r.val('dr.verification')['nominal'][step]['filter']
                self.assertTrue(r.text('vr_text').startswith(f'Insert filter {f} '))
                r.call(r.A, 'lid.set_angle', angle_rad=rt.HACH_DR6000_LID_MAX_RAD)
                r.call(r.E, 'verification_kit.place', filter=f)                         # the hands put the filter into the adapter
                r.call(r.A, 'lid.set_angle', angle_rad=0.0)
                r.tap('vr_next')
                r.call(r.A, 'lid.set_angle', angle_rad=rt.HACH_DR6000_LID_MAX_RAD)
                r.call(r.E, 'verification_kit.place', filter=None)
                r.call(r.A, 'lid.set_angle', angle_rad=0.0)
            return [c[1] for c in r.cells('vr_list')]
        self.assertEqual(run(), ['Passed'] * 6)
        self.assertEqual(r.text('vr_text'), 'Verification passed.')
        self.assertEqual(len(r.val('dr.check_log')), 6)
        r.tap('vr_cancel')
        r.call(r.E, 'sensor.configure_error', errors={'wavelength_offset_m': 2e-9, 'stray_light_fraction': 0.005, 'photometric_gain': 0.03})
        self.assertEqual(run(), ['Failed', 'Failed', 'Failed', 'Failed', 'Failed', 'Failed'])
        res = r.val('dr.verification')['run']['results']
        self.assertAlmostEqual(res[4]['measured'], 536.4e-9 - 2e-9, delta=0.11e-9)          # holmium peak seen 2 nm off
        self.assertLess(res[0]['measured'], 2.4)                                           # stray light limits the cut-off filter
        self.assertEqual(r.text('vr_text'), 'Verification failed.')
        self.assertEqual(r.call(r.A, 'verification_kit.place', expect='rejected', filter='NG10')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call(r.E, 'verification_kit.place', expect='rejected', filter='NG10')['error']['code'], 'COVER_CLOSED')
        r.call(r.A, 'lid.set_angle', angle_rad=rt.HACH_DR6000_LID_MAX_RAD)
        r.call(r.E, 'verification_kit.place', filter='NG10')
        self.assertEqual(r.call(r.A, 'sample.load', expect='rejected', sample_id='syn')['error']['code'], 'PRECONDITION_FAILED')

    def test_ratios(self):
        r = Rig()
        r.call(r.A, 'sample_id.manage', action='new', name='inflow', add_number=True)
        for i, (p, n) in enumerate((('a', 9), ('b', 9))):
            pass
        for k in range(2):                                                            # two samples, each read with programs 490 and 9
            r.h.register_template_sample(f'p{k}', template_id='hach_prog_490', concentration_kg_m3=(1.0 + k) * 1e-3)
            r.h.register_template_sample(f'al{k}', template_id='hach_prog_9', concentration_kg_m3=0.1e-3)
            for prog, cell in ((490, f'p{k}'), (9, f'al{k}')):
                r.call(r.A, 'program.start', kind='stored', number=prog)
                r.load('blank')
                r.call(r.A, 'reading.zero')
                r.load(cell)
                r.call(r.A, 'reading.read')
                r.call(r.A, 'sample_id.manage', action='edit', name='inflow', first_number=k + 1)
            r.call(r.A, 'sample_id.manage', action='edit', name='inflow', first_number=k + 2)
        r.call(r.A, 'program.exit')
        r.tile('Recall Data')
        r.tile('Data analysis')
        r.tile('Ratios')
        r.tap('ra_new')
        r.row('ratio_sid_list', 'inflow')
        r.row('ratio_p1_list', '490 P React. PP')
        r.row('ratio_p2_list', '9 Aluminum ECR')
        r.tap('ratio_p3_none')
        r.fill('Ratio I upper limit', '15')
        r.ov('OK')
        self.assertEqual(r.page(), ('gui', 'ratios'))
        r.row('ra_list', r.cells('ra_list')[0][0])
        r.tap('ra_select')
        rows = r.cells('rv_list')
        self.assertEqual([c[1:] for c in rows], [['10.00 : 1.00', ''], ['20.00 : 1.00', 'out of limits']])

    def test_aqa_standard_pipette_and_reminder(self):
        r = Rig()
        r.tile('System Checks')
        r.tile('AQA')
        r.tile('AQA Standard')
        r.tap('as_new')
        r.fill('Name', 'po4 std')
        r.fill('Concentration', '1')
        r.fill('Confidence Interval', '0.05')
        r.pick('Reminder', 'daily')
        r.ov('OK')
        self.assertEqual(r.cells('as_list')[0], ['po4 std  1 mg/L +-0.05'])
        self.assertEqual(r.widgets()['tb_aqa']['style'], 'warn')                     # reminder due
        r.tap('tb_aqa')
        r.row('am_list', 'po4 std  1 mg/L +-0.05')
        self.assertEqual(r.page(), ('gui', 'aqa_standards'))
        self.assertEqual(r.val('dr.aqa')['pending'], 0)
        r.tap('as_back')
        r.tap('aq_back')
        r.tap('sy_menu')
        r.call(r.A, 'program.start', kind='stored', number=490)
        r.load('blank')
        r.call(r.A, 'reading.zero')
        r.load('po4')
        r.call(r.A, 'reading.read')
        self.assertEqual(r.val('dr.aqa_log')[0]['result'], 'Passed')
        self.assertEqual(r.val('dr.aqa')['due'], [])
        r.call(r.A, 'aqa.configure', item='pipette', action='new', settings={'name': 'p1', 'wavelength_m': 520e-9, 'lower': 1.0, 'upper': 1.5})
        r.call(r.A, 'program.exit')
        r.tile('System Checks')
        r.tile('AQA')
        r.tile('Pipette Check')
        r.row('ap_list', r.cells('ap_list')[0][0])
        r.ov('Start')
        r.load('blank')
        r.tap('cp_zero')
        r.load('syn')
        r.tap('cp_read')
        self.assertTrue(r.text('cp_res').endswith('Passed'))
        r.tap('cp_close')
        r.tap('ap_back')
        r.tile('AQA Addition')
        r.form_row('aqa_addition_form', 'Reminder')
        r.ov('On')
        self.assertIn('addition', r.val('dr.aqa')['due'])

    def test_timers_and_service_reminder(self):
        r = Rig()
        r.stored(490)
        r.tap('tb_timer')
        r.ov('Reaction time  2 min')
        self.assertEqual(r.text('ms_timer'), 'Reaction time 02:00')
        r.step(119)
        self.assertEqual(r.text('ms_timer'), 'Reaction time 00:01')
        r.step(2)
        self.assertEqual(r.text('nt_text'), 'Reaction time: 00:00')
        r.tap('nt_ok')
        r.tap('tb_timer')
        r.ov('General Timer')
        r.fill('Minutes', '1')
        r.ov('OK')
        r.tap('tb_timer')
        r.ov('Cancel timer General Timer 01:00')
        self.assertIsNone(r.val('dr.timer'))
        r.call(r.A, 'service.configure', enabled=True, last_date='2025-01-01', interval_days=365)
        r.switch('off')
        r.switch('on')
        r.step(166)
        self.assertEqual(r.text('nt_text'), 'Next service is due!')
        r.tap('nt_ok')
        self.assertIsNone(r.val('dr.status')['notice'])

    def test_selfcheck_fault_language_and_toolbar(self):
        r = Rig(on=False)
        r.load('syn')
        r.switch('on')
        r.touch(400, 240)                                                             # touched during the startup: language list
        r.step(46)
        self.assertEqual(r.frame()['screen'], 'language')
        r.tap('lang_1')
        self.assertEqual(r.val('dr.status')['language'], 'Deutsch')
        r.step(121)
        self.assertEqual(r.frame()['screen'], 'fault')
        self.assertEqual(r.text('fx_l2'), 'Please remove the cuvette')
        r.tap('fx_ok')
        self.assertEqual(r.overlay()['dialog'], 'err_fault')
        r.ov('OK')
        r.load(None)
        r.tap('fx_ok')
        r.step(121)
        self.assertEqual(r.page(), ('gui', 'main'))
        for wid, note in (('tb_carousel', 'carousel insert not installed'), ('tb_link2sc', 'LINK2sc needs a memory card or network'),
                          ('tb_web', 'no network connection')):
            r.tap(wid)
            self.assertEqual(r.frame()['ui']['unsupported'], note)
            r.ov('OK')
        r.tile('Barcode Programs')
        r.tap('ms_zero_btn')
        self.assertEqual(r.overlay()['dialog'], 'err_no_barcode')


class EquivalenceRefusals(unittest.TestCase):
    def test_panel_equals_direct(self):
        a, b = Rig(), Rig()
        a.stored(490)
        a.zero_read()
        b.call(b.A, 'program.start', kind='stored', number=490)
        b.load('blank')
        b.call(b.A, 'reading.zero')
        b.load('po4')
        b.call(b.A, 'reading.read')
        ra, rb = a.val('dr.result'), b.val('dr.result')
        self.assertEqual({k: ra[k] for k in ('abs', 'conc', 'unit', 'form', 'warnings')}, {k: rb[k] for k in ('abs', 'conc', 'unit', 'form', 'warnings')})
        self.assertEqual(a.val('measurement.count'), b.val('measurement.count'))

    def test_direct_and_panel_readings_share_the_optics_errors(self):
        errors = {'wavelength_offset_m': 1e-9, 'stray_light_fraction': 0.001, 'photometric_gain': 0.02, 'photometric_offset_abs': 0.01}
        a, b = Rig(), Rig()
        for r in (a, b):
            r.call(r.E, 'sensor.configure_error', errors=errors)
        a.tile('Single Wavelength')                                                  # panel: Zero on the blank, Read the sample at 520 nm
        a.tap('ms_options')
        a.tile('Wavelength')
        a.fill('Wavelength', '520')
        a.ov('OK')
        a.tap('op_return')
        a.zero_read('blank', 'syn')
        b.call(b.A, 'spectroscopy.set_wavelength', wavelength_m=520e-9)               # direct CHEM-071 path: blank and sample
        b.load('blank')
        blank = b.call(b.A, 'spectroscopy.measure')['spectrum']['absorbance'][0]
        b.load('syn')
        sample = b.call(b.A, 'spectroscopy.measure')['spectrum']['absorbance'][0]
        self.assertAlmostEqual(a.val('dr.result')['abs'][0], sample - blank, places=12)
        self.assertNotAlmostEqual(sample - blank, Rig().h._raw_spectrum(dict(b.h._state, **{'scan.start_m': 520e-9, 'scan.stop_m': 520e-9}))[
            'absorbance'][0], places=3)                                               # the errors are really applied
        self.assertAlmostEqual(blank, 0.01, places=12)                                # the photometric offset shows on the direct blank

    def test_legacy_capabilities_unchanged(self):
        r = Rig()
        r.load('syn')
        r.call(r.A, 'spectroscopy.set_wavelength', wavelength_m=520e-9)
        sp = r.call(r.A, 'spectroscopy.measure')['spectrum']
        self.assertEqual(sp['wavelength_m'], [520e-9])
        for cap in ('spectroscopy.calibrate', 'spectroscopy.configure_method', 'spectroscopy.time_scan'):
            self.assertEqual(r.call(r.A, cap, expect='unsupported')['error']['code'], 'UNSUPPORTED')

    def test_direct_measure_uses_the_instrument_readiness_and_lamps(self):
        """The CHEM-071 spectroscopy.measure reads the same instrument as Zero / Read: refused in the startup, the self-check, a
        self-check fault and sleep mode, and while the UV lamp warms up (sync audit 2026-10-19)."""
        r = Rig(on=False)
        assert r.switch('on')
        r.call(r.A, 'spectroscopy.set_wavelength', wavelength_m=520e-9)
        r.step(1)
        before = r.h.debug_snapshot()
        self.assertEqual(r.code('spectroscopy.measure'), 'NOT_READY')                 # startup: lamps off, no reading
        self.assertEqual(before, r.h.debug_snapshot())
        r.step(60)
        self.assertEqual(r.val('dr.status')['phase'], 'selfcheck')
        self.assertEqual(r.code('spectroscopy.measure'), 'NOT_READY')
        r.load('syn')                                                                # a cuvette in the compartment: the self-check fails
        r.step(110)
        self.assertEqual(r.val('dr.status')['phase'], 'fault')
        self.assertEqual(r.code('spectroscopy.measure'), 'NOT_READY')
        r.load(None)
        r.call(r.A, 'message.confirm')
        r.step(121)
        r.load('syn')
        sp = r.call(r.A, 'spectroscopy.measure')['spectrum']
        self.assertEqual(sp['wavelength_m'], [520e-9])
        r.load(None)
        r.call(r.A, 'power.sleep')
        r.load('syn')
        self.assertEqual(r.code('spectroscopy.measure'), 'SLEEP')
        r.load(None)
        r.call(r.A, 'power.sleep')                                                   # pressed again: self-check, then ready
        r.step(121)
        r.call(r.A, 'lamp.set', lamp='uv', on=False)                                    # UV lamp switched off by hand (Lamp Control)
        r.call(r.A, 'spectroscopy.set_wavelength', wavelength_m=275e-9)
        r.load('syn')
        self.assertFalse(r.val('dr.lamps')['uv_on'])
        self.assertEqual(r.code('spectroscopy.measure'), 'LAMP_WARMUP')                 # switched on now, the reading waits
        self.assertTrue(r.val('dr.lamps')['uv_on'])
        r.step(30)
        self.assertEqual(r.code('spectroscopy.measure'), 'LAMP_WARMUP')
        r.step(31)
        self.assertEqual(r.call(r.A, 'spectroscopy.measure')['spectrum']['wavelength_m'], [275e-9])
        r.call(r.A, 'settings.configure', settings={'power.vis_save': 'Off'})          # VIS lamp switched off by hand
        r.call(r.A, 'spectroscopy.set_wavelength', wavelength_m=520e-9)
        r.load('syn')
        self.assertEqual(r.code('spectroscopy.measure'), 'LAMP_OFF')
        r.call(r.A, 'lamp.set', lamp='vis', on=True)
        r.call(r.A, 'spectroscopy.measure')

    def test_panel_commands_run_as_the_gesturing_session(self):
        """A panel gesture's commands run as the session that made it (not a fixed panel principal)."""
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        b = r.h.session('agent_b')
        seen = []
        orig = rt.HachDR6000Session._terminal

        def spy(sess, cap, args, rid):
            seen.append((sess._principal_id, cap))
            return orig(sess, cap, args, rid)
        rt.HachDR6000Session._terminal = spy
        try:
            r.tile('Single Wavelength')
            r.load('blank')
            n = len(seen)
            x, y, w, h = r.widgets()['ms_zero_btn']['rect']                            # agent B touches Zero
            self.assertEqual(b.invoke(capability_id='panel.touch', arguments=dict(display_id=TOUCH, x=x + w // 2, y=y + h // 2),
                                      request_id='b1')['result'], {'accepted': True})
        finally:
            rt.HachDR6000Session._terminal = orig
        self.assertIn(('__agent__', 'program.start'), seen[:n])
        self.assertTrue(seen[n:] and all(p == 'agent_b' for p, _ in seen[n:]), seen[n:])
        self.assertIsNone(r.h._caller)
        self.assertFalse([k for k in r.h._requests if k[1].startswith('__panel_')])

    def test_every_terminal_command_once(self):
        r = Rig()
        meta = rt.export_hach_dr6000_contract_metadata()
        r.load('blank')
        plan = [('program.start', {'kind': 'stored', 'number': 490}), ('program.options', {'settings': {'dilution': 2.0}}),
                ('reading.zero', {}), ('load', 'po4'), ('settings.configure', {'settings': {'log.auto_store': False}}), ('reading.read', {}), ('program.standard_adjust', {'action': 'adjust', 'standard': 0.0}),
                ('standard_addition.control', {'action': 'on'}), ('data.store', {'target': 'log'}), ('data.manage', {'source': 'log', 'action': 'delete',
                                                                                                                       'scope': 'all'}),
                ('favorites.manage', {'action': 'add', 'kind': 'stored', 'number': 490}), ('timer.control', {'action': 'start', 'duration_s': 120.0}),
                ('program.exit', {}), ('program.start', {'kind': 'time'}), ('load', 'blank'), ('reading.zero', {}), ('reading.read', {}),
                ('timecourse.control', {'action': 'mark'}), ('timecourse.control', {'action': 'stop'}), ('user_program.edit', {'action': 'new'}), ('operator.manage', {'action': 'new', 'name': 'OP1'}),
                ('sample_id.manage', {'action': 'new', 'name': 'Inflow', 'add_number': True}), ('security.configure', {'action': 'on', 'password': '1234'}),
                ('security.check', {'function': 'Instrument Setup'}),
                ('clock.set', {'date': '2026-10-19', 'time': '09:30'}), ('lamp.set', {'lamp': 'uv', 'on': True}),
                ('system.check', {'check': 'wavelength', 'action': 'start', 'wavelength_m': 656.1e-9}),
                ('aqa.configure', {'item': 'standard', 'action': 'new', 'settings': {'name': 'PO4 1.0', 'concentration': 1.0, 'unit': 'mg/L', 'ci': 0.1}}),
                ('aqa.measure', {'standard': 0}), ('service.configure', {'enabled': True, 'last_date': '2026-01-01', 'interval_days': 365}),
                ('lamp_history.reset', {'lamp': 'vis'}), ('load', None), ('power.sleep', {}), ('power.sleep', {})]
        done = set()
        skip = {'program.standard_adjust'}
        for cap, args in plan:
            if cap == 'load':
                r.load(args)
                continue
            m = meta['capabilities'][cap]
            if cap == 'program.standard_adjust':
                args = {'action': 'adjust', 'standard': r.val('dr.result')['base_conc'] * 1.1}
            if cap == 'reading.read' and r.val('dr.mode')['kind'] == 'time':
                r.load('syn')
            before = r.E.read()['observations']
            res = r.call(r.A, cap, **args)
            self.assertEqual(validate_value(res, m['output_schema']), [], cap)
            after = r.E.read()['observations']
            changed = {k for k in after if after[k]['value'] != before[k]['value']} - {'dr.datetime', 'dr.status', 'dr.timer', 'dr.lamps'}
            if cap not in skip:
                self.assertTrue(changed <= set(m['state_ids']) | {'dr.programs'}, (cap, changed - set(m['state_ids'])))
            done.add(cap)
        r.step(121)
        r.call(r.A, 'program.start', kind='stored', number=490)
        r.call(r.A, 'trend.manage', action='new', parameter='490 P React. PP') if r.val('dr.data_log') else None
        r.call(r.A, 'settings.configure', settings={'log.auto_store': True})
        r.load('blank')
        r.call(r.A, 'reading.zero')
        r.load('po4')
        r.call(r.A, 'reading.read')
        r.call(r.A, 'trend.manage', action='new', parameter='490 P React. PP')
        done.add('trend.manage')
        r2 = Rig(on=False)
        r2.switch('on')
        r2.touch(10, 10)
        r2.step(46)
        r2.call(r2.A, 'language.select', language='English')
        r3 = Rig(on=False)
        r3.load('syn')
        r3.switch('on')
        r3.step(166)
        r3.load(None)
        r3.call(r3.A, 'message.confirm')
        done |= {'language.select', 'message.confirm'}
        r4 = Rig()
        r4.call(r4.E, 'sensor.configure_error', errors={'photometric_gain': 0.01})
        r4.call(r4.A, 'verification.configure', index=1, tolerance=0.006)
        r4.call(r4.A, 'verification.run', action='start')
        r4.call(r4.A, 'lid.set_angle', angle_rad=rt.HACH_DR6000_LID_MAX_RAD)
        r4.call(r4.E, 'verification_kit.place', filter='SL340')
        r4.call(r4.A, 'lid.set_angle', angle_rad=0.0)
        self.assertEqual(validate_value(r4.call(r4.A, 'verification.run', action='next'), meta['capabilities']['verification.run']['output_schema']), [])
        r.call(r.A, 'ratio.manage', action='new', sample_id='Inflow', parameters=['490 P React. PP', '490 P React. PP'], expect='rejected')
        r.call(r.A, 'ratio.manage', action='new', sample_id='Inflow', parameters=['490 P React. PP', '10 Aluminum Alumin'], expect='rejected')
        r.call(r.A, 'program.start', kind='stored', number=9)
        r.load('blank')
        r.call(r.A, 'reading.zero')
        r.h.register_template_sample('al', template_id='hach_prog_9', concentration_kg_m3=1e-4)
        r.load('al')
        r.call(r.A, 'reading.read')
        r.call(r.A, 'ratio.manage', action='new', sample_id='Inflow', parameters=['490 P React. PP', '9 Aluminum ECR'])
        done |= {'sensor.configure_error', 'verification.configure', 'verification.run', 'verification_kit.place', 'ratio.manage'}
        r5 = Rig()                                                                    # consistency sweep 2026-10-19: evaluator-only errors
        self.assertEqual(r5.call(r5.E, 'fault.inject', code='temperature_high'), {'code': 'temperature_high'})
        self.assertEqual(r5.call(r5.E, 'fault.clear'), {'cleared': True})
        done |= {'fault.inject', 'fault.clear'}
        self.assertEqual(done, set(rt._DR_TERMINAL))

    def test_refusals_leave_state_unchanged(self):
        r = Rig()
        r.call(r.A, 'program.start', kind='single')
        before = r.h.debug_snapshot()
        for cap, args, code in (('reading.read', {}, 'ZERO_FIRST'), ('program.options', {'settings': {'start_m': 4e-7}}, 'INVALID_ARGUMENT'),
                                ('program.options', {'settings': {'result_mode': 'conc'}}, 'PRECONDITION_FAILED'),
                                ('program.start', {'kind': 'stored', 'number': 1}, 'NOT_FOUND'), ('data.store', {'target': 'scan'}, 'PRECONDITION_FAILED'),
                                ('timecourse.control', {'action': 'stop'}, 'PRECONDITION_FAILED'), ('operator.manage', {'action': 'login', 'name': 'x'}, 'NOT_FOUND'),
                                ('security.configure', {'action': 'off', 'password': 'x'}, 'PRECONDITION_FAILED'),
                                ('user_program.edit', {'action': 'store'}, 'PRECONDITION_FAILED'),
                                ('data.manage', {'source': 'log', 'action': 'send'}, 'NO_USB'), ('trend.manage', {'action': 'new', 'parameter': 'x'}, 'NO_DATA'),
                                ('message.confirm', {}, 'PRECONDITION_FAILED'), ('language.select', {'language': 'English'}, 'PRECONDITION_FAILED')):
            self.assertEqual(r.code(cap, **args), code, cap)
        self.assertEqual(before, r.h.debug_snapshot())
        r.call(r.A, 'lid.set_angle', angle_rad=1.0)
        self.assertEqual(r.code('reading.zero'), 'COVER_OPEN')


class Sweep(unittest.TestCase):
    """Consistency sweep 2026-10-19: a time course belongs to its caller; instrument errors of p.139-141 injected by the evaluator."""
    def start_time_course(self, r):
        """A starts a time course on the touch screen (Time Course tile, Zero, Read)."""
        r.tile('Time Course')
        r.load('blank')
        r.tap('tc_zero')
        r.load('syn')
        r.tap('tc_read')

    def test_time_course_belongs_to_its_caller(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        self.start_time_course(r)
        self.assertTrue(r.val('dr.time_course')['running'])
        for cap, args in (('timecourse.control', {'action': 'stop'}), ('program.exit', {}), ('power.sleep', {})):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertTrue(r.val('dr.time_course')['running'])
        x, y, w, h = r.widgets()['tc_read']['rect']                                   # B taps Stop on the touch screen
        self.assertFalse(B.invoke(capability_id='panel.touch', arguments=dict(display_id=TOUCH, x=x + w // 2, y=y + h // 2),
                                  request_id='b1')['result']['accepted'])
        self.assertTrue(r.val('dr.time_course')['running'])
        self.assertEqual(r.overlay()['lines'], ["Another user's reading is running."])
        r.ov('OK')
        r.call(r.E, 'timecourse.control', action='stop')                             # the evaluator is unrestricted
        self.assertFalse(r.val('dr.time_course')['running'])
        r.call(r.A, 'reading.read')                                                   # A reads the next time course
        self.assertTrue(B.invoke(capability_id='panel.switch', arguments=dict(input_id='power_switch', position='off'),
                                 request_id='b2')['result']['accepted'])               # the power switch is physical
        self.assertFalse(r.val('power.enabled'))

    def test_injected_instrument_errors(self):
        r = Rig()
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='lamp_failure')['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('fault.inject', r.A.describe()['capabilities'])
        r.call(r.E, 'fault.inject', code='lamp_failure')
        self.assertEqual(r.frame()['screen'], 'fault')
        self.assertEqual((r.text('fx_title'), r.text('fx_l1'), r.text('fx_l2')), ('Fault', 'Test program stopped!', 'Please check lamp'))
        self.assertFalse(r.tap('fx_ok'))                                              # OK does not clear it: the lamp must be checked first
        r.load('syn')
        self.assertEqual(r.code('spectroscopy.measure'), 'NOT_READY')
        r.switch('off')                                                               # a power cycle does not repair the lamp
        r.switch('on')
        r.step(166)
        self.assertEqual((r.frame()['screen'], r.text('fx_l2')), ('fault', 'Please check lamp'))
        r.call(r.E, 'fault.clear')                                                    # new lamp: the self-check runs again
        r.load(None)
        r.step(130)
        self.assertEqual(r.val('dr.status')['phase'], 'ready')
        r.call(r.E, 'fault.inject', code='temperature_high')
        self.assertEqual((r.text('fx_title'), r.text('fx_l1'), r.text('fx_l2')), ('Error', 'Temperature too high.', 'Measurement not possible!'))
        r.switch('off')                                                               # the remedy: switch off and let it cool
        r.switch('on')
        r.step(166)
        self.assertEqual(r.val('dr.status')['phase'], 'ready')
        r.call(r.E, 'fault.inject', code='unstable_lighting')                         # a warning; readings go on
        self.assertEqual(r.val('dr.status')['notice'], 'Unstable lighting conditions!')
        self.assertEqual(r.val('dr.status')['phase'], 'ready')


class RenderEvidence(unittest.TestCase):
    def test_render_determinism_and_evidence(self):
        r = Rig()
        a, b = r.png(), r.png()
        self.assertEqual(a, b)
        self.assertEqual(hashlib.sha256(Rig().png()).hexdigest(), hashlib.sha256(a).hexdigest())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {}
        r.h.panel.save_png(TOUCH, OUT / 'home.png')
        shots['home.png'] = 'Main Menu with the toolbar'
        r.stored(490)
        r.zero_read()
        r.h.panel.save_png(TOUCH, OUT / 'result.png')
        shots['result.png'] = 'Stored program 490: result after Zero / Read'
        r.tap('ms_options')
        r.tile('More...')
        r.h.panel.save_png(TOUCH, OUT / 'menu.png')
        shots['menu.png'] = 'Options > More...'
        r.tap('mo_return')
        r.tap('op_return')
        r.call(r.A, 'lid.set_angle', angle_rad=1.0)
        r.tap('ms_read')
        r.h.panel.save_png(TOUCH, OUT / 'error.png')
        shots['error.png'] = 'Read with the cover open: Please close the cover.'
        r.ov('OK')
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        r.tap('ms_menu')
        r.tile('Wavelength Scan')
        r.load('blank')
        r.tap('sc_zero')
        r.load('syn')
        r.tap('sc_read')
        r.tap('sc_mode')
        r.tap('sc_right')
        r.h.panel.save_png(TOUCH, OUT / 'scan.png')
        shots['scan.png'] = 'Wavelength scan with the Peak cursor'
        (OUT / 'renders.json').write_text(json.dumps({k: dict(caption=v, sha256=hashlib.sha256((OUT / k).read_bytes()).hexdigest())
                                                      for k, v in shots.items()}, indent=1) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_has_the_panel(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_hach_dr6000_contract_metadata()
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for cap in list(rt._DR_TERMINAL) + sorted(rt.DR6000_PANEL_CAPS):
            self.assertEqual(c['capabilities'][cap]['input_schema'], meta['capabilities'][cap]['input_schema'], cap)
        for comp in ('power_switch', 'power_save_key', 'display'):
            self.assertIn(comp, c['components'])
        self.assertEqual(c['asset_version'], rt.DR6000_ASSET_VERSION)


if __name__ == '__main__':
    unittest.main()
