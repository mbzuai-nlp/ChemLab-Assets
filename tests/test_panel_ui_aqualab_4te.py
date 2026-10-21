"""front-panel tests for CHEM-007 aqualab_4te (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/aqualab_4te.py):
python3 -m pytest tests/test_panel_ui_aqualab_4te.py -q

Every gesture goes through the session API (panel.press / panel.switch, the runtime's normal invoke path); the screen is read from the structured
frame (panel.frame, evaluator) and the rendered pixels.  The sample, the lid and the cup are host actions (the lid is not a panel input).  Themes:
the six key procedures of the menu survey (reading, chamber temperature, Custom mode, linear offset with a standard, viewing stored data, sample
temperature check); the other Configuration options (Temp Eq, ISO 18787 / Low-Emitting, date / time, regional formatting with °F, autosave,
annotation on the character grid, beeps and AUDIO OFF, print mode, diagnostics, about, %Moisture model selection and calibration New / Edit /
Defaults / Delete, factory defaults, administrator / users / access / log-in), the Data tab delete with its reminder; the device functions as
direct commands; panel = direct command; refusals shown as the instrument's messages; no menu time-out; render determinism, evidence PNGs and
contract entries."""
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
from asset_runtime import aqualab_4te as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'aqualab4te_01'
OUT = ROOT / 'outputs/panel_ui/aqualab_4te'
C0 = 273.15
SURVEY_TASKS = ['take a water activity reading', 'set chamber temperature to 25 C', 'select Custom mode with 2 readings within 0.001 aw',
                'adjust linear offset with 0.76 aw standard', 'view stored readings', 'check sample temperature before reading']
DONE_TASKS = set()
ISOTHERM = dict(matrix_id='salt', temperature_k=298.15, source='synthetic panel test isotherm',
                points=[dict(water_activity=0.5, moisture_dry_basis=0.10), dict(water_activity=0.99, moisture_dry_basis=0.60)])


class Rig:
    """A runtime with an agent and an evaluator session; the operator's keys, lever and switch only through panel.*."""

    def __init__(self, power=True):
        self.h = rt.Aqualab4teRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        if power:
            self.switch('power', 'I')
            self.step(4.0)                                                    # past the splash screen

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        if r['status'] in ('succeeded', 'accepted'):
            rt.validate(rt.ALL_CAPS[cap][1], r['result'])
            assert self.h.time_s == t0, cap                                   # commands and gestures take no simulated time
            return r['result']
        return r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        self.h.step(float(dt))

    # ---- host actions (the cup, the lid)
    def load(self, sid='sodium_chloride', near_aw=None, **kw):
        row = self.h.templates[sid]
        idx = min(range(len(row['records'])), key=lambda i: abs(row['records'][i]['temperature_k'] - 298.15) * 10
                  + (abs(row['records'][i]['value'] - near_aw) if near_aw else 0))
        self.call(self.A, 'lid.set_angle', angle_rad=math.pi / 2)
        self.call(self.E, 'sample.configure', **dict(rt.CAPS['sample.configure'][6], substance_id=sid, record_index=idx, **kw))
        self.call(self.A, 'lid.set_angle', angle_rad=0.0)
        return row['records'][idx]

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lines(self):
        return self.frame()['displays']['lcd']['lines']

    def keys(self):
        return self.lines()[-1].split()

    def screen(self):
        return self.frame()['screen']

    def item(self):
        m = self.frame()['ui']['menu']
        return m and m['item']

    def press(self, k, n=1):
        ok = True
        for _ in range(n):
            ok = self.call(self.A, 'panel.press', input_id=k)['accepted'] and ok
        return ok

    def switch(self, inp, pos):
        return self.call(self.A, 'panel.switch', input_id=inp, position=pos)['accepted']

    def to_item(self, iid, limit=20):
        """Move the list cursor (DOWN, then UP) until the item is under it."""
        for k in ('down', 'up'):
            for _ in range(limit):
                if self.item() == iid:
                    return True
                if not self.press(k) and self.item() != iid:
                    break
        assert self.item() == iid, (iid, self.item(), self.lines())
        return True

    def tab(self, name):
        """Back to the top of a tab with the right round key (CANCEL in lists, then MENU: Configuration -> Data -> Measurement)."""
        for _ in range(8):
            if self.screen() == name and self.editor() is None and (name == 'meas' or len(self.frame()['ui']['menu']['path']) == 1):
                return
            self.press('btn3')
        raise AssertionError((name, self.screen()))

    def menu(self):
        """From the Measurement tab to the Configuration tab (MENU)."""
        assert self.screen() == 'meas', self.screen()
        self.press('btn3')
        assert self.screen() == 'config'

    def open(self, *path):
        """Configuration tab: walk the items of the path with DOWN / UP and ENTER."""
        for iid in path:
            self.to_item(iid)
            assert self.press('btn2'), iid

    def editor(self):
        return self.frame()['ui']['editor']

    def type(self, text):
        """Type on the character grid: arrows to the character, OK (round key 2); ^ switches case."""
        for ch in text:
            g = self.editor()
            name = g['grid_name'] or 'kb'
            if ch.isalpha() and ((ch.islower() and name == 'kb') or (ch.isupper() and name == 'kb_lower')):
                self._go_to_token('^')
                self.press('btn2')
                name = self.editor()['grid_name'] or 'kb'
            self._go_to_token('SP' if ch == ' ' else ch)
            assert self.press('btn2'), ch

    def _go_to_token(self, label):
        g = self.editor()
        grid = rt.PANEL_SPEC['char_grids'][g['grid_name'] or 'kb']
        labels = [[grid.get('labels', {}).get(t[1:-1], t) if isinstance(t, str) and t.startswith('{') else (t['label'] if isinstance(t, dict) else t)
                   for t in row['tokens']] for row in grid['rows']]
        r, c = next((i, row.index(label)) for i, row in enumerate(labels) if label in row)
        r0, c0 = g['grid']
        self.press('down' if r > r0 else 'up', abs(r - r0))
        c0 = min(c0, len(labels[r]) - 1)
        self.press('right' if c > c0 else 'left', abs(c - c0))
        assert self.editor()['grid'] == [r, c], (label, self.editor()['grid'])

    def read_to_end(self, limit_s=900):
        t = 0
        while self.h.phase == 'running' and t < limit_s:
            self.step(10)
            t += 10


