"""front-panel tests for CHEM-164 biorad_miniprotean (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/biorad_miniprotean.py):
python3 -m pytest tests/test_panel_ui_biorad_miniprotean.py -q

Every gesture goes through the session API (panel.press / panel.switch, the runtime's normal invoke path).  Each check looks at the runtime state and
at the screen twice: the structured frame (panel.frame) and the rendered pixels, read back with a seven-segment reader that knows only the display
geometry of the spec and an LED reader that looks at the lamp colour.  Themes: the four key procedures of the menu survey (timed constant-voltage
run, pause and edit, power-failure detection, stop and power down) plus every other panel function of the PowerPac Basic manual (held scroll keys,
dE9, firmware version at switch-on, error codes and their keys); panel = direct capability (same runtime state); refusals and locks (power off, limit
edits while running, untimed time edit, firmware display, chords, foreign run); time-outs (Pfd / dE9 messages, lamp test and version); render
determinism and the PNGs of outputs/panel_ui/biorad_miniprotean/; contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import biorad_miniprotean as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'electrophoresis_01'
OUT = ROOT / 'outputs/panel_ui/biorad_miniprotean'
ASSET = rt.ASSET_DIR
SPEC = rt.PANEL_SPEC
DISPLAY = SPEC['displays']['display']
LEDS = ('led_volts', 'led_milliamps', 'led_time', 'led_run', 'led_pause', 'led_constant_v', 'led_constant_a')
SDS, TGX = 'tris_glycine_sds_1x', 'tgx_4_20_10w_30ul'
SURVEY_TASKS = ['run at constant voltage for a set time', 'pause and edit during a run', 'enable power failure detection', 'stop and power down']
DONE_TASKS = set()
_PREF = '0123456789EPFdbrnoAcCHLU-'
_CHARS = {}
for _c in _PREF:
    _CHARS.setdefault(pu.SEG7[_c], _c)


# ---------------------------------------------------------------- reading the rendered display
def _lit(img, x, y):
    return int(img[int(round(y)), int(round(x))][0]) > 160                         # red segment over the dark window


def read_field(img, fid='main'):
    """Seven-segment reader: sample the middle of each segment of each cell (geometry from the spec only) and spell the field."""
    f = DISPLAY['fields'][fid]
    n, cw, ch, gap = f['cells'], f['cell_w'], f['cell_h'], f.get('gap', 6)
    t = f.get('thickness', max(2.0, cw * 0.18))
    out = ''
    for i in range(n):
        x = f['x'] + i * (cw + gap)
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2),
                   g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if _lit(img, px, py))
        out += _CHARS.get(segs, ' ' if not segs else '?')
        r = max(1.5, t * 0.6)
        if _lit(img, x + cw + gap / 2.0, f['y'] + ch - r):
            out += '.'
    return out.strip()


def norm(text):
    """What the pixels can tell apart: the lit segments of each cell (f and F look the same), and the points."""
    return tuple(ch if ch == '.' else pu.SEG7.get(ch, pu.SEG7.get(ch.upper(), pu.SEG7.get(ch.lower(), ''))) or '_' for ch in text.replace(' ', ''))


# ---------------------------------------------------------------- rig
class Rig:
    """Supply and Tetra cell with two TGX gels, buffer, lid and leads in place (the cell is set up by direct robot skills; the supply only through
    its panel), the supply switched off."""

    def __init__(self, cell=True, power=False):
        self.h = rt.BioradMiniproteanRuntime(INST)
        self.h.panel_capture_dir = tempfile.mkdtemp(prefix='miniprotean_panel_')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        if cell:
            self.call(self.A, 'leads.disconnect')
            self.call(self.A, 'lid.remove')
            for i in (1, 2):
                self.call(self.A, 'gel.provide', gel_id=f'g{i}', gel_type=TGX)
                self.call(self.A, 'gel.prepare', gel_id=f'g{i}', remove_comb=True, remove_tape=True, rinse_wells=True)
                self.call(self.A, 'gel.install', item=f'g{i}', position=i)
            self.call(self.A, 'buffer.fill', chamber='inner', buffer_id=SDS, volume_m3=150e-6, temperature_k=295.15)
            self.call(self.A, 'buffer.fill', chamber='outer', buffer_id=SDS, volume_m3=550e-6, temperature_k=295.15)
            self.call(self.A, 'lid.place')
            self.call(self.A, 'leads.connect', polarity='normal')
        if power:
            assert self.switch('on')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0 + (args['dt_s'] if cap == 'sim.step' else 0.0)       # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    # gestures (agent session)
    def press(self, key, hold=0.1, held=()):
        args = dict(input_id=key, duration_s=hold)
        if held:
            args['held'] = list(held)
        return self.call(self.A, 'panel.press', **args)['accepted']

    def presses(self, key, n):
        return all(self.press(key) for _ in range(n))

    def switch(self, pos, held=()):
        args = dict(input_id='switch_power', position=pos)
        if held:
            args['held'] = list(held)
        return self.call(self.A, 'panel.switch', **args)['accepted']

    # screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def image(self, display_id='display'):
        """The display as the close-up camera sees it (panel.capture through the agent session)."""
        from PIL import Image
        r = self.call(self.A, 'panel.capture', display_id=display_id)
        return np.asarray(Image.open(io.BytesIO(Path(r['image']['path']).read_bytes())).convert('RGB'))

    def lcd(self):
        """The digits shown now, from the frame and from the pixels (both must agree)."""
        shown = self.frame()['displays']['display']['fields']['main']['shown'].strip()
        read = read_field(self.image())
        assert norm(shown) == norm(read), (shown, read)
        return shown

    def leds(self):
        f = self.frame()
        on = {k for k in LEDS if f['displays'][k]['shown']}
        lit = {k for k in LEDS if tuple(self.image(k)[12, 12]) == tuple(SPEC['displays'][k]['colors']['on'])}
        assert on == lit, (on, lit)
        return on

    def screen(self):
        return self.frame()['screen']

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def state(self, *ids):
        o = self.E.read(instance_id=INST, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def to_mode(self, mode):
        """Display mode key until the LED of that parameter is lit."""
        for _ in range(3):
            if self.state('display.mode') == mode:
                return
            assert self.press('key_display_mode')
        raise AssertionError(mode)

    def to_constant(self, constant):
        if self.state('psu.constant') != constant:
            assert self.press('key_constant')
        assert self.leds() >= {'led_constant_v' if constant == 'voltage' else 'led_constant_a'}

    def set_value(self, target):
        """Scroll keys only: holds for the tens, presses for the units (the shown value is the set value while stopped or paused)."""
        cur = int(self.lcd())
        d = target - cur
        key = 'key_up' if d > 0 else 'key_down'
        n_fast = (abs(d) - 5) // 10 if abs(d) >= 15 else 0
        if n_fast:                                                                    # 5 units by 1, then n_fast by 10 (repeats after 0.5 s at 5/s)
            assert self.press(key, hold=0.5 + (4 + n_fast) / 5.0 + 0.01)
            cur = int(self.lcd())
            d = target - cur
            key = 'key_up' if d > 0 else 'key_down'
        assert self.presses(key, abs(d))
        assert int(self.lcd()) == target, (self.lcd(), target)


def runtime_state(h):
    v = h.read('evaluator')
    return json.dumps(dict(v, **{'clock.time_s': None}), sort_keys=True, default=float), json.dumps(h.records, sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_run_at_constant_voltage_for_a_set_time(self):
        g = Rig()
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('off', '', set()))
        self.assertTrue(g.switch('on'))                                                # p.10 step 1: constant V, the voltage shown
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('home', '200', {'led_volts', 'led_constant_v'}))
        self.assertTrue(g.press('key_constant'))                                       # step 3: the constant key toggles V / A
        self.assertEqual(g.leds(), {'led_volts', 'led_constant_a'})
        g.to_constant('voltage')
        g.set_value(180)                                                                # step 4: 180 V
        g.to_mode('mA')                                                                 # step 5: the current limit
        self.assertEqual((g.lcd(), g.leds()), ('400', {'led_milliamps', 'led_constant_v'}))
        g.set_value(300)
        g.to_mode('min')                                                                # step 6: a timed run
        self.assertEqual((g.lcd(), g.leds()), ('0', {'led_time', 'led_constant_v'}))
        g.set_value(30)
        self.assertEqual(g.state('psu.voltage_set_v', 'psu.current_set_a', 'psu.time_set_s'),
                         {'psu.voltage_set_v': 180.0, 'psu.current_set_a': 0.3, 'psu.time_set_s': 1800.0})
        self.assertTrue(g.press('key_run_pause'))                                       # step 9: run, the run LED lit
        self.assertEqual(g.state('psu.state'), 'running')
        g.step(60.0)
        self.assertEqual((g.lcd(), g.leds()), ('29', {'led_time', 'led_constant_v', 'led_run'}))   # remaining minutes
        g.to_mode('V')
        self.assertLessEqual(abs(int(g.lcd()) - 180), 6)                                # the reading (accuracy 2 % or 3 V)
        g.step(1800.0)
        rec = g.h.records[g.state('result.last_record_id')]
        self.assertEqual((rec['status'], rec['run_time_s'], g.state('psu.state')), ('completed', 1800.0, 'stopped'))
        self.assertEqual((g.lcd(), g.leds()), ('180', {'led_volts', 'led_constant_v'}))   # step 12: values kept, neither run nor pause LED
        g.to_mode('min')
        self.assertEqual(g.lcd(), '30')                                                 # the programmed time is kept, the timer reset
        DONE_TASKS.add('run at constant voltage for a set time')

    def test_pause_and_edit_during_a_run(self):
        g = Rig(power=True)
        g.to_mode('min')
        g.set_value(20)
        self.assertTrue(g.press('key_run_pause'))
        g.step(120.0)
        self.assertTrue(g.press('key_down'))                                            # p.12 step 10: the time of a timed run is editable while running
        self.assertEqual(g.state('psu.time_set_s'), 1140.0)
        g.to_mode('mA')
        before = runtime_state(g.h)
        self.assertFalse(g.press('key_down'))                                           # the limit is edited only in pause
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'LIMIT_EDIT_NEEDS_PAUSE')
        self.assertEqual(runtime_state(g.h), before)
        self.assertFalse(g.press('key_constant'))
        self.assertTrue(g.press('key_run_pause'))                                       # step 11: pause, the pause LED lit, no output
        self.assertEqual((g.state('psu.state'), g.leds()), ('paused', {'led_milliamps', 'led_constant_v', 'led_pause'}))
        self.assertEqual(g.lcd(), '400')                                                # paused: the set value
        g.set_value(250)
        self.assertTrue(g.press('key_constant'))                                        # the constant parameter changes in pause
        self.assertEqual(g.leds(), {'led_milliamps', 'led_constant_a', 'led_pause'})
        g.to_mode('min')
        self.assertEqual(g.lcd(), '17')                                                 # the remaining time is kept
        self.assertTrue(g.press('key_run_pause'))                                       # continue
        g.step(30.0)
        self.assertEqual(g.state('psu.state', 'psu.constant', 'psu.current_set_a'), {'psu.state': 'running', 'psu.constant': 'current', 'psu.current_set_a': 0.25})
        self.assertIn('led_run', g.leds())
        DONE_TASKS.add('pause and edit during a run')

    def test_enable_power_failure_detection(self):
        g = Rig(power=True)
        g.to_mode('min')
        self.assertTrue(g.press('key_stop', hold=2.0))                                  # untimed: no Pfd (p.11 step 7: timed runs only)
        self.assertEqual((g.state('psu.power_failure_detection'), g.lcd()), (False, '0'))
        g.set_value(10)
        self.assertTrue(g.press('key_stop', hold=2.0))                                  # time LED lit, time entered, stop held ~2 s
        self.assertEqual((g.lcd(), g.state('psu.power_failure_detection')), ('Pfd', True))
        g.step(2.0)
        self.assertEqual(g.lcd(), '10')                                                 # the message times out
        self.assertTrue(g.press('key_run_pause'))
        g.step(240.0)
        self.assertEqual(g.call(g.E, 'fault.inject', fault='power_loss', active=True)['faults'], ['power_loss'])
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('off', '', set()))            # mains failure: dark
        g.step(120.0)
        g.call(g.E, 'fault.inject', fault='power_loss', active=False)
        g.call(g.E, 'plant.configure', **rt.PLANT_EXAMPLE)                             # the mains is back with the switch on: the run resumes
        self.assertEqual((g.screen(), g.state('psu.state')), ('home', 'running'))
        g.step(400.0)
        self.assertEqual(g.lcd(), 'E5')                                                 # p.13: completed after a power failure with Pfd
        self.assertEqual(g.state('psu.power_failure_detection'), False)                 # de-activated after the run
        self.assertTrue(g.press('key_display_mode'))                                    # any key clears E5
        self.assertEqual(g.lcd(), '10')
        DONE_TASKS.add('enable power failure detection')

    def test_stop_and_power_down(self):
        g = Rig(power=True)
        self.assertTrue(g.press('key_run_pause'))
        g.step(30.0)
        self.assertTrue(g.press('key_stop'))                                            # p.12 step 14: stop first
        self.assertEqual((g.state('psu.state'), g.leds()), ('stopped', {'led_volts', 'led_constant_v'}))
        self.assertTrue(g.switch('off'))
        self.assertEqual((g.screen(), g.lcd(), g.leds(), g.state('power.enabled')), ('off', '', set(), False))
        self.assertTrue(g.switch('on'))
        self.assertEqual(g.lcd(), '200')                                                # no error code: stopped before switching off
        self.assertTrue(g.press('key_run_pause'))
        g.step(30.0)
        self.assertTrue(g.switch('off'))                                                # switched off during a run: a power failure
        self.assertTrue(g.switch('on'))
        self.assertEqual(g.lcd(), 'E7')
        self.assertTrue(g.press('key_stop'))
        self.assertEqual(g.lcd(), '200')
        DONE_TASKS.add('stop and power down')


# ---------------------------------------------------------------- other panel functions of the manual
class OtherPanelFunctions(unittest.TestCase):
    def test_held_scroll_keys(self):
        g = Rig(power=True)
        self.assertTrue(g.press('key_up', hold=0.4))                                    # below the repeat delay: one step
        self.assertEqual(g.lcd(), '201')
        self.assertTrue(g.press('key_up', hold=1.3))                                    # 5 units by 1
        self.assertEqual(g.lcd(), '206')
        self.assertTrue(g.press('key_down', hold=2.1))                                  # 5 by 1, then 4 by 10 (p.9)
        self.assertEqual(g.lcd(), '161')
        self.assertTrue(g.press('key_down', hold=60.0))                                 # stops at 10 V
        self.assertEqual(g.lcd(), '10')
        g.to_mode('mA')
        self.assertTrue(g.press('key_down', hold=60.0))
        self.assertEqual(g.lcd(), '4')                                                  # 4 mA minimum

    def test_de9_switches_the_resistance_check_off_for_one_run(self):
        g = Rig(power=True)
        g.to_mode('mA')
        self.assertTrue(g.press('key_stop', hold=2.0))                                  # p.11 step 8: current LED lit, stop held ~2 s
        self.assertEqual((g.lcd(), g.state('psu.resistance_check')), ('dE9', False))
        g.step(2.0)
        self.assertEqual(g.lcd(), '400')
        g.to_mode('V')
        self.assertTrue(g.press('key_run_pause'))
        g.step(60.0)
        g.call(g.E, 'fault.inject', fault='lead_break', active=True)                   # loose lead: no E9 hold, the run ends with no load
        g.step(1.0)
        self.assertEqual((g.lcd(), g.state('psu.state'), g.state('psu.resistance_check')), ('E1', 'error', True))   # re-activated after the run
        g.call(g.E, 'fault.inject', fault='lead_break', active=False)
        self.assertTrue(g.press('key_up'))                                              # any key clears the code
        self.assertTrue(g.press('key_run_pause'))
        g.step(60.0)
        g.call(g.E, 'fault.inject', fault='lead_break', active=True)
        g.step(1.0)
        self.assertEqual((g.lcd(), g.state('psu.state')), ('E9', 'error'))              # detection active again: E9 holds the run
        g.call(g.E, 'fault.inject', fault='lead_break', active=False)
        self.assertTrue(g.press('key_run_pause'))                                       # p.14: run/pause resumes
        self.assertEqual((g.state('psu.state'), len(g.h.records)), ('running', 2))

    def test_firmware_version_at_switch_on(self):
        g = Rig()
        self.assertTrue(g.switch('on', held=['key_constant']))                         # p.15: constant key held while switching on
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('fw_test', '8.8.8.', set(LEDS)))
        self.assertFalse(g.press('key_run_pause'))                                      # keys do nothing meanwhile
        g.step(1.0)
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('fw_version', '1.00', set()))
        g.step(3.0)
        self.assertEqual((g.screen(), g.lcd()), ('home', '200'))
        self.assertTrue(g.switch('off'))
        self.assertTrue(g.switch('on'))                                                 # without the key: straight to the value
        self.assertEqual(g.screen(), 'home')

    def test_error_codes_on_the_display(self):
        g = Rig(power=True)
        g.call(g.A, 'leads.disconnect')
        self.assertTrue(g.press('key_run_pause'))                                       # p.13: E1 no load
        self.assertEqual((g.lcd(), g.leds()), ('E1', {'led_volts', 'led_constant_v'}))
        self.assertTrue(g.press('key_constant'))                                        # any key clears the code (and only clears it)
        self.assertEqual((g.lcd(), g.state('psu.constant')), ('200', 'voltage'))
        g.call(g.A, 'leads.connect', polarity='normal')
        self.assertTrue(g.press('key_run_pause'))
        g.step(10.0)
        g.call(g.E, 'fault.inject', fault='short_circuit', active=True)
        g.step(1.0)
        self.assertEqual((g.lcd(), g.leds()), ('E2', {'led_volts', 'led_constant_v'}))   # over current: run held, run LED off
        g.call(g.E, 'fault.inject', fault='short_circuit', active=False)
        self.assertTrue(g.press('key_stop'))                                            # any other key than run/pause: cleared, run ended
        self.assertEqual((g.lcd(), g.h.records['EP0002']['status']), ('200', 'error'))


class SupplyFaults(unittest.TestCase):
    """Sync audit 2026-10-19: the supply fault codes of manual 4.2 (raised by the evaluator's fault.inject) with their documented display and
    remedy: E3 / E8 power cycle; E13-E15 any key but run/pause; E12, E16, E17, E98, E99 Bio-Rad service (fault.inject active=false)."""
    def running(self):
        g = Rig(power=True)
        self.assertTrue(g.press('key_run_pause'))
        g.step(10.0)
        self.assertEqual(g.state('psu.state'), 'running')
        return g, g.h.op['record_id']

    def assert_fault_shown(self, g, rid, code):
        self.assertEqual((g.lcd(), g.state('psu.state'), g.state('display.error_code')), (code, 'error', code))
        self.assertEqual(g.state('psu.output_voltage_v'), 0.0)                          # the output is off
        self.assertEqual((g.h.records[rid]['status'], g.h.records[rid]['error_codes']), ('error', [code]))

    def test_e3_and_e8_are_reset_by_switching_off_and_on(self):
        for code in ('E3', 'E8'):
            g, rid = self.running()
            g.call(g.E, 'fault.inject', fault=code, active=True)
            self.assert_fault_shown(g, rid, code)
            g.call(g.E, 'fault.inject', fault=code, active=False)                        # the cause is gone, the code stays
            before = runtime_state(g.h)
            for key in ('key_stop', 'key_run_pause', 'key_constant', 'key_display_mode'):
                self.assertFalse(g.press(key))                                          # keys do not clear it
            self.assertEqual(g.call(g.A, 'psu.start', expect='rejected')['error']['code'], 'SUPPLY_FAULT')
            self.assertEqual(runtime_state(g.h), before)
            self.assertTrue(g.switch('off'))
            self.assertTrue(g.switch('on'))
            self.assertEqual((g.lcd(), g.state('psu.state')), ('200', 'stopped'))
        g = Rig(power=True)                                                             # a persisting problem returns with the next run
        g.call(g.E, 'fault.inject', fault='E8', active=True)
        self.assertEqual(g.lcd(), 'E8')
        g.switch('off')
        g.switch('on')
        self.assertEqual(g.lcd(), '200')
        self.assertTrue(g.press('key_run_pause'))
        self.assertEqual((g.lcd(), g.h.records[g.state('result.last_record_id')]['error_codes']), ('E8', ['E8']))

    def test_e13_to_e15_are_cleared_by_any_key_but_run_pause(self):
        for code in ('E13', 'E14', 'E15'):
            g, rid = self.running()
            g.call(g.E, 'fault.inject', fault=code, active=True)
            self.assert_fault_shown(g, rid, code)
            g.call(g.E, 'fault.inject', fault=code, active=False)                        # contacts / wires / buffer corrected
            self.assertFalse(g.press('key_run_pause'))
            self.assertEqual(g.lcd(), code)
            self.assertTrue(g.press('key_stop'))
            self.assertEqual((g.lcd(), g.state('psu.state')), ('200', 'stopped'))
            self.assertTrue(g.press('key_run_pause'))                                   # a new run works
            self.assertEqual(g.state('psu.state'), 'running')

    def test_hardware_failures_stay_until_service(self):
        for code in ('E12', 'E16', 'E17', 'E98', 'E99'):
            g, rid = self.running()
            g.call(g.E, 'fault.inject', fault=code, active=True)
            self.assert_fault_shown(g, rid, code)
            self.assertFalse(g.press('key_stop'))
            self.assertTrue(g.switch('off'))
            self.assertTrue(g.switch('on'))
            self.assertEqual((g.lcd(), g.state('psu.state')), (code, 'error'))         # also after a power cycle
            self.assertEqual(g.call(g.A, 'psu.start', expect='rejected')['error']['code'], 'SUPPLY_FAULT')
            self.assertNotIn(code, g.call(g.E, 'fault.inject', fault=code, active=False)['faults'])        # Bio-Rad service repairs it
            self.assertEqual((g.lcd(), g.state('psu.state'), g.state('truth.faults')), ('200', 'stopped', []))

    def test_the_spec_lists_the_codes_as_simulated(self):
        rows = {d['code']: d for d in SPEC['errors']['device']}
        for code in rt.HW_FAULTS:
            self.assertTrue(rows[code]['simulated'], code)
        self.assertFalse(any('E12' in u['item'] for u in SPEC['unsupported']))


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def test_panel_and_direct_calls_give_the_same_runtime_state(self):
        a, b = Rig(), Rig()
        panel = [('switch', 'on', ()), ('press', 'key_display_mode', 0.1), ('press', 'key_down', 3.1), ('press', 'key_down', 0.1),
                 ('press', 'key_display_mode', 0.1), ('press', 'key_up', 1.7), ('press', 'key_stop', 2.0), ('press', 'key_constant', 0.1),
                 ('press', 'key_run_pause', 0.1), ('step', 300.0, None), ('press', 'key_run_pause', 0.1), ('press', 'key_display_mode', 0.1),
                 ('press', 'key_up', 0.1), ('press', 'key_run_pause', 0.1), ('step', 120.0, None), ('press', 'key_stop', 0.1), ('switch', 'off', ())]
        keys = {'key_display_mode': 'display_mode', 'key_up': 'up', 'key_down': 'down', 'key_stop': 'stop', 'key_run_pause': 'run_pause',
                'key_constant': 'constant'}
        for kind, x, y in panel:
            if kind == 'switch':
                self.assertTrue(a.switch(x, y))
                b.call(b.A, 'power.set', enabled=x == 'on')
            elif kind == 'step':
                a.step(x)
                b.step(x)
            else:
                self.assertTrue(a.press(x, hold=y))
                cap = 'psu.hold_key' if y >= SPEC['inputs'][x].get('long_press_s', SPEC['timing']['long_press_s']) else 'psu.press_key'
                b.call(b.A, cap, key=keys[x], **({'duration_s': y} if cap == 'psu.hold_key' else {}))
            self.assertEqual(runtime_state(a.h), runtime_state(b.h), (kind, x, y))
        self.assertEqual(a.h.records['EP0001']['settings']['power_failure_detection'], True)
        self.assertEqual(a.h.records['EP0001']['settings']['constant'], 'current')


# ---------------------------------------------------------------- refusals, locks, time-outs
class Rules(unittest.TestCase):
    def test_power_off_gate_and_mains(self):
        g = Rig()
        before = runtime_state(g.h)
        for k in SPEC['inputs']:
            if SPEC['inputs'][k]['kind'] != 'switch':
                self.assertFalse(g.press(k))
        self.assertFalse(g.switch('off'))                                               # already off
        self.assertEqual(runtime_state(g.h), before)
        g.call(g.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0))  # mains out of range: switching on is refused
        self.assertFalse(g.switch('on'))
        self.assertEqual((g.screen(), g.frame()['ui']['last_gesture']['rejection']), ('off', 'MAINS_OUT_OF_RANGE'))
        g.call(g.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        self.assertTrue(g.switch('on'))
        self.assertFalse(g.switch('on'))                                                # already on

    def test_untimed_run_time_and_chords(self):
        g = Rig(power=True)
        self.assertTrue(g.press('key_run_pause'))
        g.to_mode('min')
        before = runtime_state(g.h)
        self.assertFalse(g.press('key_up'))                                             # untimed run: stop and re-program (p.11-12)
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'TIMED_MODE_CHANGE_NEEDS_STOP')
        self.assertFalse(g.press('key_up', held=['key_down']))                          # no documented key combinations while running
        self.assertEqual(runtime_state(g.h), before)
        self.assertFalse(g.press('key_stop', hold=2.0) and g.state('psu.power_failure_detection'))   # held during a run: stop
        self.assertEqual(g.state('psu.state'), 'stopped')

    def test_foreign_run_and_session_rules(self):
        g = Rig(power=True)
        g.h.register_principal('other', 'agent')
        o = g.h.session('other')
        self.assertTrue(g.press('key_run_pause'))
        before = runtime_state(g.h)
        self.assertFalse(g.call(o, 'panel.press', input_id='key_stop')['accepted'])      # another agent's run is not stopped from the panel
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'ACCESS_DENIED')
        self.assertEqual(g.lcd(), 'LOC')                                                # the refusal shows on the display (2.5 s)
        self.assertEqual(runtime_state(g.h), before)
        g.step(2.5)
        self.assertNotEqual(g.lcd(), 'LOC')
        before = runtime_state(g.h)
        self.assertFalse(g.call(o, 'panel.press', input_id='key_run_pause')['accepted'])  # pause: refused
        self.assertEqual(runtime_state(g.h), before)
        self.assertTrue(g.call(o, 'panel.press', input_id='key_display_mode')['accepted'])  # the display mode key only changes the view
        r = g.call(o, 'psu.stop', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.state('psu.state'), 'running')
        g.call(o, 'leads.disconnect')                                                   # physical (unplugging): not refused; no load -> E1
        g.step(1.0)
        self.assertNotEqual(g.state('psu.state'), 'running')
        g.call(g.A, 'leads.connect', polarity='normal')
        self.assertTrue(g.press('key_run_pause'))                                       # A starts again ...
        self.assertTrue(g.call(o, 'panel.switch', input_id='switch_power', position='off')['accepted'])   # ... the rocker switch cuts the mains
        self.assertFalse(g.state('power.enabled'))                                      # physical: not refused; the run ends as at a power failure
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='key_bogus')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        caps = g.A.describe(instance_id=INST)['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.press', 'panel.switch', 'panel.capture'})
        self.assertEqual(g.A.describe(instance_id=INST)['asset_version'], rt.ASSET_VERSION)

    def test_time_outs(self):
        g = Rig(power=True)
        g.to_mode('mA')
        g.press('key_stop', hold=2.0)
        g.step(1.9)
        self.assertEqual(g.lcd(), 'dE9')
        g.step(0.1)
        self.assertEqual(g.lcd(), '400')
        g.step(3600.0)                                                                  # no menu time-out: the display mode stays
        self.assertEqual((g.screen(), g.state('display.mode')), ('home', 'mA'))


# ---------------------------------------------------------------- rendering, capture, contract
def composite(h):
    """The panel as one picture: the 3-digit display with the LEDs of Fig. 3 beside the keys (for the evidence PNGs only)."""
    from PIL import Image
    canvas = Image.new('RGB', (340, 150), (206, 208, 202))
    canvas.paste(h.panel.render_image('display'), (64, 8))
    for name, xy in dict(led_volts=(28, 14), led_milliamps=(28, 44), led_time=(28, 74), led_run=(120, 118), led_pause=(150, 118), led_constant_v=(260, 118),
                         led_constant_a=(290, 118)).items():
        canvas.paste(h.panel.render_image(name), xy)
    return canvas


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig(power=True)
            g.press('key_run_pause')
            g.step(301.0)
            return g
        a, b = scenario(), scenario()
        for d in SPEC['displays']:
            ia, ib = a.h.panel.render(d), b.h.panel.render(d)
            self.assertEqual((ia.dtype.name, ia.shape[2]), ('uint8', 3))
            self.assertTrue((ia == ib).all())
            self.assertTrue((ia == a.h.panel.render(d)).all())
        self.assertEqual(a.h.panel.png_bytes('display'), b.h.panel.png_bytes('display'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {}

        def save(name):
            img = composite(a.h)
            buf = io.BytesIO()
            img.save(buf, format='PNG', optimize=False)
            (OUT / f'{name}.png').write_bytes(buf.getvalue())
            shots[name] = dict(path=str((OUT / f'{name}.png').relative_to(ROOT)), width=img.size[0], height=img.size[1],
                               sha256=hashlib.sha256(buf.getvalue()).hexdigest(), screen=a.screen(), digits=a.lcd(), leds=sorted(a.leds()))
        self.assertIn('led_run', a.leds())
        save('home')
        a.press('key_stop')
        a.to_mode('min')
        a.set_value(45)
        a.press('key_stop', hold=2.0)
        self.assertEqual(a.lcd(), 'Pfd')
        save('menu')
        a.step(2.0)
        a.call(a.A, 'leads.disconnect')
        a.press('key_run_pause')
        self.assertEqual(a.lcd(), 'E1')
        save('error')
        shots['display'] = a.h.panel.save_png('display', OUT / 'display_error.png')
        shots['display']['path'] = str(Path(shots['display']['path']).relative_to(ROOT))
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((ROOT / s['path']).read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True) + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        r = g.call(g.A, 'panel.capture', display_id='display')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        self.assertEqual((r['image']['width'], r['image']['height']), tuple(DISPLAY['size']))
        g.switch('on')
        r2 = g.call(g.A, 'panel.capture', display_id='display')
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        self.assertEqual(g.call(g.A, 'panel.capture', display_id='display'), r2)       # same screen, same file
        c = copy.deepcopy(g.h)                                                          # the panel state is copied with its host
        self.assertIs(c.panel.host, c)

    def test_spec_and_contract(self):
        meta = rt.export_biorad_miniprotean_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.switch', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k, v in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'state_effects', 'errors'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        for k, v in list(SPEC['inputs'].items()) + list(SPEC['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'parameter_cycle', pu.SPEC_VERSION))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests'):                              # shared files: registered unpinned
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('miniprotean_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
