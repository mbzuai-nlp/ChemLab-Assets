"""front-panel tests for CHEM-180 alicat_mc (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_automation.py):
python3 -m pytest tests/test_panel_ui_alicat_mc_1slpm_rev6.py -q

Every gesture goes through the session API (panel.press, the runtime's normal invoke path).  Each check looks at the runtime state and at the
screen twice: the structured frame (panel.frame) and the rendered pixels, read back with a 5x7 dot reader that knows only the text grid of the
display (22 x 10 characters) and the large-digit area.  Themes: the eight key procedures of the menu survey plus every other operator function of
the manual's menus (setpoint setup, control loop, ramping, units, reference conditions, averaging, zero band, serial and display settings,
COMPOSER mixes, Recent gases, totalizer and batch, actions menu, backlight, contrast, rotation, Factory Restore, ABOUT); panel = direct
capability (same runtime state); the device functions behind the panel at the runtime level (autotare, analog setpoint, power-up setpoint, ramp,
batch valve closing, volumetric control, averaging, zero band, COMPOSER mixture properties); refusals and locks (no supply, LCK, setpoint limits,
analog source, gas change while flowing, unsupported control variables, service menus); no menu time-out; render determinism and the PNGs of
outputs/panel_ui/alicat_mc_1slpm_rev6/; contract entries of the panel capabilities."""
import hashlib
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_automation as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'alicat_mc_01'
FS = rt.ALICAT_MC_FS_M3_S
OUT = ROOT / 'outputs/panel_ui/alicat_mc_1slpm_rev6'
ASSET = rt.ALICAT_MC_ASSET_DIR
SPEC = rt._amc_panel()['spec']
LCD = SPEC['displays']['lcd']
TL, TC, TR, BL, BC, BR = 'sk_tl', 'sk_tc', 'sk_tr', 'sk_bl', 'sk_bc', 'sk_br'
SURVEY_TASKS = ['Tare the flow reading', 'Change the flow setpoint', 'Select a different gas', 'Change engineering units', 'Dispense a batch',
                'Reset the totalizer', 'Set standard reference conditions', 'Unlock a locked front panel (LCK)']
DONE_TASKS = set()
_GLYPHS = {tuple(b & 0x7F for b in v): k for k, v in pu.FONT5X7.items() if k != ' '}


# ---------------------------------------------------------------- reading the rendered display
def _ink(img):
    """Dot-on mask: darker than half way between the background (the most common colour) and the darkest pixel."""
    flat = img.reshape(-1, 3).astype(int).sum(axis=1)
    values, counts = __import__('numpy').unique(flat, return_counts=True)
    bg, dark = values[counts.argmax()], flat.min()
    return (img.astype(int).sum(axis=2) < (bg + dark) / 2.0) if bg - dark > 60 else (img[..., 0] < 0)


