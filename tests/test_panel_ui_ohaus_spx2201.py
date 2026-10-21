"""Front-panel tests for the OHAUS Scout SPX2201 (assets/SPX2201/panel_ui.json + scripts/asset_runtime/_panel_ui.py over
scripts/asset_runtime/ohaus_balance.py): python3 -m pytest tests/test_panel_ui_ohaus_spx2201.py -q

Every key goes through the session API (panel.press / panel.turn / panel.switch); the pan load, the adapter, batteries, transport lock and the
RS232 kit are robot skills or evaluator settings, the host clock is sim.step.  The 6-digit display is checked twice: the structured frame
(panel.frame) and the rendered pixels, read back with a seven-segment reader that knows only the display geometry of the spec.  Themes: the
seven key procedures of the menu survey; panel = direct capability; refusals and locks (security switch, Err 8.x, ref.err, transport lock,
mode and unit enables, the RS232 kit); time-outs (boot display test, auto off, backlight); the firmware functions behind the menus (calibration,
modes, units, printing); render determinism and the evidence PNGs."""
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import ohaus_balance as ob  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

MODEL = 'SPX2201'
INST = 'ohaus_spx2201_01'
SPEC = ob.PANEL_SPEC[MODEL]
LCD = SPEC['displays']['lcd']
OUT = ROOT / 'outputs/panel_ui/ohaus_spx2201'
SURVEY_TASKS = ['turn on / off', 'span calibration', 'weigh with a container', 'parts counting', 'check weighing limits', 'set filter level', 'define custom unit']
DONE_TASKS = set()
YES, NO, BACK, EXIT = 'key_zero', 'key_print', 'key_function', 'key_tare'
_DIGITS = {v: k for k, v in pu.SEG7.items() if k in '0123456789'}
_LETTERS = {v: k for k, v in pu.SEG7.items() if k not in '0123456789' and v}


def _lit(img, x, y, bg):
    return img[int(round(y)), int(round(x))].astype(int).sum() < 0.5 * bg


def read_seven(img, f, bg):
    n, cw, ch, gap = f['cells'], f['cell_w'], f['cell_h'], f.get('gap', 6)
    colons, cgap = f.get('colons', []), f.get('colon_gap', 0)
    t = f.get('thickness', max(2.0, cw * 0.18))
    out = ''
    for i in range(n):
        x = f['x'] + i * (cw + gap) + cgap * sum(1 for b in colons if b <= i)
        if i in colons:
            cx = x - (gap + cgap) / 2.0
            out += ':' if _lit(img, cx, f['y'] + ch * 0.3, bg) and _lit(img, cx, f['y'] + ch * 0.7, bg) else ''
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2),
                   g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if _lit(img, px, py, bg))
        out += _DIGITS.get(segs, _LETTERS.get(segs, ' ' if not segs else '?'))
        r = max(1.5, t * 0.6)
        if _lit(img, x + cw + gap / 2.0, f['y'] + ch - r, bg):
            out += '.'
    return out.strip()


def seg_norm(text):
    return tuple(ch if ch in '.:' else pu.SEG7.get(ch, pu.SEG7.get(ch.upper(), pu.SEG7.get(ch.lower(), ''))) or '_' for ch in text)


