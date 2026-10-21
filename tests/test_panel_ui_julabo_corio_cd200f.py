"""front-panel tests for CHEM-187 julabo_corio_cd200f (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_thermal.py):
python3 -m pytest tests/test_panel_ui_julabo_corio_cd200f.py -q

Every gesture goes through the session API (panel.press / panel.turn / panel.switch, the runtime's normal invoke path).  Each check looks at the
runtime state and at the screen twice: the structured frame (panel.frame) and the rendered pixels, read back with a seven-segment reader that
knows only the display geometry of the spec (and the LED colours).  Themes: the seven key procedures of the menu survey plus every other panel
function of the manual (power supply, autostart, ATC, black box, flow lever); panel = direct capability (same runtime state); the device
functions behind the panel (timer band, cooling modes, hLO limit, cut-off alarm, sensor error and ATC, recording, black box); refusals and locks
(power off, boot, remote mode, alarm, missing flash drive, ATC outside +-5 C); time-outs (lamp test, entries, messages); render determinism and
the PNGs of outputs/panel_ui/julabo_corio_cd200f/; contract entries of the panel capabilities."""
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

INST = 'julabo_corio_cd200f_01'
C0 = 273.15
OUT = ROOT / 'outputs/panel_ui/julabo_corio_cd200f'
ASSET = ROOT / 'assets/basic40_batch02/t1_thermal/julabo_corio_cd200f'
SPEC = rt._julabo_panel()['spec']
DISPLAY = SPEC['displays']['display']
LEDS = ('heating_led', 'cooling_led', 'alarm_led')
SURVEY_TASKS = ['set setpoint to 25.5 C', 'start tempering', 'set the high temperature cut-off', 'program a 60 min timer', 'select cooling mode',
                'enable remote control', 'record data to USB stick']
DONE_TASKS = set()
_DIGITS = {v: k for k, v in pu.SEG7.items() if k in '0123456789'}
_LETTERS = {v: k for k, v in pu.SEG7.items() if k not in '0123456789' and v}
BATH = dict(mass_kg=3.5, specific_heat_J_kg_K=3600.0, heat_loss_W_K=1.0, lid_open_loss_W_K=0.0, initial_temperature_K=293.15,
            minimum_temperature_K=253.15, maximum_temperature_K=373.15, evidence='Synthetic 3.5 kg water-glycol bath; explicit test assumption')


# ---------------------------------------------------------------- reading the rendered display
def _lit(img, x, y):
    px = img[int(round(y)), int(round(x))]
    return int(px[0]) + int(px[1]) + int(px[2]) > 400


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
        out += _DIGITS.get(segs, _LETTERS.get(segs, ' ' if not segs else '?'))
        r = max(1.5, t * 0.6)
        if _lit(img, x + cw + gap / 2.0, f['y'] + ch - r):
            out += '.'
    return out.strip()


def norm(text):
    """What the pixels can tell apart: the lit segments of each cell (S and 5 look the same), and the points."""
    return tuple(ch if ch == '.' else pu.SEG7.get(ch, pu.SEG7.get(ch.upper(), pu.SEG7.get(ch.lower(), ''))) or '_' for ch in text.replace(' ', ''))


