"""front-panel tests for CHEM-191 miele_plw6111 (MP.CON 3 control of the Miele PLW 6111; panel_ui.json + scripts/asset_runtime/_panel_ui.py
on scripts/basic40_runtime_thermal.py): python3 -m pytest tests/test_panel_ui_miele_plw6111.py -q

Every gesture goes through the session API (panel.press / panel.switch).  Each check looks at the runtime state and at the screen twice: the
structured frame (panel.frame) and the rendered pixels, read back with a 5x7 dot reader that knows only the display geometry of the spec (text
lines) and the window rectangles.  Themes: the seven key procedures of the menu survey; the other menu functions (standard programme view and
copy, customer programmes, programme selection, user name, language, A0 display, clock, end-of-programme and live printouts, drying key);
panel = direct capability; the controller functions behind the panel (block table, A0, power-failure restart, history, refusals); rules
(power gate, password, programming mode, time-outs); render determinism and the PNGs of outputs/panel_ui/miele_plw6111/; contract entries."""
import hashlib
import importlib.util
import json
import math
import sys
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_thermal as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'miele_01'
C0 = 273.15
OUT = ROOT / 'outputs/panel_ui/miele_plw6111'
ASSET = ROOT / 'assets/basic40_batch02/t1_thermal/miele_plw6111'
SPEC = rt._miele_panel()['spec']
DISP = SPEC['displays']['display']
SCALE = DISP['text_scale']
PASSWORD = 2741
SURVEY_TASKS = ['run the Universal programme', 'select another programme', 'interrupt or cancel a programme', 'reset after a malfunction',
                'print a stored batch protocol', 'change a parameter', 'view temperature or warnings during a run']
DONE_TASKS = set()
_GLYPHS = {}
for _ch, _cols in pu.FONT5X7.items():
    _GLYPHS.setdefault(bytes(_cols), _ch)


# ---------------------------------------------------------------- reading the rendered display (white 5x7 text on black, coloured windows)
def _white(px):
    return min(int(px[0]), int(px[1]), int(px[2])) > 150


def read_line(img, row, inverse=False):
    y0 = 2 + row * 9 * SCALE
    out = ''
    cols = (img.shape[1] - 4) // (6 * SCALE)
    for c in range(cols):
        x0 = 2 + c * 6 * SCALE
        glyph = bytes(sum(1 << gy for gy in range(8) if _white(img[y0 + gy * SCALE, x0 + gx * SCALE]) != inverse) for gx in range(5))
        out += _GLYPHS.get(glyph, '?')
    return out.rstrip()


def _ascii(text):
    return ''.join(c if c in pu.FONT5X7 else unicodedata.normalize('NFKD', c)[:1] or '?' for c in text)


# ---------------------------------------------------------------- rig
class Rig:
    def __init__(self, on=True, water=True, scene=True, password=True):
        self.h = rt.MielePLW6111Runtime(scene_adapter=(lambda key, values: None) if scene else None)
        self.h.create_instance(INST)
        if water:
            self.h.configure_water_model(INST, effective_heat_capacity_J_K=167200.0, heat_loss_W_K=5.0, initial_temperature_K=293.15, maximum_temperature_K=368.15,
                                         evidence='test fixture: 40 kg water equivalent, 5 W/K loss (assumed)')
        if password:
            self.h.set_user_password(INST, PASSWORD)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if on:
            assert self.switch('on')

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
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self):
        """Text lines shown now, from the frame and (lines not under a window) from the pixels; both must agree."""
        f = self.frame()['displays']['display']
        img = self.h.panel(INST).render('display')
        if f.get('brightness') == 0:
            assert not img.any()
            return f['lines']
        covered = set()
        for w in f.get('widgets', []):
            x, y, ww, hh = w['rect']
            covered |= {r for r in range(len(f['lines'])) if y < 2 + (r + 1) * 9 * SCALE and 2 + r * 9 * SCALE < y + hh}
        for r, text in enumerate(f['shown']):
            if r not in covered:
                self.t.assertEqual(read_line(img, r, f['inverse'][r]), _ascii(text).rstrip(), (r, text))
        return f['lines']

    def windows(self):
        """Window texts and their fill colour read from the pixels (green end, orange interrupt / cancel, red fault)."""
        f = self.frame()['displays']['display']
        img = self.h.panel(INST).render('display')
        out = []
        for w in f.get('widgets', []):
            x, y, _, _ = w['rect']
            px = tuple(int(v) for v in img[y + 2, x + 2])
            out.append((w['text'], {(40, 160, 70): 'green', (225, 165, 0): 'orange', (200, 40, 40): 'red'}.get(px, px)))
        return out

    def step(self, s, dt=60.0):
        while s > 1e-9:
            self.h.step(min(s, dt))
            s -= min(s, dt)
            self.frame()

    def state(self, sid, path=None):
        v = self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']
        return v[path] if path is not None else v

    def raw(self):
        return self.h.debug_snapshot()['instances'][INST]

    def menu(self, index):
        """PRG 5 s: the main menu; P1 down to the line; START."""
        assert self.press('key_prg', hold=5.0)
        assert self.screen() == 'menu'
        self.press('key_p1', times=index)
        assert self.press('key_start')

    def password(self, code=PASSWORD):
        assert self.screen() == 'password'
        for digit in f'{code:04d}':
            self.press('key_p2', times=int(digit))
            self.press('key_start')

    def item(self):
        return self.frame()['ui']['menu']['item']

    def to_item(self, item_id, limit=60):
        for key in ('key_p1', 'key_p2'):                                                # down to the end of the list, then up
            last = None
            for _ in range(limit):
                if self.item() == item_id:
                    return
                if self.item() == last:
                    break
                last = self.item()
                self.press(key)
        raise AssertionError((item_id, self.item()))

    def run_to_end(self, limit=6 * 3600.0):
        t = 0.0
        while self.state('programme.status') == 'running' and t < limit:
            self.step(60.0)
            t += 60.0
        return t