def ready(**kw):
    r = Rig()
    rec = r.load(**kw)
    r.call(r.A, 'thermal.set_temperature', temperature_k=round((rec['temperature_k'] - C0) * 10) / 10 + C0)
    return r, rec


# ---------------------------------------------------------------- the six key procedures of the menu survey
class SurveyProcedures(unittest.TestCase):
    def test_take_a_water_activity_reading(self):
        r = Rig(power=False)
        self.assertEqual(r.lines(), [''] * 6)                                  # off: blank
        self.assertTrue(r.switch('power', 'I'))                                 # 1 power on: splash, then the Measurement tab
        self.assertEqual(r.screen(), 'splash')
        r.step(4.0)
        self.assertEqual(r.screen(), 'meas')
        self.assertTrue(r.lines()[0].startswith('Measurement'))
        r.call(r.A, 'isotherm.configure', **ISOTHERM)                          # (a product model loaded through AquaLink)
        r.call(r.A, 'isotherm.select', name=None)
        self.assertTrue(r.press('down'))                                        # 2 DOWN selects the product model
        self.assertTrue(r.lines()[0].startswith('^vsalt'))
        self.assertEqual(r.lines()[2].strip(), '-- %MC')
        rec = r.load()                                                          # 4-7 (host) lid open, cup in, lid closed
        r.call(r.A, 'thermal.set_temperature', temperature_k=round((rec['temperature_k'] - C0) * 10) / 10 + C0)
        self.assertTrue(r.switch('lever', 'READ'))                              # 8 lever to READ: the reading starts
        self.assertEqual(r.h.phase, 'running')
        self.assertEqual(r.keys(), ['CANCEL'])
        r.step(120)
        self.assertIsNotNone(r.val('water_activity.indicated'))                 # intermediate readings within 1-2 min
        r.read_to_end()
        self.assertEqual(r.h.phase, 'succeeded')
        self.assertEqual(r.keys(), ['AUDIO', 'ENTER', 'MENU'])                   # the end beeps (four: 2 s), AUDIO OFF silences them
        r.step(3)
        self.assertEqual(r.keys(), ['SAVE', 'ENTER', 'MENU'])                    # the SAVE icon replaces the spinning icon
        aw = r.val('water_activity.indicated')
        self.assertEqual(r.lines()[1].strip(), f'{aw:.4f} aw')
        self.assertLess(abs(aw - rec['value']), 0.003)
        self.assertEqual(r.lines()[2].strip(), f"{r.h._measure()['moisture_dry_basis'] * 100:.2f} %MC")
        self.assertFalse(r.val('beeper.on'))
        self.assertEqual(len(r.val('data.records')), 1)                         # autosave (default on)
        DONE_TASKS.add('take a water activity reading')

    def test_set_chamber_temperature_to_25_c(self):
        r = Rig()
        r.call(r.A, 'thermal.set_temperature', temperature_k=303.15)
        r.menu()                                                                # 1 MENU: Configuration tab
        r.to_item('temp_c')                                                     # 2 UP / DOWN to Temperature, ENTER
        self.assertTrue(r.press('btn2'))
        self.assertEqual(r.keys(), ['SAVE', 'CANCEL'])
        self.assertTrue(r.press('down', 50))                                    # 3 UP / DOWN: 0.1 °C steps
        self.assertEqual(r.lines()[1].strip(), '25.0 °C')
        self.assertTrue(r.press('btn2'))                                        # 4 SAVE
        self.assertAlmostEqual(r.val('thermal.target_k'), 298.15)
        self.assertIn('Temperature: 25.0 °C', r.lines())
        r.to_item('temp_c')
        r.press('btn2')
        r.press('up')
        self.assertTrue(r.press('btn3'))                                        # CANCEL: no change
        self.assertAlmostEqual(r.val('thermal.target_k'), 298.15)
        DONE_TASKS.add('set chamber temperature to 25 C')

    def test_select_custom_mode_2_readings_within_0_001(self):
        r = Rig()
        r.menu()
        r.open('mode')                                                          # 1 Mode, ENTER
        self.assertEqual(r.lines()[0], 'Mode: Single')
        r.open('custom')                                                        # 2 Custom...
        self.assertEqual(r.editor()['field'], 'readings')
        r.press('right')                                                        # 3 RIGHT: delta aw
        self.assertEqual(r.editor()['field'], 'delta')
        r.press('up', 5)                                                        # UP / DOWN: 0.0005 -> 0.0010
        r.press('left')
        self.assertEqual(r.editor()['buffer'], dict(readings=2, delta=0.001))
        self.assertTrue(r.press('btn2'))                                        # 4 SAVE stores both
        self.assertEqual((r.h.mode, r.h.consecutive, r.h.delta), ('custom', 2, 0.001))
        self.assertIn('Mode: 2 <0.0010', [x.strip() for x in r.lines()])
        DONE_TASKS.add('select Custom mode with 2 readings within 0.001 aw')

    def test_adjust_linear_offset_with_a_standard(self):
        r, rec = ready(near_aw=0.76)
        r.call(r.A, 'calibration.configure', offset_aw=0.004, source='drift (test)')   # the instrument reads 0.004 high
        r.menu()
        r.open('calibration', 'aw', 'start')                                    # 1 Calibration > Water Activity > Start
        self.assertEqual(r.lines()[1:4], ['  Insert a fresh', ' standard and seal', '    the chamber.'])
        r.switch('lever', 'READ')                                               # 2 standard in, lid closed, lever READ (no reading off the tab)
        self.assertEqual(r.h.phase, 'idle')
        self.assertTrue(r.press('btn2'))                                        # OK begins testing
        self.assertEqual(r.screen(), 'cal_read')
        r.read_to_end()
        self.assertEqual(r.screen(), 'cal_change')                              # 3 Change the offset
        measured = r.frame()['ui']['vars']['cal_measured']
        self.assertAlmostEqual(measured, rec['value'] + 0.004, delta=0.003)
        steps = round((measured - rec['value']) / 0.0001)
        r.press('down', steps)                                                  # UP / DOWN to the standard value
        self.assertEqual(r.lines()[2].strip(), f"{rec['value']:.4f} aw")
        self.assertTrue(r.press('btn2'))                                        # 4 SAVE
        self.assertAlmostEqual(r.val('calibration.offset_aw'), round(0.004 - steps * 0.0001, 4), places=6)
        self.assertLess(abs(r.val('calibration.offset_aw')), 0.003)
        self.assertEqual(r.screen(), 'config')
        DONE_TASKS.add('adjust linear offset with 0.76 aw standard')

    def test_view_stored_readings(self):
        r, rec = ready()
        for name in range(3):                                                   # three autosaved readings
            r.switch('lever', 'READ')
            r.read_to_end()
            r.switch('lever', 'OPEN')
            r.step(60)
        self.assertEqual(len(r.val('data.records')), 3)
        r.press('btn3')
        r.press('btn3')                                                         # 1 MENU twice: Data tab
        self.assertEqual(r.screen(), 'data')
        self.assertTrue(r.lines()[0].startswith('Data'))
        self.assertTrue(r.press('btn2'))                                        # 2 ENTER (View)
        ls = r.lines()
        self.assertEqual(len([x for x in ls[1:4] if x.strip()]), 3)             # the list, most recent first
        self.assertTrue(ls[1].strip().endswith(f"{r.val('data.records')[-1]['water_activity']:.4f}"))
        r.press('down', 2)                                                      # 3 DOWN to the oldest
        self.assertTrue(r.press('btn2'))                                        # 4 ENTER: detail
        self.assertEqual(r.screen(), 'data_detail')
        self.assertEqual(r.lines()[4], 'Dew Point  1/3')
        r.press('up')                                                           # scroll in the detail
        self.assertEqual(r.lines()[4], 'Dew Point  2/3')
        self.assertTrue(r.press('btn3'))                                        # 5 OK: back to the list
        self.assertEqual((r.screen(), r.editor()['buffer']['rec']), ('data', 2))
        self.assertTrue(r.press('btn3'))                                        # 6 CANCEL: the Data tab
        self.assertIsNone(r.editor())
        DONE_TASKS.add('view stored readings')

    def test_check_sample_temperature_before_reading(self):
        r = Rig()
        r.load(initial_temperature_k=300.15)                                    # 1 sample in, lid closed, not latched
        self.assertTrue(r.press('right'))                                       # 2 RIGHT: temperature difference screen
        self.assertEqual(r.screen(), 'meas_dt')
        self.assertEqual(r.lines()[2].strip(), f"dT = {r.h.sample_k - r.h.block_k:+.2f}°C")
        r.step(120)
        self.assertEqual(r.lines()[2].strip(), f"dT = {round(r.h.sample_k - r.h.block_k, 2):+.2f}°C")
        self.assertTrue(r.press('left'))
        self.assertEqual(r.screen(), 'meas')
        DONE_TASKS.add('check sample temperature before reading')


