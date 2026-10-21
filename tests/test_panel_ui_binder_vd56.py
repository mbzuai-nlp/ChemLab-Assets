"""front-panel tests for CHEM-012 binder_vd56 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_thermal.py):
python3 -m pytest tests/test_panel_ui_binder_vd56.py -q

Every gesture goes through the session API (panel.press / panel.switch, the runtime's normal invoke path).  Each check looks at the runtime
state and at the screen twice: the structured frame (panel.frame) and the rendered pixels, read back with a seven-segment reader and a 5x7
dot reader that know only the display geometry of the spec.  Themes: the seven key procedures of the menu survey plus the other RD4
functions of the manual (programs, chamber info, settings, passwords, Ethernet, recorder and USB, tolerance alarms, interior / heating
display, service reminder, power plug and start-up queries, cyclical rinsing, GAS/AIR 2, vacuum pump off); panel = direct capability (same
runtime state); the controller functions behind the panel; refusals and locks; time-outs; render determinism and the PNGs of
outputs/panel_ui/binder_vd56/; contract entries."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_thermal as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'binder_vd56_01'
C0 = 273.15
OUT = ROOT / 'outputs/panel_ui/binder_vd56'
ASSET = ROOT / 'assets/basic40_batch02/t1_thermal/binder_vd56'
SPEC = rt._vd56_panel()['spec']
SEG = SPEC['displays']['display']
LINE = SPEC['displays']['text_line']
SURVEY_TASKS = ['turn the oven on from standby', 'set temperature 60 C and pressure 50 mbar', 'set safety controller to Offset 5 C',
                'ventilate the chamber (pressure control off)', 'close all valves', 'start drying monitoring', 'acknowledge an alarm']
DONE_TASKS = set()
_DIGITS = {v: k for k, v in pu.SEG7.items() if k in '0123456789'}
_LETTERS = {v: k for k, v in pu.SEG7.items() if k not in '0123456789' and v}
_GLYPHS = {}
for _ch, _cols in pu.FONT5X7.items():
    _GLYPHS.setdefault(bytes(_cols), _ch)


# ---------------------------------------------------------------- reading the rendered displays
def _dark(img, x, y):
    px = img[int(round(y)), int(round(x))]
    return int(px[0]) + int(px[1]) + int(px[2]) < 250


def read_field(img, fid):
    """Seven-segment reader: the middle of each segment of each cell (geometry from the spec only) and the decimal points."""
    f = SEG['fields'][fid]
    n, cw, ch, gap = f['cells'], f['cell_w'], f['cell_h'], f.get('gap', 6)
    t = f.get('thickness', max(2.0, cw * 0.18))
    out = ''
    for i in range(n):
        x = f['x'] + i * (cw + gap)
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2),
                   g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if _dark(img, px, py))
        out += _DIGITS.get(segs, _LETTERS.get(segs, ' ' if not segs else '?'))
        r = max(1.5, t * 0.6)
        if _dark(img, x + cw + gap / 2.0, f['y'] + ch - r):
            out += '.'
    return out.strip()


def read_line(img):
    """5x7 dot reader of the text line: each cell's 35 dots against the font table."""
    cw, ch = LINE.get('cell', [12, 18])
    px, m = LINE.get('dot', 2), LINE.get('margin', 8)
    out = ''
    for c in range(LINE['cols']):
        x0, y0 = m + c * cw, m
        cols = bytes(sum(1 << gy for gy in range(8) if _dark(img, x0 + 1 + gx * px + px / 2 - 0.5, y0 + 1 + gy * px + px / 2 - 0.5)) for gx in range(5))
        out += _GLYPHS.get(cols, '?')
    return out.rstrip()


def _segments(ch):
    return pu.SEG7.get(ch, pu.SEG7.get(ch.upper(), pu.SEG7.get(ch.lower(), '')))


def norm(text):
    """What seven segments can tell apart: the lit segments of each cell (characters they cannot draw stay dark), and the points."""
    return tuple(ch if ch == '.' else _segments(ch) for ch in text if ch == '.' or _segments(ch))


