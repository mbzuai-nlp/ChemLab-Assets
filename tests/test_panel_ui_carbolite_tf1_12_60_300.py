"""front-panel tests for CHEM-186 carbolite_tf1 (Carbolite 3016 controller; panel_ui.json + scripts/asset_runtime/_panel_ui.py on
scripts/basic40_runtime_thermal.py): python3 -m pytest tests/test_panel_ui_carbolite_tf1_12_60_300.py -q

Every gesture goes through the session API (panel.press / panel.switch).  Each check looks at the runtime state and at the screen twice: the
structured frame (panel.frame) and the rendered pixels, read back with a seven-segment reader (PV line) and a 5x7 dot reader (second line)
that know only the display geometry of the spec.  Themes: the seven key procedures of the menu survey; the other functions of the 3016 lists
(Level 3 and CONF parameters, units, home display, keylock, demo mode, output limit and loop break, recovery after a power cycle,
communications, program advance and cycles, scrolling-text language); panel = direct capability; the controller functions behind the panel
(program segments and holdback, PV offset with the evaluator error model, autotune time-out); refusals, locks and passcode lockout;
time-outs; render determinism and the PNGs of outputs/panel_ui/carbolite_tf1_12_60_300/; contract entries."""
import hashlib
import importlib.util
import json
import sys
import unittest
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_thermal as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'carbolite_tf1_01'
C0 = 273.15
OUT = ROOT / 'outputs/panel_ui/carbolite_tf1_12_60_300'
ASSET = ROOT / 'assets/basic40_batch02/t1_thermal/carbolite_tf1'
SPEC = rt._tf1_panel()['spec']
SEG = SPEC['displays']['display']
LINE = SPEC['displays']['line2']
SURVEY_TASKS = ['set a setpoint and heat', 'log in at Operator Level 2', 'run / hold / reset the program', 'create a program (example)',
                'acknowledge an alarm', 'offset calibration', 'autotune']
DONE_TASKS = set()
_DIGITS = {v: k for k, v in pu.SEG7.items() if k in '0123456789'}
_LETTERS = {v: k for k, v in pu.SEG7.items() if k not in '0123456789' and v}
_GLYPHS = {}
for _ch, _cols in pu.FONT5X7.items():
    _GLYPHS.setdefault(bytes(_cols), _ch)


# ---------------------------------------------------------------- reading the rendered displays (bright segments on a dark glass)
def _lit(img, x, y):
    px = img[int(round(y)), int(round(x))]
    return int(px[1]) > 150


def read_pv(img):
    f = SEG['fields']['pv']
    n, cw, ch, gap = f['cells'], f['cell_w'], f['cell_h'], f.get('gap', 6)
    t = f.get('thickness', max(2.0, cw * 0.18))
    out = ''
    for i in range(n):
        x = f['x'] + i * (cw + gap)
        xl, xr, yt, ym, yb = x + t / 2, x + cw - t / 2, f['y'] + t / 2, f['y'] + ch / 2, f['y'] + ch - t / 2
        pts = dict(a=(x + cw / 2, yt), b=(xr, (yt + ym) / 2), c=(xr, (ym + yb) / 2), d=(x + cw / 2, yb), e=(xl, (ym + yb) / 2), f=(xl, (yt + ym) / 2),
                   g=(x + cw / 2, ym))
        segs = ''.join(s for s, (px, py) in pts.items() if _lit(img, px, py))
        out += _DIGITS.get(segs, _LETTERS.get(segs, ' ' if not segs else '?'))
        r = max(1.5, t * 0.6)
        if _lit(img, x + cw + gap / 2.0, f['y'] + ch - r):
            out += '.'
    return out.strip()


def read_line(img, inverse=False):
    """5x7 dot reader of the second line (a message line is drawn inverse: dark dots on a lit cell)."""
    cw, ch = LINE.get('cell', [12, 18])
    px, m = LINE.get('dot', 2), LINE.get('margin', 8)
    out = ''
    for c in range(LINE['cols']):
        x0, y0 = m + c * cw, m
        cols = bytes(sum(1 << gy for gy in range(8) if _lit(img, x0 + 1 + gx * px + px / 2 - 0.5, y0 + 1 + gy * px + px / 2 - 0.5) != inverse) for gx in range(5))
        out += _GLYPHS.get(cols, '?')
    return out.rstrip()


def _segments(ch):
    return pu.SEG7.get(ch, pu.SEG7.get(ch.upper(), pu.SEG7.get(ch.lower(), '')))


def norm(text):
    return tuple(ch if ch == '.' else _segments(ch) for ch in text if ch == '.' or _segments(ch))


def _ascii(text):
    return ''.join(c if c in pu.FONT5X7 else unicodedata.normalize('NFKD', c)[:1] or '?' for c in text)


