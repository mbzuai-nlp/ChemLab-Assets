"""front-panel tests for CHEM-080 shimadzu_tocl (panel_ui.json + scripts/asset_runtime/_panel_ui.py + the 1.1.0 standalone functions of the
runtime):  python3 -m pytest tests/test_panel_ui_shimadzu_tocl.py -q

Every gesture goes through the session API (panel.press / panel.switch, the runtime's normal invoke path).  Each screen check reads the
structured frame (panel.frame) and the rendered pixels: every text row of the TFT equals the row drawn alone from the frame text with the
spec colours.  Themes: the eight key procedures of the menu survey (carrier flow check, bubble removal, TC catalyst regeneration, TC blank
check, needle rinse, syringe washes, start-up, shutdown); analyses started and stopped from the panel; Conditions, Measurement Conditions,
Maintenance (IC reagent, residue removal, syringe zero point, counters), calibration curves and the measurement log; panel = direct
capability; refusals; render determinism; evidence PNGs; contract entries."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import shimadzu_tocl as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'tocl_01'
OUT = ROOT / 'outputs/panel_ui/shimadzu_tocl'
CONTRACT = ROOT / 'assets/expansion_batch03/t3_analytical/shimadzu_tocl/asset.contract.json'
SURVEY_TASKS = ['check carrier gas flow rate', 'remove bubbles from dilution-water and HCl lines', 'regenerate the TC catalyst',
                'TC blank check before high-sensitivity measurement', 'turn ON needle rinse (ASI-L)', 'set number of syringe washes',
                'start up (standalone)', 'shut down (standalone)']
DONE_TASKS = set()
KEY_SEEN = set()
LCD = rt.PANEL_SPEC['displays']['lcd']
CAPTURE = Path(tempfile.mkdtemp(prefix='tocl_panel_'))
FURNACE_K = 953.15


def row_image(text, inverse):
    """One text row drawn alone (as the TFT draws it), 9 px high with the text at y = 1."""
    from PIL import Image, ImageDraw
    bg, fg = tuple(LCD['colors']['background']), tuple(LCD['colors']['text'])
    img = Image.new('RGB', (LCD['size'][0], 10), bg)
    dr = ImageDraw.Draw(img)
    if inverse:
        dr.rectangle([0, 0, LCD['size'][0] - 1, 9], fill=fg)
    pu._text(dr, 2, 1, text, 1, bg if inverse else fg)
    return np.asarray(img)


class Rig:
    """A configured CSH (plant model) with the agent at the panel: main switch on, powered up and initialised (power=True), the furnace ON
    and at temperature (hot=True)."""

    def __init__(self, power=True, hot=True, method=True, plant=None):
        self.h = rt.ShimadzuToclRuntime(INST)
        self.h.panel_capture_dir = str(CAPTURE)
        orig = self.h.invoke

        def recorded(cap, **a):
            out = orig(cap, **a)
            KEY_SEEN.add(cap)
            return out
        self.h.invoke = recorded
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, **(plant or {})))
        if method:
            self.call(self.A, 'method.configure', **rt.METHOD_EXAMPLE)
        if power:
            assert self.press('button_power')
            self.step(61.0)
            assert self.screen() == 'initial', self.screen()
        if power and hot:
            self.call(self.A, 'furnace.configure', **rt.FURNACE_EXAMPLE)
            self.step(2.0)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'm{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def step(self, s):
        self.h.step(float(s))

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self):
        """Text rows shown now (right-stripped, empty rows dropped), from the frame and from the pixels (every row must agree)."""
        d = self.frame()['displays']['lcd']
        img = self.h.panel.render('lcd')
        for i, (text, inv) in enumerate(zip(d['shown'], d['inverse'])):
            y = 2 + i * 9
            assert np.array_equal(img[y:y + 8], row_image(text, inv)[1:9]), (i, text)
        return [x.rstrip() for x in d['lines'] if x.strip()]

    def text(self):
        return '\n'.join(self.lines())

    def line(self, start):
        """The row that starts with this label (its value columns squeezed)."""
        for x in self.lines():
            if x.startswith(start):
                return ' '.join(x.split())
        raise AssertionError((start, self.lines()))

    def item(self):
        m = self.frame()['ui']['menu']
        return m and m['item']

    def message(self):
        m = self.frame()['ui']['message']
        return m and m['text']

    def rejection(self):
        return self.frame()['ui']['last_gesture']['rejection']

    # gestures
    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='main_switch', position=pos)['accepted']

    def type(self, text):
        for ch in text:
            assert self.press('key_decimal' if ch == '.' else f'key_{ch}'), ch

    def to(self, item_id, limit=12):
        """Arrow keys until the cursor bar is on this item."""
        for key in ('key_down', 'key_up'):
            for _ in range(limit):
                if self.item() == item_id:
                    return True
                if not self.press(key):
                    break
        assert self.item() == item_id, (self.item(), item_id)
        return True

    def maintenance(self, item_id, wait=True):
        """F5 Maintenance, cursor to the item, [NEXT]; wait until the message closes."""
        if self.screen() != 'maint':
            assert self.press('key_f5')
        self.to(item_id)
        ok = self.press('key_next')
        if ok and wait:
            self.step(self.val('maintenance.remaining_s') + 0.01)
        return ok

    def load(self, kind='sample', **changes):
        if self.val('sample.loaded'):
            self.call(self.E, 'sample.unload', recipient_id='rack')
        a = copy.deepcopy(rt.BLANK_EXAMPLE if kind == 'blank' else rt.STANDARD_EXAMPLE if kind == 'standard' else rt.SAMPLE_EXAMPLE)
        a.update(changes)
        a['kind'] = kind
        self.call(self.E, 'sample.load', **a)

    def run(self, limit=400):
        """START on the panel and step until the analysis ends."""
        assert self.press('button_start'), (self.rejection(), self.message())
        for _ in range(limit):
            if self.val('measurement.phase') != 'running':
                break
            self.step(0.25)
        return self.val('measurement.phase')


class SurveyProcedures(unittest.TestCase):
    def test_check_carrier_gas_flow_rate(self):
        r = Rig()
        self.assertTrue(r.press('key_f4'))
        self.assertEqual((r.screen(), r.lines()[0]), ('cond_unit', 'Conditions - Unit'))
        self.assertTrue(r.press('key_f5'))
        self.assertEqual(r.lines()[:2], ['Conditions - Others', 'Carrier Gas Flow Rate' + ' ' * 46 + '150.0 mL/min'])
        self.assertAlmostEqual(r.val('monitor.carrier_flow_m3_s'), 150e-6 / 60)
        self.assertTrue(r.press('key_f1'))
        self.assertEqual(r.screen(), 'initial')
        self.assertTrue(r.press('key_f3'))                                       # the same reading on the Background Monitor
        self.assertEqual(r.line('Carrier Flow Rate'), 'Carrier Flow Rate 150.0 mL/min OK')
        dry = Rig(plant=dict(carrier_ready=False))
        dry.press('key_f3')
        self.assertEqual(dry.line('Carrier Flow Rate'), 'Carrier Flow Rate 0.0 mL/min NG')
        DONE_TASKS.add('check carrier gas flow rate')

    def test_remove_bubbles(self):
        r = Rig()
        before = r.val('maintenance.counters')
        self.assertTrue(r.press('key_f5'))
        self.assertEqual(r.lines()[:2], ['Maintenance', 'Bubble Removal'])
        self.assertTrue(r.press('key_next'))
        self.assertEqual(r.screen(), 'maint_busy')
        self.assertEqual(r.lines()[1:3], ['Bubble removal in progress. Please wait.', 'Remaining' + ' ' * 17 + '60 s'])
        self.assertFalse(r.press('button_start'))                                # busy
        r.step(60.01)
        self.assertEqual((r.screen(), r.val('maintenance.phase')), ('maint', 'succeeded'))
        c = r.val('maintenance.counters')
        self.assertAlmostEqual(c['diluent_used_m3']['value'] - before['diluent_used_m3']['value'], rt.BUBBLE_DILUENT_M3)
        self.assertAlmostEqual(c['acid_used_m3']['value'] - before['acid_used_m3']['value'], rt.BUBBLE_ACID_M3)
        self.assertTrue(r.press('key_f1'))
        self.assertEqual(r.screen(), 'initial')
        ledger = r.call(r.E, 'product.read')
        self.assertLess(abs(ledger['volume_m3']['residual']), 1e-15)
        DONE_TASKS.add('remove bubbles from dilution-water and HCl lines')

    def test_regenerate_the_tc_catalyst(self):
        cold = Rig(hot=False)
        self.assertFalse(cold.maintenance('catalyst'))
        self.assertEqual((cold.message(), cold.rejection()), ('Oven temperature too low.', 'OVEN_TEMPERATURE_LOW'))
        r = Rig()
        self.assertTrue(r.maintenance('catalyst', wait=False))
        self.assertEqual(r.lines()[1], 'TC Catalyst regeneration in progress. Please wait.')
        r.step(300.01)
        self.assertEqual((r.screen(), r.val('maintenance.item'), r.val('maintenance.phase')), ('maint', 'catalyst_regeneration', 'succeeded'))
        self.assertAlmostEqual(r.val('maintenance.counters')['combustion_injection_m3']['value'], rt.REGEN_ACID_M3)
        DONE_TASKS.add('regenerate the TC catalyst')

    def test_tc_blank_check(self):
        r = Rig()
        r.press('key_f5')
        r.to('blank')
        self.assertTrue(r.press('key_next'))
        r.type('2')
        self.assertTrue(r.press('key_next'))
        self.assertEqual(r.screen(), 'blank_prompt')
        self.assertFalse(r.press('button_start'))                                # catalyst type Regular: refused
        self.assertEqual(r.message(), 'Set the catalyst type to High.')
        r.press('key_f1')
        r.press('key_f1')
        r.press('key_f4')                                                        # Conditions: catalyst type High
        r.to('catalyst')
        self.assertTrue(r.press('key_f2'))
        self.assertEqual(r.line('Catalyst'), 'Catalyst High')
        r.press('key_f1')
        r.press('key_f5')
        r.to('blank')
        r.press('key_next')
        r.type('2')
        r.press('key_next')
        self.assertEqual(r.line('Sets x Injections'), 'Sets x Injections 2 x 5')
        diluent = r.val('maintenance.counters')['diluent_used_m3']['value']
        self.assertTrue(r.press('button_start'))
        self.assertEqual((r.screen(), r.lines()[1]), ('maint_busy', 'TC Blank Check in progress.'))
        self.assertEqual(r.val('maintenance.remaining_s'), 10 * rt.BLANK_CHECK_INJECTION_S)
        r.step(10 * rt.BLANK_CHECK_INJECTION_S + 0.01)
        self.assertEqual((r.screen(), r.val('maintenance.blank_check_area_s')), ('maint', 0.0))
        self.assertAlmostEqual(r.val('maintenance.counters')['diluent_used_m3']['value'] - diluent, 10 * rt.METHOD_EXAMPLE['injection_volume_m3'])
        self.assertEqual(r.line('TC Blank Check'), 'TC Blank Check last 0.0000 s')
        DONE_TASKS.add('TC blank check before high-sensitivity measurement')

    def test_needle_rinse_without_the_autosampler(self):
        """ASI-L is not connected (autosampler.run unsupported): the item shows it and [Select] does nothing."""
        r = Rig()
        r.press('key_f4')
        r.to('needle_rinse')
        self.assertEqual(r.line('Needle Rinse'), 'Needle Rinse --- (ASI-L not connected)')
        self.assertFalse(r.press('key_f2'))
        self.assertIn('ASI-L', r.frame()['ui']['unsupported'])
        self.assertFalse(r.press('key_enter'))
        self.assertTrue(r.press('key_f1'))
        self.assertEqual(r.screen(), 'initial')
        DONE_TASKS.add('turn ON needle rinse (ASI-L)')

    def test_set_number_of_syringe_washes(self):
        r = Rig()
        self.assertTrue(r.press('key_f4'))
        self.assertTrue(r.press('key_f4'))
        self.assertEqual(r.lines()[0], 'Measurement Conditions')
        r.to('washes')
        r.type('3')
        self.assertTrue(r.press('key_enter'))
        self.assertEqual((r.val('settings.syringe_washes'), r.line('# of Syringe Wash')), (3, '# of Syringe Wash 3'))
        r.type('12')
        self.assertFalse(r.press('key_enter'))                                   # 0-9 only
        self.assertEqual((r.message(), r.val('settings.syringe_washes')), ('Out of range.', 3))
        self.assertTrue(r.press('key_f1'))
        self.assertEqual(r.screen(), 'initial')
        # each wash draws one original aliquot through the syringe to waste
        r.load('blank')
        v0 = r.val('truth.ledger')['volume_m3']['sample']
        self.assertEqual(r.run(), 'succeeded')
        aliquot = rt.METHOD_EXAMPLE['injection_volume_m3']
        self.assertAlmostEqual(v0 - r.val('truth.ledger')['volume_m3']['sample'], 2 * aliquot * (1 + 3), places=15)   # TOC: TC and IC aliquots
        ledger = r.call(r.E, 'product.read')
        self.assertLess(abs(ledger['volume_m3']['residual']), 1e-15)
        DONE_TASKS.add('set number of syringe washes')

    def test_start_up(self):
        r2 = Rig(power=False)
        self.assertTrue(r2.switch('off'))
        self.assertFalse(r2.press('button_power'))                               # main switch off: the front key does nothing
        self.assertFalse(r2.val('power.enabled'))
        self.assertTrue(r2.switch('on'))
        self.assertEqual(r2.screen(), 'off')
        self.assertTrue(r2.press('button_power'))
        self.assertEqual((r2.screen(), r2.lines()[1:3]), ('init', ['Initializing...', 'Please wait.']))
        self.assertFalse(r2.press('key_f4'))                                      # keys wait for the initialisation
        r2.step(61.0)
        self.assertEqual(r2.screen(), 'initial')
        self.assertEqual(r2.line('Status'), 'Status NOT READY')
        r2.press('key_f4')
        r2.to('furnace')
        self.assertTrue(r2.press('key_f2'))                                       # electric furnace ON
        self.assertEqual(r2.val('furnace.target_k'), FURNACE_K)
        r2.press('key_f1')
        r2.step(5.0)
        self.assertEqual((r2.line('Status'), r2.frame()['displays']['ready_lamp']['on']), ('Status NOT READY', False))   # baseline not settled
        r2.press('key_f3')
        self.assertEqual([r2.line(x) for x in ('Electric Furnace', 'Baseline Noise', 'READY')],
                         ['Electric Furnace 680 C (Set 680 C) OK', 'Baseline Noise NG', 'READY OFF'])
        r2.step(rt.BASELINE_SETTLE_S)
        self.assertEqual((r2.line('Baseline Noise'), r2.line('READY')), ('Baseline Noise OK', 'READY ON'))
        self.assertTrue(r2.frame()['displays']['ready_lamp']['on'])
        r2.press('key_f1')
        self.assertEqual(r2.line('Status'), 'Status READY')
        DONE_TASKS.add('start up (standalone)')

    def test_shut_down(self):
        r = Rig()
        r.load('blank')
        for _ in range(3):                                                       # three DI-water blanks first
            self.assertEqual(r.run(), 'succeeded')
        self.assertTrue(r.press('button_power'))
        self.assertEqual((r.screen(), r.lines()[1]), ('shutdown_confirm', 'Shut down the instrument?'))
        self.assertTrue(r.press('key_f1'))                                       # No
        self.assertEqual((r.screen(), r.val('power.enabled')), ('initial', True))
        r.press('button_power')
        self.assertTrue(r.press('key_f6'))                                       # Yes
        self.assertEqual(r.screen(), 'shutdown')
        self.assertEqual(r.line('Power off in'), 'Power off in 30 min')
        self.assertFalse(r.press('button_start'))
        r.step(600.0)
        self.assertLess(r.val('furnace.actual_k'), 400.0)                        # heater off: cooling
        self.assertEqual(r.line('Power off in'), 'Power off in 20 min')
        r.step(1200.01)
        self.assertEqual((r.val('power.enabled'), r.screen()), (False, 'off'))
        self.assertTrue(r.switch('off'))
        self.assertFalse(r.val('power.mains'))
        DONE_TASKS.add('shut down (standalone)')


class Analyses(unittest.TestCase):
    def test_start_stop_results_and_residue(self):
        r = Rig()
        self.assertFalse(r.press('button_start'))                                # nothing connected
        self.assertEqual(r.message(), 'No sample connected.')
        r.load('sample')
        self.assertFalse(r.press('button_start'))
        self.assertEqual(r.message(), 'Measure a blank with these conditions first.')
        r.load('blank')
        self.assertEqual(r.run(), 'succeeded')
        r.load('sample')
        assert r.press('button_start')
        r.step(0.05)
        self.assertEqual(r.line('Status'), 'Status ANALYZING')
        self.assertTrue(r.line('Progress').startswith('Progress TC 0 / 2'))
        op = r.h.operation_id
        self.assertEqual(r.A.poll(op)['status'], 'running')                      # the agent that pressed START owns the run
        r.step(0.15)
        self.assertTrue(r.press('button_stop'))
        self.assertEqual((r.val('measurement.phase'), r.line('Last run')), ('cancelled', 'Last run: CANCELLED'))
        self.assertFalse(r.press('button_start'))
        self.assertEqual(r.message(), 'Residue in the flow line: run Residue Removal.')
        self.assertTrue(r.maintenance('residue'))
        self.assertEqual(r.val('syringe.position_m'), 0.0)
        r.press('key_f1')
        self.assertEqual(r.run(), 'succeeded')
        res = r.A.invoke(instance_id=INST, capability_id='result.read', arguments={}, request_id='rr')['result']
        self.assertEqual(r.line('  TC'), 'TC ---')                             # no calibration: no concentration
        self.assertEqual(res['qc'][:1], ['TC_calibration_unavailable'])
        ledger = r.call(r.E, 'product.read')
        self.assertLess(max(abs(v) for v in ledger['carbon_kg']['residual'].values()), 1e-16)

    def test_power_and_main_switch_interrupt(self):
        r = Rig()
        r.load('blank')
        r.press('button_start')
        r.step(0.05)
        self.assertTrue(r.switch('off'))
        self.assertEqual((r.val('measurement.phase'), r.val('safety.warning'), r.screen()), ('cancelled', 'POWER_OFF', 'off'))
        self.assertFalse(r.press('key_f4'))
        r.switch('on')
        r.press('button_power')
        self.assertEqual(r.screen(), 'init')
        self.assertEqual(r.h.power_epoch, 2)


class MenuFunctions(unittest.TestCase):
    def test_conditions_and_measurement_conditions(self):
        r = Rig()
        r.press('key_f4')
        keys = r.lines()[-2:]
        self.assertEqual((keys[0].split(), keys[1].split()), (['Return', 'Select', 'Unit', 'Meas.', 'Others'], ['Management', 'Cond.']))
        r.to('furnace')
        self.assertTrue(r.press('key_f2'))                                       # OFF: target back to ambient
        self.assertEqual(r.val('furnace.target_k'), rt.PLANT_EXAMPLE['ambient_k'])
        self.assertTrue(r.press('key_enter'))                                    # ENTER + arrows also edit it
        r.press('key_down')
        self.assertTrue(r.press('key_enter'))
        self.assertEqual(r.val('furnace.target_k'), FURNACE_K)
        r.to('tn_power')
        self.assertFalse(r.press('key_f2'))
        self.assertIn('TNM-L', r.frame()['ui']['unsupported'])
        r.press('key_f4')
        self.assertEqual(r.line('Analysis'), 'Analysis TOC')
        r.to('mode')
        for want in ('NPOC', 'TC', 'IC', 'TOC'):
            self.assertTrue(r.press('key_f2'))
            self.assertEqual((r.val('method.mode'), r.line('Analysis')), (want, 'Analysis ' + want))
        r.to('inj_volume')
        r.type('5')
        self.assertTrue(r.press('key_bs'))
        r.type('250')
        self.assertTrue(r.press('key_enter'))
        self.assertEqual((r.h.method['injection_volume_m3'], r.line('Injection Volume')), (250e-9, 'Injection Volume 250 uL'))
        r.type('5')
        self.assertFalse(r.press('key_enter'))                                   # below 10 uL: refused, the field stays open
        self.assertEqual(r.message(), 'Out of range.')
        self.assertTrue(r.press('key_enter'))                                    # ENTER keeps the old value
        self.assertEqual(r.h.method['injection_volume_m3'], 250e-9)
        r.to('injections')
        r.type('3')
        r.press('key_enter')
        self.assertEqual(r.h.method['repeats'], 3)
        r.to('dilution')
        r.type('1.5')
        self.assertFalse(r.press('key_enter'))                                   # 1 (none) or 2-50
        self.assertEqual((r.message(), r.rejection(), r.h.method['dilution_factor']), ('Out of range.', 'DILUTION_RANGE', 1.0))
        r.type('5')
        self.assertTrue(r.press('key_enter'))
        self.assertEqual(r.h.method['dilution_factor'], 5.0)
        r.to('sparge')
        r.type('0.3')
        r.press('key_enter')
        self.assertEqual(r.h.method['sparging_s'], 0.3)
        r.press('key_f1')
        self.assertEqual(r.line('Inj. Volume'), 'Inj. Volume / No. / Dil. 250 uL x3 Dil. 5.0')
        self.assertTrue(r.press('key_method'))                                   # MTHD: the measurement conditions
        self.assertEqual(r.screen(), 'meas_cond')

    def test_ic_reagent_syringe_zero_and_counters(self):
        r = Rig()
        for level in (25, 50, 75, 100):
            self.assertTrue(r.maintenance('ic_regen'))
            self.assertEqual(r.line('IC regeneration'), f'IC regeneration {level} %')
        self.assertEqual(r.val('maintenance.ic_reagent_level'), 1.0)
        self.assertTrue(r.maintenance('syringe_zero', wait=False))
        self.assertEqual(r.lines()[1], 'Detecting the syringe zero point. Please wait.')
        self.assertTrue(r.press('button_stop'))                                  # STOP aborts the item
        self.assertEqual((r.val('maintenance.phase'), r.screen()), ('cancelled', 'maint'))
        self.assertTrue(r.maintenance('syringe_zero'))
        self.assertTrue(r.val('maintenance.syringe_zeroed'))
        r.to('counters')
        self.assertTrue(r.press('key_next'))
        self.assertEqual(r.screen(), 'counters')
        strokes = r.val('maintenance.counters')['syringe_strokes']['value']
        self.assertEqual(r.line('Syringe Stroke Number'), f'Syringe Stroke Number {strokes:.0f} / ---')
        r.to('syringe_strokes')
        r.type('1')
        self.assertTrue(r.press('key_enter'))
        self.assertEqual(r.val('maintenance.counters')['syringe_strokes']['limit'], 1.0)
        self.assertEqual(r.line('Syringe Stroke Number'), f'Syringe Stroke Number {strokes:.0f} / 1 OVER')
        r.to('diluent_used_m3')
        r.type('2.5')
        r.press('key_enter')
        self.assertAlmostEqual(r.val('maintenance.counters')['diluent_used_m3']['limit'], 2.5e-6)
        r.press('key_f1')
        r.press('key_f1')
        self.assertEqual(r.line('Counter Over-'), 'Counter Over- Syringe Stroke Number')
        r.press('key_f5')
        r.to('counters')
        r.press('key_next')
        r.to('syringe_strokes')
        self.assertTrue(r.press('key_f2'))                                       # Clear after replacing the plunger tip
        self.assertEqual(r.val('maintenance.counters')['syringe_strokes'], dict(value=0.0, limit=1.0, over=False))
        r.type('0')
        r.press('key_enter')                                                     # 0: no warning
        self.assertIsNone(r.val('maintenance.counters')['syringe_strokes']['limit'])
        r.press('key_f1')
        r.press('key_f1')
        self.assertNotIn('Counter Over-', r.text())

    def test_calibration_curves_and_measurement_log(self):
        r = Rig()
        h = r.h
        r.load('blank')
        r.run()
        for ch in ('TC', 'IC'):                                                 # two curves per channel fitted by the host from observed standards
            src = rt.IC_STANDARD_EXAMPLE if ch == 'IC' else rt.STANDARD_EXAMPLE
            obs = [dict(reference_id=src['template_id'], reference_channel=ch, injection_volume_m3=v, dilution_factor=1., peak_area_s=a)
                   for v, a in ((1e-8, 0.01), (1e-7, 0.1), (1e-6, 1.0))]
            for n in ('a', 'b'):
                r.call(r.A, 'calibration.fit', curve_id=f'{ch}_{n}', channel=ch, observations=obs, accept_source_channel_transfer=False,
                       accept_same_response_conditions=True)
        self.assertTrue(r.press('key_calibration'))
        self.assertEqual(r.line('TC Calibration Curve'), 'TC Calibration Curve ---')
        r.to('tc')
        for want in ('TC_a', 'TC_b', ''):
            self.assertTrue(r.press('key_f2'))
            self.assertEqual(h._selected['TC'], want)
        r.press('key_f2')
        r.to('ic')
        r.press('key_f2')
        self.assertEqual((h._selected['TC'], h._selected['IC'], r.line('IC Calibration Curve')), ('TC_a', 'IC_a', 'IC Calibration Curve IC_a'))
        r.to('npoc')
        self.assertTrue(r.press('key_f2'))                                       # no NPOC curve stored: stays none
        self.assertEqual(h._selected['NPOC'], '')
        r.press('key_f1')
        for _ in range(2):
            r.load('blank')
            r.run()
        self.assertTrue(r.press('key_measurement'))
        self.assertEqual((r.screen(), r.lines()[0]), ('log', 'Measurement Log'))
        self.assertEqual(len([x for x in r.lines() if x.lstrip()[:1].isdigit()]), 3)
        self.assertEqual(r.lines()[1], '   3  TOC   blank     ---')
        self.assertFalse(r.press('key_f2'))                                      # Data Save: no USB stick
        self.assertIn('USB', r.frame()['ui']['unsupported'])
        r.to('rec1')
        self.assertTrue(r.press('key_f5'))                                       # Clear Select
        self.assertEqual(sorted(h._records), ['tocl_01:result:1', 'tocl_01:result:3'])
        self.assertTrue(r.press('key_f4'))                                       # Clear All
        self.assertEqual((h._records, r.lines()[1]), ({}, 'No data'))
        self.assertTrue(r.press('key_f6'))
        self.assertEqual(r.screen(), 'cal')

    def test_unsupported_keys_and_option_items(self):
        r = Rig()
        for key, word in (('key_asi', 'ASI-L'), ('key_shortcut_5', 'USB'), ('key_print', 'printer'), ('key_back', 'character')):
            self.assertFalse(r.press(key), key)
            self.assertIn(word, r.frame()['ui']['unsupported'], key)
        r.press('key_f3')
        self.assertFalse(r.press('key_f4'))                                      # O3 generator of the TNM-L
        self.assertEqual(r.line('O3 Generator'), 'O3 Generator --- (TNM-L not connected)')
        r.press('key_f1')
        self.assertFalse(r.press('key_left'))                                    # no documented role
        self.assertEqual(r.screen(), 'initial')


class BrochureFunctions(unittest.TestCase):
    """1.2.0 (sync audit, rule 4): automatic selection among up to three calibration curves and automatic sleep / restart (brochure p.11)."""

    def test_optimal_curve_of_three(self):
        r = Rig()
        h = r.h
        r.load('blank')
        self.assertEqual(r.run(), 'succeeded')
        ref = rt.STANDARD_EXAMPLE['template_id']
        for cid, vols, gain in (('TC_low', (1e-8, 2e-8, 4e-8), 1e8), ('TC_high', (1e-7, 1e-6, 2e-6), 1.2e8)):   # 10 mg/L standard
            obs = [dict(reference_id=ref, reference_channel='TC', injection_volume_m3=v, dilution_factor=1., peak_area_s=0.01 * v * gain) for v in vols]
            r.call(r.A, 'calibration.fit', curve_id=cid, channel='TC', observations=obs, accept_source_channel_transfer=False, accept_same_response_conditions=True)
        self.assertTrue(r.press('key_calibration'))
        r.to('tc')
        r.press('key_f2')
        r.to('tc2')
        self.assertTrue(r.press('key_f2'))                                       # curve 2: the other stored TC curve
        self.assertEqual((h._selected['TC'], r.val('calibration.curve_slots')['TC']), ('TC_low', ['TC_low', 'TC_high', '']))
        self.assertEqual(r.line('TC Calibration Curve 2'), 'TC Calibration Curve 2 TC_high')
        r.to('tc3')
        self.assertTrue(r.press('key_f2'))                                       # no third TC curve stored: stays none
        self.assertEqual(r.val('calibration.curve_slots')['TC'][2], '')
        self.assertEqual(r.call(r.A, 'calibration.set_slot', expect='rejected', channel='TC', slot=3, curve_id='TC_low')['error']['code'], 'INVALID_ARGUMENT')
        r.press('key_f1')
        for inj, want in ((5e-7, 'TC_high'), (2e-8, 'TC_low')):                 # 5e-9 kg carbon: only the high range holds it; 2e-10 kg: the low one
            r.call(r.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, injection_volume_m3=inj))
            r.load('standard')
            self.assertEqual(r.run(), 'succeeded')
            tc = [q for q in h.assays if q['channel'] == 'TC'][0]
            used = json.loads(tc['calibration_json'])
            self.assertEqual((used['curve_id'], tc['quality']), (want, 'estimated_from_observed_calibration'), inj)
            self.assertAlmostEqual(tc['concentration_kg_m3'], (tc['peak_area_s'] - used['intercept_s']) / used['slope_s_kg'] / inj, places=12)

    def test_auto_sleep_and_restart(self):
        r = Rig()
        r.press('key_f4')
        r.press('key_f5')
        self.assertEqual(r.lines()[0], 'Conditions - Others')
        r.to('auto_sleep')
        self.assertTrue(r.press('key_f2'))
        r.to('sleep_after')
        r.type('2')
        self.assertTrue(r.press('key_enter'))
        r.to('auto_restart')
        self.assertTrue(r.press('key_f2'))
        r.to('restart_hour')
        r.type('0')
        self.assertTrue(r.press('key_enter'))
        r.to('restart_minute')
        r.type('30')
        self.assertTrue(r.press('key_enter'))
        self.assertEqual(r.val('sleep.settings'), dict(auto_sleep=True, sleep_after_s=120.0, auto_restart=True, restart_hour=0, restart_minute=30))
        self.assertEqual(r.line('Auto Sleep'), 'Auto Sleep ON')
        r.press('key_f1')
        r.load('blank')
        self.assertEqual(r.run(), 'succeeded')                                   # the idle time counts from the end of the analysis
        end = r.h._last_activity_s
        r.step(end + 119.0 - r.h.time_s)
        self.assertFalse(r.val('sleep.active'))
        hot = r.val('furnace.actual_k')
        r.step(2.0)
        self.assertEqual((r.val('sleep.active'), r.screen(), r.val('monitor.ready'), r.val('monitor.carrier_flow_m3_s')), (True, 'sleep', False, 0.0))
        self.assertEqual(r.line('Automatic restart'), 'Automatic restart at 00:30')
        self.assertFalse(r.press('button_start'))                                # analyses refused in sleep mode
        r.load('blank')
        self.assertEqual(r.call(r.A, 'measurement.start', expect='rejected')['error']['code'], 'BUSY')
        r.step(300.0)
        self.assertLess(r.val('furnace.actual_k'), hot - 100.0)                 # furnace heating off
        r.step(30 * 60.0 - r.h.time_s + 1.0)                                    # 00:30: automatic restart
        self.assertEqual((r.val('sleep.active'), r.screen()), (False, 'initial'))
        self.assertFalse(r.val('monitor.baseline_ok'))                          # the baseline settles again
        r.step(130.0)                                                            # idle again: back to sleep, F1 Restart by hand
        self.assertEqual(r.screen(), 'sleep')
        self.assertTrue(r.press('key_f1'))
        self.assertEqual((r.val('sleep.active'), r.screen()), (False, 'initial'))
        self.assertEqual(r.call(r.A, 'sleep.restart', expect='rejected')['error']['code'], 'NOT_SLEEPING')
        r.call(r.A, 'sleep.configure', **dict(r.val('sleep.settings'), auto_sleep=False))
        r.step(rt.BASELINE_SETTLE_S)
        self.assertEqual((r.val('sleep.active'), r.val('monitor.ready')), (False, True))


class Refusals(unittest.TestCase):
    def test_refused_arguments_access_and_off_state(self):
        r = Rig(power=False)
        for cap, args in (('panel.press', dict(input_id='key_nope')), ('panel.press', dict(input_id='key_f1', duration_s=0.0)),
                          ('panel.switch', dict(input_id='main_switch', position='middle')), ('panel.switch', dict(input_id='key_f1', position='on'))):
            self.assertEqual(r.call(r.A, cap, expect='rejected', **args)['error']['code'], 'INVALID_ARGUMENT', (cap, args))
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        for key in ('key_f4', 'button_start', 'key_next'):
            self.assertFalse(r.press(key))                                       # off: only the power key and the main switch act
        self.assertEqual(r.screen(), 'off')
        r.call(r.A, 'maintenance.run', item='bubble_removal', expect='rejected')
        r.call(r.A, 'power.shutdown', expect='rejected')
        self.assertEqual(r.call(r.A, 'settings.configure', catalyst='ultra', syringe_washes=0, expect='rejected')['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'maintenance.tc_blank_check', sets=11, expect='rejected')['error']['code'], 'INVALID_ARGUMENT')
        r.call(r.E, 'cover.set', opened=True)
        self.assertFalse(r.press('button_power'))
        self.assertEqual(r.rejection(), 'POWER_INTERLOCK')


class Equivalence(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(power=False, method=False), Rig(power=False, method=False)
        a.press('button_power')
        a.step(61.0)
        a.press('key_f4')
        a.to('furnace')
        a.press('key_f2')
        a.to('catalyst')
        a.press('key_f2')
        a.press('key_f4')
        a.to('inj_volume')
        a.type('100')
        a.press('key_enter')
        a.to('washes')
        a.type('2')
        a.press('key_enter')
        a.press('key_f1')
        a.maintenance('bubble', wait=False)
        b.call(b.A, 'power.set', enabled=True)
        b.step(61.0)
        b.call(b.A, 'furnace.configure', temperature_k=FURNACE_K)
        b.call(b.A, 'settings.configure', catalyst='high', syringe_washes=0)
        b.call(b.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, injection_volume_m3=100e-9))
        b.call(b.A, 'settings.configure', catalyst='high', syringe_washes=2)
        b.call(b.A, 'maintenance.run', item='bubble_removal')
        for r in (a, b):
            r.step(61.0)
            r.load('blank')
        a.press('key_f1')
        a.press('button_start')
        b.call(b.A, 'measurement.start')
        for r in (a, b):
            for _ in range(100):
                if r.val('measurement.phase') != 'running':
                    break
                r.step(0.25)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))
        self.assertEqual(a.h.method, b.h.method)


class Rendering(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        def run():
            r = Rig()
            r.press('key_f4')
            r.press('key_f4')
            cap = r.call(r.A, 'panel.capture', display_id='lcd')
            return cap, Path(cap['image']['path']).read_bytes()
        (c1, p1), (_, p2) = run(), run()
        self.assertEqual(hashlib.sha256(p1).hexdigest(), hashlib.sha256(p2).hexdigest())
        self.assertEqual(hashlib.sha256(p1).hexdigest(), c1['image']['sha256'])
        self.assertEqual((c1['image']['width'], c1['image']['height']), (480, 240))
        caps = Rig(power=False).A.describe(instance_id=INST)['capabilities']
        self.assertIn('panel.press', caps)
        self.assertNotIn('panel.frame', caps)

    def test_evidence_renders(self):
        """home (Initial Display after a blank) / menu (Measurement Conditions) / error (Maintenance: Oven temperature too low.)."""
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        r.step(rt.BASELINE_SETTLE_S)
        r.load('blank')
        r.run()
        panel = r.h.panel
        r.lines()
        shots = {'home': panel.save_png('lcd', OUT / 'home.png')}
        r.press('key_f4')
        r.press('key_f4')
        r.to('washes')
        r.lines()
        shots['menu'] = panel.save_png('lcd', OUT / 'menu.png')
        r.press('key_f1')
        cold = Rig(hot=False)
        cold.maintenance('catalyst')
        cold.lines()
        shots['error'] = cold.h.panel.save_png('lcd', OUT / 'error.png')
        s = Rig()
        s.call(s.A, 'sleep.configure', auto_sleep=True, sleep_after_s=60.0, auto_restart=True, restart_hour=8, restart_minute=0)
        s.step(60.0)
        s.lines()
        shots['sleep'] = s.h.panel.save_png('lcd', OUT / 'sleep.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_shimadzu_tocl_contract_metadata()
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for name, cap in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'errors'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        spec = rt.PANEL_SPEC
        for k, v in list(spec['inputs'].items()) + list(spec['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        for cid in ('main_switch', 'ready_lamp'):
            self.assertEqual(c['components'][cid]['geometry_status'], 'absent', cid)
        self.assertEqual(set(pu.spec_capabilities(spec)) - set(c['capabilities']), set())
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', '0.1'))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        from asset_tool import validate_contract
        self.assertEqual(validate_contract(c, ROOT, check_files=False), [])


class SweepOwnershipAndFaults(unittest.TestCase):
    """Sweep 2026-10-19 (1.3.0): another agent cannot end an analysis or a maintenance item it did not start (the main switch is a hard
    switch); the main-unit error codes and messages of the TOC-L error list by evaluator fault.inject / fault.clear."""
    def two(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        return r, r.h.session('agent_b')

    def test_stop_and_power_key_of_another_agent_are_refused(self):
        r, B = self.two()
        r.load('blank')
        self.assertTrue(r.press('button_start'))
        op = r.h.operation_id
        before = r.val('measurement.phase')
        self.assertFalse(r.call(B, 'panel.press', input_id='button_stop', duration_s=0.1)['accepted'])
        self.assertEqual((r.rejection(), r.message()), ('ACCESS_DENIED', 'Run of another user'))   # the refusal on the LCD
        for cap, args in (('measurement.stop', {}), ('power.set', {'enabled': False}), ('power.toggle', {})):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual((before, r.val('measurement.phase'), r.A.poll(op)['status']), ('running', 'running', 'running'))
        self.assertTrue(r.call(B, 'panel.switch', input_id='main_switch', position='off')['accepted'])   # hard switch: a power loss
        self.assertEqual((r.val('measurement.phase'), r.val('power.enabled')), ('cancelled', False))

    def test_maintenance_item_of_another_agent(self):
        r, B = self.two()
        self.assertTrue(r.maintenance('bubble', wait=False))
        self.assertEqual(r.call(B, 'maintenance.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.val('maintenance.phase'), 'running')
        r.call(r.A, 'maintenance.stop')
        self.assertNotEqual(r.val('maintenance.phase'), 'running')

    def test_error_code_stops_and_needs_a_power_cycle(self):
        r = Rig()
        r.load('blank')
        self.assertTrue(r.press('button_start'))
        r.step(0.5)
        r.call(r.E, 'fault.inject', code='main_fan_stop')
        self.assertEqual((r.val('measurement.phase'), r.val('safety.warning')), ('failed', 'INSTRUMENT_ERROR'))
        self.assertEqual(r.screen(), 'error')
        self.assertIn('Error!! Main Fan Stop', r.lines())
        self.assertFalse(r.press('key_f5'))                                       # keys do nothing while the error is shown
        self.assertEqual(r.call(r.A, 'measurement.start', expect='rejected')['error']['code'], 'INSTRUMENT_ERROR')
        for _ in range(2):                                                        # off / on: the cause is still there, the error shows again
            r.switch('off')
            r.switch('on')
            r.press('button_power')
            r.step(61.0)
        self.assertEqual(r.screen(), 'error')
        r.call(r.E, 'fault.clear', code='main_fan_stop')                         # repaired: off / on clears it
        r.switch('off')
        r.switch('on')
        r.press('button_power')
        r.step(61.0)
        self.assertEqual(r.screen(), 'initial')
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='flash_rom')['error']['code'], 'ACCESS_DENIED')   # evaluator only

    def test_error_list_messages(self):
        r = Rig()
        r.call(r.E, 'fault.inject', code='backup_battery')
        self.assertIn('Exchange the back-up battery.', r.lines())                 # shown on the Initial Display, operation goes on
        r.call(r.E, 'fault.inject', code='cf_card')
        r.load('blank')
        self.assertFalse(r.press('button_start'))
        self.assertEqual(r.message(), 'Unable to access the CF card.')
        r.call(r.E, 'fault.clear', code='cf_card')
        r.call(r.E, 'fault.inject', code='syringe_zero_failed')
        self.assertFalse(r.maintenance('syringe_zero', wait=False))
        self.assertEqual(r.message(), 'Syringe Zero Point Correction failed.')
        r.call(r.E, 'fault.clear', code='syringe_zero_failed')
        r.call(r.E, 'fault.clear', code='backup_battery')
        self.assertNotIn('Exchange the back-up battery.', r.lines())


class ZKeyCoverage(unittest.TestCase):
    def test_every_menu_function_ran(self):
        if len(KEY_SEEN) > 10:
            self.assertEqual(set(rt.KEY_CAPS) - KEY_SEEN, set())


if __name__ == '__main__':
    unittest.main()