def state_dump(h):
    inst = h.debug_snapshot()['instances'][INST]
    return json.dumps({'state': inst['state'], 'mp': inst['mp']}, sort_keys=True, default=float)


class Base(unittest.TestCase):
    def rig(self, **kw):
        g = Rig(**kw)
        g.t = self
        return g


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(Base):
    def test_run_the_universal_programme(self):
        g = self.rig(on=False)
        self.assertEqual(g.screen(), 'off')
        self.assertFalse(g.press('key_p1'))                                             # switched off: keys do nothing
        self.assertTrue(g.switch('on'))
        lines = g.lines()
        self.assertEqual((lines[0], lines[2].strip(), lines[12]), ('01.01.26 00:00       00000', 'UNIVERSAL              DRY', 'press start'))
        self.assertTrue(g.press('key_p3'))
        self.assertTrue(g.lines()[2].startswith('INTENSIVE'))
        self.assertTrue(g.press('key_p1'))                                              # P1: Universal (p.29)
        self.assertTrue(g.press('key_dry'))                                             # DRY off, on again
        self.assertNotIn('DRY', g.lines()[2])
        self.assertFalse(g.state('programme.drying'))
        self.assertTrue(g.press('key_dry'))
        self.assertTrue(g.state('programme.drying'))
        self.assertTrue(g.press('key_start'))
        self.assertEqual((g.screen(), g.state('programme.status'), g.state('door.locked'), g.state('programme.cycle_counter')), ('run', 'running', True, 1))
        lines = g.lines()
        self.assertEqual((lines[0][-5:], lines[3]), ('00001', 'pre-wash'))
        self.assertEqual(g.call(g.A, 'door.open', expect='rejected')['error']['code'], 'BUSY')     # locked while running (p.24)
        blocks = []
        while g.state('programme.status') == 'running':
            b = g.state('programme.block')['block']
            if not blocks or blocks[-1] != b:
                blocks.append(b)
            if b == 'main_wash' and g.state('water.indicated_temperature_k') >= 75 + C0 - 1e-6:
                self.assertIn('SP 75°C', g.lines())
            g.step(20.0, dt=20.0)
        self.assertEqual(blocks, ['prewash', 'main_wash', 'neutralisation', 'interim_rinse_1', 'final_rinse', 'drying', 'cooling'])
        self.assertEqual((g.screen(), g.state('programme.status'), g.state('door.locked'), g.state('program.phase')), ('home', 'end', False, 'completed'))
        self.assertGreater(g.state('programme.a0'), 0)
        self.assertAlmostEqual(g.state('programme.max_temperature_k'), 75 + C0, places=6)
        self.assertEqual(g.windows(), [('MAX TEMP. 75.0 °C', 'green'), ('end programme', 'green')])
        rec = g.state('history.records')[-1]
        self.assertEqual((rec['cycle'], rec['programme'], rec['result'], rec['max_temperature_C']), (1, 'universal', 'OK', 75.0))
        g.call(g.A, 'door.open')                                                        # opening the door clears the end window
        self.assertEqual((g.state('programme.status'), g.windows()), ('ready', []))
        DONE_TASKS.add('run the Universal programme')

    def test_select_another_programme(self):
        g = self.rig()
        names = [g.lines()[2].split()[0]]
        for _ in range(8):
            self.assertTrue(g.press('key_pplus'))                                       # P+ scrolls all programmes (p.30)
            names.append(g.lines()[2][:22].strip())
        self.assertEqual(names[-1], 'DISIN 93/10')
        self.assertEqual(g.state('programme.selected'), 'disin_93_10')
        self.assertTrue(g.press('key_start'))
        self.assertEqual(g.state('programme.block'), {'block': 'main_wash', 'index': 0, 'count': 6, 'target_K': 93 + C0, 'hold_s': 600.0})
        self.assertAlmostEqual(g.state('thermal.target_k'), 93 + C0)
        self.assertIn('SP 93°C', g.lines())
        g.step(3600.0)
        self.assertEqual(g.state('programme.block')['block'], 'drying')
        rec_a0 = g.state('programme.a0')
        self.assertGreater(rec_a0, 600 * 10 ** 1.3 - 1)                                 # >= 10 min at 93 degC (A0 600 * 10^1.3)
        DONE_TASKS.add('select another programme')

    def test_interrupt_or_cancel_a_programme(self):
        g = self.rig()
        g.press('key_p2')
        g.press('key_start')
        g.step(600.0)
        self.assertEqual(g.state('programme.block')['block'], 'main_wash')
        elapsed = g.state('program.phase_elapsed_s')
        self.assertTrue(g.press('key_stop'))                                            # STOP once: interrupted, door locked (p.29)
        self.assertEqual((g.screen(), g.state('programme.status'), g.state('program.running'), g.state('door.locked')), ('interrupted', 'interrupted', False, True))
        self.assertEqual(g.windows()[0], ('NO DISINFECTION', 'orange'))
        self.assertEqual(g.call(g.A, 'door.open', expect='rejected')['error']['code'], 'BUSY')
        g.step(60.0)
        self.assertTrue(g.press('key_start'))                                           # START: the phase is repeated
        self.assertEqual((g.screen(), g.state('programme.status'), g.state('program.phase_elapsed_s')), ('run', 'running', 0.0))
        self.assertGreaterEqual(elapsed, 0.0)
        g.step(600.0)
        hot = g.state('water.indicated_temperature_k') > 65 + C0
        self.assertTrue(hot)
        self.assertTrue(g.press('key_stop'))
        self.assertEqual(g.windows(), [('NO DISINFECTION', 'orange'), ('HOT MATERIAL', 'orange')])
        self.assertTrue(g.press('key_stop'))                                            # STOP twice: cancelled, unlocked (p.29, p.32)
        self.assertEqual((g.screen(), g.state('programme.status'), g.state('door.locked')), ('home', 'cancelled', False))
        self.assertEqual(g.windows(), [('NO DISINFECTION', 'orange'), ('HOT MATERIAL', 'orange')])
        self.assertEqual(g.state('history.records')[-1]['result'], 'NO DISINFECTION')
        g.call(g.A, 'door.open')
        self.assertEqual(g.state('programme.status'), 'ready')
        DONE_TASKS.add('interrupt or cancel a programme')

    def test_reset_after_a_malfunction(self):
        g = self.rig()
        g.press('key_start')
        g.step(120.0)
        g.call(g.E, 'diagnostics.inject_fault', code=39)
        self.assertEqual((g.screen(), g.state('programme.status'), g.state('door.locked'), g.state('program.running')), ('fault', 'malfunction', True, False))
        self.assertEqual(g.windows(), [('BLOCK E39', 'red'), ('no tank heating', 'red')])
        self.assertEqual(g.state('buzzer.state'), {'tone': 'alarm', 'volume': 25, 'until_s': None})
        self.assertEqual(g.state('history.records')[-1]['result'], 'FAULT 39')
        self.assertFalse(g.press('key_start'))                                          # START alone does nothing
        self.assertTrue(g.press('key_stop', hold=5.0, held=['key_start']))              # STOP + START 5 s (p.41)
        self.assertEqual((g.screen(), g.windows()[1][0]), ('reset', 'P2  >  P1'))
        self.assertTrue(g.press('key_p2'))
        self.assertTrue(g.press('key_p1'))
        self.assertEqual((g.screen(), g.state('programme.status'), g.state('faults.active'), g.state('door.locked'), g.state('buzzer.state')),
                         ('home', 'ready', None, False, None))
        g.call(g.E, 'diagnostics.inject_fault', code=27)                               # a wrong order keeps the fault
        g.press('key_start', hold=5.0, held=['key_stop'])
        self.assertFalse(g.press('key_p1'))
        self.assertEqual((g.screen(), g.state('programme.status')), ('fault', 'malfunction'))
        DONE_TASKS.add('reset after a malfunction')

    def test_print_a_stored_batch_protocol(self):
        g = self.rig()
        g.press('key_pplus', times=9)                                                   # COLD WATER RINSE: one 2 min block
        self.assertEqual(g.state('programme.selected'), 'cold_water_rinse')
        g.press('key_start')
        g.run_to_end()
        g.menu(3)                                                                       # History (no password, p.44)
        self.assertEqual(g.item(), 'hist_view')
        g.press('key_p1')
        self.assertTrue(g.press('key_start'))                                           # Print
        self.assertEqual((g.screen(), g.lines()[3]), ('printing', 'Printing in progress'))
        self.assertEqual(g.state('documentation.printing')['cycle'], 1)
        self.assertFalse(g.press('key_start'))
        g.step(19.0, dt=1.0)
        self.assertEqual(g.screen(), 'printing')
        g.step(1.0, dt=1.0)
        self.assertEqual(g.screen(), 'menu')
        out = g.state('documentation.serial_output')
        self.assertEqual(out[-1]['lines'][:3], ['PLW 6111', 'CYCLE 00001', 'PROGRAMME COLD WATER RINSE'])
        self.assertEqual(g.item(), 'standard_programmes')
        g.press('key_p1', times=3)                                                      # History > Print again, cancelled with STOP: menu exited
        g.press('key_start')
        g.press('key_p1')
        g.press('key_start')
        self.assertEqual(g.screen(), 'printing')
        self.assertTrue(g.press('key_stop'))
        self.assertEqual((g.screen(), g.state('documentation.printing')), ('home', None))
        g.step(30.0)
        self.assertEqual(len(g.state('documentation.serial_output')), 1)
        DONE_TASKS.add('print a stored batch protocol')

    def test_change_a_parameter(self):
        g = self.rig()
        g.menu(4)                                                                       # Settings: password first (p.43)
        g.password()
        self.assertEqual((g.screen(), g.item()), ('menu', 'par_user_name'))
        g.to_item('par_buzzer_key')
        self.assertIn('P1.07 buzzer key        25', g.lines())
        g.press('key_start')
        g.press('key_p1', times=25)                                                     # P1 decreases (p.43)
        self.assertEqual(g.frame()['ui']['editor']['buffer'], {'par_buzzer_key': 0})
        g.press('key_start')
        self.assertEqual(g.state('settings.values', 'buzzer_key'), 0)
        self.assertIn('P1.07 buzzer key         0', g.lines())
        self.assertFalse(g.press('key_stop'))                                           # a short STOP does not leave programming
        self.assertEqual(g.screen(), 'menu')
        self.assertTrue(g.press('key_stop', hold=5.0))                                  # STOP 5 s
        self.assertEqual(g.screen(), 'home')
        beeps = g.frame()['ui']['beeps']
        g.press('key_p3')
        self.assertEqual(g.frame()['ui']['beeps'], beeps)                               # key sound off
        DONE_TASKS.add('change a parameter')

    def test_view_temperature_or_warnings_during_a_run(self):
        g = self.rig()
        g.press('key_start')
        g.step(300.0)
        g.switch('off')                                                                 # power failure during the programme
        self.assertEqual((g.screen(), g.state('programme.status'), g.state('door.locked')), ('off', 'power_fail', True))
        g.step(60.0)
        block = g.raw()['state']['programme.block']['block']
        g.switch('on')                                                                  # the current phase is repeated (p.41)
        self.assertEqual((g.screen(), g.state('programme.status'), g.state('program.phase_elapsed_s')), ('run', 'running', 0.0))
        self.assertEqual(g.state('programme.block')['block'], block)
        self.assertEqual(g.lines()[12], 'power fail')
        self.assertTrue(g.press('key_prg'))                                             # PRG once: probes (Fig. 2)
        t = g.state('water.indicated_temperature_k') - C0
        lines = g.lines()
        self.assertEqual((g.screen(), lines[2], lines[3], lines[4]), ('run_temps', f'tank probe       {t:5.1f}°C', f'tank probe 2     {t:5.1f}°C', 'drying probe      20.0°C'))
        # (2026-10-19 sync fix: the drying probe reads the drying-air node, in a wash block the room temperature as in Fig. 2, no longer --.-)
        self.assertTrue(g.press('key_prg'))                                             # PRG twice: warnings (Fig. 3)
        self.assertEqual((g.screen(), g.lines()[2]), ('run_warn', 'power fail'))
        self.assertTrue(g.press('key_prg'))
        self.assertEqual(g.screen(), 'run')
        g.press('key_prg')
        self.assertTrue(g.press('key_stop'))                                            # STOP works on the information pages too
        self.assertEqual(g.screen(), 'interrupted')
        DONE_TASKS.add('view temperature or warnings during a run')