# ---------------------------------------------------------------- rig
class Rig:
    def __init__(self, on=True, scene=False):
        self.h = rt.CarboliteTF1Runtime(scene_adapter=(lambda key, values: None) if scene else None)
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if on:
            self.switch_on()

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if expect == 'succeeded' else r

    def press(self, key, hold=0.1, held=(), times=1):
        ok = True
        for _ in range(times):
            args = dict(input_id=key, duration_s=hold)
            if held:
                args['held'] = list(held)
            ok = self.call(self.A, 'panel.press', **args)['accepted'] and ok
        return ok

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='instrument_switch', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lcd(self):
        """(PV line, second line) shown now, from the frame and from the pixels (both must agree)."""
        f = self.frame()['displays']
        pv = f['display']['fields']['pv']['shown'].strip()
        line = (f['line2']['shown'] or [''])[0].rstrip()
        img, img2 = self.h.panel(INST).render('display'), self.h.panel(INST).render('line2')
        if f['display']['brightness'] == 0:
            assert not img.any() and not img2.any()
            return pv, line
        assert norm(pv) == norm(read_pv(img)), (pv, read_pv(img))
        assert _ascii(line) == read_line(img2, inverse=bool(f['line2']['inverse'] and f['line2']['inverse'][0])), (line, read_line(img2))
        return pv, line

    def icons(self):
        return {k for k, v in self.frame()['displays']['display']['icons'].items() if v['on']}

    def level(self):
        return self.frame()['ui']['vars']['level']

    def step(self, s, dt=3600.0):
        while s > 1e-9:
            self.h.step(min(s, dt))
            s -= min(s, dt)
            self.frame()

    def state(self, sid, path=None):
        v = self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']
        return v[path] if path else v

    def switch_on(self):
        assert self.switch('on')
        assert self.screen() == 'boot'
        for _ in range(7):
            self.step(1.0)
        assert self.screen() == 'home'

    def login(self, level):
        """Hold Page 3 s: GOTO; Up to the level, Scroll; CODE: the digits with Up / Scroll; wait 2 s: PASS."""
        code = {2: 9, 3: 5, 4: 95}[level]
        assert self.press('key_page', hold=3.0)
        self.press('key_up', times=level - self.level())
        self.press('key_scroll')
        for k, digit in enumerate(f'{code:04d}'):
            self.press('key_up', times=int(digit))
            if k < 3:
                self.press('key_scroll')
        self.step(2.0, dt=1.0)
        assert self.screen() == 'pass', self.screen()
        self.step(2.0, dt=1.0)

    def to(self, screen, key='key_scroll', limit=60):
        for _ in range(limit):
            if self.screen() == screen:
                return
            self.press(key)
        raise AssertionError((screen, self.screen()))


