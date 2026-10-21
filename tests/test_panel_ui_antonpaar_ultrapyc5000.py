"""front-panel tests for CHEM-114 antonpaar_ultrapyc5000 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on
scripts/asset_runtime/antonpaar_ultrapyc5000.py): python3 -m pytest tests/test_panel_ui_antonpaar_ultrapyc5000.py -q

The panel is approximate (no Ultrapyc 5000 manual): the 1200e menu functions as touch pages and the brochure's overview screen.  Every
gesture goes through the session API (panel.switch / panel.press / panel.touch); widgets are found by id or text in the structured frame
(panel.frame, evaluator).  Themes: the four key procedures of the menu survey; leak check, settings, reports, temperature, Home key;
panel = direct capability; refusals and absent hardware; render determinism; contract entries."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import antonpaar_ultrapyc5000 as rt  # noqa: E402
from antonpaar_panel_rig import Rig as _Rig  # noqa: E402
from asset_tool import validate_value  # noqa: E402

CONTRACT = rt.ASSET / 'asset.contract.json'
SURVEY_TASKS = ['set run parameters and start a sample run', 'calibrate cell volume', 'recover from an error', 'shut down']
DONE_TASKS = set()
SPHERE = dict(sample_id='sphere', reference_id='nist_fe', true_mass_kg=0.07874, closed_pore_m3=0., open_pore_m3=0., reference_temperature_k=298.15,
              volume_expansion_per_k=0., accept_reference_transfer=True)                     # a 10 cm3 steel calibration sphere


class Rig(_Rig):
    def __init__(self, power=True, plant=None):
        super().__init__(rt, 'AntonpaarUltrapyc5000Runtime')
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, **(plant or {})))
        if power:
            self.call(self.A, 'panel.switch', input_id='power_switch', position='on')

    def open_cup(self):
        for k, a in [('seal.set', {'angle_rad': 0.}), ('lock.set', {'angle_rad': 0.}), ('lid.set', {'lift_m': .13}), ('cup.set', {'lift_m': .12})]:
            self.call(self.A, k, **a)

    def close_cup(self):
        for k, a in [('cup.set', {'lift_m': 0.}), ('lid.set', {'lift_m': 0.}), ('lock.set', {'angle_rad': 1.1}), ('seal.set', {'angle_rad': 1.4})]:
            self.call(self.A, k, **a)

    def home(self):
        self.call(self.A, 'panel.press', input_id='home_key')
        assert self.page() == ('main', 'home'), self.page()

    def wait(self, limit=1800):
        for _ in range(limit // 10):
            self.step(10)
            if self.val('acquisition.phase') != 'running':
                break
        self.step(60)                                                          # venting after the run

    def run_parameters(self, **values):
        self.home()
        self.tap_text('Run')
        self.row('Run Parameters')
        for label, v in values.items():
            if isinstance(v, str) and not v.replace('.', '').isdigit():
                self.choose(label, v)
            else:
                self.enter(label, v)
        self.tap_text('OK')
        self.home()

    def calibrate(self):
        """Calibrate: blank run, sphere run, calibration volume 10 cm3, OK."""
        self.home()
        self.tap_text('Calibrate')
        self.tap_text('Blank run')
        self.wait()
        self.open_cup()
        self.call(self.E, 'sample.load', **SPHERE)
        self.close_cup()
        self.tap_text('Sphere run')
        self.wait()
        self.tap_text('Cal. volume')
        self.enter('Cal. Volume', '10')
        self.tap_text('OK')
        self.open_cup()
        self.call(self.E, 'sample.unload')

    def sample(self, weight='50', sample_id='AL1'):
        self.call(self.E, 'sample.load', **rt.SAMPLE_EXAMPLE)
        self.close_cup()
        self.home()
        self.tap_text('Run')
        self.row('Sample Parameters')
        self.enter('Sample weight', weight)
        self.enter('Sample ID', sample_id)
        self.tap_text('OK')


# ---------------------------------------------------------------- the key procedures of the survey
class SurveyProcedures(unittest.TestCase):
    def test_set_run_parameters_and_start_a_sample_run(self):
        r = Rig()
        r.run_parameters(**{'Target Pressure': '120', 'Purge': 'pulse', 'Purge pulses': '3', 'Max. runs': '5', 'Runs to average': '3'})
        m = r.val('method.settings')
        self.assertEqual((m['target_gauge_pa'], m['preparation'], m['purge_pulses'], m['max_runs'], m['runs_to_average'], m['mode']),
                         (120000.0, 'pulse', 3, 5, 3, 'measure'))
        r.calibrate()
        r.sample()
        self.assertEqual((r.val('sample.entered_mass_kg'), r.val('device.settings')['sample_id']), (0.05, 'AL1'))
        r.row('Start')                                                         # 1-Run > 3-Start (up p.31)
        self.assertEqual((r.page(), r.val('acquisition.phase')), (('main', 'home'), 'running'))
        r.step(30)
        self.assertNotEqual(r.text('ov2_value'), 'all closed')                 # valve status on the overview (bro p.3)
        r.wait()
        self.assertEqual(r.val('acquisition.phase'), 'succeeded')
        self.assertEqual(r.text('ov5_value'), '2.6990')                        # aluminium, g/cm3
        rep = r.val('device.reports')[-1]
        self.assertEqual((rep['kind'], rep['sample_id'], rep['runs'], rep['density_g_cm3']), ('sample', 'AL1', 5, 2.699))
        DONE_TASKS.add('set run parameters and start a sample run')

    def test_calibrate_cell_volume(self):
        r = Rig()
        r.run_parameters()
        self.assertFalse(r.val('volume.calibrated'))
        r.tap_text('Calibrate')
        r.tap_text('Blank run')                                                # empty cup
        self.assertEqual(r.val('device.status')['run'], 'blank')
        r.wait()
        self.assertAlmostEqual(r.val('device.calibration')['blank'], 1.0, places=6)
        self.assertEqual(r.val('method.settings')['mode'], 'measure')          # the mode returns after the calibration run
        r.open_cup()
        r.call(r.E, 'sample.load', **SPHERE)                                   # calibration sphere in the cup
        r.close_cup()
        r.tap_text('Sphere run')
        r.wait()
        self.assertAlmostEqual(r.val('device.calibration')['sphere'], 0.8, places=5)
        r.tap_text('Cal. volume')
        r.enter('Cal. Volume', '10')
        r.tap_text('OK')
        self.assertTrue(r.val('volume.calibrated'))
        rep = r.val('device.reports')[-1]
        self.assertEqual((rep['kind'], rep['cell_volume_cm3'], rep['reference_volume_cm3']), ('cell calibration', 50.0, 50.0))
        DONE_TASKS.add('calibrate cell volume')

    def test_recover_from_an_error(self):
        r = Rig()
        r.run_parameters()
        r.calibrate()
        r.call(r.E, 'sample.load', **rt.SAMPLE_EXAMPLE)                       # cup still open, no weight entered
        r.home()
        before = copy.deepcopy(r.h.read('evaluator'))
        r.tap_text('Start')
        self.assertEqual(r.dialog(), 'err_NOT_READY')                          # the error is shown
        self.assertEqual(r.h.read('evaluator'), before)
        r.ov('OK')                                                             # cleared
        r.close_cup()                                                          # fix the cause
        r.tap_text('Run')
        r.row('Sample Parameters')
        r.enter('Sample weight', '50')
        r.tap_text('OK')
        r.home()
        r.tap_text('Start')                                                    # restart
        r.wait()
        self.assertEqual(r.val('acquisition.phase'), 'succeeded')
        DONE_TASKS.add('recover from an error')

    def test_shut_down(self):
        r = Rig()
        r.tap_text('Settings')
        r.row('Shutdown')                                                      # 8-Shutdown (up p.31)
        r.ov('Yes')
        self.assertEqual(r.page()[0], 'shutdown')
        self.assertEqual(r.text('sd_text'), 'Shutting down ... please wait.')
        r.call(r.A, 'settings.configure', expect='SHUT_DOWN', settings={'user_id': 'x'})
        r.step(61)                                                             # wait at least 1 minute
        self.assertEqual(r.text('sd_text'), 'You can switch off the instrument now.')
        r.call(r.A, 'panel.switch', input_id='power_switch', position='off')
        self.assertEqual(r.page()[0], 'off')
        r.call(r.A, 'panel.switch', input_id='power_switch', position='on')
        self.assertEqual((r.page(), r.val('device.status')['shutdown']), (('main', 'home'), 'none'))
        DONE_TASKS.add('shut down')


# ---------------------------------------------------------------- functions, refusals, equivalence
class Workflows(unittest.TestCase):
    def test_leak_check(self):
        r = Rig()
        r.run_parameters()
        r.tap_text('Settings')
        r.row('Leak Check')
        self.assertEqual(r.val('device.status')['run'], 'leak')
        r.wait()
        rep = r.val('device.reports')[-1]
        self.assertEqual((rep['kind'], rep['leak_pass']), ('leak check', True))
        r.open_cup()
        r.call(r.E, 'sample.load', **rt.SAMPLE_EXAMPLE)
        r.close_cup()
        r.tap_text('Settings')
        r.row('Leak Check')                                                    # only without a sample
        self.assertEqual(r.dialog(), 'err_REMOVE_SAMPLE_FIRST')

    def test_settings_temperature_and_home_key(self):
        r = Rig()
        r.tap_text('Settings')
        r.row('Units, Gas, User ID, Reports')
        r.enter('User ID', 'LAB2')
        r.choose('Pressure units', 'psig')
        r.enter('Gas type', 'Nitrogen')
        r.tap_text('OK')
        s = r.val('device.settings')
        self.assertEqual((s['user_id'], s['pressure_unit'], s['gas_name']), ('LAB2', 'psig', 'Nitrogen'))
        r.home()
        self.assertEqual(r.text('home_user'), 'User: LAB2')
        self.assertTrue(r.text('ov1_value').endswith('psig'))
        self.assertTrue(r.text('ov1_value').startswith('0.000'))               # gauge pressure at ambient
        r.tap_text('Settings')
        r.row('Temperature')
        r.enter('Temperature', '30')
        r.tap_text('OK')
        self.assertAlmostEqual(r.val('thermal.target_k'), 303.15)
        r.step(900)
        r.home()
        self.assertEqual(r.text('ov0_value'), '30.00 °C')
        r.call(r.A, 'panel.press', input_id='home_key')                        # Home contact (panel.home: page home, stroke)
        self.assertEqual((r.page(), r.val('panel.page'), r.val('home.stroke_m')), (('main', 'home'), 'home', 0.0015))

    def test_change_languages(self):
        """6-Change Languages (up p.31): the selection is stored, shown and written into the reports; the texts stay English (no translation)."""
        r = Rig()
        r.tap_text('Settings')
        r.row('Change Languages')
        r.choose('Language', 'German')
        r.tap_text('OK')
        self.assertEqual((r.val('device.settings')['language'], r.page()), ('German', ('main', 'settings')))
        r.row('Change Languages')
        self.assertIn('German', json.dumps(r.disp()['widgets']))               # shown on its page
        r.home()
        self.assertTrue(r.has_button('Calibrate'))                              # texts stay English
        r.run_parameters()
        r.call(r.A, 'leak_check.start')
        r.wait()
        self.assertEqual(r.val('device.reports')[-1]['language'], 'German')

    def test_reports_absent_hardware_and_delete(self):
        r = Rig()
        r.run_parameters()
        r.tap_text('Settings')
        r.row('Leak Check')
        r.wait()
        r.tap_text('Reports')
        self.assertEqual(r.rows()[0][1], 'leak check')
        r.tap_text('Print Last Run')
        self.assertEqual(r.dialog(), 'no_printer')
        r.ov('OK')
        r.home()
        r.tap_text('Settings')
        for row, dialog in (('Copy Reports to USB', 'no_usb'), ('Balance Mode', 'no_balance'), ('System Configuration (network, printer)', 'no_network')):
            r.row(row)
            self.assertEqual(r.dialog(), dialog)
            r.ov('OK')
        r.row('Delete Reports')
        r.ov('Yes')
        self.assertEqual(r.val('device.reports'), [])

    def test_no_changes_while_running(self):
        r = Rig()
        r.run_parameters()
        r.tap_text('Settings')
        r.row('Leak Check')
        r.tap_text('Run')
        r.row('Run Parameters')
        self.assertFalse(r.button('OK')['enabled'])
        r.home()
        r.tap_text('Stop')
        self.assertEqual(r.val('acquisition.phase'), 'cancelled')


class PanelEqualsDirect(unittest.TestCase):
    def test_run_parameters_and_sample_run_equal_direct_commands(self):
        a, b = Rig(), Rig()
        a.run_parameters(**{'Target Pressure': '120'})
        b.call(b.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, target_gauge_pa=120000.0))
        self.assertEqual(a.val('method.settings'), b.val('method.settings'))
        a.calibrate()
        b.call(b.A, 'calibration.run', kind='blank')
        b.wait()
        b.open_cup()
        b.call(b.E, 'sample.load', **SPHERE)
        b.close_cup()
        b.call(b.A, 'calibration.run', kind='sphere')
        b.wait()
        b.call(b.A, 'calibration.apply', sphere_volume_m3=1e-5)
        b.open_cup()
        b.call(b.E, 'sample.unload')
        self.assertEqual(a.val('device.calibration'), b.val('device.calibration'))
        self.assertEqual(a.call(a.A, 'process.measure')['runs'], b.call(b.A, 'process.measure')['runs'])


class DeviceReadingControl(unittest.TestCase):
    """The controller acts on the device's own (zeroed) transducer reading; Number Report Files names the reports (up p.11, p.34, p.59)."""
    ERR = dict(pressure_gain=[1.02, 1.02], pressure_offset_pa=[800.0, -800.0])

    def fill_end(self, r):
        """Step until the fill stage ends: (gauge reading, true gauge pressure) of the filled chamber at that moment."""
        prev = r.h.stage
        for _ in range(20000):
            r.h.step(0.1)
            if prev == 'fill' and r.h.stage == 'expand':
                i = 0 if r.h.method['direction'] == 'sample_first' else 1
                return i, r.h.gauge_p(i), r.h.pressure(i) - r.h.plant['ambient_pa']
            prev = r.h.stage
        raise AssertionError('no fill')

    def test_fill_stops_at_the_device_reading_panel_and_direct(self):
        out = []
        for panel in (True, False):
            r = Rig(plant=self.ERR)
            r.run_parameters(**{'Target Pressure': '120'})
            if panel:
                r.home()
                r.tap_text('Calibrate')
                r.tap_text('Blank run')
            else:
                r.call(r.A, 'calibration.run', kind='blank')
            i, gauge, true = self.fill_end(r)
            self.assertAlmostEqual(gauge, 120000.0, delta=0.06)                   # the device stops at its own set point
            self.assertAlmostEqual(true, 120000.0 / 1.02, delta=1.0)              # i.e. 2.3 kPa below the true target (gain 1.02)
            out.append((i, round(gauge, 6), round(true, 6)))
        self.assertEqual(out[0], out[1])

    def test_pressure_calibration_moves_the_stop_point(self):
        r = Rig(plant=self.ERR)
        r.call(r.A, 'settings.configure', settings={'pressure_unit': 'psig'})
        r.home()
        self.assertEqual(r.text('ov1_value'), '0.000 / 0.000 psig')              # zeroed at ambient: the +-800 Pa offsets do not show
        r.call(r.A, 'settings.configure', settings={'pressure_unit': 'kPa'})
        ref = [101325.0, 201325.0]
        r.call(r.A, 'pressure.calibrate', reference_pa=ref, sample_measured_pa=[1.02 * x + 800.0 for x in ref],
               reference_measured_pa=[1.02 * x - 800.0 for x in ref])
        r.run_parameters(**{'Target Pressure': '120'})
        r.home()
        r.tap_text('Calibrate')
        r.tap_text('Blank run')
        i, gauge, true = self.fill_end(r)
        self.assertAlmostEqual(true, 120000.0, delta=0.1)                         # the stored calibration corrects the control too
        self.assertAlmostEqual(gauge, 120000.0, delta=0.06)

    def test_number_report_files(self):
        r = Rig()
        r.run_parameters()
        for _ in range(2):                                                       # on (default): vcReport, vcReport-1
            r.home()
            r.tap_text('Calibrate')
            r.tap_text('Blank run')
            r.wait()
        self.assertEqual([x['file'] for x in r.val('device.reports')], ['vcReport', 'vcReport-1'])
        r.home()
        r.tap_text('Settings')
        r.row('Units, Gas, User ID, Reports')
        r.choose('Number report files', 'Off')
        r.tap_text('OK')
        self.assertFalse(r.val('device.settings')['number_reports'])
        r.home()
        r.tap_text('Calibrate')
        r.tap_text('Blank run')
        r.wait()
        reps = r.val('device.reports')                                           # off: the new report replaces the older ones of that name
        self.assertEqual([(x['id'], x['file']) for x in reps], [(3, 'vcReport')])
        r.call(r.A, 'settings.configure', settings={'number_reports': True})     # direct command, same rule
        r.home()
        r.tap_text('Settings')
        r.row('Leak Check')
        r.wait()
        r.home()
        r.tap_text('Settings')
        r.row('Leak Check')
        r.wait()
        reps = r.val('device.reports')
        self.assertEqual([x['file'] for x in reps], ['vcReport', None, None])   # leak checks: no documented file name, always kept
        r.home()
        r.tap_text('Calibrate')
        r.tap_text('Blank run')
        r.wait()
        self.assertEqual(r.val('device.reports')[-1]['file'], 'vcReport-1')
        r.home()
        r.tap_text('Reports')
        self.assertEqual([c[2] for c in r.rows()], ['vcReport', '-', '-', 'vcReport-1'])