# ---------------------------------------------------------------- rig
class Rig:
    def __init__(self, configured=True, ambient_temperature_K=293.15, **bath):
        self.h = rt.JulaboCorioCD200FRuntime(ambient_temperature_K=ambient_temperature_K)
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if configured:
            self.h.configure_bath_fluid(INST, **dict(BATH, **bath))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0                                                     # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    # gestures (agent session)
    def press(self, key, hold=0.1, held=()):
        args = dict(input_id=key, duration_s=hold)
        if held:
            args['held'] = list(held)
        return self.call(self.A, 'panel.press', **args)['accepted']

    def hold(self, key):
        return self.press(key, 2.0)

    def switch(self, pos, held=()):
        args = dict(input_id='power_switch', position=pos)
        if held:
            args['held'] = list(held)
        return self.call(self.A, 'panel.switch', **args)['accepted']

    def lever(self, pos):
        return self.call(self.A, 'panel.switch', input_id='flow_lever', position=pos)['accepted']

    def screw(self, n):
        return self.call(self.A, 'panel.turn', input_id='cutoff_screw', detents=n)['accepted']

    # screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def main(self):
        return self.frame()['displays']['display']['fields']['main']

    def lcd(self):
        """The display text shown now, from the frame and from the pixels (both must agree)."""
        shown = self.main()['shown'].strip()
        read = read_field(self.h.panel(INST).render('display'))
        assert norm(shown) == norm(read), (shown, read)
        return shown

    def leds(self):
        on = {k for k in LEDS if self.frame()['displays'][k]['shown']}
        lit = {k for k in LEDS if tuple(self.h.panel(INST).render(k)[16, 16]) == tuple(SPEC['displays'][k]['colors']['on'])}
        assert on == lit, (on, lit)
        return on

    def screen(self):
        return self.frame()['screen']

    def step(self, s):
        while s > 1e-9:
            self.h.step(min(s, 3600.0))
            s -= min(s, 3600.0)

    def state(self, *ids):
        o = self.E.read(instance_id=INST, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def on(self, held=()):
        assert self.switch('on', held)
        assert self.screen() == 'boot'
        self.step(1.0)                                                                 # lamp test

    def remote(self):
        """Remote mode rOn for the agent's direct device commands, which stand for the USB interface (OUT commands only in rOn, manual p.83-84;
        sweep 2026-10-19).  The setting survives switching off; switched on and off again at once when the unit is off."""
        powered = self.state('power.enabled')
        if not powered:
            self.call(self.A, 'power.set', enabled=True)
        self.call(self.A, 'interface.set_remote', enabled=True)
        if not powered:
            self.call(self.A, 'power.set', enabled=False)

    def direct(self, cap, expect='succeeded', **args):
        """One direct device command of the agent over the remote interface (unit switched on): rOn for the command, then rOFF again."""
        self.call(self.A, 'interface.set_remote', enabled=True)
        r = self.call(self.A, cap, expect=expect, **args)
        self.call(self.A, 'interface.set_remote', enabled=False)
        return r

    def set_point(self, celsius):
        """Arrows and OK only: open, integer stage, decimal stage, apply."""
        assert self.press('key_up')
        cur = self.frame()['ui']['editor']['buffer']['whole']
        whole = int(celsius) - int(cur)
        for _ in range(abs(whole)):
            self.press('key_up' if whole > 0 else 'key_down')
        self.press('key_ok')
        tenth = round((celsius - self.frame()['ui']['editor']['buffer']['tenth']) * 10)
        for _ in range(abs(tenth)):
            self.press('key_up' if tenth > 0 else 'key_down')
        self.press('key_ok')
        self.press('key_ok')
        assert self.screen() == 'home'
        self.step(2.0)                                                                 # the new set point flashes briefly

    def wait_for(self, sid, value, limit_s=7200.0, dt=10.0):
        t = 0.0
        while self.state(sid) != value:
            assert t < limit_s, (sid, value, self.state(sid))
            self.step(dt)
            t += dt
        return t


def runtime_state(h, ignore=()):
    s = h.debug_snapshot()['instances'][INST]
    state = {k: v for k, v in s['state'].items() if k not in ignore}
    return json.dumps(dict(state=state, timer=s['timer'], drive=h._drives[INST], box=h._blackbox[INST]), sort_keys=True, default=float)


REMOTE = ('interface.remote',)          # the direct side of an equivalence test works over the remote interface (rOn); the panel side does not


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_set_setpoint_25_5(self):
        g = Rig()
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('off', '', set()))
        self.assertTrue(g.switch('on'))
        self.assertEqual((g.screen(), g.lcd()), ('boot', '8.8.8.8.8.'))              # all display elements light up briefly
        self.assertEqual(g.leds(), set(LEDS))
        self.assertFalse(g.press('key_up'))                                            # keys do nothing while it boots
        g.step(1.0)
        self.assertEqual((g.screen(), g.lcd(), g.leds()), ('home', 'OFF', set()))    # ready: OFF
        self.assertTrue(g.press('key_up'))                                             # the stored set point, integer part flashing
        m = g.main()
        self.assertEqual((m['text'], m['blink_mask']), ('20.0', '1100'))
        self.assertEqual(g.state('thermal.target_k'), C0 + 20.0)                      # the first press changes nothing
        for _ in range(5):
            g.press('key_up')
        self.assertEqual(g.lcd(), '25.0')
        self.assertTrue(g.press('key_ok'))                                             # integer accepted: the decimal place flashes
        self.assertEqual(g.main()['blink_mask'], '0001')
        for _ in range(5):
            g.press('key_up')
        self.assertEqual(g.main()['text'], '25.5')
        self.assertTrue(g.press('key_ok'))
        m = g.main()
        self.assertEqual((m['text'], m['blink'], g.state('thermal.target_k')), ('25.5', False, C0 + 20.0))
        self.assertTrue(g.press('key_ok'))                                             # OK twice: applied, the new set point flashes
        self.assertEqual(g.state('thermal.target_k'), 298.65)
        m = g.main()
        self.assertEqual((g.screen(), m['text'], m['blink']), ('home', '25.5', True))
        g.step(0.5)
        self.assertEqual(g.lcd(), '')                                                  # off phase of the flash
        g.step(1.5)
        self.assertEqual(g.lcd(), 'OFF')
        DONE_TASKS.add('set setpoint to 25.5 C')

    def test_start_tempering(self):
        g = Rig()
        g.on()
        g.set_point(40.0)
        self.assertFalse(g.press('key_ok'))                                            # a short press in standby does nothing
        self.assertTrue(g.hold('key_ok'))                                              # hold OK until control begins
        self.assertTrue(g.state('circulation.running'))
        m = g.main()
        self.assertEqual((m['text'], m['blink']), ('20.00', True))                     # the display flashes briefly
        self.assertEqual(g.leds(), {'heating_led'})
        g.step(60.0)
        self.assertAlmostEqual(float(g.lcd()), 20.0 + (2000.0 * 60 - 1.0 * 0) / 12600.0, delta=0.15)
        g.step(600.0)
        self.assertEqual(g.lcd(), '40.00')
        self.assertEqual(g.leds(), {'heating_led'})                                    # holding above ambient: heater only (AUTO)
        self.assertTrue(g.press('key_ok'))                                             # OK pauses temperature control
        self.assertEqual((g.state('circulation.running'), g.lcd(), g.leds()), (False, 'OFF', set()))
        DONE_TASKS.add('start tempering')

    def test_set_and_check_the_high_temperature_cut_off(self):
        g = Rig()
        g.on()
        self.assertEqual(g.state('safety.cutoff_k'), C0 + 250.0)
        self.assertTrue(g.screw(-200))                                                 # screwdriver: the display shows the set value
        self.assertEqual((g.state('safety.cutoff_k'), g.lcd()), (C0 + 50.0, '50'))
        g.step(2.0)
        self.assertEqual(g.lcd(), 'OFF')
        g.set_point(45.0)
        g.hold('key_ok')
        g.step(900.0)
        self.assertEqual(g.lcd(), '45.00')
        self.assertTrue(g.screw(-10))                                                  # check (p.69): below the actual value
        self.assertEqual(g.lcd(), '40')
        self.assertEqual(g.state('alarm.code'), 14)
        self.assertEqual((g.state('circulation.running'), g.leds()), (False, {'alarm_led'}))
        g.step(2.0)
        self.assertEqual(g.lcd(), 'E 14')
        self.assertTrue(g.press('key_ok'))                                             # OK silences the beep; control stays stopped
        self.assertTrue(g.frame()['ui']['vars']['muted'])
        self.assertTrue(g.hold('key_ok'))
        self.assertFalse(g.state('circulation.running'))
        g.call(g.A, 'circulation.set_running', expect='rejected', running=True)
        g.screw(30)                                                                    # above the actual value again: the alarm stays latched
        self.assertEqual(g.state('alarm.code'), 14)
        g.switch('off')                                                                # switching off resets it
        g.on()
        self.assertEqual((g.state('alarm.code'), g.lcd(), g.leds()), (0, 'OFF', set()))
        DONE_TASKS.add('set the high temperature cut-off')

    def test_program_a_60_min_timer(self):
        g = Rig()
        g.on()
        g.set_point(30.0)
        self.assertTrue(g.press('key_ok', held=['key_down']))                           # Down + OK: <t O>
        self.assertEqual(g.screen(), 'timer_edit')
        m = g.main()
        self.assertEqual((m['text'], m['blink_mask']), ('t   0', '01111'))
        self.assertTrue(g.press('key_up', hold=6.4))                                   # held: 1 + 59 repeats
        self.assertEqual(g.lcd(), 't  60')
        self.assertTrue(g.press('key_ok'))                                             # programmed: the display flashes briefly
        self.assertEqual(g.state('timer.duration_s', 'timer.phase'), {'timer.duration_s': 3600.0, 'timer.phase': 'waiting'})
        self.assertEqual((g.main()['text'], g.main()['blink']), ('t  60', True))
        g.step(2.0)
        self.assertEqual(g.lcd(), 'OFF')
        g.hold('key_ok')
        g.step(2.0)
        self.assertEqual(g.main()['blink_mask'], '00100')                               # the decimal point flashes until the timer starts
        start = g.wait_for('timer.phase', 'running', dt=1.0)                            # 30 s within 0.1 K of the set point
        self.assertGreater(start, 30.0)
        self.assertAlmostEqual(g.state('timer.remaining_s'), 3600.0, delta=1.0)
        g.step(2.0 - g.h.time_s % 2.0 + 2.0)                                           # the display alternates temperature / remaining time
        texts = set()
        for _ in range(2):
            texts.add(g.lcd())
            g.step(2.0)
        self.assertEqual(texts, {'30.00', 't  60'})
        g.step(3600.0 - 60.0 - 8.0)
        g.step(2.0 - g.h.time_s % 2.0 + 2.0)
        texts = {g.lcd()}
        g.step(2.0)
        texts.add(g.lcd())
        self.assertIn('30.00', texts)
        self.assertTrue(any(t.startswith('t') and int(t[1:]) < 60 for t in texts if t != '30.00'), texts)   # the last minute in seconds
        g.wait_for('timer.phase', 'off', dt=1.0)
        self.assertEqual((g.state('circulation.running'), g.lcd()), (False, 'OFF'))     # elapsed: standby
        DONE_TASKS.add('program a 60 min timer')

    def test_select_cooling_mode(self):
        g = Rig()
        self.assertEqual(g.state('cooling.mode'), 'auto')
        shown = []
        for _ in range(3):
            g.on(held=['key_down'])                                                    # Down + power switch: the mode is shown briefly
            shown.append((g.lcd(), g.state('cooling.mode')))
            g.step(2.0)
            self.assertEqual(g.lcd(), 'OFF')
            g.switch('off')
        self.assertEqual(shown, [('COFF', 'off'), ('CON', 'on'), ('AUTO', 'auto')])
        DONE_TASKS.add('select cooling mode')

    def test_enable_remote_control(self):
        g = Rig()
        g.on()
        self.assertTrue(g.press('key_up', held=['key_down']))                           # both arrows: <rOn>
        self.assertEqual((g.lcd(), g.state('interface.remote')), ('rOn', True))
        before = runtime_state(g.h)
        for gesture in (lambda: g.press('key_up'), lambda: g.hold('key_ok'), lambda: g.press('key_ok', held=['key_down'])):
            self.assertFalse(gesture())                                                # the device cannot be operated manually
            self.assertEqual((g.screen(), runtime_state(g.h)), ('home', before))
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 35.0)               # the remote side still works
        self.assertTrue(g.press('key_down', held=['key_up']))                           # the combination again: <rOFF>
        self.assertEqual((g.lcd(), g.state('interface.remote')), ('rOFF', False))
        self.assertTrue(g.press('key_up'))
        self.assertEqual((g.screen(), g.main()['text']), ('sp_edit', '35.0'))
        DONE_TASKS.add('enable remote control')

    def test_record_data_to_usb_stick(self):
        g = Rig()
        g.on()
        self.assertFalse(g.press('key_ok', held=['key_up']))                            # no flash drive: nothing happens
        self.assertEqual((g.state('logging.recording'), g.lcd()), (False, 'OFF'))
        g.call(g.A, 'usb.set_flash_drive', inserted=True)                               # insert the USB flash drive
        self.assertTrue(g.press('key_ok', held=['key_up']))                             # Up + OK: <LOG I>
        self.assertEqual((g.lcd(), g.state('logging.recording')), ('LOG I', True))
        g.step(2.0)
        self.assertEqual(g.main()['text'], 'OFF.')                                     # blinking dot while recording
        g.set_point(30.0)
        g.hold('key_ok')
        g.step(60.0)
        m = g.main()
        self.assertTrue(m['text'].endswith('.') and m['text'].count('.') == 2 and m['blink_mask'][-1] == '1', m)
        self.assertTrue(g.press('key_up', held=['key_ok']))                             # same combination: <LOG O>
        self.assertEqual((g.lcd(), g.state('logging.recording')), ('LOG O', False))
        g.step(60.0)
        files = g.call(g.A, 'usb.read_flash_drive')['files']
        self.assertEqual([f['name'] for f in files], ['LOG001.TXT'])
        rows = files[0]['records']
        self.assertEqual([r['time_s'] for r in rows], [10.0 * k for k in range(1, 1 + len(rows))])
        self.assertEqual(rows[-1]['time_s'], 10.0 * int(g.h.time_s / 10 - 6))
        self.assertTrue(all(r['setpoint_K'] in (C0 + 20.0, 303.15) and r['external_K'] is None for r in rows))
        self.assertEqual(rows[-1]['power_fraction'], 1.0)                              # heating at full power
        self.assertGreater(rows[-1]['internal_K'], C0 + 25.0)
        self.assertIsNotNone(files[0]['closed_s'])
        DONE_TASKS.add('record data to USB stick')