def runtime_state(h):
    s = h.debug_snapshot()['instances'][INST]['state']
    return json.dumps({k: v for k, v in s.items() if k not in ('controller.output_percent',)}, sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_set_a_setpoint_and_heat(self):
        g = Rig(on=False)
        self.assertEqual((g.screen(), g.lcd()), ('off', ('', '')))
        self.assertFalse(g.press('key_up'))                                            # switched off: keys do nothing
        self.assertTrue(g.switch('on'))                                                # short test cycle, home after 7 s
        self.assertEqual((g.screen(), g.lcd()), ('boot', ('8.8.8.8.', '8888888')))
        g.step(6.9, dt=0.1)
        self.assertEqual(g.screen(), 'boot')
        g.step(0.1, dt=0.1)
        self.assertEqual((g.screen(), g.lcd()), ('home', ('20', '20')))              # PV and set point (despatched at the floor)
        self.assertTrue(g.press('key_up', hold=48.4))                                 # held: 1 + 479 steps of 1 C
        self.assertEqual((g.lcd()[1], g.state('thermal.target_k')), ('500', C0 + 500.0))
        self.assertIn('unit_c', g.icons())
        g.step(60.0)
        self.assertIn('output1', g.icons())                                            # output indicator: power to the elements
        self.assertEqual(g.state('controller.output_percent'), 100.0)
        g.step(3600.0)
        self.assertEqual(g.lcd(), ('500', '500'))
        g.press('key_down', hold=48.4)                                                 # back to the floor: heating stops
        g.step(60.0)
        self.assertNotIn('output1', g.icons())
        self.assertTrue(g.switch('off'))
        self.assertEqual(g.screen(), 'off')
        DONE_TASKS.add('set a setpoint and heat')

    def test_log_in_at_operator_level_2(self):
        g = Rig()
        self.assertTrue(g.press('key_page', hold=3.0))
        self.assertEqual((g.screen(), g.lcd()), ('goto', ('LEv1', 'GOTO')))
        g.press('key_up')
        self.assertEqual(g.lcd()[0], 'LEv2')
        g.press('key_scroll')
        self.assertEqual(g.lcd(), ('0___', 'CODE'))
        for k, digit in enumerate((0, 0, 0, 9)):
            g.press('key_up', times=digit)
            if k < 3:
                g.press('key_scroll')
        self.assertEqual(g.lcd()[0], '___9')                                           # entered digits are obscured
        g.step(1.9, dt=0.1)
        self.assertEqual(g.screen(), 'code')
        g.step(0.1, dt=0.1)                                                            # 2 s without a key: PASS
        self.assertEqual((g.screen(), g.lcd(), g.level()), ('pass', ('LEv2', 'PASS'), 2))
        g.step(2.0, dt=1.0)
        g.press('key_scroll', times=4)                                                 # Level 2 home list adds LANG
        self.assertEqual(g.lcd(), ('En', 'LANG'))
        g.switch('off')
        g.switch_on()
        self.assertEqual(g.level(), 1)                                                 # a power cycle returns to Level 1
        DONE_TASKS.add('log in at Operator Level 2')

    def test_run_hold_reset_the_program(self):
        g = Rig()
        g.call(g.A, 'program.set_segment', number=1, type='rate', target_K=C0 + 400.0, rate_per_unit_K=5.0)
        g.press('key_page')                                                            # PROG LIST, Scroll to P.MODE
        self.assertEqual(g.lcd(), ('PrOG', 'LIST'))
        g.press('key_scroll', times=3)
        self.assertEqual(g.lcd(), ('rSEt', 'P.MODE'))
        g.press('key_up')                                                              # rESET -> RUN: the program starts
        self.assertEqual((g.lcd()[0], g.state('program.status', 'mode')), ('run', 'run'))
        g.press('key_up')                                                              # RUN -> HOLd
        self.assertEqual(g.state('program.status', 'mode'), 'hold')
        g.step(600.0)
        self.assertAlmostEqual(g.state('program.status', 'working_setpoint_K'), C0 + 20.0, places=6)   # held: the set point stays
        self.assertEqual(g.screen(), 'home')                                           # 30 s without a key: back home
        g.press('key_page')
        g.to('prog__p_mode')
        g.press('key_down')
        self.assertEqual(g.state('program.status', 'mode'), 'run')
        g.press('key_page', times=2)
        self.assertEqual(g.screen(), 'home')
        g.step(600.0)
        self.assertEqual(g.lcd()[0], '70')                                             # 20 C + 5 C/min x 10 min
        self.assertTrue({'seg_up', 'output1'} <= g.icons())
        self.assertTrue(g.press('key_up', held=['key_down']))                         # shortcut: Up + Down holds
        self.assertEqual(g.state('program.status', 'mode'), 'hold')
        g.press('key_down', held=['key_up'])                                           # again: runs
        self.assertEqual(g.state('program.status', 'mode'), 'run')
        self.assertTrue(g.press('key_up', held=['key_down'], hold=3.0))               # held 3 s: reset
        self.assertEqual((g.state('program.status', 'mode'), g.state('thermal.target_k'), g.screen()), ('reset', C0 + 20.0, 'home'))
        DONE_TASKS.add('run / hold / reset the program')

    def test_create_a_program_example(self):
        g = Rig()
        g.login(2)
        g.press('key_page', times=2)                                                   # P.Set
        self.assertEqual(g.lcd()[0], 'P.SEt')
        g.to('pset__hb_typ')
        self.assertEqual(g.lcd(), ('OFF', 'HB.TYP'))
        g.to('pset__ramp_u')
        self.assertEqual(g.lcd()[0], 'P.mIN')
        g.to('pset__dwel_u')
        g.to('pset__p_end')
        self.assertEqual(g.lcd()[0], 'rSEt')
        g.press('key_scroll')                                                          # segment 1
        self.assertEqual(g.lcd(), ('1', 'S.NUM'))

        def segment(kind_presses, settings):
            g.to('pset__s_type')
            g.press('key_up', times=kind_presses)
            for screen, key, n in settings:
                g.to(screen)
                g.press(key, times=n)
            g.press('key_scroll')
            g.to('pset__s_num')
        segment(1, [('pset__tsp', 'key_up', 400), ('pset__r_rate', 'key_up', 49)])     # RAtE: TSP 400 C, R.RATE 5.0 C/min
        self.assertEqual(g.lcd()[0], '2')
        segment(3, [('pset__dur', 'key_up', 30)])                                     # dwEL 00:30
        segment(4, [('pset__tsp', 'key_up', 600)])                                    # StEP to 600 C
        segment(3, [('pset__dur', 'key_up', 30)])
        segment(2, [('pset__tsp', 'key_up', 200), ('pset__r_time', 'key_up', 80)])    # tImE: 200 C in 01:20
        g.to('pset__s_type')
        self.assertEqual(g.lcd(), ('ENd', 'S.TYPE'))                                    # segment 6: End (default)
        segs = g.state('program.definition', 'segments')[:6]
        self.assertEqual([(s['type'], round(s['target_K'] - C0), s['rate_per_unit_K'], s['time_s']) for s in segs],
                         [('rate', 400, 5.0, 0.0), ('dwell', 0, 0.1, 1800.0), ('step', 600, 0.1, 0.0), ('dwell', 0, 0.1, 1800.0), ('time', 200, 0.1, 4800.0),
                          ('end', 0, 0.1, 0.0)])
        g.press('key_page', times=3)
        g.press('key_up', held=['key_down'])                                           # run it
        g.step(4800.0, dt=600.0)                                                       # 380 K at 5 K/min = 76 min, then the dwell
        self.assertEqual((g.state('program.status', 'segment'), g.lcd()[0]), (2, '400'))
        g.step(1800.0, dt=600.0)
        self.assertEqual(g.state('program.status', 'segment'), 4)                      # the step segment applied at once
        self.assertAlmostEqual(g.state('program.status', 'working_setpoint_K'), C0 + 600.0, places=6)
        g.step(1800.0 + 4800.0, dt=600.0)
        self.assertEqual(g.state('program.status', 'mode'), 'reset')                   # end type rSEt: back to basic control
        DONE_TASKS.add('create a program (example)')

    def test_acknowledge_an_alarm(self):
        g = Rig()
        g.call(g.A, 'loop.configure', output_high_percent=1.0, loop_break_time_s=300.0, loop_break_delta_K=5.0)
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 500.0)
        g.step(1200.0, dt=60.0)
        self.assertTrue(g.state('loop.break'))                                         # 15 W cannot heat by 5 K in 10 min
        icon = g.frame()['displays']['display']['icons']['alarm']
        self.assertEqual((icon['on'], icon['blink']), (True, True))                   # the bell flashes
        g.press('key_scroll', times=3)                                                 # ACK: No -> Yes
        self.assertEqual(g.lcd(), ('no', 'ACK'))
        g.press('key_up')
        self.assertEqual(g.lcd()[0], 'YES')
        icon = g.frame()['displays']['display']['icons']['alarm']
        self.assertEqual((icon['on'], icon['blink']), (True, False))                  # acknowledged: lit, the alarm state remains
        g.call(g.A, 'loop.configure', loop_break_time_s=0.0)
        g.step(60.0)
        self.assertNotIn('alarm', g.icons())
        g.call(g.A, 'loop.configure', loop_break_time_s=300.0)
        g.step(1200.0, dt=60.0)
        self.assertTrue(g.press('key_page', held=['key_scroll']))                      # Page + Scroll on the home display also acknowledges
        self.assertEqual(g.state('alarm.state'), {'active': ['loop_break'], 'acknowledged': ['loop_break']})
        DONE_TASKS.add('acknowledge an alarm')

    def test_offset_calibration(self):
        g = Rig()
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 1080.0)
        g.step(4 * 3600.0)
        self.assertEqual(g.lcd()[0], '1080')
        g.login(3)
        g.press('key_page', times=2)                                                   # A1 list, Scroll to PV.OFS
        self.assertEqual(g.lcd(), ('A1', 'LIST'))
        g.to('a1__pv_ofs')
        self.assertEqual(g.lcd(), ('0.00', 'PV.OFS'))
        g.press('key_down', hold=50.4)                                                 # 1 + 499 steps of 0.01: -5.00
        self.assertEqual((g.lcd()[0], g.state('sensor.pv_offset_k')), ('-5.00', -5.0))
        g.step(3600.0)
        g.press('key_page', times=8)
        self.assertEqual(g.screen(), 'home')
        self.assertEqual(g.lcd()[0], '1080')                                           # the controller holds its corrected reading
        self.assertAlmostEqual(g.state('thermal.temperature_k'), C0 + 1085.0, places=6)
        g.press('key_page', hold=3.0)                                                  # log out
        g.press('key_down', times=2)
        g.press('key_scroll')
        self.assertEqual(g.level(), 1)
        DONE_TASKS.add('offset calibration')

    def test_autotune(self):
        g = Rig()
        g.login(3)
        g.press('key_page', times=3)                                                   # LOOP list: s.list AtuN
        self.assertEqual(g.lcd(), ('LOOP', 'LIST'))
        g.press('key_scroll')
        g.press('key_up')
        self.assertEqual(g.lcd(), ('AtuN', 'S.LIST'))
        g.press('key_scroll')
        self.assertEqual(g.lcd(), ('OFF', 'TUNE'))
        g.press('key_up')                                                              # Tune On: stand-by / monitoring
        self.assertEqual(g.state('autotune.state', 'stage'), 'moni')
        g.press('key_page', times=20)
        g.to('home', key='key_page')
        g.press('key_up', hold=38.4)                                                   # within the stand-by time: the target set point
        g.step(30.0, dt=1.0)
        seen = set()
        for _ in range(8):
            g.step(0.5, dt=0.5)
            seen.add(g.lcd()[1])
        self.assertTrue(any('TUNE' in s or 'AUTO' in s for s in seen), seen)          # <AUTOTUNE ACTIVE> scrolls
        g.step(3 * 3600.0, dt=600.0)
        self.assertEqual(g.state('autotune.state'), {'enabled': False, 'status': 'done', 'stage': 'idle', 'stage_time_s': 0.0})
        DONE_TASKS.add('autotune')


