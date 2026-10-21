"""front-panel tests for CHEM-137 antonpaar_mcr503 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/antonpaar_mcr503.py):
python3 -m pytest tests/test_panel_ui_antonpaar_mcr503.py -q

The panel is approximate (no MCR 503 manual): status screen, MCR menu, head positions and procedures from the MCR 702e manual, guided testing
from the brochure and the manufacturer photo of the 503 screen.  Every gesture goes through the session API (panel.switch / panel.touch);
widgets are found by id or text in the structured frame (panel.frame, evaluator).  Themes: the four key procedures of the menu survey; the
guided tests; head, temperature, service, information and message pages; panel = direct capability; refusals shown as the device's own
message; time-outs (boot, tolerance hold time); render determinism; contract entries."""
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
from asset_runtime import antonpaar_mcr503 as rt  # noqa: E402
from antonpaar_panel_rig import Rig as _Rig  # noqa: E402
from asset_tool import validate_value  # noqa: E402

CONTRACT = rt.ASSET / 'asset.contract.json'
SURVEY_TASKS = ['switch on', 'change an MCR menu setting', 'stop movement in an emergency', 'start a test from the panel']
DONE_TASKS = set()


class Rig(_Rig):
    def __init__(self, power=True, boot=True):
        super().__init__(rt, 'AntonpaarMcr503Runtime')
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)              # the trusted host sets up the PP25 / Peltier configuration
        self.call(self.E, 'geometry.configure', **rt.GEOMETRY_EXAMPLE)
        if power:
            self.call(self.A, 'panel.switch', input_id='power_switch', position='on')
            if boot:
                self.step(7)

    def load(self, **kw):
        self.call(self.E, 'sample.load', **dict(rt.SAMPLE_EXAMPLE, **kw))

    def home(self):
        for _ in range(3):
            if self.overlay():
                self.ov(self.overlay()['buttons'][0]['label'])
        scr, pid = self.page()
        if scr == 'main' and pid != 'home':
            self.tap(f'{pid}_back')
        assert self.page() == ('main', 'home'), self.page()

    def ro(self):
        return self.h._readout()

    def test(self):
        return self.val('test.state')

    def guided(self, template='Viscosity at constant shear rate', temperature=None, hold=None, sample=None):
        """Tests > template > parameters > Start test, then the steps up to the measurement (the host loads the sample at the lift position)."""
        self.home()
        self.tap_text('Tests')
        self.row(template)
        if temperature is not None:
            self.enter('Temperature', temperature)
        if hold is not None:
            self.enter('Within tolerance for', hold)
        self.tap_text('Start test')
        assert self.page()[0] == 'test', self.page()
        self.tap('test_ok')                                                    # 1/7 measuring system
        self.tap('test_skip')                                                  # 2/7 zero gap skipped
        self.step(5)
        self.load(**(sample or {}))
        self.tap('test_ok')                                                    # 3/7 lift position: sample loaded
        self.step(5)
        self.tap('test_ok')                                                    # 4/7 measuring position: trimmed