# ---------------------------------------------------------------- the other menu functions
class OtherFunctions(Base):
    def test_standard_programme_view_and_copy_then_customer_edit_and_run(self):
        g = self.rig()
        g.menu(0)
        g.password()
        g.to_item('std_plastic')
        g.press('key_start')
        lines = g.lines()
        self.assertEqual(lines[:7], ['PLASTIC', "pre-wash           CW50 1'", "main wash      CW50 55° 3'", "neutralis.         CW50 2'",
                                     "interim 1          DI50 1'", "final rinse      DI 55° 1'", "drying             60° 30'"])   # p.36
        g.to_item('copy')
        g.press('key_start')
        g.press('key_p2', times=4)                                                      # to C5
        g.press('key_start')
        self.assertEqual(g.state('programmes.customer')[4]['base'], 'plastic')
        g.press('key_stop')
        g.press('key_stop')
        g.press('key_p1')
        g.press('key_start')                                                            # Customer programmes (password given in this visit)
        self.assertEqual((g.screen(), g.item()), ('menu', 'cust_01'))
        g.to_item('cust_05')
        self.assertIn('CUSTOMER 05', g.lines())
        g.press('key_start')
        self.assertEqual(g.screen(), 'cust_edit')
        g.to_item('block_main_wash')
        g.press('key_start')                                                            # active on, 55 -> 45 degC, 3 -> 1 min
        g.press('key_start')
        g.press('key_p1', times=10)
        g.press('key_start')
        g.press('key_p1', times=2)
        g.press('key_start')
        self.assertEqual(g.state('programmes.customer')[4]['blocks']['main_wash'], {'active': True, 'temperature_C': 45, 'time_min': 1, 'water': 'CW50', 'dosing': 'DOS1'})
        self.assertIn("main wash           45° 1'", g.lines())
        g.to_item('block_drying')
        g.press('key_start')
        g.press('key_p1')                                                               # drying off
        g.press('key_start', times=3)
        g.press('key_stop', hold=5.0)
        g.press('key_pplus', times=17)                                                  # the customer programme follows the 18 standard ones
        self.assertEqual((g.state('programme.selected'), g.lines()[2].strip()), ('C05', 'CUSTOMER 05'))       # drying off: no DRY
        g.press('key_start')
        g.step(240.0, dt=10.0)
        self.assertEqual(g.state('programme.block')['block'], 'main_wash')
        self.assertAlmostEqual(g.state('thermal.target_k'), 45 + C0)
        g.run_to_end()
        self.assertEqual(g.state('history.records')[-1]['name'], 'CUSTOMER 05')
        # create an empty position from a standard programme, then delete it
        g.menu(1)
        g.password()
        g.to_item('cust_02')
        g.press('key_start')
        self.assertEqual((g.screen(), g.lines()[0]), ('cust_new', 'C02 empty'))
        g.press('key_start')
        g.press('key_p1', times=2)                                                      # STANDARD
        g.press('key_start')
        self.assertEqual((g.screen(), g.state('programmes.customer')[1]['base']), ('cust_edit', 'standard'))
        g.to_item('delete')
        g.press('key_start')
        self.assertIsNone(g.state('programmes.customer')[1])
        self.assertEqual((g.screen(), g.item()), ('menu', 'cust_02'))

    def test_programme_selection(self):
        g = self.rig()
        g.menu(2)
        g.password()
        g.to_item('sel_standard')
        g.press('key_start')
        g.press('key_p1')
        g.press('key_start')
        self.assertFalse(g.state('programmes.active', 'standard'))
        self.assertIn('STANDARD               OFF', g.lines())
        g.press('key_stop', times=2)
        self.assertFalse(g.press('key_p2'))                                             # deactivated: P2 does nothing
        self.assertEqual(g.state('programme.selected'), 'universal')
        g.press('key_pplus')
        self.assertEqual(g.state('programme.selected'), 'intensive')                    # P+ skips it
        g.call(g.A, 'programmes.set_active', programme_id='intensive', active=False)    # the selected one: the first active is chosen
        self.assertEqual(g.state('programme.selected'), 'quick_wash')

    def test_user_name_language_a0_and_clock(self):
        g = self.rig()
        g.menu(4)
        g.password()
        g.press('key_start')                                                            # P1.01: characters with P2 / P1, START to the next
        for ch in 'LAB 7':
            g.press('key_p2', times=rt._MIELE_NAME_CHARS.index(ch))
            g.press('key_start')
        g.press('key_start', times=16 - 5)
        self.assertEqual(g.state('settings.values', 'user_name'), 'LAB 7')
        g.to_item('par_show_a0')
        g.press('key_start')
        g.press('key_p1')
        g.press('key_start')
        g.to_item('clock_date')
        g.press('key_start')
        g.press('key_p2', times=8)                                                      # 9.2.2026
        g.press('key_start')
        g.press('key_p2')
        g.press('key_start', times=2)
        g.to_item('clock_time')
        g.press('key_start')
        g.press('key_p2', times=20)
        g.press('key_start')
        g.press('key_p2', times=32)
        g.press('key_start')
        self.assertTrue(g.state('clock.datetime').startswith('2026-02-09T20:32'))
        g.to_item('par_language')
        g.press('key_start')
        g.press('key_p2', times=2)                                                      # 2 Deutsch
        g.press('key_start')
        g.press('key_stop', hold=5.0)
        lines = g.lines()
        self.assertEqual((lines[0][:14], lines[5], lines[12]), ('09.02.26 20:32', 'T1  20.0°C', 'Start druecken'))
        g.press('key_start')
        g.run_to_end()
        self.assertEqual(g.windows()[1], ('Programmende', 'green'))
        self.assertEqual(g.state('history.records')[-1]['user'], 'LAB 7')

    def test_end_of_programme_printout_live_results_and_on_print(self):
        g = self.rig()
        g.call(g.A, 'settings.configure', printout=2, live_printout=1)
        g.press('key_pplus', times=10)                                                  # AD RINSE: one block
        g.press('key_start')
        g.step(120.0, dt=10.0)
        self.assertEqual(g.state('programme.status'), 'end')
        self.assertEqual(g.state('documentation.serial_output')[-1], {'cycle': 1, 'format': 'live', 'lines': ['pre-wash 120 s']})
        self.assertEqual(g.state('documentation.printing')['format'], 'table')
        self.assertIn('Printing in progress', g.lines())
        self.assertFalse(g.press('key_start'))                                          # START during the printout: ON PRINT (p.47)
        self.assertEqual(g.lines()[12], 'ON PRINT')
        g.step(20.0, dt=5.0)
        self.assertEqual(g.state('documentation.serial_output')[-1]['format'], 'table')
        self.assertTrue(g.press('key_start'))

    def test_drying_key_shortens_the_programme_and_settings_survive(self):
        g = self.rig()
        g.press('key_dry')
        g.press('key_start')
        self.assertEqual(g.state('programme.block')['count'], 5)                        # no drying and cooling blocks
        t = g.run_to_end()
        self.assertLess(t, 1800.0)
        self.assertFalse(g.state('programme.drying'))
        g.press('key_p1')                                                               # choosing the programme again restores its drying
        self.assertTrue(g.state('programme.drying'))
        g.press('key_pplus', times=11)
        self.assertEqual(g.state('programme.selected'), 'drying')
        self.assertFalse(g.press('key_dry'))                                            # the Drying programme cannot switch drying off