def _cell(ink, x0, y0, scale=1):
    cols = []
    for cx in range(5):
        bits = 0
        for cy in range(7):
            if ink[y0 + cy * scale + scale // 2, x0 + cx * scale + scale // 2]:
                bits |= 1 << cy
        cols.append(bits)
    return tuple(cols)


def read_text(img):
    """The 10 lines of 22 characters of the text grid (x = 2 + 6 c, y = 2 + 9 r), as a camera would read them."""
    ink = _ink(img)
    out = []
    for r in range(10):
        band = ink[1 + 9 * r:10 + 9 * r]
        row = ~band if band.mean() > 0.5 else band                                     # an inverse (message) line: light dots on a dark bar
        rink = ink.copy()
        rink[1 + 9 * r:10 + 9 * r] = row
        line = ''
        for c in range(22):
            g = _cell(rink, 2 + 6 * c, 2 + 9 * r)
            line += ' ' if not any(g) else _GLYPHS.get(g, '?')
        out.append(line.rstrip())
    return out


def read_big(img, rect, align):
    """Large digits (scale 2) in a widget rect, laid out as the engine's text box (4 px padding, vertically centred)."""
    ink = _ink(img)
    x0, y0, w, h = rect
    y = y0 + (h - 18 + 4) // 2
    chars = []
    if align == 'left':
        x = x0 + 4
        while x + 10 <= x0 + w:
            g = _cell(ink, x, y, 2)
            if not any(g):
                break
            chars.append(_GLYPHS.get(g, '?'))
            x += 12
        return ''.join(chars)
    right = x0 + w - 4
    for j in range(12):
        x = right - 12 * (j + 1) + 2
        if x < x0:
            break
        g = _cell(ink, x, y, 2)
        if not any(g):
            break
        chars.append(_GLYPHS.get(g, '?'))
    return ''.join(reversed(chars))


# ---------------------------------------------------------------- rig
class Rig:
    def __init__(self, ready=True, gas='mix_air', tau=0.2):
        self.h = rt.AlicatMCRuntime()
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if ready:
            self.call(self.A, 'power.set', enabled=True)
            self.call(self.A, 'gas.select', gas_id=gas)
            self.h.host_connect_ports(INST, upstream_node_id='supply', downstream_node_id='receiver')
            self.boundary()
            self.h.host_configure_dynamics(INST, time_constant_s=tau, evidence='Synthetic test assumption')
            self.call(self.A, 'flow.zero')
            self.call(self.A, 'flow.reset_total')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0                                                    # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def boundary(self, flowing=False, upstream=130000.0, temperature=298.15):
        self.h.host_set_boundary(INST, upstream_pressure_Pa=upstream, downstream_pressure_Pa=101325.0, temperature_K=temperature,
                                 equilibrium=not flowing, no_flow=not flowing)

    # gestures (agent session)
    def press(self, key, hold=0.1, held=()):
        args = dict(input_id=key, duration_s=hold)
        if held:
            args['held'] = list(held)
        return self.call(self.A, 'panel.press', **args)['accepted']

    def keys(self, *seq):
        for k in seq:
            self.press(k)

    # screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lcd(self):
        """The text shown now, from the frame and from the pixels (both must agree outside the large-digit area)."""
        fr = self.frame()['displays']['lcd']
        img = self.h.panel(INST).render('lcd')
        if fr.get('rotated'):
            img = img[::-1, ::-1]
        read = read_text(img)
        covered = {r for w in fr['widgets'] for r in range(10) if w['rect'][1] < 2 + 9 * r + 7 and 2 + 9 * r < w['rect'][1] + w['rect'][3]}
        for r, line in enumerate(fr['shown']):
            if r not in covered:
                assert line.rstrip() == read[r], (r, line, read[r])
        return [x.rstrip() for x in fr['shown']]

    def big(self):
        """The large digits, from the frame and from the pixels."""
        fr = self.frame()['displays']['lcd']
        w = next(w for w in fr['widgets'] if w['id'] == 'big')
        img = self.h.panel(INST).render('lcd')
        assert read_big(img, w['rect'], w.get('align', 'left')) == w['text'], (w['text'], read_big(img, w['rect'], w.get('align', 'left')))
        return w['text']

    def text(self):
        return '\n'.join(self.lcd())

    def step(self, s):
        while s > 1e-12:
            self.h.host_step(min(s, 3600.0))
            s -= min(s, 3600.0)

    def state(self, *ids):
        o = self.E.read(instance_id=INST, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def setting(self, key):
        return self.state('device.settings')[key]

    # navigation by the menu cursor (the agent reads the screen; tests use the frame for the cursor position)
    def cursor(self):
        return self.frame()['ui']['menu']['item']

    def select(self, item_id, limit=60):
        """DOWN, then UP, until the > cursor is on the item, then SELECT."""
        for key in (TR, TC):
            for _ in range(limit):
                if self.cursor() == item_id:
                    return self.press(BR)
                before = self.cursor()
                self.press(key)
                if self.cursor() == before:
                    break
        raise AssertionError(f'item {item_id} not reached; at {self.cursor()}')

    def choose(self, text):
        """In an option list: DOWN, then UP, until the > cursor is on the option, then SET."""
        for key in (TR, TC):
            for _ in range(40):
                shown = self.lcd()
                if any(line == '>' + text for line in shown):
                    return self.press(BR)
                self.press(key)
                if self.lcd() == shown:
                    break
        raise AssertionError(f'option {text} not found: {self.lcd()}')

    def enter_digits(self, value, dec):
        """A digit-wise entry: CLEAR, then per digit SELECT DIGIT and UP (the cursor wraps over the digits)."""
        ed = self.frame()['ui']['editor']
        self.press(BC)
        f = next(x for x in SPEC_FIELDS() if x['id'] == ed['field'])
        text = f'{value:0{f["digits"] + (1 if dec else 0)}.{dec}f}'.replace('.', '')
        assert len(text) == f['digits'], (text, f['digits'])
        for k, ch in enumerate(text):
            while self.frame()['ui']['editor']['digit'] != k:
                self.press(TL)
            for _ in range(int(ch)):
                self.press(TC)
        return self.press(BR)

    def menu(self, menu):
        """MENU from the main display, then CONTROL / ABOUT / SETUP."""
        if self.screen() != 'main':
            self.keys(BC) if self.screen() == 'list' else None
        assert self.screen() == 'main', self.screen()
        self.keys(BR, BR)                                                             # NEXT (totalizer), MENU
        assert self.screen() == 'menu'
        self.press({'control': TL, 'about': TC, 'setup': BC}[menu])


def SPEC_FIELDS():
    out = []
    for m in SPEC['menus'].values():
        stack = list(m['items'])
        while stack:
            it = stack.pop()
            stack += it.get('items', [])
            if it['type'] in ('number', 'text', 'enum'):
                out.append(it)
    for e in SPEC['editors'].values():
        out += e['fields']
    return out


def runtime_state(h):
    s = h.debug_snapshot()['instances'][INST]
    return json.dumps(dict(values=s['values'], zero=s['zero_since_s'], tared=s['autotare_done'], avg=s['averages']), sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_tare_the_flow_reading(self):
        g = Rig()
        g.call(g.A, 'menu.configure', autotare=False)
        g.call(g.A, 'gas.set_reference_conditions', temperature_K=293.15, pressure_Pa=101325.0)     # the reading is no longer tared
        self.assertFalse(g.state('flow.zeroed'))
        self.assertIn('+----', g.lcd()[1] + g.big())
        g.keys(BR, BR, TR)                                                            # NEXT, MENU, TARE FLOW
        self.assertEqual(g.lcd()[3:5], ['    ENSURE NO FLOW', ' BEFORE PRESSING TARE'])
        self.assertEqual(g.lcd()[-1].split(), ['CANCEL', 'TARE'])
        g.press(BR)
        self.assertIn('VERIFY THAT THE', g.text())
        g.press(BR)                                                                   # SET
        self.assertTrue(g.state('flow.zeroed'))
        self.assertEqual(g.screen(), 'menu')
        g.press(BR)                                                                   # MAIN
        self.assertEqual(g.big(), '+0.0000')
        DONE_TASKS.add('Tare the flow reading')

    def test_change_the_flow_setpoint(self):
        g = Rig()
        g.boundary(flowing=True)
        g.press(TR)                                                                   # SETPT
        ls = g.lcd()
        self.assertEqual((ls[0].split(), ls[1].strip(), ls[2]), (['SELECT', 'UP', 'DOWN'], 'DIGIT', 'Setpoint'))
        self.assertEqual(ls[7], 'SLPM: +1.0000 Max')
        self.assertEqual(g.big(), '+0.0000')
        g.enter_digits(0.75, 4)
        self.assertAlmostEqual(g.state('flow.setpoint_m3_s'), 0.75 / 60000.0, delta=1e-15)
        self.assertEqual(g.screen(), 'main')
        g.step(3.0)
        self.assertEqual(g.big(), '+0.7500')
        self.assertEqual(g.lcd()[1].split()[-1], '+0.7500')
        g.keys(TR, BC, BR)                                                            # cancel the setpoint: CLEAR then SET
        self.assertEqual(g.state('flow.setpoint_m3_s'), 0.0)
        g.keys(BR, BR, TL)                                                            # also MENU > CONTROL > Setpt
        self.assertEqual(g.lcd()[1], '>Setpt: +0.0000 SLPM >')
        g.press(BR)
        g.enter_digits(0.25, 4)
        self.assertEqual(g.screen(), 'list')
        self.assertEqual(g.lcd()[1], '>Setpt: +0.2500 SLPM >')
        DONE_TASKS.add('Change the flow setpoint')

    def test_select_a_different_gas(self):
        g = Rig()
        g.menu('setup')
        self.assertEqual(g.lcd()[1], '>Active Gas: Air     >')
        g.press(BR)
        ls = g.lcd()
        self.assertEqual((ls[0].split(), ls[1:4]), (['PAGE', 'UP', 'DOWN'], ['>Recent              >', ' Standard            >', ' COMPOSER Mixes      >']))
        g.press(TL)                                                                   # PAGE: the next page of categories
        self.assertEqual(g.cursor(), 'cat_5')
        g.press(TL)
        g.select('cat_0')                                                             # Standard
        ls = g.lcd()
        self.assertEqual((ls[0].split(), ls[-1].split()), (['PAGE', 'UP', 'DOWN'], ['CANCEL', 'INFO', 'SET']))
        self.assertEqual(ls[1:4], ['>Air', ' Ar Argon', ' CH4 Methane'])
        g.select('gas_gas_nitrogen')
        self.assertEqual(g.state('gas.selected_id'), 'gas_nitrogen')
        self.assertEqual((g.screen(), g.cursor()), ('list', 'active_gas'))           # back in the setup menu
        self.assertEqual(g.lcd()[1], '>Active Gas: N2      >')
        self.assertEqual(g.state('gas.recent_ids'), ['gas_nitrogen', 'mix_air'])
        g.keys(BR, BR)                                                                # Recent lists it first
        self.assertEqual(g.lcd()[1:3], ['>N2 Nitrogen', ' Air'])
        DONE_TASKS.add('Select a different gas')

    def test_change_engineering_units(self):
        g = Rig()
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS / 2)
        g.boundary(flowing=True)
        g.step(3.0)
        g.menu('setup')
        g.select('sensor')
        g.select('eng_units')
        self.assertEqual(g.lcd()[1:4], ['>Mass Flow           >', ' Volumetric Flow     >', ' Pressure            >'])
        g.press(BR)
        g.choose('SCCM')
        self.assertEqual(g.setting('units_mass_flow'), 'SCCM')
        g.select('u_press')
        g.choose('kPaA')
        g.press(BC)                                                                   # MAIN
        ls = g.lcd()
        self.assertEqual(ls[0].split()[0], 'kPaA')
        self.assertEqual(ls[1].split()[0], '+130.00')
        self.assertEqual((g.big(), ls[3].strip(), ls[-1].split()[1]), ('+500.0', 'SCCM', 'SCCM'))
        DONE_TASKS.add('Change engineering units')

    def test_dispense_a_batch(self):
        g = Rig()
        g.boundary(flowing=True)
        g.press(BR)                                                                   # NEXT: the totalizer
        self.assertEqual(g.lcd()[8], '-NONE-')
        g.press(BL)                                                                   # BATCH
        self.assertEqual(g.lcd()[2], 'Batch Size')
        g.enter_digits(0.1, 3)                                                        # 0.100 SL
        self.assertAlmostEqual(g.state('totalizer.batch_size_m3'), 1e-4, delta=1e-15)
        g.press(TC)                                                                   # SETPT: a non-zero setpoint starts the flow
        g.enter_digits(0.5, 4)
        self.assertEqual(g.screen(), 'totalizer')
        g.step(6.0)
        self.assertEqual(g.lcd()[-1].split()[0], 'REMAIN')
        remaining = g.state('totalizer.batch_remaining_m3')
        self.assertAlmostEqual(remaining, 1e-4 - g.state('measurement.total_standard_volume_m3'), delta=1e-15)
        self.assertEqual(g.lcd()[8], f'+{remaining * 1000:.3f}')
        g.step(10.0)                                                                  # 0.1 SL at 0.5 SLPM: about 12 s, then the valve closes
        self.assertEqual((g.lcd()[8], g.lcd()[-1].split()[0]), ('-DONE-', 'BATCH'))
        self.assertGreaterEqual(g.state('measurement.total_standard_volume_m3'), 1e-4 - 1e-15)
        g.step(5.0)
        self.assertLess(g.state('measurement.standard_flow_m3_s'), FS * 1e-6)         # flow stopped
        g.press(BC)                                                                   # RESET: the same batch again
        self.assertEqual(g.state('measurement.total_standard_volume_m3'), 0.0)
        g.step(1.0)
        self.assertGreater(g.state('measurement.standard_flow_m3_s'), FS * 0.4)
        DONE_TASKS.add('Dispense a batch')

    def test_reset_the_totalizer(self):
        g = Rig()
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS)
        g.boundary(flowing=True)
        g.step(30.0)
        g.press(BR)
        self.assertEqual(g.big(), '+0.497')                                           # 1 SLPM for 30 s minus the start-up lag
        self.assertEqual(g.lcd()[6].strip(), '0:00:30 h:m:s')
        g.press(TL)                                                                   # TOTAL/TIMER
        self.assertEqual(g.big(), '0:00:30')
        g.press(BC)                                                                   # RESET
        self.assertEqual((g.big(), g.state('measurement.total_standard_volume_m3'), g.state('totalizer.elapsed_s')), ('0:00:00', 0.0, 0.0))
        DONE_TASKS.add('Reset the totalizer')

    def test_set_standard_reference_conditions(self):
        g = Rig()
        g.menu('setup')
        g.select('sensor')
        g.select('stp_ref')
        self.assertEqual(g.lcd()[1:3], ['>Stan T: 25.00 °C    >', ' Stan P: 1.000 atm   >'])
        g.press(BR)
        self.assertEqual(g.lcd()[2], 'Standard Temperature')
        g.enter_digits(20.0, 2)
        self.assertAlmostEqual(g.state('gas.reference_temperature_k'), 293.15, delta=1e-9)
        self.assertFalse(g.state('flow.zeroed'))                                      # a new reference invalidates the tare (runtime rule)
        g.select('stan_p_atm')
        g.enter_digits(1.0, 3)
        self.assertEqual(g.lcd()[1:3], [' Stan T: 20.00 °C    >', '>Stan P: 1.000 atm   >'])
        DONE_TASKS.add('Set standard reference conditions')

    def test_unlock_a_locked_front_panel(self):
        g = Rig()
        g.call(g.A, 'display.set_lock', locked=True)                                 # the serial lock command
        self.assertEqual(g.lcd()[5].strip(), 'LCK')
        self.assertFalse(g.press(BR))                                                 # the keys do nothing
        self.assertEqual(g.screen(), 'main')
        self.assertFalse(g.press(TL, hold=2.0, held=[TR, BL]))                       # three keys are not enough
        self.assertTrue(g.press(TL, hold=2.0, held=[TR, BL, BR]))                    # hold all four outer keys
        self.assertFalse(g.state('display.locked'))
        self.assertEqual(g.lcd()[5].strip(), '')
        self.assertTrue(g.press(BR))
        DONE_TASKS.add('Unlock a locked front panel (LCK)')


# ---------------------------------------------------------------- every other operator function of the menus
class SetpointAndControlMenus(unittest.TestCase):
    def setup_menu(self, g, *path):
        g.menu('control')
        for item in path:
            g.select(item)

    def test_autotare_toggle_and_delay(self):
        g = Rig()
        self.setup_menu(g, 'setpoint_setup', 'zero_setpoint')
        self.assertEqual(g.lcd()[1:4], ['>Autotare: On', ' Delay Before Tare   >', ' Ramp: Instantly zero'])
        g.press(BR)                                                                   # SELECT toggles
        self.assertEqual((g.lcd()[1], g.setting('autotare')), ('>Autotare: Off', False))
        g.press(BR)
        g.select('autotare_delay')
        g.enter_digits(5.0, 1)
        self.assertEqual(g.setting('autotare_delay_s'), 5.0)
        g.select('zero_ramp')
        self.assertEqual((g.lcd()[3], g.setting('ramp_zero_setpoint')), ('>Ramp: Honor ramp to 0', True))
        # behind the panel: a zero setpoint held for the delay tares the reading at a host-confirmed no-flow equilibrium
        g.call(g.A, 'gas.set_reference_conditions', temperature_K=298.15, pressure_Pa=101000.0)
        self.assertFalse(g.state('flow.zeroed'))
        g.step(4.0)
        self.assertFalse(g.state('flow.zeroed'))
        g.step(1.0)
        self.assertTrue(g.state('flow.zeroed'))

    def test_setpoint_source_analog(self):
        g = Rig()
        self.setup_menu(g, 'setpoint_setup', 'sp_source')
        self.assertEqual(g.lcd()[1:3], ['>Serial / Front Panel', ' Analog'])
        g.choose('Analog')
        self.assertEqual(g.setting('setpoint_source'), 'analog')
        g.h.host_set_analog_setpoint_input(INST, volts=2.5)                           # 0-5 V = 0-100 % FS
        self.assertAlmostEqual(g.state('flow.setpoint_m3_s'), FS / 2, delta=1e-15)
        g.press(BC)
        self.assertFalse(g.press(TR))                                                 # SETPT does nothing with an analog source
        self.assertEqual(g.screen(), 'main')
        r = g.call(g.A, 'flow.set_setpoint', expect='rejected', flow_rate_m3_s=FS / 4)
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')
        g.call(g.A, 'menu.configure', setpoint_max_m3_s=FS * 0.4)                     # analog setpoints beyond a limit sit at the limit
        self.assertAlmostEqual(g.state('flow.setpoint_m3_s'), FS * 0.4, delta=1e-15)

    def test_modbus_timeout_and_power_up_setpoint(self):
        g = Rig()
        self.setup_menu(g, 'setpoint_setup', 'modbus_timeout')
        g.choose('Maintain Last Setpt')
        self.assertEqual(g.setting('modbus_timeout_action'), 'hold')
        g.select('power_up_sp')
        self.assertEqual(g.lcd()[1:3], ['>Value: Last Setpt   >', ' Ramp: Jump from 0'])
        g.press(BR)
        self.assertEqual(g.lcd()[1:3], ['>Last Setpoint', ' Fixed Setpoint      >'])
        g.press(TR)
        g.press(BR)
        g.enter_digits(0.3, 4)
        self.assertEqual(g.setting('power_up_setpoint_mode'), 'fixed')
        self.assertAlmostEqual(g.setting('power_up_setpoint_m3_s'), 0.3 / 60000.0, delta=1e-18)
        g.press(BL)
        self.assertEqual(g.lcd()[1], '>Value: 0.3000       >')
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS * 0.9)
        g.call(g.A, 'power.set', enabled=False)
        g.call(g.A, 'power.set', enabled=True)
        self.assertAlmostEqual(g.state('flow.setpoint_m3_s'), 0.3 / 60000.0, delta=1e-18)   # the fixed value on power-up
        g.keys(BR, BR, TL)
        g.select('setpoint_setup')
        g.select('power_up_sp')
        g.keys(BR, BR)                                                                # Value > Last Setpoint
        self.assertEqual(g.setting('power_up_setpoint_mode'), 'last')
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS * 0.9)
        g.call(g.A, 'power.set', enabled=False)
        g.call(g.A, 'power.set', enabled=True)
        self.assertEqual(g.state('flow.setpoint_m3_s'), FS * 0.9)

    def test_setpoint_limits(self):
        g = Rig()
        self.setup_menu(g, 'setpoint_setup', 'sp_limits')
        self.assertEqual(g.lcd()[1:4], ['>User Min: +0.0000   >', ' User Max: +1.0000   >', ' Clear Limits'])
        g.select('umax_slpm')
        self.assertEqual(g.lcd()[2:8:5], ['Maximum Setpoint', 'SLPM: +1.0000 Max'])
        g.enter_digits(0.8, 4)
        g.select('umin_slpm')
        g.enter_digits(0.1, 4)
        self.assertAlmostEqual(g.setting('setpoint_min_m3_s'), 0.1 / 60000.0, delta=1e-18)
        self.assertAlmostEqual(g.setting('setpoint_max_m3_s'), 0.8 / 60000.0, delta=1e-18)
        g.press(BC)
        g.press(TR)
        before = g.state('flow.setpoint_m3_s')
        g.enter_digits(0.9, 4)                                                         # above the user maximum: refused, back to the main display
        self.assertEqual((g.state('flow.setpoint_m3_s'), g.screen()), (before, 'main'))
        r = g.call(g.A, 'flow.set_setpoint', expect='rejected', flow_rate_m3_s=0.0)    # a non-zero minimum cannot stop the flow
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.5 / 60000.0)
        g.keys(BR, BR, TL)
        g.select('setpoint_setup')
        g.select('sp_limits')
        g.select('clear_limits')
        self.assertEqual((g.setting('setpoint_min_m3_s'), g.setting('setpoint_max_m3_s')), (0.0, FS))

    def test_control_loop(self):
        g = Rig()
        self.setup_menu(g, 'control_loop')
        self.assertEqual(g.lcd()[1:5], ['>Control: Mass Flow  >', ' Loop Type: PD / PDF >', ' P Gain: 167         >', ' D Gain: 1280        >'])
        g.press(BR)
        g.choose('Absolute Pressure')                                                  # not simulated: refused, the setting stays
        self.assertEqual(g.setting('control_variable'), 'mass_flow')
        g.press(BR)
        g.choose('Volumetric Flow')
        self.assertEqual(g.setting('control_variable'), 'volumetric_flow')
        g.select('loop_type')
        g.choose('PD2I')
        g.select('i_gain')
        g.enter_digits(25, 0)
        g.select('p_gain')
        g.enter_digits(200, 0)
        self.assertEqual((g.setting('loop_type'), g.setting('p_gain'), g.setting('i_gain')), ('pd2i', 200, 25))
        g.select('flow_limiter')
        g.press(BR)
        g.choose('Volumetric Flow')
        g.select('limit_max_slpm')
        g.enter_digits(0.6, 4)
        g.select('limiter_gain')
        g.enter_digits(700, 0)
        self.assertEqual((g.setting('flow_limit_type'), g.setting('limiter_gain')), ('volumetric_flow', 700))
        self.assertAlmostEqual(g.setting('flow_limit_max_m3_s'), 0.6 / 60000.0, delta=1e-18)
        g.press(BL)
        g.select('deadband')
        self.assertEqual(g.lcd()[3:5], ['   Only active when', ' controlling pressure'])   # the device's own message instead of the deadband menu
        g.step(2.0)
        self.assertEqual((g.screen(), g.cursor()), ('list', 'deadband'))
        g.keys(BC, TR)                                                                 # volumetric control: the setpoint is entered in LPM
        self.assertEqual(g.lcd()[7], 'LPM: +1.0000 Max')

    def test_setpoint_ramping(self):
        g = Rig()
        self.setup_menu(g, 'sp_ramp')
        self.assertEqual(g.lcd()[1:3], ['>Ramp: 0.0000        >', ' Units: SLPM / s     >'])
        g.press(BR)
        self.assertEqual((g.lcd()[2], g.lcd()[7]), ('Max Ramp Rate', 'SLPM / s'))
        g.enter_digits(0.1, 4)                                                         # 0.1 SLPM per second
        self.assertAlmostEqual(g.state('flow.ramp_rate_m3_s2'), 0.1 / 60000.0, delta=1e-18)
        g.select('ramp_units')
        g.choose('m')
        self.assertEqual(g.lcd()[1:3], [' Ramp: 6.0000        >', '>Units: SLPM / m     >'])
        g.select('delta_time')
        self.assertEqual(g.lcd()[1:4], ['>Delta: +0.1000 SLPM >', ' Time: 0.0167 m      >', ' Time Units: m       >'])
        g.select('ramp_time')
        g.enter_digits(0.5, 4)                                                         # 0.1 SLPM per half minute
        self.assertAlmostEqual(g.setting('ramp_time_s'), 30.0, delta=1e-9)
        g.press(BL)
        for item, key, line in (('ramp_up', 'ramp_up', 'Ramp Up: Off'), ('ramp_down', 'ramp_down', 'Ramp Down: On'),
                                ('ramp_pu', 'ramp_power_up', 'Power Up: Allow Ramp'), ('ramp_zero', 'ramp_zero_setpoint', '0 Setpt: Allow Ramp')):
            before = g.setting(key)
            g.select(item)
            self.assertNotEqual(g.setting(key), before)
            self.assertIn('>' + line, g.lcd())
        g.select('ramp_up')                                                            # back on: ramps up at 0.2 SLPM/min
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.5 / 60000.0)
        self.assertEqual(g.state('flow.active_setpoint_m3_s'), 0.0)
        g.boundary(flowing=True)
        g.step(60.0)
        self.assertAlmostEqual(g.state('flow.active_setpoint_m3_s'), 0.2 / 60000.0, delta=1e-15)

    def test_show_valve(self):
        g = Rig()
        g.menu('control')
        g.select('show_valve')
        g.choose('Main Screen')
        self.assertEqual(g.setting('show_valve'), 'main_screen')
        g.press(BC)
        self.assertEqual(g.lcd()[2].strip(), '0.00%')                                   # zero setpoint, no flow: the valve is not driven
        g.boundary(flowing=True)
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.5 * FS)
        g.step(5.0)                                                                    # settled (tau 0.2 s)
        q, q_open = g.state('truth.standard_flow_m3_s'), FS * (130000.0 - 101325.0) / rt.ALICAT_MC_DP_FS_PA
        drive = g.state('valve.drive_percent')
        self.assertAlmostEqual(drive, 100.0 * q / q_open, places=9)                    # the drive the flow needs on the linear valve
        self.assertEqual(g.lcd()[2].strip(), '%.2f%%' % drive)
        g.h.host_set_boundary(INST, upstream_pressure_Pa=130000.0, downstream_pressure_Pa=101325.0, temperature_K=298.15, equilibrium=False, no_flow=True)
        self.assertEqual(g.state('valve.drive_percent'), 100.0)                        # setpoint without flow: the controller drives fully open
        g.call(g.A, 'power.set', enabled=False)
        self.assertEqual(g.state('valve.drive_percent'), 0.0)

    def test_gains_set_the_settling(self):
        def settle(**gains):
            g = Rig(tau=0.2)
            if gains:
                g.call(g.A, 'menu.configure', **gains)
            g.boundary(flowing=True)
            g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.5 * FS)
            g.step(0.2)
            return g.state('measurement.standard_flow_m3_s') / (0.5 * FS)              # fraction of the step reached after 0.2 s (indicated)
        factory = settle()
        self.assertAlmostEqual(factory, 1 - math.exp(-1.0), delta=0.02)                 # host tau at the factory gains (167 / 1280)
        self.assertAlmostEqual(settle(p_gain=334), 1 - math.exp(-2.0), delta=0.02)      # larger P: faster (manual p.14)
        self.assertAlmostEqual(settle(d_gain=2560), 1 - math.exp(-0.5), delta=0.02)     # larger D: slower
        self.assertAlmostEqual(settle(d_gain=0), 1 - math.exp(-0.2 / 0.03), delta=1e-6)  # bounded by the fastest response (T63 30 ms)
        self.assertEqual(settle(p_gain=0), 0.0)                                         # no correction: the valve output stays
        self.assertAlmostEqual(settle(loop_type='pd2i', p_gain=167, i_gain=167), 1 - math.exp(-2.0), delta=0.02)   # PD2I: P and I