# ---------------------------------------------------------------- the other panel functions of the manual
class OtherPanelFunctions(unittest.TestCase):
    def test_power_supply_mode(self):
        g = Rig()
        self.assertEqual(g.state('power.supply_mode'), 'shared')                       # factory: circulator powered by the cooling machine
        g.on(held=['key_up'])                                                          # Up + power switch: shows the setting
        self.assertEqual((g.lcd(), g.state('power.supply_mode')), ('hHI', 'separate'))
        g.switch('off')
        g.on(held=['key_up'])
        self.assertEqual((g.lcd(), g.state('power.supply_mode')), ('hLO', 'shared'))
        self.assertTrue(g.switch('on', held=['key_up']) is False)                      # already on: the rocker does not move
        self.assertEqual(g.state('power.supply_mode'), 'shared')

    def test_autostart(self):
        g = Rig()
        g.call(g.A, 'thermal.set_temperature', expect='rejected', temperature_K=C0 + 30.0)
        g.on(held=['key_ok'])                                                          # OK + power switch: <AOn>, control starts
        self.assertEqual((g.lcd(), g.state('power.autostart'), g.state('circulation.running')), ('AOn', True, True))
        g.step(2.0)
        self.assertEqual(g.lcd(), '20.00')
        g.switch('off')
        g.on()                                                                         # every power-up starts again
        self.assertTrue(g.state('circulation.running'))
        g.switch('off')
        g.on(held=['key_ok'])
        self.assertEqual((g.lcd(), g.state('power.autostart'), g.state('circulation.running')), ('AOFF', False, False))

    def test_atc_one_point_adjustment(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', offset_K=0.5, gain=0.0)                   # the probe reads 0.5 K high (evaluator scenario)
        g.on()
        g.set_point(30.0)
        g.hold('key_ok')
        g.step(1200.0)
        self.assertEqual(g.lcd(), '30.00')                                             # the reading is at the set point ...
        self.assertAlmostEqual(g.state('thermal.temperature_k'), C0 + 29.5, places=6)  # ... the bath (reference thermometer) is 0.5 K lower
        self.assertTrue(g.press('key_down', held=['key_service']))                      # Service + Down
        m = g.main()
        self.assertEqual((g.screen(), m['text'], m['blink_mask']), ('atc_edit', '30.00', '11000'))
        g.press('key_down')                                                            # 29.00
        g.press('key_ok')
        self.assertEqual(g.main()['blink_mask'], '00011')
        g.press('key_up', hold=5.4)                                                    # 1 + 49 repeats: 29.50
        self.assertEqual(g.main()['text'], '29.50')
        self.assertTrue(g.press('key_ok'))                                             # applied at once: <CAL>
        self.assertEqual(g.lcd(), 'CAL')
        self.assertAlmostEqual(g.state('sensor.atc_offset_k'), -0.5, places=9)
        self.assertAlmostEqual(g.state('thermal.indicated_temperature_k'), C0 + 29.5, places=9)   # the reading now agrees with the reference
        self.assertEqual(g.leds(), {'heating_led'})                                    # and the control heats the bath to the true set point
        g.step(1200.0)
        self.assertEqual(g.lcd(), '30.00')
        self.assertAlmostEqual(g.state('thermal.temperature_k'), C0 + 30.0, places=6)  # and the bath is controlled to the true set point
        g.press('key_service', held=['key_down'])                                      # a reference outside +-5 C: error, ignored
        for _ in range(6):
            g.press('key_up')
        self.assertEqual(g.main()['text'], '36.00')
        g.press('key_ok')
        self.assertFalse(g.press('key_ok'))
        self.assertEqual((g.screen(), g.lcd()), ('home', 'Err'))
        self.assertAlmostEqual(g.state('sensor.atc_offset_k'), -0.5, places=9)

    def test_black_box_export(self):
        g = Rig()
        g.on()
        self.assertFalse(g.press('key_ok', held=['key_service']))                       # no flash drive: nothing
        g.set_point(35.0)
        g.hold('key_ok')
        g.step(1800.0)
        g.screw(-220)                                                                  # E 14 at 30 C (logged in the black box)
        g.step(600.0)
        g.call(g.A, 'usb.set_flash_drive', inserted=True)
        self.assertTrue(g.press('key_service', held=['key_ok']))                        # OK + Service: <-BB->
        self.assertEqual(g.lcd(), '-BB-')
        f = g.call(g.A, 'usb.read_flash_drive')['files'][-1]
        self.assertEqual((f['name'], f['kind']), ('BB001.TXT', 'blackbox'))
        times = [r['time_s'] for r in f['records']]
        now = g.h.time_s
        self.assertEqual((times[0], times[-1]), (10.0 * (int((now - 1200.0) / 10) + 1), 10.0 * int(now / 10)))   # the last 20 minutes
        self.assertEqual(len(times), 120)
        self.assertEqual([(e['kind'], e['code']) for e in f['events']], [('alarm', 14)])

    def test_flow_lever(self):
        g = Rig()
        g.h.configure_circulation_boundary(INST, connected=True, flow_rate_m3_s=8 / 60000, pressure_rise_Pa=25800, evidence='Synthetic external loop')
        self.assertTrue(g.lever('internal'))                                           # mechanical: works switched off too
        g.on()
        g.hold('key_ok')
        self.assertEqual(g.state('circulation.flow_rate_m3_s', 'circulation.pressure_rise_pa'), {'circulation.flow_rate_m3_s': 0.0, 'circulation.pressure_rise_pa': 0.0})
        self.assertTrue(g.lever('external'))
        self.assertEqual(g.state('circulation.flow_rate_m3_s'), 8 / 60000)


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def pair(self):
        a, b = Rig(), Rig()
        a.on()
        b.remote()                                                                     # direct commands = the remote interface (rOn)
        b.call(b.A, 'power.set', enabled=True)
        b.step(1.0)
        return a, b

    def test_setpoint_start_timer_stop(self):
        a, b = self.pair()
        a.set_point(42.7)
        a.hold('key_ok')
        a.press('key_ok', held=['key_down'])
        a.press('key_up', hold=1.4)
        a.press('key_ok')
        b.step(2.0)
        b.call(b.A, 'thermal.set_temperature', temperature_K=round(C0 + 42.7, 2))
        b.call(b.A, 'circulation.set_running', running=True)
        b.call(b.A, 'timer.set', duration_s=600.0)
        self.assertEqual(runtime_state(a.h, REMOTE), runtime_state(b.h, REMOTE))
        for g in (a, b):
            g.step(1500.0)
        self.assertEqual(runtime_state(a.h, REMOTE), runtime_state(b.h, REMOTE))
        a.press('key_ok')
        b.call(b.A, 'circulation.set_running', running=False)
        self.assertEqual(runtime_state(a.h, REMOTE), runtime_state(b.h, REMOTE))

    def test_power_on_settings_remote_logging_cutoff_atc(self):
        a, b = Rig(), Rig()
        b.remote()                                                                     # direct commands = the remote interface (rOn)
        a.on(held=['key_down'])
        a.switch('off')
        a.on(held=['key_up'])
        a.switch('off')
        a.on(held=['key_ok'])
        a.press('key_up', held=['key_down'])
        a.press('key_down', held=['key_up'])
        a.call(a.A, 'usb.set_flash_drive', inserted=True)
        a.press('key_ok', held=['key_up'])
        a.screw(-100)
        a.lever('internal')
        for cap, args in (('cooling.set_mode', dict(mode='off')), ('power.set', dict(enabled=True)), ('power.set', dict(enabled=False)),
                          ('power.set_supply_mode', dict(mode='separate')), ('power.set', dict(enabled=True)), ('power.set', dict(enabled=False)),
                          ('power.set_autostart', dict(enabled=True)), ('power.set', dict(enabled=True)), ('interface.set_remote', dict(enabled=True)),
                          ('interface.set_remote', dict(enabled=False)), ('interface.set_remote', dict(enabled=True)), ('usb.set_flash_drive', dict(inserted=True)), ('logging.set_recording', dict(enabled=True)),
                          ('safety.set_cutoff', dict(temperature_K=C0 + 150.0)), ('circulation.set_flow_path', dict(path='internal'))):
            b.call(b.A, cap, **args)
            if args == dict(enabled=True) and cap == 'power.set':
                b.step(1.0)
            if cap == 'interface.set_remote' and args == dict(enabled=False):
                self.assertEqual(b.state('interface.remote'), a.state('interface.remote'))   # the rOn / rOFF chord = interface.set_remote
        for g in (a, b):
            g.step(300.0)
        self.assertEqual(runtime_state(a.h, REMOTE), runtime_state(b.h, REMOTE))
        a.press('key_down', held=['key_service'])
        for _ in range(3):
            a.press('key_down')
        a.press('key_ok')
        a.press('key_ok')
        reading = b.state('thermal.indicated_temperature_k')
        b.call(b.A, 'sensor.calibrate', reference_temperature_K=round(reading - 3.0, 2))
        self.assertEqual(runtime_state(a.h, REMOTE), runtime_state(b.h, REMOTE))


# ---------------------------------------------------------------- the device functions behind the panel (runtime level)
class DeviceFunctions(unittest.TestCase):
    def run_to(self, mode, supply='shared', target=C0 + 10.0, seconds=600.0, start=C0 + 0.0):
        g = Rig(initial_temperature_K=start, heat_loss_W_K=0.0)
        g.remote()                                                                     # direct commands = the remote interface (rOn)
        g.call(g.A, 'cooling.set_mode', mode=mode)
        g.call(g.A, 'power.set_supply_mode', mode=supply)
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'thermal.set_temperature', temperature_K=target)
        g.call(g.A, 'circulation.set_running', running=True)
        g.step(seconds)
        return g

    def test_cooling_modes_and_supply_limit(self):
        cap = 3.5 * 3600.0
        auto = self.run_to('auto', seconds=60.0)
        self.assertAlmostEqual(auto.state('thermal.temperature_k'), C0 + 2000.0 * 60 / cap, places=9)   # AUTO: compressor off while heating
        self.assertEqual(auto.state('indicator.heating', 'indicator.cooling'), {'indicator.heating': True, 'indicator.cooling': False})
        con = self.run_to('on', supply='separate', seconds=60.0)                        # CON: the compressor runs against the heater
        rise = con.state('thermal.temperature_k') - C0
        self.assertTrue(2000.0 * 60 / cap - 200.0 * 60 / cap < rise < 2000.0 * 60 / cap - 170.0 * 60 / cap, rise)
        self.assertEqual(con.state('indicator.heating', 'indicator.cooling'), {'indicator.heating': True, 'indicator.cooling': True})
        hlo = self.run_to('on', supply='shared', seconds=60.0)                          # hLO: the heater also gives up the compressor's draw
        self.assertAlmostEqual(rise - (hlo.state('thermal.temperature_k') - C0), 460.0 * 60 / cap, delta=0.05)
        s = con.h.debug_snapshot()['instances'][INST]['state']
        self.assertAlmostEqual(s['thermal.heater_energy_j'], 2000.0 * 60, places=6)    # both run: the ledger keeps them apart
        self.assertGreater(s['thermal.cooling_energy_j'], 170.0 * 60)
        self.assertAlmostEqual(s['thermal.stored_energy_change_j'], s['thermal.heater_energy_j'] - s['thermal.cooling_energy_j'] - s['thermal.environment_energy_j'], places=6)
        off = self.run_to('off', target=C0 - 10.0, seconds=600.0)                       # COFF: no active cooling at all
        self.assertEqual((off.state('thermal.temperature_k'), off.state('indicator.cooling')), (C0, False))
        on = self.run_to('auto', target=C0 - 10.0, seconds=3600.0)
        self.assertAlmostEqual(on.state('thermal.temperature_k'), C0 - 10.0, places=6)

    def test_timer_band_restart_and_setpoint_change(self):
        g = Rig()
        g.remote()
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'timer.set', expect='rejected', duration_s=90.0)                    # whole minutes only
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 25.0)
        g.call(g.A, 'timer.set', duration_s=120.0)
        g.step(600.0)
        self.assertEqual(g.state('timer.phase'), 'waiting')                            # control not running: the timer waits
        g.call(g.A, 'circulation.set_running', running=True)
        g.step(1.0)
        g.wait_for('timer.phase', 'running', dt=0.5)
        reach = (5.0 - 0.1) * 12600.0 / 2000.0                                         # within 0.1 K after ~31 s of full heating ...
        self.assertAlmostEqual(g.h.time_s, 601.0 + reach + 30.0, delta=1.0)            # ... then 30 s in the band
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 25.0)                # the same value: no change
        self.assertEqual(g.state('timer.phase'), 'running')
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 26.0)                # a changed set point cancels a running timer
        self.assertEqual(g.state('timer.phase', 'timer.duration_s'), {'timer.phase': 'off', 'timer.duration_s': 0.0})
        g.call(g.A, 'timer.set', duration_s=60.0)
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 27.0)                # while waiting it stays armed
        self.assertEqual(g.state('timer.phase'), 'waiting')
        g.step(400.0)
        self.assertEqual((g.state('timer.phase'), g.state('circulation.running')), ('off', False))
        g.call(g.A, 'timer.set', expect='rejected', duration_s=60.0 * 1000)
        g.call(g.A, 'power.set', enabled=False)
        g.call(g.A, 'timer.set', expect='rejected', duration_s=60.0)

    def test_cutoff_trips_in_a_long_step(self):
        g = Rig()
        g.remote()
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'safety.set_cutoff', temperature_K=C0 + 40.0)
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 60.0)
        g.call(g.A, 'circulation.set_running', running=True)
        g.step(3600.0)
        self.assertEqual((g.state('alarm.code'), g.state('circulation.running')), (14, False))
        s = g.h.debug_snapshot()['instances'][INST]['state']
        self.assertTrue(12600.0 * 20.0 < s['thermal.heater_energy_j'] < 12600.0 * 20.0 + 1.0 * 20.0 * 130.0, s['thermal.heater_energy_j'])   # heated only up to 40 C
        self.assertLess(s['thermal.temperature_k'], C0 + 40.0)                          # then cooling passively
        g.call(g.A, 'power.set', enabled=False)
        g.call(g.A, 'power.set', enabled=True)
        self.assertEqual(g.state('alarm.code'), 0)
        g.call(g.A, 'safety.set_cutoff', temperature_K=C0 + 10.0)                       # below the bath: E 14 at once
        self.assertEqual(g.state('alarm.code'), 14)
        g.call(g.A, 'power.set', enabled=False)
        g.call(g.A, 'power.set', enabled=True)
        self.assertEqual(g.state('alarm.code'), 14)                                    # still above the cut-off at power-up

    def test_sensor_error_gain_and_calibration(self):
        g = Rig()
        self.assertEqual(g.state('sensor.offset_error_k', 'sensor.gain_error'), {'sensor.offset_error_k': 0.0, 'sensor.gain_error': 0.0})   # ideal by default
        r = g.call(g.A, 'sensor.configure_error', expect='rejected', offset_K=0.2, gain=0.0)
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')                         # evaluator only
        self.assertNotIn('sensor.configure_error', g.A.describe(instance_id=INST)['capabilities'])
        g.call(g.E, 'sensor.configure_error', offset_K=0.2, gain=0.01)
        self.assertAlmostEqual(g.state('thermal.indicated_temperature_k'), round(20.0 + 0.2 + 0.2, 2) + C0, places=9)
        g.remote()
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 50.0)
        g.call(g.A, 'circulation.set_running', running=True)
        g.step(3600.0)
        bath = (50.0 - 0.2) / 1.01                                                     # the reading T + 0.2 + 0.01 T(C) is held at 50
        self.assertAlmostEqual(g.state('thermal.temperature_k'), C0 + bath, places=6)
        g.call(g.A, 'sensor.calibrate', expect='rejected', reference_temperature_K=C0 + 55.5)   # outside +-5 C of the set point
        g.call(g.A, 'sensor.calibrate', reference_temperature_K=C0 + round(bath, 2))
        g.step(3600.0)
        self.assertAlmostEqual(g.state('thermal.temperature_k'), C0 + 50.0, delta=0.01)   # one point: exact at the calibration point
        self.assertAlmostEqual(g.state('thermal.indicated_temperature_k'), C0 + 50.0, places=6)

    def test_drive_removal_stops_recording_and_power_off_closes_the_file(self):
        g = Rig()
        g.remote()
        g.call(g.A, 'power.set', enabled=True)
        g.call(g.A, 'logging.set_recording', expect='rejected', enabled=True)
        g.call(g.A, 'usb.set_flash_drive', inserted=True)
        g.call(g.A, 'logging.set_recording', enabled=True)
        g.step(30.0)
        g.call(g.A, 'usb.set_flash_drive', inserted=False)
        self.assertFalse(g.state('logging.recording'))
        g.step(30.0)
        files = g.call(g.A, 'usb.read_flash_drive')['files']
        self.assertEqual(([r['time_s'] for r in files[0]['records']], files[0]['closed_s']), ([10.0, 20.0, 30.0], 30.0))
        g.call(g.A, 'usb.set_flash_drive', inserted=True)
        g.call(g.A, 'logging.set_recording', enabled=True)
        g.step(10.0)
        g.call(g.A, 'power.set', enabled=False)
        f = g.call(g.A, 'usb.read_flash_drive')['files'][1]
        self.assertEqual((f['name'], [r['time_s'] for r in f['records']], f['closed_s']), ('LOG002.TXT', [70.0], 70.0))
        self.assertFalse(g.state('logging.recording'))