# ---------------------------------------------------------------- rig
class Rig:
    def __init__(self, boot=True, scene=False, vacuum=None):
        self.scene = []
        self.h = rt.BinderVD56Runtime(scene_adapter=(lambda key, values: self.scene.append((key, values))) if scene else None)
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if vacuum:
            self.h.configure_external_vacuum(INST, connected=True, boundary_pressure_pa=vacuum, time_constant_s=120.0, evidence='synthetic test pump boundary')
        if boot:
            self.startup()

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0                                                     # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def press(self, key, hold=0.1, times=1):
        ok = True
        for _ in range(times):
            ok = self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted'] and ok
        return ok

    def hold(self, key):
        return self.press(key, 5.0)

    def switch(self, inp, pos):
        return self.call(self.A, 'panel.switch', input_id=inp, position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lcd(self):
        """(upper row, lower row, text line) shown now, from the frame and from the pixels (both must agree)."""
        f = self.frame()['displays']
        upper, lower = (f['display']['fields'][k]['shown'].strip() for k in ('upper', 'lower'))
        text = (f['text_line']['shown'] or [''])[0].rstrip()
        img, line = self.h.panel(INST).render('display'), self.h.panel(INST).render('text_line')
        if f['display']['brightness'] == 0:                                              # no mains: the whole LCD is dark
            assert not img.any() and not line.any()
            return upper, lower, text
        assert norm(upper) == norm(read_field(img, 'upper')), (upper, read_field(img, 'upper'))
        assert norm(lower) == norm(read_field(img, 'lower')), (lower, read_field(img, 'lower'))
        assert self._ascii(text) == read_line(line), (text, read_line(line))
        return upper, lower, text

    @staticmethod
    def _ascii(text):
        import unicodedata
        return ''.join(c if c in pu.FONT5X7 else unicodedata.normalize('NFKD', c)[:1] if unicodedata.normalize('NFKD', c)[:1] in pu.FONT5X7 else '?' for c in text)

    def text(self):
        return self.lcd()[2]

    def icons(self):
        return {k for k, v in self.frame()['displays']['display']['icons'].items() if v['on']}

    def editing(self):
        ed = self.frame()['ui']['editor']
        return ed and ed['field']

    def item(self):
        m = self.frame()['ui']['menu']
        return m and m['item']

    def step(self, s):
        while s > 1e-9:
            self.h.step(min(s, 3600.0))
            s -= min(s, 3600.0)

    def state(self, *ids):
        o = self.E.read(instance_id=INST, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def at_text(self, phase=0):
        """Move the clock to the next 2 s slot of the text line cycle with the given index (two-message cycles)."""
        while int(self.h.time_s // 2.0) % 2 != phase:
            self.step(1.0)

    def wait_text(self, text, limit_s=20.0):
        """Step the clock in 1 s until the text line shows the given message (it cycles every 2 s)."""
        t = 0.0
        while self.lcd()[2] != text:
            assert t < limit_s, (text, self.lcd())
            self.step(1.0)
            t += 1.0
        return text

    def startup(self):
        """The start-up queries (language, unit, date, time) confirmed with OK, as on every power-up with Language selection at restart ON."""
        assert self.screen() == 'startup'
        self.press('key_ok', times=8)
        assert self.screen() == 'normal'

    def menu(self, downs):
        """From Normal display: Down to a top menu and OK into it."""
        assert self.screen() == 'normal'
        self.press('key_down', times=downs)
        self.press('key_ok')

    def password(self, code):
        """Password entry: the left two digits with the arrows, OK, the right two digits, OK."""
        for digits in divmod(code, 100):
            if digits:
                self.press('key_up', times=digits)
            self.press('key_ok')


def runtime_state(h):
    s = h.debug_snapshot()
    return json.dumps(dict(state=s['instances'][INST]['state'], ctl={k: v for k, v in s['rd4_controller'][INST].items() if k != 'recorder'}), sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_turn_the_oven_on_from_standby(self):
        g = Rig(boot=False)
        self.assertEqual(g.screen(), 'startup')                                       # first power-up: language query (Language selection at restart ON)
        self.assertEqual(g.lcd()[0], 'ENG')
        g.startup()
        g.at_text(1)
        self.assertEqual(g.lcd(), ('22.0', '1013', 'Standby'))                        # standby: info message, function 1 icon, info icon
        self.assertTrue({'fn_box', 'fn1', 'info', 'unit_c'} <= g.icons())
        self.assertFalse(g.press('key_standby'))                                       # a short press does nothing
        self.assertTrue(g.hold('key_standby'))                                         # hold 5 s: the chamber turns on
        self.assertTrue(g.state('power.enabled'))
        self.assertNotIn('fn1', g.icons())
        g.step(60.0)
        self.assertIn('heating', g.icons())                                            # controls to the last entered values (31 C)
        self.assertEqual(g.lcd()[0], f"{g.state('thermal.indicated_temperature_k') - C0:.1f}")
        self.assertTrue(g.hold('key_standby'))                                         # another 5 s: standby again
        self.assertFalse(g.state('power.enabled'))
        DONE_TASKS.add('turn the oven on from standby')

    def test_set_temperature_60_and_pressure_50_mbar(self):
        g = Rig()
        g.hold('key_standby')
        g.menu(1)                                                                      # Normal display, Down: Setpoints, OK: Temperature
        self.assertEqual((g.item(), g.lcd()), ('temperature_c', ('31.0', '', 'Temperature')))
        g.press('key_ok')                                                              # the current setting flashes
        self.assertEqual(g.editing(), 'temperature_c')
        g.press('key_up', hold=29.4)                                                   # held: 1 + 289 steps of 0.1 K
        self.assertEqual(g.frame()['ui']['editor']['buffer']['temperature_c'], 60.0)
        g.step(0.2)
        self.assertEqual(g.lcd()[0], '60.0')
        g.press('key_ok')
        self.assertEqual(g.state('thermal.target_k'), 333.15)
        g.press('key_down')                                                            # Down: the pressure set point
        self.assertEqual((g.item(), g.lcd()[1:]), ('pressure', ('1000', 'Pressure (mbar)')))
        g.press('key_ok')
        g.press('key_down', hold=60.0)                                                 # held: 1 + 595 steps of 1 mbar, then 1 + 353
        g.press('key_down', hold=35.8)
        g.press('key_ok')
        self.assertEqual(g.state('pressure.target_pa'), 5000.0)
        g.press('key_back', times=2)                                                   # Back repeatedly: Normal display
        self.assertEqual(g.screen(), 'normal')
        DONE_TASKS.add('set temperature 60 C and pressure 50 mbar')

    def test_set_safety_controller_offset_5(self):
        g = Rig()
        g.call(g.A, 'safety.configure', mode='limit')
        g.menu(1)
        g.press('key_down', times=5)                                                   # Setpoints: ... Safety controller
        self.assertEqual(g.lcd()[2], 'Safety controller')
        g.press('key_ok')                                                              # Mode
        self.assertEqual(g.lcd()[:3:2], ('LIMI', 'Mode'))
        g.press('key_ok')
        g.press('key_up')                                                              # LIMI -> OFFS
        self.assertEqual(g.lcd()[0], 'OFFS')
        g.press('key_ok')
        self.assertEqual(g.state('safety.mode'), 'offset')
        g.press('key_down')                                                            # Offset (the Limit line is hidden in Offset mode)
        self.assertEqual((g.item(), g.lcd()[2]), ('safety_offset_c', 'Offset'))
        g.call(g.A, 'safety.configure', offset_K=3.0)
        g.press('key_ok')
        g.press('key_up', times=20)
        g.press('key_ok')
        self.assertEqual((g.state('safety.offset_k'), g.lcd()[0]), (5.0, '5.0'))
        DONE_TASKS.add('set safety controller to Offset 5 C')

    def test_ventilate_the_chamber(self):
        g = Rig(vacuum=500.0)
        g.hold('key_standby')
        g.call(g.A, 'pressure.set_setpoint', pressure_Pa=5000.0)
        g.press('key_ok')                                                              # Normal display, OK: quick access
        self.assertEqual((g.screen(), g.lcd()[2]), ('quick', 'Ventilate'))
        g.press('key_ok')
        self.assertEqual(g.lcd()[2], 'Yes')                                            # pressure control is on by default (p.64-65)
        g.press('key_ok')                                                              # Yes kept
        self.assertEqual((g.screen(), g.state('pressure.control_enabled')), ('normal', True))
        g.step(3600.0)
        self.assertEqual((g.state('pressure.pressure_pa'), g.lcd()[1]), (5000.0, '50'))
        g.press('key_ok')
        g.press('key_ok')
        g.press('key_down')
        g.press('key_ok')                                                              # No: pressure control off, ventilated
        self.assertEqual(g.state('pressure.control_enabled', 'vent.valve_open', 'vacuum.valve_open'),
                         {'pressure.control_enabled': False, 'vent.valve_open': True, 'vacuum.valve_open': False})
        g.at_text(1)
        self.assertEqual(g.lcd()[2], 'Pressure control off')                          # info message, the info icon flashes
        self.assertTrue(g.frame()['displays']['display']['icons']['info']['blink'])
        g.step(120.0)
        self.assertGreater(g.state('pressure.pressure_pa'), 99000.0)
        DONE_TASKS.add('ventilate the chamber (pressure control off)')

    def test_close_all_valves(self):
        g = Rig(vacuum=500.0)
        g.hold('key_standby')
        g.call(g.A, 'pressure.set_control', enabled=True)
        g.step(600.0)
        g.menu(1)
        g.press('key_down', times=3)
        self.assertEqual(g.lcd()[1:], ('0000', 'Functions on/off'))
        g.press('key_ok')
        g.press('key_down', times=2)                                                   # function 3
        self.assertEqual(g.lcd()[1:], ('0', 'Close all valves'))
        g.press('key_ok')
        g.press('key_up')
        g.press('key_ok')
        self.assertEqual(g.state('functions.valves_closed', 'vacuum.valve_open', 'vent.valve_open'),
                         {'functions.valves_closed': True, 'vacuum.valve_open': False, 'vent.valve_open': False})
        g.press('key_back')
        self.assertEqual(g.lcd()[1], '0010')
        self.assertTrue({'fn_box', 'fn3'} <= g.icons())
        p = g.state('pressure.pressure_pa')
        g.step(3600.0)
        self.assertAlmostEqual(g.state('pressure.pressure_pa') - p, 1000.0, places=6)   # isolated chamber: the leak only
        g.call(g.A, 'vacuum.set_valve', expect='rejected', opened=True)
        DONE_TASKS.add('close all valves')

    def test_start_drying_monitoring(self):
        g = Rig(vacuum=400.0)
        g.hold('key_standby')
        g.call(g.A, 'pressure.set_control', enabled=True)
        g.press('key_ok')
        g.press('key_down', times=2)                                                   # quick access: Drying monitoring
        self.assertEqual(g.lcd()[2], 'Drying monitoring')
        g.press('key_ok')
        g.press('key_up')
        self.assertEqual(g.lcd()[2], 'Start')
        g.press('key_ok')
        self.assertEqual((g.screen(), g.state('drying.active')), ('normal', True))
        t = 0.0
        while g.state('drying.active'):
            g.step(60.0)
            t += 60.0
            self.assertLess(t, 7200.0)
        self.assertEqual(g.state('info.pending', 'pressure.control_enabled', 'vent.valve_open'),
                         {'info.pending': ['drying_finished'], 'pressure.control_enabled': False, 'vent.valve_open': True})
        g.at_text(1)
        self.assertEqual(g.lcd()[2], 'Pressure control off')
        g.step(2.0)
        self.assertEqual(g.lcd()[2], 'Drying finished')
        self.assertTrue(g.press('key_ok'))                                             # OK confirms the message
        self.assertEqual((g.state('info.pending'), g.screen()), ([], 'normal'))
        DONE_TASKS.add('start drying monitoring')

    def test_acknowledge_an_alarm(self):
        g = Rig()
        g.hold('key_standby')
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 80.0)
        g.call(g.A, 'safety.configure', mode='limit', limit_K=C0 + 50.0)
        g.step(3600.0)
        self.assertEqual(g.state('alarm.latched', 'alarm.buzzer'), {'alarm.latched': ['safety_controller'], 'alarm.buzzer': True})
        icon = g.frame()['displays']['display']['icons']['alarm']
        self.assertEqual((icon['on'], icon['blink']), (True, True))                   # the collective alarm icon flashes
        self.assertNotIn('heating', g.icons())                                         # heating off
        g.at_text(1)
        self.assertEqual(g.lcd()[2], 'Safety controller')
        g.call(g.A, 'safety.configure', limit_K=C0 + 40.0)                             # the cause persists (chamber above the value)
        g.step(1.0)
        self.assertTrue(g.press('key_ok'))                                             # OK: buzzer muted, the icon stays lit
        self.assertEqual(g.state('alarm.latched', 'alarm.acknowledged', 'alarm.buzzer'),
                         {'alarm.latched': ['safety_controller'], 'alarm.acknowledged': ['safety_controller'], 'alarm.buzzer': False})
        icon = g.frame()['displays']['display']['icons']['alarm']
        self.assertEqual((icon['on'], icon['blink']), (True, False))
        g.call(g.A, 'safety.configure', mode='offset')                                 # cause gone: reset automatically, heating released
        g.step(60.0)
        self.assertEqual((g.state('alarm.latched'), g.state('thermal.heating_active')), ([], True))
        DONE_TASKS.add('acknowledge an alarm')


# ---------------------------------------------------------------- the other RD4 functions of the manual
class OtherPanelFunctions(unittest.TestCase):
    def test_time_program_start_and_stop(self):
        g = Rig()
        g.hold('key_standby')
        g.menu(2)
        g.press('key_ok')
        g.press('key_up')
        g.press('key_ok')                                                              # YES without a stored program: refused, Normal display
        self.assertEqual((g.screen(), g.state('program.running')), ('normal', 'none'))
        g.call(g.A, 'program.load', kind='time', sections=[{'duration_s': 600.0, 'temperature_K': C0 + 50.0, 'pressure_Pa': 100000.0},
                                                           {'duration_s': 600.0, 'temperature_K': C0 + 60.0, 'pressure_Pa': 100000.0, 'ramp': True}])
        g.menu(2)
        g.press('key_ok')
        self.assertEqual(g.lcd()[0::2], ('NO', 'Time program'))
        g.press('key_up')
        g.press('key_ok')
        self.assertEqual((g.screen(), g.lcd()[2], g.state('program.running')), ('program_started', 'Program started', 'time'))
        g.press('key_ok')
        self.assertEqual((g.screen(), g.wait_text('Time program active')), ('normal', 'Time program active'))
        g.step(900.0)
        self.assertAlmostEqual(g.state('thermal.target_k'), C0 + 50.0 + 10.0 * (g.state('program.elapsed_s') - 600.0) / 600.0, places=6)   # ramp
        g.menu(2)
        g.press('key_down', times=2)                                                   # Stop program: YES
        g.press('key_ok')
        g.press('key_up')
        g.press('key_ok')
        self.assertEqual((g.screen(), g.state('program.running')), ('normal', 'none'))

    def test_week_program(self):
        g = Rig()
        g.hold('key_standby')
        g.call(g.A, 'clock.set_date', day=5, month=10, year=2026)                      # a Monday
        g.call(g.A, 'clock.set_time', hour=7, minute=58)
        g.call(g.A, 'program.load', kind='week', points=[{'weekday': 0, 'time': '08:00', 'temperature_K': C0 + 70.0, 'pressure_Pa': 100000.0},
                                                         {'weekday': 0, 'time': '17:00', 'temperature_K': C0 + 40.0, 'pressure_Pa': 100000.0}])
        g.menu(2)
        g.press('key_down')
        g.press('key_ok')
        g.press('key_up')
        g.press('key_ok')
        g.press('key_ok')
        self.assertAlmostEqual(g.state('thermal.target_k'), C0 + 31.0, places=6)
        g.wait_text('Week program active', limit_s=4.0)
        g.step(56.0)
        self.assertAlmostEqual(g.state('thermal.target_k'), C0 + 40.0, places=6)     # before 08:00 the last point of the week holds
        g.step(120.0)
        self.assertAlmostEqual(g.state('thermal.target_k'), C0 + 70.0, places=6)

    def test_chamber_info_ethernet(self):
        g = Rig()
        g.menu(3)
        g.press('key_down')
        g.press('key_ok')                                                              # Ethernet: MAC address
        self.assertEqual(g.lcd()[2], 'MAC address')
        g.press('key_ok')                                                              # OK: the value, Back: the label again
        self.assertEqual(g.lcd()[2], '00:0c:d8:0a:28:00')
        g.press('key_back')
        self.assertEqual((g.screen(), g.lcd()[2]), ('menu', 'MAC address'))
        g.press('key_ok')
        g.press('key_down')                                                            # moving on shows the next label
        self.assertEqual(g.lcd()[2], 'IP address')
        g.press('key_down', times=4)
        g.press('key_ok')
        self.assertEqual(g.lcd()[2], 'BinderRD4-000cd80a2800'[:21])                  # 21 characters of the text line

    def test_settings_need_the_admin_password(self):
        g = Rig()
        g.menu(4)
        self.assertEqual((g.screen(), g.lcd()[1:], g.editing()), ('pw_settings', ('0000', 'Password'), 'pw_hi'))
        g.password(2)                                                                  # wrong
        self.assertEqual((g.screen(), g.lcd()[2]), ('pw_wrong_settings', 'Wrong password'))
        g.step(2.9)
        self.assertEqual(g.screen(), 'pw_wrong_settings')
        g.step(0.1)
        self.assertEqual(g.screen(), 'pw_settings')                                    # after 3 s: the entry again
        g.password(1)                                                                  # factory Admin password 00 01
        self.assertEqual((g.screen(), g.item()), ('menu', 'date'))

    def test_language_unit_date_time(self):
        g = Rig()
        g.menu(4)
        g.password(1)
        g.press('key_ok')                                                              # Date: day, month, year (2 + 2 digits)
        g.press('key_up', times=17)
        g.press('key_ok')
        g.press('key_up', times=9)
        g.press('key_ok', times=3)
        self.assertTrue(g.state('clock.datetime').startswith('2026-10-18'))
        self.assertEqual(g.lcd()[:2], ('18.10.', '2026'))
        g.press('key_down')
        g.press('key_ok')
        g.press('key_up', times=9)
        g.press('key_ok')
        g.press('key_up', times=30)
        g.press('key_ok')
        self.assertEqual(g.state('clock.datetime')[11:16], '09:30')
        self.assertEqual(g.lcd()[0], '09:30')
        g.press('key_down')                                                            # Language: ENG -> DEU
        g.press('key_ok')
        g.press('key_down')
        self.assertEqual(g.lcd()[0], 'DEU')
        g.press('key_ok')
        self.assertEqual((g.state('settings.language'), g.lcd()[2]), ('de', 'Sprache'))
        g.press('key_down')                                                            # Temperature unit: degF, all values converted
        g.press('key_ok')
        g.press('key_up')
        g.press('key_ok')
        self.assertEqual(g.state('settings.temperature_unit'), 'F')
        g.press('key_back', times=3)
        g.at_text(0)
        self.assertEqual(g.lcd(), ('71.6', '1013', 'T / Druck (mbar)'))
        self.assertIn('unit_f', g.icons())
        g.menu(1)                                                                      # Setpoints in degF
        self.assertEqual((g.item(), g.lcd()[0]), ('temperature_f', '87.8'))
        g.press('key_ok')
        g.press('key_up', times=18)
        g.press('key_ok')
        self.assertEqual(g.state('thermal.target_k'), C0 + 31.0)                       # refused in standby: the old value stays
        g.press('key_back', times=2)
        g.hold('key_standby')
        g.menu(1)
        g.press('key_ok')
        g.press('key_up', times=18)
        g.press('key_ok')
        self.assertAlmostEqual(g.state('thermal.target_k'), C0 + 32.0, places=4)     # 87.8 F + 1.8 F = 89.6 F = 32.0 C

    def test_address_brightness_audible_restart(self):
        g = Rig()
        g.menu(4)
        g.password(1)
        g.press('key_down', times=4)                                                   # Language sel. at restart: OFF
        g.press('key_ok')
        g.press('key_up')
        g.press('key_ok')
        self.assertFalse(g.state('settings.language_at_restart'))
        g.press('key_down')                                                            # Chamber address 1 -> 12
        g.press('key_ok')
        g.press('key_up', times=11)
        g.press('key_ok')
        self.assertEqual((g.state('settings.chamber_address'), g.lcd()[1]), (12, '12'))
        g.press('key_down')                                                            # Brightness 80 -> 50 %
        g.press('key_ok')
        g.press('key_down', times=3)
        g.press('key_ok')
        self.assertEqual(g.state('settings.brightness_percent'), 50.0)
        self.assertEqual(g.frame()['displays']['display']['brightness'], 50.0)
        bg = SEG['colors']['background']
        self.assertEqual(tuple(g.h.panel(INST).render('display')[2, 2]), tuple(int(c * 0.5) for c in bg))
        g.press('key_down')                                                            # Audible alarm OFF
        g.press('key_ok')
        g.press('key_up')
        g.press('key_ok')
        self.assertFalse(g.state('settings.audible_alarm'))
        g.press('key_back', times=3)
        self.assertTrue(g.switch('power_plug', 'out'))                                 # power-up without the start-up queries
        self.assertEqual(g.screen(), 'off')
        self.assertTrue(g.switch('power_plug', 'in'))
        self.assertEqual(g.screen(), 'normal')

    def test_user_password_locks_setpoints(self):
        g = Rig()
        g.menu(4)
        g.password(1)
        g.press('key_down', times=8)                                                   # Password User: 12 34
        self.assertEqual(g.lcd()[1:], ('0000', 'Password User'))
        g.press('key_ok')
        g.press('key_up', times=12)
        g.press('key_ok')
        g.press('key_up', times=34)
        g.press('key_ok')
        self.assertEqual(g.state('access.user_password'), 1234)
        g.press('key_back', times=3)
        g.menu(1)                                                                      # Setpoints now asks for it
        self.assertEqual(g.screen(), 'pw_setpoints')
        g.press('key_back')
        self.assertEqual((g.screen(), g.item()), ('menu', 'setpoints'))
        g.press('key_ok')
        g.password(1234)
        self.assertEqual((g.screen(), g.item()), ('menu', 'temperature_c'))

    def test_ethernet_manual_address(self):
        g = Rig()
        g.menu(4)
        g.password(1)
        g.press('key_back')
        g.press('key_down')
        g.press('key_ok')                                                              # Settings > Ethernet: IP address assignment AUTO -> MANU
        self.assertEqual(g.lcd()[0::2], ('AUTO', 'IP address assignment'))
        g.press('key_ok')
        g.press('key_up')
        g.press('key_ok')
        g.press('key_down')                                                            # IP address: sections 1/4 .. 4/4
        self.assertEqual(g.item(), 'ip_address')
        g.press('key_ok')
        self.assertEqual(g.lcd()[:2], ('1/4', '0'))
        g.press('key_up', hold=19.6)                                                   # 1 + 191 steps
        g.press('key_ok')
        g.press('key_up', hold=17.2)
        g.press('key_ok')
        g.press('key_ok')
        g.press('key_up', times=5)
        g.press('key_ok')
        self.assertEqual(g.state('network.ip_address'), '192.168.0.5')

    def test_recorder_interval_export_and_import(self):
        g = Rig()
        g.menu(4)
        g.password(1)
        g.press('key_back')
        g.press('key_down', times=2)
        g.press('key_ok')                                                              # Data recorder > Storage interval 1 -> 5 min
        g.press('key_ok')
        g.press('key_up', times=4)
        g.press('key_ok')
        self.assertEqual(g.state('recorder.interval_s'), 300.0)
        g.press('key_back', times=3)
        g.step(1800.0)
        self.assertTrue(g.switch('usb_port', 'stick'))                                 # the USB menu opens: Import configuration
        self.assertEqual((g.screen(), g.lcd()[0], g.lcd()[2]), ('usb', 'USB', 'Import configuration'))
        g.press('key_ok')                                                              # Admin only: password; no file on the stick: ERR
        g.password(1)
        self.assertEqual((g.screen(), g.lcd()[1]), ('usb', 'ERR'))
        g.step(3.0)
        g.press('key_down', times=2)
        g.press('key_ok')                                                              # Export recorder data
        files = g.call(g.A, 'usb.read_stick')['files']
        dl1 = files['DL1_000cd80a2800.csv']
        self.assertEqual(len(dl1), 6)                                                  # 30 min at 5 min
        self.assertEqual(len(files['DL2_000cd80a2800.csv']), 30)                       # fixed 1 min
        self.assertEqual((g.item(), dl1[0]['temperature_C']), ('import', 22.0))       # back to Import configuration
        g.press('key_down')
        g.press('key_ok')                                                              # Export configuration, then change a setting, then import it back
        g.call(g.A, 'settings.configure', chamber_address=99)
        g.press('key_up')
        g.press('key_ok')
        g.password(1)
        self.assertEqual(g.state('settings.chamber_address'), 1)
        files = g.call(g.A, 'usb.read_stick')['files']
        self.assertTrue(g.switch('usb_port', 'empty'))
        self.assertEqual(g.screen(), 'normal')
        g.switch('usb_port', 'stick')
        g.press('key_down', times=3)
        g.press('key_ok')
        g.switch('usb_port', 'empty')
        events = g.call(g.A, 'usb.read_stick')['files']['Service/EvList_000cd80a2800.csv']
        self.assertEqual([e['event'] for e in events], ['New config (USB)'])          # importing cleared the recorder (p.138)

    def test_tolerance_range_alarm(self):
        g = Rig(scene=True)
        g.hold('key_standby')
        g.call(g.A, 'pressure.set_control', enabled=False)                             # no vacuum connection: pressure control off, ventilated (p.64)
        g.menu(4)
        g.password(1)
        g.press('key_back')
        g.press('key_down', times=3)
        g.press('key_ok')                                                              # Various: Temp. alarm delay 10 -> 5 min
        g.press('key_ok')
        g.press('key_down', times=5)
        g.press('key_ok')
        g.press('key_down')                                                            # Temperature range 2.0 -> 1.0 C
        self.assertEqual(g.lcd()[1:], ('2.0', 'Temperature range(°C)'[:21]))
        g.press('key_ok')
        g.press('key_down', times=10)
        g.press('key_ok')
        self.assertEqual(g.state('tolerance.temperature_delay_s', 'tolerance.temperature_range_k'),
                         {'tolerance.temperature_delay_s': 300.0, 'tolerance.temperature_range_k': 1.0})
        g.press('key_back', times=3)
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 60.0)
        g.step(3 * 3600.0)
        g.call(g.A, 'scene.set_door', angle_rad=rt.BINDER_VD56_PARAMETERS['maximum_door_angle_rad']['value'])   # door wide open: the band is left
        g.step(240.0)
        self.assertEqual(g.state('alarm.latched'), [])
        g.step(600.0)
        self.assertEqual(g.state('alarm.latched'), ['temperature_range'])
        g.call(g.A, 'scene.set_door', angle_rad=0.0)
        g.step(3600.0)                                                                 # back in the band: still shown until OK
        self.assertEqual(g.state('alarm.latched', 'alarm.active'), {'alarm.latched': ['temperature_range'], 'alarm.active': []})
        g.press('key_ok')
        self.assertEqual(g.state('alarm.latched'), [])

    def test_interior_heating_display_and_service_reminder(self):
        g = Rig()
        g.hold('key_standby')
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 100.0)
        g.step(600.0)
        self.assertTrue(g.hold('key_back'))
        up, low, text = g.lcd()
        self.assertEqual((g.screen(), text), ('interior', 'Interior / heating'))
        self.assertAlmostEqual(float(low) - float(up), 100.0, delta=0.11)              # full heater power while heating up
        self.assertTrue(g.hold('key_back'))
        g.h._instances[INST].state['service.operating_s'] = 8640.0                    # trusted host: 0.1 day of operation
        self.assertTrue(g.hold('key_ok'))
        self.assertEqual(g.lcd()[2], 'Service in 364.9 days')
        g.press('key_ok')
        g.h._instances[INST].state['service.operating_s'] = 365 * 86400.0 - 30.0       # trusted host: jump close to one year of operation
        g.step(60.0)
        self.assertEqual(g.state('info.pending'), ['service_due'])
        g.wait_text('Service due!')
        g.press('key_ok')
        g.h._instances[INST].state['service.operating_s'] += 7 * 86400.0 - 60.0
        g.step(120.0)
        self.assertEqual(g.state('info.pending'), ['service_due'])                     # weekly again

    def test_power_failure_restart_queries(self):
        g = Rig()
        g.hold('key_standby')
        g.call(g.A, 'program.load', kind='time', sections=[{'duration_s': 3600.0, 'temperature_K': C0 + 50.0, 'pressure_Pa': 100000.0}])
        g.call(g.A, 'program.start', kind='time')
        g.call(g.A, 'messages.acknowledge')
        self.assertTrue(g.switch('power_plug', 'out'))
        self.assertEqual((g.screen(), g.lcd()), ('off', ('', '', '')))
        self.assertFalse(g.press('key_ok'))
        g.step(600.0)
        self.assertNotIn('heating', g.icons())
        g.switch('power_plug', 'in')
        self.assertEqual(g.screen(), 'startup')                                        # language, unit, date and time are checked
        g.startup()
        self.assertEqual(g.state('info.pending'), ['program_started'])                # the program continues (p.48)
        self.assertTrue(g.state('power.enabled'))

    def test_rinsing_gas_air2_and_pump_off(self):
        g = Rig(vacuum=500.0)
        g.hold('key_standby')
        g.call(g.A, 'pressure.set_setpoint', pressure_Pa=0.0)
        g.call(g.A, 'pressure.set_control', enabled=True)
        g.menu(1)
        g.press('key_down', times=2)
        self.assertEqual(g.lcd()[1:], ('0', 'Cyclical rinsing'))
        self.assertIn('percent', g.icons())
        g.press('key_ok')
        g.press('key_up', times=5)
        g.press('key_ok')
        g.step(3600.0)
        k, p_eq = 1 / 120.0 + 0.05 / 15.0, (500.0 / 120.0 + 101325.0 * 0.05 / 15.0) / (1 / 120.0 + 0.05 / 15.0)
        self.assertAlmostEqual(g.state('pressure.pressure_pa'), p_eq, delta=1e-6)     # dynamic balance of pump and aeration
        g.press('key_down')
        g.press('key_ok')
        g.press('key_down')                                                            # GAS/AIR 2 (not fitted): no aeration any more
        g.press('key_ok')
        g.press('key_up')
        g.press('key_ok')
        g.step(3600.0)
        self.assertAlmostEqual(g.state('pressure.pressure_pa'), 500.0, delta=1e-3)
        g.press('key_down', times=2)                                                   # Vacuum pump off: the leak raises the pressure
        g.press('key_ok')
        g.press('key_up')
        g.press('key_ok')
        self.assertTrue({'fn2', 'fn4'} <= g.icons())
        p = g.state('pressure.pressure_pa')
        g.step(3600.0)
        self.assertAlmostEqual(g.state('pressure.pressure_pa') - p, 1000.0, places=6)


# ---------------------------------------------------------------- panel and direct capability calls reach the same state
class Equivalence(unittest.TestCase):
    def _run(self, panel):
        g = Rig(vacuum=500.0)
        if panel:
            g.hold('key_standby')
            g.menu(1)
            g.press('key_ok')
            g.press('key_up', times=90)
            g.press('key_ok')
            g.press('key_down')
            g.press('key_ok')
            g.press('key_down', times=900)
            g.press('key_ok')
            g.press('key_back', times=2)
            g.press('key_ok')
            g.press('key_ok')
            g.press('key_ok')                                                          # Ventilate: Yes (on by default) confirmed
        else:
            g.call(g.A, 'power.set', enabled=True)
            g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 40.0)
            g.call(g.A, 'pressure.set_setpoint', pressure_Pa=10000.0)
            g.call(g.A, 'pressure.set_control', enabled=True)
        g.step(3600.0)
        return runtime_state(g.h)

    def test_setpoints_and_pressure_control(self):
        self.assertEqual(self._run(True), self._run(False))