# ---------------------------------------------------------------- the other functions of the lists
class OtherFunctions(unittest.TestCase):
    def test_conf_level_units_home_display_and_customer_id(self):
        g = Rig()
        g.login(4)
        self.assertEqual((g.screen(), g.lcd()), ('conf_home', ('CONF', 'LIST')))     # CONF home: no temperature
        g.to('list__inst', key='key_page')
        g.press('key_scroll')
        g.press('key_scroll')                                                          # INFO: LANG, T.UNIT
        g.press('key_scroll')
        self.assertEqual(g.lcd(), ('dEG.C', 'T.UNIT'))
        g.press('key_up')
        g.press('key_scroll')
        g.press('key_up', times=42)                                                    # CS.ID
        self.assertEqual((g.state('controller.settings', 'temperature_unit'), g.state('controller.settings', 'customer_id')), ('F', 42))
        g.press('key_page')
        g.press('key_up')                                                              # sublist HmI: H.DISP PV.SP -> LPU
        g.press('key_scroll')
        g.press('key_up', times=2)
        self.assertEqual(g.lcd(), ('LPU', 'H.DISP'))
        g.press('key_page', hold=3.0)                                                  # log out of CONF: test cycle, Level 1
        g.press('key_scroll')
        self.assertEqual(g.screen(), 'boot')
        g.step(7.0, dt=1.0)
        self.assertEqual((g.screen(), g.level(), g.lcd()), ('home', 1, ('68', '')))  # 20 C = 68 F, PV only
        self.assertIn('unit_f', g.icons())

    def test_keylock_demo_and_language(self):
        g = Rig()
        g.call(g.A, 'controller.configure', language='de', demo=True)
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 300.0)
        g.step(600.0)
        self.assertEqual((g.lcd()[0], g.state('controller.output_percent')), ('300', 0.0))   # demo: simulated load, heaters off
        self.assertAlmostEqual(g.state('thermal.temperature_k'), C0 + 20.0, places=6)
        windows = set()
        for _ in range(20):
            g.step(0.25, dt=0.25)
            windows.add(g.lcd()[1])
        self.assertTrue(any('DEMO' in w for w in windows), windows)                    # REGELKREIS DEMO scrolls
        g.login(4)
        g.to('list__inst', key='key_page')
        g.press('key_scroll')
        g.press('key_up')
        g.press('key_scroll')
        g.press('key_scroll')                                                          # HmI: H.DISP, K.LOCK
        self.assertEqual(g.lcd(), ('OFF', 'K.LOCK'))
        self.assertTrue(g.press('key_up'))
        for key in ('key_page', 'key_scroll', 'key_up', 'key_down'):
            self.assertFalse(g.press(key))                                             # every key refused
        g.call(g.A, 'controller.configure', keylock=False)                             # iTools stand-in
        self.assertTrue(g.press('key_page'))

    def test_output_high_limit_slows_heating(self):
        g = Rig()
        g.call(g.A, 'loop.configure', output_high_percent=50.0)
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 800.0)
        g.step(600.0)
        self.assertEqual(g.state('controller.output_percent'), 50.0)
        half = g.state('thermal.heater_energy_j')
        self.assertAlmostEqual(half, 750.0 * 600.0, places=3)
        g.login(3)
        g.to('list__loop', key='key_page')
        g.press('key_scroll')
        g.press('key_up', times=3)                                                     # sublist OP
        g.press('key_scroll')
        self.assertEqual(g.lcd(), ('50', 'OUT.HI'))
        self.assertIn('percent', g.icons())

    def test_power_cycle_recovery(self):
        for recovery, expect in (('continue', ('run', 1)), ('reset', ('reset', 0)), ('ramp', ('run', 1))):
            g = Rig()
            g.call(g.A, 'program.configure', recovery=recovery)
            g.call(g.A, 'program.set_segment', number=1, type='rate', target_K=C0 + 600.0, rate_per_unit_K=10.0)
            g.call(g.A, 'program.set_mode', mode='run')
            g.step(600.0)
            g.switch('off')
            self.assertTrue(g.state('program.status', 'interrupted'))
            g.step(1200.0)
            g.switch_on()
            status = g.state('program.status')
            self.assertEqual((status['mode'], status['segment']), expect, recovery)
            if recovery == 'ramp':                                                     # restarts from the cooled reading
                self.assertLess(status['working_setpoint_K'], C0 + 120.0)

    def test_running_list_advance_and_cycles(self):
        g = Rig()
        g.call(g.A, 'program.configure', cycles=2, dwell_units='sec')
        g.call(g.A, 'program.set_segment', number=1, type='dwell', time_s=120.0)
        g.call(g.A, 'program.set_segment', number=2, type='dwell', time_s=60.0)
        g.call(g.A, 'program.set_mode', mode='run')
        g.press('key_page')
        g.to('prog__p_tim_l')
        self.assertEqual(g.lcd(), ('00:03', 'P.TIM.L'))                                 # RESN minutes: hh:mm
        g.to('prog__s_tim_l')
        self.assertEqual(g.lcd(), ('00:02', 'S.TIM.L'))
        g.to('prog__p_advn')
        self.assertFalse(g.press('key_up'))                                            # P.ADVN needs Level 2
        g.press('key_page', times=2)
        g.login(2)
        g.press('key_page')
        g.to('prog__p_advn')
        g.press('key_up')
        self.assertEqual(g.state('program.status', 'segment'), 2)
        g.step(60.0, dt=10.0)
        self.assertEqual((g.state('program.status', 'segment'), g.state('program.status', 'cycles_left')), (1, 1))   # second cycle
        g.step(180.0, dt=10.0)
        self.assertEqual(g.state('program.status', 'mode'), 'reset')

    def test_comms_ip_address(self):
        g = Rig()
        g.login(4)
        g.to('list__comm', key='key_page')
        g.press('key_scroll')
        g.press('key_up')                                                              # Nwrk
        g.press('key_scroll')
        self.assertEqual(g.lcd(), ('192', 'IP.A1'))
        g.press('key_scroll', times=3)
        g.press('key_down', times=22)
        self.assertEqual(g.state('comms.settings', 'ip_address'), [192, 168, 111, 200])
        g.to('comm_nwrk__mac1')
        self.assertEqual(g.lcd(), ('0', 'MAC1'))


