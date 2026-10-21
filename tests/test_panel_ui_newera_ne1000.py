"""front-panel tests for CHEM-184 newera_ne1000 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on the runtime's keypad firmware):
python3 -m pytest tests/test_panel_ui_newera_ne1000.py -q

Every gesture goes through the session API (panel.press with hold times and held keys, panel.switch with keys held at power-up); the host steps
the clock between gestures.  Each check looks at the runtime state and at the screen twice: the structured frame (panel.frame) and the rendered
pixels, read back with a seven-segment reader that knows only the LCD geometry of the spec.  Themes: the eight key procedures of the menu survey;
the Setup sequence (TTL settings, keypad lockout with the Lockout Disable Key, network address, baud rate, communications mode); Program Entry
Mode (phase number, functions and parameters, Insert / Delete, PS tenths, trigger override, sub-programs); power-up functions (version, reset,
default program, Program Entry lockout); sticky direction; volume units; decimal points (two keys, held left arrow); refusals (----, oor, LoC-);
the 2 s stores and time-outs; panel = direct capability; render determinism, evidence PNGs and contract entries."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import newera_ne1000 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'newera_ne1000_01'
OUT = ROOT / 'outputs/panel_ui/newera_ne1000'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['Set syringe diameter', 'Set pumping rate and units', 'Set volume to dispense (or continuous)', 'Change direction', 'Purge / prime',
                'Start / stop pumping', 'Clear volume dispensed', 'Reset the pump']
DONE_TASKS = set()
SEEN_KEYPAD = set()
LCD = rt.PANEL_SPEC['displays']['lcd']
FIELD = LCD['fields']['value']
MH = 1e-6 / 3600.0
ML = 1e-6
_DIGITS = {v: k for k, v in pu.SEG7.items() if k in '0123456789'}
_LETTERS = {v: k for k, v in pu.SEG7.items() if k not in '0123456789' and v}


def read_lcd(img):
    """Seven-segment reader: sample the middle of each segment of each cell, the decimal points and the colon (geometry from the spec only)."""
    f = FIELD
    n, cw, ch, gap, cgap, t = f['cells'], f['cell_w'], f['cell_h'], f['gap'], f['colon_gap'], f['thickness']
    dark = lambda x, y: int(img[int(round(y)), int(round(x))].sum()) < 200     # noqa: E731
    out = ''
    for i in range(n):
        x = f['x'] + i * (cw + gap) + cgap * sum(1 for b in f['colons'] if b <= i)
        if i in f['colons']:
            cx = x - (gap + cgap) / 2.0
            out += ':' if dark(cx, f['y'] + ch * 0.3) and dark(cx, f['y'] + ch * 0.7) else ''
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2),
                   g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if dark(px, py))
        out += _DIGITS.get(segs, _LETTERS.get(segs, ' ' if not segs else '?'))
        r = max(1.5, t * 0.6)
        if dark(x + cw + gap / 2.0, f['y'] + ch - r):
            out += '.'
    return out


def norm(text):
    """What the pixels can tell apart: the segments of each cell (S and 5 look the same), points and colons; blanks dropped at the ends."""
    out = [c if c in '.:' else pu.SEG7.get(c, pu.SEG7.get(c.upper(), pu.SEG7.get(c.lower(), ''))) or '_' for c in text]
    while out and out[0] == '_':
        out.pop(0)
    while out and out[-1] == '_':
        out.pop()
    return tuple(out)


def cells_dp(text):
    """Four digit cells (blank = 0) and the decimal point position of a number display."""
    cells, dp = [], None
    for c in text:
        if c == '.':
            dp = len(cells) - 1
        else:
            cells.append(c)
    pad = 4 - len(cells)
    return [int(c) if c.isdigit() else 0 for c in [' '] * pad + cells], (dp + pad if dp is not None else None)


class Rig:
    def __init__(self, seed=1):
        self.h = rt.NeweraNe1000Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            assert cap == 'sim.step' or self.h.t == t0, cap                              # gestures and commands take no simulated time
            if cap in rt.KEYPAD_CAPS:
                SEEN_KEYPAD.add(cap)
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    # gestures (agent session)
    def press(self, key, hold=0.1, held=()):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold, **({'held': list(held)} if held else {}))['accepted']

    def hold(self, key, s=1.5):
        return self.press(key, s)

    def chord(self, a, b):
        return self.press(b, held=[a])

    def switch(self, pos, held=()):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos, **({'held': list(held)} if held else {}))['accepted']

    def on(self, syringe='bd_60ml', fill=50 * ML):
        assert self.switch('on')
        if syringe:
            self.call(self.A, 'syringe.load', syringe=syringe, liquid_id='water', fill_volume_m3=fill)
            self.call(self.A, 'outlet.connect', tubing='ptfe_0p8mm', tubing_length_m=0.5, reservoir_liquid_m3=100 * ML)

    # screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lcd(self, shown=False):
        """The LCD text from the frame (all characters, or what is lit now when shown) and the pixels (must agree with what is lit now)."""
        f = self.frame()['displays']['lcd']['fields']['value']
        assert norm(read_lcd(self.h.panel.render('lcd'))) == norm(f['shown']), (read_lcd(self.h.panel.render('lcd')), f)
        return (f['shown'] if shown else f['text']).strip()

    def leds(self, blink=False):
        ds = self.frame()['displays']
        return {k[4:]: (d['blink'] if blink else d['on']) for k, d in ds.items() if k.startswith('led_')}

    def screen(self):
        return self.frame()['screen']

    def type_value(self, target):
        """Arrows only: the decimal point with two adjacent arrows, then each digit up to the target (manual 6.4)."""
        tc, tdp = cells_dp(target)
        cc, cdp = cells_dp('0.000' if self.lcd() == 'oFF' else self.lcd())     # 'oFF' is a volume of 0.000 (manual 6.7)
        if tdp != cdp:
            pair = tdp if tdp is not None else cdp
            assert self.chord(rt.ARROWS[pair], rt.ARROWS[pair + 1])
        cc, _ = cells_dp(self.lcd())
        for i in range(4):
            for _ in range((tc[i] - cc[i]) % 10):
                assert self.press(rt.ARROWS[i])
        assert cells_dp(self.lcd()) == (tc, tdp), (self.lcd(), target)


class SurveyProcedures(unittest.TestCase):
    """The eight key procedures of the menu survey, done only with panel gestures (plus the host's syringe and clock)."""

    def test_set_syringe_diameter(self):
        g = Rig()
        g.on()
        self.assertTrue(g.press('diameter'))                                   # 1 Diameter: mm LED lit
        self.assertEqual((g.lcd(), g.leds()['mm']), ('0.000', True))
        g.type_value('26.59')                                                   # 2 arrows under the digits, point with two adjacent keys
        self.assertTrue(g.leds(blink=True)['mm'])                               # mm blinks while the value changes
        self.assertIsNone(g.val('syringe.diameter_m') or None)
        g.step(2.0)                                                             # 3 stored 2 s after the last arrow; the display blinks
        self.assertAlmostEqual(g.val('syringe.diameter_m'), 0.02659)
        self.assertTrue(g.frame()['displays']['lcd']['fields']['value']['blink'])
        self.assertEqual(g.lcd(), '26.59')
        self.assertFalse(g.leds(blink=True)['mm'])
        DONE_TASKS.add('Set syringe diameter')

    def test_set_pumping_rate_and_units(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.press('diameter')
        self.assertTrue(g.press('rate'))                                        # 1 Rate: mL/hr
        self.assertEqual({k for k, v in g.leds().items() if v}, {'ml', 'hr'})
        self.assertTrue(g.press('rate'))                                        # 2 Rate again: UntS, units LEDs blink
        self.assertEqual(g.lcd(), 'UntS')
        self.assertTrue(g.leds(blink=True)['ml'])
        g.press('up1')
        g.press('up1')                                                          # mL/hr -> uL/min -> mL/min
        self.assertEqual({k for k, v in g.leds().items() if v}, {'ml', 'min'})
        g.step(2.0)                                                             # wait 2 s: stored
        self.assertEqual(g.val('program.state')['phases'][0]['rate_units'], 'MM')
        g.type_value('5.000')                                                   # 3 the rate with the arrows
        self.assertTrue(g.press('volume'))                                      # a non-arrow key stores at once
        self.assertAlmostEqual(g.val('program.state')['phases'][0]['rate_m3_s'], 5.0 * ML / 60.0)
        DONE_TASKS.add('Set pumping rate and units')

    def test_set_volume_to_dispense_or_continuous(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        self.assertTrue(g.press('volume'))                                      # 1 Volume: oFF (continuous)
        self.assertEqual((g.lcd(), g.leds()['ml'], g.leds()['dispensed']), ('oFF', True, False))
        g.type_value('10.00')                                                   # 2 set 10 mL
        g.press('diameter')                                                     # 3 non-arrow key stores
        self.assertAlmostEqual(g.val('program.state')['phases'][0]['volume_m3'], 10 * ML)
        g.press('volume')
        self.assertEqual(g.lcd(), '10.00')
        g.type_value('0.000')                                                   # back to 0: continuous
        g.step(2.0)
        self.assertEqual((g.val('program.state')['phases'][0]['volume_m3'], g.lcd()), (0.0, 'oFF'))
        DONE_TASKS.add('Set volume to dispense (or continuous)')

    def test_change_direction(self):
        g = Rig()
        g.on()
        self.assertFalse(g.leds()['withdraw'])
        self.assertTrue(g.press('direction'))                                   # 1 Withdraw LED lit = withdraw
        self.assertEqual((g.leds()['withdraw'], g.val('pump.direction')), (True, 'WDR'))
        g.press('direction')
        self.assertEqual((g.leds()['withdraw'], g.val('pump.direction')), (False, 'INF'))
        DONE_TASKS.add('Change direction')

    def test_purge_prime(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', rate_m3_s=10 * MH, units='MH')
        x0 = g.val('pusher.position_m')
        self.assertTrue(g.hold('start', 3.0))                                   # 1 press and hold Start/Stop (released after 3 s)
        g.step(1.2)                                                             # 2 after 1 s: purge at top speed (PurG)
        self.assertEqual((g.val('run.state'), g.lcd()), ('purging', 'PurG'))
        self.assertGreater(g.val('pump.rate_m3_s'), 1000 * MH)
        g.step(2.0)                                                             # released: the pump stops
        self.assertEqual((g.val('run.state'), g.val('pump.rate_m3_s')), ('stopped', 0.0))
        self.assertAlmostEqual(g.val('pusher.position_m') - x0, rt.V_MAX * 2.0, delta=rt.V_MAX * 0.11)   # about 2 s at top speed
        DONE_TASKS.add('Purge / prime')

    def test_start_stop_pumping(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        self.assertTrue(g.press('start'))                                       # 1 Pumping LED lit
        self.assertEqual((g.val('run.state'), g.leds()['pumping'], g.leds(blink=True)['pumping']), ('running', True, False))
        g.step(5.0)
        self.assertTrue(g.press('start'))                                       # 2 paused: Pumping blinks
        self.assertEqual((g.val('run.state'), g.leds(blink=True)['pumping']), ('paused', True))
        self.assertTrue(g.press('start'))                                       # Start resumes
        self.assertEqual(g.val('run.state'), 'running')
        g.press('start')
        self.assertTrue(g.press('rate'))                                        # any other key resets to phase 1
        self.assertEqual((g.val('run.state'), g.leds()['pumping']), ('stopped', False))
        DONE_TASKS.add('Start / stop pumping')

    def test_clear_volume_dispensed(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
        g.press('start')
        g.step(30.0)
        g.press('start')
        g.press('diameter')                                                     # stopped (a key other than Start resets the pause)
        g.press('volume')                                                       # 1 Volume until the Dispensed LED is lit
        g.press('volume')
        self.assertTrue(g.leds()['dispensed'])
        self.assertEqual(g.lcd(), f'{600 / 3600 * 30:.3f}'[:5])
        self.assertTrue(g.hold('up2', 1.2))                                     # 2 hold an arrow 1 s
        self.assertEqual(g.val('volume.dispensed')['infused_m3'], 0.0)
        self.assertEqual(g.lcd(), '0.000')
        DONE_TASKS.add('Clear volume dispensed')

    def test_reset_the_pump(self):
        g = Rig()
        g.on()
        g.call(g.A, 'phase.select', phase=2)
        g.call(g.A, 'phase.set_function', function='STP')
        g.call(g.A, 'setup.configure', keypad_lockout=True)
        g.call(g.A, 'volume.set_units', units='UL')
        g.switch('off')
        self.assertTrue(g.switch('on', held=['up4']))                           # 1 hold the right-most arrow while turning on
        self.assertEqual((g.lcd(), g.screen()), ('rESt', 'special'))
        self.assertIsNone(g.val('program.state')['phases'][1])
        self.assertFalse(g.val('setup.state')['keypad_lockout'])
        self.assertIsNone(g.val('firmware.state')['volume_units_override'])
        self.assertTrue(g.press('volume'))                                      # 2 any key clears the message
        self.assertEqual((g.lcd(), g.screen()), ('0.000', 'run'))
        DONE_TASKS.add('Reset the pump')


class SyncAuditFixes(unittest.TestCase):
    """1.2.0 (sync audit 2026-10-19): Low Noise (manual 8.2), the computer's baud rate (10.1), the RS-232 Safe protocol with its r232 time-out and
    auto-alarm (10.2.3-10.3, 6.14) and the power-up memory errors Err.n (6.14)."""
    @staticmethod
    def pumping(low_noise):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'setup.configure', low_noise=low_noise)
        g.call(g.A, 'rate.set', rate_m3_s=0.5 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(60.0)
        moves, out = [], []
        for _ in range(100):
            x0, d0 = g.val('pusher.position_m'), g.h.led['delivered_m3']
            g.step(0.1)
            moves.append(g.val('pusher.position_m') - x0)
            out.append(g.h.led['delivered_m3'] - d0)
        return g, moves, out

    def test_low_noise_moves_in_half_steps_and_pulsates(self):
        smooth, m0, q0 = self.pumping(False)
        coarse, m1, q1 = self.pumping(True)
        self.assertEqual(len(set(round(x, 15) for x in m0)), 1)                  # fine micro-stepping: smooth travel and flow
        steps = [x for x in m1 if x > 1e-15]
        self.assertTrue(2 <= len(steps) <= 4, steps)                             # 0.5 mL/hr with a 26.59 mm syringe: a half step every 3.4 s
        for x in steps:
            self.assertAlmostEqual(x, rt.LN_STEP_M, delta=1e-12)
        self.assertGreater(max(q1), 5 * max(q0))                                 # the delivered flow comes in pulses
        self.assertEqual(coarse.val('volume.dispensed'), smooth.val('volume.dispensed'))   # the pump's own count is the commanded volume
        self.assertTrue(coarse.val('setup.state')['low_noise'])
        _ = coarse.call(coarse.A, 'serial.send', command='')                    # acknowledge the power-up reset
        self.assertEqual(coarse.call(coarse.A, 'serial.send', command='LN')['response'], '00I1')

    def test_baud_rate_of_the_computer(self):
        g = Rig()
        g.on(syringe=None)
        self.assertEqual(g.call(g.A, 'serial.send', command='')['response'], '00A?R')
        self.assertEqual(g.call(g.A, 'serial.send', command='*ADR0B9600')['response'], '')   # the answer comes at 9600 baud: not received
        self.assertEqual(g.val('firmware.state')['network']['baud_rate'], 9600)        # but the command was carried out
        self.assertEqual(g.call(g.A, 'serial.send', command='DIA')['response'], '')      # computer at 19200: nothing arrives, nothing done
        g.call(g.A, 'serial.configure_port', baud_rate=9600)
        self.assertEqual(g.call(g.A, 'serial.send', command='DIA')['response'], '00S0.000')
        self.assertEqual(g.call(g.A, 'serial.configure_port', expect='rejected', baud_rate=4800)['error']['code'], 'OUT_OF_RANGE')

    def test_safe_mode_packets_time_out_and_auto_alarm(self):
        self.assertEqual(rt._safe_pack('SAF0'), '\x02\x08SAF0\x55\x43\x03')       # the manual's example packet (10.2.2)
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.call(g.A, 'serial.send', command='')                                    # power-up reset acknowledged
        r = g.call(g.A, 'serial.safe_mode', command='SAF5')                         # Safe packet accepted in Basic mode, answered in the Safe protocol
        self.assertEqual((r['data'], r['crc_ok'], r['response'][0], r['response'][-1]), ('00S', True, '\x02', '\x03'))
        self.assertEqual(g.val('firmware.state')['safe_mode_timeout_s'], 5)
        self.assertEqual(g.call(g.A, 'serial.send', command='DIA')['response'], '')    # no STX in Safe mode: not a packet
        bad = rt._safe_pack('DIA')[:-3] + '\x00\x00\x03'
        self.assertEqual(rt._safe_unpack(g.call(g.A, 'serial.send', command=bad)['response']), '00S?COM')   # corrupted packet
        self.assertEqual(g.call(g.A, 'serial.safe_mode', command='DIA')['data'], '00S26.59')
        g.call(g.A, 'serial.safe_mode', command='RUN')
        g.step(4.0)
        self.assertEqual((g.val('run.state'), g.val('alarm.state')), ('running', None))
        g.call(g.A, 'serial.safe_mode', command='DIS')                              # a valid packet resets the time-out
        g.step(4.0)
        self.assertEqual(g.val('run.state'), 'running')
        g.step(1.5)                                                                 # 5 s without a valid packet
        self.assertEqual((g.val('alarm.state'), g.val('run.state'), g.val('pump.rate_m3_s'), g.lcd()), ('comm_timeout', 'stopped', 0.0, 'r232'))
        packets = g.call(g.A, 'serial.receive')['packets']
        self.assertEqual([rt._safe_unpack(x) for x in packets], ['00A?T'])         # auto-alarm, sent by the pump on its own
        self.assertEqual(g.call(g.A, 'serial.receive')['packets'], [])
        self.assertEqual(g.val('alarm.state'), 'comm_timeout')                       # the automatic message does not clear the alarm
        self.assertEqual(g.call(g.A, 'serial.safe_mode', command='DIA')['data'], '00A?T')   # the status in a reply acknowledges it
        self.assertIsNone(g.val('alarm.state'))
        g.switch('off')
        g.switch('on')                                                               # Safe mode is kept (non-volatile); reset alarm sent at once
        self.assertEqual([rt._safe_unpack(x) for x in g.call(g.A, 'serial.receive')['packets']], ['00A?R'])
        g.step(10.0)
        self.assertIsNone(g.val('alarm.state'))                                       # the time-out starts with the first valid packet
        self.assertEqual(g.call(g.A, 'serial.safe_mode', command='')['data'], '00A?R')   # the first reply after power-up: the reset alarm
        r = g.call(g.A, 'serial.safe_mode', command='SAF0')                          # back to Basic: answered in the Basic protocol
        self.assertEqual((r['response'], r['crc_ok']), ('00S', None))
        self.assertEqual(g.call(g.A, 'serial.send', command='SAF')['response'], '00S0')
        g.step(10.0)
        self.assertIsNone(g.val('alarm.state'))

    def test_another_agent_cannot_stop_or_change_the_program(self):
        """Lead rule 10-19: a Pumping Program started through a session belongs to that caller; another agent can neither stop, power off nor change it,
        on the keypad, over RS-232 or directly (state unchanged); the caller, the evaluator and TTL inputs still can (no operation ids: poll/cancel n/a)."""
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.call(g.A, 'serial.send', command='')
        self.assertTrue(g.press('start'))                                         # agent 1 starts on the keypad
        self.assertEqual((g.val('run.state'), g.h.run_owner), ('running', 'agent'))
        g.step(1.0)
        g.h.register_principal('agent2', 'agent')
        b = g.h.session('agent2')
        before = g.h.read('evaluator')
        r = b.invoke(instance_id=INST, capability_id='panel.press', arguments=dict(input_id='start', duration_s=0.1), request_id='b-start')
        self.assertFalse(r['result']['accepted'])
        self.assertEqual((g.lcd(), g.frame()['ui']['message']['text']), ('dEnY', 'dEnY'))   # sweep 10-19: the refusal is visible for 2 s
        for cap, args in (('pump.stop', {}), ('rate.set', dict(rate_m3_s=50 * MH)),
                          ('direction.set', dict(direction='REV')), ('keypad.press', dict(key='start'))):
            r = b.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        r = b.invoke(instance_id=INST, capability_id='serial.send', arguments=dict(command='STP'), request_id='b-stp')
        self.assertEqual(r['result']['response'], '00I?NA')                      # RS-232: not applicable, nothing changes
        after = g.h.read('evaluator')
        for k in ('run.state', 'run.phase', 'pump.rate_m3_s', 'pump.direction', 'power.on', 'program.state'):
            self.assertEqual(before[k], after[k], k)
        shown = g.lcd()
        g.step(2.5)
        self.assertNotEqual(g.lcd(), 'dEnY')                                     # after 2 s the pump display again
        self.assertEqual(shown, 'dEnY')
        self.assertTrue(g.press('start'))                                         # the caller pauses its own program
        self.assertEqual(g.val('run.state'), 'paused')
        r = b.invoke(instance_id=INST, capability_id='pump.run', arguments={}, request_id='b-run')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')                     # nor resume it
        g.call(g.E, 'pump.stop')                                                  # the evaluator may (paused -> reset)
        self.assertEqual(g.val('run.state'), 'stopped')
        self.assertEqual(b.invoke(instance_id=INST, capability_id='pump.run', arguments={}, request_id='b-run2')['status'], 'succeeded')
        self.assertEqual(g.h.run_owner, 'agent2')                                 # a new program belongs to whoever starts it
        r = g.call(g.A, 'panel.switch', input_id='power_switch', position='off')  # 1.2.1: the rear power switch is physical: A may switch B's run off
        self.assertTrue(r['accepted'])
        self.assertEqual((g.val('power.on'), g.val('run.state')), (False, 'stopped'))

    def test_power_up_memory_errors(self):
        g = Rig()
        g.on(syringe=None)
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'setup.configure', alarm=False)
        g.call(g.E, 'fault.inject', fault='nv_memory_invalid', active=True)
        self.assertEqual(g.val('syringe.diameter_m'), 0.02659)                      # nothing until the next power-up
        g.switch('off')
        g.switch('on')
        self.assertEqual(g.lcd(), 'Err.1')                                          # values invalid: reset to the factory settings
        self.assertEqual((g.val('syringe.diameter_m'), g.val('setup.state')['alarm']), (0.0, True))
        self.assertTrue(g.press('rate'))                                            # any key clears the display
        self.assertNotEqual(g.lcd(), 'Err.1')
        g.switch('off')
        g.switch('on')
        self.assertNotEqual(g.lcd(), 'Err.1')                                       # one-off
        g.call(g.E, 'fault.inject', fault='nv_memory_failed', active=True)
        for _ in range(2):
            g.switch('off')
            g.switch('on')
            self.assertEqual(g.lcd(), 'Err.2')                                      # at every power-up while the memory is faulty
            g.press('rate')
        g.call(g.E, 'fault.inject', fault='nv_memory_failed', active=False)         # memory replaced
        g.switch('off')
        g.switch('on')
        self.assertNotEqual(g.lcd(), 'Err.2')
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', fault='nv_memory_failed', active=True)['error']['code'], 'ACCESS_DENIED')


class Firmware(unittest.TestCase):
    def test_setup_sequence_ttl_lockout_network(self):
        g = Rig()
        g.on(syringe=None)
        g.call(g.A, 'ttl.set', pin=6, level=0)                                  # Lockout Disable Key inserted: LoC is shown
        g.step(0.2)
        self.assertTrue(g.hold('diameter'))
        self.assertEqual((g.screen(), g.lcd()), ('setup', 'PF: 0'))
        self.assertTrue(g.press('up4'))
        self.assertEqual(g.lcd(), 'PF: 1')
        g.press('start')                                                        # non-arrow key: stored, next parameter
        self.assertTrue(g.val('setup.state')['power_failure'])
        self.assertEqual(g.lcd(), 'Ln: 0')
        g.step(2.0)                                                             # about 2 s each
        self.assertEqual(g.lcd(), 'AL: 1')
        g.step(2.0)
        self.assertEqual(g.lcd(), 'ttL')
        g.press('up2')                                                          # any arrow: the TTL settings
        self.assertEqual(g.lcd(), 'tr:Ft')
        g.press('up3')
        g.press('up3')
        self.assertEqual(g.lcd(), 'tr:F2')
        g.step(2.0)
        self.assertEqual((g.val('setup.state')['trigger'], g.lcd()), ('F2', 'dr:rE'))
        g.press('up4')
        g.press('rate')
        self.assertEqual((g.val('setup.state')['direction_input'], g.lcd()), (1, 'rUn.0'))
        g.press('rate')
        self.assertEqual(g.lcd(), 'LoC.0')
        g.press('up4')
        g.press('rate')
        self.assertTrue(g.val('setup.state')['keypad_lockout'])
        self.assertEqual(g.lcd(), 'bP: 1')
        g.press('rate')
        self.assertEqual(g.lcd(), 'Ad:00')
        g.press('up4')
        g.press('up3')
        g.press('rate')
        self.assertEqual((g.val('firmware.state')['network']['address'], g.lcd()), (11, '1920'))
        g.press('up1')
        g.press('up1')
        g.step(2.0)                                                             # stored, last parameter: back to the diameter
        self.assertEqual((g.val('firmware.state')['network']['baud_rate'], g.screen(), g.lcd()), (1200, 'run', '0.000'))
        self.assertTrue(g.leds()['mm'])
        self.assertEqual(g.call(g.A, 'serial.send', command='11DIA')['response'], '')      # 1.2.0: the computer is still at 19200 baud: nothing arrives
        self.assertEqual(g.call(g.A, 'serial.configure_port', baud_rate=1200)['baud_rate'], 1200)   # the same baud rate as the pump (manual 10.1)
        self.assertEqual(g.call(g.A, 'serial.send', command='DIA')['response'], '')        # no address: for pump 0
        self.assertEqual(g.call(g.A, 'serial.send', command='11')['response'], '11A?R')    # first reply after power-up: reset alarm
        self.assertEqual(g.call(g.A, 'serial.send', command='11DIA')['response'], '11S0.000')
        g.call(g.A, 'ttl.set', pin=6, level=1)                                  # key removed: settings locked
        g.step(0.2)
        self.assertFalse(g.press('up1'))
        self.assertEqual(g.lcd(), 'LoC-')
        g.press('rate')
        self.assertTrue(g.hold('diameter'))                                     # reviewing the settings is allowed
        self.assertEqual(g.lcd(), 'PF: 1')
        self.assertFalse(g.press('up4'))
        self.assertEqual(g.lcd(), 'LoC-')

    def test_communications_mode_menu_and_search_display(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.hold('diameter')
        for _ in range(5):
            g.press('rate')
        self.assertEqual(g.lcd(), 'Ad:00')
        g.press('up1')                                                          # the left-most arrow: communications mode menu
        self.assertEqual(g.lcd(), 'Addr')
        g.press('up2')
        self.assertEqual(g.lcd(), 'rECP')
        g.step(2.0)
        self.assertEqual((g.val('firmware.state')['network']['mode'], g.screen()), ('RECP', 'run'))
        g.press('rate')
        g.press('start')
        self.assertEqual(g.lcd(), 'rECP')                                       # searching for the secondary pump (absent)
        g.step(2.5)
        self.assertEqual((g.lcd(), g.val('run.state')), ('100.0', 'running'))
        self.assertEqual(g.call(g.A, 'serial.send', command='DIA')['response'], '')        # special mode: only '*' commands
        self.assertEqual(g.call(g.A, 'serial.send', command='*')['response'], '00A?R')
        self.assertEqual(g.call(g.A, 'serial.send', command='*ADR0')['response'], '00I')

    def test_program_entry_phases_functions_parameters(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        self.assertTrue(g.hold('rate'))                                         # Program Entry Mode: PH:01
        self.assertEqual((g.screen(), g.lcd()), ('program', 'PH:01'))
        g.press('up4')
        g.step(2.0)                                                             # phase 2 selected 2 s later
        self.assertEqual(g.val('program.state')['selected'], 2)
        g.press('volume')                                                       # the function of phase 2 (empty: rAtE)
        self.assertEqual(g.lcd(), 'rAtE')
        for _ in range(5):
            g.press('up1')
        self.assertEqual(g.lcd(), 'JP:01')
        g.press('rate')                                                         # stored, back to the phase number
        self.assertEqual(g.val('program.state')['phases'][1]['function'], 'JMP')
        g.press('volume')
        g.press('up4')
        g.press('up4')
        self.assertEqual(g.lcd(), 'JP:03')
        g.step(2.0)
        self.assertEqual(g.val('program.state')['phases'][1]['parameter'], 3)
        for _ in range(6):                                                      # JP -> ... -> PS:nn
            g.press('up1')
        self.assertEqual(g.lcd(), 'PS:00')
        g.press('rate')
        g.press('volume')
        self.assertTrue(g.chord('up3', 'up4'))                                  # PS tenths: the point between the digits
        g.press('up4')
        g.press('up4')
        g.press('up4')
        self.assertEqual(g.lcd(), 'PS:0.3')
        g.step(2.0)
        ph = g.val('program.state')['phases'][1]
        self.assertEqual((ph['function'], ph['parameter'], ph['tenths']), ('PAS', 3, True))
        g.press('up4')
        g.step(2.0)
        self.assertEqual(g.lcd(), 'PS:0.4')
        self.assertTrue(g.hold('up4'))                                          # holding the right-most arrow toggles the point
        g.step(2.0)
        self.assertEqual(g.lcd(), 'PS:04')
        self.assertFalse(g.val('program.state')['phases'][1]['tenths'])
        g.hold('rate')                                                          # hold Phase #: phase 1
        self.assertEqual((g.lcd(), g.val('program.state')['selected']), ('PH:01', 1))
        g.press('rate')                                                         # phase 1 is a rate function: back to the rate display
        self.assertEqual((g.screen(), g.lcd()), ('run', '0.000'))

    def test_insert_delete_and_their_time_out(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'phase.set_function', function='RAT', rate_m3_s=50 * MH, rate_units='MH', volume_m3=1 * ML, direction='INF')
        g.call(g.A, 'phase.select', phase=2)
        g.call(g.A, 'phase.set_function', function='JMP', parameter=1)
        g.call(g.A, 'phase.select', phase=3)
        g.call(g.A, 'phase.set_function', function='STP')
        g.call(g.A, 'phase.select', phase=2)
        g.hold('rate')
        self.assertEqual(g.lcd(), 'PH:02')
        g.press('up1')
        self.assertEqual(g.lcd(), 'In:02')
        g.step(2.1)                                                             # not confirmed in 2 s: canceled
        self.assertEqual(g.lcd(), 'PH:02')
        g.press('up1')
        g.press('rate')                                                         # Phase # within 2 s: insert at phase 2
        ph = g.val('program.state')['phases']
        self.assertEqual([p['function'] if p else None for p in ph[:5]], ['RAT', 'STP', 'JMP', 'STP', None])
        g.press('up2')
        g.press('up2')
        self.assertEqual(g.lcd(), 'dE:02')
        g.press('rate')
        ph = g.val('program.state')['phases']
        self.assertEqual([p['function'] if p else None for p in ph[:4]], ['RAT', 'JMP', 'STP', None])
        self.assertEqual(ph[1]['parameter'], 1)

    def test_sub_program_selection(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        prog = [dict(function='PRI'), dict(function='PRL', parameter=1), dict(function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=0.5 * ML),
                dict(function='STP'), dict(function='PRL', parameter=2), dict(function='RAT', rate_m3_s=300 * MH, rate_units='MH', volume_m3=0.2 * ML,
                                                                             direction='WDR'), dict(function='STP')]
        for i, p in enumerate(prog, 1):
            g.call(g.A, 'phase.select', phase=i)
            g.call(g.A, 'phase.set_function', **p)
        g.call(g.A, 'phase.select', phase=1)
        g.press('start')
        self.assertEqual((g.val('run.state'), g.lcd()), ('user_wait', 'Pr:00'))
        self.assertFalse(g.press('rate'))                                       # other keys: not applicable
        g.press('rate')
        g.press('start')                                                        # label 00 does not exist: oor
        self.assertEqual(g.lcd(), 'oor')
        g.press('rate')
        g.press('up4')
        g.press('up4')
        self.assertEqual(g.lcd(), 'Pr:02')
        g.press('start')
        self.assertEqual((g.val('run.state'), g.val('run.phase'), g.val('pump.direction')), ('running', 6, 'WDR'))
        self.assertEqual(g.val('program.state')['phases'][0]['parameter'], 2)    # the default label next time

    def test_trigger_override_and_event_redirect(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        prog = [dict(function='EVN', parameter=4), dict(function='TRG', parameter=14), dict(function='RAT', rate_m3_s=100 * MH, rate_units='MH'),
                dict(function='RAT', rate_m3_s=600 * MH, rate_units='MH', direction='WDR')]
        for i, p in enumerate(prog, 1):
            g.call(g.A, 'phase.select', phase=i)
            g.call(g.A, 'phase.set_function', **p)
        g.call(g.A, 'phase.select', phase=1)
        g.hold('rate')
        g.press('volume')
        self.assertEqual(g.lcd(), 'Et:04')
        g.press('rate')
        g.press('up4')
        g.press('volume')
        self.assertEqual(g.lcd(), 'tr:bt')
        g.press('rate')
        g.press('up4')                                                          # PH:03
        g.press('rate')                                                         # phase 3 is a RATE phase: back to the rate display
        self.assertEqual((g.screen(), g.lcd()), ('run', '100.0'))
        g.call(g.A, 'phase.select', phase=1)
        g.press('start')
        self.assertEqual((g.val('run.state'), g.val('run.phase')), ('running', 3))
        g.press('start')                                                        # bt: the Stop key jumps to the event trap
        self.assertEqual((g.val('run.state'), g.val('run.phase'), g.val('pump.direction')), ('running', 4, 'WDR'))
        g.press('start')                                                        # one time only: now a pause
        self.assertEqual(g.val('run.state'), 'paused')

    def test_power_up_functions_and_entry_lockout(self):
        g = Rig()
        g.on(syringe=None)
        g.switch('off')
        self.assertTrue(g.switch('on', held=['up1']))                           # firmware version
        self.assertEqual(g.lcd(), 'r3.92')
        g.press('rate')
        g.switch('off')
        g.switch('on', held=['volume'])                                         # default program menu
        self.assertEqual(g.lcd(), 'rECP')
        self.assertTrue(g.press('start'))                                       # loads the reciprocating program
        ph = g.val('program.state')['phases']
        self.assertEqual([(p['function'], p['direction']) for p in ph[:3]], [('RAT', 'WDR'), ('FIL', 'INF'), ('JMP', 'INF')])
        self.assertEqual(g.val('firmware.state')['network']['mode'], 'RECP')
        g.switch('off')
        g.switch('on', held=['diameter'])
        self.assertEqual(g.lcd(), 'P-L.0')
        g.press('up4')
        g.step(2.0)                                                             # multi-phase program: refused
        self.assertEqual((g.lcd(), g.val('firmware.state')['program_entry_lockout']), ('oor', False))
        g.switch('off')
        g.switch('on', held=['up4'])                                            # reset first
        g.press('rate')
        g.switch('off')
        g.switch('on', held=['diameter'])
        g.press('up4')
        g.press('rate')
        self.assertTrue(g.val('firmware.state')['program_entry_lockout'])
        self.assertFalse(g.hold('rate'))                                        # Program Entry Mode locked out
        self.assertEqual(g.lcd(), '----')

    def test_power_failure_restart_and_a_held_key(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.call(g.A, 'setup.configure', power_failure=True)
        g.press('start')
        g.switch('off')
        g.switch('on')
        self.assertEqual(g.val('run.state'), 'running')                        # restarted
        g.switch('off')
        g.switch('on', held=['rate'])                                           # any key held at power-up stops the restart
        self.assertEqual(g.val('run.state'), 'stopped')

    def test_sticky_direction(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        self.assertTrue(g.hold('direction'))                                    # Withdraw blinks
        self.assertTrue(g.leds(blink=True)['withdraw'])
        self.assertEqual(g.val('program.state')['phases'][0]['direction'], 'STK')
        self.assertEqual(g.call(g.A, 'serial.send', command='')['response'], '00A?R')
        self.assertEqual(g.call(g.A, 'serial.send', command='DIR')['response'], '00SSTK')
        self.assertEqual(g.val('pump.direction'), 'WDR')                        # first phase: TTL pin 3 high, dr:rE = withdraw
        g.call(g.A, 'ttl.set', pin=3, level=0)
        g.step(0.2)
        self.assertEqual(g.val('pump.direction'), 'INF')
        g.press('direction')                                                    # a press sets a fixed direction again
        self.assertFalse(g.leds(blink=True)['withdraw'])
        self.assertEqual(g.val('program.state')['phases'][0]['direction'], 'WDR')

    def test_volume_units_and_decimal_point_hold(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.press('volume')
        g.press('volume')
        self.assertTrue(g.press('up1'))                                         # Volume Dispensed 0.000: UntS
        self.assertEqual(g.lcd(), 'UntS')
        self.assertTrue(g.leds(blink=True)['ml'])
        g.press('up1')
        self.assertTrue(g.leds()['ul'])
        g.step(2.0)
        self.assertEqual(g.val('firmware.state')['volume_units_override'], 'UL')
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.030)                  # the override stays
        self.assertEqual(g.val('volume.dispensed')['units'], 'UL')
        g.press('diameter')
        self.assertEqual(g.lcd(), '30.00')
        self.assertTrue(g.hold('up1', 2.0))                                     # left-most arrow held: digit to 0, point shifts twice
        self.assertEqual(g.lcd(), '0000')                                       # 30.00 -> 0.000 -> 00.00 -> 0000 (whole number)
        g.press('up2')
        g.press('up2')
        self.assertFalse(g.press('rate'))                                       # '0200' mm: out of range
        self.assertEqual(g.lcd(), 'oor')
        g.press('rate')
        self.assertAlmostEqual(g.val('syringe.diameter_m'), 0.030)

    def test_refusals_and_locks(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.press('start')
        before = g.h.read('evaluator')
        self.assertFalse(g.hold('diameter'))                                    # Setup while operating: ----
        self.assertEqual(g.lcd(), '----')
        self.assertTrue(g.press('rate'))                                        # any key clears the message
        self.assertFalse(g.hold('rate'))                                        # Program Entry Mode while operating: ----
        g.press('rate')
        self.assertFalse(g.chord('up1', 'up3'))                                 # not adjacent
        g.press('rate')
        self.assertFalse(g.press('rate', held=['volume']))                      # undocumented combination: ignored
        after = g.h.read('evaluator')
        self.assertEqual({k: after[k] for k in after if not k.startswith(('display', 'clock', 'truth', 'pusher', 'volume.dispensed'))},
                         {k: before[k] for k in before if not k.startswith(('display', 'clock', 'truth', 'pusher', 'volume.dispensed'))})
        g.switch('off')
        self.assertFalse(g.press('rate'))                                       # off: keys do nothing
        self.assertEqual((g.lcd(), any(g.leds().values())), ('', False))
        self.assertFalse(g.switch('on', held=['up1']) and g.switch('on', held=['up1']))   # power-up keys only when switching on

    def test_two_second_store_and_no_menu_time_out(self):
        g = Rig()
        g.on()
        g.press('diameter')
        g.press('up2')
        g.step(1.9)
        self.assertIsNone(g.val('syringe.diameter_m') or None)                 # not yet
        g.step(0.2)
        self.assertAlmostEqual(g.val('syringe.diameter_m'), 0.0001)
        g.hold('rate')
        g.step(120.0)                                                           # Program Entry Mode has no time-out
        self.assertEqual(g.screen(), 'program')

    def test_panel_and_direct_commands_give_the_same_state(self):
        p, d = Rig(), Rig()
        for g in (p, d):
            g.on()
        p.press('diameter')
        p.type_value('26.59')
        p.press('rate')
        p.type_value('100.0')
        p.press('volume')
        p.type_value('5.000')
        p.press('direction')
        p.hold('direction')
        p.step(2.0)
        d.call(d.A, 'syringe.set_diameter', diameter_m=0.02659)
        d.call(d.A, 'rate.set', rate_m3_s=100 * MH)
        d.call(d.A, 'volume.set', volume_m3=5 * ML)
        d.call(d.A, 'direction.set', direction='REV')
        d.call(d.A, 'direction.sticky')
        d.step(2.0)
        a, b = p.h.read('evaluator'), d.h.read('evaluator')
        skip = ('display.text', 'display.lcd_cells', 'indicator.mm', 'indicator.ml', 'indicator.hr')   # the panel shows another display
        self.assertEqual({k: v for k, v in a.items() if k not in skip}, {k: v for k, v in b.items() if k not in skip})

    def test_only_agent_capabilities_behind_the_panel(self):
        caps = pu.spec_capabilities(rt.PANEL_SPEC)
        self.assertEqual(sorted(caps), ['keypad.chord', 'keypad.hold', 'keypad.press', 'power.on_with_key', 'power.set'])
        self.assertTrue(all(rt.ALL_CAPS[k]['visibility'] == 'agent' for k in caps))
        g = Rig()
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.call(g.A, 'panel.press', expect='rejected', input_id='nope')['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(g.call(g.A, 'panel.press', expect='rejected', input_id='power_switch')['error']['code'], 'INVALID_ARGUMENT')

    def test_direct_firmware_commands(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'keypad.press', key='diameter')
        g.call(g.A, 'keypad.chord', keys=['up2', 'up3'])
        g.call(g.A, 'keypad.hold', key='up1', duration_s=1.0)
        g.call(g.A, 'program.edit', operation='insert')
        g.call(g.A, 'program.load_default', program='RECP')
        g.call(g.A, 'network.address', address=0, baud_rate=9600, mode='ADR')
        g.call(g.A, 'network.address', expect='rejected', baud_rate=4800)
        g.call(g.A, 'program.entry_lockout', expect='rejected', enabled=True)
        g.call(g.A, 'program.clear')
        g.call(g.A, 'program.entry_lockout', enabled=True)
        g.call(g.A, 'program.entry_lockout', enabled=False)
        g.call(g.A, 'volume.set_units', units='ML')
        g.call(g.A, 'direction.sticky')
        g.call(g.A, 'ttl.override_trigger', expect='rejected', trigger='SP')
        g.call(g.A, 'program.subprogram', expect='rejected', label=1)
        g.call(g.A, 'phase.set_function', function='PRI')
        g.call(g.A, 'phase.select', phase=2)
        g.call(g.A, 'phase.set_function', function='PRL', parameter=7)
        g.call(g.A, 'phase.select', phase=3)
        g.call(g.A, 'phase.set_function', function='RAT', rate_m3_s=100 * MH, rate_units='MH')
        g.call(g.A, 'pump.run')
        g.call(g.A, 'program.subprogram', expect='rejected', label=5)
        g.call(g.A, 'program.subprogram', label=7)
        g.call(g.A, 'ttl.override_trigger', trigger='SP')
        g.call(g.A, 'ttl.set', pin=2, level=0)
        g.step(0.2)                                                             # SP: a falling edge stops (pauses) the program
        self.assertEqual(g.val('run.state'), 'paused')
        g.switch('off')
        g.call(g.A, 'power.on_with_key', key='up1')
        g.call(g.A, 'power.on_with_key', expect='rejected', key='up1')
        self.assertEqual(SEEN_KEYPAD | {'keypad.hold', 'keypad.chord'}, set(rt.KEYPAD_CAPS))

    def test_render_determinism_capture_and_frame(self):
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.press('diameter')
        a, b = g.h.panel.render('lcd'), g.h.panel.render('lcd')
        self.assertEqual(a.shape, (132, 330, 3))
        self.assertTrue((a == b).all())
        r1 = g.call(g.A, 'panel.capture', display_id='lcd')
        r2 = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertEqual(r1['image'], r2['image'])
        self.assertEqual(hashlib.sha256(Path(r1['image']['path']).read_bytes()).hexdigest(), r1['image']['sha256'])
        self.assertEqual(json.dumps(g.frame(), sort_keys=True), json.dumps(g.frame(), sort_keys=True))
        c = copy.deepcopy(g.h)
        self.assertTrue((c.panel.render('lcd') == a).all())
        led = g.h.panel.render('led_mm')
        self.assertGreater(int(led[14, 14, 0]), 200)                            # mm LED lit (red)

    def test_evidence_renders(self):
        """home (rate display), menu (Setup PF), program (PH:01), error (StAL and oor), LEDs strip; deterministic, with renders.json."""
        OUT.mkdir(parents=True, exist_ok=True)
        g = Rig()
        g.on()
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.press('start')                                                        # pumping 100.0 mL/hr: Pumping LED lit
        panel = g.h.panel
        shots = dict(home=panel.save_png('lcd', OUT / 'home.png'))
        strip = np.concatenate([panel.render(f'led_{k}') for k, _ in [('mm', 0), ('ml', 0), ('ul', 0), ('dispensed', 0), ('min', 0), ('hr', 0),
                                                                     ('withdraw', 0), ('pumping', 0)]], axis=1)
        from PIL import Image
        Image.fromarray(strip).save(OUT / 'leds.png')
        shots['leds'] = dict(path=str(OUT / 'leds.png'), format='png', width=strip.shape[1], height=strip.shape[0],
                             sha256=hashlib.sha256((OUT / 'leds.png').read_bytes()).hexdigest())
        g.hold('diameter')
        shots['menu'] = panel.save_png('lcd', OUT / 'menu.png')
        g.press('rate')
        for _ in range(9):
            g.press('rate')
        g.hold('rate')
        shots['program'] = panel.save_png('lcd', OUT / 'program.png')
        g.press('rate')
        g.press('diameter')
        g.press('up1')
        g.press('up1')
        g.press('up1')
        g.press('rate')
        shots['error'] = panel.save_png('lcd', OUT / 'error.png')
        g.press('rate')
        g.call(g.A, 'serial.send', command='')
        g.call(g.A, 'serial.safe_mode', command='SAF1')
        g.step(1.2)                                                              # Safe-mode communications time-out
        shots['fault'] = panel.save_png('lcd', OUT / 'fault.png')
        for k, v in shots.items():
            self.assertEqual(v['sha256'], hashlib.sha256(Path(v['path']).read_bytes()).hexdigest())
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()}, indent=1) + '\n')

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class']), ('panel_ui', 'parameter_cycle'))
        for k in list(rt.PANEL_CAPS) + list(rt.KEYPAD_CAPS) + list(rt.SERIAL_CAPS):
            self.assertIn(k, c['capabilities'])
            self.assertEqual(c['capabilities'][k]['availability'], 'implemented')
            for cid in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][cid]['capability_ids'])
        self.assertIn('firmware.state', c['state_slots'])
        self.assertIn('comm_timeout', c['state_slots']['alarm.state']['value_schema']['anyOf'][0]['enum'])
        self.assertEqual(sorted(f['id'] for f in c['extensions']['chemistry_lab.fixes']),
                         ['baud_rate_gates_rs232', 'low_noise_half_steps', 'nv_memory_errors', 'program_owner_rule', 'safe_mode_protocol', 'sweep_refusal_visible',
                          'water_reference_density'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])


if __name__ == '__main__':
    unittest.main()