# ---------------------------------------------------------------- the controller functions behind the panel
class DeviceFunctions(unittest.TestCase):
    def test_pressure_control_holds_and_vents_to_the_set_point(self):
        g = Rig(boot=False, vacuum=500.0)
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'pressure.set_setpoint', pressure_Pa=20000.0)
        g.call(g.A, 'pressure.set_control', enabled=True)
        g.call(g.A, 'vacuum.set_valve', expect='rejected', opened=True)               # the controller owns the valves
        g.step(600.0)
        self.assertEqual((g.state('pressure.pressure_pa'), g.state('vacuum.valve_open')), (20000.0, True))
        g.call(g.A, 'pressure.set_setpoint', pressure_Pa=60000.0)
        g.step(600.0)
        self.assertEqual(g.state('pressure.pressure_pa'), 60000.0)
        g.call(g.A, 'power.set', enabled=False)                                        # standby: all valves closed (p.49)
        self.assertEqual(g.state('vacuum.valve_open', 'vent.valve_open'), {'vacuum.valve_open': False, 'vent.valve_open': False})

    def test_safety_trip_is_exact_inside_a_step(self):
        g = Rig(boot=False)
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 80.0)
        g.call(g.A, 'safety.configure', mode='limit', limit_K=C0 + 45.0)
        trace = []
        for _ in range(60):
            g.step(77.0)
            trace.append((g.state('thermal.temperature_k'), g.state('alarm.latched')))
        peak = max(t for t, _ in trace)
        self.assertLess(peak - (C0 + 45.0), 1e-6)                                      # the heater stops at the value, no overshoot
        self.assertGreater(peak - (C0 + 45.0), -0.05)
        self.assertEqual(trace[-1][1], ['safety_controller'])

    def test_programs_need_on_state_and_standby_cancels(self):
        g = Rig(boot=False)
        g.call(g.A, 'program.load', kind='time', sections=[{'duration_s': 60.0, 'temperature_K': C0 + 40.0, 'pressure_Pa': 1000.0}])
        g.call(g.A, 'program.start', expect='rejected', kind='time')
        g.call(g.A, 'program.start', expect='rejected', kind='week')
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'program.start', kind='time')
        g.call(g.A, 'power.set', enabled=False)
        self.assertEqual(g.state('program.running'), 'none')
        g.call(g.A, 'program.load', expect='rejected', kind='week', points=[{'weekday': 0, 'time': '25:00', 'temperature_K': C0 + 40.0, 'pressure_Pa': 0.0}])
        g.call(g.A, 'program.load', expect='rejected', kind='time', sections=[{'duration_s': 60.0, 'temperature_K': C0 + 20.0, 'pressure_Pa': 0.0}])

    def test_drying_cancel_takes_the_actual_pressure_and_mains_stops_everything(self):
        g = Rig(boot=False, vacuum=400.0)
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'pressure.set_control', enabled=False)
        g.call(g.A, 'drying.set_monitoring', expect='rejected', running=True)          # pressure control must be on (p.66)
        g.call(g.A, 'pressure.set_control', enabled=True)
        g.call(g.A, 'drying.set_monitoring', running=True)
        g.step(120.0)
        p = g.state('pressure.pressure_pa')
        g.call(g.A, 'drying.set_monitoring', running=False)
        self.assertEqual(g.state('pressure.target_pa'), p)
        g.call(g.A, 'power.set_mains', connected=False)
        for cap, args in (('power.set', {'enabled': True}), ('thermal.set_temperature', {'temperature_K': C0 + 40.0}), ('settings.configure', {'language': 'de'})):
            g.call(g.A, cap, expect='rejected', **args)

    def test_settings_validation_and_password_privacy(self):
        g = Rig(boot=False)
        for cap, args in (('settings.configure', {}), ('settings.configure', {'recorder_interval_s': 90.0}), ('settings.configure', {'language': 'nl'}),
                          ('clock.set_date', {'day': 31, 'month': 2, 'year': 2026}), ('network.configure', {'ip_address': [192, 168, 0.5, 1]}),
                          ('access.set_password', {'level': 'service', 'code': 1})):
            before = runtime_state(g.h)
            g.call(g.A, cap, expect='rejected', **args)
            self.assertEqual(before, runtime_state(g.h))
        r = g.call(g.A, 'access.set_password', level='admin', code=4321)
        self.assertEqual(r, {'level': 'admin'})
        self.assertNotIn('access.admin_password', g.A.read(instance_id=INST)['observations'])
        self.assertEqual(g.state('access.admin_password'), 4321)