# ---------------------------------------------------------------- the key procedures of the survey
class SurveyProcedures(unittest.TestCase):
    def test_switch_on(self):
        r = Rig(power=False)
        self.assertEqual(r.page()[0], 'off')
        r.call(r.A, 'panel.switch', input_id='power_switch', position='on')   # main power switch at the back (mcr p.21)
        self.assertEqual(r.page()[0], 'boot')                                  # progress indicator while booting
        self.assertTrue(r.widgets()['boot_bar'])
        r.step(3)
        self.assertEqual(r.page()[0], 'boot')
        r.step(3.5)                                                            # the MCR starts in six seconds (bro p.4)
        self.assertEqual(r.page(), ('main', 'home'))
        w = r.widgets()
        self.assertEqual([w[f'st{i}_name']['text'] for i in range(5)], ['Status', 'Normal Force', 'Temperature', 'Position', 'Measuring System'])
        self.assertEqual([w[f'st{i}_value']['text'] for i in (0, 2, 3, 4)], ['O.K.', '25.00', '80.000', 'PP25'])
        DONE_TASKS.add('switch on')

    def test_change_an_mcr_menu_setting(self):
        r = Rig()
        r.tap_text('Settings')                                                 # Menu (mcr p.23)
        self.assertEqual(r.page(), ('main', 'settings'))
        r.choose('Motion Warning', 'OFF')                                      # select, Edit, change, Select
        r.choose('Show Temperature', 'OFF')
        self.assertTrue(r.val('device.settings')['motion_warning'])           # not active before Save
        r.tap_text('Save')
        s = r.val('device.settings')
        self.assertEqual((s['motion_warning'], s['show_temperature']), (False, False))
        self.assertEqual(r.text('st2_value'), '')                              # the measured temperature is not shown (mcr p.24)
        r.tap_text('Settings')
        r.choose('Status Information', 'Air Pressure')
        r.tap_text('Cancel')                                                   # Cancel discards
        self.assertEqual(r.val('device.settings')['status_information'], 'ON')
        r.tap_text('Settings')
        r.choose('Status Information', 'OFF')
        r.tap_text('Save')
        self.assertEqual(r.text('st0_value'), '')
        DONE_TASKS.add('change an MCR menu setting')

    def test_stop_movement_in_an_emergency(self):
        r = Rig()
        r.tap('home_head')
        r.tap_text('Measuring position')
        r.step(1)                                                              # 20 mm/s: the head is on its way down
        self.assertTrue(r.val('geometry.moving'))
        self.assertTrue(r.ro()['motion_sound'])                                # motion warning while the head moves (mcr p.24)
        r.tap('head_stop')                                                     # Stop (mcr p.22)
        r.step(1)
        self.assertFalse(r.val('geometry.moving'))
        self.assertAlmostEqual(r.val('geometry.gap_m'), 0.06, places=6)
        DONE_TASKS.add('stop movement in an emergency')

    def test_abort_a_running_measurement(self):
        r = Rig()
        r.guided()
        r.step(1)
        self.assertEqual((r.test()['step'], r.val('acquisition.phase')), ('measurement', 'running'))
        self.assertTrue(r.tap('test_abort'))                                   # ABORT (photo); Stop interrupts a test (mcr p.22)
        self.assertEqual((r.page(), r.val('acquisition.phase'), r.val('rotor.speed_rad_s')), (('main', 'home'), 'cancelled', 0.0))
        self.assertIn('Test aborted.', [m['text'] for m in r.val('device.messages')])

    def test_start_a_test_from_the_panel(self):
        r = Rig()
        r.tap_text('Tests')
        r.row('Test from RheoCompass')                                         # softkey Start: a test prepared by RheoCompass (mcr p.23)
        self.assertEqual(r.dialog(), 'no_pc')
        r.ov('OK')
        r.guided()                                                             # guided test at the instrument (bro p.4, p.7)
        r.step(5)
        t = r.test()
        self.assertEqual((t['k'], t['step'], t['result']['phase']), (7, 'results', 'succeeded'))
        self.assertAlmostEqual(t['result']['mean_apparent_viscosity_pa_s'], 0.00089, delta=0.00001)   # water at 25 °C (reference)
        self.assertIn('Viscosity: 0.89', r.text('test_result'))
        r.tap('test_ok')
        self.assertEqual(r.page(), ('main', 'home'))
        r.step(5)
        self.assertEqual(r.val('geometry.gap_m'), 0.08)                        # the head is lifted
        DONE_TASKS.add('start a test from the panel')