class SyncFixes(Base):
    """2026-10-19 sync fixes: drying-air probe, the pressure probe message by evaluator injection, P2.07 font, panel caller."""

    def test_drying_probe_follows_the_drying_unit(self):
        g = self.rig()
        g.press('key_pplus', times=11)
        self.assertEqual(g.state('programme.selected'), 'drying')
        g.press('key_start')
        g.step(600.0)
        self.assertEqual(g.state('programme.block')['block'], 'drying')
        air = g.state('drying.indicated_air_temperature_k') - C0
        self.assertAlmostEqual(air, 120.0 - 100.0 * math.exp(-5.0), delta=0.06)       # first order towards 120 C (tau 120 s, assumption)
        g.press('key_prg')                                                              # PRG once: the probes (Fig. 2)
        self.assertEqual((g.screen(), g.lines()[4]), ('run_temps', f'drying probe     {air:5.1f}°C'))
        g.press('key_prg', times=2)
        g.step(1200.0)
        self.assertEqual(g.state('programme.block')['block'], 'cooling')
        self.assertAlmostEqual(g.state('drying.indicated_air_temperature_k') - C0, 120.0, delta=0.1)
        g.run_to_end()                                                                  # 120 s cooling with the fan: room air
        cooled = g.state('drying.indicated_air_temperature_k') - C0
        self.assertLess(cooled, 60.0)
        self.assertGreater(cooled, 50.0)
        self.assertNotIn('drying.air_temperature_k', g.A.read(instance_id=INST)['observations'])   # the truth stays debug

    def test_pressure_probe_message_by_evaluator_injection(self):
        g = self.rig()
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='pressure_probe')['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('fault.inject', g.A.describe(instance_id=INST)['capabilities'])
        g.call(g.E, 'fault.inject', code='pressure_probe')
        self.assertEqual(g.state('messages.active'), ['pressure probe'])
        self.assertEqual(g.lines()[12], 'pressure probe')                              # bottom-left message (p.31, p.47)
        self.assertTrue(g.press('key_start'))                                           # a message does not stop the machine
        self.assertEqual(g.state('programme.status'), 'running')
        g.step(120.0)
        self.assertEqual(g.lines()[12], 'pressure probe')
        g.press('key_prg', times=2)
        self.assertEqual((g.screen(), g.lines()[2]), ('run_warn', 'pressure probe'))
        g.call(g.E, 'fault.clear', code='pressure_probe')
        self.assertEqual(g.call(g.E, 'fault.clear', expect='rejected', code='pressure_probe')['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(g.state('messages.active'), [])
        self.assertIsNone(g.h._mp_panel_read(INST)['host.warning_1'])

    def test_font_type_is_stored_and_draws_the_european_table(self):
        g = self.rig()
        before = g.h.panel(INST).png_bytes('display')
        g.call(g.A, 'settings.configure', font=2)
        self.assertEqual(g.state('settings.values', 'font'), 2)
        self.assertEqual(g.h.panel(INST).png_bytes('display'), before)                 # every simulated text is in the European table

    def test_another_callers_run_cannot_be_stopped_or_switched_off(self):
        """Lead rule 2026-10-19: B cannot interrupt or cancel A's programme (panel or direct); A interrupts its own; the evaluator is
        unrestricted.  The power switch is a hard switch: never refused, the machine reacts as to a power failure (lead clarification 2026-10-19)."""
        g = self.rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        g.press('key_start')                                                           # A starts the programme on the panel
        self.assertEqual(g.state('programme.status'), 'running')
        before = state_dump(g.h)
        for cap, args in (('programme.interrupt', {}), ('programme.cancel', {}), ('washing.stop', {})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertFalse(g.call(B, 'panel.press', input_id='key_stop', duration_s=0.1)['accepted'])   # B's STOP key: refused
        self.assertEqual(g.lines()[12], 'access denied')                               # shown on the message line (frame and pixels)
        self.assertEqual(state_dump(g.h), before)
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])   # physical: power failure
        self.assertEqual(g.state('programme.status'), 'power_fail')
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='on')['accepted'])
        self.assertEqual(g.state('programme.status'), 'running')                       # the programme goes on (p.41, p.45), still A's
        self.assertTrue(g.press('key_stop'))                                           # A interrupts its own programme
        self.assertEqual(g.state('programme.status'), 'interrupted')
        g.call(g.E, 'programme.cancel')                                                # the evaluator is unrestricted
        self.assertEqual(g.state('programme.status'), 'cancelled')

    def test_panel_gesture_acts_as_the_calling_session(self):
        from unittest import mock
        g = self.rig()
        callers, real = [], rt.MielePLW6111Session._checked

        def spy(sess, *a, **k):
            callers.append((sess, a[2]))
            return real(sess, *a, **k)
        with mock.patch.object(rt.MielePLW6111Session, '_checked', autospec=True, side_effect=spy):
            g.press('key_start')
        self.assertEqual(g.state('programme.status'), 'running')
        self.assertIn('programme.start', [c for _, c in callers])
        self.assertTrue(all(s is g.A for s, c in callers if c != 'panel.frame'))
        self.assertIsNone(g.h._panel_caller)


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(Base):
    def test_programme_and_menu_functions(self):
        a, b = self.rig(), self.rig()
        a.press('key_p3')
        a.press('key_dry')
        a.press('key_start')
        b.call(b.A, 'programme.select', programme_id='intensive')
        b.call(b.A, 'programme.set_drying', enabled=False)
        b.call(b.A, 'programme.start')
        self.assertEqual(state_dump(a.h), state_dump(b.h))
        for g in (a, b):
            g.step(400.0)
        a.press('key_stop')
        a.press('key_stop')
        b.call(b.A, 'programme.interrupt')
        b.call(b.A, 'programme.cancel')
        self.assertEqual(state_dump(a.h), state_dump(b.h))
        a.menu(4)
        a.password()
        a.to_item('par_buzzer_end')
        a.press('key_start')
        a.press('key_p2', times=5)
        a.press('key_start')
        b.call(b.A, 'settings.configure', buzzer_end=30)
        self.assertEqual(state_dump(a.h), state_dump(b.h))