class SetupMenus(unittest.TestCase):
    def test_composer_mix_create_select_info_delete(self):
        g = Rig()
        g.menu('setup')
        g.press(BR)                                                                    # Active Gas
        g.select('cat_composer')
        self.assertEqual(g.lcd()[1], '>Create Mix          >')
        g.press(BR)
        self.assertEqual(g.lcd()[1:4], ['>Short Name: MIX     >', ' Long Name:          >', ' Add Gas to Mix      >'])
        g.press(BR)                                                                    # the name: SELECT CHAR, UP / DOWN change the character
        self.assertEqual(g.lcd()[0].split()[0], 'SELECT')
        g.press(TR)                                                                    # 'M' -> 'L'
        g.press(TL)
        g.press(TC)                                                                    # 'I' -> 'J'
        g.press(BR)
        self.assertEqual(g.frame()['ui']['vars']['draft_short'], 'LJX')
        g.select('add_gas')                                                             # Add Gas to Mix: Gas Select, then the percentage
        self.assertEqual(g.screen(), 'gas')
        g.press(BR)
        g.select('gas_gas_carbon_dioxide')
        self.assertEqual(g.lcd()[2], 'Percent of Mix')
        g.enter_digits(25.0, 2)
        self.assertEqual(g.screen(), 'composer')
        g.select('add_gas')
        g.press(BR)
        g.select('gas_gas_argon')
        g.enter_digits(70.0, 2)
        self.assertIn(' 25.00% CO2          >', g.lcd())
        self.assertIn(' Total: 95.00%', g.lcd())
        self.assertNotIn(' Save Mix', g.lcd())                                         # only at 100 %
        g.select('comp_2')
        g.press(BR)                                                                    # Set % to Balance
        self.assertIn('>75.00% Ar           >', g.lcd())
        g.select('save_2')
        mixes = g.state('gas.user_mixes')
        self.assertEqual(mixes, [{'mix_number': 255, 'short_name': 'LJX', 'long_name': '',
                                  'components': [{'gas_id': 'gas_carbon_dioxide', 'percent': 25.0}, {'gas_id': 'gas_argon', 'percent': 75.0}]}])
        self.assertEqual((g.screen(), g.lcd()[1]), ('gas', '>LJX'))
        g.press(BR)                                                                    # SET: load the mix
        self.assertEqual(g.state('gas.selected_id'), 'composer_255')
        g.press(BC)
        self.assertEqual(g.lcd()[4].strip(), 'LJX')
        # the mix is the C-25 composition: same readings as the library mixture
        twin = Rig(gas='mix_c_25')
        for r in (g, twin):
            r.call(r.A, 'flow.zero')
            r.call(r.A, 'flow.set_setpoint', flow_rate_m3_s=FS / 2)
            r.boundary(flowing=True)
            r.step(2.0)
        for sid in ('measurement.standard_flow_m3_s', 'measurement.actual_flow_m3_s', 'measurement.mass_flow_kg_s'):
            self.assertAlmostEqual(g.state(sid), twin.state(sid), delta=abs(twin.state(sid)) * 1e-9)
        # INFO, create similar, delete
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.0)
        g.boundary()
        g.step(2.0)
        g.menu('setup')
        g.press(BR)
        g.select('cat_composer')
        g.press(BC)                                                                    # INFO
        self.assertEqual(g.lcd()[2:7], ['LJX', '', 'Gas #255', '25.00% CO2', '75.00% Ar'])
        self.assertFalse(g.press(TL))                                                  # the loaded mix cannot be deleted
        g.press(TR)                                                                    # CREATE SIMILAR: the composition is copied
        self.assertEqual((g.screen(), g.frame()['ui']['vars']['c2_pct']), ('composer', 75.0))
        g.press(BL)                                                                    # BACK discards it
        self.assertEqual(len(g.state('gas.user_mixes')), 1)
        g.call(g.A, 'gas.select', gas_id='mix_air')
        g.press(BC)
        self.assertTrue(g.press(TL))                                                   # DELETE
        self.assertEqual(g.state('gas.user_mixes'), [])

    def test_gas_info_and_refused_change_while_flowing(self):
        g = Rig()
        g.menu('setup')
        g.press(BR)
        g.select('cat_2')                                                               # Breathing
        g.press(BC)                                                                     # INFO on the first gas
        ls = g.lcd()
        self.assertEqual(ls[2:5], ['EAN-32', '32% O2 + 68% N2', 'Gas #164'])
        self.assertEqual(ls[5:7], ['68.00% N2', '32.00% O2'])
        g.press(BL)
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS / 2)
        g.boundary(flowing=True)
        g.step(1.0)
        self.assertFalse(g.press(BR))                                                   # gas changes need a stopped flow: nothing happens
        self.assertEqual((g.screen(), g.state('gas.selected_id')), ('gas', 'mix_air'))

    def test_all_engineering_units(self):
        g = Rig()
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS / 2)
        g.boundary(flowing=True)
        g.step(10.0)
        g.menu('setup')
        g.select('sensor')
        g.select('eng_units')
        for item, option, key, value in (('u_vol', 'CCM', 'units_volumetric_flow', 'CCM'), ('u_temp', '°F', 'units_temperature', 'F'),
                                         ('u_mtot', 'SmL', 'units_mass_total', 'SmL'), ('u_vtot', 'mL', 'units_volumetric_total', 'mL'),
                                         ('u_time', 's', 'units_time_of_total', 's')):
            g.select(item)
            g.choose(option)
            self.assertEqual(g.setting(key), value)
        g.press(BC)
        ls = g.lcd()
        self.assertEqual((ls[0].split()[1], ls[1].split()[1], ls[9].split()[0]), ('°F', '+77.00', 'CCM'))
        g.press(BR)
        self.assertEqual(g.lcd()[6].strip(), '10 s')
        self.assertEqual(g.big(), f"+{g.state('measurement.total_standard_volume_m3') * 1e6:.1f}")

    def test_averaging_zero_band(self):
        g = Rig()
        g.menu('setup')
        g.select('sensor')
        g.select('flow_avg')
        self.assertEqual(g.lcd()[7], 'msec: 0 - 255')
        g.enter_digits(255, 0)
        g.select('press_avg')
        g.enter_digits(100, 0)
        g.select('zero_band')
        g.enter_digits(6.38, 2)
        self.assertEqual((g.setting('flow_averaging_ms'), g.setting('pressure_averaging_ms'), g.setting('zero_band_pct')), (255, 100, 6.38))
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS * 0.05)
        g.boundary(flowing=True)
        g.step(5.0)
        self.assertEqual(g.state('measurement.standard_flow_m3_s'), 0.0)             # below the zero band: shown as 0
        g.call(g.A, 'menu.configure', zero_band_pct=0.0)
        self.assertAlmostEqual(g.state('measurement.standard_flow_m3_s'), FS * 0.05, delta=1e-12)

    def test_serial_settings(self):
        g = Rig()
        g.menu('setup')
        g.select('serial')
        self.assertEqual(g.lcd()[1:5], ['>Unit ID: A          >', ' Modbus Address: 1   >', ' Modbus Idle: Infinit>', ' Baud Rate: 19200    >'])
        g.press(BR)
        g.choose('B')
        g.select('modbus_addr')
        g.enter_digits(12, 0)
        g.select('modbus_idle')
        g.select('idle_time')
        g.enter_digits(30.0, 1)
        g.press(BL)
        g.select('baud')
        g.choose('9600')
        st = g.state('device.settings')
        self.assertEqual((st['unit_id'], st['modbus_address'], st['modbus_idle_s'], st['baud']), ('B', 12, 30.0, 9600))
        g.select('modbus_idle')
        g.select('idle_infinite')
        self.assertIsNone(g.setting('modbus_idle_s'))

    def test_display_settings_actions_menu_backlight_contrast_rotation(self):
        g = Rig()
        g.menu('setup')
        g.select('display')
        g.select('main_screen')
        g.press(BR)
        g.choose('Show Actions Menu')
        g.press(BC)
        g.press(TL)                                                                     # the pressure key opens its actions menu
        self.assertEqual((g.screen(), g.lcd()[1:3]), ('actions', ['>Highlight', ' Engineering Units   >']))
        g.press(BR)
        self.assertEqual((g.screen(), g.big(), g.lcd()[3].strip()), ('main', '+18.85', 'PSIA'))
        g.press(BC)
        g.keys(TR, BR)                                                                  # Engineering Units: the mass flow unit list
        self.assertEqual((g.screen(), g.frame()['ui']['menu']['item']), ('list', 'u_mass'))
        g.press(BC)
        bright = g.frame()['displays']['lcd']['brightness']
        g.press('logo')                                                                 # backlight off
        self.assertEqual((g.state('display.backlight_on'), g.frame()['displays']['lcd']['brightness']), (False, 55.0))
        g.press('logo')
        self.assertEqual(g.frame()['displays']['lcd']['brightness'], bright)
        g.menu('setup')
        g.select('display')
        g.select('lighting')
        self.assertEqual(g.lcd()[2].split(), ['-', '+0', '+'])
        g.keys(TR, TR, TL, TR)
        self.assertEqual(g.setting('contrast'), 2)
        g.press(BC)                                                                     # POWER UP: Dark
        self.assertEqual((g.setting('power_up_backlight'), g.lcd()[7].strip()), (False, 'Dark'))
        g.press(BL)
        g.select('rotation')
        g.choose('Inverted')
        fr = g.frame()['displays']['lcd']
        self.assertTrue(fr['rotated'])
        g.lcd()                                                                         # the pixel reader reads the turned image
        g.call(g.A, 'power.set', enabled=False)
        g.call(g.A, 'power.set', enabled=True)
        self.assertFalse(g.state('display.backlight_on'))

    def test_factory_restore(self):
        g = Rig()
        g.call(g.A, 'menu.configure', units_mass_flow='SCCM', autotare=False, p_gain=10)
        g.call(g.A, 'gas.select', gas_id='gas_nitrogen')
        g.menu('setup')
        g.select('advanced')
        self.assertEqual(g.lcd()[1:5], ['>Factory Restore     >', ' Register Status     >', ' Edit Register       >', ' Device Properties   >'])
        g.press(BR)
        g.press(BL)                                                                     # CANCEL
        self.assertEqual(g.cursor(), 'factory_restore')
        g.keys(BR, BR)
        self.assertEqual(g.state('device.settings'), {k: v[1] for k, v in rt._AMC_SETTINGS.items()})
        self.assertEqual((g.state('gas.selected_id'), g.screen()), ('mix_air', 'main'))

    def test_service_screens(self):
        g = Rig()
        g.menu('setup')
        g.select('advanced')
        for item in ('register_status', 'edit_register', 'device_properties'):
            self.assertFalse(g.select(item))
            self.assertIn(item.split('_')[0].title(), g.frame()['ui']['unsupported'])
            self.assertEqual(g.screen(), 'list')

    def test_about_pages(self):
        g = Rig()
        g.menu('about')
        self.assertEqual(g.lcd()[1:4], ['>About Device        >', ' Full Scale Ranges   >', ' About Manufacturer  >'])
        g.press(BR)
        self.assertEqual(g.lcd()[2:5], ['MODEL: MC-1SLPM-D', 'SERIAL NO: ------', 'DATE MFG: --/--/----'])
        g.press(BL)
        g.select('fs_ranges')
        self.assertEqual(g.lcd()[2:5], ['Mass: 1.0000 SLPM', 'Vol:  1.0000 LPM', 'Press: 160.00 PSIA'])
        g.press(BL)
        g.select('about_maker')
        self.assertEqual(g.lcd()[2:4], ['Alicat Scientific', 'alicat.com'])
        g.press(BC)
        self.assertEqual(g.screen(), 'main')

    def test_highlight_keys(self):
        g = Rig()
        for key, big, unit, name in ((TL, '+18.85', 'PSIA', 'Pressure'), (TC, '+25.00', '°C', 'Temperature'), (BL, '+0.0000', 'LPM', 'Volumetric Flow'),
                                     (BC, '+0.0000', 'SLPM', 'Mass Flow')):
            g.press(key)
            self.assertEqual((g.big(), g.lcd()[3].strip(), g.lcd()[6].strip()), (big, unit, name))


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def test_setpoint_units_autotare_batch_reset(self):
        a, b = Rig(), Rig()
        for g in (a, b):
            g.boundary(flowing=True)
        a.press(TR)
        a.enter_digits(0.5, 4)
        a.menu('setup')
        a.select('sensor')
        a.select('eng_units')
        a.press(BR)
        a.choose('SCCM')
        a.press(BC)
        a.menu('control')
        a.select('setpoint_setup')
        a.select('zero_setpoint')
        a.press(BR)                                                                    # Autotare: Off
        a.press(BC)
        a.press(BR)                                                                    # NEXT
        a.press(BL)                                                                    # BATCH 200.0 SmL? no: the total unit is SL
        a.enter_digits(0.2, 3)
        a.press(BC)                                                                    # RESET
        b.call(b.A, 'flow.set_setpoint', flow_rate_m3_s=0.5 * (1.0 / 60000.0))
        b.call(b.A, 'menu.configure', units_mass_flow='SCCM')
        b.call(b.A, 'menu.configure', autotare=False)
        b.call(b.A, 'totalizer.set_batch', batch_size_m3=0.2 * (1.0 / 1000.0))
        b.call(b.A, 'flow.reset_total')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        for g in (a, b):
            g.step(40.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        self.assertEqual(a.state('totalizer.batch_remaining_m3'), 0.0)

    def test_gas_mix_limits_ramp_display_restore(self):
        a, b = Rig(), Rig()
        a.menu('setup')
        a.press(BR)
        a.select('cat_composer')
        a.press(BR)                                                                    # Create Mix, default name MIX
        a.select('add_gas')
        a.press(BR)
        a.select('gas_gas_nitrogen')
        a.enter_digits(50.0, 2)
        a.select('add_gas')
        a.press(BR)
        a.select('gas_gas_oxygen')
        a.enter_digits(50.0, 2)
        a.select('save_2')
        a.press(BR)                                                                    # SET: load it
        a.press(BC)
        a.menu('control')
        a.select('setpoint_setup')
        a.select('sp_limits')
        a.select('umax_slpm')
        a.enter_digits(0.9, 4)
        a.press(BL)
        a.press(BL)
        a.select('sp_ramp')
        a.press(BR)
        a.enter_digits(0.05, 4)
        a.press(BC)
        a.press('logo')
        b.call(b.A, 'gas.create_mix', mix_number=0, short_name='MIX', long_name='', components=[{'gas_id': 'gas_nitrogen', 'percent': 50.0},
                                                                                                {'gas_id': 'gas_oxygen', 'percent': 50.0}])
        b.call(b.A, 'gas.select', gas_id='composer_255')
        b.call(b.A, 'menu.configure', setpoint_max_m3_s=0.9 * (1.0 / 60000.0))
        b.call(b.A, 'menu.configure', ramp_delta_m3_s=0.05 * (1.0 / 60000.0), ramp_time_s=1.0)
        b.call(b.A, 'display.set_backlight', on=False)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.menu('setup')
        a.select('advanced')
        a.keys(BR, BR)                                                                 # Factory Restore, SET
        b.call(b.A, 'device.factory_restore')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


# ---------------------------------------------------------------- the device functions behind the panel (runtime level)
class DeviceFunctions(unittest.TestCase):
    def test_ramp_is_the_exact_first_order_response_to_a_linear_setpoint(self):
        g = Rig(tau=0.2)
        g.call(g.A, 'menu.configure', ramp_delta_m3_s=0.1 / 60000.0, ramp_time_s=1.0, ramp_up=True, ramp_down=False)
        g.boundary(flowing=True)
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.6 / 60000.0)
        b, tau, t = 0.1 / 60000.0, 0.2, 2.0
        g.step(t)
        self.assertAlmostEqual(g.state('flow.active_setpoint_m3_s'), 0.2 / 60000.0, delta=1e-18)
        self.assertAlmostEqual(g.state('measurement.standard_flow_m3_s'), b * t - b * tau + b * tau * math.exp(-t / tau), delta=1e-17)
        total = b * t * t / 2 - b * tau * t + b * tau * tau * (1 - math.exp(-t / tau))
        self.assertAlmostEqual(g.state('measurement.total_standard_volume_m3'), total, delta=1e-17)
        split = Rig(tau=0.2)                                                            # the same schedule in small steps
        split.call(split.A, 'menu.configure', ramp_delta_m3_s=0.1 / 60000.0, ramp_time_s=1.0, ramp_up=True, ramp_down=False)
        split.boundary(flowing=True)
        split.call(split.A, 'flow.set_setpoint', flow_rate_m3_s=0.6 / 60000.0)
        for _ in range(40):
            split.step(0.05)
        self.assertAlmostEqual(split.state('measurement.total_standard_volume_m3'), total, delta=1e-16)
        g.step(10.0)
        self.assertEqual(g.state('flow.active_setpoint_m3_s'), 0.6 / 60000.0)
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.3 / 60000.0)                 # Ramp Down off: the new setpoint applies at once
        self.assertEqual(g.state('flow.active_setpoint_m3_s'), 0.3 / 60000.0)
        g.call(g.A, 'menu.configure', ramp_down=True)
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.0)                           # 0 Setpt: No Ramp
        self.assertEqual(g.state('flow.active_setpoint_m3_s'), 0.0)
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.5 / 60000.0)
        g.call(g.A, 'menu.configure', ramp_power_up=True)                              # Power Up: Allow Ramp, from zero
        g.step(10.0)
        g.call(g.A, 'power.set', enabled=False)
        g.call(g.A, 'power.set', enabled=True)
        self.assertEqual((g.state('flow.setpoint_m3_s'), g.state('flow.active_setpoint_m3_s')), (0.5 / 60000.0, 0.0))

    def test_batch_closes_the_valve_at_the_crossing_inside_a_step(self):
        g = Rig(tau=0.2)
        g.call(g.A, 'totalizer.set_batch', batch_size_m3=5e-5)                          # 0.050 SL
        g.boundary(flowing=True)
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS)
        g.step(60.0)                                                                    # one long step: about 3 s to the batch
        q_cross = FS                                                                    # the flow has settled long before the crossing
        over = g.state('measurement.total_standard_volume_m3') - 5e-5
        self.assertAlmostEqual(over, q_cross * 0.2, delta=FS * 0.2 * 1e-6)             # the closing valve's tail: q * tau
        self.assertEqual(g.state('totalizer.batch_remaining_m3'), 0.0)
        split = Rig(tau=0.2)
        split.call(split.A, 'totalizer.set_batch', batch_size_m3=5e-5)
        split.boundary(flowing=True)
        split.call(split.A, 'flow.set_setpoint', flow_rate_m3_s=FS)
        for _ in range(600):
            split.step(0.1)
        self.assertAlmostEqual(split.state('measurement.total_standard_volume_m3'), g.state('measurement.total_standard_volume_m3'), delta=1e-15)
        g.call(g.A, 'totalizer.set_batch', batch_size_m3=1e-4)                          # a larger batch: the flow continues
        g.step(1.0)
        self.assertGreater(g.state('measurement.standard_flow_m3_s'), FS * 0.9)

    def test_volumetric_control_holds_the_actual_flow(self):
        g = Rig(tau=0.2)
        g.call(g.A, 'menu.configure', control_variable='volumetric_flow')
        g.boundary(flowing=True, upstream=150000.0, temperature=310.0)
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS / 2)
        g.step(5.0)
        self.assertAlmostEqual(g.state('measurement.actual_flow_m3_s'), FS / 2, delta=FS * 1e-9)
        ideal = FS / 2 * (298.15 / 310.0) * (150000.0 / 101325.0)                    # standard flow of 0.5 LPM at 310 K and 1.48 atm (air, nearly ideal)
        self.assertAlmostEqual(g.state('measurement.standard_flow_m3_s'), ideal, delta=ideal * 2e-3)
        r = g.call(g.A, 'menu.configure', expect='unsupported', control_variable='absolute_pressure')
        self.assertEqual(r['error']['code'], 'UNSUPPORTED')

    def test_averaging_lags_the_readings_and_holds_between_steps(self):
        g = Rig(tau=0.2)
        g.call(g.A, 'menu.configure', pressure_averaging_ms=255)
        g.step(0.1)
        g.boundary(upstream=150000.0)
        self.assertEqual(g.state('measurement.pressure_pa'), 130000.0)                 # no time has passed: the average is unchanged
        g.step(0.255)
        self.assertAlmostEqual(g.state('measurement.pressure_pa'), 150000.0 - 20000.0 * math.exp(-1.0), delta=1e-6)
        g.step(10.0)
        self.assertAlmostEqual(g.state('measurement.pressure_pa'), 150000.0, delta=1e-6)

    def test_timer_freezes_and_totalizer_rolls_over(self):
        g = Rig()
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS)
        g.boundary(flowing=True)
        values = g.h._instances[INST]['values']
        values['totalizer.elapsed_s'] = float(rt.ALICAT_MC_TIMER_MAX_S - 5)
        values['truth.total_standard_volume_m3'] = 10.5                                # 10500 SL: past the 7 totalizer digits (9999.999 SL)
        g.step(10.0)
        self.assertEqual(g.state('totalizer.elapsed_s'), float(rt.ALICAT_MC_TIMER_MAX_S))
        g.press(BR)
        self.assertEqual(g.big()[:5], '+500.')                                         # rolled over
        g.press(TL)
        self.assertEqual(g.big(), '9999:59:59')
        g.press(BR)
        g.press(BR)
        self.assertEqual(g.lcd()[5].strip(), 'OVR')

    def test_volumetric_over_range_status(self):
        g = Rig()
        g.h.host_set_boundary(INST, upstream_pressure_Pa=130000.0, downstream_pressure_Pa=101325.0, temperature_K=333.15, equilibrium=False, no_flow=False)
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=FS)
        g.step(5.0)
        self.assertEqual(g.lcd()[5].strip(), '')
        g.h.host_set_boundary(INST, upstream_pressure_Pa=80000.0, downstream_pressure_Pa=79300.0, temperature_K=333.15, equilibrium=False, no_flow=False)
        self.assertEqual(g.lcd()[5].strip(), 'VOV')                                    # full-scale standard flow at 80 kPa: > 128 % FS actual
        g.step(5.0)                                                                    # 700 Pa across the valve passes far less (valve saturated)
        self.assertEqual((g.lcd()[5].strip(), g.state('valve.drive_percent')), ('', 100.0))


