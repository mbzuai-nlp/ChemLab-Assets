"""Front-panel tests for CHEM-194 inficon_ul1000fab (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_inficon_ul1000fab.py -q

Every key goes through the session API (panel.press on START / STOP / ZERO / MENU, the eight softkeys and the mains switch, the runtime's normal
invoke path); the host steps the clock and the robot connects parts, sniffer line and test leaks.  Each check looks at the runtime state and at the
screen: the structured frame (screen, text lines, softkey labels) and the rendered pixels.  Themes: the key procedures of the menu survey (start up
and measure, automatic internal and external calibration, trigger level by numerical entry, sniffer mode, vent delay, RS232 control, warnings and
errors); every menu function; panel = direct capability; refusals and locks (control location, Device-PIN, Menu-PIN); help messages that time out;
render determinism and the PNGs of outputs/panel_ui/inficon_ul1000fab/; contract entries."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import inficon_ul1000fab as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'inficon_ul1000fab_01'
MB = rt.MBAR_L
OUT = ROOT / 'outputs/panel_ui/inficon_ul1000fab'
SPEC = rt.PANEL_SPEC
SURVEY_TASKS = ['Start up and measure', 'Automatic internal calibration', 'External calibration', 'Change trigger level 1 from 1.0e-7 to 3e-7 mbar l/s',
                'Switch to sniffer mode', 'Set the vent delay', 'Enable RS232 control', 'Acknowledge a warning or error']
DONE_TASKS = set()
PANEL_CALLS = set()
_panel_invoke = rt.InficonUl1000fabRuntime.panel_invoke


def _recording_panel_invoke(self, capability_id, arguments):
    out = _panel_invoke(self, capability_id, arguments)
    PANEL_CALLS.add(capability_id)
    return out


rt.InficonUl1000fabRuntime.panel_invoke = _recording_panel_invoke
CHAMBER = dict(part_id='chamber', volume_m3=0.002, surface_area_m2=0.1, material='stainless_unbaked', leaks=[])


class Rig:
    def __init__(self, on=True, **plant):
        self.h = rt.InficonUl1000fabRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, **plant))
        if on:
            self.boot()

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            if cap.startswith('panel.'):
                assert self.h.t == t0, cap
            return r['result']
        return r

    def step(self, dt):
        self.call(self.E, 'sim.step', dt_s=float(dt))

    def until(self, pred, limit, every=1.0):
        t = 0.0
        while not pred() and t < limit:
            self.step(every)
            t += every
        return t

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def sk(self, i, hold=0.1):
        return self.press(f'sk{i}', hold)

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self):
        return [' '.join(x.split()) for x in self.frame()['displays']['lcd']['lines'] if x.strip()]

    def text(self):
        return ' | '.join(self.lines())

    def labels(self):
        ws = {w['id']: w['text'] for w in self.frame()['displays']['lcd']['widgets']}
        return {i: ws.get(f'sk{i}_label', '') for i in range(1, 9)}

    def widget(self, wid):
        return next(w for w in self.frame()['displays']['lcd']['widgets'] if w['id'] == wid)

    def message(self):
        m = self.frame()['ui']['message']
        return m and m['text']

    def read(self, k):
        return self.h.read('evaluator')[k]

    def boot(self):
        assert self.press('mains')
        assert self.screen() == 'runup', self.screen()
        self.step(rt.DOC['run_up_s'] + 1.0)
        assert self.screen() == 'standby', self.screen()

    def menu(self, *keys):
        """MENU, then softkeys from the main menu (the menu opens where it was left: Back to the main menu first)."""
        self.press('menu')
        while self.screen() != 'main':
            assert self.sk(1)
        for k in keys:
            assert self.sk(k), (k, self.screen())

    def digits(self, *ds):
        """Numerical entry: each digit by its pair key and the submenu; the cursor moves on by itself; OK at the end."""
        pair_key = {0: 2, 1: 3, 2: 5, 3: 6, 4: 7}
        for d in ds:
            assert self.sk(pair_key[d // 2])
            assert self.sk(3 if d % 2 == 0 else 4)
        return self.sk(8)

    def measure(self, leaks=(), volume=0.002):
        self.call(self.A, 'part.connect', **dict(CHAMBER, volume_m3=volume, leaks=list(leaks)))
        assert self.press('start')
        self.until(lambda: self.read('range.current') == 'ULTRA', 600.0)
        assert self.screen() == 'measure', self.screen()


class KeyProcedures(unittest.TestCase):
    def test_start_up_and_measure(self):
        g = Rig(on=False)
        self.assertEqual(g.screen(), 'off')
        self.assertFalse(g.press('start'))                                     # dark: only the mains switch works
        g.press('mains')
        self.assertIn('RUN UP', g.text())
        g.step(60.0)
        self.assertGreater(g.widget('runup_bar')['fraction'], 0.3)
        g.step(rt.DOC['run_up_s'])
        self.assertIn('STAND-BY', g.text())
        g.measure(leaks=[dict(leak_id='weld', helium_leak_rate_pa_m3_s=1e-8 * MB, path_length_m=0.0)])
        lr = g.h.panel_read()['host.lr']
        self.assertEqual((g.widget('lr_mant')['text'], g.widget('lr_exp')['text'], g.widget('lr_unit')['text']), (lr['mant'], lr['exp'], 'mbar l/s'))
        self.assertIn('VAC ULTRA', g.text())
        self.assertTrue(g.lines()[-1].startswith('V2 S1 S2'))
        g.step(60.0)
        self.assertTrue(g.press('zero'))
        self.assertTrue(g.read('zero.enabled'))
        self.assertIn('ZERO', g.lines()[-1])
        g.press('zero', 3.5)                                                   # held about 3 s: undo
        self.assertFalse(g.read('zero.enabled'))
        g.call(g.A, 'spray.set', target='weld', on=True)
        g.step(20.0)
        self.assertEqual(g.read('leak.status'), 'reject')
        self.assertTrue(g.frame()['displays']['lcd']['inverse_spans'][13])     # S1 / S2 inverted in the status line
        self.assertTrue(g.press('start'))                                      # START again: maximum hold
        g.call(g.A, 'spray.set', target='weld', on=False)
        g.step(30.0)
        self.assertGreater(g.read('leak.hold_pa_m3_s'), 2 * g.read('leak.display_pa_m3_s'))
        self.assertTrue(any(x.startswith('HOLD') for x in g.lines()))
        g.press('stop', 0.5)
        self.assertEqual((g.read('state.current'), g.screen()), ('standby', 'standby'))
        g.press('stop', 3.0)                                                   # longer than the vent delay (2 s): vent
        self.assertEqual(g.read('state.current'), 'vent')
        self.assertIn('VENT', g.text())
        g.step(30.0)
        self.assertEqual(g.read('state.current'), 'standby')
        DONE_TASKS.add('Start up and measure')

    def test_automatic_internal_calibration(self):
        g = Rig()
        g.step(30.0)
        self.assertEqual(g.labels()[5], 'CAL')
        g.sk(5)
        self.assertEqual((g.screen(), g.labels()[4], g.labels()[8]), ('c_type', 'internal', 'external'))
        g.sk(4)
        self.assertEqual((g.labels()[4], g.labels()[8]), ('manual', 'automatic'))
        g.sk(8)
        self.assertEqual((g.read('state.current'), g.screen()), ('calibration', 'cal'))
        self.assertIn('CALIBRATION', g.text())
        g.until(lambda: g.read('state.current') != 'calibration', 120.0)
        self.assertEqual(g.screen(), 'cal_result')
        self.assertIn('accepted', g.text())
        hist = g.read('calibration.history')
        self.assertEqual((len(hist), hist[-1]['kind']), (1, 'internal'))
        self.assertIn(f"New factor {hist[-1]['factor_new']:.2f}", g.text())
        g.sk(8)
        self.assertEqual(g.screen(), 'standby')
        g.press('stop', 3.0)
        DONE_TASKS.add('Automatic internal calibration')

    def test_external_calibration(self):
        g = Rig(ambient_k=303.15, temperature_at_calibration_k=293.15)
        g.measure(leaks=[dict(leak_id='ref', helium_leak_rate_pa_m3_s=1e-7 * MB, path_length_m=0.0)])
        g.step(300.0)
        g.press('zero')
        g.sk(5)
        g.sk(8)
        self.assertEqual(g.screen(), 'c_ext')
        self.assertIn('1.0E-07 mbar l/s', g.text())                           # the default external test leak (manual 2.5)
        g.sk(4)                                                                # Edit leak rate: 5.0E-08 (the sprayed helium fraction 0.5)
        g.digits(5, 0, 0, 8)
        self.assertEqual(g.screen(), 'c_ext')
        self.assertAlmostEqual(g.read('menu.settings')['external_test_leak_vacuum_pa_m3_s'], 5e-8 * MB)
        g.sk(8)                                                                # START
        g.call(g.A, 'spray.set', target='ref', on=True)
        self.assertEqual(g.read('state.current'), 'calibration')
        g.until(lambda: g.read('state.current') != 'calibration', 300.0)
        self.assertEqual(g.screen(), 'cal_result')
        self.assertEqual(g.read('calibration.history')[-1]['kind'], 'external')
        g.sk(8)
        g.step(30.0)
        self.assertAlmostEqual(g.read('leak.display_pa_m3_s') / (1e-7 * MB * rt.ENG['spray_he_fraction']), 1.0, delta=0.12)
        DONE_TASKS.add('External calibration')

    def test_change_trigger_level_1_by_numerical_entry(self):
        g = Rig()
        g.call(g.A, 'settings.configure', trigger_1_pa_m3_s=1e-7 * MB, trigger_2_pa_m3_s=1e-6 * MB)
        g.menu(4)
        self.assertEqual(g.screen(), 'trig')
        g.sk(2)                                                                # Trigger Level 1
        self.assertIn('Trigger Level 1', g.text())
        self.assertIn('1.0x10-07 mbar l/s', g.text())
        self.assertEqual(g.frame()['displays']['lcd']['inverse_spans'][6], [[12, 13]])   # the first digit is inverted
        self.assertEqual([g.labels()[i] for i in range(1, 9)], ['Cancel', '0/1', '2/3', '', '4/5', '6/7', '8/9', '->'])
        g.sk(3)                                                                # 2/3 opens the submenu
        self.assertEqual((g.labels()[3], g.labels()[4]), ('2', '3'))
        g.sk(4)                                                                # 3
        self.assertIn('3.0x10-07', g.text())
        for _ in range(3):
            g.sk(8)                                                            # cursor to the last digit, then OK
        self.assertEqual(g.screen(), 'trig')
        self.assertAlmostEqual(g.read('settings.current')['trigger_1_pa_m3_s'], 3e-7 * MB)
        # Cancel keeps the value; a trigger 1 above trigger 2 is refused with the device's message
        g.sk(2)
        g.digits(5, 0, 0, 5)
        self.assertEqual(g.message(), 'Trigger 2 below trigger 1')
        self.assertEqual(g.screen(), 'num')
        g.sk(1)
        self.assertEqual(g.screen(), 'trig')
        self.assertAlmostEqual(g.read('settings.current')['trigger_1_pa_m3_s'], 3e-7 * MB)
        g.sk(3)
        g.digits(2, 0, 0, 6)
        self.assertAlmostEqual(g.read('settings.current')['trigger_2_pa_m3_s'], 2e-6 * MB)
        DONE_TASKS.add('Change trigger level 1 from 1.0e-7 to 3e-7 mbar l/s')

    def test_switch_to_sniffer_mode(self):
        g = Rig()
        g.call(g.A, 'sniffer.connect', connected=True)
        g.menu(3)
        self.assertEqual((g.labels()[3], g.labels()[7]), ('Sniff', 'Vacuum'))
        g.sk(3)
        self.assertEqual(g.widget('sk3_label')['fill'], 'text')               # the selected option is inverted
        g.sk(8)
        self.assertEqual(g.read('settings.current')['mode'], 'sniff')
        g.press('menu')
        g.press('start')
        g.step(10.0)
        self.assertIn('SNIFF FINE', g.lines()[-1])
        g.press('menu')
        g.sk(4)
        g.sk(5)                                                                # Units: sniff units selectable
        for _ in range(4):
            g.sk(7)
        self.assertIn('ppm', g.text())
        g.sk(8)
        self.assertEqual(g.widget('lr_unit')['text'] if g.press('menu') else None, 'ppm')
        ppm = float(g.widget('lr_mant')['text']) * 10 ** int(g.widget('lr_exp')['text'])
        self.assertAlmostEqual(ppm, g.read('leak.display_pa_m3_s') / rt.ENG['sniff_flow_pa_m3_s'] * 1e6, delta=0.06 * ppm)
        DONE_TASKS.add('Switch to sniffer mode')

    def test_set_the_vent_delay(self):
        g = Rig()
        g.menu(6, 2, 3)
        self.assertEqual(g.screen(), 's_vent')
        self.assertEqual([g.labels()[i] for i in (2, 3, 4, 6, 7)], ['Immediately', '1 s', '1.5 s', '2 s', 'No vent'])
        g.sk(4)
        g.sk(8)
        self.assertEqual(g.read('settings.current')['vent_delay_s'], 1.5)
        g.sk(3)
        g.sk(7)
        g.sk(8)
        self.assertTrue(g.read('menu.settings')['no_vent'])
        g.press('menu')
        g.measure()
        g.press('stop', 5.0)
        self.assertEqual(g.read('state.current'), 'standby')                  # No vent: STOP never vents
        DONE_TASKS.add('Set the vent delay')

    def test_enable_rs232_control(self):
        g = Rig()
        g.menu(6, 5, 3)
        g.sk(6)                                                                # Local & RS232
        self.assertEqual((g.screen(), g.read('menu.settings')['control_location']), ('s_if', 'local_rs232'))
        g.sk(4)
        g.sk(7)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['rs232_protocol'], 'ascii')
        g.sk(3)
        g.sk(3)                                                                # RS232 only: the panel keys are locked
        g.press('menu')
        self.assertFalse(g.press('start'))
        self.assertEqual(g.message(), 'Control location: keys locked')
        self.assertEqual(g.read('state.current'), 'standby')
        g.menu(6, 5, 3)
        g.sk(7)                                                                # back to Local
        self.assertTrue(g.press('start'))
        DONE_TASKS.add('Enable RS232 control')

    def test_acknowledge_a_warning_or_error(self):
        g = Rig()
        g.call(g.E, 'fault.inject', fault='filament_1_failure', active=True)
        self.assertEqual(g.screen(), 'warning')
        self.assertIn('W45', g.text())
        self.assertIn('cathode 1', g.widget('warn_text')['text'])
        g.sk(8)
        self.assertEqual((g.screen(), g.read('alarms.unacknowledged'), g.read('alarms.active')), ('standby', [], ['W45']))
        self.assertIn('!', g.lines()[-1])                                      # the warning triangle stays
        g.sk(4)
        self.assertIn('W45 Emission', g.text())
        g.sk(1)
        g.call(g.E, 'fault.inject', fault='turbo_failure', active=True)
        self.assertEqual(g.screen(), 'error')
        self.assertIn('E52', g.text())
        g.sk(4)                                                                # Menu: look at the settings, the error comes back afterwards
        self.assertEqual(g.screen(), 'main')
        g.press('menu')
        self.assertEqual(g.screen(), 'error')
        g.sk(8)                                                                # Restart: new run-up
        self.assertEqual((g.read('state.current'), g.screen()), ('run_up', 'runup'))
        g.step(rt.DOC['run_up_s'] + 1.0)
        self.assertEqual(g.screen(), 'error')                                  # the cause is still there
        g.call(g.E, 'fault.inject', fault='turbo_failure', active=False)
        self.assertEqual(g.screen(), 'warning')                                # after the restart the cathode warning is shown again
        g.sk(8)
        self.assertEqual(g.screen(), 'standby')
        DONE_TASKS.add('Acknowledge a warning or error')


class MenuFunctions(unittest.TestCase):
    def test_view_menu(self):
        g = Rig()
        g.menu(2, 2)                                                           # Scale: linear / log decades
        g.sk(7)
        g.sk(7)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['decades'], 6)
        g.sk(3)                                                                # Display range: manual, upper limit
        g.sk(2)
        g.sk(3)
        g.sk(8)
        self.assertEqual((g.read('menu.settings')['range_auto'], g.read('menu.settings')['range_upper_exp']), (False, -6))
        g.sk(4)
        g.sk(7)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['time_axis_s'], 64)
        g.sk(5)                                                                # Contrast applies at once; Cancel restores it
        g.sk(7)
        self.assertEqual(g.read('menu.settings')['contrast'], 51)
        self.assertAlmostEqual(g.frame()['displays']['lcd']['brightness'], 55 + 0.45 * 51)
        g.sk(4)
        self.assertTrue(g.frame()['displays']['lcd']['inverted'])
        g.sk(1)
        self.assertEqual((g.read('menu.settings')['contrast'], g.read('menu.settings')['invert']), (50, False))
        g.sk(6)
        g.sk(7)
        g.sk(8)
        self.assertTrue(g.read('menu.settings')['background_standby'])
        g.sk(7)
        g.sk(7)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['decimals'], 2)
        g.sk(8)
        g.sk(7)
        g.sk(7)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['lower_display_limit_exp'], -10)
        g.sk(5)
        self.assertEqual(g.message(), 'Lower leak rate limit of the display' if g.screen() == 'v_lower' else g.message())
        g.press('menu')
        self.assertIn('Bkg', g.text())                                         # background shown in stand-by
        g.measure()
        self.assertGreaterEqual(g.read('leak.display_pa_m3_s'), 1e-10 * MB)    # the lower display limit is a floor of the reading
        self.assertEqual(len(g.widget('lr_mant')['text']), 4)                  # 2 decimal places
        g.sk(6)                                                                # manual range: upper decade on the measurement screen
        self.assertEqual(g.read('menu.settings')['range_upper_exp'], -5)
        g.sk(8)
        self.assertEqual(g.widget('lr_trend')['type'], 'plot')

    def test_trigger_and_alarm_menu(self):
        g = Rig()
        g.menu(4, 4)                                                           # Volume
        g.sk(7)
        g.sk(6)
        g.sk(4)
        g.sk(8)
        m = g.read('menu.settings')
        self.assertEqual((m['volume'], m['min_volume'], m['beep']), (3, 1, False))
        g.sk(5)                                                                # Units
        g.sk(3)
        g.sk(7)
        g.sk(8)
        self.assertEqual((g.read('menu.settings')['pressure_unit'], g.read('menu.settings')['leak_unit']), ('Pa', 'Pa m3/s'))
        g.sk(7)                                                                # Alarm delay: up to infinite
        for _ in range(30):
            g.sk(7)
        self.assertIn('infinite', g.text())
        g.sk(8)
        self.assertTrue(g.read('menu.settings')['alarm_delay_infinite'])
        g.sk(8)                                                                # Audio alarm type
        g.sk(2)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['audio_alarm'], 'pinpoint')
        g.press('menu')
        g.measure()
        tone = g.read('audio.tone')
        self.assertTrue(tone['on'])
        self.assertGreaterEqual(tone['hz'], 491.0)
        self.assertEqual(g.widget('lr_unit')['text'], 'Pa m3/s')
        g.sk(2)                                                                # speaker + / -
        self.assertEqual(g.read('menu.settings')['volume'], 4)
        for _ in range(5):
            g.sk(3)
        self.assertEqual(g.read('menu.settings')['volume'], 1)                # stops at the minimum loudness

    def test_vacuum_zero_mass_settings(self):
        g = Rig()
        g.menu(6, 2, 2)                                                        # automatic purge off
        g.sk(3)
        g.sk(8)
        self.assertFalse(g.read('menu.settings')['auto_purge'])
        g.sk(4)
        g.sk(3)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['vacuum_ranges'], 'fine_only')
        g.sk(6)                                                                # leak rate of the internal test leak
        g.digits(4, 0, 0, 8)
        self.assertAlmostEqual(g.read('menu.settings')['internal_test_leak_pa_m3_s'], 4e-8 * MB)
        g.sk(7)                                                                # machine factor 2.5E+01
        g.sk(3)
        g.sk(3)                                                                # 2
        g.sk(5)
        g.sk(4)                                                                # 5
        g.sk(2)                                                                # sign + (toggle from -)
        if g.h.panel.ui['vars']['ne_sign'] == -1:
            g.sk(4)
            g.sk(2)
        g.digits(0, 1)
        self.assertAlmostEqual(g.read('settings.current')['machine_factor'], 25.0, delta=1e-9)
        g.sk(5)
        self.assertEqual(g.message(), 'TC1000 test chamber not connected')
        self.assertIn('TC1000', g.frame()['ui']['unsupported'])
        g.sk(1)
        g.sk(3)
        g.sk(3)
        g.sk(3)
        g.sk(8)
        self.assertFalse(g.read('settings.current')['background_suppression'])
        g.sk(7)
        g.sk(6)
        g.sk(8)
        self.assertEqual(g.read('settings.current')['zero_mode'], 'i_zero')
        g.sk(1)
        g.sk(4)
        g.sk(2)
        self.assertEqual(g.message(), 'Only helium (4 amu) in this configuration')
        g.press('menu')
        g.measure()
        self.assertEqual(g.read('range.current'), 'FINE')                     # FINE only: no ULTRA
        self.assertIn('COR', g.lines()[-1])
        self.assertTrue('I-ZERO' in g.lines()[-1] or 'STABLE' in g.lines()[-1])

    def test_interfaces_misc_parameters(self):
        g = Rig()
        g.menu(6, 5, 7)                                                        # recorder output 2: LR exponent
        g.sk(2)
        g.sk(7)
        g.sk(7)
        g.sk(7)
        g.sk(7)
        g.sk(7)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['recorder_2'], 'lr_exponent')
        g.sk(8)
        g.sk(7)
        g.sk(6)
        g.sk(8)
        self.assertEqual((g.read('menu.settings')['recorder_scale_v'], g.read('menu.settings')['recorder_upper_exp']), (1.0, 6))
        self.assertAlmostEqual(g.read('recorder.output_v')[0], round(g.read('pressure.inlet_pa') and 0.0, 2))      # no leak rate in stand-by
        g.sk(1)
        g.sk(6)                                                                # Miscellaneous
        g.sk(2)                                                                # Time&Date: 24.12.26 then 18:30
        self.assertIn('Date DD.MM.YY', g.text())
        g.digits(2, 4, 1, 2, 2, 6)
        self.assertIn('Time hh:mm', g.text())
        g.digits(1, 8, 3, 0)
        self.assertEqual(g.read('device.datetime')[:16], '2026-12-24T18:30')
        g.sk(3)
        for _ in range(9):
            g.sk(3)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['language'], 'Korean')
        g.sk(3)
        for _ in range(7):
            g.sk(7)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['language'], 'German')
        g.press('menu')
        g.menu()
        self.assertIn('Hauptmenue', g.text())
        g.sk(6)
        g.sk(6)
        g.sk(4)
        g.sk(3)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['leak_rate_filter'], 'fixed')
        g.sk(6)
        g.sk(6)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['mains_hz'], 60)
        g.sk(7)
        g.sk(3)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['filter_interval_h'], 1500)
        g.sk(8)
        g.sk(7)
        g.sk(8)
        self.assertTrue(g.read('menu.settings')['filter_message'])
        # parameter sets: save set 1 as 'AB', change a setting, load set 1, defaults
        g.sk(1)
        g.sk(7)
        g.sk(2)
        g.sk(2)
        g.sk(7)
        g.sk(2)
        g.sk(2)
        g.sk(8)
        self.assertEqual(g.read('parameters.sets')[0]['name'], 'AB')
        g.call(g.A, 'menu.configure', mains_hz=50)
        g.sk(6)
        self.assertIn('Name AB', g.text())
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['mains_hz'], 60)
        g.sk(5)
        self.assertEqual((g.read('menu.settings'), g.message()), (rt.MENU_DEFAULTS, 'Default values loaded'))

    def test_monitoring_information_access(self):
        g = Rig()
        g.menu(6, 8, 3)                                                        # particle protection on
        g.sk(7)
        g.sk(8)
        self.assertTrue(g.read('menu.settings')['particle_protection'])
        g.sk(3)
        g.sk(3)
        g.sk(8)
        g.sk(4)                                                                # contamination protection, limit 1.0E-05
        g.sk(4)
        g.digits(1, 0, 0, 5)
        g.sk(7)
        g.sk(8)
        self.assertEqual((g.read('menu.settings')['contamination_protection'], g.read('menu.settings')['contamination_limit_pa_m3_s']), (True, 1e-5 * MB))
        g.sk(5)                                                                # pressure limits: EVAC-GROSS 12 mbar, GROSS-FINE 1.5 mbar
        g.sk(2)
        g.sk(3)
        g.sk(8)
        self.assertEqual((g.read('menu.settings')['evac_gross_pa'], g.read('menu.settings')['gross_fine_pa']), (1200.0, 150.0))
        g.sk(5)
        g.sk(4)
        self.assertEqual(g.message(), 'Argon values not available')
        g.sk(1)
        g.sk(6)                                                                # sniff limits
        g.sk(6)
        g.sk(8)
        self.assertEqual(g.read('menu.settings')['sniff_p_max_pa'], 120.0)
        g.sk(7)                                                                # evacuation time until 100 mbar: endless
        for _ in range(20):
            g.sk(6)
        g.sk(8)
        self.assertTrue(g.read('menu.settings')['evac_100mbar_endless'])
        g.sk(2)
        g.sk(7)
        g.sk(8)
        self.assertTrue(g.read('settings.current')['calibration_request'])
        # information pages
        g.press('menu')
        g.press('menu')
        g.sk(1)
        g.sk(1)
        g.sk(7)
        g.sk(2)
        self.assertIn('Trigger 1', g.text())
        g.sk(7)
        self.assertIn('2/4', g.text())
        g.sk(1)
        g.sk(3)
        self.assertIn('TMP', g.text())
        g.sk(1)
        g.sk(4)
        self.assertIn('V2:open', g.text())
        g.sk(1)
        g.sk(7)
        self.assertIn('He (4) 1.000', g.text())
        g.sk(1)
        g.sk(8)
        self.assertEqual(g.message(), 'Service PIN required')
        # access control: CAL access off, Menu-PIN 1234, then the menu asks for it
        g.sk(1)
        g.sk(8)
        g.sk(2)
        g.sk(3)
        g.sk(8)
        self.assertFalse(g.read('menu.settings')['cal_access'])
        g.sk(8)
        g.digits(1, 2, 3, 4)
        self.assertEqual(g.read('access.pins_set'), dict(device=False, menu=True))
        g.press('menu')
        self.assertEqual(g.labels()[5], '')                                    # no CAL key in stand-by
        g.h.panel.ui['vars']['menu_ok'] = False                                # a new session of the menu (as after power-up)
        g.press('menu')
        self.assertEqual(g.screen(), 'num')
        g.digits(1, 1, 1, 1)
        self.assertEqual((g.message(), g.screen()), ('Wrong PIN', 'num'))
        g.digits(1, 2, 3, 4)
        self.assertEqual(g.screen(), 'main')

    def test_device_pin_and_purge(self):
        g = Rig()
        g.call(g.A, 'access.set_pin', kind='device', pin=4321)
        g.press('mains')
        g.press('mains')
        self.assertEqual((g.screen(), g.read('access.device_locked')), ('num', True))
        self.assertIn('Device-PIN', g.text())
        g.step(300.0)
        self.assertEqual(g.read('state.current'), 'run_up')                   # without the PIN the pumps do not start
        self.assertFalse(g.press('menu') and g.screen() != 'num')
        g.digits(4, 3, 2, 1)
        self.assertEqual((g.read('access.device_locked'), g.screen()), (False, 'runup'))
        g.step(rt.DOC['run_up_s'] + 1.0)
        g.sk(7)                                                                # manual purge
        self.assertTrue(g.read('purge.active'))
        self.assertIn('PURGE', g.text())
        g.sk(7)
        self.assertFalse(g.read('purge.active'))
        g.measure()
        g.press('stop')                                                        # automatic purge 20 s after the change to stand-by
        g.step(21.0)
        self.assertTrue(g.read('purge.active'))
        g.step(20.0)
        self.assertFalse(g.read('purge.active'))


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_calls(self):
        a, b = Rig(), Rig()
        a.menu(4, 2)
        a.digits(3, 0, 1, 0)
        b.call(b.A, 'settings.configure', trigger_1_pa_m3_s=3e-10 * MB)
        a.sk(8)
        a.sk(6)
        a.sk(8)
        b.call(b.A, 'menu.configure', audio_alarm='setpoint')
        a.press('menu')
        a.menu(6, 2, 3)
        a.sk(6)
        a.sk(8)
        b.call(b.A, 'settings.configure', vent_delay_s=2.0)
        b.call(b.A, 'menu.configure', no_vent=False)
        for g in (a, b):
            g.press('menu') if g is a else None
            g.call(g.A, 'part.connect', **CHAMBER)
            g.call(g.A, 'measure.start') if g is b else g.press('start')
            g.step(120.0)
        ra, rb = a.h.read('evaluator'), b.h.read('evaluator')
        self.assertEqual(json.dumps(ra, sort_keys=True, default=str), json.dumps(rb, sort_keys=True, default=str))


class RenderAndContract(unittest.TestCase):
    def test_help_message_times_out(self):
        g = Rig()
        g.menu(2, 4)
        g.sk(5)
        self.assertEqual(g.message(), 'Length of the time axis in trend mode')
        g.step(5.0)
        self.assertIsNone(g.message())

    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.measure(leaks=[dict(leak_id='weld', helium_leak_rate_pa_m3_s=1e-8 * MB, path_length_m=0.0)])
            g.step(60.0)
            return g
        a, b = scenario(), scenario()
        self.assertTrue((a.h.panel.render('lcd') == b.h.panel.render('lcd')).all())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('lcd', OUT / 'home.png')}
        a.sk(8)
        a.step(30.0)
        shots['trend'] = a.h.panel.save_png('lcd', OUT / 'trend.png')
        a.sk(8)
        a.press('menu')
        shots['menu'] = a.h.panel.save_png('lcd', OUT / 'menu.png')
        a.sk(4)
        a.sk(2)
        a.sk(3)
        shots['numerical_entry'] = a.h.panel.save_png('lcd', OUT / 'numerical_entry.png')
        a.sk(1)
        a.sk(1)
        a.press('menu')
        a.call(a.E, 'fault.inject', fault='filament_1_failure', active=True)
        shots['error'] = a.h.panel.save_png('lcd', OUT / 'error.png')
        r = Rig(on=False)
        r.press('mains')
        r.step(90.0)
        shots['runup'] = r.h.panel.save_png('lcd', OUT / 'runup.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_and_copies(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        self.assertNotIn('panel', vars(g.h))

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(SPEC, set(rt.ALL_CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.capture', 'panel.frame'})
        m = rt.export_inficon_ul1000fab_contract_metadata()
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k, e in m['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'errors', 'state_effects', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], e[key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for i in (1, 2, 3, 4, 6, 7, 8):
            self.assertIn(f'softkey_{i}', c['components'])
        self.assertIn('menu_key', c['components'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/inficon_ul1000fab.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('ul_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class SyncFixes(unittest.TestCase):
    """Sync audit 2026-10-19: external calibration in sniff mode (sniff test leak, own calibration factor, close-leak step), refusals with their own
    messages, and the control location locking the key commands on both paths."""
    def sniff_rig(self, **plant):
        g = Rig(**plant)
        g.call(g.A, 'sniffer.connect', connected=True)
        g.call(g.A, 'settings.configure', mode='sniff')
        g.call(g.A, 'part.connect', part_id='tl', volume_m3=1e-4, surface_area_m2=0.01, material='stainless_unbaked', helium_fill_pa=rt.P_REF,
               leaks=[dict(leak_id='tl', helium_leak_rate_pa_m3_s=1e-5 * MB, path_length_m=0.0)])           # the sniff test leak (1E-5 mbar l/s)
        g.call(g.A, 'sniffer.position', target='tl', distance_m=0.0)
        self.assertTrue(g.press('start'))
        g.step(30.0)
        return g

    def leak_reading(self, g):
        """Calibrated signal at the test leak minus the signal with the sniffer held away (air-helium background); the displayed value adds the
        display noise to this signal."""
        g.call(g.A, 'sniffer.position', target='tl', distance_m=0.0)
        g.step(20.0)
        on = g.read('truth.detector')['raw_signal_pa_m3_s']
        g.call(g.A, 'sniffer.position', target='tl', distance_m=0.2)
        g.step(20.0)
        return on - g.read('truth.detector')['raw_signal_pa_m3_s']

    def test_external_calibration_in_sniff_mode(self):
        g = self.sniff_rig(ambient_k=303.15, temperature_at_calibration_k=293.15)
        before = self.leak_reading(g)
        self.assertGreater(abs(before / (1e-5 * MB) - 1), 0.07)                # 10 K from the last calibration: about 10 % off
        g.call(g.A, 'sniffer.position', target='tl', distance_m=0.0)
        g.step(20.0)
        self.assertTrue(g.sk(5))                                               # CAL
        self.assertTrue(g.sk(8))                                               # external
        self.assertEqual(g.screen(), 'c_ext')
        self.assertIn('1.0E-05 mbar l/s', g.text())                            # the sniff test leak of the menu (manual 2.5 default)
        self.assertTrue(g.sk(8))                                               # START
        self.assertEqual(g.read('state.current'), 'calibration')
        self.assertFalse(g.sk(8))                                              # no OK yet: the leak-open step runs
        g.step(rt.SNIFF_CAL_STEP_S + 1.0)
        self.assertIn('Close leak, OK', g.text())
        g.call(g.A, 'sniffer.position', target='tl', distance_m=0.2)           # close the test leak: sniffer away from it
        self.assertTrue(g.sk(8))                                               # OK (calibration.proceed)
        g.until(lambda: g.read('state.current') != 'calibration', 120.0)
        self.assertEqual(g.screen(), 'cal_result')
        last = g.read('calibration.history')[-1]
        self.assertEqual((last['kind'], last['mode'], last['accepted']), ('external', 'sniff', True))
        self.assertAlmostEqual(g.read('calibration.factor_sniff'), last['factor_new'])
        self.assertEqual(g.read('calibration.factor'), 1.0)                   # the vacuum factor is a separate one
        g.sk(8)
        self.assertAlmostEqual(self.leak_reading(g) / (1e-5 * MB), 1.0, delta=0.05)

    def test_sniff_mode_refusals_have_their_own_messages(self):
        g = self.sniff_rig()
        g.sk(5)
        g.sk(4)                                                                # internal
        before = json.dumps(g.read('calibration.history'))
        self.assertFalse(g.sk(8))                                              # automatic internal calibration: not in sniff mode
        self.assertEqual(g.message(), 'Sniff mode: external calibration only')
        self.assertEqual(json.dumps(g.read('calibration.history')), before)
        self.assertEqual(g.call(g.A, 'calibration.proceed', expect='rejected')['error']['code'], 'NO_CAL_STEP')
        v = Rig()
        v.call(v.A, 'sniffer.connect', connected=True)                         # sniffer line on the inlet, vacuum mode
        self.assertEqual(v.call(v.A, 'calibration.start', expect='rejected', kind='internal')['error']['code'], 'SNIFFER_IN_VACUUM_MODE')

    def test_control_location_and_the_rs232_interface(self):
        """Sweep 2026-10-19, rule 5: the agent's direct commands stand for RS232 control; the panel keys are the local keyboard (manual 6.6.4.1,
        interface description 1.1).  The simulated unit starts at Local & RS232 (both work)."""
        g = Rig()
        self.assertEqual(g.read('menu.settings')['control_location'], 'local_rs232')
        g.call(g.A, 'menu.configure', control_location='rs232')                # RS232 only: the panel keys are locked, RS232 commands work
        self.assertFalse(g.press('start'))
        self.assertEqual(g.message(), 'Control location: keys locked')
        self.assertEqual(g.read('state.current'), 'standby')
        g.call(g.A, 'measure.start')
        self.assertIn(g.read('state.current'), ('evacuation', 'measure'))
        g.call(g.A, 'measure.stop')
        g.call(g.A, 'menu.configure', control_location='local')                # Local: RS232 control is not enabled
        for cap, args in (('measure.start', {}), ('zero.toggle', {}), ('menu.configure', dict(control_location='local_rs232')),
                          ('calibration.start', dict(kind='internal'))):
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'REMOTE_DISABLED')
        g.call(g.A, 'datalog.read', last=5)                                    # reads stay allowed
        g.A.read(instance_id=INST, state_ids=['state.current'])
        g.call(g.E, 'menu.configure', control_location='local')                # the evaluator is not restricted
        self.assertTrue(g.press('start'))                                      # the local keys work
        self.assertIn(g.read('state.current'), ('evacuation', 'measure'))
        g.press('stop')
        g.menu(6, 5, 3)
        g.sk(5)                                                                # Local & PLC: no RS232 control
        self.assertEqual((g.screen(), g.read('menu.settings')['control_location']), ('s_if', 'local_plc'))
        self.assertEqual(g.call(g.A, 'measure.start', expect='rejected')['error']['code'], 'REMOTE_DISABLED')
        g.sk(3)
        g.sk(6)                                                                # Local & RS232 again on the panel
        g.call(g.A, 'measure.start')
        self.assertIn(g.read('state.current'), ('evacuation', 'measure'))

    def test_injected_manual_codes_show_and_act(self):
        """Sweep 2026-10-19, rule 3: the other codes of the manual's list (8.2) are raised by the evaluator; warnings stay until OK, errors
        interrupt the measurement and stay until Restart (8.1)."""
        g = Rig()
        g.call(g.A, 'measure.start')
        g.until(lambda: g.read('state.current') == 'measure', 120.0)
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', fault='W53', active=True)['error']['code'], 'ACCESS_DENIED')
        g.call(g.E, 'fault.inject', fault='W53', active=True)                 # a warning: the detector keeps measuring
        self.assertEqual((g.screen(), g.read('state.current')), ('warning', 'measure'))
        self.assertIn('W53', g.text())
        self.assertIn('electronic unit is too high', g.widget('warn_text')['text'])
        g.sk(8)
        self.assertEqual(g.read('state.current'), 'measure')
        g.call(g.E, 'fault.inject', fault='E50', active=True)                 # an error: stand-by, shown until Restart
        self.assertEqual((g.screen(), g.read('state.current')), ('error', 'standby'))
        self.assertIn('E50', g.text())
        self.assertIn('No communication with turbo pump', g.widget('err_text')['text'])
        self.assertEqual(g.call(g.A, 'measure.start', expect='rejected')['error']['code'], 'DETECTOR_FAULT')
        g.sk(8)                                                                # Restart
        g.step(rt.DOC['run_up_s'] + 1.0)
        self.assertEqual(g.screen(), 'error')                                  # the cause is still present
        g.call(g.E, 'fault.inject', fault='E50', active=False)
        g.sk(8)
        g.step(rt.DOC['run_up_s'] + 1.0)
        self.assertNotIn('E50', g.read('alarms.active'))
        g.call(g.A, 'measure.start')
        self.assertIn(g.read('state.current'), ('evacuation', 'measure'))


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)
            self.assertEqual(set(rt.TERMINAL_CAPS) - PANEL_CALLS, set())       # every menu function ran through the panel


if __name__ == '__main__':
    unittest.main()