# ---------------------------------------------------------------- refusals, locks, time-outs
class FaultInjection(unittest.TestCase):
    """2026-10-19 sync fix: the code table of the manual (p.56, p.76-80) by the evaluator-only fault.inject / fault.clear."""

    @staticmethod
    def running():
        g = Rig()
        g.on()
        g.set_point(40.0)
        g.hold('key_ok')
        g.step(600.0)
        return g

    def test_alarm_stops_control_until_cause_removed_and_restart(self):
        g = self.running()
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code=1)['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('fault.inject', g.A.describe(instance_id=INST)['capabilities'])
        self.assertEqual(g.call(g.E, 'fault.inject', expect='rejected', code=3)['error']['code'], 'INVALID_ARGUMENT')   # limits not settable: not offered
        g.call(g.E, 'fault.inject', code=1)                                            # level below minimum (float, p.70, p.76)
        self.assertEqual((g.state('alarm.code'), g.state('circulation.running')), (1, False))
        self.assertEqual((g.lcd(), g.leds()), ('E 01', {'alarm_led'}))
        self.assertTrue(g.press('key_ok'))                                             # OK silences; the control stays stopped
        g.hold('key_ok')
        self.assertFalse(g.state('circulation.running'))
        g.switch('off')
        g.step(10.0)
        g.on()
        self.assertEqual((g.state('alarm.code'), g.lcd()), (1, 'E 01'))                # cause still there: alarm again at power-up
        g.call(g.E, 'fault.clear', code=1)                                             # medium topped up
        self.assertEqual(g.state('alarm.code'), 1)                                     # a restart is still required (p.56)
        g.switch('off')
        g.step(10.0)
        g.on()
        self.assertEqual((g.state('alarm.code'), g.lcd(), g.leds()), (0, 'OFF', set()))
        events = g.h._blackbox[INST]['events']
        self.assertEqual([e['code'] for e in events if e['kind'] == 'alarm'][:2], [1, 1])

    def test_switch_off_codes_need_four_seconds_and_four_digit_display(self):
        g = self.running()
        g.call(g.E, 'fault.inject', code=62)                                           # CAN bus error: switch off, wait 4 s, on (p.77)
        self.assertEqual(g.lcd(), 'E 62')
        g.switch('off')
        g.step(2.0)
        g.on()
        self.assertEqual(g.state('alarm.code'), 62)                                    # 2 s were not enough
        g.switch('off')
        g.step(5.0)
        g.on()
        self.assertEqual((g.state('alarm.code'), g.state('fault.injected')), (0, []))
        g.call(g.E, 'fault.inject', code=1103)                                         # four digits in the five cells
        self.assertEqual(g.lcd(), 'E1103')
        h = Rig()                                                                      # injected while switched off: at power-up
        h.call(h.E, 'fault.inject', code=33)
        h.on()
        self.assertEqual((h.lcd(), h.state('alarm.code')), ('E 33', 33))
        k = Rig()                                                                      # E 61 with the cooling unit off: heating thermostat (p.77)
        k.on()
        k.direct('cooling.set_mode', mode='off')
        k.call(k.E, 'fault.inject', code=61)
        self.assertEqual(k.state('alarm.code'), 61)
        k.switch('off')
        k.step(5.0)
        k.on()
        self.assertEqual(k.state('alarm.code'), 0)
        self.assertEqual(k.state('fault.injected'), [61])

    def test_warning_keeps_control_and_alternates_with_the_temperature(self):
        g = self.running()
        g.call(g.E, 'fault.inject', code=1427)                                         # condensation pressure warning threshold (p.80)
        self.assertEqual((g.state('warning.code'), g.state('alarm.code'), g.state('circulation.running')), (1427, 0, True))
        shown = set()
        for _ in range(8):
            g.step(0.5)
            shown.add(g.lcd())
        self.assertEqual(shown, {'40.00', 'E1427'})                                    # alternates with the temperature (p.56)
        self.assertNotIn('alarm_led', g.leds())
        self.assertTrue(g.press('key_ok'))                                             # first OK silences the beep only
        self.assertTrue(g.state('circulation.running'))
        g.call(g.E, 'fault.clear', code=1427)                                          # cause removed: the warning clears
        self.assertEqual(g.state('warning.code'), 0)
        g.step(2.0)
        self.assertEqual(g.lcd(), '40.00')
        self.assertTrue(g.press('key_ok'))                                             # OK pauses as usual
        self.assertFalse(g.state('circulation.running'))

    def test_another_callers_run_cannot_be_stopped_or_switched_off(self):
        """Lead rule 2026-10-19: B cannot stop or change A's temperature control (panel or direct); A pauses its own; the evaluator is unrestricted.
        The red rocker is a hard power switch: never refused, the control stops as on a power loss (lead clarification 2026-10-19)."""
        g = Rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        g.on()
        g.hold('key_ok')                                                               # A starts the temperature control on the panel
        self.assertTrue(g.state('circulation.running'))
        before, before_r = runtime_state(g.h), runtime_state(g.h, REMOTE)
        self.assertFalse(g.call(B, 'panel.press', input_id='key_ok', duration_s=0.1)['accepted'])   # B's OK (pause): refused
        self.assertEqual(g.lcd(), 'dEnY')                                              # shown briefly on the display (frame and pixels)
        self.assertEqual(runtime_state(g.h), before)
        g.call(B, 'interface.set_remote', enabled=True)                                # over the remote interface (rOn) as well
        for cap, args in (('circulation.set_running', {'running': False}), ('thermal.set_temperature', {'temperature_K': C0 + 30.0}),
                          ('timer.set', {'duration_s': 600.0}), ('cooling.set_mode', {'mode': 'on'})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(runtime_state(g.h, REMOTE), before_r)
        g.call(g.A, 'circulation.set_running', running=False)                          # A pauses its own control (remote side)
        self.assertFalse(g.state('circulation.running'))
        g.call(B, 'circulation.set_running', running=True)
        g.call(g.E, 'circulation.set_running', running=False)                          # the evaluator is unrestricted
        self.assertFalse(g.state('circulation.running'))
        g.call(g.A, 'interface.set_remote', enabled=False)
        g.hold('key_ok')                                                               # A starts on the panel again and pauses it there
        self.assertTrue(g.press('key_ok'))
        self.assertFalse(g.state('circulation.running'))
        g.hold('key_ok')
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])   # physical: never refused
        self.assertEqual((g.state('power.enabled'), g.state('circulation.running')), (False, False))

    def test_direct_commands_are_the_remote_interface(self):
        """Sweep 2026-10-19 (lead rule 5; manual p.63, p.83-84): an agent's direct device commands stand for the USB interface and work only in remote
        mode rOn; reads, physical actions and the remote switch stay open, the keys work locally in rOFF, the evaluator is not gated."""
        g = Rig()
        g.on()
        before = runtime_state(g.h)
        for cap, args in (('thermal.set_temperature', {'temperature_K': C0 + 30.0}), ('circulation.set_running', {'running': True}),
                          ('timer.set', {'duration_s': 600.0}), ('cooling.set_mode', {'mode': 'on'}), ('power.set_supply_mode', {'mode': 'separate'}),
                          ('power.set_autostart', {'enabled': True}), ('sensor.calibrate', {'reference_temperature_K': C0 + 20.0}),
                          ('logging.set_recording', {'enabled': True}), ('blackbox.export', {})):
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'PRECONDITION_FAILED', cap)
        self.assertEqual(runtime_state(g.h), before)
        self.assertIn('thermal.target_k', g.A.read(instance_id=INST)['observations'])   # reads stay allowed
        desc = g.A.describe(instance_id=INST)['capabilities']
        self.assertIn('interface.remote', desc['thermal.set_temperature']['preconditions'][-1]['state_ids'])
        for cap, args in (('circulation.set_flow_path', {'path': 'internal'}), ('safety.set_cutoff', {'temperature_K': C0 + 100.0}),
                          ('usb.set_flash_drive', {'inserted': True}), ('usb.read_flash_drive', {})):
            g.call(g.A, cap, **args)                                                   # physical actions and the stick: not gated
        g.call(g.E, 'thermal.set_temperature', temperature_K=C0 + 25.0)                 # the evaluator is not gated
        self.assertTrue(g.press('key_up'))                                             # rOFF: the keys work locally
        g.press('key_ok', hold=6.0)                                                    # (leave the set point entry unchanged)
        g.call(g.A, 'interface.set_remote', enabled=True)                              # rOn: the interface commands work, the keys are locked
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 30.0)
        g.call(g.A, 'circulation.set_running', running=True)
        self.assertEqual(g.state('thermal.target_k'), C0 + 30.0)
        self.assertFalse(g.press('key_up'))
        g.call(g.A, 'power.set', enabled=False)                                        # the setting survives switching off (p.57)
        g.on()
        self.assertTrue(g.state('interface.remote'))
        g.call(g.A, 'thermal.set_temperature', temperature_K=C0 + 31.0)

    def test_panel_gesture_acts_as_the_calling_session(self):
        """2026-10-19 sync fix: the commands behind a gesture run as the session that made it, not as a fixed panel principal."""
        from unittest import mock
        g = Rig()
        g.on()
        callers, real = [], rt.JulaboCorioCD200FSession._invoke_checked

        def spy(sess, *a, **k):
            callers.append((sess, a[2]))
            return real(sess, *a, **k)
        with mock.patch.object(rt.JulaboCorioCD200FSession, '_invoke_checked', autospec=True, side_effect=spy):
            g.hold('key_ok')
        self.assertTrue(g.state('circulation.running'))
        self.assertIn('circulation.set_running', [c for _, c in callers])
        self.assertTrue(all(s is g.A for s, c in callers if c != 'panel.frame'))
        self.assertIsNone(g.h._panel_caller)