# ---------------------------------------------------------------- refusals, locks, time-outs
class StatusMessages(unittest.TestCase):
    """Status messages of manual p.6 (consistency sweep 2026-10-19): POV / TOV from the host boundaries (160 PSIA, -10..60 C), ADC injected by the
    evaluator."""
    def test_pov_tov_from_the_boundaries_and_injected_adc(self):
        g = Rig()
        self.assertEqual(g.state('device.alarms'), [])
        self.assertNotIn('POV', g.text())
        g.boundary(upstream=170 * rt.ALICAT_MC_PSI_PA)                                    # inlet above the 160 PSIA range
        self.assertEqual(g.state('device.alarms'), ['POV'])
        self.assertIn('POV', g.text())
        self.assertIsNone(g.state('measurement.pressure_pa'))                            # out of range: no valid reading
        g.boundary(temperature=343.15)                                                   # 70 C gas
        self.assertEqual(g.state('device.alarms'), ['TOV'])
        self.assertIn('TOV', g.text())
        g.boundary()
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='ADC')['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('fault.inject', g.A.describe(instance_id=INST)['capabilities'])
        self.assertEqual(g.call(g.E, 'fault.inject', code='ADC'), {'device.alarms': ['ADC']})
        self.assertIn('ADC', g.text())
        self.assertEqual(g.call(g.E, 'fault.inject', expect='rejected', code='ADC')['error']['code'], 'PRECONDITION_FAILED')
        g.call(g.A, 'power.set', enabled=False)
        self.assertEqual(g.state('device.alarms'), [])                                   # switched off: no display
        g.call(g.A, 'power.set', enabled=True)
        self.assertEqual(g.state('device.alarms'), ['ADC'])
        g.call(g.E, 'fault.clear')
        self.assertEqual(g.state('device.alarms'), [])
        self.assertNotIn('ADC', g.text())


class ValveDrive(unittest.TestCase):
    """Control variable Valve Drive (manual p.14; serial statistic 45: the setpoint directly drives the valve), sync audit 2026-10-19."""
    def test_valve_drive_control_from_the_panel_and_the_api(self):
        g = Rig(tau=0.2)
        up = 101325.0 + rt.ALICAT_MC_DP_FS_PA                                          # 1.5 PSID: 100 % drive passes full scale (linear valve)
        g.h.host_set_boundary(INST, upstream_pressure_Pa=up, downstream_pressure_Pa=101325.0, temperature_K=298.15, equilibrium=False, no_flow=False)
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.25 * FS)
        SetpointAndControlMenus.setup_menu(None, g, 'control_loop')
        g.press(BR)
        g.choose('Valve Drive')
        self.assertEqual((g.setting('control_variable'), g.state('valve.drive_setpoint_percent')), ('valve_drive', 25.0))   # % of full scale kept
        g.keys(BC)                                                                     # MAIN
        g.press(TR)                                                                    # SETPT: entered in %
        self.assertEqual(g.lcd()[7], '%: +100.00 Max')
        g.enter_digits(40.0, 2)
        self.assertEqual(g.state('valve.drive_setpoint_percent'), 40.0)
        g.step(5.0)
        self.assertAlmostEqual(g.state('truth.standard_flow_m3_s'), 0.4 * FS, delta=FS * 1e-9)   # open loop: drive x linear valve
        self.assertEqual((g.state('valve.drive_percent'), g.lcd()[1].split()[-1]), (40.0, '+40.00'))
        self.assertEqual(g.call(g.A, 'flow.set_setpoint', expect='rejected', flow_rate_m3_s=0.5 * FS)['error']['code'], 'PRECONDITION_FAILED')
        g.call(g.A, 'valve.set_drive_setpoint', percent=60.0)
        g.h.host_set_boundary(INST, upstream_pressure_Pa=101325.0 + 0.5 * rt.ALICAT_MC_DP_FS_PA, downstream_pressure_Pa=101325.0, temperature_K=298.15,
                              equilibrium=False, no_flow=False)
        g.step(5.0)
        self.assertAlmostEqual(g.state('truth.standard_flow_m3_s'), 0.3 * FS, delta=FS * 1e-9)   # half the pressure drop: half the flow at the same drive
        g.call(g.A, 'menu.configure', control_variable='mass_flow')
        self.assertAlmostEqual(g.state('flow.setpoint_m3_s'), 0.6 * FS, delta=1e-15)
        self.assertEqual(g.call(g.A, 'valve.set_drive_setpoint', expect='rejected', percent=10.0)['error']['code'], 'PRECONDITION_FAILED')
        g.call(g.A, 'menu.configure', control_variable='valve_drive', setpoint_source='analog')
        g.h.host_set_analog_setpoint_input(INST, volts=1.0)
        self.assertAlmostEqual(g.state('valve.drive_setpoint_percent'), 20.0)          # 0-5 V = 0-100 % drive

    def test_panel_equals_direct_drive_setpoint(self):
        a, b = Rig(), Rig()
        for g in (a, b):
            g.boundary(flowing=True)
            g.call(g.A, 'menu.configure', control_variable='valve_drive')
        a.press(TR)
        a.enter_digits(12.5, 2)
        b.call(b.A, 'valve.set_drive_setpoint', percent=12.5)
        for g in (a, b):
            g.step(2.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


class Rules(unittest.TestCase):
    def test_no_supply(self):
        g = Rig(ready=False)
        self.assertEqual((g.screen(), g.lcd()), ('off', [''] * 10))
        self.assertEqual(g.frame()['displays']['lcd']['brightness'], 30)
        self.assertFalse(g.press(BR))
        self.assertFalse(g.press('logo'))
        g.call(g.A, 'power.set', enabled=True)
        self.assertEqual(g.screen(), 'main')
        self.assertEqual(g.lcd()[1].split()[0], '+----')                               # no gas, no ports: readings unavailable

    def test_lock_in_a_menu_and_unlock(self):
        g = Rig()
        g.menu('setup')
        g.call(g.A, 'display.set_lock', locked=True)
        self.assertFalse(g.press(TR))
        self.assertEqual(g.cursor(), 'active_gas')
        self.assertFalse(g.press(BR, hold=0.5, held=[TL, TR, BL]))                    # held too briefly
        self.assertTrue(g.press(BR, hold=1.5, held=[TL, TR, BL]))
        self.assertEqual((g.state('display.locked'), g.cursor()), (False, 'active_gas'))

    def test_no_menu_time_out_and_alternating_unit_line(self):
        g = Rig()
        g.menu('setup')
        g.step(3600.0)
        self.assertEqual((g.screen(), g.cursor()), ('list', 'active_gas'))
        g.press(BC)
        g.press(BR)
        shown = set()
        for _ in range(4):
            shown.add(g.lcd()[2].strip())
            g.step(1.0)
        self.assertEqual(shown, {'SL', 'Air'})

    def test_rejected_and_unknown_gestures(self):
        g = Rig()
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='knob')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.press', expect='rejected', input_id=TL, held=[TL])
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = g.call(g.A, 'panel.turn', expect='rejected', input_id=TL, detents=1)
        self.assertEqual(r['error']['code'], 'UNKNOWN_CAPABILITY')
        caps = g.A.describe(instance_id=INST)['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.press', 'panel.capture'})
        self.assertIn('panel.frame', g.E.describe(instance_id=INST)['capabilities'])
        self.assertEqual([x['capability_id'] for x in g.A.logs()][-4:], ['panel.press', 'panel.press', 'panel.frame', 'panel.turn'])


