"""front-panel tests for CHEM-197 eppendorf_cryocubef570n (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_eppendorf_cryocubef570n.py -q

Every gesture goes through the session API (panel.press / panel.switch, the runtime's normal invoke path).  Each check looks at the runtime state
and at the screen twice: the structured frame (panel.frame) and the rendered pixels (a seven-segment reader that knows only the display geometry
of the spec; LED colours).  Themes: the six key procedures of the menu survey (set temperature, alarm limits, alarm delay, lock code, checking
values, lamp test); panel = direct capability (same runtime state); refusals and locks (no supply, lock code, ----, EE, wrong code, E with nothing
typed); time-outs (version at switch-on, stored values, messages) and no programming time-out; outage flashing, E-0x codes, mute; render
determinism and the PNGs of outputs/panel_ui/eppendorf_cryocubef570n/; contract entries of the panel capabilities."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import eppendorf_cryocubef570n as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'eppendorf_cryocubef570n_01'
T0 = 273.15
OUT = ROOT / 'outputs/panel_ui/eppendorf_cryocubef570n'
SPEC = rt.PANEL_SPEC
DISPLAY = SPEC['displays']['display']
LEDS = [k for k, v in SPEC['displays'].items() if v['kind'] == 'led']
SURVEY_TASKS = ['set the interior set temperature to -80 C', 'set the high and low alarm limits', 'set the on-site alarm delay',
                'activate or change the lock code', 'check set temperature and alarm limits without editing', 'test indicator lights and signal tone']
DONE_TASKS = set()
_SEGS = {v: k for k, v in pu.SEG7.items() if k in '0123456789-EP' and v}


# ---------------------------------------------------------------- reading the rendered display
def _lit(img, x, y):
    px = img[int(round(y)), int(round(x))]
    return int(px[0]) > 150 and int(px[1]) < 120                                   # red segment on the dark window (ghost segments stay dim)


def read_display(img):
    """Seven-segment reader: sample the middle of each segment of each cell (geometry from the spec only) and spell the field."""
    f = DISPLAY['fields']['main']
    cw, ch, gap = f['cell_w'], f['cell_h'], f['gap']
    t = max(2.0, cw * 0.18)
    out = ''
    for i in range(f['cells']):
        x = f['x'] + i * (cw + gap)
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2),
                   g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if _lit(img, px, py))
        out += _SEGS.get(segs, ' ' if not segs else '?')
        if _lit(img, x + cw + gap / 2.0, f['y'] + ch - max(1.5, t * 0.6)):
            out += '.'
    return out.strip()


def led_lit(img, name):
    c = img[img.shape[0] // 2, img.shape[1] // 2]
    return [int(v) for v in c] == SPEC['displays'][name]['colors']['on']


# ---------------------------------------------------------------- rig
class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.EppendorfCryocubef570nRuntime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        before = self.h.read('evaluator')
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            if cap != 'sim.step':
                assert self.h.t == t0, cap                                             # gestures take no simulated time
                after = self.h.read('evaluator')
                changed = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)}
                assert not changed - set(rt.EFFECTS.get(cap, [])), (cap, changed - set(rt.EFFECTS.get(cap, [])))
        return r['result'] if expect == 'succeeded' else r

    # gestures (agent session)
    def press(self, key):
        return self.call(self.A, 'panel.press', input_id=key)['accepted']

    def type(self, digits):
        return [self.press(f'key_{d}') for d in digits]

    def switch(self, name, pos):
        return self.call(self.A, 'panel.switch', input_id=name, position=pos)['accepted']

    # screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lcd(self):
        """Text shown now, from the frame and from the pixels (both must agree)."""
        f = self.frame()['displays']['display']['fields']['main']
        read = read_display(self.h.panel.render('display'))
        assert f['shown'].strip() == read, (f['shown'], read)
        return read

    def leds(self):
        """Lights lit now: frame and pixels must agree."""
        f = self.frame()['displays']
        on = {k for k in LEDS if f[k]['shown']}
        assert on == {k for k in LEDS if led_lit(self.h.panel.render(k), k)}, on
        return on

    def blinking(self):
        f = self.frame()['displays']
        return {k for k in LEDS if f[k]['blink']}

    def screen(self):
        return self.frame()['screen']

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def state(self, *ids):
        o = self.E.read(instance_id=self.inst, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def on(self, battery=True):
        if battery:
            assert self.switch('battery_switch', 'on')
        assert self.switch('mains_switch', 'on')
        assert self.screen() == 'boot'
        self.step(3.0)
        assert self.screen() == 'home'

    def program(self, code=None):
        assert self.press('lock_key')
        if code is not None:
            assert self.screen() == 'code_entry'
            self.type(code)
        assert self.state('panel.programming')


def runtime_state(h):
    r = h.read('evaluator')
    r.pop('clock.time_s')
    return json.dumps(dict(read=r, settings=h.settings, code=h.code, programming=h.programming), sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_set_the_interior_set_temperature(self):
        g = Rig()
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('off', '', set()))
        g.on()
        self.assertEqual(g.lcd(), '20')                                                # actual interior temperature (room, integer C)
        self.assertTrue(g.press('lock_key'))                                           # no lock code: programming mode at once
        self.assertTrue(g.state('panel.programming'))
        self.assertEqual(g.leds(), {'led_lock'})
        self.assertTrue(g.press('softkey_set_temp'))                                   # set temp light flashes, 0 shown
        self.assertEqual((g.screen(), g.lcd()), ('e_set', '0'))
        self.assertIn('led_set_temp', g.blinking())
        g.type('75')
        self.assertEqual(g.lcd(), '-75')                                               # shown negative automatically
        self.assertTrue(g.press('key_c'))                                              # C deletes the entry
        self.assertEqual(g.lcd(), '0')
        g.type('80')
        self.assertEqual(g.lcd(), '-80')
        self.assertTrue(g.press('key_e'))
        self.assertAlmostEqual(g.state('settings.current')['set_point_k'], T0 - 80.0, places=6)
        self.assertEqual((g.screen(), g.leds()), ('home', {'led_lock'}))              # the set temp light goes out
        self.assertTrue(g.press('lock_key'))                                           # leave programming mode
        self.assertFalse(g.state('panel.programming'))
        self.assertEqual(g.leds(), set())
        g.type('8')                                                                     # 8 now only shows the delay (30 min)
        self.assertEqual(g.lcd(), '30')
        DONE_TASKS.add('set the interior set temperature to -80 C')

    def test_set_the_high_and_low_alarm_limits(self):
        g = Rig()
        g.on()
        g.program()
        self.assertTrue(g.press('softkey_high_alarm'))
        self.assertEqual((g.lcd(), 'led_high_alarm' in g.blinking()), ('0', True))
        g.type('65')
        self.assertTrue(g.press('key_e'))
        self.assertTrue(g.press('softkey_low_alarm'))
        self.assertEqual((g.lcd(), 'led_low_alarm' in g.blinking()), ('0', True))
        g.type('88')
        self.assertTrue(g.press('key_e'))
        s = g.state('settings.current')
        self.assertAlmostEqual(s['high_alarm_k'], T0 - 65.0, places=6)
        self.assertAlmostEqual(s['low_alarm_k'], T0 - 88.0, places=6)
        self.assertTrue(g.press('softkey_high_alarm'))                                 # above -10 C: EE, type again
        g.type('5')
        self.assertFalse(g.press('key_e'))
        self.assertEqual((g.screen(), g.lcd()), ('e_high', 'EE'))
        g.step(2.0)
        self.assertEqual(g.lcd(), '0')
        g.type('78')                                                                    # closer than 5 K to the set point (-80): EE
        self.assertFalse(g.press('key_e'))
        self.assertEqual(g.lcd(), 'EE')
        self.assertAlmostEqual(g.state('settings.current')['high_alarm_k'], T0 - 65.0, places=6)
        self.assertTrue(g.press('lock_key'))                                           # unconfirmed entry discarded, programming left
        self.assertEqual((g.screen(), g.state('panel.programming')), ('home', False))
        DONE_TASKS.add('set the high and low alarm limits')

    def test_set_the_alarm_delays(self):
        g = Rig()
        g.on()
        g.program()
        self.assertTrue(g.press('key_8'))                                              # PP prompt
        self.assertEqual((g.screen(), g.lcd()), ('e_onsite', 'PP'))
        g.type('45')
        self.assertEqual(g.lcd(), '45')
        self.assertFalse(g.press('key_e'))                                             # outside 0-40 min: EE, repeat
        self.assertEqual(g.lcd(), 'EE')
        g.step(2.0)
        self.assertEqual(g.lcd(), 'PP')
        g.type('10')
        self.assertTrue(g.press('key_e'))
        self.assertEqual((g.screen(), g.lcd()), ('home', '---'))                       # --- saved
        self.assertEqual(g.state('settings.current')['onsite_delay_s'], 600.0)
        g.step(2.0)
        self.assertEqual(g.lcd(), '20')
        self.assertTrue(g.press('key_9'))                                              # remote delay 0 min (the runtime applies 15 s)
        g.type('0')
        self.assertTrue(g.press('key_e'))
        self.assertEqual(g.state('settings.current')['remote_delay_s'], 0.0)
        self.assertTrue(g.press('lock_key'))
        DONE_TASKS.add('set the on-site alarm delay')

    def test_activate_change_and_deactivate_the_lock_code(self):
        g = Rig()
        g.on()
        g.program()
        self.assertTrue(g.press('softkey_code_change'))                                # light flashes, display empty
        self.assertEqual((g.screen(), g.lcd(), 'led_code_change' in g.blinking()), ('e_code', '', True))
        g.type('47')
        self.assertFalse(g.press('key_e'))                                             # fewer than four digits: E does nothing
        self.assertEqual(g.lcd(), '47')
        g.type('11')
        self.assertEqual(g.lcd(), '4711')
        self.assertFalse(g.press('key_5'))                                             # a fifth digit is not taken
        self.assertTrue(g.press('key_e'))
        self.assertTrue(g.state('panel.code_active'))
        self.assertTrue(g.press('lock_key'))
        self.assertEqual((g.state('panel.programming'), g.leds()), (False, set()))
        self.assertTrue(g.press('softkey_set_temp'))                                   # checking is still allowed
        self.assertEqual(g.lcd(), '-80')
        self.assertTrue(g.press('lock_key'))                                           # lock light flashes: enter the code
        self.assertEqual((g.screen(), g.blinking() >= {'led_lock'}), ('code_entry', True))
        self.assertTrue(g.press('softkey_set_temp'))                                   # softkey while the light flashes: ----
        self.assertEqual(g.lcd(), '----')
        g.step(2.0)
        g.type('123')
        self.assertEqual(g.lcd(), '123')
        self.assertFalse(g.press('key_4'))                                             # wrong code: entry cleared, light keeps flashing
        self.assertEqual((g.screen(), g.state('panel.programming')), ('code_entry', False))
        self.assertEqual(g.lcd(), '20')
        g.type('4711')
        self.assertEqual((g.screen(), g.state('panel.programming'), g.leds()), ('home', True, {'led_lock'}))
        self.assertTrue(g.press('softkey_code_change'))                                # 0000 deactivates the code
        g.type('0000')
        self.assertTrue(g.press('key_e'))
        self.assertFalse(g.state('panel.code_active'))
        self.assertTrue(g.press('lock_key'))
        self.assertTrue(g.press('lock_key'))                                           # no code: programming at once again
        self.assertEqual((g.screen(), g.state('panel.programming')), ('home', True))
        DONE_TASKS.add('activate or change the lock code')

    def test_check_values_without_editing(self):
        g = Rig()
        g.on()
        for key, text in (('softkey_set_temp', '-80'), ('softkey_high_alarm', '-70'), ('softkey_low_alarm', '-90'), ('key_8', '30'), ('key_9', '30')):
            self.assertTrue(g.press(key))
            self.assertEqual((g.screen(), g.lcd()), ('home', text), key)
        g.step(5.0)                                                                     # the stored value is shown for 5 s
        self.assertEqual(g.lcd(), '20')
        for key in ('softkey_code_change', 'key_e', 'key_c', 'key_1'):                 # nothing else reacts outside programming mode
            self.assertFalse(g.press(key), key)
        self.assertEqual(g.state('settings.current'), Rig().h.settings)
        DONE_TASKS.add('check set temperature and alarm limits without editing')

    def test_lamp_test_and_mute(self):
        g = Rig()
        g.on()
        self.assertTrue(g.press('mute_key'))                                           # no alarm: lamp and tone test
        self.assertEqual((g.lcd(), g.leds(), g.state('alarm.audible')), ('8888', set(LEDS), True))
        g.step(rt.ENG['mute_test_s'])
        self.assertEqual((g.lcd(), g.leds(), g.state('alarm.audible')), ('20', set(), False))
        g.call(g.E, 'fault.inject', fault='interior_sensor_failure', active=True)       # E-01: tone, fault light, code on the display
        g.step(1.0)
        self.assertEqual((g.lcd(), g.state('alarm.audible')), ('E-01', True))
        self.assertIn('led_fault', g.leds())
        self.assertTrue(g.press('mute_key'))                                           # with an alarm: the tone is silenced
        self.assertFalse(g.state('alarm.audible'))
        self.assertEqual(g.lcd(), 'E-01')
        DONE_TASKS.add('test indicator lights and signal tone')


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def test_programming_sequence_equals_direct_calls(self):
        a, b = Rig(), Rig()
        a.on()
        a.program()
        for key, digits in (('softkey_high_alarm', '60'), ('softkey_set_temp', '70'), ('softkey_low_alarm', '80'), ('key_8', '5'), ('key_9', '12')):
            self.assertTrue(a.press(key))
            a.type(digits)
            self.assertTrue(a.press('key_e'))
        self.assertTrue(a.press('softkey_code_change'))
        a.type('2468')
        self.assertTrue(a.press('key_e'))
        self.assertTrue(a.press('lock_key'))
        b.call(b.A, 'battery.set', on=True)
        b.call(b.A, 'power.set', on=True)
        b.step(3.0)
        b.call(b.A, 'panel.unlock', code='0000')
        for args in (dict(high_alarm_k=213.15), dict(set_point_k=203.15), dict(low_alarm_k=193.15), dict(onsite_delay_s=300.0), dict(remote_delay_s=720.0)):
            b.call(b.A, 'settings.configure', **args)
        b.call(b.A, 'code.change', code='2468')
        b.call(b.A, 'panel.lock')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.step(3600.0)
        b.step(3600.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))

    def test_switches_vent_and_mute_equal_direct_calls(self):
        a, b = Rig(), Rig()
        a.on()
        b.call(b.A, 'battery.set', on=True)
        b.call(b.A, 'power.set', on=True)
        b.step(3.0)
        a.step(4 * 3600.0)
        b.step(4 * 3600.0)
        self.assertTrue(a.press('auto_vent_button'))
        b.call(b.A, 'vent.press')
        self.assertTrue(a.switch('mains_switch', 'off'))                               # outage on the battery circuit
        b.call(b.A, 'power.set', on=False)
        a.step(60.0)
        b.step(60.0)
        self.assertTrue(a.press('mute_key'))
        b.call(b.A, 'alarm.mute')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        self.assertEqual(a.h.vent_until, b.h.vent_until)


# ---------------------------------------------------------------- rules, refusals, time-outs
class Rules(unittest.TestCase):
    def test_no_supply_gate(self):
        g = Rig()
        for key in ('lock_key', 'softkey_set_temp', 'key_1', 'mute_key'):
            self.assertFalse(g.press(key), key)
        self.assertTrue(g.press('auto_vent_button'))                                   # the vent key works without supply
        self.assertTrue(g.switch('battery_switch', 'on'))                              # battery circuit alone: panel on battery (power fail)
        self.assertEqual(g.screen(), 'boot')
        g.step(3.0)
        self.assertIn('POWER_FAIL', g.state('alarms.active'))
        self.assertEqual(g.blinking() & {'led_power_fail'}, {'led_power_fail'})
        self.assertTrue(g.switch('battery_switch', 'off'))
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('off', '', set()))
        self.assertFalse(g.press('lock_key'))

    def test_outage_display_flashes_every_10_s(self):
        g = Rig()
        g.on()
        g.step(600.0)
        self.assertTrue(g.switch('mains_switch', 'off'))                               # 7.5.2: power fail light, display flashes
        self.assertEqual(g.screen(), 'home')                                            # the panel stays on (battery)
        shown = []
        for _ in range(4):
            g.step(5.0)
            shown.append(g.lcd() != '')
        self.assertIn(True, shown)
        self.assertIn(False, shown)
        self.assertIn('led_power_fail', g.blinking())
        self.assertTrue(g.switch('mains_switch', 'on'))                                # switching on again shows the version
        self.assertEqual((g.screen(), g.lcd()), ('boot', '1.00'))

    def test_boot_ignores_keys_and_times_out(self):
        g = Rig()
        g.switch('mains_switch', 'on')
        self.assertFalse(g.press('lock_key'))
        g.step(2.9)
        self.assertEqual(g.screen(), 'boot')
        g.step(0.1)
        self.assertEqual(g.screen(), 'home')

    def test_programming_mode_has_no_time_out(self):
        g = Rig()
        g.on()
        g.program()
        self.assertTrue(g.press('softkey_set_temp'))
        g.type('7')
        g.step(3600.0)
        self.assertEqual((g.screen(), g.lcd()), ('e_set', '-7'))
        self.assertFalse(g.press('key_e'))                                             # -7 C is outside -50 ... -86: EE
        self.assertEqual(g.lcd(), 'EE')
        self.assertFalse(g.press('key_e'))                                             # nothing typed: E does nothing
        self.assertAlmostEqual(g.state('settings.current')['set_point_k'], T0 - 80.0, places=6)

    def test_set_point_that_breaks_the_limits_shows_ee(self):
        g = Rig()
        g.on()
        g.program()
        self.assertTrue(g.press('softkey_set_temp'))
        g.type('86')                                                                    # low limit -90 would be above -91 ... -86 - 5
        self.assertFalse(g.press('key_e'))
        self.assertEqual((g.screen(), g.lcd()), ('e_set', 'EE'))
        self.assertTrue(g.press('softkey_low_alarm'))                                  # another softkey switches the entry
        self.assertEqual(g.screen(), 'e_low')
        g.type('91')
        self.assertTrue(g.press('key_e'))
        self.assertTrue(g.press('softkey_set_temp'))
        g.type('86')
        self.assertTrue(g.press('key_e'))
        self.assertAlmostEqual(g.state('settings.current')['set_point_k'], T0 - 86.0, places=6)

    def test_direct_lock_leaves_an_open_entry(self):
        g = Rig()
        g.on()
        g.program()
        self.assertTrue(g.press('softkey_high_alarm'))
        g.call(g.A, 'panel.lock')
        self.assertEqual(g.screen(), 'home')

    def test_unknown_inputs_rejected(self):
        g = Rig()
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='key_x')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.switch', expect='rejected', input_id='mains_switch', position='half')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.A.invoke(instance_id=INST, capability_id='panel.frame', arguments={}, request_id='f1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')


# ---------------------------------------------------------------- renders and contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.on()
            g.step(5 * 3600.0)
            return g
        a, b = scenario(), scenario()
        for d in SPEC['displays']:
            ia, ib = a.h.panel.render(d), b.h.panel.render(d)
            self.assertEqual((ia.dtype.name, ia.shape[2]), ('uint8', 3))
            self.assertTrue((ia == ib).all())
            self.assertTrue((ia == a.h.panel.render(d)).all())
        self.assertEqual(a.h.panel.png_bytes('display'), b.h.panel.png_bytes('display'))
        OUT.mkdir(parents=True, exist_ok=True)
        self.assertEqual(a.lcd(), '-80')
        shots = {'home': a.h.panel.save_png('display', OUT / 'home.png')}
        a.program()
        a.press('softkey_set_temp')
        a.type('82')
        self.assertEqual(a.lcd(), '-82')
        shots['menu'] = a.h.panel.save_png('display', OUT / 'menu.png')
        shots['menu_led_set_temp'] = a.h.panel.save_png('led_set_temp', OUT / 'menu_led_set_temp.png')
        a.press('key_8')                                                                # third digit not taken
        a.press('key_c')
        a.type('5')
        a.press('key_e')
        self.assertEqual(a.lcd(), 'EE')
        shots['error'] = a.h.panel.save_png('display', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        self.assertEqual((shots['home']['width'], shots['home']['height']), tuple(DISPLAY['size']))
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='display')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.on()
        r2 = g.call(g.A, 'panel.capture', display_id='display')
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        self.assertEqual(g.call(g.A, 'panel.capture', display_id='display'), r2)       # same screen, same file

    def test_spec_and_contract(self):
        meta = rt.export_eppendorf_cryocubef570n_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.switch', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in rt.PANEL_CAPS:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'preconditions', 'errors'):
                self.assertEqual(c['capabilities'][k][key], meta['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for inp in SPEC['inputs'].values():
            self.assertIn(inp['component_id'], c['components'])
        for d in SPEC['displays'].values():
            self.assertIn(d['component_id'], c['components'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'parameter_cycle', pu.SPEC_VERSION))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((rt.ASSET_DIR / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests'):                              # shared files: registered unpinned
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('cryocube_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
