"""front-panel tests for CHEM-004 antonpaar_abbematadvanced (panel_ui.json + scripts/asset_runtime/_panel_ui.py + _antonpaar_touch.py on
scripts/asset_runtime/antonpaar_abbematadvanced.py): python3 -m pytest tests/test_panel_ui_antonpaar_abbematadvanced.py -q

Every gesture goes through the session API (panel.touch / panel.switch); widgets are found by id or text in the structured frame (panel.frame,
evaluator).  Themes: the six key procedures of the menu survey; the device functions of the M series software (messages, favorites, sample
list, methods, data memory, checks, adjustments, user functions, users and security, control panel, printout, service); panel = direct
capability; refusals shown as the device's own message; time-outs; render determinism and the evidence PNGs; contract entries."""
import copy
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import antonpaar_abbematadvanced as rt  # noqa: E402
from antonpaar_panel_rig import Rig as _Rig  # noqa: E402
from asset_tool import validate_value  # noqa: E402

CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
OUT = ROOT / 'outputs/panel_ui/antonpaar_abbematadvanced'
SURVEY_TASKS = ['measure a sample', 'enter sample name and temperature for next measurement', 'set method temperature and accuracy',
                'set a temperature scan', 'change a result field to Brix', 'log in']
DONE_TASKS = set()


class Rig(_Rig):
    def __init__(self, power=True):
        super().__init__(rt, 'AntonpaarAbbematadvancedRuntime', power=('mains_switch', 'on') if power else None)

    def load(self, fractions=None, volume_m3=100e-9):
        """The trusted host puts a liquid on the prism (cover open, then closed)."""
        self.call(self.A, 'lid.set_angle', angle_rad=math.pi / 2)
        self.call(self.E, 'sample.configure', volume_fractions=fractions or {'ethanol': 1.0}, volume_m3=volume_m3, matrix_id='ethanol',
                  contamination_fraction=0.0)
        self.call(self.A, 'lid.set_angle', angle_rad=0.0)

    def clean(self):
        self.call(self.A, 'lid.set_angle', angle_rad=math.pi / 2)
        self.call(self.A, 'cleaning.start', volume_m3=1e-6)
        self.step(35)

    def go(self, *path):
        """From the main screen: <Menu>, then the rows of the menu levels."""
        self.home()
        self.tap('main_menu')
        for p in path:
            assert self.row(p), p

    def home(self):
        """Back to the main screen with the 'Main' field of the breadcrumb (abm p.28)."""
        for _ in range(3):
            if self.overlay():
                self.ov(self.overlay()['buttons'][0]['label'])
        scr, pid = self.page()
        if (scr, pid) != ('main', 'home'):
            self.tap(f'{pid}_crumb0')
        assert self.page() == ('main', 'home'), self.page()

    def readout(self):
        return self.val('display.readout')

    def field_text(self, i):
        return self.readout()['fields'][i]['text']

    def measure(self, seconds=200):
        self.home()
        assert self.tap('main_start')
        self.step(seconds)


# ---------------------------------------------------------------- the key procedures of the survey
class SurveyProcedures(unittest.TestCase):
    def test_measure_a_sample(self):
        r = Rig()
        r.load()
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertEqual(r.text('main_start'), 'Start')
        self.assertTrue(r.tap('main_start'))                                   # <Start>
        self.assertEqual(r.val('cycle.phase'), 'running')
        self.assertTrue(r.has_button('Stop'))
        r.step(3)
        ro = r.readout()
        self.assertEqual(ro['progress']['color'], 'red')
        self.assertIn('Measuring', ro['progress']['text'])
        r.step(30)
        ro = r.readout()
        self.assertEqual((ro['progress']['color'], ro['progress']['text']), ('green', 'Sample 1: Finished'))
        self.assertEqual(ro['fields'][4]['text'], 'valid')                     # Master Condition
        rec = r.val('data.measured')[-1]
        self.assertEqual((rec['sample_name'], rec['condition'], rec['values']['nD']), ('Sample 1', 'valid', 1.3611))
        self.assertEqual(r.field_text(0), '1.36110')
        self.assertEqual(r.val('optical.indicated_index'), 1.3611)
        self.assertGreater(r.val('device.system')['beeps'], 0)                 # beep at the end of a measurement
        DONE_TASKS.add('measure a sample')

    def test_sample_name_and_temperature_by_quick_settings(self):
        r = Rig()
        r.load()
        r.tap('main_quick')
        self.assertEqual(r.page(), ('main', 'quick_settings'))
        r.enter('Sample Name', 'Batch 7')
        r.enter('Temperature', '25')
        r.choose('Measurement Accuracy', 'Most precise')
        r.enter('Hold Time', '3')
        r.tap_text('OK')
        self.assertEqual(r.page(), ('main', 'home'))
        q = r.val('sample.quick_settings')
        self.assertEqual((q['sample_name'], q['ri.temperature'], q['ri.accuracy'], q['ri.hold_s']), ('Batch 7', 25.0, 'Most precise', 3))
        self.assertAlmostEqual(r.val('thermal.target_k'), 298.15)
        self.assertEqual(r.val('methods.list')[0]['settings']['ri.temperature'], 20.0)   # the method itself is unchanged
        r.measure(300)
        rec = r.val('data.measured')[-1]
        self.assertEqual(rec['sample_name'], 'Batch 7')
        self.assertAlmostEqual(rec['values']['ri_temperature'], 25.0, delta=0.006)
        self.assertAlmostEqual(rec['values']['nD'], 1.3591, delta=1e-5)
        DONE_TASKS.add('enter sample name and temperature for next measurement')

    def test_method_temperature_and_accuracy(self):
        r = Rig()
        r.go('Methods', 'Method Settings', 'Refractive Index', 'Refractometer Module')
        self.assertEqual(r.page(), ('main', 'ri_module'))
        r.enter('Temperature', '30')
        r.choose('Measurement Accuracy', 'Fast Measuring')
        r.enter('Hold Time', '5')
        r.enter('Timeout', '300')
        r.tap_text('OK')
        self.assertEqual(r.page(), ('main', 'method_menu'))
        s = r.val('methods.list')[0]['settings']
        self.assertEqual((s['ri.temperature'], s['ri.accuracy'], s['ri.hold_s'], s['ri.timeout_s']), (30.0, 'Fast Measuring', 5, 300))
        self.assertAlmostEqual(r.val('thermal.target_k'), 303.15)               # the current method reaches the Peltier
        self.assertEqual(r.val('sample.quick_settings')['ri.accuracy'], 'Fast Measuring')
        DONE_TASKS.add('set method temperature and accuracy')

    def test_temperature_scan(self):
        r = Rig()
        r.load()
        r.go('Methods', 'Method Settings', 'Refractive Index', 'Measurement Mode')
        r.choose('Measurement Mode', 'TS (Temperature Scan)')
        r.tap_text('Next')
        self.assertEqual(r.page(), ('main', 'measurement_mode2'))
        r.enter('Start', '20')
        r.enter('Step', '5')
        r.enter('End', '30')
        r.tap_text('OK')
        m = r.val('methods.list')[0]
        self.assertEqual((m['mode'], m['ts_start'], m['ts_step'], m['ts_end']), ('TS', 20.0, 5.0, 30.0))
        r.home()
        r.measure(1500)
        scan = [x for x in r.val('data.measured') if x['kind'] == 'scan']
        self.assertEqual([round(x['values']['ri_temperature']) for x in scan], [20, 25, 30])
        self.assertTrue(scan[0]['values']['nD'] > scan[1]['values']['nD'] > scan[2]['values']['nD'])
        self.assertEqual(r.val('data.statistics'), [])                         # scans are not part of the statistics (abm p.91)
        DONE_TASKS.add('set a temperature scan')

    def test_change_a_result_field(self):
        """The survey changes a field to Brix; the factory concentration scales are not loaded, so the field becomes the user-calibrated
        concentration (same dialog: Group, Quantity, Unit, Digits)."""
        r = Rig()
        r.tap('field_L0_1')                                                    # tap the RI Set Temperature field
        self.assertEqual(r.page(), ('main', 'output_field'))
        r.choose('Group', 'RI Basic Scales')
        r.choose('Quantity', 'Concentration (user calibration)')
        r.choose('Unit', 'g/kg')
        r.enter('Digits', '1')
        r.tap_text('OK')
        self.assertEqual(r.page(), ('main', 'home'))
        f = r.val('methods.list')[0]['fields'][1]
        self.assertEqual((f['q'], f['unit'], f['digits']), ('concentration', 'g/kg', 1))
        self.assertEqual(r.readout()['fields'][1]['label'], 'Concentration (user calibration)')
        DONE_TASKS.add('change a result field to Brix')

    def test_log_in(self):
        r = Rig()
        self.assertEqual(r.text('main_user_icon'), 'ADM')                       # Administrator logged on by auto logon (abm p.47)
        r.tap('main_user_icon')
        self.assertEqual(r.page(), ('main', 'logon'))
        r.choose('User name', 'Operator')
        r.enter('Password', 'operator')
        r.tap_text('OK')
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertEqual(r.val('device.users')['current'], 'Operator')
        self.assertEqual((r.text('main_user_icon'), r.text('main_user')), ('OPR', 'Operator'))
        DONE_TASKS.add('log in')


