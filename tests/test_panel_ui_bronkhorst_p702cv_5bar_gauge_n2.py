"""manual-control tests for CHEM-181 bronkhorst_p702cv (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_automation.py):
python3 -m pytest tests/test_panel_ui_bronkhorst_p702cv_5bar_gauge_n2.py -q

Every gesture goes through the session API (panel.switch 'pressed' / 'released' on the multifunctional switch; the host advances the simulated clock
in between, so a hold lasts real simulated time).  Checks look at the runtime state and at the two LEDs (frame and rendered pixels) at chosen
instants.  Themes: the six key procedures of the menu survey, the other switch functions (FLASH mode, auto-zero, network settings), the instrument
functions behind them (control modes, alarms, reset, factory settings), panel = direct command, refusals, render determinism and the PNGs, contract."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_automation as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

OUT = ROOT / 'outputs/panel_ui/bronkhorst_p702cv_5bar_gauge_n2'
ASSET = rt.P702CV_ASSET_DIR
SPEC = rt._p702cv_panel()['spec']
SURVEY_TASKS = ['Reset an alarm', 'Restart the instrument', 'Restore factory settings', 'Read the control mode', 'Change the control mode',
                'Enable RS-232 configuration mode']
DONE_TASKS = set()
FS = rt.P702CV_SENSOR_FULL_SCALE_PA


class Rig:
    def __init__(self, on=True, instance='p702cv_panel_01', **changes):
        args = dict(upstream_volume_m3=0.001, temperature_K=293.15, initial_upstream_absolute_pressure_Pa=301325.0, downstream_absolute_pressure_Pa=101325.0,
                    inflow_mol_s=0.0001, valve_conductance_mol_s_Pa=1e-8, pressure_reference_Pa=101325.0, control_gain_per_Pa_s=1e-6)
        args.update(changes)
        self.r = rt.P702CVRuntime(instance, **args)
        self.r.register_principal('agent', visibility='agent')
        self.r.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.r.session('agent'), self.r.session('eval')
        self.n = 0
        if on:
            self.call('power.set', enabled=True)

    def call(self, cap, sess=None, expect=('succeeded',), **args):
        self.n += 1
        r = (sess or self.A).invoke(capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        return r['result'] if r['status'] == 'succeeded' else r

    def sw(self, position):
        return self.call('panel.switch', input_id='switch', position=position)['accepted']

    def hold(self, seconds):
        """Press, keep the switch down for `seconds` of simulated time, release; returns whether the release was accepted."""
        self.sw('pressed')
        self.wait(seconds)
        return self.sw('released')

    def presses(self, n):
        for _ in range(n):
            self.sw('pressed')
            self.sw('released')

    @property
    def t(self):
        return self.r.debug_snapshot()['world_time_s']

    def wait(self, dt):
        self.r.step(dt, world_time_s=self.t + dt)

    def frame(self):
        return self.call('panel.frame', sess=self.E)['frame']

    def leds(self):
        """(green, red) lit now, from the frame and from the pixels (both must agree)."""
        fr = self.frame()['displays']
        on = tuple(fr[k]['shown'] for k in ('led_mode', 'led_error'))
        px = tuple(tuple(self.r.panel().render(k)[16, 16]) == tuple(SPEC['displays'][k]['colors']['on']) for k in ('led_mode', 'led_error'))
        assert on == px, (on, px)
        return on

    def state(self, sid):
        return self.E.read(state_ids=[sid])['observations'][sid]['value']

    def screen(self):
        return self.frame()['screen']

    def sample(self, times, t0):
        """LED pairs at the given times after t0 (advancing the clock)."""
        out = []
        for x in times:
            if t0 + x - self.t > 1e-9:
                self.wait(t0 + x - self.t)
            out.append(self.leds())
        return out


G, R, BOTH, DARK = (True, False), (False, True), (True, True), (False, False)
ALARM = dict(mode='absolute', min_limit_Pa=100000.0, max_limit_Pa=150000.0, delay_s=5, new_setpoint_Pa=None, reset_by_switch=True,
             reset_by_command=True, reset_automatic=False)


class KeyProcedures(unittest.TestCase):
    def test_reset_an_alarm(self):
        g = Rig()
        g.call('alarm.configure', **ALARM)
        g.wait(1.0)
        self.assertFalse(g.state('alarm.active'))                                       # 200 kPa above the 150 kPa limit, but the 5 s delay runs
        g.wait(5.0)
        self.assertEqual((g.state('alarm.active'), g.state('alarm.info')), (True, ['maximum']))
        self.assertEqual(g.sample([6.25, 7.25], 0.0), [G, R])                         # slow wink: green and red alternate every second
        g.sw('pressed')
        self.assertEqual(g.sample([0.5, 2.0, 5.0, 9.0, 13.0], g.t), [DARK, DARK, G, R, BOTH])   # the hold pattern sequence (p.23)
        self.assertEqual(g.screen(), 'holding')
        g2 = Rig(instance='p702cv_panel_02')
        g2.call('alarm.configure', **ALARM)
        g2.wait(1.0)
        g2.wait(5.0)                                                                    # the delay counts from the first step that sees it
        self.assertTrue(g2.hold(2.0))                                                   # release in the 1-4 s pattern: alarm reset
        self.assertEqual((g2.state('alarm.active'), g2.state('alarm.info')), (False, []))
        self.assertEqual(g2.sample([0.05, 0.15, 1.2], g2.t), [G, R, G])                # fast wink 1 s, then green steady
        g2.wait(5.0)
        self.assertTrue(g2.state('alarm.active'))                                       # still above the limit: back after the delay
        DONE_TASKS.add('Reset an alarm')

    def test_restart_the_instrument(self):
        g = Rig()
        g.call('control.set_control_mode', control_mode=18, set_default=False)
        g.call('alarm.configure', **ALARM)
        g.wait(1.0)
        g.wait(5.0)
        epoch = g.r.panel().host.panel_read()['host.power_epoch']
        self.assertTrue(g.state('alarm.active'))
        self.assertTrue(g.hold(5.0))                                                   # release in the 4-8 s pattern (green)
        self.assertEqual(g.r.panel().host.panel_read()['host.power_epoch'], epoch + 1)
        self.assertEqual((g.state('control.control_mode'), g.state('alarm.active')), (0, False))   # mode 18 returns to the default 0 (p.34)
        self.assertEqual(g.sample([0.05, 0.15, 1.5], g.t), [G, R, G])
        DONE_TASKS.add('Restart the instrument')

    def test_restore_factory_settings(self):
        g = Rig()
        g.call('control.set_gain', gain_per_Pa_s=3e-6)
        g.call('control.set_ramp_rate', rate_Pa_s=1000.0)
        g.call('alarm.configure', **ALARM)
        g.call('control.set_control_mode', control_mode=7, set_default=True)
        g.call('device.set_configuration_mode', enabled=True)
        g.call('power.set', enabled=False)
        self.assertFalse(g.sw('pressed'))                                              # held while the power is off: nothing happens yet
        g.wait(1.0)
        g.call('power.set', enabled=True)
        t0 = g.t
        self.assertEqual(g.screen(), 'powerup_hold')
        self.assertEqual(g.sample([1.1, 4.1, 4.3], t0), [DARK, R, DARK])                      # flashing 0.2 / 0.2 s (p.23)
        g.wait(t0 + 5.0 - g.t)
        self.assertTrue(g.sw('released'))                                              # release between 4 and 8 s
        self.assertEqual((g.state('control.control_mode'), g.state('control.default_control_mode'), g.state('control.gain_per_pa_s'),
                          g.state('control.ramp_rate_pa_s'), g.state('alarm.mode')), (0, 0, 1e-6, None, 'off'))
        self.assertTrue(g.state('device.configuration_mode'))                          # communication settings are kept (p.43)
        DONE_TASKS.add('Restore factory settings')

    def test_read_the_control_mode(self):
        for mode, tens, units in ((20, 2, 0), (1, 0, 1), (22, 2, 2)):               # 1 and 22 are the manual's examples (p.24)
            with self.subTest(mode=mode):
                g = Rig()
                if mode != 20:
                    g.call('control.set_control_mode', control_mode=mode, set_default=False)
                g.presses(2)
                t0 = g.t + 1.0
                green = [g.sample([x + 0.25], t0)[0] for x in range(tens + 1)]
                red = [g.sample([tens + 1 + x + 0.25], t0)[0] for x in range(units + 1)]
                self.assertEqual(green, [G] * tens + [DARK])
                self.assertEqual(red, [R] * units + [DARK])
                g.wait(t0 + tens + units + 2.1 - g.t)
                self.assertEqual((g.screen(), g.leds()), ('normal', G))
        DONE_TASKS.add('Read the control mode')

    def test_change_the_control_mode(self):
        g = Rig()
        g.presses(4)
        g.wait(1.05)
        self.assertEqual(g.screen(), 'entry_tens')
        self.assertEqual(g.sample([0.05, 0.15], g.t - 0.05), [G, DARK])               # green flashes fast: step 1
        g.sw('pressed')
        self.assertEqual(g.sample([0.75, 1.25, 1.75, 2.75, 3.75], g.t), [G, DARK, G, DARK, G])   # slow flashes, max 2, then restart
        g2 = Rig(instance='p702cv_panel_02')
        g2.presses(4)
        g2.wait(1.5)
        self.assertTrue(g2.hold(1.3))                                                  # 1 flash: tens 1
        self.assertEqual(g2.screen(), 'entry_units')
        self.assertEqual(g2.sample([0.05, 0.15], g2.t - 0.05), [R, DARK])
        self.assertTrue(g2.hold(2.4))                                                  # 2 flashes: units 2 -> mode 12 'setpoint 0 %'
        self.assertEqual((g2.state('control.control_mode'), g2.state('control.default_control_mode'), g2.state('control.mode')), (12, 12, 'automatic'))
        self.assertEqual((g2.screen(), g2.state('valve.opening')), ('normal', 0.0))   # setpoint 0 %: the valve stays closed
        g2.call('power.set', enabled=False)
        g2.call('power.set', enabled=True)
        self.assertEqual(g2.state('control.control_mode'), 12)                         # persistent mode (p.34)
        DONE_TASKS.add('Change the control mode')

    def test_enable_rs232_configuration_mode(self):
        g = Rig(on=False)
        g.sw('pressed')
        g.call('power.set', enabled=True)
        t0 = g.t
        self.assertEqual(g.sample([8.1, 8.3, 12.1, 12.3], t0), [G, DARK, BOTH, DARK])         # 8-12 s green, 12-16 s both, flashing
        g.wait(t0 + 13.0 - g.t)
        self.assertTrue(g.sw('released'))
        self.assertTrue(g.state('device.configuration_mode'))
        g.wait(1.5)
        k = (g.t // 2.1 + 1) * 2.1                                                      # start of the next 2.1 s cycle
        g.wait(k + 1.0 - g.t)
        self.assertEqual(g.leds(), G)
        g.wait(k + 2.05 - g.t)
        self.assertEqual(g.leds(), DARK)                                               # long flash: 2 s on, 0.1 s off
        g.call('power.set', enabled=False)
        g.sw('pressed')
        g.call('power.set', enabled=True)
        g.wait(14.0)
        g.sw('released')
        self.assertFalse(g.state('device.configuration_mode'))                         # the same procedure leaves it
        DONE_TASKS.add('Enable RS-232 configuration mode')


class OtherSwitchFunctions(unittest.TestCase):
    def test_flash_mode_shuts_down_until_the_next_power_up(self):
        g = Rig()
        g.call('valve.set_opening', opening=0.4)
        self.assertTrue(g.hold(13.0))
        self.assertEqual((g.state('device.flash_mode'), g.state('control.status'), g.state('pressure.reading_pa'), g.state('valve.opening')),
                         (True, 'program_stopped', None, 0.0))
        self.assertEqual((g.screen(), g.leds()), ('off', DARK))
        r = g.call('pressure.set_setpoint', expect=('rejected',), pressure_Pa=200000.0)
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')
        self.assertFalse(g.hold(2.0))
        g.call('power.set', enabled=False)
        g.call('power.set', enabled=True)
        self.assertEqual((g.state('device.flash_mode'), g.screen(), g.leds()), (False, 'normal', G))

    def test_network_settings_are_absent(self):
        g = Rig()
        before = g.r.debug_snapshot()['state']
        g.presses(3)
        g.wait(1.1)
        fr = g.frame()
        self.assertEqual(fr['screen'], 'normal')
        self.assertIn('network settings', fr['ui']['unsupported'])
        after = g.r.debug_snapshot()['state']
        self.assertEqual({k: v for k, v in before.items() if not k.startswith(('process.', 'pressure.'))},
                         {k: v for k, v in after.items() if not k.startswith(('process.', 'pressure.'))})

    def test_entry_wraps_cancels_and_refuses_unknown_modes(self):
        g = Rig()
        g.presses(4)
        g.wait(1.1)
        g.hold(3.4)                                                                    # past the maximum 2: the count restarted at 0
        g.hold(5.2)                                                                    # units 5: mode 05 does not exist
        self.assertEqual(g.state('control.control_mode'), 20)
        self.assertFalse(g.frame()['ui']['last_gesture']['accepted'])
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'INVALID_ARGUMENT')
        g.presses(4)
        g.wait(1.1)
        g.hold(0.4)
        g.wait(59.0)
        self.assertEqual(g.screen(), 'entry_units')
        g.wait(1.1)
        self.assertEqual(g.screen(), 'normal')                                         # 60 s without a press: cancelled
        self.assertEqual(g.state('control.control_mode'), 20)
        g.presses(4)
        g.wait(1.1)
        g.hold(0.5)
        g.hold(2.5)                                                                    # 02: FLOW-BUS slave, no FLOW-BUS
        self.assertEqual(g.state('control.control_mode'), 20)

    def test_alarm_reset_by_switch_can_be_disabled(self):
        g = Rig()
        g.call('alarm.configure', **dict(ALARM, delay_s=0, reset_by_switch=False))
        g.wait(0.5)
        self.assertFalse(g.hold(2.0))
        self.assertTrue(g.state('alarm.active'))
        g.call('alarm.reset')                                                          # by parameter Reset still allowed
        self.assertFalse(g.state('alarm.active'))
        g.call('alarm.configure', **dict(ALARM, delay_s=0, reset_by_command=False))
        g.wait(0.5)
        self.assertEqual(g.call('alarm.reset', expect=('rejected',))['error']['code'], 'PRECONDITION_FAILED')
        self.assertTrue(g.hold(2.0))
        self.assertFalse(g.state('alarm.active'))

    def test_hold_without_alarm_and_power_off(self):
        g = Rig()
        self.assertFalse(g.hold(2.0))                                                  # 1-4 s without an alarm: no action
        g.call('power.set', enabled=False)
        self.assertFalse(g.sw('pressed'))
        self.assertFalse(g.sw('released'))
        self.assertEqual(g.leds(), DARK)


class AutoZero(unittest.TestCase):
    """Auto-zero (hold 8-12 s, p.23; Calibration Mode 9 / 255, p.34) and the sensor error model behind every reading path."""
    @staticmethod
    def vented(**changes):
        return Rig(**dict(dict(initial_upstream_absolute_pressure_Pa=101325.0, inflow_mol_s=0.0), **changes))   # at the gauge reference

    @staticmethod
    def true_gauge(g):
        return g.state('process.upstream_absolute_pressure_pa') - 101325.0

    def test_ideal_by_default_and_errors_for_the_evaluator_only(self):
        g = Rig(inflow_mol_s=0.0)
        self.assertEqual([g.state(k) for k in ('sensor.offset_error_pa', 'sensor.gain_error', 'sensor.drift_error_pa_s', 'sensor.zero_offset_pa')], [0.0] * 4)
        self.assertEqual(g.state('pressure.reading_pa'), self.true_gauge(g))
        self.assertEqual(g.call('sensor.configure_error', expect=('rejected',), offset_error_Pa=1.0, gain_error=0.0, drift_error_Pa_s=0.0)['error']['code'],
                         'UNKNOWN_CAPABILITY')
        self.assertNotIn('sensor.configure_error', g.A.describe()['capabilities'])
        self.assertEqual(g.A.read(state_ids=['sensor.offset_error_pa'])['error']['code'], 'ACCESS_DENIED')
        g.call('sensor.configure_error', sess=g.E, offset_error_Pa=1000.0, gain_error=0.01, drift_error_Pa_s=10.0)
        self.assertAlmostEqual(g.state('pressure.reading_pa'), 200000.0 * 1.01 + 1000.0, places=6)
        g.wait(100.0)
        self.assertAlmostEqual(g.state('pressure.reading_pa'), 200000.0 * 1.01 + 2000.0, places=6)        # 10 Pa/s drift for 100 s
        self.assertAlmostEqual(g.A.read(state_ids=['pressure.deviation_pa'])['observations']['pressure.deviation_pa']['value'] or 0.0, 0.0)

    def test_auto_zero_at_the_reference_condition_corrects_reading_control_and_alarm(self):
        zeroed, ideal, raw = self.vented(), self.vented(), self.vented()
        for g in (zeroed, raw):
            g.call('sensor.configure_error', sess=g.E, offset_error_Pa=3000.0, gain_error=0.0, drift_error_Pa_s=0.0)
        self.assertTrue(zeroed.hold(9.0))                                                         # release in the 8-12 s pattern (red LED)
        self.assertEqual((zeroed.state('sensor.zero_offset_pa'), zeroed.state('sensor.calibration_mode')), (3000.0, 0))
        self.assertEqual(zeroed.sample([0.05, 0.15, 1.2], zeroed.t), [G, R, G])                  # fast wink 1 s: action started
        for g in (ideal, raw):
            g.wait(10.2)
        for g in (zeroed, ideal, raw):
            g.r.set_boundary(inflow_mol_s=0.0001, downstream_absolute_pressure_Pa=101325.0)
            g.wait(800.0)
        self.assertAlmostEqual(zeroed.state('pressure.reading_pa'), self.true_gauge(zeroed), places=6)
        self.assertAlmostEqual(raw.state('pressure.reading_pa'), self.true_gauge(raw) + 3000.0, places=6)
        for g in (zeroed, ideal, raw):                                                            # the controller follows the indicated value
            g.call('pressure.set_setpoint', pressure_Pa=150000.0)
            g.call('alarm.configure', **dict(ALARM, min_limit_Pa=100000.0, max_limit_Pa=152000.0, delay_s=0))
            g.call('control.set_mode', mode='automatic')
            g.call('control.set_gain', gain_per_Pa_s=1e-6)
            for _ in range(60):
                g.wait(10.0)
        self.assertAlmostEqual(self.true_gauge(zeroed), self.true_gauge(ideal), delta=1e-6 * 150000.0)
        self.assertGreater(abs(self.true_gauge(raw) - self.true_gauge(ideal)), 1000.0)
        self.assertEqual(zeroed.state('alarm.active'), ideal.state('alarm.active'))

    def test_refused_away_from_the_zero_reset_and_factory_zero(self):
        g = Rig(inflow_mol_s=0.0)                                                                 # 2 bar gauge: not the reference condition
        r = g.call('sensor.calibrate_zero')
        self.assertEqual(r, {'calibration_mode': 255, 'zero_offset_Pa': 0.0})
        self.assertEqual(g.state('pressure.reading_pa'), 200000.0)
        v = self.vented()
        v.call('sensor.configure_error', sess=v.E, offset_error_Pa=-4000.0, gain_error=0.0, drift_error_Pa_s=0.0)
        self.assertEqual(v.call('sensor.calibrate_zero'), {'calibration_mode': 0, 'zero_offset_Pa': -4000.0})
        self.assertEqual(v.call('sensor.reset_calibration'), {'zero_offset_Pa': 0.0})
        v.call('sensor.calibrate_zero')
        v.call('device.factory_restore')
        self.assertEqual((v.state('sensor.zero_offset_pa'), v.state('sensor.calibration_mode')), (0.0, 0))
        v.call('power.set', enabled=False)
        self.assertEqual(v.call('sensor.calibrate_zero', expect=('rejected',))['error']['code'], 'PRECONDITION_FAILED')

    def test_switch_equals_direct_command(self):
        a, b = self.vented(), self.vented()
        for g in (a, b):
            g.call('sensor.configure_error', sess=g.E, offset_error_Pa=2500.0, gain_error=0.0, drift_error_Pa_s=0.0)
        a.hold(10.0)
        b.wait(10.0)
        b.call('sensor.calibrate_zero')
        sa, sb = a.r.debug_snapshot(), b.r.debug_snapshot()
        self.assertEqual((sa['state'], sa['world_time_s']), (sb['state'], sb['world_time_s']))


class InstrumentFunctions(unittest.TestCase):
    def test_control_modes_drive_the_valve_and_setpoint(self):
        g = Rig()
        g.call('control.set_control_mode', control_mode=8, set_default=False)
        self.assertEqual(g.state('valve.opening'), 1.0)
        g.call('control.set_control_mode', control_mode=3, set_default=False)
        self.assertEqual(g.state('valve.opening'), 0.0)
        g.r.set_analog_input(fraction=0.3)
        g.call('control.set_control_mode', control_mode=21, set_default=False)
        self.assertEqual(g.state('valve.opening'), 0.3)
        g.call('control.set_control_mode', control_mode=7, set_default=False)
        self.assertEqual((g.state('pressure.active_setpoint_pa'), g.state('control.status')), (FS, 'regulating'))
        g.call('control.set_control_mode', control_mode=1, set_default=False)
        g.wait(0.1)
        self.assertAlmostEqual(g.state('pressure.active_setpoint_pa'), 0.3 * FS)
        g.r.set_analog_input(fraction=0.01)                                            # below 2 %: 0 %, the valve closes
        g.wait(0.1)
        self.assertEqual((g.state('pressure.active_setpoint_pa'), g.state('valve.opening')), (0.0, 0.0))
        r = g.call('valve.set_opening', expect=('rejected',), opening=0.5)
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')                    # only valve steering (20) takes a valve output

    def test_volatile_modes_return_to_the_default_at_power_up(self):
        g = Rig()
        g.call('control.set_control_mode', control_mode=1, set_default=True)
        g.call('control.set_control_mode', control_mode=18, set_default=False)
        g.call('power.set', enabled=False)
        g.call('power.set', enabled=True)
        self.assertEqual(g.state('control.control_mode'), 1)
        g.call('device.reset')
        self.assertEqual(g.state('control.control_mode'), 1)

    def test_response_power_up_alarms_new_setpoint_and_automatic_reset(self):
        g = Rig()
        g.call('pressure.set_setpoint', pressure_Pa=200000.0)
        g.call('control.set_mode', mode='automatic')
        g.call('alarm.configure', mode='response', min_limit_Pa=5000.0, max_limit_Pa=5000.0, delay_s=0, new_setpoint_Pa=150000.0,
               reset_by_switch=True, reset_by_command=True, reset_automatic=True)
        g.call('pressure.set_setpoint', pressure_Pa=300000.0)
        g.wait(0.1)
        self.assertEqual(g.state('alarm.info'), ['minimum', 'response'])
        g.wait(0.1)
        self.assertEqual(g.state('pressure.active_setpoint_pa'), 150000.0)            # alarm new setpoint
        g.call('alarm.configure', mode='absolute', min_limit_Pa=0.0, max_limit_Pa=FS, delay_s=0, new_setpoint_Pa=None, reset_by_switch=True,
               reset_by_command=True, reset_automatic=True)
        g.wait(0.1)
        self.assertFalse(g.state('alarm.active'))
        g.call('alarm.configure', mode='power_up', min_limit_Pa=0.0, max_limit_Pa=0.0, delay_s=0, new_setpoint_Pa=None, reset_by_switch=True,
               reset_by_command=True, reset_automatic=True)
        g.call('power.set', enabled=False)
        g.call('power.set', enabled=True)
        self.assertEqual(g.state('alarm.info'), ['power_up'])
        g.wait(1.0)
        self.assertTrue(g.state('alarm.active'))                                       # a power-up alarm waits for a reset
        r = g.call('alarm.configure', expect=('rejected',), **dict(ALARM, min_limit_Pa=200000.0))
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')                       # maximum must exceed minimum (p.39)


class CriticalError(unittest.TestCase):
    """Critical error of manual p.21 (red LED continuous: needs servicing), injected by the evaluator (consistency sweep 2026-10-19)."""
    def test_injected_critical_error_stops_control_until_serviced(self):
        g = Rig()
        g.call('valve.set_opening', opening=0.4)
        r = g.call('fault.inject', expect=('rejected',), code='critical_error')
        self.assertEqual(r['error']['code'], 'UNKNOWN_CAPABILITY')                    # evaluator only
        self.assertNotIn('fault.inject', g.A.describe()['capabilities'])
        self.assertEqual(g.call('fault.inject', sess=g.E, code='critical_error'), {'critical_error': True})
        self.assertEqual((g.state('device.critical_error'), g.state('control.status'), g.state('valve.opening')), (True, 'critical_error', 0.0))
        self.assertEqual(g.leds(), R)                                                  # red continuous, green off
        g.wait(5.0)
        self.assertEqual((g.leds(), g.state('valve.opening')), (R, 0.0))
        self.assertIsNotNone(g.state('pressure.reading_pa'))                          # the reading continues (assumption)
        for cap, args in (('pressure.set_setpoint', dict(pressure_Pa=200000.0)), ('valve.set_opening', dict(opening=0.5)), ('alarm.reset', {}),
                          ('device.reset', {})):
            self.assertEqual(g.call(cap, expect=('rejected',), **args)['error']['code'], 'PRECONDITION_FAILED', cap)
        self.assertFalse(g.hold(5.0))                                                  # switch functions refused (no indication)
        self.assertEqual(g.call('fault.inject', sess=g.E, expect=('rejected',), code='critical_error')['error']['code'], 'PRECONDITION_FAILED')
        g.call('power.set', enabled=False)
        g.call('power.set', enabled=True)
        self.assertEqual((g.state('device.critical_error'), g.leds()), (True, R))       # stays through a power cycle
        g.call('fault.clear', sess=g.E)
        self.assertEqual((g.state('device.critical_error'), g.leds()), (False, G))
        g.call('valve.set_opening', opening=0.5)
        self.assertEqual(g.state('valve.opening'), 0.5)


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        a, b = Rig(), Rig()
        a.presses(4)
        a.wait(1.1)
        a.hold(0.4)
        a.hold(7.2)
        for dt in (1.1, 0.4, 7.2):
            b.wait(dt)
        b.call('control.set_control_mode', control_mode=7, set_default=True)
        self.assertEqual(a.state('control.control_mode'), 7)
        self.assertEqual((a.r.debug_snapshot()['state'], a.r._aux), (b.r.debug_snapshot()['state'], b.r._aux))
        a.hold(5.0)
        b.wait(5.0)
        b.call('device.reset')
        self.assertEqual((a.r.debug_snapshot()['state'], a.r._aux), (b.r.debug_snapshot()['state'], b.r._aux))


class Rules(unittest.TestCase):
    def test_access_and_arguments(self):
        g = Rig()
        r = g.call('panel.frame', expect=('rejected',))
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        caps = g.A.describe()['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.switch', 'panel.capture'})
        self.assertNotIn('simulation.set_analog_input', caps)
        for args in ({'input_id': 'switch', 'position': 'up'}, {'input_id': 'knob', 'position': 'pressed'}):
            self.assertEqual(g.call('panel.switch', expect=('rejected',), **args)['error']['code'], 'INVALID_ARGUMENT')
        first = g.A.invoke(capability_id='panel.switch', arguments={'input_id': 'switch', 'position': 'pressed'}, request_id='same')
        self.assertEqual(g.A.invoke(capability_id='panel.switch', arguments={'input_id': 'switch', 'position': 'pressed'}, request_id='same'), first)
        self.assertEqual(g.A.invoke(capability_id='panel.switch', arguments={'input_id': 'switch', 'position': 'released'}, request_id='same')['error']['code'],
                         'REQUEST_CONFLICT')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        for k in SPEC['displays']:
            self.assertTrue((a.r.panel().render(k) == b.r.panel().render(k)).all())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.r.panel().save_png('led_mode', OUT / 'home.png')}
        a.sw('pressed')
        a.wait(5.0)
        shots['menu'] = a.r.panel().save_png('led_mode', OUT / 'menu.png')            # holding: the 4-8 s pattern (reset instrument)
        a.sw('released')
        c = Rig()
        c.call('alarm.configure', **dict(ALARM, delay_s=0))
        c.wait(1.5)
        shots['error'] = c.r.panel().save_png('led_error', OUT / 'error.png')          # alarm slow wink, red phase
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.r.panel_capture_dir = str(OUT / 'captures')
        r = g.call('panel.capture', display_id='led_mode')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())

    def test_spec_and_contract(self):
        meta = rt.export_p702cv_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities'])), [])
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for sid, slot in meta['state_slots'].items():
            self.assertEqual(c['state_slots'][sid]['value_schema'], slot['value_schema'], sid)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['inputs'], ['switch'])
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('p702cv_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])
        for k, cap in meta['capabilities'].items():
            for f in ('input_schema', 'output_schema'):
                self.assertEqual(tool.lint_schema(cap[f]), [], (k, f))
            self.assertEqual(tool.validate_value(cap['example_arguments'], cap['input_schema']), [], k)


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 1:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