# ---------------------------------------------------------------- panel and direct capability calls reach the same state
class Equivalence(unittest.TestCase):
    def _run(self, panel):
        g = Rig()
        if panel:
            g.press('key_up', hold=38.4)
            g.login(2)
            g.press('key_page', times=2)
            g.to('pset__s_num')
            g.to('pset__s_type')
            g.press('key_up')
            g.to('pset__tsp')
            g.press('key_up', times=500)
            g.to('pset__r_rate')
            g.press('key_up', times=99)
            g.press('key_page', times=4)
            g.press('key_up', held=['key_down'])
        else:
            g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 400.0)
            g.step(4.0, dt=1.0)                                                        # the time the login takes on the panel
            g.call(g.A, 'program.set_segment', number=1, type='rate')
            g.call(g.A, 'program.set_segment', number=1, target_K=C0 + 500.0)
            g.call(g.A, 'program.set_segment', number=1, rate_per_unit_K=10.0)
            g.call(g.A, 'program.set_mode', mode='run')
        g.step(1200.0)
        return runtime_state(g.h)

    def test_program_and_setpoint(self):
        self.assertEqual(self._run(True), self._run(False))


# ---------------------------------------------------------------- the controller functions behind the panel
class DeviceFunctions(unittest.TestCase):
    def test_holdback_stops_the_program_time(self):
        g = Rig()
        g.call(g.A, 'program.configure', holdback_type='low', holdback_value_K=5.0, ramp_units='sec')
        g.call(g.A, 'program.set_segment', number=1, type='rate', target_K=C0 + 1000.0, rate_per_unit_K=10.0)   # 10 K/s: faster than the furnace
        g.call(g.A, 'program.set_mode', mode='run')
        g.step(600.0, dt=60.0)
        status = g.state('program.status')
        self.assertTrue(status['holdback'])
        self.assertLess(status['working_setpoint_K'] - g.state('thermal.temperature_k'), 5.0 + 100.0 + 1e-6)

    def test_sensor_error_and_offset(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', offset_K=3.0, gain=0.0)
        g.call(g.A, 'sensor.configure_error', expect='rejected', offset_K=1.0, gain=0.0)   # evaluator only
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 500.0)
        g.step(3 * 3600.0)
        self.assertAlmostEqual(g.state('thermal.temperature_k'), C0 + 497.0, places=6)      # the reading is held at 500
        g.call(g.A, 'sensor.calibrate_offset', offset_K=-3.0)
        g.step(3600.0)
        self.assertAlmostEqual(g.state('thermal.temperature_k'), C0 + 500.0, places=6)
        g.call(g.A, 'sensor.reset_calibration')
        self.assertEqual(g.state('sensor.pv_offset_k'), 0.0)

    def test_autotune_abort_and_timeout(self):
        g = Rig()
        g.call(g.A, 'autotune.set', enabled=True)
        g.call(g.A, 'autotune.set', enabled=False)
        self.assertEqual(g.state('autotune.state', 'status'), 'abort')
        g.call(g.A, 'loop.configure', output_high_percent=1.0)
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 900.0)
        g.call(g.A, 'autotune.set', enabled=True)
        g.step(3 * 3600.0, dt=600.0)
        self.assertEqual(g.state('autotune.state', 'status'), 'timeout')

    def test_another_callers_run_cannot_be_stopped_or_switched_off(self):
        """Lead rule 2026-10-19: B cannot stop or change A's run (panel or direct); A resets its own; the evaluator is unrestricted.  The
        instrument switch is a hard switch: never refused, the controller reacts as to a power loss (lead clarification 2026-10-19)."""
        g = Rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        g.call(g.A, 'program.set_segment', number=1, type='rate', target_K=C0 + 400.0, rate_per_unit_K=5.0)
        g.press('key_up', held=['key_down'])                                           # A runs the program (Up + Down, p.14)
        self.assertEqual(g.state('program.status', 'mode'), 'run')
        before = runtime_state(g.h)
        for cap, args in (('program.set_mode', {'mode': 'reset'}), ('program.set_mode', {'mode': 'hold'}), ('program.advance', {}),
                          ('program.set_segment', {'number': 1, 'type': 'rate', 'target_K': C0 + 500.0, 'rate_per_unit_K': 5.0})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', (cap, args))
        self.assertFalse(g.call(B, 'panel.press', input_id='key_up', duration_s=3.0, held=['key_down'])['accepted'])   # B's reset chord
        self.assertEqual(g.lcd()[1], 'DENIED')                                         # shown on the second line (frame and pixels)
        self.assertEqual(runtime_state(g.h), before)
        self.assertTrue(g.press('key_up', held=['key_down'], hold=3.0))                # A resets its own run
        self.assertEqual(g.state('program.status', 'mode'), 'reset')
        g.call(B, 'program.set_mode', mode='run')
        g.call(g.E, 'program.set_mode', mode='reset')                                 # the evaluator is unrestricted
        self.assertEqual(g.state('program.status', 'mode'), 'reset')
        self.assertTrue(g.press('key_up', held=['key_down']))                          # A runs again; B's instrument switch is physical
        self.assertTrue(g.call(B, 'panel.switch', input_id='instrument_switch', position='off')['accepted'])
        self.assertFalse(g.state('power.enabled'))
        self.assertTrue(g.call(B, 'panel.switch', input_id='instrument_switch', position='on')['accepted'])
        self.assertEqual(g.state('program.status', 'mode'), 'run')                     # power-loss recovery (RECOV) continues A's program

    def test_panel_gesture_acts_as_the_calling_session(self):
        """2026-10-19 sync fix: the commands behind a gesture run as the session that made it, not as a fixed panel principal."""
        from unittest import mock
        g = Rig()
        callers, real = [], rt.CarboliteTF1Session._invoke_checked

        def spy(sess, *a, **k):
            callers.append((sess, a[2]))
            return real(sess, *a, **k)
        with mock.patch.object(rt.CarboliteTF1Session, '_invoke_checked', autospec=True, side_effect=spy):
            g.press('key_up')
        self.assertEqual(g.state('thermal.target_k'), C0 + 21.0)
        self.assertIn('thermal.set_temperature', [c for _, c in callers])
        self.assertTrue(all(s is g.A for s, c in callers if c != 'panel.frame'))
        self.assertIsNone(g.h._panel_caller)

    def test_refusals(self):
        g = Rig()
        g.call(g.A, 'program.set_mode', expect='rejected', mode='run')                 # no program
        g.call(g.A, 'program.set_segment', expect='rejected', number=3, type='rate')   # beyond the first End
        g.call(g.A, 'program.set_segment', number=1, type='dwell', time_s=60.0)
        g.call(g.A, 'program.set_mode', mode='run')
        g.call(g.A, 'thermal.set_temperature', expect='rejected', temperature_K=C0 + 100.0)   # program owns the set point
        g.call(g.A, 'controller.configure', expect='rejected')                         # nothing to set
        g.call(g.A, 'comms.configure', expect='rejected', ip_address=[1, 2, 3.5, 4])
        g.switch('off')
        g.call(g.A, 'loop.configure', expect='rejected', output_high_percent=10.0)


class SensorBreakFault(unittest.TestCase):
    """2026-10-19 sync fix: the documented sensor break (3016 manual p.13, TF1 manual p.72, EPC3000 guide p.231, p.256, p.265, p.287) by the
    evaluator-only fault.inject / fault.clear; the panel shows it and the controller reacts as documented."""

    def test_sensor_break_display_stops_heating_and_holds_the_program(self):
        g = Rig()
        g.call(g.A, 'program.set_segment', number=1, type='rate', target_K=C0 + 600.0, rate_per_unit_K=10.0)
        g.call(g.A, 'program.set_mode', mode='run')
        g.step(1800.0, dt=60.0)
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='S.brk')['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('fault.inject', g.A.describe(instance_id=INST)['capabilities'])
        before = g.state('thermal.temperature_k')
        sp = g.state('program.status', 'working_setpoint_K')
        self.assertEqual(g.call(g.E, 'fault.inject', code='S.brk'), {'code': 'S.brk'})
        self.assertTrue(g.state('sensor.break'))
        self.assertEqual(g.state('program.status', 'mode'), 'hold')                  # RECOV cont: held while the PV is bad (guide p.231)
        self.assertEqual(g.state('thermal.indicated_temperature_k'), C0 + 1200.0)    # the high point (3016 manual p.13)
        g.step(0.25, dt=0.25)
        shown = set()
        lines = []
        for _ in range(40):
            g.step(0.25, dt=0.25)
            pv, line = g.lcd()
            shown.add(pv)
            lines.append(line)
        self.assertEqual(shown, {'S.brk', 'bAd'})                                    # PV line alternates (pixels read back by lcd())
        self.assertTrue(any('SENSOR' in x for x in lines) and any('BROKEN' in x for x in lines), lines)
        g.step(600.0, dt=60.0)
        self.assertLess(g.state('thermal.temperature_k'), before - 20.0)             # forced manual, fallback output 0 %: cools
        self.assertEqual(g.state('controller.output_percent'), 0.0)
        self.assertNotIn('output1', g.icons())
        self.assertNotIn('alarm', g.icons())                                          # no alarm relays fitted: no beacon (p.13)
        self.assertEqual(g.state('program.status', 'working_setpoint_K'), sp)
        g.press('key_down', held=['key_up'])                                          # Run shortcut: refused while the PV is bad
        self.assertEqual(g.state('program.status', 'mode'), 'hold')
        g.call(g.A, 'program.set_mode', expect='rejected', mode='run')
        g.call(g.A, 'autotune.set', expect='rejected', enabled=True)
        g.login(3)
        g.press('key_page', times=2)
        g.to('a1__sb_out')
        self.assertEqual(g.lcd(), ('On', 'SB.OUT'))
        g.to('a1__pv')
        self.assertEqual(g.lcd()[0], '1200')                                          # high point; decimal auto-scaled (guide p.68)
        g.to('a1__pv_st')
        self.assertEqual(g.lcd(), ('bAd', 'PV.ST'))
        self.assertEqual(g.call(g.E, 'fault.clear'), {'cleared': ['S.brk']})          # thermocouple repaired: RECOV cont runs on
        self.assertFalse(g.state('sensor.break'))
        self.assertEqual(g.state('program.status', 'mode'), 'run')
        self.assertEqual(g.lcd(), ('Good', 'PV.ST'))
        g.step(600.0, dt=60.0)
        self.assertGreater(g.state('controller.output_percent'), 0.0)

    def test_recovery_reset_ramp_autotune_and_power_cycle(self):
        for recovery in ('reset', 'ramp'):
            g = Rig()
            g.call(g.A, 'program.configure', recovery=recovery)
            g.call(g.A, 'program.set_segment', number=1, type='rate', target_K=C0 + 600.0, rate_per_unit_K=10.0)
            g.call(g.A, 'program.set_mode', mode='run')
            g.step(1800.0, dt=60.0)
            g.call(g.E, 'fault.inject', code='S.brk')
            self.assertEqual(g.state('program.status', 'mode'), 'reset' if recovery == 'reset' else 'hold', recovery)
            g.step(1200.0, dt=60.0)
            g.call(g.E, 'fault.clear')
            status = g.state('program.status')
            if recovery == 'ramp':                                                    # from the cooled reading at the segment rate
                self.assertEqual(status['mode'], 'run')
                self.assertAlmostEqual(status['working_setpoint_K'], g.state('thermal.indicated_temperature_k'), delta=0.06)
            else:
                self.assertEqual(status['mode'], 'reset')
        g = Rig()
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 400.0)
        g.call(g.A, 'autotune.set', enabled=True)
        g.call(g.E, 'fault.inject', code='S.brk')
        self.assertEqual(g.state('autotune.state', 'status'), 'abort')               # guide p.265: autotune aborts on a bad PV
        g.switch('off')
        g.switch_on()                                                                 # the broken thermocouple stays broken
        self.assertTrue(g.state('sensor.break'))
        self.assertIn(g.lcd()[0], ('S.brk', 'bAd'))
        g.call(g.E, 'fault.clear')
        self.assertEqual(g.call(g.E, 'fault.clear'), {'cleared': []})
        g.step(1.0, dt=1.0)
        self.assertTrue(g.lcd()[0].isdigit())                                         # the PV again


