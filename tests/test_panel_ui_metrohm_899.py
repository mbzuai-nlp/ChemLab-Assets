"""front-panel tests for CHEM-102 metrohm_899 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on the Metrohm 899 dialog firmware of
scripts/basic40_runtime_measure.py): python3 -m pytest tests/test_panel_ui_metrohm_899.py -q

Every gesture goes through the session API (panel.press, the runtime's normal invoke path).  Checks look at the runtime state and at the screen
twice: the structured frame (panel.frame) and the rendered pixels of panel.capture, read back with a 5x7 dot-matrix reader that knows only the
display geometry of the spec.  Themes: the eight key procedures of the menu survey and the other dialogs of the manual (statistics, common
variables, sample table, calculations and the formula editor, system settings, contrast, date / time, manual stirrer, curve, initialization,
autostart, automatic start, stop criteria); panel = direct capability (same runtime state); refusals and locks; time-outs; render determinism
and the PNGs of outputs/panel_ui/metrohm_899/; contract entries."""
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_measure as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'kf899_01'
OUT = ROOT / 'outputs/panel_ui/metrohm_899'
ASSET = ROOT / 'assets/basic40_batch02/t4_measure/metrohm_899'
SPEC = rt.METROHM899_PANEL_SPEC
LCD = SPEC['displays']['lcd']
UG = 1e-9                                                                      # kg per µg
SURVEY_TASKS = ['switch on / off', 'load a method template', 'carry out a KF determination', 'change the stirring rate during conditioning or titration',
                'change a method parameter (e.g. Start drift)', 'save a modified method', 'activate the sample table', 'cancel a determination']
DONE_TASKS = set()
_GLYPHS = {}
for _ch, _cols in pu.FONT5X7.items():
    _GLYPHS.setdefault(bytes(_cols), _ch)