# ---------------------------------------------------------------- the other options of the tabs
class Options(unittest.TestCase):
    def test_temp_eq_iso_low_emitting_and_continuous(self):
        r = Rig()
        r.menu()
        r.open('temp_eq')
        r.press('up', 5)
        r.press('btn2')
        self.assertAlmostEqual(r.h.temp_tolerance, 1.0)
        self.assertIn('Temp Eq: <1.0°C', r.lines())
        for iid, mode in (('iso', 'iso18787'), ('continuous', 'continuous'), ('single', 'single')):
            r.open('mode', iid)
            self.assertEqual(r.h.mode, mode)
        r.open('mode', 'low')
        r.press('up', 5)                                                        # trigger 0.0005 -> 0.0010
        r.press('btn2')
        self.assertEqual((r.h.mode, r.h.delta), ('low_emitting', 0.001))
        r.open('mode', 'iso')
        rec = r.load()
        r.call(r.A, 'thermal.set_temperature', temperature_k=round((rec['temperature_k'] - C0) * 10) / 10 + C0)
        r.tab('meas')                                                           # back to the Measurement tab
        r.switch('lever', 'READ')
        r.step(55)
        self.assertEqual(r.h.phase, 'running')                                  # ISO 18787 needs a minute of history
        r.read_to_end()
        self.assertEqual(r.h.phase, 'succeeded')
        h = r.h.history
        self.assertLessEqual(abs(h[-1][1] - h[-7][1]), 0.0003)

    def test_continuous_is_stopped_with_cancel_and_lever(self):
        r, rec = ready()
        r.call(r.A, 'method.configure', **dict(rt.CAPS['method.configure'][6], mode='continuous'))
        r.switch('lever', 'READ')
        r.step(400)
        self.assertEqual(r.h.phase, 'running')
        self.assertEqual(r.keys(), ['CANCEL'])
        self.assertTrue(r.press('btn3'))
        self.assertEqual(r.h.phase, 'cancelled')
        r.switch('lever', 'OPEN')
        r.switch('lever', 'READ')
        r.step(60)
        self.assertTrue(r.switch('lever', 'OPEN'))                               # opening the chamber ends the reading
        self.assertEqual(r.h.phase, 'cancelled')

    def test_date_time_and_regional_formatting(self):
        r = Rig()
        r.menu()
        r.open('date')
        r.press('up', 2)                                                        # Jan -> Mar
        r.press('right')
        r.press('up', 14)                                                       # day 1 -> 15
        r.press('btn2')
        self.assertTrue(r.val('device.datetime').startswith('2026-03-15T08:00'))
        self.assertIn('Date: Mar 15, 2026', r.lines())
        r.open('time')
        r.press('up', 6)                                                        # 8AM -> 2PM
        r.press('right')
        r.press('up', 30)
        r.press('btn2')
        self.assertTrue(r.val('device.datetime').startswith('2026-03-15T14:30'))
        r.open('region', 'temperature_unit')
        r.press('down')
        r.press('btn2')
        r.to_item('time_format')
        r.press('btn2')
        r.press('down')
        r.press('btn2')
        self.assertEqual((r.val('device.settings')['temperature_unit'], r.val('device.settings')['time_format']), ('F', '24h'))
        r.tab('config')
        self.assertTrue(r.lines()[0].endswith('14:30'))                          # 24 h clock on the title
        r.to_item('temp_f')
        self.assertTrue(any(x.startswith('Temperature: 77.0 °F') for x in r.lines()))
        r.to_item('temp_f')
        r.press('btn2')
        r.press('up')
        r.press('btn2')
        self.assertAlmostEqual(r.val('thermal.target_k'), 298.25)               # 0.18 °F = 0.1 °C
        r.call(r.A, 'clock.set_date', year=2026, month=2, day=28)
        r.open('date')
        r.press('right')
        r.press('up', 2)                                                        # 30 February does not exist
        r.press('btn2')
        self.assertEqual(r.lines()[-2].strip(), 'Invalid value')
        self.assertTrue(r.val('device.datetime').startswith('2026-02-28'))

    def test_annotate_beeps_audio_and_print(self):
        r, rec = ready()
        r.menu()
        r.open('beeps')
        r.press('down')
        r.press('btn2')
        r.open('print_mode')
        r.press('down', 2)
        r.press('btn2')
        r.open('autosave')
        r.press('up')
        r.press('btn2')
        self.assertEqual(r.val('device.settings'), dict(rt.SETTINGS_DEFAULT, beeps='continuous', print_mode='detailed', autosave=False))
        r.tab('meas')
        r.switch('lever', 'READ')
        r.read_to_end()
        self.assertEqual(r.val('data.records'), [])                              # autosave off
        r.step(30)
        self.assertTrue(r.val('beeper.on'))                                     # continuous beeps
        self.assertEqual(r.keys(), ['AUDIO', 'ENTER', 'MENU'])
        self.assertTrue(r.press('btn1'))                                        # AUDIO OFF
        self.assertFalse(r.val('beeper.on'))
        self.assertEqual(r.keys(), ['SAVE', 'ENTER', 'MENU'])
        line = r.val('serial.output')[-1].split()
        self.assertEqual((line[0], line[2]), ('01-01-26', rt.SERIAL))           # detailed print: date, time, serial, minutes, °C, aw
        self.assertEqual(line[-1], f"{r.val('water_activity.indicated'):.4f}")
        self.assertTrue(r.press('btn1'))                                        # SAVE: the annotation keyboard
        self.assertEqual(r.screen(), 'annotate')
        self.assertEqual(r.keys(), ['SAVE', 'OK', 'CANCEL'])
        r.type('Salt 7a')
        self.assertEqual(r.editor()['typed'], 'Salt 7a')
        self.assertTrue(r.press('btn1'))                                        # SAVE again
        self.assertEqual(r.screen(), 'meas')
        self.assertEqual([x['name'] for x in r.val('data.records')], ['Salt 7a'])
        r.switch('lever', 'OPEN')
        r.switch('lever', 'READ')                                               # a new test closes the save window
        self.assertIsNone(r.val('data.last_reading'))
        res = r.call(r.A, 'data.save', expect='rejected', name='x')
        self.assertEqual(res['error']['code'], 'NO_READING')

    def test_diagnostics_about_and_data_delete(self):
        r, rec = ready()
        r.switch('lever', 'READ')
        r.read_to_end()
        r.menu()
        r.open('diagnostics')
        ls = r.lines()
        self.assertEqual(ls[0], 'Diagnostics')
        self.assertTrue(ls[3].startswith('Sample:') and ls[3].endswith(f'{round(r.h.sample_k, 2) - C0:.2f}°C'))
        r.press('down')
        self.assertEqual(r.lines()[2].split(), ['Offset:', '+0.0000', 'aw'])
        r.press('btn3')
        r.open('about')
        self.assertEqual(r.lines()[1:4], [f'SN: {rt.SERIAL}', f'Lid SN: {rt.LID_SERIAL}', f'Version: {rt.FIRMWARE}'])
        r.press('btn3')
        r.press('btn3')                                                         # MENU: Data tab
        r.to_item('delete')
        r.press('btn2')
        self.assertEqual(r.lines()[1:5], ["Some readings haven't", 'been backed up.', 'Delete all data from', 'memory?'])
        r.press('btn3')                                                         # CANCEL keeps them
        self.assertEqual(len(r.val('data.records')), 1)
        r.press('btn2')
        r.press('btn2')                                                         # OK deletes all
        self.assertEqual(r.val('data.records'), [])
        self.assertFalse(r.press('btn2'))                                       # View: nothing stored (the line is skipped)

    def test_moisture_models_and_calibration(self):
        r, rec = ready()
        r.call(r.A, 'isotherm.configure', **ISOTHERM)
        r.call(r.A, 'isotherm.select', name=None)
        r.menu()
        r.open('moisture')                                                      # %Moisture: None / salt
        r.press('down')
        r.press('btn2')
        self.assertEqual(r.val('isotherm.selected'), 'salt')
        r.open('calibration', 'mc', 'edit')                                     # %Moisture calibration: Edit the model
        r.press('btn2')                                                         # the model
        self.assertEqual(r.screen(), 'mc_menu')
        r.open('start')
        r.press('btn2')                                                         # OK: reading (lever already READ? no: seal first)
        self.assertEqual(r.lines()[-1].strip(), 'Seal the chamber')
        r.switch('lever', 'READ')
        r.press('btn2')
        r.read_to_end()
        self.assertEqual(r.screen(), 'mc_change')
        computed = r.h._measure()['moisture_dry_basis']
        r.press('up', 150)                                                      # +1.50 %MC to the reference value
        r.press('btn2')
        self.assertAlmostEqual(r.h.models['salt']['offset'], round((round(computed * 100, 2) + 1.5) / 100 - computed, 6), places=9)
        self.assertAlmostEqual(r.h._measure()['moisture_dry_basis'], round(computed * 100 + 1.5, 2) / 100, places=6)
        r.switch('lever', 'OPEN')
        r.tab('config')
        r.open('calibration', 'mc', 'new')                                      # New model from salt
        r.press('btn2')
        r.open('start')
        r.switch('lever', 'READ')
        r.press('btn2')
        r.read_to_end()
        r.press('down', 100)
        r.press('btn2')
        self.assertEqual(r.screen(), 'mc_name')
        r.type('SALT2')
        r.press('btn1')
        self.assertEqual([m['name'] for m in r.val('isotherm.models')], ['salt', 'SALT2'])
        self.assertAlmostEqual(r.h.models['SALT2']['offset'], r.h.models['salt']['offset'] - 0.01, delta=1e-4)   # -1.00 %MC from the shown value
        r.tab('config')
        r.open('calibration', 'mc', 'edit')                                     # Defaults
        r.press('btn2')
        r.open('defaults')
        r.press('btn2')
        self.assertEqual(r.lines()[2:4], [' Factory calibration', '      restored.'])
        self.assertEqual(r.h.models['salt']['offset'], 0.0)
        r.press('btn3')
        r.tab('config')
        r.open('calibration', 'mc', 'delete')
        r.press('down')
        r.press('btn2')
        self.assertEqual(r.lines()[2].strip(), 'SALT2')
        r.press('btn2')
        self.assertEqual([m['name'] for m in r.val('isotherm.models')], ['salt'])

    def test_factory_defaults(self):
        r = Rig()
        r.call(r.A, 'calibration.configure', offset_aw=0.002, source='test')
        r.menu()
        r.open('calibration', 'aw', 'defaults')
        self.assertEqual(r.lines()[2].strip(), 'Reset to defaults?')
        r.press('btn2')
        self.assertEqual(r.val('calibration.offset_aw'), 0.0)
        self.assertEqual(r.keys(), ['OK'])
        r.press('btn3')
        self.assertEqual(r.screen(), 'config')

    def test_admin_users_access_and_login(self):
        r = Rig()
        r.menu()
        r.open('admin')
        r.open('admin_pw')
        r.type('pw1')
        r.press('btn1')
        self.assertTrue(r.val('users.state')['admin_set'])
        r.open('users', 'add')
        r.type('Jean Paul')
        r.press('btn1')                                                         # SAVE the name, then the password
        r.type('jp')
        r.press('btn1')
        self.assertEqual([u['name'] for u in r.val('users.state')['users']], ['Jean Paul'])
        r.open('edit')
        r.press('btn2')
        self.assertEqual((r.screen(), r.lines()[1].strip()), ('user', 'Name: Jean Paul'))
        r.open('access')
        r.press('down')                                                         # Calibration Off (locked)
        r.press('right')
        r.press('down')                                                         # Temperature Off
        r.press('btn2')
        self.assertEqual({k for k, v in r.val('users.state')['users'][0]['access'].items() if v}, {'calibration', 'temperature'})
        r.switch('power', 'O')
        r.switch('power', 'I')
        r.step(4)
        self.assertEqual(r.screen(), 'login')                                   # with users set up: log in after the splash
        r.press('down')                                                         # Jean Paul
        r.press('btn2')
        r.type('xx')
        r.press('btn1')
        self.assertEqual(r.lines()[-2].strip(), 'Wrong password')
        r.press('btn2')
        r.press('down')
        r.press('btn2')
        r.type('jp')
        r.press('btn1')
        self.assertEqual((r.screen(), r.val('users.state')['current']), ('meas', 'Jean Paul'))
        r.menu()
        self.assertEqual(r.item(), 'temp_eq')                                   # Calibration and Temperature are locked: skipped
        r.press('up')
        self.assertEqual(r.item(), 'temp_eq')
        r.open('admin')
        self.assertEqual(r.screen(), 'admin_pw')                                # the administrator password is asked
        r.type('pw1')
        r.press('btn1')
        self.assertEqual((r.screen(), r.val('users.state')['current']), ('config', 'Admin'))
        r.press('btn3')
        r.to_item('calibration')                                                # Admin: everything open


