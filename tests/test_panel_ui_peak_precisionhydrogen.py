"""front-panel tests for CHEM-196 peak_precisionhydrogen (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_peak_precisionhydrogen.py -q

Every gesture goes through the session API (panel.touch / panel.press, the runtime's normal invoke path); widgets are found by id in the
structured frame (panel.frame, evaluator) and tapped at their centre.  Each screen check reads the frame and the rendered pixels: the pixels of
a widget must equal the widget drawn alone from the spec colours and the text the frame reports.  Themes: the five key procedures of the
menu survey; panel = direct capability (same runtime state); refusals (touches while off, keypad out of range, modal alarm screens, START
while depressurising); no time-outs (and the leak-test screen that ends by itself); render determinism and the evidence PNGs of
outputs/panel_ui/peak_precisionhydrogen/; contract entries."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import peak_precisionhydrogen as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'peak_precisionhydrogen_01'
OUT = ROOT / 'outputs/panel_ui/peak_precisionhydrogen'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['first start and set pressure', 'stop supply', 'switch the unit off', 'replace the deioniser column', 'recover from water load fault']
DONE_TASKS = set()
HMI = 'hmi'
D = rt.PANEL_SPEC['displays'][HMI]
PSI = 6894.757


def _rgb(role):
    return tuple(D['colors'].get(role, pu.ROLE_COLORS.get(role, (0, 0, 0))))


def _contains(a, b):
    return a[0] <= b[0] and a[1] <= b[1] and a[0] + a[2] >= b[0] + b[2] and a[1] + a[3] >= b[1] + b[3]


def drawn_alone(w, widgets):
    """The widget drawn alone (over the widgets listed before it that contain it, e.g. a frame) on the screen background with the spec colours,
    from the text the frame reports: what the pixels of its rect must be."""
    from PIL import Image, ImageDraw
    img = Image.new('RGB', tuple(D['size']), _rgb('background'))
    dr = ImageDraw.Draw(img)
    s = D.get('text_scale', 2)
    under = [u for u in widgets[:widgets.index(w)] if _contains(u['rect'], w['rect'])]
    for x in under + [w]:
        x0, y0, ww, hh = x['rect']
        if pu._draw_touch_widget(dr, x, s, D['colors'], _rgb('text'), _rgb('background'), _rgb('accent'), _rgb('panel')):
            continue
        if x['type'] == 'button':
            dr.rectangle([x0, y0, x0 + ww - 1, y0 + hh - 1], fill=_rgb('panel'), outline=_rgb('text'), width=1)
            pu._text(dr, x0 + (ww - pu.text_width(x['text'], s)) // 2, y0 + (hh - 7 * s) // 2, x['text'], s, _rgb('text'))
        else:
            assert x['type'] == 'number', x
            pu._text(dr, x0 + 2, y0 + 2, x['text'], s, _rgb('text'))
            dr.rectangle([x0, y0 + 9 * s, x0 + ww - 1, y0 + hh - 1], fill=_rgb('background'), outline=_rgb('text'))
            pu._text(dr, x0 + 4, y0 + 9 * s + (hh - 9 * s - 7 * s) // 2, x.get('value_text', ''), s, _rgb('text'))
    x0, y0, ww, hh = w['rect']
    return np.asarray(img)[y0:y0 + hh, x0:x0 + ww]


class Rig:
    def __init__(self, seed=1, **plant):
        self.h = rt.PeakPrecisionhydrogenRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            assert cap == 'sim.step' or self.h.t == t0, cap                               # gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    # screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def widgets(self):
        return {w['id']: w for w in self.frame()['displays'][HMI]['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def shows(self, wid, text=None):
        """The widget is on the screen with this text, in the frame and in the pixels."""
        ws = self.frame()['displays'][HMI]['widgets']
        w = next(x for x in ws if x['id'] == wid)
        if text is not None:
            assert w['text'] == text, (wid, w['text'], text)
        img = self.h.panel.render(HMI)
        x0, y0, ww, hh = w['rect']
        assert np.array_equal(img[y0:y0 + hh, x0:x0 + ww], drawn_alone(w, ws)), wid
        return True

    # gestures
    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=HMI, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def key(self, label):
        """Tap a key of the open keypad by its label."""
        for b in self.frame()['displays'][HMI]['overlay']['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(label)

    def keypad(self, text):
        for ch in text:
            assert self.key(ch), ch
        return self.key('OK')

    def power(self):
        return self.call(self.A, 'panel.press', input_id='mains_switch')['accepted']

    def ready(self):
        assert self.power()
        self.step(rt.DOC['leak_test_s'] + 30.0)
        assert self.screen() == ('gui', 'home'), self.screen()


class SurveyProcedures(unittest.TestCase):
    """The five key procedures of the menu survey, done only with panel gestures."""

    def test_first_start_and_set_pressure(self):
        r = Rig()
        self.assertEqual(r.screen()[0], 'dark')
        self.assertEqual(int(r.h.panel.render(HMI).max()), 0)                     # unlit
        self.assertTrue(r.power())
        self.assertEqual(r.screen(), ('boot', 'boot'))
        r.shows('b_model', 'Precision H2 Trace')
        self.assertFalse(r.tap('b_progress'))                                    # the diagnostics screen has no buttons
        r.step(200.0)
        self.assertEqual(r.screen()[0], 'boot')
        g = r.widgets()['b_progress']['fraction']
        self.assertGreater(g, 0.0)
        self.assertLess(g, 1.0)
        r.step(rt.DOC['leak_test_s'])
        self.assertEqual(r.screen(), ('gui', 'home'))
        r.shows('h_status', 'SYSTEM STAND-BY')
        r.shows('h_start', 'START')
        self.assertTrue(r.tap('h_menu'))
        self.assertEqual(r.screen(), ('gui', 'menu'))
        r.shows('m_units', 'BAR')                                                # CHANGE UNITS TO <the other unit>
        self.assertTrue(r.tap('m_units'))
        self.assertEqual(r.val('settings.current')['units'], 'bar')
        r.shows('m_units', 'PSI')
        self.assertTrue(r.tap('m_units'))
        self.assertEqual(r.val('settings.current')['units'], 'psi')
        self.assertTrue(r.tap('m_set_psi'))
        self.assertEqual(r.frame()['displays'][HMI]['overlay']['kind'], 'keyboard')
        self.assertTrue(r.keypad('75'))
        self.assertAlmostEqual(r.val('pressure.set_pa'), 75 * PSI, places=6)
        r.shows('m_set_psi')
        self.assertEqual(r.widgets()['m_set_psi']['value_text'], '75')
        self.assertTrue(r.tap('m_back'))
        self.assertEqual(r.screen(), ('gui', 'home'))
        self.assertTrue(r.tap('h_start'))
        self.assertEqual(r.val('state.current'), 'running')
        r.shows('h_status', 'SYSTEM RUNNING')
        r.shows('h_start', 'STOP')
        r.call(r.E, 'consumer.set', demand_sccm=150.0, min_pressure_pa=2.5e5)
        r.step(1800.0)
        self.assertAlmostEqual(r.val('pressure.delivery_pa'), 75 * PSI, delta=0.05e5)
        r.shows('h_delivery', f'DELIVERY PRESSURE - {r.val("display.pressure"):.0f}')
        self.assertEqual(r.text('h_delivery'), 'DELIVERY PRESSURE - 75')
        lit = sum(1 for k in range(10) if f'h_bar_{k}' in r.widgets())               # % FLOW bar: 150 of 500 sccm
        self.assertEqual(lit, int(r.val('display.flow_percent') / 10.0 + 0.5))
        self.assertEqual(lit, 3)
        DONE_TASKS.add('first start and set pressure')

    def test_stop_supply(self):
        r = Rig()
        r.ready()
        r.tap('h_start')
        self.assertEqual(r.val('state.current'), 'running')
        self.assertTrue(r.tap('h_start'))                                        # the same key now reads STOP
        self.assertEqual(r.val('state.current'), 'standby')
        r.shows('h_status', 'SYSTEM STAND-BY')
        r.shows('h_start', 'START')
        DONE_TASKS.add('stop supply')

    def test_switch_the_unit_off(self):
        r = Rig()
        r.ready()
        r.step(600.0)
        r.tap('h_start')
        r.tap('h_start')                                                         # STOP
        r.tap('h_menu')
        self.assertTrue(r.tap('m_shutdown'))
        self.assertEqual(r.screen(), ('gui', 'home'))
        self.assertEqual(r.val('state.current'), 'depressurising')
        r.shows('h_status', 'DEPRESSURISING')
        r.step(60.0)
        self.assertTrue(r.val('indicator.depressurised'))
        r.shows('h_status', 'SYSTEM SHUT DOWN')
        self.assertLess(r.val('pressure.delivery_pa'), 0.1e5)
        self.assertTrue(r.power())
        self.assertFalse(r.val('power.on'))
        self.assertEqual(r.screen()[0], 'dark')
        self.assertEqual(int(r.h.panel.render(HMI).max()), 0)
        self.assertFalse(r.touch(200, 120))                                      # the dark screen ignores touches
        DONE_TASKS.add('switch the unit off')

    def test_replace_the_deioniser_column(self):
        r = Rig()
        r.ready()
        r.h.di_age = rt.DOC['deioniser_interval_s'] + 1.0                         # the scheduled change is due
        r.step(1.0)
        self.assertEqual(r.screen(), ('di_due', 'prompt'))
        r.shows('d_title', 'CHANGE D.I. COLUMN')
        self.assertTrue(r.tap('d_later'))
        self.assertEqual(r.screen(), ('gui', 'home'))                            # LATER: hidden until the next power-up
        r.step(3600.0)
        self.assertEqual(r.screen()[0], 'gui')
        r.power()
        self.assertTrue(r.power())
        r.step(rt.DOC['leak_test_s'] + 30.0)
        self.assertEqual(r.screen(), ('di_due', 'prompt'))
        self.assertTrue(r.tap('d_continue'))
        self.assertEqual(r.screen(), ('di_due', 'change'))
        self.assertTrue(r.tap('e_change'))
        self.assertTrue(r.val('deioniser.change_pending'))
        self.assertEqual(r.screen(), ('di_changed', 'di_changed'))
        r.shows('k_title', 'D.I. COLUMN CHANGED?')
        r.call(r.A, 'deioniser.replace')                                         # fascia off, column swapped (robot skill)
        self.assertEqual(r.call(r.E, 'truth.inspect')['water']['deioniser_used_eq'], 0.0)
        self.assertTrue(r.tap('k_confirm'))
        self.assertFalse(r.val('deioniser.change_pending'))
        self.assertEqual(r.val('deioniser.age_s'), 0.0)
        self.assertEqual(r.screen(), ('gui', 'home'))
        # manual change through MENU > COLUMN
        r.step(86400.0)
        r.tap('h_menu')
        r.tap('m_column')
        self.assertEqual(r.screen(), ('gui', 'column'))
        r.shows('c_days', f'-{int((rt.DOC["deioniser_interval_s"] - r.val("deioniser.age_s")) // 86400)}')
        self.assertTrue(r.tap('c_change'))
        self.assertEqual(r.screen()[0], 'di_changed')
        self.assertTrue(r.tap('k_confirm'))
        self.assertEqual(r.screen(), ('gui', 'home'))
        self.assertEqual(r.val('deioniser.age_s'), 0.0)
        DONE_TASKS.add('replace the deioniser column')

    def test_recover_from_water_load_fault(self):
        r = Rig(bottle_water_m3=0.0)
        r.h.tank = 0.2 * rt.ENG['tank_volume_m3']
        r.power()
        r.step(200.0)
        self.assertEqual(r.screen(), ('no_water', 'no_water'))
        r.shows('w_title', 'NO WATER LOADED')
        self.assertTrue(r.val('alarm.audible'))
        self.assertTrue(r.tap('w_mute'))
        self.assertFalse(r.val('alarm.audible'))
        r.call(r.A, 'water.refill', volume_m3=0.003)                             # check the external supply
        self.assertTrue(r.tap('w_recovery'))
        self.assertEqual(r.screen(), ('boot', 'boot'))
        r.step(rt.DOC['leak_test_s'] + 120.0)
        self.assertEqual(r.val('state.current'), 'standby')
        self.assertEqual(r.screen(), ('gui', 'home'))
        DONE_TASKS.add('recover from water load fault')


class Screens(unittest.TestCase):
    def test_water_level_fault_in_operation(self):
        r = Rig(bottle_water_m3=0.0002)
        r.ready()
        r.step(600.0)
        r.call(r.E, 'consumer.set', demand_sccm=450.0, min_pressure_pa=2e5)
        r.tap('h_start')
        r.step(2 * 86400.0)
        self.assertIn('WATER_LOW', r.val('alarms.active'))
        self.assertEqual(r.screen(), ('water_fault', 'water_fault'))
        r.shows('l_line1', 'Hydrogen generator SHUT DOWN')
        self.assertTrue(r.tap('l_mute'))
        self.assertFalse(r.val('alarm.audible'))
        r.call(r.A, 'water.refill', volume_m3=0.003)
        r.step(600.0)
        self.assertEqual(r.screen(), ('water_fault', 'water_fault'))            # 1.1.1: latched until the power is cycled (manual p.28)
        self.assertTrue(r.power())                                               # off
        self.assertTrue(r.power())                                               # on: water check, leak test
        r.step(rt.DOC['leak_test_s'] + 120.0)
        self.assertEqual(r.screen(), ('gui', 'home'))

    def test_failure_screens_are_modal_and_red(self):
        r = Rig()
        r.ready()
        r.call(r.E, 'fault.inject', fault='cell_failure', active=True)
        r.step(1.0)
        self.assertEqual(r.screen(), ('failure', 'failure'))
        r.shows('f_title', 'CELL OVER VOLTAGE ALARM')
        img = r.h.panel.render(HMI)
        self.assertTrue(np.array_equal(img[5, 5], np.array(_rgb('error'), dtype=np.uint8)))
        self.assertFalse(r.touch(80, 182))                                       # where START is on the home screen
        self.assertEqual(r.val('state.current'), 'fault')
        self.assertTrue(r.tap('f_mute'))
        self.assertFalse(r.val('alarm.audible'))
        self.assertEqual(r.screen()[0], 'failure')
        r.call(r.E, 'fault.inject', fault='cell_failure', active=False)
        r.power()
        r.power()                                                                # power cycle: diagnostics again
        self.assertEqual(r.screen()[0], 'boot')
        r.step(rt.DOC['leak_test_s'] + 30.0)
        self.assertEqual(r.screen(), ('gui', 'home'))

    def test_leak_test_failure_title(self):
        r = Rig()
        r.call(r.E, 'fault.inject', fault='internal_leak', active=True)
        r.power()
        r.step(rt.DOC['leak_test_s'] + 30.0)
        self.assertIn('LEAK_TEST_FAILED', r.val('alarms.active'))
        r.shows('f_title', 'DIAGNOSTICS FAILED')


class Refusals(unittest.TestCase):
    def test_keypad_range_cancel_and_units(self):
        r = Rig()
        r.ready()
        r.tap('h_menu')
        before = r.val('pressure.set_pa')
        r.tap('m_set_psi')
        for ch in '120':
            r.key(ch)
        self.assertFalse(r.key('OK'))
        self.assertEqual(r.frame()['displays'][HMI]['overlay']['error'], 'Out of range')
        self.assertEqual(r.val('pressure.set_pa'), before)
        self.assertTrue(r.key('Cancel'))
        self.assertIsNone(r.frame()['displays'][HMI].get('overlay'))
        r.tap('m_units')                                                         # bar: 0.1 bar steps, 6.9 bar = 100 psi
        self.assertEqual(r.widgets()['m_set_bar']['value_text'], f'{before / 1e5:.1f}')
        r.tap('m_set_bar')
        self.assertTrue(r.keypad('6.9'))
        self.assertAlmostEqual(r.val('pressure.set_pa'), rt.P_MAX_G, places=3)
        r.tap('m_set_bar')
        self.assertTrue(r.keypad('2.5'))
        self.assertAlmostEqual(r.val('pressure.set_pa'), 2.5e5, places=6)

    def test_start_while_depressurising_is_ignored(self):
        r = Rig()
        r.ready()
        r.step(600.0)
        r.tap('h_menu')
        r.tap('m_shutdown')
        self.assertEqual(r.val('state.current'), 'depressurising')
        self.assertFalse(r.tap('h_start'))
        self.assertEqual(r.val('state.current'), 'depressurising')
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'DEPRESSURISING')

    def test_off_and_invalid_gestures(self):
        r = Rig()
        self.assertFalse(r.touch(10, 10))
        self.assertFalse(r.val('power.on'))
        self.assertEqual(r.call(r.A, 'panel.touch', expect='rejected', display_id=HMI, x=400, y=10)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.press', expect='rejected', input_id='menu_key')['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')   # evaluator only


class TimeOuts(unittest.TestCase):
    def test_no_menu_time_out_and_boot_ends_by_itself(self):
        r = Rig()
        r.power()
        r.step(rt.DOC['leak_test_s'] - 30.0)
        self.assertEqual(r.screen()[0], 'boot')
        r.step(60.0)
        self.assertEqual(r.screen(), ('gui', 'home'))
        r.tap('h_menu')
        r.tap('m_column')
        r.step(3 * 3600.0)
        self.assertEqual(r.screen(), ('gui', 'column'))                          # no time-out documented: the page stays


class Equivalence(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(), Rig()
        for r in (a, b):
            r.call(r.E, 'consumer.set', demand_sccm=200.0, min_pressure_pa=2e5)
        a.power()
        b.call(b.A, 'power.toggle')
        for r in (a, b):
            r.step(rt.DOC['leak_test_s'] + 30.0)
        a.tap('h_menu')
        a.tap('m_units')
        a.tap('m_set_bar')
        a.keypad('4.5')
        a.tap('m_back')
        a.tap('h_start')
        b.call(b.A, 'settings.configure', units='bar')
        b.call(b.A, 'settings.configure', pressure_set_pa=4.5e5)
        b.call(b.A, 'cycle.toggle')
        for r in (a, b):
            r.step(900.0)
        a.tap('h_start')
        a.tap('h_menu')
        a.tap('m_shutdown')
        b.call(b.A, 'cycle.toggle')
        b.call(b.A, 'output.depressurise')
        for r in (a, b):
            r.step(120.0)
        a.tap('h_menu')
        a.tap('m_column')
        a.tap('c_change')
        a.tap('k_confirm')
        b.call(b.A, 'deioniser.change_begin')
        b.call(b.A, 'deioniser.change_confirm')
        sa, sb = a.h.read('evaluator'), b.h.read('evaluator')
        self.assertEqual(json.dumps(sa, sort_keys=True), json.dumps(sb, sort_keys=True))
        self.assertEqual(json.dumps(a.call(a.E, 'ledger.read'), sort_keys=True), json.dumps(b.call(b.E, 'ledger.read'), sort_keys=True))


class Rendering(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        def run():
            r = Rig()
            r.ready()
            r.tap('h_menu')
            r.tap('m_set_psi')
            cap = r.call(r.A, 'panel.capture', display_id=HMI)
            return cap, Path(cap['image']['path']).read_bytes()
        (c1, p1), (c2, p2) = run(), run()
        self.assertEqual(hashlib.sha256(p1).hexdigest(), hashlib.sha256(p2).hexdigest())
        self.assertEqual(c1['image']['sha256'], hashlib.sha256(p1).hexdigest())
        self.assertEqual((c1['image']['width'], c1['image']['height']), tuple(D['size']))
        r = Rig()
        caps = r.A.describe(instance_id=INST)['capabilities']
        self.assertIn('panel.touch', caps)
        self.assertNotIn('panel.frame', caps)
        self.assertIn('panel.frame', r.E.describe(instance_id=INST)['capabilities'])

    def test_evidence_renders(self):
        """home / menu / error (failure screen) and the other screens, deterministic, with renders.json."""
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        panel = r.h.panel
        shots = {}
        r.power()
        r.step(120.0)
        shots['boot'] = panel.save_png(HMI, OUT / 'boot.png')
        r.step(rt.DOC['leak_test_s'])
        r.call(r.E, 'consumer.set', demand_sccm=250.0, min_pressure_pa=2e5)
        r.tap('h_start')
        r.step(1800.0)
        shots['home'] = panel.save_png(HMI, OUT / 'home.png')
        r.tap('h_menu')
        shots['menu'] = panel.save_png(HMI, OUT / 'menu.png')
        r.tap('m_set_psi')
        shots['keypad'] = panel.save_png(HMI, OUT / 'keypad.png')
        r.key('Cancel')
        r.tap('m_column')
        shots['column'] = panel.save_png(HMI, OUT / 'column.png')
        r.tap('c_change')
        shots['di_changed'] = panel.save_png(HMI, OUT / 'di_changed.png')
        r.tap('k_confirm')
        r.call(r.E, 'fault.inject', fault='cell_failure', active=True)
        r.step(1.0)
        shots['error'] = panel.save_png(HMI, OUT / 'error.png')
        w = Rig(bottle_water_m3=0.0)
        w.h.tank = 0.2 * rt.ENG['tank_volume_m3']
        w.power()
        w.step(200.0)
        shots['no_water'] = w.h.panel.save_png(HMI, OUT / 'no_water.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_peak_precisionhydrogen_contract_metadata()
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for name, cap in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'errors'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        spec = rt.PANEL_SPEC
        for k, v in list(spec['inputs'].items()) + list(spec['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        self.assertEqual(set(pu.spec_capabilities(spec)) - set(c['capabilities']), set())
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', '0.1'))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])            # the shared engine is not pinned per asset
        self.assertIn('manufacturer_manual_bw', c['sources'])
        from asset_tool import validate_contract
        self.assertEqual(validate_contract(c, ROOT, check_files=False), [])


if __name__ == '__main__':
    unittest.main()
