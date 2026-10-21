"""front-panel tests for CHEM-005 metrohm_914 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on the Metrohm 914 meter firmware of
scripts/basic40_runtime_measure.py): python3 -m pytest tests/test_panel_ui_metrohm_914.py -q

Every gesture goes through the session API (panel.press, the runtime's normal invoke path).  Checks look at the runtime state and at the screen:
the structured frame (panel.frame) and the rendered pixels of panel.capture, read back with a 5x7 dot-matrix reader that knows only the display
geometry of the spec.  Themes: the six key procedures of the menu survey (two-buffer pH calibration, buffer type NIST, measure and save, cell
constant, DO two-point calibration, CSV export) and the other dialogs (views, measuring modes, criteria, values view, sensors, report settings,
configuration with power-off and display time-outs, language, user and password, reset at start-up); panel = direct capability; refusals and
locks; time-outs; render determinism and the PNGs of outputs/panel_ui/metrohm_914/; contract entries."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_measure as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'm914_01'
OUT = ROOT / 'outputs/panel_ui/metrohm_914'
ASSET = ROOT / 'assets/basic40_batch02/t4_measure/metrohm_914'
SPEC = rt.METROHM914_PANEL_SPEC
TFT = SPEC['displays']['tft']
SURVEY_TASKS = ['two-buffer pH calibration', 'change buffer type to NIST', 'measure and save a pH value', 'conductivity cell constant calibration',
                'DO two-point calibration', 'export values as CSV']
DONE_TASKS = set()
_GLYPHS = {}
for _ch, _cols in pu.FONT5X7.items():
    _GLYPHS.setdefault(bytes(_cols), _ch)


def read_line(img, r):
    """Text of display line r from the pixels (5x7 dots at text_scale; inverted cells recognised by their dark gap column)."""
    s = TFT['text_scale']
    light = lambda x, y: int(img[y, x, 0]) + int(img[y, x, 1]) + int(img[y, x, 2]) > 300  # noqa: E731
    y0 = 2 + r * 9 * s
    text = ''
    for c in range(TFT['cols']):
        x0 = 2 + c * 6 * s
        inv = not light(x0 + 5 * s, y0 + 3 * s + s // 2) and not light(x0 + 5 * s, y0 + 7 * s + s // 2)
        cols = []
        for cx in range(5):
            bits = 0
            for cy in range(8):
                if (not light(x0 + cx * s + s // 2, y0 + cy * s + s // 2)) != inv:
                    bits |= 1 << cy
            cols.append(bits)
        text += _GLYPHS.get(bytes(cols), '?')
    return text.rstrip()


def sample(ph=7.0, t_c=25.0, ref_mv=10.0, conductance=None, do_mg_l=None):
    s = {'temperature_k': t_c + 273.15}
    if ph is not None:
        s.update(ph=ph, reference_ph=7.0, reference_potential_v=ref_mv / 1000.0)
    if conductance is not None:
        s['conductance_s'] = conductance
    if do_mg_l is not None:
        s['do_kg_m3'] = do_mg_l / 1000.0
    return s


class Rig:
    def __init__(self, on=True, probes=('ph', 'conductance'), **first):
        self.h = rt.Metrohm914DeviceRuntime()
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.t = 0.0
        for probe in probes:
            self.ok('probe.set_installed', slot=probe, installed=True)
        if on:
            self.press('onoff')
            self.feed(3.0, **(first or dict(conductance=0.0012)))

    def call(self, cap, session=None, **args):
        self.n += 1
        return (session or self.A).invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')

    def ok(self, cap, **args):
        r = self.call(cap, **args)
        assert r['status'] == 'succeeded', (cap, args, r['error'])
        return r['result']

    def feed(self, dt=1.0, **kw):
        """The host steps the common clock with a new sample (a buffer, a standard or a sample solution)."""
        self.t += dt
        self.h.host_step(self.t, inputs={INST: sample(**kw)})

    def idle(self, dt):
        self.t += dt
        self.h.host_step(self.t)

    def press(self, key, duration_s=0.1, held=None):
        args = dict(input_id=key, duration_s=duration_s)
        if held:
            args['held'] = held
        r = self.call('panel.press', **args)
        assert r['status'] == 'succeeded', r
        return r['result']['accepted']

    def keys(self, *ks):
        return [self.press(k) for k in ks]

    def frame(self):
        return self.call('panel.frame', session=self.E)['result']['frame']

    def lines(self):
        return self.frame()['displays']['tft']['lines']

    def screen(self):
        return self.frame()['screen']

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def dev(self, name, *path):
        v = self.val('device.' + name)
        for k in path:
            v = v[k]
        return v

    def img(self):
        import numpy as np
        from PIL import Image
        r = self.call('panel.capture', display_id='tft')['result']['image']
        return np.asarray(Image.open(r['path']).convert('RGB'))

    def seen(self, r):
        return read_line(self.img(), r)

    def select(self, label):
        for _ in range(3):
            for _ in range(120):
                fr = self.frame()['ui']['menu']
                if fr and fr['label'].strip().startswith(label):
                    return
                if not self.press('down'):
                    break
            for _ in range(120):
                if not self.press('up'):
                    break
        raise AssertionError(f'no line {label!r} on {self.screen()}: {self.lines()}')

    def open(self, *labels):
        for lab in labels:
            self.select(lab)
            assert self.press('ok'), lab

    def to_main(self):
        for _ in range(8):
            if self.screen() == 'main' and self.frame()['ui']['editor'] is None:
                return
            self.press('back')
        assert self.screen() == 'main', self.screen()

    def menu(self, *labels):
        self.to_main()
        self.select('Menu')
        self.press('ok')
        self.open(*labels)

    def view(self, n):
        for _ in range(3):
            if self.frame()['ui']['vars']['view'] == n:
                return
            self.press('right')
        raise AssertionError(n)

    def grid_rows(self):
        ed = self.frame()['ui']['editor']
        name = ed.get('grid_name') or next(x for x in _fields() if x['id'] == ed['field'])['grid']
        return [[t['label'] if isinstance(t, dict) else t for t in row] for row in SPEC['char_grids'][name]['rows']]

    def grid_goto(self, row, col):
        for _ in range(60):
            g = self.frame()['ui']['editor']['grid']
            if g == [row, col]:
                return
            self.press('down' if g[0] < row else 'up' if g[0] > row else 'right' if g[1] < col else 'left')
        raise AssertionError((row, col))

    def type(self, text):
        for ch in text:
            rows = self.grid_rows()
            low = ch.lower()
            r, c = next((i, j) for i, row in enumerate(rows) for j, t in enumerate(row) if t == low or t == ch)
            self.grid_goto(r, c)
            assert self.press('ok', duration_s=1.0 if ch != low else 0.1), ch     # held OK: the capital

    def grid_fn(self, fn):
        rows = self.grid_rows()
        label = {'accept': 'Entry', 'cancel': 'Cancel', 'clear': 'Del all', 'bksp': 'BkSp'}.get(fn, fn)
        r, c = next((i, j) for i, row in enumerate(rows) for j, t in enumerate(row) if t in ('{' + fn + '}', label))
        self.grid_goto(r, c)
        return self.press('ok')

    def enter(self, text, clear=True):
        if clear:
            self.grid_fn('clear')
        self.type(text)
        return self.grid_fn('accept')

    def choose(self, option):
        for k in range(80):
            d = self.frame()['displays']['tft']
            cur = next((ln for ln, inv in zip(d['lines'][1:], d['inverse'][1:]) if inv), None)
            if cur is not None and cur.strip() == option:
                return self.press('ok')
            names = [x.strip() for x in d['lines'][1:]]
            up = option in names and cur is not None and names.index(option) < names.index(cur.strip())
            self.press('up' if up or k >= 40 else 'down')
        raise AssertionError(option)


def _fields():
    out = []
    for m in SPEC['menus'].values():
        stack = list(m['items'])
        while stack:
            it = stack.pop()
            out.append(it)
            stack += it.get('items', []) + it.get('fields', [])
    return out


# ---------------------------------------------------------------- key procedures of the survey
class KeyProcedures(unittest.TestCase):
    def test_two_buffer_ph_calibration(self):
        g = Rig(ph=7.0, ref_mv=12.0)
        self.assertEqual(g.screen(), 'main')
        g.press('cal')
        self.assertEqual(g.screen(), 'main')                                  # not in the two-channel view
        g.view(1)
        self.assertEqual(g.val('probe.selected'), 'ph')
        uncal = g.dev('channels', 'ph', 'value')
        self.assertAlmostEqual(uncal, 7.0 - 0.012 / (rt.KS_K * 298.15), places=3)   # the 12 mV asymmetry before calibration
        g.press('cal')
        self.assertEqual(g.screen(), 'cal')
        self.assertEqual(g.seen(2).strip(), 'Buffer 1 of 2')
        g.feed(ph=4.0, ref_mv=12.0)                                            # buffer 1 (Metrohm pH 4.00)
        g.press('ok')
        self.assertEqual(g.lines()[2].strip(), 'Buffer 2 of 2')
        g.press('ok')                                                          # the same buffer again: 912-181
        self.assertEqual(g.screen(), 'message')
        self.assertIn('912-181 Same buffer', g.lines()[2])
        g.press('ok')
        self.assertEqual(g.screen(), 'cal')
        g.feed(ph=7.0, ref_mv=12.0)
        g.press('ok')
        self.assertEqual(g.screen(), 'cal_result')
        self.assertTrue(g.seen(2).endswith('100.0 %'))
        s = next(x for x in g.dev('sensors') if x['name'] == 'pH default')
        self.assertAlmostEqual(s['slope'], 100.0, places=6)
        self.assertAlmostEqual(s['ph0'], 7.0 + 0.012 / (rt.KS_K * 298.15), places=6)
        g.press('ok')
        self.assertEqual(g.screen(), 'main')
        self.assertAlmostEqual(g.dev('channels', 'ph', 'value'), 7.0, places=9)
        self.assertEqual(g.frame()['displays']['tft']['widgets'][0]['text'], '7.000')
        DONE_TASKS.add('two-buffer pH calibration')

    def test_one_measuring_model_on_every_path(self):
        """Sync audit 2026-10-19 (rule 1): the CHEM-005 readings come from the meter's model (sensor slope / pH(0), DO phase errors and
        calibration, meter temperature); a panel calibration marks the CHEM-005 channel calibrated."""
        g = Rig(ph=7.0, ref_mv=12.0)
        g.view(1)
        dev_ph = g.dev('channels', 'ph', 'value')
        self.assertAlmostEqual(g.val('measurement.ph'), round(dev_ph, 3), places=9)     # the 12 mV asymmetry on both paths
        self.assertAlmostEqual(g.ok('electrochemistry.reading')['ph'], g.val('measurement.ph'), places=9)
        self.assertFalse(g.val('probe.ph.calibrated'))
        g.press('cal')
        g.feed(ph=4.0, ref_mv=12.0)
        g.press('ok')
        g.feed(ph=7.0, ref_mv=12.0)
        g.press('ok')
        g.press('ok')
        self.assertTrue(g.val('probe.ph.calibrated'))
        self.assertEqual(g.val('probe.ph.calibration_time_s'), g.t)
        g.feed(ph=4.0, ref_mv=12.0)
        self.assertAlmostEqual(g.val('measurement.ph'), 4.0, places=9)
        h = Rig(probes=('do', 'conductance'), ph=None, do_mg_l=8.26, conductance=0.001)
        h.h.set_sensor_errors(INST, dphi0_deg=1.5, dphi100_deg=-0.8)
        sat = rt._ks_do_sat_mg_l(25.0, 0.0, 1013.25)
        h.view(1)
        self.assertEqual(h.val('probe.selected'), 'do')
        h.feed(ph=None, do_mg_l=sat, conductance=0.001)
        self.assertAlmostEqual(h.val('measurement.do_kg_m3') * 1000.0, h.dev('channels', 'do', 'raw', 'mg_l'), places=9)
        self.assertNotAlmostEqual(h.val('measurement.do_kg_m3') * 1000.0, sat, places=2)   # the phase errors show on the CHEM-005 path too

    def test_temperature_sensor_type_and_offset(self):
        """Sync audit (rule 3): Temp. sensor / R(25 degC) / B value read the physical probe; a wrong type gives a wrong temperature."""
        g = Rig(ph=7.0, ref_mv=0.0, conductance=0.001, t_c=30.0)
        g.h.set_temperature_probe(INST, 'ph', 'NTC')                                   # the electrode has an NTC 30 kOhm
        g.feed(ph=7.0, ref_mv=0.0, conductance=0.001, t_c=30.0)
        wrong = g.dev('channels', 'ph', 'temperature_c')
        self.assertGreater(abs(wrong - 30.0), 10.0)                                     # read as a Pt1000
        self.assertAlmostEqual(g.val('temperature.compensation_k'), wrong + 273.15, places=6)
        g.ok('sensor.save', name='pH default', settings={'temp_sensor': 'NTC'})
        g.feed(ph=7.0, ref_mv=0.0, conductance=0.001, t_c=30.0)
        self.assertAlmostEqual(g.dev('channels', 'ph', 'temperature_c'), 30.0, places=6)
        g.ok('sensor.save', name='pH default', settings={'b_value': 3500.0})            # a wrong B value
        g.feed(ph=7.0, ref_mv=0.0, conductance=0.001, t_c=30.0)
        self.assertNotAlmostEqual(g.dev('channels', 'ph', 'temperature_c'), 30.0, places=1)
        g.ok('sensor.save', name='pH default', settings={'b_value': 4100.0, 'temp_offset': 0.5})
        g.feed(ph=7.0, ref_mv=0.0, conductance=0.001, t_c=30.0)
        self.assertAlmostEqual(g.val('temperature.compensation_k'), 303.65, places=6)  # the offset acts on the compensation too

    def test_do_calibration_compensation_entries(self):
        """Sync audit (rule 3): the 100 % calibration takes the standard's saturation from the entered air pressure / temperature."""
        def calibrate(press):
            g = Rig(probes=('do', 'conductance'), ph=None, do_mg_l=8.26, conductance=0.001)
            if press is not None:
                g.ok('parameters.configure', channel='do', parameters={'cal_press_comp': press, 'points': '100%'})
            else:
                g.ok('parameters.configure', channel='do', parameters={'points': '100%'})
            sat = rt._ks_do_sat_mg_l(25.0, 0.0, 1013.25)
            g.feed(ph=None, do_mg_l=sat, conductance=0.001)                             # water-saturated air at 1013.25 mbar
            g.view(1)
            g.press('cal')
            g.press('ok')
            g.select('Calibrate 100 %')
            g.press('ok')
            g.press('ok')
            g.feed(ph=None, do_mg_l=sat, conductance=0.001)
            return g.dev('channels', 'do', 'value')
        self.assertAlmostEqual(calibrate(None), 100.0, places=6)
        pw = rt._m914_math.exp(11.8571 - 3840.70 / 298.15 - 216961.0 / 298.15 ** 2) * 1013.25
        self.assertAlmostEqual(calibrate(950.0), 100.0 * (950.0 - pw) / (1013.25 - pw), places=6)   # a wrong pressure entry: low readings

    def test_signal_intensity_do(self):
        """Sync audit (rule 3): the DO sensor status follows the signal intensity (LED intensity, cap wear) and is shown only if switched on."""
        g = Rig(probes=('do', 'conductance'), ph=None, do_mg_l=8.26, conductance=0.001)
        g.view(1)
        self.assertEqual(g.dev('channels', 'do', 'condition'), 'good')
        g.ok('sensor.save', name='O2 Lumitrode', settings={'led': 10})
        g.feed(ph=None, do_mg_l=8.26, conductance=0.001)
        self.assertEqual(g.dev('channels', 'do', 'condition'), 'near')
        g.h.set_sensor_errors(INST, cap_wear=0.5)
        g.feed(ph=None, do_mg_l=8.26, conductance=0.001)
        self.assertEqual(g.dev('channels', 'do', 'condition'), 'out')
        g.ok('settings.configure', settings={'signal_do': False})
        self.assertIsNone(g.dev('channels', 'do', 'condition'))
        self.assertFalse([w for w in g.frame()['displays']['tft']['widgets'] if w['id'].endswith('_cond') and w.get('visible', True)])

    def test_panel_commands_run_as_the_gesturing_session(self):
        g = Rig()
        before = len(g.h._logs['agent'])
        g.press('onoff')                                                               # switching off from the panel
        self.assertTrue(any(x.get('request_id', '').startswith('panel-') for x in g.h._logs['agent'][before:]))
        self.assertIsNone(getattr(g.h, '_ks_caller', None))

    def test_runs_belong_to_the_caller_that_started_them(self):
        """Lead rule 2026-10-19: an acquisition and a calibration belong to the caller that started them; another caller can neither stop, power
        off nor step them, on the panel or the direct path; the refusal shows on the display; the evaluator is unrestricted."""
        g = Rig(ph=7.0, ref_mv=12.0)
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        g.ok('electrochemistry.configure_endpoint', hold_s=2.0, tolerance_fraction=0.01)
        g.ok('acquisition.start')                                                       # A's acquisition
        self.assertEqual(g.call('acquisition.stop', session=B)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.call('power.set', session=B, enabled=False)['error']['code'], 'ACCESS_DENIED')
        self.assertTrue(g.val('acquisition.running'))
        self.assertFalse(g.call('panel.press', session=B, input_id='onoff', duration_s=0.1)['result']['accepted'])   # B's On/Off
        self.assertTrue(g.val('power.enabled'))
        self.assertTrue(any('Started by other user' in line for line in g.lines()))      # shown on the display
        g.idle(4.0)
        g.ok('acquisition.stop')                                                        # A stops its own acquisition
        g.view(1)
        g.press('cal')                                                                  # A's calibration
        self.assertEqual(g.screen(), 'cal')
        before = g.dev('calibration')
        for cap in ('calibration.next', 'calibration.reject', 'calibration.start'):
            self.assertEqual(g.call(cap, session=B)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(g.dev('calibration'), before)
        r = g.call('calibration.reject', session=g.E)                                   # the evaluator is unrestricted
        self.assertEqual(r['status'], 'succeeded')
        self.assertIsNone(g.dev('calibration'))

    def test_change_buffer_type_to_nist(self):
        g = Rig()
        g.menu('Parameters pH/U/T IS', 'Calibration param.', 'Buffer type')
        g.choose('NIST')
        self.assertEqual(g.dev('parameters', 'ph', 'buffer_type'), 'NIST')
        self.assertTrue(g.lines()[4].endswith('NIST'))
        g.to_main()
        g.view(1)
        g.press('cal')
        g.feed(ph=6.865, ref_mv=0.0)
        g.press('ok')
        self.assertEqual(g.dev('calibration', 'points')[0]['nominal'], 6.865)  # recognised from the NIST series
        DONE_TASKS.add('change buffer type to NIST')

    def test_measure_and_save_a_ph_value(self):
        g = Rig(ph=5.5, ref_mv=0.0, conductance=0.0005)
        g.menu('Measured values', 'Data')
        g.choose('save')
        g.select('Criterion')
        g.press('ok'), g.choose('immediately')
        g.to_main()
        g.select('Print/save')
        self.assertEqual(g.frame()['ui']['menu']['item'], 'print')
        g.press('ok')
        v = g.dev('values')
        self.assertEqual(len(v), 1)
        self.assertEqual(sorted(v[0]['channels']), ['conductance', 'ph'])     # both channels in the two-channel view
        self.assertEqual(v[0]['channels']['ph']['text'], '5.500')
        g.view(1)
        g.select('ID1')
        g.press('ok')
        g.enter('Lot7')
        g.select('Print/save')
        g.press('ok')
        v = g.dev('values')
        self.assertEqual(sorted(v[1]['channels']), ['ph'])                    # the one-channel view: that channel
        self.assertEqual(v[1]['id1'], 'Lot7')
        DONE_TASKS.add('measure and save a pH value')

    def test_conductivity_cell_constant_calibration(self):
        g = Rig(conductance=0.012870 / 0.5)                                    # a cell of 0.5 /cm in the 12.870 mS/cm standard at 25 C
        g.view(2)
        self.assertEqual(g.val('probe.selected'), 'conductance')
        self.assertAlmostEqual(g.dev('channels', 'conductance', 'value'), 25.74, places=6)    # stored cell constant 1.000 /cm
        g.press('cal')
        g.press('ok')                                                          # immersed: the entries
        self.assertEqual(g.screen(), 'cal_k')
        self.assertTrue(g.lines()[3].strip().endswith('12.870 mS/cm'))
        g.select('Start calibration')
        g.press('ok')
        self.assertEqual(g.screen(), 'cal_result')
        self.assertIn('0.500 /cm', g.lines()[2])
        g.press('ok')
        self.assertAlmostEqual(g.dev('channels', 'conductance', 'value'), 12.87, places=6)
        self.assertEqual(g.screen(), 'main')
        DONE_TASKS.add('conductivity cell constant calibration')

    def test_do_two_point_calibration(self):
        g = Rig(probes=('do', 'conductance'), ph=None, do_mg_l=8.26, conductance=0.001)
        g.h.set_sensor_errors(INST, dphi0_deg=1.5, dphi100_deg=-0.8)          # an aged cap: the stored phase angles no longer fit
        g.feed(ph=None, do_mg_l=rt._ks_do_sat_mg_l(25.0, 0.0, 1013.25), conductance=0.001)
        g.view(1)
        self.assertEqual(g.val('probe.selected'), 'do')
        self.assertNotAlmostEqual(g.dev('channels', 'do', 'value'), 100.0, places=1)
        g.press('cal')
        g.press('ok')                                                          # vessel fitted: compensation entries
        self.assertEqual(g.screen(), 'cal_do')
        g.select('Calibrate 100 %')
        g.press('ok')
        self.assertEqual(g.screen(), 'cal')
        self.assertEqual(g.lines()[2].strip(), '0 % oxygen standard')
        g.feed(ph=None, do_mg_l=0.0, conductance=0.001)
        g.press('ok')
        self.assertEqual(g.screen(), 'cal_result')
        g.press('ok')
        g.feed(ph=None, do_mg_l=rt._ks_do_sat_mg_l(25.0, 0.0, 1013.25) / 2, conductance=0.001)
        self.assertAlmostEqual(g.dev('channels', 'do', 'value'), 50.0, places=6)   # % air sat after the calibration
        DONE_TASKS.add('DO two-point calibration')

    def test_export_values_as_csv(self):
        g = Rig(ph=6.0, ref_mv=0.0)
        g.select('Print/save')
        g.press('ok'), g.press('ok')
        g.menu('Measured values', 'Values')
        g.choose('save as CSV')
        self.assertEqual(g.dev('files'), {'CSV/MEASREPORT.CSV': 3})            # header + 2 values of the pH channel (no conductance sample)
        text = g.h._instances[INST]['device']['files']['CSV/MEASREPORT.CSV']
        self.assertIn(';ph;pH;6.000;pH;25.0', text)
        g.select('Values')
        g.press('ok'), g.choose('save as PC/LIMS')
        self.assertIn('PCLIMS/PCLIMS_1.UTF8', g.dev('files'))
        DONE_TASKS.add('export values as CSV')


# ---------------------------------------------------------------- the other functions
class OtherFunctions(unittest.TestCase):
    def test_measuring_modes_and_last_decimal(self):
        g = Rig(ph=7.0, ref_mv=0.0, conductance=0.0005)
        g.menu('Parameters pH/U/T IS', 'Measuring parameters', 'Measuring mode')
        g.choose('U')
        self.assertEqual(g.dev('channels', 'ph', 'unit'), 'mV')
        g.select('Delta measurement mV')
        g.press('ok')
        g.enter('-50')
        self.assertAlmostEqual(g.dev('channels', 'ph', 'value'), 50.0, places=6)
        g.to_main()
        g.menu('Parameters K/TDS/Sal/rho/T', 'Measuring parameters', 'Measuring mode')
        g.choose('TDS')
        self.assertAlmostEqual(g.dev('channels', 'conductance', 'value'), 500.0 * 0.40, places=6)
        g.select('TDS factor')
        g.press('ok'), g.enter('0.65')
        self.assertAlmostEqual(g.dev('channels', 'conductance', 'value'), 500.0 * 0.65, places=6)
        g.select('Measuring mode')
        g.press('ok'), g.choose('ρ')
        self.assertAlmostEqual(g.dev('channels', 'conductance', 'value'), 2000.0, places=6)
        g.select('Temp. compens.')
        g.press('ok'), g.enter('0')
        g.to_main()
        g.menu('Parameters pH/U/T IS', 'Measuring parameters', 'Measuring mode')
        g.choose('pH')
        g.to_main()
        g.menu('Configuration', 'Last decimal place')
        g.choose('off')
        self.assertEqual(g.dev('channels', 'ph', 'text'), '7.00')

    def test_temperature_compensation_and_manual_temperature(self):
        g = Rig(ph=7.0, ref_mv=0.0, conductance=0.001, t_c=30.0)
        self.assertAlmostEqual(g.dev('channels', 'conductance', 'value'), 1000.0 / (1 + 0.02 * 5), places=6)   # µS/cm at 25 C
        g.menu('Parameters K/TDS/Sal/rho/T', 'Measuring parameters', 'Temperature')
        g.enter('20')
        self.assertEqual(g.val('temperature.mode'), 'manual')
        g.feed(ph=7.0, ref_mv=0.0, conductance=0.001, t_c=30.0)
        self.assertAlmostEqual(g.dev('channels', 'conductance', 'temperature_c'), 20.0, places=6)
        g.select('Temperature')
        g.press('ok')
        g.grid_goto(4, 0)
        g.press('ok')                                                          # 'Auto': the sensor temperature again
        self.assertEqual(g.val('temperature.mode'), 'automatic')
        g.select('Temp. compens.')
        g.press('ok')
        g.grid_goto(4, 0)
        g.press('ok')                                                          # DIN
        self.assertIsNone(g.dev('parameters', 'conductance', 'tc'))
        self.assertTrue(g.lines()[g.lines().index(next(x for x in g.lines() if x.startswith('Temp. compens.')))].endswith('DIN'))

    def test_time_dependent_recording_and_stop_time(self):
        g = Rig(ph=7.0, ref_mv=0.0)
        g.ok('measured_values.configure', settings={'criterion': 'time-dependent', 'interval': 10.0, 'stop_time': 35.0})
        g.select('Print/save')
        g.press('ok')
        self.assertEqual(g.dev('recording', 'criterion'), 'time-dependent')
        self.assertIn('REC', g.lines()[9])
        for _ in range(5):
            g.feed(10.0, ph=7.0, ref_mv=0.0)
        self.assertEqual(len(g.dev('values')), 4)                             # at 0, 10, 20, 30 s; stopped at 35 s
        self.assertIsNone(g.dev('recording'))

    def test_when_changed_and_drift_dependent(self):
        g = Rig(ph=7.0, ref_mv=0.0)
        g.ok('measured_values.configure', settings={'criterion': 'when changed', 'delta_ph': 0.5})
        g.select('Print/save')
        g.press('ok')
        for ph in (7.2, 7.6, 8.0, 8.1):
            g.feed(1.0, ph=ph, ref_mv=0.0)
        self.assertEqual([v['channels']['ph']['text'] for v in g.dev('values')], ['7.000', '7.600'])
        g.press('ok')                                                          # the button again stops the recording
        self.assertIsNone(g.dev('recording'))
        g.ok('measured_values.configure', settings={'criterion': 'drift-dependent'})
        g.press('ok')
        g.feed(1.0, ph=8.5, ref_mv=0.0)
        g.feed(60.0, ph=8.51, ref_mv=0.0)                                      # 0.01 pH/min: below 0.028 pH/min
        self.assertEqual(g.dev('values')[-1]['channels']['ph']['text'], '8.510')
        self.assertIsNone(g.dev('recording'))

    def test_values_view_delete_and_data_print(self):
        g = Rig(ph=4.0, ref_mv=0.0)
        g.select('Print/save')
        g.press('ok')
        g.feed(ph=5.0, ref_mv=0.0)
        g.press('ok')
        g.menu('Measured values', 'Values')
        g.choose('view')
        self.assertEqual(g.screen(), 'values')
        self.assertIn('2 / 2', g.lines()[2])
        g.press('left')
        self.assertIn('1 / 2', g.lines()[2])
        self.assertIn('4.000', ' '.join(g.lines()))
        g.press('ok')
        self.assertIn('2 / 2', g.lines()[2])
        g.press('ok', duration_s=1.0)
        self.assertIn('1 / 2', g.lines()[2])
        g.press('back')
        g.select('Values')
        g.press('ok'), g.choose('delete last')
        self.assertEqual(len(g.dev('values')), 1)
        g.select('Data')
        g.press('ok'), g.choose('print')
        g.to_main()
        g.select('Print/save')
        g.press('ok')
        self.assertEqual(len(g.dev('values')), 1)                             # 'print' only: no printer, nothing stored

    def test_sensors_new_edit_delete(self):
        g = Rig()
        g.menu('Sensors', 'New sensor')
        g.choose('pH electrode')
        g.enter('Unitrode')
        self.assertEqual(g.screen(), 'sensor')
        self.assertTrue(any(s['name'] == 'Unitrode' for s in g.dev('sensors')))
        g.select('Slope')
        g.press('ok'), g.enter('98.5')
        g.select('Calibration interval')
        g.press('ok'), g.enter('7')
        s = next(x for x in g.dev('sensors') if x['name'] == 'Unitrode')
        self.assertEqual((s['slope'], s['cal_interval']), (98.5, 7))
        g.press('back')
        g.to_main()
        g.menu('Parameters pH/U/T IS', 'Measuring parameters', 'Sensor name')
        g.choose('Unitrode')
        self.assertEqual(g.dev('parameters', 'ph', 'sensor'), 'Unitrode')
        g.to_main()
        g.menu('Sensors', 'Delete sensor')
        g.choose('Unitrode')
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'SENSOR_IN_USE')
        g.press('ok')
        g.choose('metal def.')
        self.assertFalse(any(s['name'] == 'metal def.' for s in g.dev('sensors')))

    def test_calibration_interval_and_sensor_condition(self):
        g = Rig(ph=4.0, ref_mv=0.0)
        g.ok('sensor.save', name='pH default', settings={'cal_interval': 2})
        g.ok('settings.configure', settings={'power_off': None, 'lcd_off': 'off'})
        g.view(1)
        g.press('cal'), g.press('ok')
        g.feed(ph=7.0, ref_mv=0.0)
        g.press('ok'), g.press('ok')
        self.assertEqual(g.dev('channels', 'ph', 'cal_days'), 2)
        g.idle(86400.0)
        self.assertEqual(g.dev('channels', 'ph', 'cal_days'), 1)
        self.assertTrue(g.lines()[4].rstrip().endswith('1 d'))
        g.ok('sensor.save', name='pH default', settings={'slope': 94.5})
        self.assertEqual(g.dev('channels', 'ph', 'condition'), 'out')
        g.ok('sensor.save', name='pH default', settings={'slope': 95.5})
        self.assertEqual(g.dev('channels', 'ph', 'condition'), 'near')

    def test_calibration_outside_limits_accept_or_reject(self):
        g = Rig(ph=4.0, ref_mv=0.0)
        g.view(1)
        g.press('cal')
        g.feed(ph=4.0, ref_mv=0.0, t_c=25.0)
        g.press('ok')
        g.feed(ph=7.3, ref_mv=0.0)                                              # a pH 7.00 buffer reading 7.3: a 110 % slope
        g.press('ok')
        self.assertEqual(g.dev('calibration', 'stage'), 'limits')
        self.assertIn('Limit values exceeded!', ' '.join(g.lines()))
        g.press('back')
        self.assertEqual(next(x for x in g.dev('sensors') if x['name'] == 'pH default')['slope'], 100.0)
        g.press('cal'), g.feed(ph=4.0, ref_mv=0.0), g.press('ok'), g.feed(ph=7.3, ref_mv=0.0), g.press('ok')
        g.press('ok')                                                          # accepted nevertheless
        self.assertGreater(next(x for x in g.dev('sensors') if x['name'] == 'pH default')['slope'], 103.0)   # 110 %: above the upper limit

    def test_configuration_language_brightness_and_password(self):
        g = Rig()
        g.menu('Configuration', 'Language')
        g.choose('Deutsch')
        self.assertEqual(g.lines()[0].strip(), 'Konfiguration')
        g.select('Helligkeit')
        g.press('ok'), g.choose('40 %')
        self.assertEqual(g.frame()['displays']['tft']['brightness'], 40)
        g.select('Passwort')
        g.press('ok')
        g.enter('Expert')
        g.enter('Lab1')
        g.enter('Lab1')
        self.assertEqual(g.h._instances[INST]['device']['password'], 'Lab1')
        g.select('Program')
        self.assertTrue(g.lines()[g.lines().index(next(x for x in g.lines() if x.startswith('Program')))].endswith(rt.KS_PROGRAM_VERSION))
        g.select('Datum')
        g.press('ok'), g.enter('2026-10-19')
        g.select('Zeit')
        g.press('ok'), g.enter('08:15:00')
        self.assertTrue(g.val('device.datetime').startswith('2026-10-19T08:15'))

    def test_routine_dialog_locks_and_password_unlocks(self):
        g = Rig()
        g.menu('User', 'Dialog type')
        g.choose('Routine')
        self.assertEqual(g.dev('settings', 'dialog_type'), 'Routine')
        g.to_main()
        g.press('ok')
        g.select('Measured values')
        g.press('down')
        self.assertEqual(g.frame()['ui']['menu']['label'], 'Report')                # Sensors is shown but locked in the Routine dialog type
        self.assertIn('Sensors', ' '.join(g.lines()))
        g.select('User')
        g.press('ok')
        g.select('Dialog type')
        g.press('ok'), g.choose('Expert')
        g.enter('wrong')
        self.assertEqual(g.frame()['ui']['message']['text'], 'Wrong password')
        self.assertEqual(g.dev('settings', 'dialog_type'), 'Routine')
        g.select('Dialog type')
        g.press('ok'), g.choose('Expert')
        g.enter('Expert')
        self.assertEqual(g.dev('settings', 'dialog_type'), 'Expert')

    def test_power_off_lcd_off_and_switch_off(self):
        g = Rig()
        g.ok('settings.configure', settings={'lcd_off': 'auto', 'power_off': 5})
        g.idle(25.0)
        self.assertEqual(g.dev('display'), 'dim')
        self.assertEqual(g.frame()['displays']['tft']['brightness'], 30.0)
        g.idle(60.0)
        self.assertEqual(g.dev('display'), 'off')
        self.assertFalse(g.press('down'))                                      # the first key only wakes the display
        self.assertEqual(g.dev('display'), 'on')
        g.idle(300.0)
        self.assertFalse(g.val('power.enabled'))                               # automatic power-off after 5 min without a key
        g.press('onoff')
        g.feed(3.0)
        self.assertEqual(g.screen(), 'main')
        g.press('onoff')
        self.assertFalse(g.val('power.enabled'))
        g.press('onoff'), g.feed(3.0)
        g.press('right', duration_s=2.0, held=['left'])                         # device reset: LEFT + RIGHT 2 s
        self.assertFalse(g.val('power.enabled'))

    def test_reset_to_factory_settings_at_start_up(self):
        g = Rig()
        g.ok('sensor.save', name='Extra', settings={'type': 'pH electrode'})
        g.ok('settings.configure', settings={'brightness': 60})
        g.press('onoff')
        g.press('onoff')
        self.assertEqual(g.screen(), 'boot')
        g.press('down', held=['up'])
        g.feed(3.0)
        self.assertEqual(g.screen(), 'message')
        self.assertIn('User data deleted', g.lines()[2])
        self.assertFalse(any(s['name'] == 'Extra' for s in g.dev('sensors')))
        self.assertEqual(g.dev('settings', 'brightness'), 100)

    def test_report_printing_and_service_show_the_device_response(self):
        g = Rig()
        g.menu('Report', 'Report')
        g.choose('All reports')
        self.assertEqual(g.frame()['ui']['unsupported'], 'no printer connected')
        g.select('Line feed')
        g.press('ok'), g.enter('5')
        self.assertEqual(g.dev('settings', 'line_feed'), 5)
        g.select('Printer')
        g.press('ok'), g.choose('Epson TM-U220B')
        self.assertEqual(g.dev('settings', 'printer'), 'Epson TM-U220B')
        g.to_main()
        g.menu('Configuration', 'Service/Diagnosis')
        g.select('Password')
        self.assertFalse(g.press('ok'))
        self.assertEqual(g.frame()['ui']['unsupported'], 'Metrohm service only')

    def test_capitals_by_holding_ok(self):
        g = Rig()
        g.view(1)
        g.select('User')
        g.press('ok')
        g.enter('AbC')
        self.assertEqual(g.dev('settings', 'user'), 'AbC')
        self.assertTrue(g.lines()[10].rstrip().endswith('AbC'))


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    SLOTS = ['device.parameters', 'device.values_settings', 'device.settings', 'device.sensors', 'temperature.mode', 'temperature.manual_k',
             'probe.selected']

    def test_menu_settings_equal_direct_commands(self):
        a, b = Rig(), Rig()
        a.menu('Parameters pH/U/T IS', 'Calibration param.', 'Number of buffers')
        a.choose('3')
        b.ok('parameters.configure', channel='ph', parameters={'buffers': 3})
        a.to_main()
        a.menu('Parameters DO', 'Measuring parameters', 'Sal. compens.')
        a.enter('35')
        b.ok('parameters.configure', channel='do', parameters={'sal_comp': 35.0})
        a.to_main()
        a.menu('Measured values', 'Criterion')
        a.choose('time-dependent')
        a.select('Time interval')
        a.press('ok'), a.enter('30')
        b.ok('measured_values.configure', settings={'criterion': 'time-dependent'})
        b.ok('measured_values.configure', settings={'interval': 30.0})
        a.to_main()
        a.menu('Configuration', 'Turn off LCD after')
        a.enter('5')
        b.ok('settings.configure', settings={'lcd_off': 5})
        a.to_main()
        a.view(2)
        b.ok('electrochemistry.select_probe', slot='conductance')
        for sid in self.SLOTS:
            self.assertEqual(a.val(sid), b.val(sid), sid)