# ---------------------------------------------------------------- the controller functions behind the panel
class DeviceFunctions(Base):
    def test_a0_at_constant_temperature(self):
        g = self.rig()
        g.call(g.A, 'programmes.copy', source='disin_93_10', position=1)
        for bid in ('neutralisation', 'interim_rinse_1', 'final_rinse', 'drying'):
            g.call(g.A, 'programmes.set_block', position=1, block=bid, active=False, temperature_C=0, time_min=0)
        g.call(g.A, 'programme.select', programme_id='C01')
        g.call(g.A, 'programme.start')
        heat = 167200.0 / 5.0 * math.log((293.15 + 1500.0 - 293.15) / (293.15 + 1500.0 - 366.15))   # exact heat-up to 93 degC
        g.h.step(heat)
        a0_heat = g.state('programme.a0')
        g.h.step(600.0)
        self.assertEqual(g.state('programme.status'), 'end')
        self.assertAlmostEqual(g.state('programme.a0') - a0_heat, 600 * 10 ** 1.3, delta=1e-6 * 600 * 10 ** 1.3)
        self.assertEqual(g.state('history.records')[-1]['a0'], int(math.floor(g.state('programme.a0') + 0.5)))

    def test_refusals_leave_state_unchanged(self):
        g = self.rig(water=False)
        before = state_dump(g.h)
        self.assertFalse(g.press('key_start'))                                          # no water model: no water supply
        self.assertEqual(g.lines()[12], 'no cold water')
        self.assertEqual(g.call(g.A, 'programme.start', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(state_dump(g.h), before)
        g.press('key_pplus', times=12)                                                  # Drying needs no water
        self.assertTrue(g.press('key_start'))
        g.press('key_stop', times=2)
        g = self.rig()
        g.call(g.A, 'door.open')
        before = state_dump(g.h)
        self.assertFalse(g.press('key_start'))
        self.assertEqual(g.lines()[12], 'close door!')
        g.step(3.0, dt=1.0)                                                             # the message stays 3 s
        self.assertEqual(g.lines()[12], '- open door -')
        self.assertEqual(state_dump(g.h), before)
        for cap, args, code in [('programme.select', {'programme_id': 'C07'}, 'PRECONDITION_FAILED'), ('programmes.delete', {'position': 3}, 'PRECONDITION_FAILED'),
                                ('programme.resume', {}, 'PRECONDITION_FAILED'), ('faults.reset', {}, 'PRECONDITION_FAILED'), ('history.print', {'cycle': 1}, 'PRECONDITION_FAILED'),
                                ('settings.configure', {}, 'INVALID_ARGUMENT'), ('settings.configure', {'user_name': 'lower case'}, 'INVALID_ARGUMENT'),
                                ('programmes.set_block', {'position': 1, 'block': 'main_wash', 'active': True, 'temperature_C': 120, 'time_min': 1}, 'INVALID_ARGUMENT'),
                                ('clock.set_date', {'day': 31, 'month': 2, 'year': 2026}, 'INVALID_ARGUMENT'), ('diagnostics.inject_fault', {'code': 39}, 'ACCESS_DENIED')]:
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], code, cap)
            self.assertEqual(state_dump(g.h), before, cap)
        self.assertEqual(g.call(g.E, 'diagnostics.inject_fault', expect='rejected', code=3)['error']['code'], 'INVALID_ARGUMENT')
        g.call(g.A, 'door.close')
        g.call(g.A, 'programme.start')
        for cap, args in [('thermal.set_temperature', {'temperature_K': 330.0}), ('washing.start', {}), ('programme.select', {'programme_id': 'oil'}),
                          ('programmes.copy', {'source': 'oil', 'position': 1})]:
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'BUSY', cap)
        g.call(g.A, 'programme.interrupt')
        self.assertEqual(g.call(g.A, 'washing.start', expect='rejected')['error']['code'], 'BUSY')
        g.call(g.A, 'washing.stop')                                                     # the direct stop cancels a panel programme
        self.assertEqual((g.state('programme.status'), g.state('history.records')[-1]['result']), ('cancelled', 'NO DISINFECTION'))

    def test_history_keeps_200_and_host_timed_programmes_show(self):
        g = self.rig()
        g.call(g.A, 'programme.select', programme_id='drain')
        for _ in range(203):
            g.call(g.A, 'programme.start')
            g.h.step(60.0)
        recs = g.state('history.records')
        self.assertEqual((len(recs), recs[0]['cycle'], recs[-1]['cycle']), (200, 4, 203))
        g.call(g.A, 'thermal.set_temperature', temperature_K=313.15)                    # a host-timed programme (washing.*) on the same display
        g.call(g.A, 'washing.configure_program', wash_s=60, rinse_s=30, dry_s=30)
        g.call(g.A, 'washing.start')
        lines = g.lines()
        self.assertEqual((g.screen(), lines[2].strip(), lines[3]), ('run', 'HOST TIMER', 'wash'))
        self.assertTrue(g.press('key_stop'))
        self.assertEqual((g.state('program.running'), g.state('program.phase'), g.screen()), (False, 'stopped', 'home'))