# ---------------------------------------------------------------- device functions through the touch screen
class Workflows(unittest.TestCase):
    def test_messages_limits_and_status_icon(self):
        r = Rig()
        r.load()
        r.go('Methods', 'Method Settings', 'Refractive Index', 'Limits')
        r.tap_text('New')
        r.choose('Group', 'RI Basic Scales')
        r.choose('Quantity', 'Refractive Index')
        r.enter('Lower limit', '1.30')
        r.enter('Upper limit', '1.35')
        r.tap_text('OK')
        self.assertEqual(r.val('methods.list')[0]['limits'], [dict(q='nD', lower=1.3, upper=1.35)])
        r.home()
        r.measure(60)
        self.assertEqual((r.text('qa_messages'), r.readout()['status']), ('!', 'warning'))       # yellow sign (abm p.25, p.77)
        self.assertEqual(r.val('data.measured')[-1]['error_state'], 'Error')
        r.tap('qa_messages')
        self.assertEqual(r.page(), ('main', 'messages'))
        self.assertIn('outside the limits', r.list_widget()['cells'][0][1])
        r.row('Warning')                                                        # <X> on the message
        self.assertEqual(r.val('device.messages'), [])
        self.assertEqual(r.list_widget()['empty_text'], 'No messages: the instrument is OK.')
        r.home()
        self.assertEqual(r.text('qa_messages'), 'OK')

    def test_favorites(self):
        r = Rig()
        r.tap('main_quick')
        r.tap('quick_settings_fav')                                             # <Add to Favorites> in the header
        self.assertEqual(r.dialog(), 'favorite_add')
        r.ov('OK')
        self.assertEqual([f['name'] for f in r.val('device.favorites')], ['Message List', 'Monitor Mode', 'Quick Settings'])
        r.home()
        r.tap('qa_favorites')
        self.assertEqual(r.page(), ('main', 'quick_access'))
        r.row('Quick Settings')
        self.assertEqual(r.page(), ('main', 'quick_settings'))
        self.assertIn('f_quick', r.frame()['ui']['forms'])
        r.home()
        r.tap('qa_favorites')
        r.tap_text('Settings')
        r.row('Quick Settings')
        r.tap_text('Up')
        self.assertEqual([f['page'] for f in r.val('device.favorites')], ['messages', 'quick_settings', 'monitor'])
        r.tap_text('Rename')
        ov = r.overlay()
        self.assertEqual(ov['dialog'], 'favorite_rename')
        r.ov('Name')
        r.type('QS')
        r.ov('OK')
        r.ov('OK')
        self.assertEqual(r.val('device.favorites')[1]['name'], 'QS')
        r.row('Message List')
        r.tap_text('Delete')                                                    # fixed favorites cannot be deleted (abm p.36)
        self.assertEqual(r.dialog(), 'err_NOT_ALLOWED')
        r.ov('OK')
        r.row('QS')
        r.tap_text('Delete')
        self.assertEqual(len(r.val('device.favorites')), 2)

    def test_sample_list_mode(self):
        r = Rig()
        r.go('Setup', 'Measuring System Settings', 'Sample List / Sample Settings', 'Sample List / Sample Naming')
        r.choose('Sample List Mode', 'Sample List')
        r.enter('Sample Name Postfix', '_x')
        r.tap_text('OK')
        r.home()
        self.assertFalse(any(w['id'] == 'main_quick' for w in r.disp()['widgets']))
        r.tap('main_sample_list')
        for name in ('S1', 'S2'):
            r.tap_text('New Sample')
            r.enter('Sample Name', name)
            r.tap_text('OK')
        self.assertEqual([e['name'] for e in r.val('sample.list')['entries']], ['S1', 'S2'])
        r.tap_text('List Management')
        r.choose('List Management', 'Save Sample List')
        r.enter('Template name', 'Daily')
        r.tap_text('OK')
        self.assertEqual(r.val('sample.list')['templates'][0]['name'], 'Daily')
        r.home()
        r.load()
        r.measure(60)
        e = r.val('sample.list')['entries']
        self.assertEqual([x['status'] for x in e], ['measured', 'pending'])
        self.assertEqual(r.val('data.measured')[-1]['sample_name'], 'S1_x')     # postfix added after the measurement (abm p.45)
        r.tap('main_sample_list')
        r.row('S1')
        r.tap_text('Delete Sample')                                             # measured samples cannot be deleted (abm p.83)
        self.assertEqual(r.dialog(), 'err_NOT_ALLOWED')
        r.ov('OK')
        r.tap_text('List Management')
        r.choose('List Management', 'Clear Sample List')
        r.tap_text('OK')
        self.assertEqual(r.val('sample.list')['entries'], [])
        r.tap_text('List Management')
        r.choose('List Management', 'Load Sample List')
        r.choose('Sample list templates', 'Daily')
        r.tap_text('OK')
        self.assertEqual([x['name'] for x in r.val('sample.list')['entries']], ['S1', 'S2'])

    def test_method_management_and_selection(self):
        r = Rig()
        r.go('Methods', 'Method Management')
        r.row('Refractive Index')
        r.tap_text('Copy')
        self.assertEqual([m['name'] for m in r.val('methods.list')], ['Refractive Index', 'Refractive Index (1)'])
        r.row('Refractive Index (1)')
        r.tap_text('Rename')
        r.ov('New method name')
        r.type('Oils 40C')
        r.ov('OK')
        r.ov('OK')
        r.row('Refractive Index')
        r.tap_text('Delete')
        r.ov('Yes')                                                             # the current method cannot be deleted (abm p.80)
        self.assertEqual(r.dialog(), 'err_IN_USE')
        r.ov('OK')
        r.home()
        r.tap('main_method_btn')
        r.row('Oils 40C')
        r.tap_text('OK')
        self.assertEqual((r.val('methods.active'), r.text('main_method')), ('Oils 40C', 'Method: Oils 40C'))
        r.go('Methods', 'Method Visibility')
        r.row('Refractive Index', col=1)
        self.assertFalse(r.val('methods.list')[0]['visible'])
        r.tap_text('Select all')
        self.assertTrue(all(m['visible'] for m in r.val('methods.list')))

    def test_data_memory_details_filter_statistics_delete(self):
        r = Rig()
        r.load()
        for name in ('A1', 'B1', 'A2'):
            r.tap('main_quick')
            r.enter('Sample Name', name)
            r.tap_text('OK')
            r.measure(30)
        r.go('Data Memory', 'Measurement Data', 'Measured Data')
        self.assertEqual([c[0] for c in r.rows('md_rows')], ['A1', 'B1', 'A2'])
        r.row('B1')
        r.tap_text('Details')
        det = r.cells('md_details_rows')
        self.assertEqual((det['Sample Name'], det['Refractive Index'], det['Sample Error State']), ('B1', '1.36110 nD', 'No Error'))
        r.tap_text('>')
        self.assertEqual(r.cells('md_details_rows')['Sample Name'], 'A2')
        r.tap_text('Back')
        r.tap_text('Filter')
        r.tap_text('New')
        r.choose('Filter criterion', 'Sample Name')
        r.choose('Comparator', 'containing')
        r.enter('Value', 'A')
        r.tap_text('OK')
        r.tap_text('OK')
        self.assertEqual([c[0] for c in r.rows('md_rows')], ['A1', 'A2'])
        self.assertEqual(r.text('md_filtered'), 'Measured Data (filt.)')
        r.go('Data Memory', 'Measurement Data', 'Statistic')
        r.row('Refractive Index')
        r.tap_text('Details')
        st = r.rows('stat_detail_rows')[0]
        self.assertEqual(st[:2], ['Refractive Index', '2'])                     # statistics follow the data browser filter (abm p.91)
        r.go('Data Memory', 'Measurement Data', 'Delete Measured Data')
        r.tap_text('Filter')
        r.tap_text('New')
        r.choose('Filter criterion', 'Sample Name')
        r.choose('Comparator', 'equals')
        r.enter('Value', 'B1')
        r.tap_text('OK')
        r.tap_text('OK')
        r.tap_text('Delete All')
        r.ov('Yes')
        self.assertEqual([x['sample_name'] for x in r.val('data.measured')], ['A1', 'A2'])
        r.go('Data Memory', 'Measurement Data', 'Settings', 'Data Browser Settings')
        r.tap_text('New')
        r.choose('Group', 'System Settings')
        r.choose('Quantity', 'User')
        r.tap_text('OK')
        self.assertEqual(r.val('device.lists')['browser'][-1], 'user')

    def test_reference_standard_check(self):
        r = Rig()
        r.go('Checks / Adjustments', 'Settings', 'RI Reference Standard Management')
        r.tap_text('New')
        r.enter('Substance name', 'Toluene std')
        r.enter('Reference temperature', '20')
        r.enter('Reference refractive index', '1.4967')
        r.tap_text('OK')
        self.assertEqual(r.val('standards.list')[-1]['nd'], 1.4967)
        r.go('Checks / Adjustments', 'Settings', 'Check Management')
        r.tap_text('New')
        r.enter('Name', 'Toluene check')
        r.choose('Check type', 'Reference Standard Check')
        r.choose('Reference standard', 'Toluene std')
        r.tap_text('Next')
        r.choose('Check execution reminder', 'On')
        r.enter('Interval', '7')
        r.tap_text('Next')
        r.enter('Max. deviation', '0.0002')
        r.tap_text('OK')
        c = r.val('checks.list')[-1]
        self.assertEqual((c['name'], c['kind'], c['standard'], c['interval_days'], c['max_dev']), ('Toluene check', 'Reference Standard Check', 'Toluene std', 7, 0.0002))
        r.load({'toluene': 1.0})
        r.go('Checks / Adjustments', 'Checks Execution')
        r.row('Toluene check')
        r.tap_text('Start')
        r.ov('OK')
        self.assertEqual(r.page()[0], 'running')
        r.step(200)
        self.assertEqual(r.page()[0], 'check_result')
        res = r.cells('check_result_rows')
        self.assertEqual((res['Result'], res['Reference value'], res['Measured value']), ('passed', '1.49670', '1.49670'))
        r.tap('check_result_ok')
        self.assertEqual(r.page(), ('main', 'checks_execution'))
        self.assertEqual(r.val('data.checks')[-1]['result'], 'passed')

    def test_one_point_adjustment_corrects_an_offset(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', offset_nd=0.0005, gain=0.0, baseline_nd=0.0)
        r.load()
        r.measure(40)
        self.assertEqual(r.val('data.measured')[-1]['values']['nD'], 1.3616)    # a reading error to correct
        r.go('Checks / Adjustments', 'Refractometer Adjustments', 'One-point Adjustment')
        r.choose('Reference standard', 'Other')
        r.enter('Name', 'Ethanol std')
        r.enter('Refractive index', '1.3611')
        r.tap_text('OK')
        self.assertEqual(r.dialog(), 'adjust_fill_one')
        r.ov('OK')
        self.assertEqual(r.page()[0], 'running')
        r.step(60)
        self.assertEqual(r.page()[0], 'adjust_result')
        self.assertEqual(r.text('adj_rec'), 'Recommendation: Apply')
        self.assertEqual(r.list_widget('adj_rows')['cells'][0], ['Ethanol std', 'old 1.361600', 'new 1.361100', 'dev +0.000500'])
        r.tap('adj_apply')
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertAlmostEqual(r.val('adjustment.correction')['offset'], -0.0005)
        r.measure(40)
        self.assertEqual(r.val('data.measured')[-1]['values']['nD'], 1.3611)
        self.assertEqual(r.val('data.adjustments')[-1]['type'], 'One-point Adjustment')
        r.go('Checks / Adjustments', 'Refractometer Adjustments', 'Reset to Factory Adjustment')
        r.ov('OK')
        r.ov('OK')
        self.assertTrue(r.val('adjustment.correction')['factory'])

    def test_two_point_adjustment_and_air_reference(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', offset_nd=0.0, gain=0.01, baseline_nd=0.0)
        r.go('Checks / Adjustments', 'Refractometer Adjustments', 'Two-point Adjustment')
        r.choose('Lower standard', 'Other')
        r.enter('Name', 'Ethanol std')
        r.enter('Refractive index', '1.3611')
        r.tap_text('Next')                                                      # the standard with the higher nD (abm p.67)
        r.choose('Upper standard', 'Other')
        r.enter('Name', 'Toluene std')
        r.enter('Refractive index', '1.4967')
        r.load()
        r.tap_text('OK')
        r.ov('OK')
        r.step(60)
        self.assertEqual(r.page()[0], 'fill_prompt')                           # second standard: exchange the sample
        self.assertIn('"Toluene std"', r.text('prompt_text'))
        r.clean()
        r.load({'toluene': 1.0})
        r.tap('prompt_ok')
        r.step(60)
        self.assertEqual(r.page()[0], 'adjust_result')
        r.tap('adj_apply')
        a = r.val('adjustment.correction')
        self.assertNotAlmostEqual(a['slope'], 1.0)
        r.measure(40)
        self.assertAlmostEqual(r.val('data.measured')[-1]['values']['nD'], 1.4967, places=6)   # the slope error is corrected
        r.clean()
        r.call(r.E, 'sensor.configure_error', offset_nd=0.0, gain=0.0, baseline_nd=0.0003)
        r.go('Checks / Adjustments', 'Refractometer Adjustments', 'Set Air Reference')
        r.tap_text('OK')                                                        # cover still open after cleaning: the device says so
        self.assertEqual(r.dialog(), 'err_LID_OPEN')
        r.ov('OK')
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        r.tap_text('OK')
        r.step(40)
        r.tap('adj_apply')
        self.assertAlmostEqual(r.val('adjustment.correction')['baseline'], -0.0003)


class SetupFunctions(unittest.TestCase):
    def test_users_passwords_and_rights(self):
        r = Rig()
        r.go('Setup', 'User Management', 'User Management')
        r.tap_text('New')
        r.enter('User Name', 'Analyst1')
        r.choose('User Group', 'Operator')
        r.tap_text('OK')
        r.row('Analyst1')
        r.tap_text('Set Password')
        r.ov('Password')
        r.type('lab123')
        r.ov('OK')
        r.ov('OK')
        users = {u['name']: u for u in r.val('device.users')['users']}
        self.assertTrue(users['Analyst1']['has_password'])
        r.home()
        r.tap('main_user_icon')
        r.choose('User name', 'Analyst1')
        r.enter('Password', 'LAB123')                                          # passwords are not case sensitive (abm p.48)
        r.tap_text('OK')
        self.assertEqual(r.val('device.users')['current'], 'Analyst1')
        r.go('Setup', 'Control Panel', 'Display Brightness')                   # an operator cannot change the setup (abm p.139-140)
        r.enter('Display Brightness', '50')
        r.tap_text('OK')
        self.assertEqual(r.dialog(), 'err_PERMISSION_DENIED')
        r.ov('OK')
        self.assertEqual(r.val('device.settings')['control.brightness'], 100)
        r.home()
        r.tap('field_L0_1')                                                    # output fields: managers and administrators only (abm p.77)
        r.choose('Group', 'System Settings')
        r.choose('Quantity', 'User')
        r.tap_text('OK')
        self.assertEqual(r.dialog(), 'err_PERMISSION_DENIED')

    def test_increased_security_locks_after_three_failed_logons(self):
        r = Rig()
        r.go('Setup', 'System Security', 'Security Level')
        r.choose('Security Level', '21 CFR Part 11')
        r.tap_text('OK')
        s = r.val('device.settings')
        self.assertEqual((s['security.audit_trail'], s['security.increased'], s['security.auto_logoff']), (True, True, True))
        self.assertFalse(any(u['auto_logon'] for u in r.val('device.users')['users']))   # no auto logon with increased security (abm p.48)
        r.go('Setup', 'User Management', 'User Management')
        r.row('Manager')
        r.tap_text('Set Password')
        r.ov('Password')
        r.type('manager1')
        r.ov('OK')
        r.ov('OK')
        r.tap_text('Edit')
        r.choose('Account active', 'On')
        r.tap_text('OK')
        r.home()
        for k in range(3):
            r.tap('main_user_icon')
            r.choose('User name', 'Manager')
            r.enter('Password', 'wrong')
            r.tap_text('OK')
            self.assertEqual(r.dialog(), 'err_WRONG_PASSWORD' if k < 2 else 'err_ACCOUNT_LOCKED')
            r.ov('OK')
            r.tap_text('Cancel')
        self.assertFalse({u['name']: u for u in r.val('device.users')['users']}['Manager']['active'])
        r.go('Data Memory', 'Audit Trail Data', 'Audit Trail')
        texts = [c[2] for c in r.rows('audit_rows')]
        self.assertTrue(any('Incorrect password for user Manager' in t for t in texts))
        r.go('Data Memory', 'Audit Trail Data', 'Delete Audit Trail')
        n = len(r.val('data.audit_trail'))
        r.ov('OK')
        self.assertEqual(len(r.val('data.audit_trail')), n)                    # entries not exported cannot be deleted (abm p.57)

    def test_regional_settings_reboot_and_language(self):
        r = Rig()
        epoch = r.val('device.system')['power_epoch']
        r.go('Setup', 'Control Panel', 'Regional Settings')
        r.choose('Language', 'Deutsch')
        r.choose('Data Format', 'Deutsch (Deutschland)')
        r.tap_text('OK')
        self.assertEqual(r.dialog(), 'confirm_regional')
        r.ov('Yes')                                                             # the instrument reboots (abm p.41)
        self.assertEqual(r.val('device.system')['power_epoch'], epoch + 1)
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertEqual((r.text('main_menu'), r.text('main_method_btn')), ('Menü', 'Methode'))
        r.load()
        r.measure(40)
        self.assertEqual(r.field_text(0), '1,36110')                            # decimal comma of the data format
        self.assertRegex(r.text('main_clock'), r'^\d\d:\d\d:\d\d$')

    def test_input_unit_fahrenheit(self):
        r = Rig()
        r.go('Setup', 'Control Panel', 'Input Units')
        r.choose('Input unit for temperature', '°F')
        r.tap_text('OK')
        r.home()
        r.tap('main_quick')
        self.assertEqual(r.cells()['Temperature'], '68 °F')
        r.enter('Temperature', '77')
        r.tap_text('OK')
        self.assertAlmostEqual(r.val('sample.quick_settings')['ri.temperature'], 25.0)
        self.assertAlmostEqual(r.val('thermal.target_k'), 298.15)

    def test_control_panel_settings(self):
        r = Rig()
        r.go('Setup', 'Control Panel', 'Display Brightness')
        r.enter('Display Brightness', '60')
        r.tap_text('OK')
        self.assertEqual(r.frame()['displays']['screen']['brightness'], 60)
        r.go('Setup', 'Control Panel', 'Instrument Name and Location')
        r.enter('Instrument name', 'RI-1')
        r.enter('Instrument location', 'Lab 2')
        r.tap_text('OK')
        r.go('Setup', 'Control Panel', 'Feedback Beeps')
        r.choose('Feedback beep on screen action', 'Off')
        r.tap_text('OK')
        r.go('Setup', 'Control Panel', 'Network')
        r.choose('Obtain an IP address automatically (DHCP)', 'Off')
        r.enter('IP Address', '10.0.0.5')
        r.tap_text('Next')
        r.enter('Primary DNS Server', '10.0.0.1')
        r.tap_text('OK')
        s = r.val('device.settings')
        self.assertEqual((s['instrument.name'], s['instrument.location'], s['control.beep_touch'], s['network.dhcp'], s['network.ip'], s['network.dns1']),
                         ('RI-1', 'Lab 2', False, False, '10.0.0.5', '10.0.0.1'))
        beeps = r.val('device.system')['beeps']
        r.tap_text('Back')
        self.assertEqual(r.val('device.system')['beeps'], beeps)              # no key beep any more
        r.go('Setup', 'Control Panel', 'Date and Time')
        r.row('Date')
        r.ov('+')
        r.ov('OK')
        r.row('Time')
        for _ in range(2):
            r.overlay()
            r.touch(*[b['rect'][0] + 5 for b in r.overlay()['buttons'] if b['id'] == 'inc:0'] + [r.overlay()['buttons'][0]['rect'][1] + 5])
        r.ov('OK')
        r.tap_text('OK')
        self.assertTrue(r.val('device.datetime').startswith('2026-01-06T10:'))

    def test_touch_screen_calibration(self):
        r = Rig()
        r.go('Setup', 'Control Panel', 'Calibrate Touchscreen')
        for k in range(4):
            r.tap(f'cal_cross{k}')
        r.tap('cal_accept')
        cal = r.val('device.touch_calibration')
        self.assertEqual(cal['points'], [[80, 96], [720, 96], [720, 376], [80, 376]])
        self.assertEqual(r.page(), ('main', 'control_panel'))

    def test_user_functions_and_calculators(self):
        r = Rig()
        r.go('Setup', 'Measuring System Settings', 'User Functions / Calculator', 'User Functions Management')
        r.tap_text('New')
        r.enter('Name', 'nD x 100')
        r.choose('Function Type', 'Linear Function')
        r.tap_text('Next')
        r.tap_text('Next')
        r.choose('Input quantity 1', 'Refractive Index')
        r.tap_text('Next')
        r.enter('Factor', '100')
        r.tap_text('OK')
        fn = r.val('functions.list')[0]
        self.assertEqual((fn['type'], fn['inputs'], fn['factor']), ('Linear Function', ['nD'], 100.0))
        r.home()
        r.tap('field_L0_3')
        r.choose('Group', 'User Functions')
        r.choose('Quantity', 'nD x 100')
        r.enter('Digits', '2')
        r.tap_text('OK')
        r.load()
        r.measure(40)
        self.assertEqual(r.field_text(3), '136.11')
        r.go('Setup', 'Measuring System Settings', 'User Functions / Calculator', 'Calculator')
        r.choose('Function', 'nD x 100')
        r.enter('Value (input 1)', '1.5')
        r.tap_text('Calculate')
        self.assertEqual(r.cells()['Result'], '150')
        r.go('Setup', 'Measuring System Settings', 'User Functions / Calculator', 'Group Calculator')
        r.enter('Refractive Index', '1.4')
        r.tap_text('Calculate')
        self.assertEqual(r.cells('group_rows')['nD x 100'], '140')
        r.go('Setup', 'Measuring System Settings', 'User Functions / Calculator', 'User Functions Management')
        r.tap_text('New')
        r.enter('Name', 'Tab1')
        r.choose('Function Type', 'Table')
        r.tap_text('Next')
        r.tap_text('Next')
        r.choose('Input quantity 1', 'Refractive Index')
        r.tap_text('Next')
        r.tap_text('OK')
        r.row('Tab1')
        r.tap_text('Table')
        for x, y in (('1.3', '0'), ('1.4', '10')):
            r.tap_text('New')
            r.enter('Input value', x)
            r.enter('Output value', y)
            r.tap_text('OK')
        self.assertEqual(r.val('functions.list')[1]['table'], [[1.3, 0.0], [1.4, 10.0]])
        r.go('Setup', 'Measuring System Settings', 'User Functions / Calculator', 'Delete User Functions')
        r.row('Tab1')
        r.tap_text('Delete')
        self.assertEqual([f['name'] for f in r.val('functions.list')], ['nD x 100'])

    def test_printers_reports_and_automatic_printout(self):
        r = Rig()
        r.go('Setup', 'Printout Settings', 'Printer Management')
        r.tap_text('New')
        r.enter('Name', 'Lab printer')
        r.choose('Type', 'PCL printer (network)')
        r.enter('IP address', '10.0.0.9')
        r.tap_text('OK')
        self.assertEqual(r.val('device.lists')['printers'][0]['ip'], '10.0.0.9')
        r.go('Setup', 'Printout Settings', 'Report Management')
        r.tap_text('New')
        r.enter('Name', 'Short list')
        r.choose('Type', 'List Report')
        r.tap_text('Next')
        r.choose('Paper', 'Landscape')
        r.tap_text('OK')
        self.assertEqual(r.val('device.lists')['reports'][1]['orientation'], 'Landscape')
        r.go('Setup', 'Printout Settings', 'Automatic Printouts')
        r.choose('After each measurement', 'On')
        r.choose('Printer', 'Lab printer')
        r.tap_text('OK')
        self.assertEqual(r.val('device.settings')['printout.printer'], 'Lab printer')
        r.load()
        r.measure(40)
        self.assertIn('Automatic printout: the printer is not connected.', [m['text'] for m in r.val('device.messages')])
        r.go('Setup', 'Control Panel', 'Network Share Administration')
        r.tap_text('New')
        r.enter('Name', 'LIMS share')
        r.enter('FTP Server Path', '10.0.0.20')
        r.tap_text('OK')
        self.assertEqual(r.val('device.lists')['shares'][0]['path'], '10.0.0.20')

    def test_service_pages_and_devices_absent(self):
        r = Rig()
        r.go('Service', 'System Information')
        self.assertEqual(r.cells()['Serial number'], rt.SERIAL)
        r.tap_text('Save Logfile')
        self.assertEqual(r.dialog(), 'no_storage')
        self.assertTrue(r.frame()['ui']['unsupported'])
        r.ov('OK')
        r.go('Service', 'Updates', 'Module Firmware Update')
        r.tap_text('Update')
        self.assertEqual(r.dialog(), 'firmware_ok')
        self.assertIsNotNone(r.val('device.system')['firmware_checked'])
        r.load()
        r.go('Service', 'Live Raw Data', 'Abbemat')
        self.assertEqual(r.cells()['Refractive index (raw)'], '1.361100')
        r.go('Data Memory', 'Measurement Data', 'Measured Data')
        r.tap_text('Print or Export')                                           # no printer and no USB stick in the simulated configuration
        self.assertEqual(r.dialog(), 'print_export')
        r.ov('OK')
        r.go('Service', 'Instrument Settings Management')
        self.assertFalse(r.row('Backup Instrument Settings'))                   # not accepted: the device's 'not available' response
        self.assertEqual(r.dialog(), 'no_storage')

    def test_electronic_signature_groups_and_user_deletion(self):
        r = Rig()
        r.go('Setup', 'System Security', 'Electronic Signature')
        r.choose('Electronic Signature', 'On')
        r.choose('Electronic Signature Levels', '2 roles')
        r.tap_text('OK')
        r.go('Setup', 'User Management', 'User Management')
        r.row('Administrator')
        r.tap_text('Edit')
        r.choose('Role for Electronic Signature', 'Approver')
        r.tap_text('OK')
        r.load()
        r.measure(40)
        r.go('Data Memory', 'Measurement Data', 'Measured Data')
        r.row('Sample 1')
        r.tap_text('Details')
        r.tap_text('Sign')
        self.assertEqual(r.dialog(), 'sign')
        r.ov('Sign')                                                           # an approver signs first: the submitter level follows (abm p.58-59)
        sig = r.val('data.measured')[-1]['signatures']
        self.assertTrue(sig['approver']['positive'] and sig['submitter']['positive'])
        self.assertEqual(r.cells('md_details_rows')['Signature (approver)'], 'positive (Administrator)')
        r.go('Setup', 'User Management', 'User Group Administration')
        r.row('Operator', col=1)
        r.tap_text('Copy')
        r.row('Operator (1)', col=1)
        r.tap_text('Access')
        r.row('Access')
        r.ov('[x] Start / Stop measurements')
        r.ov('OK')
        r.tap_text('OK')
        g = {x['name']: x for x in r.val('device.users')['groups']}['Operator (1)']
        self.assertNotIn('measure', g['rights'])
        r.go('Setup', 'User Management', 'User Management')
        r.tap_text('New')
        r.enter('User Name', 'Trainee')
        r.choose('User Group', 'Operator (1)')
        r.tap_text('OK')
        r.home()
        r.tap('main_user_icon')
        r.choose('User name', 'Trainee')
        r.tap_text('OK')                                                        # no password set: logon by selecting the name (abm p.48)
        self.assertFalse(r.tap('main_start'))                                   # the custom group has no measuring right
        self.assertEqual(r.dialog(), 'err_PERMISSION_DENIED')
        r.ov('OK')
        r.tap('main_user_icon')
        r.tap_text('Log off')
        self.assertEqual(r.val('device.users')['current'], 'Administrator')   # the auto logon user takes over
        r.go('Setup', 'User Management', 'User Management')
        r.row('Trainee')
        r.tap_text('Delete')
        r.ov('Yes')
        self.assertNotIn('Trainee', [u['name'] for u in r.val('device.users')['users']])


class RunsAndRefusals(unittest.TestCase):
    def test_multiple_measurement_and_multi_fill(self):
        r = Rig()
        r.load()
        r.tap('main_quick')
        r.choose('Type', 'MM (Multiple Measurement)')
        r.enter('Number of measurements', '3')
        r.tap_text('OK')
        r.measure(80)
        recs = r.val('data.measured')
        self.assertEqual([x['sample_name'] for x in recs], ['Sample 1 #1', 'Sample 1 #2', 'Sample 1 #3', 'Sample 1'])
        self.assertTrue(recs[-1]['avg'])
        r.tap('main_quick')
        r.choose('Type', 'MF (Multi Fill)')
        r.enter('Number of fills', '2')
        r.tap_text('OK')
        r.tap('main_start')
        r.step(40)
        self.assertEqual(r.page()[0], 'fill_prompt')                          # exchange the sample (abm p.74)
        r.clean()
        r.load({'methanol': 1.0})
        r.tap('prompt_ok')
        r.step(60)
        recs = r.val('data.measured')[-3:]
        self.assertEqual([x['sample_name'] for x in recs], ['Sample 2 #1', 'Sample 2 #2', 'Sample 2'])
        self.assertEqual((recs[0]['values']['nD'], recs[1]['values']['nD']), (1.3611, 1.3292))

    def test_monitor_mode_unfreezes(self):
        r = Rig()
        r.load()
        r.measure(40)
        ro = r.readout()
        self.assertTrue(ro['frozen'])
        self.assertFalse(r.widgets()['qa_monitor'].get('selected'))
        r.call(r.A, 'lid.set_angle', angle_rad=math.pi / 2)
        r.call(r.A, 'cleaning.start', volume_m3=1e-6)
        r.step(35)
        self.assertEqual(r.field_text(0), '1.36110')                          # frozen until unfrozen (abm p.85)
        r.tap('qa_monitor')
        self.assertEqual((r.readout()['frozen'], r.field_text(0)), (False, '---'))

    def test_refusals_leave_the_state_unchanged(self):
        r = Rig()
        r.call(r.A, 'lid.set_angle', angle_rad=math.pi / 2)
        before = copy.deepcopy(r.h.ap)
        self.assertFalse(r.tap('main_start'))                                   # cover open
        self.assertEqual(r.dialog(), 'err_LID_OPEN')
        self.assertEqual(r.val('cycle.phase'), 'idle')
        after = copy.deepcopy(r.h.ap)
        for k in ('last_input_s', 'beeps'):
            before.pop(k), after.pop(k)
        self.assertEqual(before, after)
        r.ov('OK')
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        r.go('Setup', 'Measuring System Settings', 'Sample List / Sample Settings', 'Mandatory Data Fields')
        r.choose('Sample name is mandatory', 'On')
        r.tap_text('OK')
        r.home()
        self.assertFalse(r.tap('main_start'))
        self.assertEqual(r.dialog(), 'err_MANDATORY_FIELD')

    def test_no_saving_while_measuring(self):
        r = Rig()
        r.load()
        r.tap('main_start')
        r.tap('main_quick')
        self.assertFalse(r.button('OK')['enabled'])                            # <OK> inactive during a measurement (abm p.32)
        r.tap_text('Home')
        self.assertEqual(r.page(), ('main', 'home'))
        r.tap('main_stop')
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        self.assertEqual(r.val('data.measured'), [])

    def test_timeouts(self):
        r = Rig()
        r.call(r.A, 'thermal.set_temperature', temperature_k=293.15)
        r.load()
        r.tap('main_quick')
        r.enter('Temperature', '60')
        r.enter('Timeout', '30')
        r.tap_text('OK')
        r.tap('main_start')
        r.step(40)                                                              # the prism cannot reach 60 °C within 30 s
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        self.assertIn('Measurement timeout: the prism temperature is not stable.', [m['text'] for m in r.val('device.messages')])
        self.assertEqual(r.text('qa_messages'), 'X')
        r.step(30 * 60)                                                         # screen saver after 30 min without input (abm p.34)
        self.assertEqual(r.frame()['displays']['screen']['brightness'], 0)
        self.assertTrue(r.tap('main_menu'))                                     # the first touch only wakes the display
        self.assertEqual((r.page(), r.frame()['displays']['screen']['brightness']), (('main', 'home'), 100))
        r.go('Setup', 'System Security', 'Logon Settings')
        r.choose('Auto logoff', 'On')
        r.enter('Auto logoff time', '5')
        r.tap_text('OK')
        r.home()
        r.tap('main_user_icon')
        r.choose('User name', 'Operator')
        r.enter('Password', 'operator')
        r.tap_text('OK')
        r.step(301)
        self.assertEqual(r.val('device.users')['current'], 'Administrator')   # auto logoff; the auto logon user takes over (abm p.33)


class PanelEqualsDirect(unittest.TestCase):
    def test_quick_settings_and_start_equal_direct_commands(self):
        a, b = Rig(), Rig()
        a.tap('main_quick')
        a.enter('Sample Name', 'X1')
        a.enter('Temperature', '22.5')
        a.tap_text('OK')
        b.call(b.A, 'quick.configure', settings={'sample_name': 'X1', 'ri.temperature': 22.5})
        for r in (a, b):
            r.load()
        a.tap('main_start')
        b.call(b.A, 'measurement.start')
        for r in (a, b):
            r.step(200)
        for sid in ('sample.quick_settings', 'thermal.target_k', 'cycle.phase', 'optical.indicated_index', 'methods.list'):
            self.assertEqual(a.val(sid), b.val(sid), sid)
        strip = lambda recs: [{k: v for k, v in x.items() if k not in ('time',)} for x in recs]   # noqa: E731
        self.assertEqual(strip(a.val('data.measured')), strip(b.val('data.measured')))

    def test_method_settings_equal_direct_command(self):
        a, b = Rig(), Rig()
        a.go('Methods', 'Method Settings', 'Refractive Index', 'Refractometer Module')
        a.enter('Temperature', '35')
        a.tap_text('OK')
        b.call(b.A, 'methods.configure', method='Refractive Index', settings={'ri.temperature': 35.0, 'ri.scale': 'Refractive Index', 'ri.temp_control': True,
                                                                              'ri.accuracy': 'Precise', 'ri.hold_s': 1, 'ri.timeout_s': 200})
        self.assertEqual(a.val('methods.list'), b.val('methods.list'))
        self.assertEqual(a.val('thermal.target_k'), b.val('thermal.target_k'))


class CallerRules(unittest.TestCase):
    """A panel gesture acts as the gesturing session: a run started from the screen belongs to that caller; another agent can neither stop it
    (panel <Stop>, direct acquisition.stop), confirm its next fill, nor decide on its adjustment result.  The rear switch is a hard switch
    (lead rule 2026-10-19): never refused, the instrument reacts as to a power loss."""

    def rig(self):
        r = Rig()
        r.h.register_principal('other', visibility='agent')
        r.B = r.h.session('other')
        return r

    def b_tap(self, r, wid):
        w = r.widgets()[wid]
        x, y, ww, hh = w['rect']
        return r.call(r.B, 'panel.touch', display_id=r.d, x=x + ww // 2, y=y + hh // 2)['accepted']

    def test_another_agent_cannot_stop_a_panel_run_but_the_switch_is_physical(self):
        r = self.rig()
        r.load()
        r.home()
        r.tap('main_start')
        op = r.h.operation_id
        self.assertEqual(r.h._owners[op], 'agent')
        self.assertEqual(r.A.poll(op)['status'], 'running')                       # the caller that started it may poll it
        r.call(r.B, 'acquisition.stop', expect='ACCESS_DENIED')
        run = copy.deepcopy(r.h.ap['run'])
        self.assertFalse(self.b_tap(r, 'main_stop'))                              # B's <Stop>: refused, the device says why
        self.assertEqual(r.dialog(), 'err_ACCESS_DENIED')
        r.ov('OK')
        self.assertEqual((r.h.phase, r.h.powered, r.h.operation_id, r.h.ap['run']), ('running', True, op, run))
        self.assertEqual(r.A.cancel(op, 'c1')['status'], 'cancelled')            # the owner cancels its own panel run
        r.tap('main_start')                                                       # a new run of A ...
        op = r.h.operation_id
        self.assertTrue(r.call(r.B, 'panel.switch', input_id='mains_switch', position='off')['accepted'])   # ... B's rear switch: physical
        self.assertFalse(r.h.powered)
        self.assertNotEqual(r.A.poll(op)['status'], 'running')                   # the run ends as on a power loss

    def test_another_agent_cannot_continue_a_fill_or_decide_an_adjustment(self):
        r = self.rig()
        r.load()
        r.tap('main_quick')
        r.choose('Type', 'MF (Multi Fill)')
        r.enter('Number of fills', '2')
        r.tap_text('OK')
        r.measure(40)
        self.assertEqual(r.page()[0], 'fill_prompt')
        self.assertFalse(self.b_tap(r, 'prompt_ok'))                              # B cannot confirm A's next fill
        r.ov('OK')
        r.clean()
        r.load({'methanol': 1.0})
        self.assertFalse(self.b_tap(r, 'prompt_cancel'))                          # the waiting run still belongs to A
        r.ov('OK')
        self.assertEqual(r.val('run.state')['stage'], 'await_fill')
        r.tap('prompt_cancel')
        r.clean()
        r.call(r.E, 'sensor.configure_error', offset_nd=0.0, gain=0.0, baseline_nd=0.0002)
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        r.go('Checks / Adjustments', 'Refractometer Adjustments', 'Set Air Reference')
        r.tap_text('OK')
        r.step(40)
        self.assertEqual(r.page()[0], 'adjust_result')
        self.assertFalse(self.b_tap(r, 'adj_apply'))                              # A's adjustment result: B may not apply it
        r.ov('OK')
        r.call(r.B, 'adjustment.decide', expect='ACCESS_DENIED', apply=False)
        r.tap('adj_apply')
        self.assertAlmostEqual(r.val('adjustment.correction')['baseline'], -0.0002)


class AirReadingAndKeyboard(unittest.TestCase):
    def test_air_reference_comes_from_the_air_reading(self):
        """Set Air Reference proposes the baseline the device read on the clean, dry prism during the run (abm p.68), not a later value."""
        r = Rig()
        r.call(r.E, 'sensor.configure_error', offset_nd=0.0, gain=0.0, baseline_nd=0.0004)
        r.go('Checks / Adjustments', 'Refractometer Adjustments', 'Set Air Reference')
        r.tap_text('OK')
        r.step(40)
        self.assertEqual(r.page()[0], 'adjust_result')
        r.call(r.E, 'sensor.configure_error', offset_nd=0.0, gain=0.0, baseline_nd=0.0009)   # drifts after the reading
        self.assertAlmostEqual(r.val('adjustment.state')['new']['baseline'], -0.0004)
        r.tap('adj_apply')
        self.assertAlmostEqual(r.val('adjustment.correction')['baseline'], -0.0004)

    def test_keyboard_layout_setting_changes_the_on_screen_keyboard(self):
        r = Rig()
        r.tap('main_quick')
        r.row('Sample Name')
        keys = [b['label'] for b in r.overlay()['buttons'] if b['id'].startswith('key:')]
        self.assertLess(keys.index('y'), keys.index('u'))                       # US: QWERTY (gsf p.8 fig. 3)
        r.ov('Cancel')
        r.home()
        r.go('Setup', 'Control Panel', 'Regional Settings')
        r.choose('Keyboard Layout', 'French')
        r.tap_text('OK')
        r.ov('Yes')                                                              # the instrument reboots (abm p.41)
        r.tap('main_quick')
        r.row('Sample Name')
        keys = [b['label'] for b in r.overlay()['buttons'] if b['id'].startswith('key:')]
        self.assertEqual(keys[:3], ['a', 'z', 'e'])                             # AZERTY
        r.type('Lot 7')
        r.ov('OK')
        r.tap_text('OK')
        self.assertEqual(r.val('sample.quick_settings')['sample_name'], 'Lot 7')
        r.go('Setup', 'Control Panel', 'Regional Settings')
        r.choose('Keyboard Layout', 'German')
        r.tap_text('OK')
        r.ov('Yes')
        r.tap('main_quick')
        r.row('Sample Name')
        keys = [b['label'] for b in r.overlay()['buttons'] if b['id'].startswith('key:')]
        self.assertLess(keys.index('z'), keys.index('u'))                       # QWERTZ with umlauts
        self.assertIn('ä', keys)


class RenderingAndContract(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        r = Rig()
        r.load()
        r.measure(40)
        p1, p2 = r.png(), r.png()
        self.assertEqual(p1, p2)
        cap = r.call(r.A, 'panel.capture', display_id='screen')
        self.assertEqual((cap['image']['width'], cap['image']['height']), (800, 480))
        self.assertEqual(cap['image']['sha256'], hashlib.sha256(p1).hexdigest())
        led = r.call(r.A, 'panel.capture', display_id='power_led')
        self.assertEqual(led['image']['width'], 24)
        r.call(r.A, 'panel.switch', input_id='mains_switch', position='off')
        self.assertEqual((r.page()[0], r.val('power.enabled')), ('off', False))
        self.assertFalse(r.frame()['displays']['power_led']['on'])

    def test_frame_is_evaluator_only_and_gestures_validate(self):
        r = Rig()
        self.assertEqual(r.call(r.A, 'panel.frame', expect='ACCESS_DENIED')['error']['code'], 'ACCESS_DENIED')
        r.call(r.A, 'panel.touch', expect='INVALID_ARGUMENT', display_id='screen', x=900, y=10)
        r.call(r.A, 'panel.switch', expect='INVALID_ARGUMENT', input_id='mains_switch', position='middle')
        caps = r.A.describe(instance_id=r.h.instance_id)['capabilities']
        self.assertIn('panel.touch', caps)
        self.assertNotIn('panel.frame', caps)

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + list(rt.DEVICE_TUPLES) + list(rt.ADJ_CAPS):
            self.assertIn(k, c['capabilities'])
        for k in ('mains_switch', 'power_led', 'screen'):
            self.assertIn(k, c['components'])
        meta = rt.export_antonpaar_abbematadvanced_contract_metadata()
        for k, v in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        r = Rig()
        for sid, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, meta['state_slots'][sid]['value_schema']), [], sid)


if __name__ == '__main__':
    unittest.main()
