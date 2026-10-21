"""front-panel tests for CHEM-171 wtw_oxitopi (panel_ui.json + scripts/asset_runtime/_panel_ui.py on the runtime's six OxiTop-i heads):
python3 -m pytest tests/test_panel_ui_wtw_oxitopi.py -q

Every gesture goes through the session API (panel.press with duration_s for long keystrokes and held keys for the reset); the head displays are read
from the structured frame (panel.frame, evaluator).  Themes: the six key procedures of the menu survey (start a BOD measurement, read stored values
with the graphic display, set the language, set the date, the OxiTop PT pneumatic test, reset); measurement and system settings (duration, unit,
sample volume, illumination, signal LED, switch-off time, ID digit by digit, time, bottle volume); switching off with the security prompt; refusals
shown on the head; the display switch-off time-out; six independent heads; panel = direct commands; render determinism and the evidence PNGs."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import wtw_oxitopi as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'oxitop_i_is6_01'
OUT = ROOT / 'outputs/panel_ui/wtw_oxitopi'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
DAY = 86400.0
SURVEY_TASKS = ['Start a BOD measurement', 'Read stored measured values', 'Set the language', 'Set the date', 'Pneumatic test OxiTop-PT',
                'Reset a head that does not react']
DONE_TASKS = set()


class Rig:
    def __init__(self):
        self.h = rt.WtwOxitopiRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.t == t0, cap
        return r['result'] if r['status'] == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lines(self, head=1):
        return self.frame()['displays'][f'h{head}_lcd']['lines']

    def lcd(self, head=1):
        return self.frame()['displays'][f'h{head}_lcd']

    def highlighted(self, head=1):
        d = self.lcd(head)
        return [t for t, inv in zip(d['lines'], d['inverse']) if inv]

    def var(self, name):
        return self.frame()['ui']['vars'][name]

    def press(self, key, head=1, long=False, held=()):
        return self.call(self.A, 'panel.press', input_id=f'h{head}_{key}', duration_s=2.5 if long else 0.1, held=[f'h{head}_{k}' for k in held])['accepted']

    def to(self, label, head=1):
        """Move the highlight with the navigation key to the line with this label."""
        for _ in range(12):
            if any(x.startswith(label) for x in self.highlighted(head)):
                return
            self.press('nav', head)
        raise AssertionError((label, self.lines(head)))

    def digits(self, target, head=1):
        """Digit-wise entry (manual p.14): the navigation key raises the underscored digit, <ENTER> goes to the next one."""
        for j, d in enumerate(target):
            for _ in range((int(d) - int(self.var(f'e{head}_{j}'))) % 10):
                self.press('nav', head)
            if j < len(target) - 1:
                self.press('enter', head)


def done(task):
    DONE_TASKS.add(task)


class KeyProcedures(unittest.TestCase):
    def test_start_a_bod_measurement(self):
        r = Rig()
        self.assertEqual(r.lines(), [''] * 6)                                  # the head is off
        r.press('m')                                                          # any key switches the head on
        self.assertEqual(r.lines()[:4], ['ID: 11', '', '', '19.10.2026 08:00'])
        r.press('m')
        self.assertEqual(r.lines()[:5], ['BOD parameters', '5 d', '432.0 ml', 'mg/l', '<M> start'])
        r.press('m')                                                          # start
        self.assertEqual(r.val('heads.status')['h1']['state'], 'adaption')
        self.assertEqual(r.lines()[1], 'AutoTemp')
        r.h.step(6 * 3600.0)
        r.press('m')                                                          # wake
        self.assertTrue(r.lines()[1].startswith('BOD0.'))
        self.assertEqual(r.val('heads.status')['h1']['led'], 'blue')
        done('Start a BOD measurement')

    def test_read_stored_values_and_graphic(self):
        r = Rig()
        r.call(r.A, 'head.configure', head='h1', duration_days=2)
        r.call(r.A, 'head.start', head='h1')
        r.h.step(3 * DAY)
        r.press('nav')                                                        # wake: start display
        self.assertTrue(r.lines()[1].startswith('BOD2 '))
        r.press('nav')
        n = r.val('heads.status')['h1']['stored_count']
        self.assertGreaterEqual(n, 2)
        self.assertTrue(r.lines()[1].startswith('BOD'))
        first = r.lines()[1]
        r.press('nav')
        self.assertNotEqual(r.lines()[1], first)
        r.press('nav', long=True)                                             # graphic display
        self.assertEqual(r.var('s1'), 'graph')
        plot = next(w for w in r.lcd()['widgets'] if w['id'] == 'h1_plot')
        self.assertEqual(len(plot['series'][0]['points']), n)
        r.press('nav', long=True)
        self.assertEqual(r.var('s1'), 'stored')
        r.press('m')
        self.assertEqual(r.var('s1'), 'start')
        done('Read stored measured values')

    def test_set_the_language(self):
        r = Rig()
        r.press('m')
        r.press('enter', long=True)
        self.assertEqual(r.lines()[:3], ['General >', 'Clock function >', 'Service >'])
        r.press('enter')                                                      # General
        r.press('enter')                                                      # Language
        self.assertEqual(r.lines()[:4], ['Deutsch', 'English *', 'Francais', 'Espanol'])
        r.to('Deutsch')
        r.press('enter')                                                      # the head restarts
        self.assertEqual(r.val('heads.settings')['h1']['language'], 'de')
        self.assertEqual(r.var('s1'), 'start')
        r.call(r.A, 'head.start', head='h1')
        r.h.step(6 * 3600.0)
        r.press('m')
        self.assertTrue(r.lines()[1].startswith('BSB0.'))
        done('Set the language')

    def test_set_the_date_and_time(self):
        r = Rig()
        r.press('m')
        r.press('enter', long=True)
        r.to('Clock function')
        r.press('enter')
        r.press('enter')                                                      # Date...
        self.assertEqual(r.lines()[2], '19.10.2026')
        self.assertEqual(r.lcd()['inverse_spans'][2], [[0, 1]])               # the first digit is underscored
        r.digits('24122026')
        r.press('m')                                                          # take over the entire number
        self.assertEqual(r.val('heads.settings')['h1']['date'], '24.12.2026')
        self.assertEqual(r.lines()[:2], ['Date...', 'Time...'])
        r.to('Time')
        r.press('enter')
        r.digits('1730')
        r.press('m')
        self.assertEqual(r.val('heads.settings')['h1']['time'], '17:30')
        done('Set the date')

    def test_pneumatic_test(self):
        r = Rig()
        r.press('m')
        r.press('enter', long=True)
        r.to('Service')
        r.press('enter')
        r.to('OxiTop PT')
        r.press('enter')
        self.assertEqual(r.lines()[0], 'OxiTop PT')
        r.press('enter')
        r.press('enter')                                                      # the head is still on its bottle
        self.assertEqual(r.lines()[5], 'Unscrew head first')
        self.assertEqual(r.var('s1'), 'pt2')
        r.call(r.A, 'head.unscrew', head='h1')
        r.press('enter')
        self.assertEqual(r.var('s1'), 'pt3')
        pt = r.val('heads.settings')['h1']['pt_last']
        self.assertEqual(r.lines()[2], f"{pt['delta_p_pa'] / 100:.0f} hPa")
        r.press('m')
        self.assertEqual(r.var('s1'), 'service')
        done('Pneumatic test OxiTop-PT')

    def test_reset(self):
        r = Rig()
        r.press('m')
        r.press('enter', long=True)
        r.press('enter')
        self.assertEqual(r.var('s1'), 'gen')
        r.press('m', held=['nav'])                                            # <M> and the navigation key together
        self.assertEqual((r.var('s1'), r.lines()[0]), ('start', 'ID: 11'))
        done('Reset a head that does not react')


class Settings(unittest.TestCase):
    def test_measurement_settings(self):
        r = Rig()
        r.press('m')
        r.press('enter')
        r.press('enter')                                                      # Duration...
        self.assertEqual(r.lines()[:2], ['Duration...', '5 d'])
        r.press('nav')
        r.press('nav')                                                        # 7 d
        r.press('enter')
        r.to('Unit')
        r.press('enter')
        r.to('digit')
        r.press('m')
        r.to('Sample volume')
        r.press('enter')
        while r.lines()[1] != '164.0 ml':
            r.press('nav')
        r.press('enter')
        st = r.val('heads.status')['h1']
        self.assertEqual((st['duration_days'], st['unit'], round(st['sample_volume_m3'] * 1e6, 1)), (7, 'digit', 164.0))
        r.press('m')
        self.assertEqual(r.lines()[:4], ['BOD parameters', '7 d', '164.0 ml', 'digit'])

    def test_system_settings_id_switch_off_bottle(self):
        r = Rig()
        r.press('m')
        r.press('enter', long=True)
        r.press('enter')                                                      # General
        r.to('Illumination')
        self.assertEqual(r.highlighted(), ['Illumination *'])
        r.press('enter')
        self.assertFalse(r.val('heads.settings')['h1']['illumination'])
        r.to('Signal LED')
        r.press('enter')
        self.assertFalse(r.val('heads.settings')['h1']['signal_led'])
        r.to('Switch-off time')
        r.press('enter')
        self.assertEqual(r.lines()[1], '2 min')
        r.press('nav')
        r.press('m')
        self.assertEqual(r.val('heads.settings')['h1']['switch_off_s'], 180.0)
        r.to('ID')
        r.press('enter')
        r.digits('042')
        r.press('m')
        self.assertEqual(r.val('heads.status')['h1']['id'], 42)
        r.press('m')                                                          # up to the system menu
        r.to('Service')
        r.press('enter')
        r.to('Intern')
        r.press('enter')
        r.press('enter')                                                      # Bottle volume...
        self.assertEqual(r.lines()[1], '0510 ml')
        r.digits('0600')
        r.press('m')
        self.assertAlmostEqual(r.val('heads.settings')['h1']['bottle_volume_m3'], 600e-6)

    def test_switch_off_prompt_refusal_and_time_out(self):
        r = Rig()
        r.press('m')
        r.press('m', long=True)                                               # no measurement: the head switches off
        self.assertEqual((r.val('heads.status')['h1']['state'], r.lines()[0]), ('off', ''))
        r.press('m')
        r.press('m')
        r.press('m')                                                          # start
        r.press('m', long=True)
        self.assertEqual(r.lines()[0], 'Stop measurement?')
        r.press('enter')                                                      # no
        self.assertEqual(r.val('heads.status')['h1']['state'], 'adaption')
        r.press('enter')                                                      # measurement settings are locked while measuring
        self.assertEqual(r.var('s1'), 'start')
        r.call(r.A, 'head.set_language', head='h1', language='en')
        r.press('enter', long=True)
        r.press('enter')
        r.to('Switch-off time')
        r.h.step(121.0)                                                       # display switch-off time 2 min
        self.assertEqual(r.lines()[0], '')
        r.press('nav')                                                        # wake: start display, nothing else
        self.assertEqual((r.var('s1'), r.lines()[0]), ('start', 'ID: 11'))
        r.press('m', long=True)
        r.press('m')                                                          # yes: stop
        self.assertEqual(r.val('heads.status')['h1']['state'], 'off')
        r.press('m')
        r.press('m')
        r.call(r.A, 'head.start', head='h1')
        r.press('m', long=True)
        r.press('enter')
        r.press('enter', long=True)
        r.to('Service')
        r.press('enter')
        r.to('Intern')
        r.press('enter')
        r.press('enter')
        r.press('m')                                                          # bottle volume while measuring: refused
        self.assertEqual(r.lines()[5], 'Measurement running')

    def test_another_agent_cannot_stop_a_head_on_the_panel(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.press('m')
        r.press('m')
        r.press('m')                                                          # A starts head 1 on the panel: the head is A's
        self.assertEqual(r.h._owners['head:h1'], 'agent')
        pb = lambda key, long=False: r.call(B, 'panel.press', input_id=f'h1_{key}', duration_s=2.5 if long else 0.1)['accepted']   # noqa: E731
        self.assertEqual(r.call(B, 'head.stop', expect='rejected', head='h1')['error']['code'], 'ACCESS_DENIED')
        before = r.val('heads.status')['h1']
        self.assertTrue(pb('m', long=True))                                   # B's <M_> on A's head: the security prompt is only a screen (free)
        self.assertEqual(r.lines()[0], 'Stop measurement?')
        self.assertFalse(pb('m'))                                             # <M> = stop A's measurement: refused like the direct head.stop
        self.assertEqual(r.lines()[5], 'In use by other user')                # shown on the head's bottom line for 3 s (text ours)
        self.assertEqual(r.val('heads.status')['h1'], before)
        r.h.step(4.0)
        self.assertNotEqual(r.lines()[5], 'In use by other user')
        self.assertEqual(r.val('heads.status')['h1']['state'], 'adaption')
        self.assertEqual(r.call(B, 'head.read', head='h1')['head'], 'h1')      # viewing another agent's head is free
        self.assertTrue(r.call(B, 'panel.press', input_id='h2_m')['accepted'])   # B works on its own head
        self.assertEqual(r.val('heads.status')['h1']['state'], 'adaption')
        self.assertEqual(r.call(B, 'head.set_system', expect='rejected', head='h1', illumination=False)['error']['code'], 'ACCESS_DENIED')
        r.press('m', long=True)                                               # A stops its own measurement
        r.press('m')
        self.assertEqual(r.val('heads.status')['h1']['state'], 'off')

    def test_heads_are_independent(self):
        r = Rig()
        r.press('m', head=1)
        r.press('enter', head=1, long=True)
        r.press('m', head=4)
        self.assertEqual(r.lines(1)[0], 'General >')
        self.assertEqual(r.lines(4)[0], 'ID: 14')
        self.assertEqual(r.lines(2)[0], '')
        self.assertEqual(r.var('s4'), 'start')


class EquivalenceAndRender(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        p, d = Rig(), Rig()
        p.press('m')
        p.press('enter')
        p.press('enter')
        p.press('nav')
        p.press('enter')                                                      # 6 d
        p.press('m')
        p.press('m')                                                          # start
        d.call(d.A, 'head.read', head='h1')
        d.call(d.A, 'head.configure', head='h1', duration_days=6)
        d.call(d.A, 'head.start', head='h1')
        a, b = p.h.read('evaluator'), d.h.read('evaluator')
        self.assertEqual({k: v for k, v in a.items() if k != 'heads.settings'}, {k: v for k, v in b.items() if k != 'heads.settings'})
        p.h.step(DAY)
        d.h.step(DAY)
        self.assertEqual(p.h.read('evaluator')['heads.stored_values'], d.h.read('evaluator')['heads.stored_values'])

    def test_render_determinism_pngs_and_rejections(self):
        r = Rig()
        r.call(r.A, 'head.start', head='h1')
        r.h.step(8 * 3600.0)
        r.press('m')
        a = r.h.panel.png_bytes('h1_lcd')[0]
        self.assertEqual(a, r.h.panel.png_bytes('h1_lcd')[0])
        self.assertEqual(copy.deepcopy(r.h).panel.png_bytes('h1_lcd')[0], a)
        OUT.mkdir(parents=True, exist_ok=True)
        renders = {'home': r.h.panel.save_png('h1_lcd', OUT / 'home.png')}
        r.press('enter', long=True)
        r.press('enter')
        renders['menu'] = r.h.panel.save_png('h1_lcd', OUT / 'menu.png')
        r.press('m')
        r.press('m')
        r.press('m', long=True)
        renders['error'] = r.h.panel.save_png('h1_lcd', OUT / 'error.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')
        cap = r.call(r.A, 'panel.capture', display_id='h1_led_blue')
        self.assertEqual(cap['image']['width'], 8)
        self.assertEqual(r.call(r.A, 'panel.press', expect='rejected', input_id='h7_m')['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_spec_and_contract(self):
        spec = json.loads((rt.ASSET_DIR / 'panel_ui.json').read_text())
        self.assertEqual(pu.check_spec(spec, set(rt.CAPS)), [])
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + rt.APP_CAPS + pu.spec_capabilities(spec):
            self.assertIn(k, c['capabilities'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        self.assertEqual(sorted(set(SURVEY_TASKS) - DONE_TASKS), [], 'run the whole module')


if __name__ == '__main__':
    unittest.main()