class VolumeOnZeroedReadings(unittest.TestCase):
    """The volume calculation uses the same zeroed transducer readings as the controller (up p.11, p.94 eq. I.10): a static offset cancels in the
    result as it does in the control; ideal sensors give the result of before."""

    def measure(self, plant):
        r = Rig(plant=plant)
        r.run_parameters()
        r.calibrate()
        r.sample()
        r.home()
        r.tap_text('Start')
        r.wait()
        self.assertEqual(r.val('acquisition.phase'), 'succeeded')
        return r.val('result.volume_m3'), r.h.runs[-1]['ratio']

    def test_static_offset_cancels_in_the_volume(self):
        v0, q0 = self.measure(None)
        v1, q1 = self.measure(dict(pressure_offset_pa=[800.0, -800.0]))
        self.assertAlmostEqual(q1, q0, delta=abs(q0) * 1e-9)
        self.assertAlmostEqual(v1, v0, delta=abs(v0) * 1e-9)


class CallerRules(unittest.TestCase):
    """Runs started on the panel or directly belong to the caller (poll, cancel); another agent's Stop, direct stop and changes of the report
    settings during the run are refused (ACCESS_DENIED, state unchanged) with a message box; the display unit stays free; the rear power switch
    is a hard switch (never refused, the run ends as on a power loss)."""

    def test_foreign_stop_power_and_settings_refused(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        r.run_parameters()
        r.home()
        r.tap_text('Calibrate')
        r.tap_text('Blank run')
        op = r.h.operation_id
        self.assertEqual((r.h._owners.get(op), r.A.poll(op)['status']), ('agent', 'running'))
        r.step(2)
        r.home()
        before = r.h.read('evaluator')
        w = r.button('Stop')
        x, y, ww, hh = w['rect']
        self.assertFalse(r.call(B, 'panel.touch', display_id='screen', x=x + ww // 2, y=y + hh // 2)['accepted'])
        self.assertEqual(r.dialog(), 'err_ACCESS_DENIED')                     # message box (assumed text)
        r.ov('OK')
        for cap, args in (('acquisition.stop', {}), ('settings.configure', dict(settings=dict(sample_id='B1')))):
            r.call(B, cap, expect='ACCESS_DENIED', **args)
        self.assertEqual(r.h.read('evaluator'), before)
        r.call(B, 'settings.configure', settings=dict(pressure_unit='psig'))   # a display setting: free
        self.assertEqual(r.A.cancel(op, 'a-cancel')['status'], 'succeeded')       # the owner cancels its own run
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')

    def test_power_switch_is_physical(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        r.run_parameters()
        r.call(r.A, 'calibration.run', kind='blank')
        op = r.h.operation_id
        r.step(2)
        self.assertTrue(r.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])
        self.assertEqual((r.val('power.enabled'), r.A.poll(op)['status']), (False, 'cancelled'))


class FaultScreens(unittest.TestCase):
    """1200e error codes (up p.73-75, Table G-1) by the evaluator-only fault.inject: the run stops, 'Error n occurred' alternates with the message
    and 'Restart: Press clear'; Clear acknowledges; while the cause is not removed (fault.clear) the next run stops with the same error."""

    def test_error_2_stops_the_run_until_clear_and_remedy(self):
        r = Rig()
        r.run_parameters()
        r.call(r.A, 'fault.inject', expect='ACCESS_DENIED', code=2)
        r.home()
        r.tap_text('Calibrate')
        r.tap_text('Blank run')
        r.step(3)
        r.call(r.E, 'fault.inject', code=2)
        self.assertEqual((r.val('acquisition.phase'), r.val('acquisition.failure'), r.page()), ('failed', 'ERROR_2', ('error', 'p')))
        texts = set()
        for _ in range(4):                                                       # the second line alternates (2 s)
            texts.add(r.text('er_text'))
            r.step(1)
        self.assertEqual((r.text('er_code'), texts), ('Error 2 occurred', {'Low Pressure', 'Restart: Press clear'}))
        r.call(r.A, 'calibration.run', expect='NOT_READY', kind='blank')        # shown: a start is refused
        r.tap_text('Clear')
        self.assertEqual(r.page()[0], 'main')
        r.call(r.A, 'calibration.run', kind='blank')                            # the cause is still there: the same error again
        r.step(1)
        self.assertEqual((r.val('acquisition.failure'), r.page()[0]), ('ERROR_2', 'error'))
        r.call(r.E, 'fault.clear')
        r.tap_text('Clear')
        r.call(r.A, 'calibration.run', kind='blank')
        r.wait()
        self.assertEqual(r.val('acquisition.phase'), 'succeeded')

    def test_error_8_is_only_printed(self):
        r = Rig()
        r.run_parameters()
        r.call(r.E, 'fault.inject', code=8)
        r.call(r.A, 'calibration.run', kind='blank')
        r.wait()
        self.assertEqual((r.val('acquisition.phase'), r.page()[0]), ('succeeded', 'main'))
        self.assertEqual(r.val('device.reports')[-1]['error'], 'Error 8 occurred: On-board clock failure')


class RenderingAndContract(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        r = Rig()
        p1, p2 = r.png(), r.png()
        self.assertEqual(p1, p2)
        cap = r.call(r.A, 'panel.capture', display_id='screen')
        self.assertEqual((cap['image']['width'], cap['image']['height']), (800, 480))
        self.assertEqual(cap['image']['sha256'], hashlib.sha256(p1).hexdigest())

    def test_frame_is_evaluator_only_and_gestures_validate(self):
        r = Rig()
        self.assertEqual(r.call(r.A, 'panel.frame', expect='ACCESS_DENIED')['error']['code'], 'ACCESS_DENIED')
        r.call(r.A, 'panel.touch', expect='INVALID_ARGUMENT', display_id='screen', x=900, y=10)
        r.call(r.A, 'panel.press', expect='INVALID_ARGUMENT', input_id='nothing')
        caps = r.A.describe(instance_id=r.h.instance_id)['capabilities']
        self.assertIn('panel.touch', caps)
        self.assertNotIn('panel.frame', caps)

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        self.assertTrue(c['extensions']['chemistry_lab.panel_ui']['approximate'])
        for k in list(rt.PANEL_CAPS) + list(rt.DEVICE_CAPS):
            self.assertIn(k, c['capabilities'])
        meta = rt.export_antonpaar_ultrapyc5000_contract_metadata()
        for k, v in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        r = Rig()
        for sid, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, meta['state_slots'][sid]['value_schema']), [], sid)


if __name__ == '__main__':
    unittest.main()