class FaultInjection(unittest.TestCase):
    """2026-10-19 sync fix: the documented faults (p.48, p.92, p.101, p.155-156) by the evaluator-only fault.inject / fault.clear; the RD4 shows
    them and reacts as documented."""

    def test_thermal_switch_overtemperature_and_the_restart_procedure(self):
        g = Rig()
        g.hold('key_standby')
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 80.0)
        g.step(600.0)
        self.assertTrue(g.state('thermal.heating_active'))
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='overtemperature')['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('fault.inject', g.A.describe(instance_id=INST)['capabilities'])
        self.assertIn('fault.inject', g.E.describe(instance_id=INST)['capabilities'])
        g.call(g.E, 'fault.inject', code='overtemperature')
        g.step(60.0)
        self.assertEqual(g.state('alarm.latched', 'alarm.buzzer'), {'alarm.latched': ['overtemperature'], 'alarm.buzzer': True})
        self.assertFalse(g.state('thermal.heating_active'))                           # the thermal switch turns the heating off (p.92)
        icon = g.frame()['displays']['display']['icons']['alarm']
        self.assertEqual((icon['on'], icon['blink']), (True, True))
        g.wait_text('Overtemperature')
        t0 = g.state('thermal.temperature_k')
        g.step(600.0)
        self.assertLess(g.state('thermal.temperature_k'), t0)
        self.assertTrue(g.press('key_ok'))                                             # confirm: buzzer muted, the alarm stays (switch tripped)
        self.assertEqual(g.state('alarm.latched', 'alarm.acknowledged', 'alarm.buzzer'),
                         {'alarm.latched': ['overtemperature'], 'alarm.acknowledged': ['overtemperature'], 'alarm.buzzer': False})
        g.switch('power_plug', 'out')                                                  # a short interruption does not reset the switch
        g.step(5.0)
        g.switch('power_plug', 'in')
        g.startup()
        g.step(60.0)
        self.assertEqual(g.state('fault.injected'), ['overtemperature'])
        self.assertFalse(g.state('thermal.heating_active'))
        g.press('key_ok')
        g.switch('power_plug', 'out')                                                  # p.48: at least 10 s without mains, then restart
        g.step(15.0)
        g.switch('power_plug', 'in')
        g.startup()
        g.step(60.0)                                                                   # below 230 C: the heating is released (p.92)
        self.assertEqual((g.state('fault.injected'), g.state('alarm.latched')), ([], []))
        self.assertTrue(g.state('thermal.heating_active'))

    def test_temperature_and_pressure_sensor_defects(self):
        g = Rig(vacuum=500.0)
        g.hold('key_standby')
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 60.0)
        g.call(g.A, 'pressure.set_setpoint', pressure_Pa=20000.0)
        g.step(1200.0)
        g.call(g.E, 'fault.inject', code='temperature_sensor')
        self.assertIsNone(g.state('thermal.indicated_temperature_k'))
        obs = g.A.read(instance_id=INST, state_ids=['thermal.indicated_temperature_k'])['observations']['thermal.indicated_temperature_k']
        self.assertEqual((obs['value'], obs['quality']), (None, 'unavailable'))       # the direct reading has no value either
        g.step(60.0)
        upper, lower, _ = g.lcd()
        self.assertEqual((upper, lower), ('----', '200'))                              # - - - - in the temperature row (p.101)
        self.assertIn('temperature_sensor', g.state('alarm.latched'))
        self.assertFalse(g.state('thermal.heating_active'))                           # p.155: the chamber does not heat up
        texts = set()
        for _ in range(8):
            g.step(1.0)
            texts.add(g.lcd()[2])
        self.assertEqual(texts, {'T / pressure (mbar)'})                               # no text message for a sensor fault
        g.call(g.E, 'fault.inject', code='pressure_sensor')
        g.step(60.0)
        self.assertEqual(g.lcd()[:2], ('----', '----'))
        self.assertEqual(g.state('vacuum.valve_open', 'vent.valve_open'), {'vacuum.valve_open': False, 'vent.valve_open': False})
        self.assertEqual(g.call(g.E, 'fault.clear', expect='rejected', code='overtemperature')['error']['code'], 'INVALID_ARGUMENT')
        g.call(g.E, 'fault.clear', code='temperature_sensor')                          # the service replaced the Pt100
        g.call(g.E, 'fault.clear', code='pressure_sensor')
        g.call(g.E, 'fault.clear', expect='rejected', code='pressure_sensor')
        g.step(60.0)
        self.assertNotEqual(g.lcd()[0], '----')
        g.press('key_ok')
        g.step(60.0)
        self.assertEqual(g.state('alarm.latched'), [])
        self.assertEqual(g.state('fault.injected'), [])

    def test_another_callers_run_cannot_be_stopped_or_switched_off(self):
        """Lead rule 2026-10-19: B cannot stop, change or switch off A's program (panel or direct); A stops its own; the evaluator is unrestricted."""
        g = Rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        g.hold('key_standby')
        g.call(g.A, 'program.load', kind='time', sections=[{'duration_s': 3600.0, 'temperature_K': C0 + 50.0, 'pressure_Pa': 100000.0}])
        g.call(g.A, 'program.start', kind='time')
        before = runtime_state(g.h)
        for cap, args in (('program.stop', {}), ('power.set', {'enabled': False}), ('thermal.set_temperature', {'temperature_K': C0 + 60.0}),
                          ('pressure.set_setpoint', {'pressure_Pa': 5000.0}), ('thermal.set_control', {'enabled': False}),
                          ('rinsing.set_percent', {'percent': 50.0}), ('functions.configure', {'close_all_valves': True}),
                          ('program.start', {'kind': 'time'})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertFalse(g.call(B, 'panel.press', input_id='key_standby', duration_s=5.0)['accepted'])   # B's Standby key: refused
        self.assertEqual(g.frame()['displays']['text_line']['shown'][0].strip(), 'Access denied')   # shown on the text line (inverse message)
        self.assertEqual(runtime_state(g.h), before)
        g.call(B, 'settings.configure', brightness_percent=50.0)                      # device settings are not part of the run
        g.call(g.A, 'program.stop')                                                    # A stops its own program
        self.assertEqual(g.state('program.running'), 'none')
        g.call(B, 'program.start', kind='time')
        g.call(g.E, 'program.stop')                                                    # the evaluator is unrestricted
        self.assertEqual(g.state('program.running'), 'none')

    def test_pulling_the_plug_is_physical_and_not_refused(self):
        """Sweep 2026-10-19: unplugging is a physical action; the chamber reacts as on a power failure (p.48) even during another caller's run."""
        g = Rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        g.hold('key_standby')
        g.call(g.A, 'program.load', kind='time', sections=[{'duration_s': 3600.0, 'temperature_K': C0 + 50.0, 'pressure_Pa': 100000.0}])
        g.call(g.A, 'program.start', kind='time')
        g.call(B, 'power.set_mains', connected=False)                                  # B pulls the plug: accepted
        self.assertFalse(g.state('power.mains_connected'))
        self.assertEqual(g.frame()['displays']['text_line']['shown'][0].strip(), '')  # no mains: dark display, no refusal message
        g.step(60.0)
        g.call(B, 'power.set_mains', connected=True)
        self.assertEqual(g.state('program.running'), 'time')                           # p.48: the time program goes on where it would be
        g.call(g.A, 'program.stop')                                                    # still A's run
        self.assertEqual(g.state('program.running'), 'none')

    def test_panel_gesture_acts_as_the_calling_session(self):
        """2026-10-19 sync fix: the commands behind a gesture run as the session that made it, not as a fixed panel principal."""
        from unittest import mock
        g = Rig()
        callers, real = [], rt.BinderVD56Session._invoke_checked

        def spy(sess, *a, **k):
            callers.append((sess, a[1]))
            return real(sess, *a, **k)
        with mock.patch.object(rt.BinderVD56Session, '_invoke_checked', autospec=True, side_effect=spy):
            g.hold('key_standby')
        self.assertTrue(g.state('power.enabled'))
        self.assertIn('power.set', [c for _, c in callers])
        self.assertTrue(all(s is g.A for s, c in callers if c != 'panel.frame'))
        self.assertIsNone(g.h._panel_caller)


# ---------------------------------------------------------------- refusals, locks, time-outs
class Rules(unittest.TestCase):
    def test_power_gate_and_mechanical_inputs(self):
        g = Rig()
        g.switch('power_plug', 'out')
        for key in ('key_up', 'key_down', 'key_ok', 'key_back', 'key_standby'):
            self.assertFalse(g.press(key))
        self.assertTrue(g.switch('usb_port', 'stick'))                                 # the stick goes in even without mains
        self.assertFalse(g.switch('power_plug', 'out'))                               # already out
        self.assertEqual(g.screen(), 'off')

    def test_standby_setpoint_refusal_and_quick_access_back(self):
        g = Rig()
        g.menu(1)
        g.press('key_ok')
        g.press('key_up', times=5)
        self.assertFalse(g.press('key_ok'))                                            # refused in standby (the asset's precondition)
        self.assertEqual((g.editing(), g.lcd()[0], g.state('thermal.target_k')), (None, '31.0', C0 + 31.0))
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'PRECONDITION_FAILED')
        g.press('key_back', times=2)
        g.press('key_ok')
        g.press('key_down', times=2)
        g.press('key_ok')
        g.press('key_up')
        self.assertFalse(g.press('key_ok'))                                            # drying without pressure control: refused
        self.assertEqual((g.screen(), g.state('drying.active')), ('normal', False))
        g.press('key_ok')
        g.press('key_back')
        self.assertEqual(g.screen(), 'normal')

    def test_text_line_cycle_and_menu_limits(self):
        g = Rig()
        g.at_text(0)
        seen = []
        for _ in range(3):
            seen.append(g.lcd()[2])
            g.step(2.0)
        self.assertEqual(seen, ['T / pressure (mbar)', 'Standby', 'T / pressure (mbar)'])   # 2 s each; pressure control on by default (p.64-65)
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'pressure.set_control', enabled=False)
        g.at_text(0)
        seen = []
        for _ in range(3):
            seen.append(g.lcd()[2])
            g.step(2.0)
        self.assertEqual(seen, ['T / pressure (mbar)', 'Pressure control off', 'T / pressure (mbar)'])
        g.call(g.A, 'power.set', enabled=False)
        g.press('key_up')                                                              # Up from Normal display: the last menu
        self.assertEqual(g.item(), 'service')
        g.press('key_down', times=3)
        self.assertEqual(g.item(), 'service')                                          # lists do not wrap
        g.press('key_ok')
        g.password(0)
        self.assertEqual(g.screen(), 'pw_wrong_service')                               # the service password is not published

    def test_rejected_and_unknown_gestures(self):
        g = Rig()
        r = g.call(g.A, 'panel.turn', expect='rejected', input_id='key_ok', detents=1)
        self.assertEqual(r['error']['code'], 'UNKNOWN_CAPABILITY')                     # no knob on this panel
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='usb_port')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        caps = g.A.describe(instance_id=INST)['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.press', 'panel.switch', 'panel.capture'})
        self.assertIn('panel.frame', g.E.describe(instance_id=INST)['capabilities'])