# ---------------------------------------------------------------- rendering, capture, contract
class RenderAndContract(unittest.TestCase):
    def scenario(self):
        g = Rig()
        g.call(g.A, 'flow.set_setpoint', flow_rate_m3_s=0.75 / 60000.0)
        g.boundary(flowing=True)
        g.step(20.0)
        return g

    def test_render_determinism_and_pngs(self):
        a, b = self.scenario(), self.scenario()
        ia, ib = a.h.panel(INST).render('lcd'), b.h.panel(INST).render('lcd')
        self.assertEqual((ia.dtype.name, ia.shape), ('uint8', (100, 136, 3)))
        self.assertTrue((ia == ib).all())
        self.assertEqual(a.h.panel(INST).png_bytes('lcd'), b.h.panel(INST).png_bytes('lcd'))
        OUT.mkdir(parents=True, exist_ok=True)
        p = a.h.panel(INST)
        self.assertEqual(a.big(), '+0.7500')
        shots = {'home': p.save_png('lcd', OUT / 'home.png')}
        a.press(BR)
        shots['totalizer'] = p.save_png('lcd', OUT / 'totalizer.png')
        a.press(BR)
        shots['main_menu'] = p.save_png('lcd', OUT / 'main_menu.png')
        a.press(TL)
        shots['menu'] = p.save_png('lcd', OUT / 'menu.png')
        a.select('control_loop')
        a.select('deadband')
        shots['error'] = p.save_png('lcd', OUT / 'error.png')
        a.keys(BC, TR)
        shots['setpoint'] = p.save_png('lcd', OUT / 'setpoint.png')
        a.press(BL)
        a.menu('setup')
        a.keys(BR, TR, BR)
        shots['gas_list'] = p.save_png('lcd', OUT / 'gas_list.png')
        a.keys(BL, BC)
        a.call(a.A, 'display.set_lock', locked=True)
        shots['locked'] = p.save_png('lcd', OUT / 'locked.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        self.assertEqual((shots['home']['width'], shots['home']['height']), tuple(LCD['size']))
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(ready=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.call(g.A, 'power.set', enabled=True)
        r2 = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        self.assertEqual(g.call(g.A, 'panel.capture', display_id='lcd'), r2)          # same screen, same file

    def test_spec_and_contract(self):
        meta = rt.export_alicat_mc_contract_metadata()['asset_types'][rt.ALICAT_MC_TYPE]
        rt._amc_templates().augment_describe(meta)
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities'])), [])
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        original = ('power.set', 'gas.select', 'gas.set_reference_conditions', 'flow.set_setpoint', 'flow.zero', 'flow.reset_total', 'flow.read', 'control.set_pid')
        for k, v in {**meta['capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
            if k not in original:
                self.assertEqual(c['capabilities'][k]['component_ids'], sorted(v.get('component_ids') or c['capabilities'][k]['component_ids']), k)
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for sid in meta['state_slots']:
            self.assertIn(sid.lower(), {k.lower() for k in c['state_slots']})
        for inp in SPEC['inputs'].values():
            self.assertIn(inp['component_id'], c['components'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', pu.SPEC_VERSION))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):                    # shared files: registered unpinned
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('alicat_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