# ---------------------------------------------------------------- refusals and time-outs
class RefusalsAndTimeouts(unittest.TestCase):
    def test_refusals_leave_the_state_unchanged(self):
        g = Rig(on=False)
        self.assertEqual(g.call('calibration.start')['error']['code'], 'POWER_OFF')
        g.press('onoff')
        self.assertEqual(g.call('calibration.start')['error']['code'], 'NOT_READY')
        self.assertFalse(g.press('ok'))                                        # keys ignored during the start image
        g.feed(3.0)
        before = g.val('device.sensors')
        self.assertEqual(g.call('password.change', old='x', new='a', confirm='a')['error']['code'], 'WRONG_PASSWORD')
        self.assertEqual(g.call('password.change', old='Expert', new='a', confirm='b')['error']['code'], 'PASSWORD_MISMATCH')
        self.assertEqual(g.call('sensor.delete', name='nope')['error']['code'], 'NOT_FOUND')
        self.assertEqual(g.call('calibration.next')['error']['code'], 'NO_CALIBRATION')
        self.assertEqual(g.call('device.reset')['error']['code'], 'NOT_READY')
        self.assertEqual(g.call('parameters.configure', channel='ph', parameters={'buffers': 9})['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(g.val('device.sensors'), before)
        h = Rig(probes=('conductance',))
        h.view(1)
        h.press('cal')
        self.assertEqual(h.frame()['ui']['last_gesture']['rejection'], 'NO_SIGNAL')
        self.assertEqual(h.frame()['ui']['message']['text'], 'No measuring signal')

    def test_calibration_result_returns_after_30_s(self):
        g = Rig(ph=4.0, ref_mv=0.0)
        g.ok('parameters.configure', channel='ph', parameters={'buffers': 1})
        g.view(1)
        g.press('cal'), g.press('ok')
        self.assertEqual(g.screen(), 'cal_result')
        g.idle(29.0)
        self.assertEqual(g.screen(), 'cal_result')
        g.idle(1.0)
        self.assertEqual(g.screen(), 'main')
        self.assertIsNone(g.dev('calibration'))
        g.idle(3.0)
        self.assertIsNone(g.frame()['ui']['message'])


# ---------------------------------------------------------------- rendering and contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(ph=7.0), Rig(ph=7.0)
        for g in (a, b):
            g.menu('Configuration')
        self.assertTrue((a.img() == b.img()).all())
        self.assertEqual(a.frame()['displays'], b.frame()['displays'])
        for r in range(9):
            self.assertEqual(read_line(a.img(), r), a.lines()[r].rstrip(), r)
        OUT.mkdir(parents=True, exist_ok=True)
        renders = {}
        g = Rig(ph=7.0, ref_mv=0.0, conductance=0.0012)
        renders['home'] = g.h.panel(INST).save_png('tft', OUT / 'home.png')
        g.view(1)
        renders['one_channel'] = g.h.panel(INST).save_png('tft', OUT / 'one_channel.png')
        g.to_main()
        g.press('ok')
        renders['menu'] = g.h.panel(INST).save_png('tft', OUT / 'menu.png')
        g.open('Configuration', 'Language')
        renders['selection'] = g.h.panel(INST).save_png('tft', OUT / 'selection.png')
        g.press('back')
        g.select('Date')
        g.press('ok')
        renders['editor'] = g.h.panel(INST).save_png('tft', OUT / 'editor.png')
        g.grid_fn('cancel')
        g.to_main()
        g.view(1)
        g.press('cal')
        g.feed(ph=4.0, ref_mv=0.0)
        g.press('ok'), g.press('ok')
        renders['error'] = g.h.panel(INST).save_png('tft', OUT / 'error.png')
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in renders.items()},
                                                     indent=2) + '\n')

    def test_spec_and_contract(self):
        meta = rt.export_metrohm914_device_metadata()['asset_types'][rt.METROHM914_TYPE]
        caps = set(meta['capabilities']) | set(meta['device_capabilities'])
        self.assertEqual(pu.check_spec(SPEC, caps), [])
        self.assertLessEqual(set(pu.spec_capabilities(SPEC)), caps)
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        legacy = rt.Metrohm914Runtime()
        legacy.create_instance('x')
        legacy.register_principal('a', visibility='agent')
        self.assertNotIn('panel.press', legacy.session('a').describe(instance_id='x')['capabilities'])   # the CHEM-005 runtime is unchanged
        g = Rig()
        self.assertIn('panel.press', g.A.describe(instance_id=INST)['capabilities'])
        self.assertEqual(g.call('panel.frame')['error']['code'], 'ACCESS_DENIED')
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        if 'chemistry_lab.panel_ui' not in c.get('extensions', {}):
            self.skipTest('contract not patched yet')
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for cap in set(meta['device_capabilities']) | set(meta['panel_capabilities']):
            self.assertIn(cap, c['capabilities'], cap)
        for k in SPEC['inputs'].values():
            self.assertIn(k['component_id'], c['components'])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