# ---------------------------------------------------------------- guided tests and pages
class Workflows(unittest.TestCase):
    def test_guided_test_steps_and_tolerance_hold(self):
        r = Rig()
        r.tap_text('Tests')
        r.row('Viscosity at constant shear rate')
        r.enter('Within tolerance for', '20')
        r.enter('Measuring gap', '0.5')
        r.tap_text('Start test')
        t = r.test()
        self.assertEqual((t['k'], t['n'], t['title']), (1, 7, 'Measuring system'))
        self.assertEqual(r.text('test_k'), '1/7')
        self.assertFalse(r.widgets().get('test_skip'))                         # SKIP only for skippable steps
        r.tap('test_ok')
        self.assertEqual(r.test()['title'], 'Zero gap')
        r.tap('test_ok')                                                       # zero gap without sample
        self.assertEqual(r.val('device.procedure')['kind'], 'zero_gap')
        r.step(10)
        self.assertTrue(r.val('device.status')['zero_gap_set'])
        self.assertEqual(r.test()['title'], 'Lift position')
        r.tap('test_ok')                                                       # no sample yet
        self.assertEqual(r.dialog(), 'err_NO_SAMPLE')
        r.ov('OK')
        r.load(volume_m3=3.141592653589793 * 0.0125 ** 2 * 0.0005)
        r.tap('test_ok')
        r.step(5)
        self.assertAlmostEqual(r.val('geometry.gap_m'), 0.0005)
        r.tap('test_ok')
        self.assertEqual(r.test()['title'], 'Device')
        self.assertEqual(r.test()['instruction'], 'Value must be within tolerance for 20 s')
        self.assertEqual(r.test()['plot']['limits'], [24.95, 25.05])
        r.step(10)
        self.assertEqual(r.test()['title'], 'Device')                          # 10 s within tolerance < 20 s
        r.step(11)
        self.assertEqual(r.test()['title'], 'Measurement')
        r.step(3)
        self.assertEqual(r.test()['result']['phase'], 'succeeded')

    def test_guided_oscillation_test(self):
        r = Rig()
        r.guided('Moduli at constant amplitude and frequency', sample=dict(reference_id='glycerol', constitutive_model='maxwell'))
        r.step(6)
        res = r.test()['result']
        self.assertEqual(res['phase'], 'succeeded')
        self.assertGreater(res['loss_modulus_pa'], 0)
        self.assertIn("G'", r.text('test_result'))
        self.assertEqual(r.val('acquisition.points'), 101)                     # 0 .. 5 s every 0.05 s

    def test_measurement_refused_by_the_instrument_shows_on_the_step(self):
        r = Rig()
        r.guided(temperature='30')                                             # water reference at 25 °C: no extrapolation
        r.step(120)
        t = r.test()
        self.assertEqual((t['step'], t['error']), ('measurement', 'REFERENCE_TEMPERATURE_RANGE'))
        self.assertIn('outside the tolerance', r.text('test_text'))
        r.tap('test_ok')                                                       # retry: refused again
        self.assertEqual(r.dialog(), 'err_REFERENCE_TEMPERATURE_RANGE')
        r.ov('OK')
        r.tap('test_abort')
        self.assertEqual(r.page(), ('main', 'home'))

    def test_temperature_page(self):
        r = Rig()
        r.tap_text('Temperature')
        r.choose('Temperature control', 'On')
        r.enter('Set temperature', '30')
        r.tap_text('OK')
        self.assertEqual((r.val('thermal.target_k'), r.val('thermal.enabled')), (303.15, True))
        self.assertEqual(r.text('home_target'), 'Temperature set point: 30.00 °C')
        r.tap_text('Temperature')
        r.choose('Temperature control', 'Off')
        r.tap_text('OK')
        self.assertFalse(r.val('thermal.enabled'))

    def test_service_procedures(self):
        r = Rig()
        r.tap_text('Service')
        r.row('Initialization')
        self.assertEqual(r.val('device.procedure')['kind'], 'initialize')
        r.step(6)
        self.assertTrue(r.val('device.status')['initialized'])
        r.row('Motor adjustment')
        self.assertEqual(r.dialog(), 'confirm_motor')
        r.ov('Motor')                                                          # Adjust, then Motor (mcr p.23)
        r.step(30)
        self.assertEqual(r.val('device.procedure')['progress'], 0.5)
        r.step(31)
        self.assertTrue(r.val('device.status')['motor_adjusted'])
        r.load()
        r.row('Zero gap position')
        r.ov('OK')
        self.assertEqual(r.dialog(), 'err_SAMPLE_PRESENT')                     # the plates would touch the sample
        r.ov('OK')
        r.tap('service_info')
        self.assertEqual(r.cells()['Motor adjusted'], 'yes')
        r.tap('info_bell')
        self.assertEqual([c[1] for c in r.rows()], ['Initialization finished.', 'Motor adjustment finished.'])
        r.row('Info')
        self.assertEqual(len(r.val('device.messages')), 1)
        r.tap_text('Delete all')
        self.assertEqual(r.val('device.messages'), [])

    def test_head_up_down_and_squeeze_guard(self):
        r = Rig()
        r.tap('home_head')
        r.tap('head_down')
        r.step(1)
        self.assertAlmostEqual(r.val('geometry.gap_m'), 0.079)
        r.tap('head_up')
        r.step(1)
        self.assertAlmostEqual(r.val('geometry.gap_m'), 0.08)
        r.load()
        r.tap_text('Measuring position')
        r.step(5)
        before = copy.deepcopy(r.h.read('evaluator'))
        r.tap('head_down')                                                     # below the sample filling height
        self.assertEqual(r.dialog(), 'err_SQUEEZE_FLOW_UNSUPPORTED')
        self.assertEqual(r.h.read('evaluator'), before)


