"""front-panel tests for CHEM-091 eco_ncld822 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/eco_ncld822.py):
python3 -m pytest tests/test_panel_ui_eco_ncld822.py -q

APPROXIMATE panel (operating manual behind a customer login): the measurement page of the brochure screenshot (NO / NOx / NO2 in ppm with one
decimal) and the assumed trend, calibration and settings pages behind the icon column, plus the power switch.  Every gesture goes through the
session API (panel.touch / panel.switch); the host connects gases and steps the clock.  Themes: power and warm-up, values in ppm, start / stop
and the trend, ranges and filter, zero and span calibration, refusals (warm-up, ranges not ascending) with the state unchanged, panel = direct
capability, power gate, render determinism, evidence PNGs, contract entries."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import eco_ncld822 as rt  # noqa: E402

INST = 'ncld822_01'
SCR = 'screen'
OUT = ROOT / 'outputs/panel_ui/eco_ncld822'


class Rig:
    def __init__(self, on=True, warm=True):
        self.h = rt.EcoNcld822Runtime(INST)
        self.h.panel_capture_dir = tempfile.mkdtemp(prefix='ncld_panel_')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', noise_enabled=False)
        if on:
            assert self.switch('on')
            if warm:
                self.h.step(900.0)
                assert self.val('status.phase') == 'ready'

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='switch_power', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCR]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y):
        return self.call(self.A, 'panel.touch', display_id=SCR, x=int(x), y=int(y))['accepted']

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        ov = self.overlay()
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def msg(self):
        ov = self.overlay()
        return '\n'.join(ov['lines']) if ov and ov.get('kind') == 'dialog' else None

    def row(self, wid, text):
        w = self.widgets()[wid]
        for k, cells in enumerate(w['cells']):
            if text in cells:
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
        raise AssertionError((text, w['cells']))

    def enter(self, text):
        while self.overlay()['field']:
            self.ov('Del')
        for ch in text:
            self.ov(ch)
        return self.ov('OK')

    def png(self):
        return Path(self.call(self.A, 'panel.capture', display_id=SCR)['image']['path']).read_bytes()


def state(h):
    return json.dumps(h.read('evaluator'), sort_keys=True, default=float)


class Panel(unittest.TestCase):
    def test_power_warm_up_and_values(self):
        g = Rig(on=False)
        self.assertEqual(g.page(), ('off', 'dark'))
        self.assertFalse(g.touch(400, 240))
        self.assertTrue(g.switch('on'))
        self.assertEqual(g.page(), ('main', 'measurement'))
        self.assertIn('Warming up', g.text('status'))
        g.tap('i_meas')                                                            # not ready yet: refused
        self.assertIn('warming up', g.msg())
        g.ov('OK')
        g.h.step(900.0)
        g.call(g.A, 'inlet.connect_standard', standard_id='srm_1683b')
        g.h.step(30.0)
        no = g.val('display.no_mol_mol')
        self.assertEqual(g.text('no_value'), f'{no / rt.PPM:.1f} ppm')              # one decimal, ppm (brochure screenshot)
        self.assertEqual(g.text('no2_value'), f'{g.val("display.no2_mol_mol") / rt.PPM:.1f} ppm')
        self.assertTrue(g.switch('off'))
        self.assertEqual(g.page(), ('off', 'dark'))

    def test_start_stop_and_trend(self):
        g = Rig()
        g.call(g.A, 'inlet.connect_standard', standard_id='srm_1683b')
        g.tap('i_meas')
        self.assertEqual(g.val('measurement.phase'), 'measuring')
        self.assertEqual(g.text('i_meas'), 'Stop')
        g.h.step(60.0)
        g.tap('i_trend')
        pts = g.widgets()['trend_plot']['series'][0]['points']
        self.assertGreaterEqual(len(pts), 60)
        self.assertAlmostEqual(pts[-1][1], g.h.series[-1]['no_mol_mol'] / rt.PPM, places=9)
        g.tap('i_meas')
        self.assertEqual(g.val('measurement.phase'), 'idle')
        g.tap('i_values')
        self.assertEqual(g.page()[1], 'measurement')

    def test_ranges_and_filter(self):
        g = Rig()
        g.tap('i_set')
        g.row('set_form', 'Active range')
        g.ov('Range 4')
        self.assertEqual(g.val('range.index'), 3)
        g.row('set_form', 'Range 4')
        g.enter('2000')
        self.assertAlmostEqual(g.val('range.full_scales_mol_mol')[3], 2000e-6, places=12)
        before = state(g.h)
        g.row('set_form', 'Range 2')                                              # not ascending: refused
        g.enter('5000')
        self.assertIn('ascending', g.msg())
        g.ov('OK')
        self.assertEqual(before, state(g.h))
        g.row('set_form', 'Filter')
        g.enter('5')
        self.assertEqual(g.val('filter.time_constant_s'), 5.0)
        g.tap('i_values')
        self.assertIn('Range 4: 2000 ppm', g.text('status'))

    def test_zero_and_span_calibration(self):
        g = Rig()
        g.tap('i_cal')
        g.row('cal_form', 'Averaging window')
        g.enter('10')
        self.assertEqual(g.val('calibration.window_s'), 10.0)
        g.call(g.A, 'inlet.connect_standard', standard_id='zero_nitrogen')
        g.h.step(30.0)
        g.tap('c_zero')
        self.assertEqual(g.val('calibration.phase'), 'zeroing')
        self.assertFalse(g.widgets()['c_span']['enabled'])
        g.h.step(15.0)
        self.assertEqual(g.val('calibration.phase'), 'idle')
        g.call(g.A, 'inlet.connect_standard', standard_id='srm_2631a')
        g.h.step(30.0)
        g.tap('c_span')
        g.tap('c_cancel')                                                         # cancel keeps the old coefficients
        self.assertEqual((g.val('calibration.phase'), g.val('calibration.gain_no')), ('idle', 1.0))
        g.tap('c_span')
        g.h.step(15.0)
        self.assertEqual(g.val('calibration.phase'), 'idle')
        self.assertIn('Gain NO', g.text('cal_state'))

    def test_panel_and_direct_calls_give_the_same_state(self):
        a, b = Rig(), Rig()
        a.tap('i_set')
        a.row('set_form', 'Active range')
        a.ov('Range 2')
        a.tap('i_meas')
        b.call(b.A, 'range.select', index=1)
        b.call(b.A, 'measurement.toggle')
        a.h.step(20.0)
        b.h.step(20.0)
        self.assertEqual(state(a.h), state(b.h))

    def test_power_gate(self):
        g = Rig(on=False)
        before = state(g.h)
        self.assertFalse(g.touch(50, 100))
        self.assertFalse(g.switch('off'))
        self.assertEqual(before, state(g.h))


class CallerRules(unittest.TestCase):
    """Sync audit 2026-10-19, rule 2: a calibration started from the screen belongs to the session caller; another agent cannot cancel it or
    switch the analyser off, through the panel or directly (state unchanged)."""
    def rig(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        g.B = g.h.session('agent_b')
        g.call(g.A, 'inlet.connect_standard', standard_id='zero_nitrogen')
        g.h.step(30.0)
        g.tap('i_cal')
        g.tap('c_zero')
        self.assertEqual(g.val('calibration.phase'), 'zeroing')
        return g

    def test_a_panel_started_calibration_belongs_to_the_caller(self):
        g = self.rig()
        op = g.h.cal_op
        self.assertEqual(g.h._owners.get(op), 'agent')
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            g.B.poll(op)
        with self.assertRaises(rt.ActionRejected):
            g.B.cancel(op, 'b1')
        self.assertEqual(g.val('calibration.phase'), 'zeroing')
        self.assertEqual(g.A.cancel(op, 'a1')['status'], 'cancelled')
        self.assertEqual(g.val('calibration.phase'), 'idle')

    def test_another_agent_cannot_cancel_or_switch_off_the_calibration(self):
        g = self.rig()
        before = state(g.h)
        x, y, w, h = g.widgets()['c_cancel']['rect']
        self.assertFalse(g.call(g.B, 'panel.touch', display_id=SCR, x=x + w // 2, y=y + h // 2)['accepted'])      # B taps Cancel
        self.assertEqual(g.msg(), "Not allowed: another user's\ncalibration is running.")                    # the refusal shows on the screen
        g.ov('OK')
        self.assertEqual(g.call(g.B, 'calibration.cancel', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(before, state(g.h))
        g.tap('c_cancel')                                                                               # the owner may cancel it
        self.assertEqual(g.val('calibration.phase'), 'idle')
        g2 = self.rig()                                                                                  # the evaluator is not restricted
        g2.call(g2.E, 'calibration.cancel')
        self.assertEqual(g2.val('calibration.phase'), 'idle')

    def test_the_power_switch_is_physical(self):
        """Sweep 2026-10-19: the rocker power switch cuts the power (hard switch): another agent's switch-off is not refused; the calibration ends."""
        g = self.rig()
        self.assertTrue(g.call(g.B, 'panel.switch', input_id='switch_power', position='off')['accepted'])
        self.assertFalse(g.val('power.enabled'))
        self.assertEqual(g.val('calibration.phase'), 'idle')

    def test_another_agent_may_change_the_inlet_gas(self):
        """Sweep 2026-10-19: the gas line at the inlet is physical (not refused); the running calibration goes on with the gas that arrives."""
        g = self.rig()
        g.call(g.B, 'inlet.disconnect')
        self.assertEqual(g.val('calibration.phase'), 'zeroing')
        self.assertIsNone(g.msg())


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        g = Rig()
        g.call(g.A, 'inlet.connect_standard', standard_id='srm_1683b')
        g.tap('i_meas')
        g.h.step(120.0)
        a = g.h.panel.png_bytes(SCR)[0]
        self.assertEqual(a, copy.deepcopy(g.h).panel.png_bytes(SCR)[0])
        self.assertEqual(g.png(), a)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'home.png').write_bytes(a)
        g.tap('i_set')
        (OUT / 'menu.png').write_bytes(g.png())
        g.row('set_form', 'Range 2')
        g.enter('5000')
        (OUT / 'error.png').write_bytes(g.png())
        g.ov('OK')
        g.tap('i_trend')
        (OUT / 'trend.png').write_bytes(g.png())
        g.tap('i_cal')
        (OUT / 'calibration.png').write_bytes(g.png())
        (OUT / 'renders.json').write_text(json.dumps({f.name: dict(sha256=hashlib.sha256(f.read_bytes()).hexdigest(), size=[800, 480])
                                                      for f in sorted(OUT.glob('*.png'))}, indent=1) + '\n')

    def test_spec_and_contract(self):
        spec = rt.PANEL_SPEC
        self.assertTrue(spec['approximate'])
        self.assertEqual(rt._panel_ui.check_spec(spec, set(rt.CAPS)), [])
        self.assertTrue(set(rt._panel_ui.spec_capabilities(spec)) <= set(rt.CAPS))
        meta = rt.export_eco_ncld822_contract_metadata()
        self.assertTrue(set(rt.PANEL_CAPS) <= set(meta['capabilities']))
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        if c['asset_version'] != '1.0.0':
            self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
            self.assertTrue(set(rt.PANEL_CAPS) <= set(c['capabilities']))
            self.assertIn('chemistry_lab.panel_ui', c['extensions'])
            self.assertIsNone(c['artifacts']['panel_engine']['sha256'])


if __name__ == '__main__':
    unittest.main()