# ---------------------------------------------------------------- rules
class Rules(Base):
    def test_power_gate_password_and_unsupported(self):
        g = self.rig(on=False)
        for key in ('key_start', 'key_prg', 'key_p1'):
            self.assertFalse(g.press(key))
        self.assertEqual(g.screen(), 'off')
        g.switch('on')
        self.assertFalse(g.press('key_prg'))                                            # a short PRG does not open the menu
        g.menu(4)
        g.password(1111)                                                                # a wrong password closes the menu (p.43)
        self.assertEqual(g.screen(), 'home')
        g.menu(3)                                                                       # History needs no password
        self.assertEqual(g.item(), 'hist_view')
        self.assertFalse(g.press('key_start'))                                          # no protocol stored yet
        g.press('key_stop', times=2)
        self.assertEqual(g.screen(), 'home')
        g.menu(4)
        g.password()
        g.to_item('service_level')
        self.assertFalse(g.press('key_start'))
        self.assertEqual(g.frame()['ui']['unsupported'], 'SERVICE level: Miele service password')
        with self.assertRaises(AssertionError):
            g.call(g.A, 'panel.press', input_id='key_x')

    def test_time_outs(self):
        g = self.rig()
        g.call(g.A, 'settings.configure', buzzer_end=40)
        g.press('key_pplus', times=10)
        g.press('key_start')
        g.step(120.0, dt=10.0)
        self.assertEqual(g.state('buzzer.state'), {'tone': 'end', 'volume': 40, 'until_s': 120.0 + 10.0})
        g.step(9.0, dt=1.0)
        self.assertIsNotNone(g.state('buzzer.state'))
        g.step(1.0, dt=1.0)
        self.assertIsNone(g.state('buzzer.state'))
        g.call(g.A, 'door.open')
        g.press('key_start')
        g.step(2.9, dt=0.1)
        self.assertEqual(g.lines()[12], 'close door!')
        g.step(0.1, dt=0.1)
        self.assertEqual(g.lines()[12], '- open door -')