# ---------------------------------------------------------------- reading the rendered display (5x7 dots at text_scale, 2 px margin)
def read_lines(img):
    """Text of every line of the graphic LCD from the pixels: per character cell the 5 x 7 dots, inverted cells recognised by their dark gap
    column; dots compared with the font table."""
    s = LCD['text_scale']
    out = []
    light = lambda x, y: int(img[y, x, 0]) + int(img[y, x, 1]) + int(img[y, x, 2]) > 300  # noqa: E731
    for r in range(LCD['rows']):
        y0 = 2 + r * 9 * s
        text = ''
        for c in range(LCD['cols']):
            x0 = 2 + c * 6 * s
            inv = not light(x0 + 5 * s, y0 + 3 * s + s // 2) and not light(x0 + 5 * s, y0 + 7 * s + s // 2)
            cols = []
            for cx in range(5):
                bits = 0
                for cy in range(8):
                    ink = not light(x0 + cx * s + s // 2, y0 + cy * s + s // 2)
                    if ink != inv:
                        bits |= 1 << cy
                cols.append(bits)
            text += _GLYPHS.get(bytes(cols), '?')
        out.append(text.rstrip())
    return out


# ---------------------------------------------------------------- rig
class Rig:
    def __init__(self, on=True, sample=True, drift_ug_min=5.0, water_ug=1000.0):
        self.h = rt.Metrohm899DeviceHost()
        self.h.create_instance(INST)
        self.h.register_principal('agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        for slot in ('cell', 'generator', 'indicator'):
            self.ok('slot.set_installed', slot=slot, installed=True)
        if sample:
            self.load(water_ug, drift_ug_min)
        if on:
            self.press('stop')
            self.step(5.0)

    def call(self, cap, session=None, **args):
        self.n += 1
        return (session or self.A).invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')

    def ok(self, cap, **args):
        r = self.call(cap, **args)
        assert r['status'] == 'succeeded', (cap, args, r['error'])
        return r['result']

    def load(self, water_ug, drift_ug_min=5.0, ready=True):
        self.n += 1
        self.h.load_sample(instance_id=INST, water_mass_kg=water_ug * UG, background_drift_kg_s=drift_ug_min * UG / 60, reagent_ready=ready,
                           request_id=f's{self.n}')

    def press(self, key, duration_s=0.1, held=None):
        args = dict(input_id=key, duration_s=duration_s)
        if held:
            args['held'] = held
        r = self.call('panel.press', **args)
        assert r['status'] == 'succeeded', r
        return r['result']['accepted']

    def keys(self, *ks):
        return [self.press(k) for k in ks]

    def step(self, dt):
        self.h.step(dt)

    def frame(self):
        return self.call('panel.frame', session=self.E)['result']['frame']

    def lines(self):
        return self.frame()['displays']['lcd']['lines']

    def screen(self):
        return self.frame()['screen']

    def item(self):
        m = self.frame()['ui']['menu']
        return m and m['item']

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
        r = self.call('panel.capture', display_id='lcd')['result']['image']
        return np.asarray(Image.open(r['path']).convert('RGB'))

    def seen(self):
        """The screen as the pixels show it."""
        return read_lines(self.img())

    # navigation by what the screen shows
    def select(self, label):
        for _ in range(3):
            for _ in range(120):
                fr = self.frame()['ui']['menu']
                if fr and fr['label'].lstrip('>').startswith(label):
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

    def menu(self, *labels):
        """From the main dialog: Menu, then the given lines."""
        self.to_main()
        self.open('Menu', *labels)

    def to_main(self):
        for _ in range(8):
            if self.screen() == 'main' and self.frame()['ui']['editor'] is None:
                return
            self.press('back')
        assert self.screen() == 'main', self.screen()

    def fbar(self, label):
        """Select a function of the function bar and execute it with OK."""
        for _ in range(12):
            fb = self.frame()['displays']['lcd'].get('function_bar')
            assert fb, self.lines()
            cur = fb['labels'][fb['selected']]
            if cur == label:
                return self.press('ok')
            self.press('right' if fb['labels'].index(label) > fb['selected'] else 'left')
        raise AssertionError(label)

    def grid_goto(self, row, col):
        for _ in range(60):
            g = self.frame()['ui']['editor']['grid']
            if g == [row, col]:
                return
            self.press('down' if g[0] < row else 'up' if g[0] > row else 'right' if g[1] < col else 'left')
        raise AssertionError((row, col))

    def grid_rows(self):
        ed = self.frame()['ui']['editor']
        f = ed['field']
        name = ed.get('grid_name') or next(x for x in _fields() if x['id'] == f)['grid']
        rows = []
        for row in SPEC['char_grids'][name]['rows']:
            rows.append([t['label'] if isinstance(t, dict) else t for t in row])
        return rows

    def type(self, text):
        """Enter characters in the character grid: select each with the arrows, OK."""
        for ch in text:
            rows = self.grid_rows()
            r, c = next((i, j) for i, row in enumerate(rows) for j, t in enumerate(row) if t == ch)
            self.grid_goto(r, c)
            assert self.press('ok'), ch

    def grid_fn(self, fn):
        rows = self.grid_rows()
        r, c = next((i, j) for i, row in enumerate(rows) for j, t in enumerate(row) if t == '{' + fn + '}')
        self.grid_goto(r, c)
        return self.press('ok')

    def enter(self, text, clear=True):
        """On an open grid editor: Clear, the characters, Accept."""
        if clear:
            self.grid_fn('clear')
        self.type(text)
        return self.grid_fn('accept')

    def choose(self, option):
        """In an open selection list: move to the option, OK."""
        for k in range(80):
            d = self.frame()['displays']['lcd']
            cur = next((ln for ln, inv in zip(d['lines'][1:], d['inverse'][1:]) if inv), None)
            if cur is not None and cur.strip() == option:
                return self.press('ok')
            names = [x.strip() for x in d['lines'][1:]]
            up = option in names and cur is not None and names.index(option) < names.index(cur.strip())
            self.press('up' if up or k >= 40 else 'down')                      # off-screen options: down first, then up
        raise AssertionError(option)

    def run_to_end(self, limit=400):
        for _ in range(limit):
            if self.dev('run') is None and self.val('device.status') != 'busy':
                return
            self.step(1.0)
        raise AssertionError('determination did not end')

    def determination(self, size=None):
        """START at Conditioning OK, the sample request, START, until the results."""
        if self.dev('conditioning', 'phase') == 'off':
            self.press('start')
            self.step(1.0)
        assert self.dev('conditioning', 'ok'), self.dev('conditioning')
        self.press('start')
        self.step(8.0)
        if size is not None:
            self.select('Sample size')
            self.press('ok')
            self.enter(size)
        self.press('start')
        self.run_to_end()


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
    def test_switch_on_and_off(self):
        g = Rig(on=False)
        self.assertEqual(g.screen(), 'off')
        self.assertFalse(g.press('ok'))                                        # nothing reacts but STOP
        self.assertTrue(g.press('stop'))
        self.assertTrue(g.val('power.enabled'))
        self.assertEqual(g.screen(), 'boot')
        self.assertIn('easy, safe, precise', g.seen()[5])
        self.assertFalse(g.press('ok'))                                        # keys are ignored during the system test
        g.step(5.0)
        self.assertEqual(g.screen(), 'main')
        seen = g.seen()
        self.assertTrue(seen[0].startswith('>Menu') and seen[0].endswith('ready'), seen)
        self.assertTrue(seen[1].startswith('Method') and seen[1].endswith('KFC'))
        g.press('stop', duration_s=2.0)                                        # released before 3 s: stays on
        self.assertTrue(g.val('power.enabled'))
        g.press('stop', duration_s=3.0)
        self.assertFalse(g.val('power.enabled'))
        self.assertEqual(g.screen(), 'off')
        self.assertEqual(g.seen(), [''] * 8)
        DONE_TASKS.add('switch on / off')

    def test_load_method_template(self):
        g = Rig()
        g.select('Method')
        g.press('ok')
        self.assertEqual(g.screen(), 'method_table')
        self.assertTrue(g.seen()[-1].startswith('Load New Store Delete Export'))
        g.fbar('New')
        self.assertEqual(g.seen()[1:4], ['KFC', 'KFC-Blank', 'Blank'])
        g.select('KFC-Blank')
        g.fbar('Load')
        self.assertEqual(g.screen(), 'main')
        self.assertTrue(g.seen()[1].endswith('KFC-Blank'))
        self.assertEqual(g.dev('method', 'template'), 'KFC-Blank')
        self.assertEqual(g.dev('method', 'calcs')[0]['formula'], '(EP1-CV01)/C00')
        DONE_TASKS.add('load a method template')

    def test_carry_out_a_kf_determination(self):
        g = Rig(water_ug=1000.0, drift_ug_min=5.0)
        g.press('start')                                                       # conditioning
        self.assertEqual(g.screen(), 'live')
        g.step(1.0)
        seen = g.seen()
        self.assertTrue(seen[0].endswith('cond.ok'))
        self.assertEqual(seen[2].strip(), 'Conditioning OK')
        self.assertIn('5.0 µg/min', g.lines()[4])
        g.press('start')                                                       # add the sample within 8 s
        self.assertIn('Add sample', g.lines()[2])
        self.assertTrue(g.lines()[2].endswith('8 s'))
        g.step(2.0)
        self.assertTrue(g.seen()[2].endswith('6 s'))
        g.step(6.0)
        self.assertEqual(g.screen(), 'request')                                # the sample size is requested, the run holds
        self.assertTrue(g.seen()[0].endswith('hold'))
        g.select('Sample size')
        g.press('ok')
        self.assertEqual(g.lines()[0], 'Sample size')
        g.enter('0.5')
        self.assertTrue(g.lines()[1].endswith('0.5'))
        g.press('start')                                                       # titration
        self.assertEqual(g.screen(), 'live')
        g.step(10.0)
        self.assertEqual(g.val('device.status'), 'busy')
        self.assertTrue(g.frame()['displays']['lcd']['widgets'][0]['series'][0]['points'])   # the curve
        self.assertTrue(g.seen()[2].endswith('µg'))
        g.run_to_end()
        self.assertEqual(g.screen(), 'results')
        seen = g.seen()
        self.assertTrue(seen[1].startswith('Water') and seen[1].endswith('2000.0 ppm'), seen)   # 1000 µg / 0.5 g
        self.assertTrue(seen[4].endswith('1000.0 µg'))
        self.assertEqual(seen[5], 'Regular stop')
        self.assertTrue(seen[-1].startswith('Curve Recalculate'))
        r = g.dev('result')
        self.assertAlmostEqual(r['ep1_ug'], 1000.0, places=6)
        self.assertAlmostEqual(r['results'][0]['value'], 2000.0, places=6)
        self.assertEqual(g.val('settings.sample_mass_kg'), 0.0005)
        self.assertEqual(g.dev('conditioning', 'phase'), 'ok')                  # conditioning restarted in the background
        g.press('back')
        self.assertEqual(g.screen(), 'main')
        self.assertTrue(g.lines()[4].endswith('0.5'))
        g.open('Menu', 'Live dialog')
        self.assertEqual(g.screen(), 'live')
        self.assertEqual(g.lines()[2].strip(), 'Conditioning OK')
        DONE_TASKS.add('carry out a KF determination')

    def test_change_the_stirring_rate(self):
        g = Rig()
        g.press('start')
        g.step(1.0)
        self.assertEqual(g.dev('stirrer'), {'on': True, 'rate': 8})
        g.fbar('Stirrer')
        self.assertEqual(g.screen(), 'stirrer')
        self.assertEqual(g.seen()[2].split(), ['Stirrer', 'on', 'Rate', '8'])
        self.assertEqual(g.seen()[-1], 'Off Stir- Stir+')
        g.fbar('Stir+')
        g.press('ok')
        self.assertEqual(g.dev('stirrer', 'rate'), 10)
        g.fbar('Stir-')
        self.assertEqual(g.dev('stirrer', 'rate'), 9)
        g.fbar('Off')
        self.assertFalse(g.dev('stirrer', 'on'))
        self.assertTrue(g.seen()[-1].startswith('On '))
        g.fbar('On')
        g.press('back')
        self.assertEqual(g.screen(), 'live')
        g.press('start')                                                       # also during the titration
        g.step(9.0)
        g.press('start')
        g.step(2.0)
        g.press('back'), g.open('Menu', 'Live dialog')
        g.fbar('Stirrer')
        g.fbar('Stir-')
        self.assertEqual(g.dev('stirrer'), {'on': True, 'rate': 7})
        self.assertEqual(g.val('coulometry.status'), 'TITRATING')
        DONE_TASKS.add('change the stirring rate during conditioning or titration')

    def test_change_start_drift(self):
        g = Rig(drift_ug_min=15.0)
        g.menu('Parameters', 'Conditioning')
        self.assertEqual(g.lines()[0].split()[0], 'Conditioning')
        g.select('Start drift')
        self.assertTrue(g.seen()[2].endswith('20 µg/min'))
        g.press('ok')
        g.enter('10')
        self.assertAlmostEqual(g.val('settings.start_drift_threshold_kg_s'), 10 * UG / 60, places=20)
        self.assertTrue(g.lines()[2].endswith('10 µg/min'))
        g.to_main()
        g.press('start')
        g.step(2.0)
        self.assertEqual(g.lines()[2].strip(), 'Conditioning not OK')          # 15 µg/min drift is above 10
        g.press('start')
        self.assertEqual(g.frame()['ui']['message']['text'], '012-111 Conditioning not OK')
        self.assertIsNone(g.dev('run'))
        DONE_TASKS.add('change a method parameter (e.g. Start drift)')

    def test_save_a_modified_method(self):
        g = Rig()
        g.menu('Parameters', 'Titration parameters', 'Extraction time')
        g.enter('30')
        self.assertEqual(g.val('settings.extraction_time_s'), 30)
        g.to_main()
        g.open('Method')
        g.fbar('Store')
        self.assertEqual(g.screen(), 'store_method')
        self.assertTrue(g.lines()[1].endswith('KFC'))                          # the template name is suggested
        g.press('ok')
        g.enter('MyKF')
        self.assertTrue(g.lines()[1].endswith('MyKF'))
        g.press('back')                                                        # BACK saves
        self.assertEqual(g.screen(), 'method_table')
        self.assertEqual(g.seen()[1], 'MyKF')
        self.assertEqual(g.dev('methods'), [{'name': 'MyKF', 'template': 'KFC'}])
        g.fbar('New'), g.fbar('Load')                                          # load KFC, then MyKF again: extraction time back to 30 s
        self.assertEqual(g.val('settings.extraction_time_s'), 0)
        g.open('Method')
        g.select('MyKF')
        g.fbar('Load')
        self.assertEqual(g.val('settings.extraction_time_s'), 30)
        self.assertTrue(g.lines()[1].endswith('MyKF'))
        DONE_TASKS.add('save a modified method')

    def test_activate_the_sample_table(self):
        g = Rig()
        g.menu()
        self.assertNotIn('>Sample table', g.lines())
        g.open('Control', 'Sample table')
        g.choose('on')
        self.assertTrue(g.lines()[1].endswith('on'))
        g.press('back')
        self.assertIn('>Sample table', g.seen())
        g.open('Sample table')
        self.assertEqual(g.screen(), 'sample_table')
        self.assertEqual(g.lines()[1].strip(), '1 ...')
        self.assertTrue(g.seen()[-1].startswith('Edit Delete Insert New Store'))
        DONE_TASKS.add('activate the sample table')

    def test_cancel_a_determination(self):
        g = Rig(water_ug=5000.0)
        g.press('start'), g.step(1.0), g.press('start'), g.step(8.0), g.press('start')
        g.step(5.0)
        self.assertEqual(g.val('coulometry.status'), 'TITRATING')
        g.press('stop')
        self.assertEqual(g.val('coulometry.status'), 'STOPPED')
        self.assertEqual(g.screen(), 'results')
        self.assertEqual(g.lines()[5], 'Manual stop')
        self.assertEqual(g.val('device.status'), 'ready')
        self.assertFalse(g.dev('stirrer', 'on'))
        DONE_TASKS.add('cancel a determination')


class Callers(unittest.TestCase):
    """Lead rule 2026-10-19: conditioning and determinations belong to the caller that started them; another caller can neither stop, power off,
    continue nor change them, on the panel or the direct path; the refusal shows on the display; the evaluator is unrestricted."""
    def test_run_belongs_to_the_caller_that_started_it(self):
        g = Rig(water_ug=5000.0)
        g.h.register_principal('agent_b')
        B = g.h.session('agent_b')
        g.press('start')                                                                 # A starts the conditioning on the panel
        g.step(1.0)
        self.assertEqual(g.h._kf899_instances[INST]['device']['owner'], 'agent')
        snap = lambda: (g.dev('conditioning', 'phase'), g.dev('run') is None, g.val('coulometry.running'), g.val('power.enabled'))   # noqa: E731
        before = snap()
        for cap, args in (('run.stop', {}), ('power.set', dict(enabled=False)), ('stirrer.set', dict(on=False))):
            r = g.call(cap, session=B, **args)
            self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        self.assertEqual(snap(), before)
        r = g.call('panel.press', session=B, input_id='stop', duration_s=0.1)              # B's STOP on the panel
        self.assertFalse(r['result']['accepted'])
        self.assertEqual(snap(), before)
        self.assertTrue(any('Started by other user' in line for line in g.lines()))       # shown on the display
        g.step(4.0)
        self.assertFalse(any('Started by other user' in line for line in g.lines()))
        g.press('start')                                                                 # A: the determination, sample data request
        g.step(8.0)
        self.assertTrue(g.dev('run')['request_open'])
        self.assertFalse(g.call('panel.press', session=B, input_id='start', duration_s=0.1)['result']['accepted'])   # B cannot Continue
        self.assertEqual(g.call('sample.set_data', session=B, size=0.5, unit='g')['error']['code'], 'ACCESS_DENIED')
        self.assertTrue(g.dev('run')['request_open'])
        g.press('start')                                                                 # A continues
        g.step(3.0)
        self.assertEqual(g.call('coulometry.stop', session=B)['error']['code'], 'ACCESS_DENIED')
        self.assertTrue(g.val('coulometry.running'))
        r = g.call('run.stop', session=g.E)                                              # the evaluator is unrestricted
        self.assertEqual(r['status'], 'succeeded')
        self.assertFalse(g.val('coulometry.running'))


# ---------------------------------------------------------------- the other dialogs of the manual
class OtherDialogs(unittest.TestCase):
    def test_statistics_series_details_reset_increase(self):
        g = Rig()
        g.menu('Parameters', 'Statistics', 'Statistics')
        g.choose('on')
        g.to_main()
        for k, (water, size) in enumerate(((1000.0, '0.5'), (1100.0, '0.5'), (900.0, '0.5'))):
            if k:
                g.load(water)
            g.determination(size=size)
        self.assertEqual(g.screen(), 'results')
        g.fbar('Statistics')
        seen = g.seen()
        self.assertEqual(seen[1], 'Water')
        self.assertIn('Mean(3) 2000.0 ppm', seen[2])
        self.assertIn('s abs 200.00 ppm', seen[3])
        self.assertIn('s rel 10.00 %', seen[4])
        self.assertEqual(seen[5].strip(), 'Statistics 3/3')
        g.fbar('Details')
        self.assertEqual(g.screen(), 'details')
        g.select(' 2')
        g.fbar('On/Off')
        self.assertFalse(g.dev('statistics', 'rows')[1]['included'])
        self.assertTrue(g.lines()[2].startswith(' 2*'))
        g.press('back')
        self.assertIn('Mean(2) 1900.0 ppm', g.lines()[2])
        g.fbar('Increase')
        self.assertTrue(g.lines()[5].endswith('3/4'))
        g.fbar('Reset')
        self.assertEqual(g.dev('statistics', 'done'), 0)

    def test_blank_to_common_variable_then_kfc_blank(self):
        g = Rig(water_ug=40.0)
        g.open('Method')
        g.fbar('New'), g.select('Blank'), g.fbar('Load')
        g.determination()
        self.assertTrue(g.lines()[1].startswith('Blank') and g.lines()[1].endswith('40.00 µg'))
        self.assertAlmostEqual(g.dev('common_variables')[0], 40.0, places=6)    # Save as CV: R1 -> CV01
        g.menu('System', 'Common variables')
        self.assertTrue(g.seen()[1].startswith('CV01') and '40' in g.seen()[1])
        g.to_main()
        g.open('Method')
        g.fbar('New'), g.select('KFC-Blank'), g.fbar('Load')
        g.load(1040.0)
        g.determination(size='0.5')
        self.assertAlmostEqual(g.dev('result', 'results')[0]['value'], 2000.0, places=4)   # (1040 - 40) / 0.5

    def test_common_variables_edit_and_delete(self):
        g = Rig()
        g.menu('System', 'Common variables')
        self.assertTrue(g.lines()[2].endswith('invalid'))
        g.select('CV02')
        g.fbar('Edit')
        g.enter('0.9638')
        self.assertEqual(g.dev('common_variables')[1], 0.9638)
        self.assertTrue(g.seen()[2].endswith('0.9638'))
        g.fbar('Delete')
        self.assertIsNone(g.dev('common_variables')[1])

    def test_calculation_editor_formula_var_and_syntax_check(self):
        g = Rig()
        g.menu('Parameters', 'Calculation')
        self.assertEqual(g.seen()[1].split(), ['R1:', 'Water'])
        g.select('R2')
        g.fbar('Edit')
        self.assertEqual(g.screen(), 'calc_edit')
        g.open('Result name')
        g.enter('Total')
        g.select('R2=')
        g.press('ok')
        g.grid_goto(3, 0)
        g.press('ok')                                                          # Var: the list of additional variables
        self.assertEqual(g.frame()['ui']['editor']['grid_name'], 'formula_var')
        g.type(['MCQ'])                                                        # one entry, back to the formula characters
        g.type('/')
        g.type('1000')
        g.grid_fn('accept')
        self.assertEqual(g.dev('method', 'calcs')[1]['formula'], 'MCQ/1000')
        g.select('R2=')
        g.press('ok')
        g.type('*(')                                                           # 'MCQ/1000*(' fails the syntax check
        self.assertFalse(g.grid_fn('accept'))
        self.assertEqual(g.frame()['ui']['message']['text'], 'Syntax error')
        self.assertEqual(g.dev('method', 'calcs')[1]['formula'], 'MCQ/1000')
        g.step(4.0)
        g.select('Result unit')
        g.press('ok')
        g.choose('mg')
        g.select('Decimal places')
        g.press('ok')
        g.enter('3')
        g.to_main()
        g.determination()
        res = {x['index']: x for x in g.dev('result', 'results')}
        self.assertEqual(res[2]['name'], 'Total')
        self.assertAlmostEqual(res[2]['value'], g.dev('result', 'mcq_ug') / 1000, places=9)
        self.assertTrue(g.lines()[2].startswith('Total') and g.lines()[2].endswith(' mg'))

    def test_sample_table_edit_and_table_driven_determinations(self):
        g = Rig()
        g.menu('Control', 'Sample table')
        g.choose('on')
        g.press('back')
        g.open('Sample table')
        g.fbar('Edit')                                                         # '1 ...': a new line
        self.assertEqual(g.screen(), 'sample_data')
        self.assertIn('Line  1 of 1', g.lines()[-1])
        g.open('ID1')
        g.enter('A1')
        g.select('Sample size')
        g.press('ok')
        g.enter('0.25')
        g.press('right')                                                       # right on the last line: a new line with the previous data
        self.assertIn('Line  2 of 2', g.lines()[-1])
        self.assertEqual(g.dev('sample_table')[1]['size'], 0.25)
        g.open('ID1')
        g.enter('A2')
        g.press('back')
        self.assertEqual(g.screen(), 'sample_table')
        self.assertTrue(g.seen()[1].startswith(' 1 A1'))
        g.fbar('Store')
        self.assertTrue(g.dev('sample_table_stored'))
        g.to_main()
        g.determination()
        self.assertEqual(g.dev('result', 'sample'), {'id1': 'A1', 'id2': '', 'size': 0.25, 'unit': 'g'})
        self.assertEqual([x['id1'] for x in g.dev('sample_table')], ['A2'])    # the line of the determination is removed
        g.menu('Sample table')
        g.fbar('Load')
        self.assertEqual([x['id1'] for x in g.dev('sample_table')], ['A1', 'A2'])
        g.select(' 1')
        g.fbar('Delete')
        self.assertEqual([x['id1'] for x in g.dev('sample_table')], ['A2'])
        g.fbar('Insert')
        self.assertEqual([x['id1'] for x in g.dev('sample_table')], ['', 'A2'])
        g.press('stop')                                                        # New is offered in ready status only
        g.fbar('New')
        self.assertEqual(g.dev('sample_table'), [])

    def test_routine_dialog_and_forced_expert(self):
        g = Rig()
        g.menu('System', 'Settings', 'Dialog type')
        g.choose('Routine')
        g.to_main()
        g.press('ok')
        self.assertNotIn('>Parameters', g.lines())
        self.assertNotIn('>System', g.lines())
        g.to_main()
        g.open('Method')
        self.assertEqual(g.seen()[-1], 'Load')                                  # methods can only be loaded
        g.to_main()
        g.press('stop', duration_s=3.0)
        g.press('stop')                                                        # at the logo: STOP held, BACK briefly
        self.assertTrue(g.press('back', held=['stop']))
        g.step(5.0)
        g.press('ok')
        self.assertIn('>System', g.lines())
        self.assertEqual(g.val('device.settings')['dialog_type'], 'Routine')

    def test_date_time_contrast_and_beep(self):
        g = Rig()
        g.menu('System', 'Settings', 'Time')
        g.enter('09:30:00')
        g.select('Date')
        g.press('ok')
        g.enter('2026:10:19')
        self.assertTrue(g.val('device.datetime').startswith('2026-10-19T09:30'))
        g.select('Time')
        g.press('ok')
        self.assertFalse(g.enter('25:61'))                                     # only valid entries: 'Invalid input', editor stays open
        self.assertEqual(g.frame()['ui']['message']['text'], 'Invalid input')
        g.grid_fn('cancel')
        g.step(3.0)                                                            # the message has gone
        g.select('Contrast')
        self.assertTrue(g.seen()[-1].endswith('212'))
        g.press('right'), g.press('right'), g.press('left')
        self.assertEqual(g.val('device.settings')['contrast'], 213)
        g.press('up', held=['stop'])                                          # STOP held with an arrow: several steps
        self.assertEqual(g.val('device.settings')['contrast'], 218)
        self.assertEqual(g.frame()['displays']['lcd']['brightness'], 103.0)
        g.ok('settings.configure', settings={'contrast': 150})
        self.assertLess(g.img().mean(), Rig().img().mean())                   # low contrast: a darker picture
        g.select('Beep')
        g.press('ok')
        g.choose('off')
        self.assertFalse(g.val('device.settings')['beep'])

    def test_manual_control_curve_and_recalculate(self):
        g = Rig()
        g.menu('Manual control')
        self.assertEqual(g.seen()[1].split(), ['Stirrer', 'off', 'Rate', '8'])
        g.fbar('On')
        self.assertTrue(g.dev('stirrer', 'on'))
        g.fbar('Off')
        g.press('back')
        self.assertEqual(g.screen(), 'menu')
        g.to_main()
        g.determination(size='0.5')
        n = len(g.dev('result', 'curve'))
        g.fbar('Curve')
        self.assertEqual(g.screen(), 'curve')
        self.assertEqual(g.lines()[1].strip(), str(n))
        g.press('left')
        pt = g.dev('result', 'curve')[n - 2]
        self.assertEqual(g.lines()[1].strip(), str(n - 1))
        self.assertTrue(g.lines()[4].strip().startswith(f'{pt["time_s"]:.0f}'))
        g.press('back')
        g.press('back')                                                        # results -> main: correct the sample size, recalculate
        g.select('Sample size')
        g.press('ok')
        g.enter('0.25')
        g.menu('Results')
        g.fbar('Recalculate')
        self.assertAlmostEqual(g.dev('result', 'results')[0]['value'], 4000.0, places=4)
        g.press('back')
        self.assertEqual(g.screen(), 'menu')

    def test_print_usb_and_update_functions_show_the_device_response(self):
        g = Rig()
        g.menu('Print reports')
        g.select('Results')
        self.assertFalse(g.press('ok'))
        self.assertFalse(g.frame()['ui']['last_gesture']['accepted'])
        self.assertEqual(g.frame()['ui']['unsupported'], 'no printer connected')
        g.select('PC/LIMS')
        g.press('ok')
        self.assertEqual(g.frame()['ui']['message']['text'], '020-511 Action not possible')
        g.to_main()
        g.menu('System')
        self.assertNotIn('>File management', g.lines())                       # visible only with a USB flash drive
        g.open('Diagnosis', 'Software update')
        self.assertTrue(g.seen()[1].endswith('58990011'))
        g.press('ok')
        self.assertEqual(g.frame()['ui']['message']['text'], '020-511 Action not possible')
        g.to_main()
        g.open('Method')
        g.fbar('Export')
        self.assertEqual(g.frame()['ui']['message']['text'], '020-511 Action not possible')

    def test_system_initialization(self):
        g = Rig()
        g.ok('method.store', name='Keep')
        g.ok('common_variable.set', index=1, value=2.5)
        g.press('stop', duration_s=3.0)
        g.press('stop', duration_s=10.0)                                      # held about 10 s at switch-on
        self.assertEqual(g.screen(), 'init_confirm')
        self.assertTrue(g.lines()[6].strip().endswith('8 s'))
        g.press('back')
        self.assertEqual(g.screen(), 'init_confirm')
        g.press('back')
        self.assertEqual(g.screen(), 'init')
        self.assertEqual(g.val('device.status'), 'init')
        g.step(79.0)
        self.assertEqual(g.screen(), 'init')
        g.step(1.0)
        self.assertEqual(g.screen(), 'boot')                                   # restarts automatically
        g.step(5.0)
        self.assertEqual(g.screen(), 'main')
        self.assertEqual(g.dev('methods'), [])
        self.assertEqual(g.dev('common_variables'), [None] * 5)

    def test_initialization_request_times_out(self):
        g = Rig()
        g.ok('method.store', name='Keep')
        g.press('stop', duration_s=3.0)
        g.press('stop', duration_s=10.0)
        g.press('back')
        g.step(8.0)                                                            # not confirmed within 8 s: canceled
        self.assertNotEqual(g.screen(), 'init_confirm')
        g.press('back')
        self.assertEqual(g.dev('methods'), [{'name': 'Keep', 'template': 'KFC'}])

    def test_autostart_and_automatic_start(self):
        g = Rig()
        g.menu('Control', 'Autostart')
        g.choose('on')
        g.select('Number of autostarts')
        g.press('ok')
        g.enter('2')
        g.to_main()
        g.press('start'), g.step(1.0), g.press('start'), g.step(8.0), g.press('start')
        for _ in range(100):
            if g.dev('result_seq') == 1:
                break
            g.step(1.0)
        self.assertEqual(g.dev('run', 'phase'), 'add')                        # the second determination starts by itself
        g.load(500.0)
        g.step(8.0)
        g.press('start')
        g.run_to_end()
        self.assertEqual(g.dev('result_seq'), 2)
        self.assertIsNone(g.dev('run'))                                       # two determinations in all
        h = Rig()
        h.ok('method.configure', parameters={'automatic_start': True})
        h.press('start')
        h.step(1.0)
        h.load(800.0)                                                         # injecting the sample starts the determination
        self.assertIsNotNone(h.dev('run'))
        self.assertEqual(h.dev('run', 'phase'), 'request')

    def test_stop_criterion_drift_and_stop_time(self):
        g = Rig(drift_ug_min=8.0)
        g.menu('Parameters', 'Control parameters', 'Stop criterion')
        g.choose('drift')
        g.select('Stop drift')
        self.assertFalse(g.frame()['displays']['lcd']['inverse'][g.lines().index(next(x for x in g.lines() if x.startswith('Stop drift')))] is False)
        g.press('back'), g.open('Stop conditions', 'Stop time')
        g.enter('60')
        g.to_main()
        g.press('start'), g.step(1.0), g.press('start'), g.step(8.0), g.press('start')
        g.step(30.0)
        self.assertEqual(g.val('coulometry.status'), 'HOLDING')              # 8 µg/min never falls to the stop drift of 5
        g.step(31.0)
        self.assertEqual(g.dev('result', 'stop_reason'), 'Stop time')
        self.assertAlmostEqual(g.dev('result', 'mcd_s'), 60.0, places=6)

    def test_conditioning_stop_time_stabilizing_time_and_beep(self):
        g = Rig()
        g.ok('method.configure', parameters={'stabilizing_time': 20.0, 'cond_stop_time': 100.0})
        g.press('start')
        g.step(19.0)
        self.assertEqual(g.val('device.status'), 'cond.busy')
        g.step(1.0)
        self.assertEqual(g.val('device.status'), 'cond.ok')
        beeps = g.val('device.beeps')
        g.step(10.0)
        self.assertEqual(g.val('device.beeps'), beeps + 1)                    # conditioned 10 s without interruption
        g.step(70.0)
        self.assertEqual(g.val('device.status'), 'ready')                     # Cond. stop time elapsed

    def test_request_without_hold_runs_in_the_background(self):
        g = Rig(water_ug=1000.0)
        g.menu('Parameters', 'Start conditions', 'Hold at request')
        g.choose('off')
        g.select('Request sample ID')
        g.press('ok'), g.choose('ID1')
        g.to_main()
        g.press('start'), g.step(1.0), g.press('start'), g.step(8.0)
        self.assertEqual(g.screen(), 'request')
        self.assertTrue(g.seen()[0].endswith('busy'))                          # not held: the titration has started
        self.assertEqual(g.val('coulometry.status'), 'TITRATING')
        g.open('ID1')
        g.enter('S7')
        g.step(60.0)
        self.assertEqual(g.val('coulometry.status'), 'COMPLETED')
        self.assertEqual(g.screen(), 'request')                                # shown until START even after the titration (p.55)
        self.assertEqual(g.dev('run', 'phase'), 'done')
        g.press('start')
        self.assertEqual(g.screen(), 'results')
        self.assertEqual(g.dev('result', 'sample')['id1'], 'S7')

    def test_measured_value_display_and_generator_electrode(self):
        g = Rig()
        g.menu('Parameters', 'Conditioning', 'Measured value display')
        g.choose('on')
        g.to_main()
        g.press('start')
        g.step(1.0)
        self.assertTrue(g.lines()[5].endswith('50.0 mV'))
        g.press('stop')
        g.menu('Parameters', 'Titration parameters', 'Generator electr.')
        g.choose('with diaphr.')
        self.assertEqual(g.dev('method', 'params')['generator_current'], 'auto')
        g.select('Generator current')
        g.press('ok')
        g.choose('200 mA')
        self.assertEqual(g.val('settings.current_a'), 0.2)


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    SLOTS = ['device.method', 'device.settings', 'device.sample', 'device.methods', 'device.common_variables', 'device.stirrer',
             'settings.start_drift_threshold_kg_s', 'settings.extraction_time_s', 'settings.stirring_level', 'settings.current_a']

    def same(self, a, b):
        for sid in self.SLOTS:
            self.assertEqual(a.val(sid), b.val(sid), sid)

    def test_parameters_settings_sample_data_and_methods(self):
        a, b = Rig(), Rig()
        a.menu('Parameters', 'Start conditions', 'Pause')
        a.enter('15')
        b.ok('method.configure', parameters={'pause': 15.0})
        a.select('Request sample ID')
        a.press('ok'), a.choose('ID1&ID2')
        b.ok('method.configure', parameters={'request_id': 'ID1&ID2'})
        a.press('back'), a.open('Conditioning', 'Start drift')
        a.enter('12')
        b.ok('coulometry.configure', start_drift_threshold_kg_s=12e-9 / 60)
        a.press('back'), a.open('Titration parameters', 'Stirring rate')
        a.enter('-4')
        b.ok('coulometry.configure', stirring_level=-4)
        a.menu('System', 'Settings', 'User name')
        a.enter('Lab A')
        b.ok('settings.configure', settings={'user_name': 'Lab A'})
        a.to_main()
        a.select('Unit')
        a.press('ok'), a.choose('>User-defined')
        a.enter('vial')
        b.ok('sample.set_data', unit='vial')
        a.open('Method')
        a.fbar('Store'), a.press('back')
        b.ok('method.store', name='KFC')
        a.ok('common_variable.set', index=3, value=1.5)
        b.ok('common_variable.set', index=3, value=1.5)
        self.same(a, b)
        self.assertEqual(a.dev('units')[-1], 'vial')

    def test_determination_by_keys_equals_remote_commands(self):
        a, b = Rig(), Rig()
        a.determination(size='0.5')
        b.ok('run.start'), b.step(1.0), b.ok('run.start'), b.step(8.0)
        b.ok('sample.set_data', size=0.5)
        b.ok('run.start')
        b.run_to_end()
        ra, rb = a.dev('result'), b.dev('result')
        for k in ('ep1_ug', 'mcq_ug', 'mcd_s', 'mdc_ug_min', 'stop_reason', 'results', 'sample'):
            self.assertEqual(ra[k], rb[k], k)


# ---------------------------------------------------------------- refusals, locks and time-outs
class RefusalsAndTimeouts(unittest.TestCase):
    def test_parameters_of_the_titration_locked_while_it_runs(self):
        g = Rig(water_ug=20000.0)
        g.press('start'), g.step(1.0), g.press('start'), g.step(8.0), g.press('start'), g.step(2.0)
        g.press('back')
        g.menu('Parameters', 'Conditioning')
        g.select('Drift correction')
        g.press('up')
        self.assertNotEqual(g.item(), 'start_drift')                          # shown but not selectable during the titration
        self.assertIn('Start drift', ' '.join(g.lines()))
        before = g.val('settings.start_drift_threshold_kg_s')
        self.assertEqual(g.call('coulometry.configure', start_drift_threshold_kg_s=1e-10)['error']['code'], 'BUSY')
        self.assertEqual(g.val('settings.start_drift_threshold_kg_s'), before)
        self.assertEqual(g.call('method.load_template', template='Blank')['error']['code'], 'BUSY')
        self.assertEqual(g.dev('method', 'name'), 'KFC')
        g.select('Stabilizing time')                                          # the firmware's own parameters stay live
        g.press('ok')
        g.enter('5')
        self.assertEqual(g.dev('method', 'params')['stabilizing_time'], 5)

    def test_refusals_leave_the_state_unchanged(self):
        g = Rig(on=False)
        self.assertEqual(g.call('run.start')['error']['code'], 'POWER_OFF')
        g.press('stop')
        self.assertEqual(g.call('run.start')['error']['code'], 'NOT_READY')    # the system test is running
        g.step(5.0)
        g.ok('method.configure', parameters={'electrode_test': True, 'conditioning': False})
        g.ok('power.set', enabled=False)
        g.ok('slot.set_installed', slot='indicator', installed=False)
        g.ok('power.set', enabled=True)
        g.step(5.0)
        before = {k: g.val(k) for k in ('device.run', 'device.result', 'device.status', 'coulometry.status')}
        self.assertFalse(g.press('start'))
        self.assertEqual(g.frame()['ui']['message']['text'], 'Electrode test failed')
        self.assertEqual({k: g.val(k) for k in before}, before)
        self.assertEqual(g.call('method.set_calculation', index=1, formula='EP1/')['error']['code'], 'INVALID_FORMULA')
        self.assertEqual(g.call('method.load', name='Nope')['error']['code'], 'NOT_FOUND')
        self.assertEqual(g.call('results.recalculate')['error']['code'], 'NO_RESULT')
        self.assertEqual(g.call('clock.set', date='2026-02-30')['error']['code'], 'INVALID_VALUE')
        self.assertEqual(g.call('stirrer.set', rate=16)['error']['code'], 'INVALID_ARGUMENT')
        for k in range(100):
            g.ok('method.store', name=f'M{k}')
        self.assertEqual(g.call('method.store', name='M100')['error']['code'], 'LIMIT')

    def test_add_sample_messages_and_boot_time_outs(self):
        g = Rig()
        g.press('start'), g.step(1.0), g.press('start')
        g.step(7.9)
        self.assertEqual(g.dev('run', 'phase'), 'add')
        g.step(0.1)
        self.assertEqual(g.dev('run', 'phase'), 'request')                    # exactly 8 s
        g.ok('method.configure', parameters={'cond_stop_time': 10.0})
        g.press('stop')
        g.press('start'), g.step(1.0)
        g.press('start')
        g.step(2.0)
        g.press('start')                                                       # START during the add-sample request: refused silently
        g.press('stop')
        g.ok('method.configure', parameters={'stabilizing_time': 50.0})
        g.press('start'), g.step(1.0), g.press('start')
        self.assertEqual(g.frame()['ui']['message']['text'], '012-111 Conditioning not OK')
        g.step(3.0)
        self.assertIsNone(g.frame()['ui']['message'])                         # messages are shown for 3 s
        h = Rig(on=False)
        h.press('stop')
        h.step(4.9)
        self.assertEqual(h.screen(), 'boot')
        h.step(0.1)
        self.assertEqual(h.screen(), 'main')

    def test_firmware_message_when_the_titration_cannot_start(self):
        g = Rig()
        g.ok('method.configure', parameters={'conditioning': False})
        g.ok('power.set', enabled=False)
        g.ok('slot.set_installed', slot='indicator', installed=False)
        g.ok('power.set', enabled=True)
        g.step(5.0)
        g.press('start')
        g.step(8.0)
        g.press('start')
        self.assertEqual(g.screen(), 'message')
        self.assertIn('Precondition', g.dev('message', 'text').title() + 'Precondition')
        self.assertTrue(g.press('ok'))
        self.assertEqual(g.screen(), 'main')


# ---------------------------------------------------------------- rendering and contract
class SyncAudit(unittest.TestCase):
    """Sync audit 2026-10-19: Endpoint at / I(pol) / Dynamics / Min. rate shape the titration, the Threshold value gates the automatic start,
    panel commands run as the gesturing session."""

    def run_with(self, **params):
        g = Rig(water_ug=1000.0, drift_ug_min=5.0)
        if params:
            g.ok('method.configure', parameters=params)
        g.determination()
        return g, g.dev('result')

    def test_ipol_and_endpoint_belong_together(self):
        _, ok20 = self.run_with(ipol=20, endpoint=100.0, dynamics=100.0, titration_rate='user')   # p.96 Table 7: matched
        self.assertAlmostEqual(ok20['ep1_ug'], 1000.0, places=6)
        _, over = self.run_with(ipol=20, endpoint=50.0, titration_rate='user')     # the curve of 20 uA crosses 50 mV after the equivalence
        self.assertAlmostEqual(over['ep1_ug'], 1000.0 + 25.0, places=6)            # (50 - 100) mV x 50 ug / 100 mV of iodine excess
        g, under = self.run_with(ipol=10, endpoint=150.0)                           # 10 uA: 150 mV is reached with water left
        left = 100.0 * 50.0 / 70.0
        self.assertAlmostEqual(under['ep1_ug'], 1000.0 - left, places=6)
        self.assertAlmostEqual(g.val('truth.water_remaining_kg') / UG, left, places=6)   # the untitrated water stays in the cell

    def test_dynamics_and_min_rate_set_the_titration_time(self):
        _, fast = self.run_with(titration_rate='fast')
        _, slow_ctrl = self.run_with(titration_rate='user', dynamics=600.0, min_rate=1.0)
        self.assertAlmostEqual(fast['ep1_ug'], slow_ctrl['ep1_ug'], places=6)       # the same water ...
        self.assertGreater(slow_ctrl['mcd_s'], fast['mcd_s'] + 60.0)                # ... but a wide control range and a low minimum rate are slower
        p = dict(rt._kd_template('KFC')['params'], titration_rate='user', dynamics=600.0, min_rate=1.0)
        self.assertAlmostEqual(slow_ctrl['mcd_s'] - fast['mcd_s'],
                               rt._kd_control_extra_s(p, 0.4, 1000.0) - rt._kd_control_extra_s(dict(p, titration_rate='fast'), 0.4, 1000.0), delta=1.0)

    def test_measured_voltage_follows_the_curve(self):
        g = Rig(water_ug=1000.0, drift_ug_min=5.0)
        g.ok('method.configure', parameters={'mp_interval': 1.0})
        g.determination()
        mvs = [p['mv'] for p in g.dev('result', 'curve')]
        self.assertEqual(mvs[-1], 50.0)                                              # at the endpoint
        self.assertTrue(any(v > 120.0 for v in mvs))                                 # well above Endpoint + Dynamics with the sample water

    def test_threshold_gates_the_automatic_start(self):
        g = Rig(water_ug=0.0)
        g.ok('method.configure', parameters={'automatic_start': True, 'threshold': 200.0})
        g.press('start')
        g.step(1.0)
        g.load(50.0)                                                                 # +70 mV jump (50 ug on the 10 uA curve): below 200 mV
        self.assertIsNone(g.dev('run'))
        g.load(500.0)                                                                # +700 mV: the determination starts
        self.assertIsNotNone(g.dev('run'))

    def test_panel_commands_run_as_the_gesturing_session(self):
        g = Rig()
        before = len(g.h._kf899_logs)
        g.press('start')                                                             # conditioning from the panel
        panel = [x for x in g.h._kf899_logs[before:] if x['request_id'].startswith('panel-')]
        self.assertTrue(panel)
        self.assertTrue(all(x['principal_id'] == 'agent' for x in panel), panel)
        self.assertIsNone(getattr(g.h, '_kd_caller', None))


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        for g in (a, b):
            g.menu('Parameters', 'Control parameters')
        self.assertTrue((a.img() == b.img()).all())
        self.assertEqual(a.frame()['displays'], b.frame()['displays'])
        self.assertEqual(read_lines(a.img()), [x.rstrip() for x in a.lines()])
        renders = {}
        OUT.mkdir(parents=True, exist_ok=True)
        g = Rig()
        renders['home'] = g.h.panel(INST).save_png('lcd', OUT / 'home.png')
        g.press('ok')
        renders['menu'] = g.h.panel(INST).save_png('lcd', OUT / 'menu.png')
        g.to_main()
        g.press('start'), g.step(1.0), g.press('start'), g.step(8.0), g.press('start'), g.step(12.0)
        renders['live'] = g.h.panel(INST).save_png('lcd', OUT / 'live.png')
        g.run_to_end()
        renders['results'] = g.h.panel(INST).save_png('lcd', OUT / 'results.png')
        g.to_main()
        g.select('ID1')
        g.press('ok')
        renders['text_editor'] = g.h.panel(INST).save_png('lcd', OUT / 'text_editor.png')
        g.grid_fn('cancel')
        g.press('stop')                                                        # conditioning off, then a start drift it cannot reach
        g.ok('method.configure', parameters={'stabilizing_time': 50.0})
        g.press('start'), g.step(1.0), g.press('start')
        self.assertEqual(g.frame()['ui']['message']['text'], '012-111 Conditioning not OK')
        renders['error'] = g.h.panel(INST).save_png('lcd', OUT / 'error.png')
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in renders.items()},
                                                     indent=2) + '\n')

    def test_spec_and_contract(self):
        meta = rt.export_metrohm899_device_metadata()
        caps = set(meta['capabilities']) | set(meta['device_capabilities'])
        self.assertEqual(pu.check_spec(SPEC, caps), [])
        self.assertLessEqual(set(pu.spec_capabilities(SPEC)), caps)
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        if 'chemistry_lab.panel_ui' not in c.get('extensions', {}):
            self.skipTest('contract not patched yet')
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for cap in set(meta['device_capabilities']) | set(meta['panel_capabilities']):
            self.assertIn(cap, c['capabilities'], cap)
        for k in SPEC['inputs'].values():
            self.assertIn(k['component_id'], c['components'])
        g = Rig()
        self.assertEqual(g.call('panel.frame')['error']['code'], 'ACCESS_DENIED')   # evaluator only
        legacy = rt.Metrohm899Host()
        legacy.create_instance('x')
        legacy.register_principal('a')
        self.assertNotIn('panel.press', legacy.session('a').describe(instance_id='x')['capabilities'])   # the CHEM-102 host is unchanged
        self.assertIn('panel.press', g.A.describe(instance_id=INST)['capabilities'])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