# ---------------------------------------------------------------- refusals, locks, time-outs
class PidLoop(unittest.TestCase):
    """The 3016 PID drives the heater (2026-10-19): the LOOP list parameters act on it; autotune identifies the plant and writes the active set."""

    @staticmethod
    def heat(g, sp, seconds, dt=10.0):
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + sp)
        temps = []
        for _ in range(int(seconds / dt)):
            g.h.step(dt)
            temps.append(g.h.debug_snapshot()['instances'][INST]['state']['thermal.temperature_k'] - C0)
        return temps

    def test_factory_terms_land_without_overshoot_and_hold(self):
        g = Rig()
        temps = self.heat(g, 800.0, 3600.0)
        self.assertLessEqual(max(temps), 800.0 + 1e-9)
        self.assertAlmostEqual(temps[-1], 800.0, places=9)
        out = g.state('loop.pid_out')
        self.assertEqual(out['set'], 2)                                                # BND.1 0: set 2 (manual p.35)
        self.assertAlmostEqual(out['out'], 100.0 * 0.5 * 780.0 / 1500.0, places=6)     # hold output = losses
        self.assertEqual(g.state('loop.pid_sets')['set1'], g.state('loop.pid_sets')['set2'])

    def test_gain_scheduling_output_limits_and_rates(self):
        g = Rig()
        g.call(g.A, 'loop.configure', band_1_K=600.0, output_high_1_percent=40.0)
        temps = self.heat(g, 800.0, 1800.0)
        self.assertEqual(g.state('loop.pid_out')['set'], 1)                            # below BND.1: set 1 with OP.HI 40 %
        self.assertEqual(g.state('controller.output_percent'), 40.0)
        self.heat(g, 800.0, 7200.0)
        self.assertEqual(g.state('loop.pid_out')['set'], 2)
        r = Rig()
        r.call(r.A, 'loop.configure', rate_up_percent_s=1.0)                           # OP.UP: 1 % per second
        r.call(r.A, 'thermal.set_temperature', temperature_K=C0 + 800.0)
        r.h.step(10.0)
        self.assertLessEqual(r.state('loop.pid_out')['out'], 11.0)
        self.assertLess(r.state('thermal.heater_energy_j'), 1500 * 10 * 0.11)
        lo = Rig()
        lo.call(lo.A, 'loop.configure', output_low_percent=20.0)                       # OUT.LO: the output never goes below 20 %
        self.heat(lo, 300.0, 36000.0, dt=600.0)
        self.assertEqual(lo.state('controller.output_percent'), 20.0)
        self.assertGreater(lo.h.debug_snapshot()['instances'][INST]['state']['thermal.temperature_k'] - C0, 600.0)

    def test_input_filter_and_autotune_identify_the_plant(self):
        g = Rig()
        g.call(g.A, 'controller.configure', filter_s=10.0)                             # FILT.T 10 s with the terms tuned for 1.6 s: overshoot
        slow = max(self.heat(g, 800.0, 3600.0))
        self.assertGreater(slow, 801.0)
        a = Rig()
        a.call(a.A, 'thermal.set_temperature', temperature_K=C0 + 600.0)
        a.call(a.A, 'autotune.set', enabled=True)
        a.step(3 * 3600.0, dt=600.0)
        self.assertEqual(a.state('autotune.state', 'status'), 'done')
        tuned = a.state('loop.pid_sets')['set2']                                       # the hi / lo step responses give C and UA: the factory terms
        factory = rt._tf1_tuning(rt.CARBOLITE_TF1_PARAMETERS['thermal_capacity_J_K']['value'], rt.CARBOLITE_TF1_PARAMETERS['heat_loss_W_K']['value'], 1500.0, 1.6)
        for k in ('pb_K', 'ti_s', 'td_s'):
            self.assertAlmostEqual(tuned[k], factory[k], delta=2e-3 * max(factory[k], 1.0), msg=k)
        b = Rig()
        b.call(b.A, 'controller.configure', filter_s=10.0)
        b.call(b.A, 'thermal.set_temperature', temperature_K=C0 + 600.0)
        b.call(b.A, 'autotune.set', enabled=True)
        b.step(3 * 3600.0, dt=600.0)
        self.assertEqual(b.state('loop.pid_sets')['set2']['ti_s'], 25.0)               # retuned for the slower input
        b.call(b.A, 'thermal.set_temperature', temperature_K=C0 + 300.0)
        b.step(3 * 3600.0, dt=600.0)
        self.assertLess(max(self.heat(b, 800.0, 3600.0)), slow)