# ---------------------------------------------------------------- rendering, capture, contract
class RenderAndContract(Base):
    def test_render_determinism_and_pngs(self):
        a, b = self.rig(), self.rig()
        for g in (a, b):
            g.press('key_p1')
        self.assertEqual(a.h.panel(INST).png_bytes('display'), b.h.panel(INST).png_bytes('display'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel(INST).save_png('display', OUT / 'home.png')}
        a.press('key_start')
        a.step(900.0)
        shots['run'] = a.h.panel(INST).save_png('display', OUT / 'run.png')
        a.press('key_stop')
        shots['stop'] = a.h.panel(INST).save_png('display', OUT / 'stop.png')
        a.press('key_start')
        a.run_to_end()
        shots['end'] = a.h.panel(INST).save_png('display', OUT / 'end.png')
        a.menu(4)
        a.password()
        shots['menu'] = a.h.panel(INST).save_png('display', OUT / 'menu.png')
        a.press('key_stop', hold=5.0)
        a.press('key_start')
        a.step(60.0)
        a.call(a.E, 'diagnostics.inject_fault', code=39)
        shots['error'] = a.h.panel(INST).save_png('display', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = self.rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='display')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        g.switch('on')
        self.assertNotEqual(g.call(g.A, 'panel.capture', display_id='display')['image']['sha256'], r['image']['sha256'])

    def test_spec_and_contract(self):
        meta = rt.export_miele_plw6111_metadata()
        spec_ = meta['asset_types'][rt.MIELE_PLW6111_TYPE]
        callable_caps = set(spec_['capabilities']) | set(spec_['controller_capabilities'])
        self.assertEqual(pu.check_spec(SPEC, callable_caps), [])
        self.assertEqual(set(spec_['panel_capabilities']), {'panel.press', 'panel.switch', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], spec_['asset_version'])
        for k, v in {**spec_['controller_capabilities'], **spec_['evaluator_capabilities'], **spec_['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for k, cap in c['capabilities'].items():
            for comp in cap['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for sid in spec_['state_slots']:
            self.assertIn(sid, c['state_slots'])
        for key in ('key_stop', 'key_start', 'key_pplus', 'key_prg', 'key_p1', 'key_p2', 'key_p3', 'key_dry', 'power_switch'):
            self.assertIn(key, c['components'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'text_menu', pu.SPEC_VERSION))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('miele_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