# ---------------------------------------------------------------- rendering, capture, contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.hold('key_standby')
            g.step(1201.0)
            return g
        a, b = scenario(), scenario()
        for d in SPEC['displays']:
            ia, ib = a.h.panel(INST).render(d), b.h.panel(INST).render(d)
            self.assertEqual((ia.dtype.name, ia.shape[2]), ('uint8', 3))
            self.assertTrue((ia == ib).all())
            self.assertTrue((ia == a.h.panel(INST).render(d)).all())
        self.assertEqual(a.h.panel(INST).png_bytes('display'), b.h.panel(INST).png_bytes('display'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {}
        a.at_text(0)
        shots['home'] = a.h.panel(INST).save_png('display', OUT / 'home.png')
        shots['home_text'] = a.h.panel(INST).save_png('text_line', OUT / 'home_text.png')
        a.menu(1)
        a.press('key_ok')
        a.press('key_up', times=5)
        shots['menu'] = a.h.panel(INST).save_png('display', OUT / 'menu.png')
        shots['menu_text'] = a.h.panel(INST).save_png('text_line', OUT / 'menu_text.png')
        a.press('key_ok')
        a.press('key_back', times=2)
        a.call(a.A, 'safety.configure', mode='limit', limit_K=C0 + 30.0)
        a.step(60.0)
        a.at_text(1)
        self.assertEqual(a.lcd()[2], 'Safety controller')
        shots['error'] = a.h.panel(INST).save_png('display', OUT / 'error.png')
        shots['error_text'] = a.h.panel(INST).save_png('text_line', OUT / 'error_text.png')
        a.call(a.E, 'fault.inject', code='overtemperature')                            # documented fault screens (evaluator injection)
        a.call(a.E, 'fault.inject', code='temperature_sensor')
        a.step(60.0)
        a.wait_text('Overtemperature')
        self.assertEqual(a.lcd()[0], '----')
        shots['fault'] = a.h.panel(INST).save_png('display', OUT / 'fault.png')
        shots['fault_text'] = a.h.panel(INST).save_png('text_line', OUT / 'fault_text.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        self.assertEqual((shots['home']['width'], shots['home']['height']), tuple(SEG['size']))
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(boot=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='display')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.press('key_up')
        r2 = g.call(g.A, 'panel.capture', display_id='display')
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        self.assertEqual(g.call(g.A, 'panel.capture', display_id='display'), r2)       # same screen, same file

    def test_spec_and_contract(self):
        meta = rt.export_binder_vd56_metadata()['asset_types'][rt.BINDER_VD56_ASSET_ID]
        callable_caps = set(meta['capabilities']) | set(meta['controller_capabilities'])
        self.assertEqual(pu.check_spec(SPEC, callable_caps), [])
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.switch', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['controller_capabilities'], **meta['evaluator_capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for k, v in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'state_effects', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for k, cap in c['capabilities'].items():
            for comp in cap['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', pu.SPEC_VERSION))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):                   # shared files: registered unpinned
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('vd56_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