class Rules(unittest.TestCase):
    def test_power_off_gate_and_mechanical_controls(self):
        g = Rig()
        for gesture in (lambda: g.press('key_up'), lambda: g.hold('key_ok'), lambda: g.press('key_ok', held=['key_down'])):
            self.assertFalse(gesture())
        self.assertEqual((g.screen(), g.lcd()), ('off', ''))
        self.assertTrue(g.screw(-50))                                                  # the screw is mechanical: it moves, the display stays dark
        self.assertEqual((g.state('safety.cutoff_k'), g.lcd()), (C0 + 200.0, ''))
        self.assertFalse(g.switch('off'))                                              # already off
        g.call(g.A, 'power.set', enabled=True)                                         # switched on another way: the panel boots
        self.assertEqual(g.screen(), 'boot')

    def test_entry_timeouts_discard(self):
        g = Rig()
        g.on()
        g.press('key_up')
        g.press('key_up')
        g.step(4.9)
        self.assertEqual(g.screen(), 'sp_edit')
        g.press('key_up')                                                              # a key restarts the time-out
        g.step(4.9)
        self.assertEqual(g.screen(), 'sp_edit')
        g.step(0.1)
        self.assertEqual((g.screen(), g.state('thermal.target_k')), ('home', C0 + 20.0))
        g.press('key_ok', held=['key_down'])
        g.press('key_up')
        g.step(5.0)
        self.assertEqual((g.screen(), g.state('timer.phase')), ('home', 'off'))
        g.press('key_up', held=['key_down'])
        g.step(1.9)
        self.assertEqual(g.lcd(), 'rOn')
        g.step(0.1)
        self.assertEqual(g.lcd(), 'OFF')                                               # messages last 2 s

    def test_setpoint_limits_and_fluid_range_refusal(self):
        g = Rig(minimum_temperature_K=C0 + 5.0, maximum_temperature_K=C0 + 80.0)
        g.on()
        g.press('key_up')
        g.press('key_up', hold=20.0)                                                   # clamps at 150.0
        self.assertEqual(g.main()['text'], '150.0')
        g.press('key_ok')
        g.press('key_up')
        self.assertEqual(g.main()['text'], '150.0')
        g.press('key_ok')
        self.assertFalse(g.press('key_ok'))                                            # outside the configured fluid range: refused
        self.assertEqual((g.screen(), g.lcd(), g.state('thermal.target_k')), ('home', 'OFF', C0 + 20.0))
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'PRECONDITION_FAILED')
        g.press('key_down')
        g.press('key_down', hold=20.0)
        self.assertEqual(g.main()['text'], '-20.0')
        g.press('key_up')                                                              # through zero in whole kelvin
        g.press('key_up', hold=1.4)
        self.assertEqual(g.main()['text'], '-9.0')

    def test_start_refused_without_bath_model(self):
        g = Rig(configured=False)
        g.on()
        self.assertFalse(g.hold('key_ok'))
        self.assertEqual((g.state('circulation.running'), g.lcd()), (False, 'OFF'))
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'PRECONDITION_FAILED')

    def test_rejected_and_unknown_gestures(self):
        g = Rig()
        r = g.call(g.A, 'panel.turn', expect='rejected', input_id='key_ok', detents=1)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='key_ok', held=['key_ok'])
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='key_ok', duration_s='long')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        caps = g.A.describe(instance_id=INST)['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.press', 'panel.turn', 'panel.switch', 'panel.capture'})
        self.assertIn('panel.frame', g.E.describe(instance_id=INST)['capabilities'])
        self.assertEqual([x['capability_id'] for x in g.A.logs()], ['panel.turn', 'panel.press', 'panel.press', 'panel.frame'])