class RunsAndRefusals(unittest.TestCase):
    def test_refusals_leave_the_state_unchanged(self):
        r = Rig()
        before = copy.deepcopy(r.h.read('evaluator'))
        r.tap_text('Settings')
        r.choose('Connection', 'RS 232')                                       # reserved for service purposes (mcr p.24)
        r.tap_text('Save')
        self.assertEqual(r.dialog(), 'err_SERVICE_ONLY')
        self.assertEqual(r.h.read('evaluator'), before)
        r.call(r.A, 'test.confirm', expect='NO_PROCEDURE')
        r.call(r.A, 'test.start', expect='INVALID_ARGUMENT', template='oscillation')   # the method is a rotation test
        r.call(r.A, 'settings.configure', expect='INVALID_ARGUMENT', settings={'test.gap_mm': 9.0})

    def test_busy_while_a_procedure_runs(self):
        r = Rig()
        r.call(r.A, 'instrument.procedure', procedure='motor_adjustment')
        r.tap_text('Tests')
        r.row('Viscosity at constant shear rate')
        self.assertFalse(r.button('Start test')['enabled'])
        r.call(r.A, 'head.move', expect='BUSY', position='lift')

    def test_timeouts_boot_and_power_off(self):
        r = Rig(boot=False)
        self.assertTrue(r.val('device.status')['booting'])
        self.assertEqual(r.page()[0], 'boot')
        self.assertFalse(r.touch(400, 300))                                    # nothing to touch while booting
        r.step(6.5)
        self.assertFalse(r.val('device.status')['booting'])
        r.guided()
        r.step(1)
        r.call(r.A, 'panel.switch', input_id='power_switch', position='off')
        self.assertEqual((r.page()[0], r.val('test.state'), r.val('acquisition.phase')), ('off', None, 'cancelled'))