# ---------------------------------------------------------------- panel = direct command, refusals, time-outs
class EquivalenceAndRules(unittest.TestCase):
    def test_settings_equal_direct_commands(self):
        a, b = Rig(), Rig()
        a.menu()
        a.open('temp_c')
        a.press('up', 50)
        a.press('btn2')
        a.open('mode', 'custom')
        a.press('up')
        a.press('btn2')
        a.open('region', 'date_format')
        a.press('down')
        a.press('btn2')
        b.call(b.A, 'thermal.set_temperature', temperature_k=303.15)
        b.call(b.A, 'method.configure', mode='custom', stability_delta=0.0005, temperature_tolerance_k=0.5, consecutive_readings=3)
        b.call(b.A, 'settings.configure', settings=dict(date_format='dd/mm/yy'))
        ra, rb = a.h.read('evaluator'), b.h.read('evaluator')
        self.assertEqual(ra, rb)

    def test_refusals_and_no_menu_time_out(self):
        r = Rig()
        r.call(r.A, 'lid.set_angle', angle_rad=math.pi / 2)
        self.assertFalse(r.switch('lever', 'READ'))                              # lid open: the lever cannot lock
        self.assertEqual(r.val('latch.angle_rad'), 0.0)
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        self.assertFalse(r.switch('lever', 'READ'))                              # sealed but empty: not ready
        self.assertEqual(r.lines()[-1].strip(), 'Not ready')
        self.assertEqual(r.h.phase, 'idle')
        r.step(3)
        r.menu()
        r.open('temp_eq')
        r.step(3600)
        self.assertEqual((r.screen(), r.editor()['field']), ('config', 'temp_eq'))   # no time-out
        r.switch('power', 'O')
        self.assertFalse(r.press('btn3'))
        self.assertEqual(r.screen(), 'off')
        self.assertEqual(int(r.h.panel.render('lcd').max()), 0)                  # unlit
        res = r.call(r.A, 'panel.press', expect='rejected', input_id='lever')
        self.assertEqual(res['error']['code'], 'INVALID_ARGUMENT')
        res = r.call(r.A, 'panel.frame', expect='rejected')
        self.assertEqual(res['error']['code'], 'ACCESS_DENIED')

    def test_panel_started_reading_belongs_to_the_caller(self):
        """Sync audit 2026-10-19: a reading started with the lever is the gesturing agent's (poll / cancel); another agent's CANCEL key and
        direct stop are refused with the state unchanged; the rear power switch is a hard switch (lead clarification 10-19): another agent may
        switch it off and the reading is cancelled as on a power loss."""
        r, rec = ready()
        r.call(r.A, 'method.configure', **dict(rt.CAPS['method.configure'][6], mode='continuous'))
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        self.assertTrue(r.switch('lever', 'READ'))                              # agent A starts the reading on the instrument
        op = r.h.operation_id
        self.assertEqual(r.h._owners, {op: 'agent'})
        r.step(60)
        self.assertEqual(r.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        before = r.h.read('evaluator')

        def b(cap, **a):
            return B.invoke(instance_id=INST, capability_id=cap, arguments=a, request_id=f'b_{cap}_{sorted(a.items())}')
        self.assertEqual(r.keys(), ['CANCEL'])
        self.assertFalse(b('panel.press', input_id='btn3')['result']['accepted'])   # B's CANCEL key
        self.assertIn('Started by other user', [s.strip() for s in r.lines()])  # sweep 2026-10-19: the refusal is shown on the screen
        self.assertEqual(b('acquisition.stop')['error']['code'], 'ACCESS_DENIED')   # the same rule as a direct call
        self.assertEqual(r.h.read('evaluator'), before)                          # state unchanged
        self.assertEqual(r.h.phase, 'running')
        self.assertEqual(r.A.cancel(op, 'a_cancel')['status'], 'cancelled')       # A cancels its own panel-started reading
        self.assertTrue(r.switch('lever', 'OPEN'))
        self.assertTrue(r.switch('lever', 'READ'))
        op2 = r.h.operation_id
        self.assertTrue(b('panel.switch', input_id='lever', position='OPEN')['result']['accepted'])   # B's own lever gesture
        self.assertTrue(b('panel.switch', input_id='lever', position='READ')['result']['accepted'])
        op3 = r.h.operation_id
        self.assertNotEqual(op2, op3)
        self.assertEqual(r.h._owners[op3], 'agent_b')                           # ...starts B's reading
        self.assertEqual(B.poll(op3)['status'], 'running')
        self.assertTrue(r.switch('power', 'O'))                                  # A's power switch: physical (lead clarification 10-19) ...
        self.assertFalse(r.h.powered)
        self.assertNotEqual(B.poll(op3)['status'], 'running')                   # ... B's reading ends as on a power loss

    def test_sample_too_hot_and_low_aw_symbol(self):
        r, rec = ready(initial_temperature_k=304.15)
        r.switch('lever', 'READ')
        r.step(1)
        self.assertEqual(r.screen(), 'warn_hot')
        self.assertEqual(r.lines()[1:3], ['Remove sample. Sample', '     is too hot!'])
        self.assertTrue(r.press('btn2'))                                        # ENTER acknowledges
        self.assertEqual(r.screen(), 'meas')
        r.read_to_end()
        r.h.raw_aw, r.h.indicated_aw = 0.0291, None                             # (no template below 0.03 aw: the display rule)
        self.assertEqual(r.lines()[1].strip(), '<0.0291 aw')


class DeviceCommands(unittest.TestCase):
    """The 1.1.0 device functions as direct commands: results, refusals without side effects."""

    def test_fault_messages_power_cycle_and_service(self):
        """Sync audit 2026-10-19: the four error messages of aq p.58 Table 6 through the evaluator fault.inject."""
        r, rec = ready()
        r.switch('lever', 'READ')
        r.step(30)
        self.assertEqual(r.h.phase, 'running')
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='crystal')['error']['code'], 'ACCESS_DENIED')
        r.call(r.E, 'fault.inject', code='crystal')
        self.assertEqual(r.screen(), 'fault')
        self.assertEqual([x.strip() for x in r.lines()[:4]], ['Error', 'Crystal Failure.', 'See Manual for', 'options.'])
        self.assertEqual(r.h.phase, 'cancelled')                                # the reading cannot go on
        self.assertEqual(r.val('device.fault'), 'crystal')
        for k in ('btn1', 'btn2', 'btn3', 'up', 'down'):
            self.assertFalse(r.press(k))                                        # the keys do nothing
        self.assertEqual(r.call(r.A, 'acquisition.start', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')
        r.switch('power', 'O')                                                  # remedy: cycle the power
        r.switch('power', 'I')
        r.step(4)
        self.assertIsNone(r.val('device.fault'))
        self.assertEqual(r.screen(), 'meas')
        r.call(r.E, 'fault.inject', code='crystal', persistent=True)            # it keeps appearing: service
        r.switch('power', 'O')
        r.switch('power', 'I')
        r.step(4)
        self.assertEqual(r.screen(), 'fault')
        r.call(r.E, 'fault.clear')
        self.assertEqual(r.screen(), 'meas')
        for code, first in (('dew_point_sensor', 'Dew point sensor'), ('contaminated_mirror', 'Contaminated mirror.'), ('firmware', 'Firmware is')):
            r.call(r.E, 'fault.inject', code=code)
            self.assertEqual(r.lines()[1].strip(), first)
            r.switch('power', 'O')
            r.switch('power', 'I')
            r.step(4)
            self.assertEqual((r.val('device.fault'), r.screen()), (code, 'fault'))   # a restart does not clear it
            r.call(r.E, 'fault.clear')                                          # service / mirror cleaning
            self.assertEqual(r.screen(), 'meas')
        r.switch('lever', 'OPEN')
        self.assertTrue(r.switch('lever', 'READ'))
        self.assertEqual(r.h.phase, 'running')                                  # readings work again

    def test_every_device_command(self):
        r = Rig()
        h = r.h
        for cap, (_, out, *_rest) in rt.DEVICE_CAPS.items():
            self.assertEqual(pu.check_arguments(rt.DEVICE_CAPS[cap][0], rt.DEVICE_CAPS[cap][6]), [], cap)
        r.call(r.A, 'settings.configure', settings=dict(beeps='none'))
        r.call(r.A, 'clock.set_date', year=2027, month=5, day=5)
        r.call(r.A, 'clock.set_time', hour=23, minute=59)
        self.assertEqual(r.val('device.datetime')[:16], '2027-05-05T23:59')
        r.call(r.A, 'isotherm.configure', **ISOTHERM)
        r.call(r.A, 'isotherm.copy', source='salt', name='salt B', offset_dry_basis=0.01)
        r.call(r.A, 'isotherm.set_offset', name='salt B', offset_dry_basis=0.02)
        r.call(r.A, 'isotherm.select', name='salt B')
        r.call(r.A, 'isotherm.delete', name='salt B')
        self.assertIsNone(r.val('isotherm.selected'))
        r.call(r.A, 'users.set_admin_password', password='a')
        r.call(r.A, 'users.add', name='U1', password='u')
        r.call(r.A, 'users.set_password', name='U1', password='v')
        r.call(r.A, 'users.set_access', name='U1', access={k: True for k in rt.ACCESS})
        r.call(r.A, 'users.login', name='U1', password='v')
        r.call(r.A, 'users.logout')
        r.call(r.A, 'users.delete', name='U1')
        r.call(r.A, 'beeper.silence')
        r.call(r.A, 'data.delete_all')
        before = copy.deepcopy(h.__dict__)
        for cap, args, code in (('clock.set_date', dict(year=2026, month=2, day=30), 'INVALID_ARGUMENT'), ('data.save', dict(name='x'), 'NO_READING'),
                                ('isotherm.select', dict(name='nope'), 'MODEL_NOT_FOUND'), ('isotherm.copy', dict(source='salt', name='salt', offset_dry_basis=0.0), 'NAME_EXISTS'),
                                ('users.add', dict(name='Admin', password='x'), 'NAME_EXISTS'), ('users.login', dict(name='Admin', password='bad'), 'WRONG_PASSWORD'),
                                ('users.delete', dict(name='nobody'), 'USER_NOT_FOUND'), ('method.configure', dict(mode='iso18787', stability_delta=0.0005,
                                                                                                                   temperature_tolerance_k=0.5, consecutive_readings=1), 'INVALID_ARGUMENT')):
            res = r.call(r.A, cap, expect='rejected', **args)
            self.assertEqual(res['error']['code'], code, cap)
        self.assertEqual({k: v for k, v in h.__dict__.items() if k != 'revision'}, {k: v for k, v in before.items() if k != 'revision'})
        h2 = rt.Aqualab4teRuntime('x')
        with self.assertRaises(rt.ActionRejected) as e:
            h2.invoke('users.add', name='a', password='b')
        self.assertEqual(str(e.exception), 'ADMIN_REQUIRED')
        rr, rec = ready()
        rr.switch('lever', 'READ')
        rr.step(30)
        res = rr.call(rr.A, 'settings.configure', expect='rejected', settings=dict(beeps='none'))
        self.assertEqual(res['error']['code'], 'BUSY')                          # settings not during a reading
        rr.read_to_end()
        rr.call(rr.A, 'data.save', name='A1')
        self.assertEqual([x['name'] for x in rr.val('data.records')], ['A1'])   # the autosaved record renamed (not a second record)

    def test_settings_survive_a_power_cycle(self):
        r = Rig()
        r.call(r.A, 'settings.configure', settings=dict(temperature_unit='F'))
        r.switch('power', 'O')
        r.switch('power', 'I')
        self.assertEqual(r.val('device.settings')['temperature_unit'], 'F')


# ---------------------------------------------------------------- rendering, capture, contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g, _ = ready()
            g.call(g.A, 'isotherm.configure', **ISOTHERM)
            g.switch('lever', 'READ')
            g.read_to_end()
            return g
        a, b = scenario(), scenario()
        ia, ib = a.h.panel.render('lcd'), b.h.panel.render('lcd')
        self.assertEqual((ia.shape, ia.dtype.name), ((64, 128, 3), 'uint8'))
        self.assertTrue((ia == ib).all())
        self.assertTrue((ia == a.h.panel.render('lcd')).all())
        self.assertEqual(a.h.panel.png_bytes('lcd'), b.h.panel.png_bytes('lcd'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = dict(home=a.h.panel.save_png('lcd', OUT / 'home.png'))
        a.press('btn3')
        a.to_item('temp_eq')
        shots['menu'] = a.h.panel.save_png('lcd', OUT / 'menu.png')
        a.open('mode')
        shots['mode'] = a.h.panel.save_png('lcd', OUT / 'mode.png')
        a.press('btn3')
        a.press('btn3')
        a.press('btn2')
        shots['data'] = a.h.panel.save_png('lcd', OUT / 'data.png')
        a.press('btn3')
        a.press('btn3')
        a.switch('lever', 'OPEN')
        a.switch('lever', 'READ')
        a.press('btn3')                                                         # CANCEL the new reading
        a.switch('lever', 'OPEN')
        a.call(a.A, 'lid.set_angle', angle_rad=math.pi / 2)
        a.call(a.E, 'sample.unload')
        a.call(a.A, 'lid.set_angle', angle_rad=0.0)
        a.switch('lever', 'OPEN')
        a.switch('lever', 'READ')                                               # empty chamber: Not ready
        shots['error'] = a.h.panel.save_png('lcd', OUT / 'error.png')
        self.assertEqual(a.lines()[-1].strip(), 'Not ready')
        a.step(3)
        a.press('btn3')
        a.open('admin', 'admin_pw')
        shots['keyboard'] = a.h.panel.save_png('lcd', OUT / 'keyboard.png')
        a.call(a.E, 'fault.inject', code='crystal')                             # a fault message (aq p.58 Table 6)
        shots['fault'] = a.h.panel.save_png('lcd', OUT / 'fault.png')
        self.assertEqual(a.screen(), 'fault')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
            self.assertEqual((s['width'], s['height']), (128, 64))
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(power=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.switch('power', 'I')
        r2 = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        self.assertEqual(g.call(g.A, 'panel.capture', display_id='lcd')['image'], r2['image'])

    def test_spec_and_contract(self):
        spec = rt.PANEL_SPEC
        self.assertEqual(pu.check_spec(spec, set(rt.CAPS) | set(rt.DEVICE_CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.switch', 'panel.capture', 'panel.frame'})
        for sid, s in spec['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        self.assertNotIn(str(Path.home()), (rt.ASSET_DIR / 'panel_ui.json').read_text())
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        meta, pmeta = rt.export_aqualab_4te_contract_metadata(), rt.export_panel_contract_metadata()
        for k in rt.PANEL_CAPS:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'errors', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], pmeta[k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for k in list(rt.CAPS) + list(rt.DEVICE_CAPS):
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], meta['capabilities'][k][key], (k, key))
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET_DIR / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/aqualab_4te.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec_ = importlib.util.spec_from_file_location('aqualab_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        self.assertEqual(sorted(set(SURVEY_TASKS) - DONE_TASKS), [])


if __name__ == '__main__':
    unittest.main()