class Rules(unittest.TestCase):
    def test_wrong_code_and_lockout(self):
        g = Rig()
        for _ in range(3):
            g.press('key_page', hold=3.0)
            g.press('key_up')
            g.press('key_scroll')
            g.press('key_up', times=8)
            g.step(2.0, dt=1.0)
            self.assertEqual((g.screen(), g.level()), ('home', 1))                     # wrong: back to the home display
        g.press('key_page', hold=3.0)
        g.press('key_up')
        g.press('key_scroll')
        self.assertEqual((g.screen(), g.lcd()[1]), ('home', 'LOCKD'))                  # locked for 30 min
        g.step(1800.0)
        g.login(2)
        self.assertEqual(g.level(), 2)

    def test_time_out_and_read_only(self):
        g = Rig()
        g.press('key_scroll')
        self.assertFalse(g.press('key_up'))                                            # W.OUT is read only
        g.step(29.0, dt=1.0)
        self.assertEqual(g.screen(), 'home__w_out')
        g.step(1.0, dt=1.0)
        self.assertEqual(g.screen(), 'home')                                           # 30 s without a key
        g.press('key_page')
        g.press('key_page')
        self.assertEqual(g.screen(), 'home')                                           # Level 1: P.SEt is not shown

    def test_power_gate_and_unknown_gestures(self):
        g = Rig(on=False)
        for key in ('key_page', 'key_scroll', 'key_up', 'key_down'):
            self.assertFalse(g.press(key))
        self.assertFalse(g.switch('off'))
        r = g.call(g.A, 'panel.turn', expect='rejected', input_id='key_up', detents=1)
        self.assertEqual(r['error']['code'], 'UNKNOWN_CAPABILITY')
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        caps = g.A.describe(instance_id=INST)['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.press', 'panel.switch', 'panel.capture'})
        self.assertNotIn('sensor.configure_error', caps)
        self.assertIn('sensor.configure_error', g.E.describe(instance_id=INST)['capabilities'])