class PanelEqualsDirect(unittest.TestCase):
    def test_guided_test_equals_direct_commands(self):
        a, b = Rig(), Rig()
        a.guided()
        b.call(b.A, 'method.configure', **rt.METHOD_EXAMPLE)
        b.call(b.A, 'settings.configure', settings={'test.temperature_c': 25.0, 'test.gap_mm': 1.0, 'test.equilibration_s': 0})
        b.call(b.A, 'test.start', template='viscosity')
        b.call(b.A, 'test.confirm')
        b.call(b.A, 'test.skip')
        b.step(5)
        b.load()
        b.call(b.A, 'test.confirm')
        b.step(5)
        b.call(b.A, 'test.confirm')
        for r in (a, b):
            r.step(10)
        for sid in ('test.state', 'acquisition.points', 'thermal.target_k', 'geometry.gap_m', 'device.settings'):
            self.assertEqual(a.val(sid), b.val(sid), sid)
        self.assertEqual(a.call(a.A, 'acquisition.result')['rows'], b.call(b.A, 'acquisition.result')['rows'])

    def test_menu_equals_settings_command(self):
        a, b = Rig(), Rig()
        a.tap_text('Settings')
        a.choose('Connection', 'USB')
        a.choose('Error Warning', 'OFF')
        a.tap_text('Save')
        b.call(b.A, 'settings.configure', settings={'connection': 'USB', 'status_information': 'ON', 'motion_warning': True, 'show_temperature': True,
                                                    'error_warning': False})
        self.assertEqual(a.val('device.settings'), b.val('device.settings'))
        self.assertEqual(a.ro()['connection'], 'USB')