# ---------------------------------------------------------------- rendering, capture, contract
class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.on()
            g.set_point(37.5)
            g.hold('key_ok')
            g.step(301.0)
            return g
        a, b = scenario(), scenario()
        for d in SPEC['displays']:
            ia, ib = a.h.panel(INST).render(d), b.h.panel(INST).render(d)
            self.assertEqual((ia.dtype.name, ia.shape[2]), ('uint8', 3))
            self.assertTrue((ia == ib).all())
            self.assertTrue((ia == a.h.panel(INST).render(d)).all())
        self.assertEqual(a.h.panel(INST).png_bytes('display'), b.h.panel(INST).png_bytes('display'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel(INST).save_png('display', OUT / 'home.png')}
        self.assertEqual(a.lcd(), '37.50')
        a.press('key_up')
        a.press('key_up')
        a.press('key_ok')
        a.press('key_up', hold=0.6)
        a.step(0.1)                                                                    # on phase of the flashing decimal place
        self.assertEqual((a.lcd(), a.main()['blink_mask']), ('38.7', '0001'))
        shots['menu'] = a.h.panel(INST).save_png('display', OUT / 'menu.png')
        a.press('key_ok')
        a.press('key_ok')
        a.step(2.0)
        a.screw(-215)                                                                  # cut-off 35 C below the bath: E 14
        a.step(2.0)
        self.assertEqual(a.lcd(), 'E 14')
        shots['error'] = a.h.panel(INST).save_png('display', OUT / 'error.png')
        for name in LEDS:
            shots[name] = a.h.panel(INST).save_png(name, OUT / f'{name}.png')
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
        meta = rt.export_julabo_corio_cd200f_metadata()['asset_types'][rt.JULABO_CORIO_CD200F_TYPE]
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities'])), [])
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.turn', 'panel.switch', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['capabilities'], **meta['evaluator_capabilities'], **meta['panel_capabilities']}.items():
            original = k in ('power.set', 'thermal.set_temperature', 'circulation.set_running', 'scene.set_bath_lid')   # their components come from the builder
            for key in ('input_schema', 'output_schema', 'kind', 'visibility') + (() if original else ('component_ids', 'state_effects', 'preconditions')):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
            if original:
                self.assertEqual(c['capabilities'][k]['state_effects'], v['state_effects'], k)
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'parameter_cycle', pu.SPEC_VERSION))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):                   # shared files: registered unpinned
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('corio_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                       # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