# ---------------------------------------------------------------- rendering, capture, contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.call(g.A, 'program.set_segment', number=1, type='rate', target_K=C0 + 400.0, rate_per_unit_K=5.0)
            g.press('key_up', held=['key_down'])
            g.step(601.0, dt=60.0)
            return g
        a, b = scenario(), scenario()
        for d in SPEC['displays']:
            ia, ib = a.h.panel(INST).render(d), b.h.panel(INST).render(d)
            self.assertEqual((ia.dtype.name, ia.shape[2]), ('uint8', 3))
            self.assertTrue((ia == ib).all())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel(INST).save_png('display', OUT / 'home.png'), 'home_line2': a.h.panel(INST).save_png('line2', OUT / 'home_line2.png')}
        a.press('key_page')
        a.to('prog__p_mode')
        self.assertEqual(a.lcd(), ('run', 'P.MODE'))
        shots['menu'] = a.h.panel(INST).save_png('display', OUT / 'menu.png')
        shots['menu_line2'] = a.h.panel(INST).save_png('line2', OUT / 'menu_line2.png')
        a.press('key_page', times=2)
        a.call(a.A, 'loop.configure', output_high_percent=1.0, loop_break_time_s=120.0)
        a.step(600.0, dt=60.0)
        self.assertIn('alarm', a.icons())
        while not a.frame()['displays']['display']['icons']['alarm']['shown']:      # the flashing bell in its on phase
            a.step(0.25, dt=0.25)
        shots['error'] = a.h.panel(INST).save_png('display', OUT / 'error.png')
        a.call(a.E, 'fault.inject', code='S.brk')                                     # the documented sensor-break screen (evaluator injection)
        while a.lcd()[0] != 'S.brk' or 'SENSOR' not in a.lcd()[1]:
            a.step(0.25, dt=0.25)
        shots['sensor_break'] = a.h.panel(INST).save_png('display', OUT / 'sensor_break.png')
        shots['sensor_break_line2'] = a.h.panel(INST).save_png('line2', OUT / 'sensor_break_line2.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='display')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.switch_on()
        self.assertNotEqual(g.call(g.A, 'panel.capture', display_id='display')['image']['sha256'], r['image']['sha256'])

    def test_spec_and_contract(self):
        meta = rt.carbolite_tf1_metadata()
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
        for k, cap in c['capabilities'].items():
            for comp in cap['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', pu.SPEC_VERSION))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('tf1_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