class CallerRules(unittest.TestCase):
    """A panel gesture acts as the gesturing session: the guided test and its measurement belong to their caller; another agent can neither
    stop, abort, confirm nor skip them (panel or direct alike); the owner polls and cancels its own measurement.  The rear main power switch
    is a hard switch (lead rule 2026-10-19): never refused, the instrument reacts as to a power loss."""

    def rig(self):
        r = Rig()
        r.h.register_principal('other', visibility='agent')
        r.B = r.h.session('other')
        return r

    def b_tap(self, r, wid):
        w = r.widgets()[wid]
        x, y, ww, hh = w['rect']
        return r.call(r.B, 'panel.touch', display_id=r.d, x=x + ww // 2, y=y + hh // 2)['accepted']

    def test_another_agent_cannot_stop_or_abort(self):
        r = self.rig()
        r.guided()
        r.step(1)
        op = r.h.operation_id
        self.assertEqual((r.val('acquisition.phase'), r.h._owners[op]), ('running', 'agent'))
        self.assertEqual(r.A.poll(op)['status'], 'running')
        for cap, args in (('acquisition.stop', {}), ('test.abort', {}), ('acquisition.result', {})):
            r.call(r.B, cap, expect='ACCESS_DENIED', **args)
        test = copy.deepcopy(r.h.dev['test'])
        self.assertFalse(self.b_tap(r, 'test_abort'))                           # B's ABORT: refused, the device says why
        self.assertEqual(r.dialog(), 'err_ACCESS_DENIED')
        r.ov('OK')
        self.assertEqual((r.val('acquisition.phase'), r.h.dev['test']), ('running', test))
        self.assertEqual(r.A.cancel(op, 'c1')['status'], 'succeeded')          # the owner cancels its own measurement
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')

    def test_main_power_switch_is_physical(self):
        r = self.rig()
        r.guided()
        r.step(1)
        op = r.h.operation_id
        self.assertTrue(r.call(r.B, 'panel.switch', input_id='power_switch', position='off')['accepted'])   # never refused
        self.assertEqual((r.val('power.enabled'), r.A.poll(op)['status'], r.h.error), (False, 'cancelled', 'POWER_OFF'))   # as a power loss

    def test_another_agent_cannot_confirm_or_skip_a_guided_test(self):
        r = self.rig()
        r.tap_text('Tests')
        r.row('Viscosity at constant shear rate')
        r.tap_text('Start test')
        self.assertEqual(r.test()['k'], 1)
        self.assertFalse(self.b_tap(r, 'test_ok'))
        r.ov('OK')
        r.call(r.B, 'test.confirm', expect='ACCESS_DENIED')
        r.tap('test_ok')                                                        # A: 1/7 measuring system
        r.call(r.B, 'test.skip', expect='ACCESS_DENIED')
        self.assertEqual(r.test()['k'], 2)
        r.tap('test_skip')                                                      # A skips its own zero gap step
        self.assertEqual(r.test()['k'], 3)
        r.tap('test_abort')


class AirPressureFault(unittest.TestCase):
    """'NO AIR PRESSURE!' (mcr p.17, p.21: do not continue, check the air supply) by the evaluator-only fault.inject: the status field shows it,
    a running measurement stops, measurements / head moves / procedures / guided tests are refused until the supply is back (fault.clear)."""

    def test_no_air_pressure(self):
        r = Rig()
        r.call(r.A, 'fault.inject', expect='ACCESS_DENIED', code='NO_AIR_PRESSURE')
        r.guided()
        r.step(1)
        self.assertEqual(r.val('acquisition.phase'), 'running')
        r.call(r.E, 'fault.inject', code='NO_AIR_PRESSURE')
        self.assertEqual((r.val('acquisition.phase'), r.h.error), ('failed', 'NO_AIR_PRESSURE'))
        self.assertEqual(r.h._readout()['status'], 'NO AIR PRESSURE!')
        r.tap('test_abort')
        r.home()
        self.assertEqual(r.text('st0_value'), 'NO AIR PRESSURE!')                 # the Status field of the screen
        r.call(r.A, 'acquisition.start', expect='NO_AIR_PRESSURE')
        r.call(r.A, 'head.move', expect='NO_AIR_PRESSURE', position='lift')
        r.call(r.E, 'fault.clear')
        self.assertNotEqual(r.h._readout()['status'], 'NO AIR PRESSURE!')


class MotorAdjustment(unittest.TestCase):
    def test_motor_adjustment_removes_the_residual_drive_torque(self):
        """RheoCompass p.31: carry out and save the motor adjustment for each measuring system to measure exactly at low torque."""
        r = Rig()
        r.call(r.E, 'sensor.configure_error', motor_residual_nm=1e-7)
        r.guided()
        r.step(5)
        self.assertGreater(r.test()['result']['mean_apparent_viscosity_pa_s'], 0.0011)   # water reads high: drag of the unadjusted drive
        r.tap('test_ok')
        r.step(5)
        r.call(r.E, 'sample.remove')
        r.home()
        r.tap_text('Service')
        r.row('Motor adjustment')
        r.ov('Motor')
        r.step(61)
        self.assertEqual(r.val('device.status')['motor_adjustments'], ['PP25'])
        r.guided()
        r.step(5)
        self.assertAlmostEqual(r.test()['result']['mean_apparent_viscosity_pa_s'], 0.00089, delta=0.00001)   # corrected for PP25
        self.assertEqual(r.val('sensor.errors'), {'motor_residual_nm': 1e-7})


class RenderingAndContract(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        r = Rig()
        r.guided()
        r.step(1)
        p1, p2 = r.png(), r.png()
        self.assertEqual(p1, p2)
        cap = r.call(r.A, 'panel.capture', display_id='screen')
        self.assertEqual((cap['image']['width'], cap['image']['height']), (800, 480))
        self.assertEqual(cap['image']['sha256'], hashlib.sha256(p1).hexdigest())

    def test_frame_is_evaluator_only_and_gestures_validate(self):
        r = Rig()
        self.assertEqual(r.call(r.A, 'panel.frame', expect='ACCESS_DENIED')['error']['code'], 'ACCESS_DENIED')
        r.call(r.A, 'panel.touch', expect='INVALID_ARGUMENT', display_id='screen', x=900, y=10)
        r.call(r.A, 'panel.switch', expect='INVALID_ARGUMENT', input_id='power_switch', position='middle')
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
        meta = rt.export_antonpaar_mcr503_contract_metadata()
        for k, v in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        r = Rig()
        for sid, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, meta['state_slots'][sid]['value_schema']), [], sid)


if __name__ == '__main__':
    unittest.main()