class Rig:
    def __init__(self, on=True, instance=INST, kit=False):
        self.h = ob.OhausBalanceRuntime(MODEL, instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.inst = instance
        if kit:
            self.call(self.E, 'plant.configure', rs232_kit=True)
        if on:
            self.call(self.A, 'power.connect_adapter', connected=True)
            assert self.press(YES)
            self.step(5)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            ob.validate(ob.ALL_CAPS[MODEL][cap]['output_schema'], r['result'])
            assert cap == 'sim.step' or self.h.t == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def hold(self, key):
        return self.press(key, 2.5)

    def keys(self, *ks):
        for k in ks:
            assert self.press(k), (k, self.main())

    def load(self, grams, settle=3.0):
        self.call(self.A, 'pan.set_load', mass_kg=grams / 1000.0)
        self.step(settle)

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def main(self, steady=False):
        """The 6 digits shown now (frame and pixels must agree); steady: the text without blinking."""
        fr = self.frame()['displays']['lcd']
        img = self.h.panel.render('lcd')
        bg = int(img[-1, -1].astype(int).sum())
        shown = fr['fields']['main']['shown'].strip()
        assert seg_norm(shown) == seg_norm(read_seven(img, LCD['fields']['main'], bg)), (shown, read_seven(img, LCD['fields']['main'], bg))
        return fr['fields']['main']['text'].strip() if steady else shown

    def icons(self):
        return {k for k, v in self.frame()['displays']['lcd']['icons'].items() if v['shown']}

    def screen(self):
        return self.frame()['screen']

    def item(self):
        return (self.frame()['ui']['menu'] or {}).get('item')

    def state(self, *ids):
        o = self.E.read(instance_id=self.inst, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def menu_to(self, *path):
        if self.screen() != 'menu':
            assert self.hold(EXIT)
        for i, target in enumerate(path):
            for _ in range(40):
                if self.item() == target:
                    break
                assert self.press(NO)
            assert self.item() == target, (target, self.main())
            if i < len(path) - 1:
                assert self.press(YES)

    def choose(self, text):
        assert self.press(YES)
        for _ in range(30):
            if self.main(steady=True) == text:
                break
            assert self.press(NO)
        assert self.main(steady=True) == text, (text, self.main(steady=True))
        assert self.press(YES)


def runtime_state(h):
    r = h.read('evaluator')
    for k in ('clock.time_s', 'clock.datetime', 'display.message'):
        r.pop(k, None)
    return json.dumps(dict(read=r, s=h.s, app=h.appstate, unit=h.unit, zero=h.zero_kg, tare=h.tare_kg), sort_keys=True, default=str)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_turn_on_off(self):
        g = Rig(on=False)
        g.call(g.A, 'power.connect_adapter', connected=True)
        self.assertEqual(g.screen(), 'off')
        self.assertTrue(g.press(YES, 1.0))                                           # hold On/Zero 1 s: display test (p.7)
        self.assertEqual(g.main(steady=True), '8.8.8.8.8.8.')
        self.assertIn('stable', g.icons())
        g.step(2.5)
        self.assertEqual(g.screen(), 'home')
        self.assertEqual(g.main(), '0.0')
        self.assertTrue(g.hold(YES))                                                 # hold until OFF: off
        self.assertEqual(g.screen(), 'off')
        g.call(g.A, 'power.connect_adapter', connected=False)                        # batteries keep it going
        g.press(YES)
        g.step(3)
        self.assertIn('battery', g.icons())
        DONE_TASKS.add('turn on / off')

    def test_span_calibration(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', gain=0.002)
        g.load(1000.0)
        self.assertEqual(g.main(), '1002.0')
        g.load(0.0)
        g.call(g.A, 'panel.switch', input_id='security_switch', position='locked')
        g.hold(EXIT)
        self.assertEqual(g.main(steady=True), 'SEtUP')                               # locked: C.A.L hidden (p.11)
        g.keys(EXIT)
        g.call(g.A, 'panel.switch', input_id='security_switch', position='unlocked')
        g.hold(EXIT)                                                                 # [MENU] while held, then [C.A.L]
        self.assertEqual(g.main(steady=True), 'C.A.L')
        g.keys(YES)
        self.assertEqual(g.main(steady=True), 'SPAn')
        g.keys(YES)
        self.assertEqual(g.main(steady=True), '--C--')                               # zero being stored
        g.step(2)
        self.assertEqual(g.main(), '2000.0')
        g.keys(NO)                                                                   # No toggles the value
        self.assertEqual(g.main(), '1000.0')
        g.load(1000.0)
        self.assertEqual(g.main(steady=True), 'donE')
        g.load(0.0)
        g.keys(NO)
        g.load(500.0)
        self.assertEqual(g.main(), '500.0')
        DONE_TASKS.add('span calibration')

    def test_weigh_with_a_container(self):
        g = Rig()
        g.call(g.A, 'app.select', app='count')
        g.hold(BACK)                                                                 # count -> percent ... until WEIGH
        while g.state('app.active') != 'weigh':
            if g.screen() != 'home':
                g.keys(EXIT)                                                         # leave the mode's prompt
            g.hold(BACK)
        self.assertEqual(g.state('app.active'), 'weigh')
        g.load(150.0)
        g.keys(EXIT)                                                                 # Tare
        self.assertIn('net', g.icons())
        g.load(212.3)
        self.assertEqual(g.main(), '62.3')
        DONE_TASKS.add('weigh with a container')

    def test_parts_counting(self):
        g = Rig()
        g.load(80.0)
        g.keys(EXIT)
        g.hold(BACK)                                                                 # [Count]; no APW yet: [Put.10] (p.8)
        self.assertEqual(g.screen(), 'count_put')
        g.keys(NO)                                                                   # 10 -> 20
        self.assertEqual(g.frame()['displays']['lcd']['fields']['main']['text'], 'Put.20')
        g.keys(BACK, YES)                                                            # back to 10, take it
        g.load(80.0 + 10 * 12.3)
        g.keys(YES)                                                                  # calculate the APW
        self.assertEqual(g.main(), '10')
        self.assertIn('u_pcs', g.icons())
        g.load(80.0 + 47 * 12.3)
        self.assertEqual(g.main(), '47')
        g.keys(BACK)                                                                 # Function: the APW
        self.assertEqual(g.main(), '12.3')
        for _ in range(6):                                                           # around the six modes to Count again: [CLr.PcS]
            if g.screen() != 'home':
                g.keys(EXIT)
            g.hold(BACK)
        self.assertEqual(g.screen(), 'count_clear')
        g.keys(NO)
        self.assertEqual(g.main(), '47')                                             # No keeps the stored APW
        DONE_TASKS.add('parts counting')

    def test_check_weighing_limits(self):
        g = Rig()
        for _ in range(2):                                                           # Weigh -> Count -> Percent
            g.hold(BACK)
            if g.screen() != 'home':
                g.keys(EXIT)
        g.hold(BACK)                                                                 # [CHeCk] -> [CLr.rEF]
        self.assertEqual(g.screen(), 'check_clear')
        g.keys(YES)                                                                  # [Set. Lo]
        self.assertEqual(g.main(steady=True), 'SEt.Lo')
        g.keys(YES)                                                                  # the stored value, first digit highlighted
        self.assertEqual(g.frame()['displays']['lcd']['fields']['main']['text'], '00000.0')
        g.keys(YES, YES, NO, YES, YES, YES, YES)                                     # 00100.0
        self.assertAlmostEqual(g.state('app.check')['low_kg'], 0.1)
        self.assertEqual(g.main(steady=True), 'SEt.Hi')
        g.keys(YES, YES, YES, NO, YES, NO, NO, YES, YES, YES)                        # 00120.0
        self.assertAlmostEqual(g.state('app.check')['high_kg'], 0.12)
        g.keys(YES)
        self.assertEqual(g.screen(), 'home')
        for grams, icon in ((90.0, 'under'), (110.0, 'accept'), (130.0, 'over')):
            g.load(grams)
            self.assertEqual(g.icons() & {'under', 'accept', 'over'}, {icon})
        DONE_TASKS.add('check weighing limits')

    def test_set_filter_level(self):
        g = Rig()
        g.menu_to('m_setup', 'setup_filter')
        self.assertEqual(g.main(steady=True), 'FILtEr')
        g.choose('HI')
        self.assertEqual(g.state('settings')['filter'], 'high')
        self.assertEqual(g.item(), 'setup_azt')                                      # Yes accepted: the next item
        g.keys(EXIT)
        self.assertEqual(g.screen(), 'home')
        DONE_TASKS.add('set filter level')

    def test_define_custom_unit(self):
        g = Rig()
        g.menu_to('m_unit', 'unit_c')                                                # No through C.A.L, SEtUP, ModE to Unit; Yes; to Unit C (p.15)
        self.assertEqual(g.main(steady=True), 'Unit C')
        g.choose('On')
        self.assertEqual(g.item(), 'unit_c_factor')
        g.keys(YES)                                                                  # factor 1.0000000: digit 1 highlighted
        g.keys(YES, NO, NO, NO, NO, NO, YES, YES, YES, YES, YES, YES, YES)           # 1.5000000
        self.assertAlmostEqual(g.state('settings')['custom_factor'], 1.5)
        g.choose('-1')                                                               # EPt -1 (wraps from 0 ... 3, -3, -2, -1)
        g.choose('1')                                                                # LSd 1
        self.assertEqual(g.item(), 'unit_end')
        g.keys(EXIT)
        g.load(100.0)
        g.call(g.A, 'weighing.set_unit', unit='C')
        self.assertEqual(g.main(), '15.00')                                          # 100 g x 0.15; LSD 1 on the decade of 1 d (0.015)
        DONE_TASKS.add('define custom unit')


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def test_panel_and_direct_calls_give_the_same_state(self):
        a, b = Rig(), Rig()
        a.menu_to('m_setup', 'setup_stable')
        a.choose('2d')
        a.keys(EXIT)
        a.menu_to('m_mode', 'mode_total')
        a.choose('OFF')
        a.keys(EXIT)
        a.menu_to('m_unit', 'unit_ct')
        a.choose('On')
        a.keys(EXIT)
        a.hold(NO)
        a.hold(NO)
        b.call(b.A, 'settings.configure', stable_range='2')
        b.call(b.A, 'settings.configure', modes={'total': False})
        b.call(b.A, 'settings.configure', units={'ct': True})
        b.call(b.A, 'weighing.next_unit')
        b.call(b.A, 'weighing.next_unit')
        self.assertEqual(a.h.unit, 'ct')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


# ---------------------------------------------------------------- refusals and errors
class Refusals(unittest.TestCase):
    def test_err_codes_and_transport_lock(self):
        g = Rig(on=False)
        g.call(g.A, 'power.connect_adapter', connected=True)
        g.call(g.A, 'pan.set_load', mass_kg=0.4)
        g.press(YES)
        g.step(3)
        self.assertEqual(g.main(), 'Err 8.1')                                        # p.17
        g.load(0.0)
        g.load(2300.0)
        self.assertEqual(g.main(), 'Err 8.3')
        g.load(0.0)
        g.load(10.0)
        g.keys(EXIT)
        g.load(0.0)
        g.keys(EXIT)
        g.load(10.0)
        g.keys(YES)                                                                  # zero with 10 g on the pan
        g.load(0.0)
        self.assertFalse(g.press(EXIT))                                              # tare of -10 g: out of range
        self.assertEqual(g.main(), 'Err 8.5')
        g.load(0.0)
        g.hold(YES)
        g.call(g.A, 'pan.remove', unlock_angle_rad=-0.35)                            # bayonet released
        g.call(g.A, 'transport_lock.set', angle_rad=0.0)                             # locked for transport
        g.call(g.A, 'pan.install')
        g.press(YES)
        g.step(3)
        self.assertEqual(g.main(), '------')
        self.assertEqual(g.state('status'), 'locked')

    def test_security_switch_locks_units_and_ref_err(self):
        g = Rig()
        g.call(g.A, 'settings.configure', units={'ct': True})
        g.call(g.A, 'panel.switch', input_id='security_switch', position='locked')
        self.assertFalse(g.hold(NO))                                                 # units locked (p.11)
        self.assertEqual(g.main(), 'LOCk')
        g.menu_to('m_unit', 'unit_kg')
        g.keys(YES, NO)
        self.assertFalse(g.press(YES))
        g.keys(EXIT)
        g.call(g.A, 'panel.switch', input_id='security_switch', position='unlocked')
        g.hold(BACK)
        g.keys(YES)                                                                  # Put.10 with an empty pan
        self.assertFalse(g.press(YES))
        self.assertEqual(g.main(), 'rEF.Err')                                        # sample < 1 d (p.17)

    def test_kit_menus_and_disabled_modes(self):
        g = Rig()
        g.hold(EXIT)
        seen = set()
        for _ in range(8):
            seen.add(g.item())
            g.keys(NO)
        self.assertNotIn('m_print', seen)                                            # no RS232 kit: no PRINT / RS232 menus
        g.keys(EXIT)
        self.assertEqual(g.call(g.A, 'interface.connect', expect='rejected', device='pc')['error']['code'], 'NO_INTERFACE')
        g.call(g.A, 'settings.configure', modes={'count': False, 'percent': False, 'check': False, 'total': False})
        g.call(g.A, 'settings.configure', hold='off')
        g.hold(BACK)
        self.assertEqual(g.state('app.active'), 'weigh')                             # only Weigh is left
        r = g.call(g.A, 'settings.configure', expect='rejected', modes={'weigh': False})
        self.assertEqual(r['error']['code'], 'NO_MODE')


# ---------------------------------------------------------------- applications, printing, time
class Functions(unittest.TestCase):
    def test_percent_totalize_hold(self):
        g = Rig()
        g.hold(BACK)
        g.keys(EXIT)
        g.hold(BACK)                                                                 # [PErCnt]: no reference yet -> [Put.rEF]
        self.assertEqual(g.screen(), 'percent_put')
        g.load(400.0)
        g.keys(YES)
        self.assertEqual(g.main(), '100.0')
        g.load(100.0)
        self.assertEqual(g.main(), '25.0')
        self.assertIn('u_pct', g.icons())
        g.hold(BACK)                                                                 # [CHeCk]
        g.keys(EXIT)
        g.hold(BACK)                                                                 # [totAL] -> [CLr.tot]
        self.assertEqual(g.screen(), 'total_clear')
        g.keys(YES)
        g.load(100.0)
        g.keys(BACK)                                                                 # store: Sigma flashes, the total shows
        self.assertTrue(g.frame()['displays']['lcd']['icons']['sum']['blink'])
        g.load(0.0)
        g.load(250.0)
        g.keys(BACK)
        self.assertEqual(g.main(), '350.0')
        g.load(0.0)
        g.hold(BACK)                                                                 # [HoLd]
        self.assertEqual(g.state('app.active'), 'hold')
        g.keys(BACK)
        self.assertEqual(g.main(), 'rEAdY')
        g.load(321.0)
        self.assertEqual(g.main(), '321.0')
        g.load(100.0)
        self.assertEqual(g.main(steady=True), '321.0')                               # held (display hold)
        self.assertTrue(g.frame()['displays']['lcd']['icons']['hold']['blink'])
        g.load(0.0)
        g.keys(BACK)
        self.assertEqual(g.main(), '0.0')

    def test_kit_printing_auto_off_backlight_linearity(self):
        g = Rig(kit=True)
        g.call(g.A, 'interface.connect', device='pc')
        g.menu_to('m_print', 'print_c_gross')
        g.choose('On')
        g.keys(EXIT)
        g.load(169.6)
        g.keys(EXIT)
        g.load(244.2)
        g.keys(NO)                                                                   # Print
        lines = [x['line'] for x in g.call(g.A, 'events.read')['output']['lines']]
        self.assertTrue(lines[-1].startswith('74.6'))
        self.assertIn('244.2', lines[-1])                                           # gross on the same line (Single format)
        g.call(g.A, 'settings.configure', auto_off='1')
        g.call(g.A, 'power.connect_adapter', connected=False)                        # on batteries: backlight auto, auto off
        g.call(g.A, 'settings.configure', backlight='auto')
        g.step(11)
        self.assertEqual(g.frame()['displays']['lcd']['brightness'], 30)
        g.step(60)
        self.assertEqual(g.screen(), 'off')
        g.call(g.A, 'power.connect_adapter', connected=True)
        g.press(YES)
        g.step(3)
        g.call(g.E, 'sensor.configure_error', nonlinearity=0.0002)
        g.load(0.0)
        g.call(g.A, 'calibration.start', kind='linearity')
        g.step(2)
        g.load(1000.0)
        g.load(2000.0)
        self.assertEqual(g.main(steady=True), 'donE')
        g.keys(NO)
        g.load(1000.0)
        self.assertEqual(g.main(), '1000.0')

    def test_levelling_and_reset(self):
        g = Rig()
        g.call(g.E, 'environment.set', tilt_rad=0.006)
        g.step(3)
        self.assertNotIn('stable', g.icons())
        g.call(g.A, 'panel.turn', input_id='foot_front_right', detents=8)
        g.call(g.A, 'panel.turn', input_id='foot_rear_right', detents=8)
        g.step(3)
        self.assertTrue(g.state('level.bubble_centered'))
        g.call(g.A, 'settings.configure', filter='high', azt='3')
        g.menu_to('m_setup', 'setup_reset')
        g.choose('yES')
        self.assertEqual(g.state('settings')['filter'], 'medium')


# ---------------------------------------------------------------- rendering, evidence
class SyncFixes(unittest.TestCase):
    def test_session_describe(self):
        """Sync audit 2026-10-19: the session describes the asset (the lab-API binding builds on it)."""
        g = Rig()
        da = g.A.describe(instance_id=INST)
        de = g.E.describe(asset_type_id='instrument.ohaus_spx2201')
        self.assertEqual((da['asset_type_id'], da['asset_version']), ('instrument.ohaus_spx2201', ob.ASSET_VERSIONS[MODEL]))
        self.assertIn('check.set_limits', da['capabilities'])
        self.assertIn('panel.press', da['capabilities'])
        self.assertNotIn('sim.step', da['capabilities'])
        self.assertNotIn('fault.inject', de['capabilities'])                         # the PX system errors are not part of the Scout
        self.assertNotIn('truth.process', da['state_slots'])
        self.assertIn('truth.process', de['state_slots'])
        self.assertNotIn('fault.code', de['state_slots'])
        self.assertEqual(set(de['state_slots']), set(g.h.read('evaluator')))
        self.assertEqual(de['capabilities']['weighing.zero']['input_schema'], ob.ALL_CAPS[MODEL]['weighing.zero']['input_schema'])
        with self.assertRaises(ob.ActionRejected):
            g.A.describe(instance_id='other_01')


class RenderAndEvidence(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        a, b = Rig(), Rig()
        for g in (a, b):
            g.load(123.4)
        self.assertTrue(np.array_equal(a.h.panel.render('lcd'), b.h.panel.render('lcd')))
        cap = a.call(a.A, 'panel.capture', display_id='lcd')
        self.assertEqual((cap['image']['width'], cap['image']['height']), tuple(LCD['size']))
        self.assertEqual(a.call(a.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_direct_commands(self):
        g = Rig()
        g.call(g.A, 'power.set_batteries', installed=False)
        g.call(g.A, 'app.select', app='count')
        g.load(100.0)
        g.call(g.A, 'counting.clear_apw')
        g.call(g.A, 'counting.set_sample_size', size=5)
        g.call(g.A, 'counting.establish')
        g.call(g.A, 'app.select', app='percent')
        g.call(g.A, 'percent.clear_reference')
        g.call(g.A, 'percent.establish')
        g.call(g.A, 'app.select', app='check')
        g.call(g.A, 'check.set_limits', low_kg=-0.01, high_kg=0.01)
        g.call(g.A, 'check.clear')
        g.call(g.A, 'calibration.start', kind='span')
        g.call(g.A, 'calibration.abort')
        g.call(g.A, 'settings.reset', menu='unit')
        self.assertIsNone(g.state('app.check')['low_kg'])
        self.assertIn('raw_gross_kg', g.call(g.E, 'truth.inspect')['process'])
        g.load(0.0)
        g.call(g.A, 'power.set', on=False)
        g.call(g.A, 'pan.remove', unlock_angle_rad=-0.35)
        g.call(g.A, 'pan.install')

    def test_evidence_pngs(self):
        OUT.mkdir(parents=True, exist_ok=True)
        g = Rig()
        g.load(123.4)
        g.h.panel.save_png('lcd', OUT / 'home.png')
        g.menu_to('m_setup', 'setup_filter')
        g.h.panel.save_png('lcd', OUT / 'menu.png')
        g.keys(EXIT)
        g.load(2300.0)
        g.h.panel.save_png('lcd', OUT / 'error.png')
        (OUT / 'renders.json').write_text(json.dumps({k: f'outputs/panel_ui/ohaus_spx2201/{k}.png' for k in ('home', 'menu', 'error')}, indent=1) + '\n')


class SurveyCoverage(unittest.TestCase):
    def test_zz_all_survey_procedures_done(self):
        if not DONE_TASKS:
            self.skipTest('run with the key procedure tests')
        self.assertEqual([t for t in SURVEY_TASKS if t not in DONE_TASKS], [])


if __name__ == '__main__':
    unittest.main()
