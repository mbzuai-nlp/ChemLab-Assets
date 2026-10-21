"""Front-panel tests for the Agilent 7890B GC asset (assets/GC7890B/panel_ui.json + scripts/asset_runtime/_panel_ui.py over
scripts/asset_runtime/agilent_7890b_ssl_fid.py): python3 -m pytest tests/test_panel_ui_agilent_7890b_ssl_fid.py -q

Every key goes through the session API (panel.press / panel.switch, the runtime's normal invoke path); robot skills (gases, syringe) and the
host clock (sim.step) are called directly.  Screens are checked twice: the structured frame (panel.frame) and the rendered pixels, read back with
a 5x7 character reader that knows only the display geometry of the spec.  Themes: the eight key procedures of the menu survey; panel = direct
capability (same runtime state); the legacy process semantics kept (mass balance of an injection); refusals and locks (pending entry, keyboard
lock, configuration lock, ranges, absent hardware, service-only items); time-outs (messages, display saver, blinking); the firmware functions
behind the menus (methods, sequences, schedule, clock table, EMF, logs, calibration, gas types, lit offset, shutdowns, macros, column
compensation, gas saver, pulsed injection, oven program and post run); render determinism and the evidence PNGs."""
import copy
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import agilent_7890b_ssl_fid as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'agilent_7890b_ssl_fid_01'
C0 = 273.15
ML = rt.ML_MIN
OUT = ROOT / 'outputs/panel_ui/agilent_7890b_ssl_fid'
SURVEY_TASKS = ['Start up the GC', 'Change a setpoint (e.g. oven temperature)', 'Load a stored method', 'Manual injection run', 'Start a stored sequence',
                'Clear a shutdown fault', 'Set the LAN address by DHCP', 'Shut down for more than a week']
DONE_TASKS = set()
LCD = rt.PANEL_SPEC['displays']['lcd']
_GLYPHS = {}
for _ch, _cols in pu.FONT5X7.items():
    _GLYPHS.setdefault(bytes(_cols), _ch)


def read_lcd(img):
    """5x7 character reader: sample the dot grid of every cell (geometry from the spec) and spell each line; inverse cells are read inverted."""
    cw, ch = LCD['cell']
    px, m = LCD['dot'], LCD['margin']
    lum = img.astype(int).sum(axis=2)
    thr = (lum.max() + lum.min()) / 2.0 if lum.max() > lum.min() else 1e9
    lines = []
    for r in range(LCD['rows']):
        text = ''
        for c in range(LCD['cols']):
            x0, y0 = m + c * cw, m + r * ch
            inv = lum[y0 + ch - 1, x0 + cw - 1] > thr
            cols = []
            for gx in range(5):
                bits = 0
                for gy in range(8):
                    on = lum[y0 + 1 + gy * px, x0 + 1 + gx * px] > thr
                    if on != inv:
                        bits |= 1 << gy
                cols.append(bits)
            text += _GLYPHS.get(bytes(cols), '?')
        lines.append(text)
    return lines


class Rig:
    def __init__(self, ready=True, instance=INST, warm_s=1500.0):
        self.h = rt.GC7890BRuntime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.inst = instance
        if ready:
            self.plumb()
            assert self.switch('on')
            self.step(warm_s)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        if expect == 'succeeded':
            rt.validate(rt.ALL_CAPS[cap]['output_schema'], r['result'])
            assert cap == 'sim.step' or self.h.t == t0, cap                          # gestures and commands take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def plumb(self):
        self.call(self.A, 'power.connect_mains', connected=True)
        for gas in ('carrier', 'hydrogen', 'air', 'makeup'):
            self.call(self.A, 'gas.connect', gas=gas, connected=True)

    def press(self, key):
        return self.call(self.A, 'panel.press', input_id=key)['accepted']

    def keys(self, *ks):
        for k in ks:
            assert self.press(k), (k, self.lcd())

    def type(self, text):
        for c in text:
            self.keys({'.': 'key_dot', '-': 'key_minus'}.get(c, f'key_{c}'))

    def enter(self, text):
        self.type(text)
        return self.press('key_enter')

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lcd(self):
        """Lines shown now, from the frame and from the pixels (both must agree)."""
        f = self.frame()['displays']['lcd']
        img = self.h.panel.render('lcd')
        read = read_lcd(img)
        shown = [s.ljust(LCD['cols'])[:LCD['cols']] for s in f['shown']]
        assert read == shown, (read, shown)
        return [s.rstrip() for s in f['shown']]

    def text(self):
        return '\n'.join(self.lcd())

    def screen(self):
        return self.frame()['screen']

    def item(self):
        return (self.frame()['ui']['menu'] or {}).get('item')

    def to(self, item_id, limit=200):
        """Scroll to a line of the current list with the scroll keys (down first, then up from the bottom)."""
        for key in ('key_down', 'key_up'):
            for _ in range(limit):
                if self.item() == item_id:
                    return
                before = self.item()
                assert self.press(key), item_id
                if self.item() == before:
                    break
        raise AssertionError(f'{item_id} not reached: {self.lcd()}')

    def led(self, lid):
        return self.frame()['displays'][lid]

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def state(self, *ids):
        o = self.E.read(instance_id=self.inst, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def status(self):
        """The Ready / Not Ready table ([Status] toggles to the setpoint table when it is already shown, p.177)."""
        self.keys('key_status')
        if self.screen() != 'status':
            self.keys('key_status')
        return self.text()

    def flame_on(self):
        self.keys('key_front_det')
        self.to('det_flame')
        self.keys('key_on')
        assert self.state('detector.flame_lit')


def runtime_state(h):
    r = h.read('evaluator')
    for k in ('clock.time_s', 'clock.datetime', 'logs.entries', 'logs.counts', 'sound.counts', 'display.message'):
        r.pop(k)
    r['truth.process']['legacy'].pop('time_s')
    return json.dumps(dict(read=r, m=h.m, methods=h.methods, seq=h.seq, opt=h.opt, cal=h.cal, cfg=h.instrument), sort_keys=True, default=str)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_start_up(self):
        g = Rig(ready=False)
        self.assertEqual(g.screen(), 'off')
        self.assertEqual(g.lcd(), ['', '', '', ''])
        self.assertFalse(g.press('key_oven'))                                        # switched off: keys do nothing
        g.plumb()                                                                    # gas sources on, local valves open
        self.assertTrue(g.switch('on'))
        self.assertEqual(g.screen(), 'status')
        self.assertIn('Power on successful', g.lcd()[3])                             # p.18
        self.assertTrue(g.led('led_not_ready')['on'])
        g.keys('key_oven')                                                           # an analytical method (oven 150 C, flame on) stored as 1
        self.assertTrue(g.enter('150'))
        g.keys('key_front_det')
        g.to('det_flame')
        g.keys('key_on')                                                             # flame On: lit by the GC once the detector is hot
        self.assertFalse(g.state('detector.flame_lit'))
        g.keys('key_method')
        g.to('store_1')
        g.keys('key_enter', 'key_on')
        self.assertIn('1', g.state('methods.stored'))
        g.call(g.A, 'thermal.set_temperature', zone='oven', temperature_k=C0 + 50.0)
        g.keys('key_load', 'key_method', 'key_1')                                    # load the method: [Load][Method] 1 [On/Yes]
        g.keys('key_on')
        self.assertAlmostEqual(g.state('oven.method')['temp_k'], C0 + 150.0)
        g.step(1800)                                                                 # wait for the detector to stabilise
        self.assertTrue(g.state('detector.flame_lit'))
        self.assertIn('Ready for Injection', g.status())
        self.assertFalse(g.led('led_not_ready')['on'])
        DONE_TASKS.add('Start up the GC')

    def test_change_a_setpoint(self):
        g = Rig()
        g.keys('key_oven')                                                           # [Oven], line Temperature, type, [Enter] (p.25)
        self.assertEqual(g.lcd()[0].strip(), 'OVEN')
        self.assertTrue(g.lcd()[1].startswith('Temperature'))
        self.assertTrue(g.lcd()[1].endswith('80.0<'))
        g.type('150')
        self.assertTrue(g.lcd()[1].rstrip().endswith('150*') or g.lcd()[1].rstrip().endswith('150'))   # the * flashes (p.12)
        self.assertEqual(g.frame()['ui']['editor']['typed'], '150')
        self.assertTrue(g.press('key_enter'))
        self.assertAlmostEqual(g.state('oven.method')['temp_k'], C0 + 150.0)
        self.assertTrue(g.lcd()[1].endswith('150.0<'))
        self.assertEqual(g.lcd()[1][16:22].strip(), '80.0')                         # Actual column: still 80 C
        g.step(600)
        self.assertEqual(g.lcd()[1][16:22].strip(), '150.0')
        DONE_TASKS.add('Change a setpoint (e.g. oven temperature)')

    def test_load_a_stored_method(self):
        g = Rig()
        g.call(g.A, 'thermal.set_temperature', zone='oven', temperature_k=C0 + 120.0)
        g.call(g.A, 'method.store', slot='2')
        g.call(g.A, 'thermal.set_temperature', zone='oven', temperature_k=C0 + 60.0)
        g.keys('key_load')
        self.assertIn('Method or Seq', g.text())
        g.keys('key_method', 'key_2')                                                # the cursor goes to method 2 and the GC asks
        self.assertEqual(g.item(), 'load_2')
        self.assertIn('Load method 2? YES/NO', g.lcd()[3])
        g.keys('key_off')                                                            # Off/No: back to the list, nothing loaded
        self.assertAlmostEqual(g.state('oven.method')['temp_k'], C0 + 60.0)
        self.assertEqual(g.screen(), 'load_method')
        g.keys('key_2', 'key_on')
        self.assertAlmostEqual(g.state('oven.method')['temp_k'], C0 + 120.0)
        self.assertEqual(g.screen(), 'status')
        self.assertTrue(g.led('led_not_ready')['on'])                               # not ready until the new setpoints are reached (p.39)
        g.keys('key_load', 'key_method', 'key_5')                                    # empty slot: refused with a message
        self.assertFalse(g.press('key_on'))
        self.assertIn('Method not stored', g.lcd()[3])
        self.assertAlmostEqual(g.state('oven.method')['temp_k'], C0 + 120.0)
        DONE_TASKS.add('Load a stored method')

    def test_manual_injection_run(self):
        g = Rig()
        g.flame_on()
        g.keys('key_prep_run')                                                       # [Prep Run] (p.50)
        self.assertTrue(g.led('led_pre_run')['on'])
        g.step(30)
        self.assertIn('Ready for Injection', g.status())
        g.call(g.A, 'sample.aspirate', fraction=0.01)
        g.call(g.A, 'syringe.insert')
        g.keys('key_start')                                                          # depress the plunger and press [Start] together
        g.call(g.A, 'syringe.depress')
        g.call(g.A, 'syringe.retract')
        self.assertTrue(g.led('led_run')['on'])
        self.assertFalse(g.led('led_pre_run')['on'])
        g.step(600)
        self.assertGreater(g.h.gc.fid_integral_C, 0)
        g.keys('key_stop')
        self.assertEqual(g.state('run.state'), 'idle')
        g.keys('key_time')
        self.assertTrue(g.lcd()[1].startswith('Last run'))
        self.assertTrue(g.lcd()[1].endswith('10.00<'))
        for led in g.h.gc.mass_balance().values():
            self.assertLess(abs(led['residual_kg']), 1e-20)
        DONE_TASKS.add('Manual injection run')

    def test_start_a_stored_sequence(self):
        g = Rig()
        g.call(g.A, 'method.store', slot='1')
        g.keys('key_seq')
        self.assertEqual(g.screen(), 'seq_list')
        g.keys('key_seq')                                                            # second press: the subsequence information (p.44)
        g.to('seq_sub1')
        self.assertTrue(g.enter('1'))
        self.assertEqual(g.state('sequence.active')['subseq'][0]['method'], 1)
        g.to('seq_sub1_inj')
        self.assertFalse(g.press('key_mode_type'))                                  # no ALS / valve to choose
        self.assertIn('No injector or valve installed', g.lcd()[3])
        g.keys('key_store', 'key_seq', 'key_3', 'key_on')                            # [Store][Seq] 3 [On/Yes] (p.46)
        self.assertIn('3', g.state('sequence.stored'))
        g.keys('key_load', 'key_seq', 'key_3', 'key_on')                             # [Load][Seq] 3 [On/Yes] (p.47)
        g.keys('key_seq_control')
        self.assertTrue(g.lcd()[1].startswith('Status'))
        self.assertIn('Stopped', g.lcd()[1])
        g.to('seqc_start')
        g.keys('key_enter')
        self.assertEqual(g.state('sequence.status'), 'no_sequence')                 # nothing to inject without a sampler (documented status)
        g.keys('key_up', 'key_up', 'key_up', 'key_up')
        self.assertIn('No sequence', g.lcd()[1])
        g.keys('key_load', 'key_seq', 'key_7')
        self.assertFalse(g.press('key_on'))
        self.assertIn('Sequence not stored', g.lcd()[3])                            # p.47: error message if not stored
        DONE_TASKS.add('Start a stored sequence')

    def test_clear_a_shutdown_fault(self):
        g = Rig()
        g.call(g.A, 'gas.connect', gas='carrier', connected=False)                  # carrier cylinder empty
        g.step(60)
        self.assertEqual(g.state('alarm.tone'), 'series')                            # beeps before the shutdown (p.33)
        g.step(70)
        self.assertIn('inlet_flow', g.state('status.shutdowns'))
        self.assertTrue(g.led('led_not_ready')['blink'])                            # Not Ready blinks on a fault
        self.assertIn('Front inlet flow shutdown', g.status())
        g.keys('key_off')                                                            # stop the alert tone
        self.assertEqual(g.state('alarm.tone'), 'none')
        g.keys('key_front_inlet')
        g.to('inlet_pres_psi')
        fr = g.frame()['displays']['lcd']
        row = next(i for i, ln in enumerate(fr['lines']) if ln.startswith('Pressure'))
        self.assertTrue(fr['blink'][row])                                            # Off blinks on the line (p.13, p.34)
        self.assertIn('Off', fr['lines'][row])
        self.assertFalse(g.press('key_on'))                                          # still shut down: clear first
        g.keys('key_off')                                                            # Off/No on the blinking line clears it
        self.assertNotIn('inlet_flow', g.state('status.shutdowns'))
        self.assertFalse(g.state('inlet.method')['gas_on'])
        g.call(g.A, 'gas.connect', gas='carrier', connected=True)                   # problem fixed
        g.keys('key_on')                                                             # On/Yes: in use again
        self.assertTrue(g.state('inlet.method')['gas_on'])
        g.step(30)
        self.assertNotIn('Off', g.lcd()[[i for i, ln in enumerate(g.lcd()) if ln.startswith('Pressure')][0]])
        DONE_TASKS.add('Clear a shutdown fault')

    def test_set_lan_address_by_dhcp(self):
        g = Rig()
        g.keys('key_options')
        g.to('opt_comm')
        g.keys('key_enter')
        g.to('comm_dhcp')
        g.keys('key_on')
        self.assertIn('Power cycle', g.lcd()[3])
        self.assertFalse(g.state('options.communication')['dhcp'])                   # applied at the next power-up
        g.switch('off')
        g.switch('on')
        self.assertTrue(g.state('options.communication')['dhcp'])
        DONE_TASKS.add('Set the LAN address by DHCP')

    def test_shut_down_for_more_than_a_week(self):
        g = Rig()
        g.flame_on()
        for key, item in (('key_oven', 'oven_temp'), ('key_front_inlet', 'inlet_temp'), ('key_front_det', 'det_temp')):
            g.keys(key)
            g.to(item)
            self.assertTrue(g.enter('40'))                                           # all heated zones to 40 C (p.20)
        g.keys('key_front_det')
        g.to('det_flame')
        g.keys('key_off')                                                            # flame off (hydrogen and air off, p.107)
        self.assertFalse(g.state('detector.method')['h2_on'])
        g.step(3600)
        self.assertIn('Ready for Injection', g.status())
        self.assertTrue(g.switch('off'))
        self.assertEqual(g.screen(), 'off')
        for gas in ('carrier', 'hydrogen', 'air', 'makeup'):
            g.call(g.A, 'gas.connect', gas=gas, connected=False)
        self.assertFalse(g.state('power.on'))
        DONE_TASKS.add('Shut down for more than a week')


# ---------------------------------------------------------------- panel = direct capability; legacy semantics kept
class Equivalence(unittest.TestCase):
    def test_panel_and_direct_calls_give_the_same_state(self):
        a, b = Rig(), Rig()
        a.keys('key_oven')
        a.enter('175')
        a.keys('key_down')
        a.enter('1.5')
        a.keys('key_down')
        a.enter('20')
        a.keys('key_down')
        a.enter('250')
        a.keys('key_front_inlet', 'key_mode_type', 'key_down', 'key_enter')
        a.to('inlet_purge_time')
        a.enter('0.75')
        a.keys('key_front_det')
        a.to('det_flame')
        a.keys('key_on')
        a.keys('key_options')
        a.to('opt_kd')
        a.keys('key_enter')
        a.to('kd_units')
        a.keys('key_mode_type', 'key_down', 'key_down', 'key_enter')
        b.call(b.A, 'thermal.set_temperature', zone='oven', temperature_k=C0 + 175.0)
        b.call(b.A, 'oven.set_initial_time', time_s=90.0)
        b.call(b.A, 'oven.set_ramp', index=1, rate_k_s=20 / 60.0)
        b.call(b.A, 'oven.set_ramp', index=1, final_k=C0 + 250.0)
        b.call(b.A, 'inlet.configure', mode='splitless')
        b.call(b.A, 'inlet.configure', purge_time_s=45.0)
        b.call(b.A, 'detector.set_flame', on=True)
        b.call(b.A, 'options.keyboard', pressure_units='kPa')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))

    def test_injection_keeps_the_legacy_split_mass_balance(self):
        """Default method = legacy defaults: the split injection loads 1/21 on the column, elutes it and integrates its charge (test_gc_runtime)."""
        g = Rig()
        g.flame_on()
        g.keys('key_start')
        g.call(g.A, 'sample.aspirate', fraction=0.01)
        g.call(g.A, 'syringe.insert')
        g.call(g.A, 'syringe.depress')
        g.call(g.A, 'syringe.retract')
        g.step(900)
        exp = 0.0
        for name, led in g.h.gc.mass_balance().items():
            col = led['injected_kg'] / 21
            self.assertAlmostEqual(led['column_loaded_kg'] / col, 1, places=9)
            self.assertAlmostEqual(led['eluted_kg'] / col, 1, places=9)
            exp += col * g.h.gc.species[name].response_C_kg
        self.assertAlmostEqual(g.h.gc.fid_integral_C / exp, 1, places=9)


# ---------------------------------------------------------------- navigation, refusals and locks
class Navigation(unittest.TestCase):
    def test_info_mode_type_and_clear(self):
        g = Rig()
        g.keys('key_oven', 'key_info')
        self.assertIn('Enter a value between 0 and 450', g.lcd()[3])                 # p.25 example
        g.keys('key_down')
        self.assertNotIn('Enter a value', g.text())                                  # the help goes with the next key
        g.keys('key_front_inlet', 'key_mode_type')
        self.assertEqual(g.lcd()[0].strip(), 'Mode')
        self.assertTrue(g.lcd()[1].endswith('Split <'))
        g.keys('key_down', 'key_down', 'key_clear')                                  # Clear abandons the choice
        self.assertEqual(g.state('inlet.method')['mode'], 'split')
        g.to('inlet_septum')
        g.keys('key_clear')                                                          # Clear: top line, then the previous display (p.28)
        self.assertEqual(g.item(), 'inlet_mode')
        g.keys('key_clear')
        self.assertEqual(g.screen(), 'status')

    def test_pending_entry_blocks_other_keys_and_ranges(self):
        g = Rig()
        g.keys('key_oven')
        g.type('99')
        self.assertFalse(g.press('key_front_det'))                                   # the * blinks: nothing else until Enter / Clear (p.12)
        self.assertEqual(g.screen(), 'oven')
        g.keys('key_clear')
        self.assertIsNone(g.frame()['ui']['editor'])
        self.assertAlmostEqual(g.state('oven.method')['temp_k'], C0 + 80.0)
        self.assertFalse(g.enter('999'))
        self.assertIn('Out of range', g.lcd()[3])
        g.call(g.A, 'config.oven', max_temperature_k=C0 + 200.0)
        self.assertFalse(g.enter('300'))                                             # above the configured maximum (p.144)
        self.assertIn('Setpoint exceeds maximum', g.lcd()[3])
        self.assertAlmostEqual(g.state('oven.method')['temp_k'], C0 + 80.0)

    def test_keyboard_lock(self):
        g = Rig()
        g.keys('key_options')
        g.to('opt_kd')
        g.keys('key_enter')
        g.to('kd_lock')
        g.keys('key_on')
        self.assertTrue(g.state('options.state')['keyboard_lock'])
        g.keys('key_clear', 'key_clear', 'key_clear')
        for k in ('key_oven', 'key_config', 'key_store', 'key_method', 'key_clock_table', 'key_time'):
            self.assertFalse(g.press(k), k)                                          # p.199: other keys not operational
        self.assertTrue(g.press('key_prep_run'))
        self.assertTrue(g.press('key_load'))
        self.assertTrue(g.press('key_method'))                                       # [Load][Method] still works
        self.assertEqual(g.screen(), 'load_method')
        self.assertTrue(g.press('key_seq_control'))
        g.keys('key_options')                                                        # assumption: Options stays usable to unlock
        g.to('opt_kd')
        g.keys('key_enter')
        g.to('kd_lock')
        g.keys('key_off')
        self.assertTrue(g.press('key_oven'))

    def test_configuration_lock_module_change_and_method_mismatch(self):
        g = Rig()
        g.call(g.A, 'method.store', slot='4')
        g.keys('key_config', 'key_front_det')                                        # [Config][Front Det]
        self.assertEqual(g.lcd()[0].strip(), 'CONFIGURE FRONT DET')
        g.keys('key_mode_type', 'key_down')
        self.assertFalse(g.press('key_enter'))
        self.assertIn('CONFIGURATION IS LOCKED', g.lcd()[3])                         # p.142
        self.assertTrue(g.state('config.state')['modules']['front_detector'])
        g.call(g.A, 'options.keyboard', hard_config_lock=False)
        g.keys('key_mode_type', 'key_down', 'key_enter')
        self.assertIn('Power cycle', g.lcd()[3])
        g.switch('off')
        g.switch('on')
        self.assertFalse(g.state('config.state')['modules']['front_detector'])
        self.assertTrue(g.state('options.state')['hard_config_lock'])               # locked again after the power cycle
        r = g.call(g.A, 'thermal.set_temperature', expect='rejected', zone='detector', temperature_k=C0 + 250.0)
        self.assertEqual(r['error']['code'], 'MODULE_UNCONFIGURED')
        g.keys('key_load', 'key_method', 'key_4', 'key_on')
        self.assertIn('mismatch', g.text())                                          # p.41-42

    def test_absent_hardware_and_service_items(self):
        g = Rig()
        for key in ('key_front_injector', 'key_back_inlet', 'key_sample_tray', 'key_run_table'):
            g.keys(key)
            self.assertFalse(g.press('key_mode_type'))
            self.assertIn('Not installed', g.lcd()[3])
            self.assertTrue(g.frame()['ui']['unsupported'])
        g.keys('key_service_mode')
        g.to('svc_settings')
        self.assertFalse(g.press('key_enter'))
        self.assertIn('Service personnel only', g.lcd()[3])
        g.keys('key_aux_col', 'key_1')
        self.assertEqual(g.screen(), 'col1')

    def test_status_table_order_and_status_switch(self):
        g = Rig()
        g.status()
        g.keys('key_status')
        self.assertEqual(g.screen(), 'status_table')
        self.assertTrue(g.lcd()[1].startswith('Oven temp'))
        g.keys('key_config', 'key_status')                                           # [Config][Status]: Enter on what comes first (p.26)
        g.to('cfg_status_column_flow')
        g.keys('key_enter')
        g.to('cfg_status_detector_output')
        g.keys('key_enter')
        self.assertEqual(g.state('config.state')['status_order'][:2], ['column_flow', 'detector_output'])
        g.keys('key_status', 'key_status')
        self.assertTrue(g.lcd()[1].startswith('Column 1 flow'))


# ---------------------------------------------------------------- time-outs, display options, LEDs
class TimeAndDisplay(unittest.TestCase):
    def test_message_timeout_display_saver_units_radix(self):
        g = Rig()
        g.keys('key_load', 'key_method', 'key_5')
        self.assertFalse(g.press('key_on'))
        self.assertIn('Method not stored', g.lcd()[3])
        g.step(6)
        self.assertNotIn('Method not stored', g.text())                              # refusals shown for 5 s
        g.call(g.A, 'options.keyboard', display_saver=True)
        g.step(1700)
        self.assertEqual(g.frame()['displays']['lcd']['brightness'], 100)
        g.step(200)
        self.assertEqual(g.frame()['displays']['lcd']['brightness'], 30)             # dimmed after 30 min without a key
        g.keys('key_front_inlet')
        self.assertEqual(g.frame()['displays']['lcd']['brightness'], 100)
        g.to('inlet_pres_psi')
        psi = float(g.lcd()[[i for i, ln in enumerate(g.lcd()) if ln.startswith('Pressure')][0]].split()[-1].rstrip('<'))
        g.call(g.A, 'options.keyboard', pressure_units='kPa')
        line = next(ln for ln in g.lcd() if ln.startswith('Pressure'))
        self.assertAlmostEqual(float(line.split()[-1].rstrip('<')), psi * rt.PSI / 1000.0, places=1)
        g.call(g.A, 'options.keyboard', radix='1,00')
        self.assertIn(',', next(ln for ln in g.lcd() if ln.startswith('Pressure')))

    def test_stopwatch_and_clock(self):
        g = Rig()
        g.keys('key_time')
        self.assertEqual(g.lcd()[0].strip(), '2026-01-05 08:25')
        g.to('time_stopwatch')
        g.keys('key_enter')
        g.step(65)
        self.assertTrue(g.lcd()[3].startswith('time='))
        self.assertTrue(g.lcd()[3].endswith('1:05<'))
        g.keys('key_enter')
        g.step(10)
        self.assertTrue(g.lcd()[3].endswith('1:05<'))
        g.keys('key_clear')
        self.assertTrue(g.lcd()[3].endswith('0:00<'))
        g.keys('key_config', 'key_time')
        g.to('cfg_time_time')
        self.assertTrue(g.enter('1345'))
        self.assertTrue(g.state('clock.datetime').endswith('T13:45:00'))

    def test_oven_program_post_run_and_ramp_leds(self):
        g = Rig()
        g.call(g.A, 'oven.set_initial_time', time_s=60.0)
        g.call(g.A, 'oven.set_ramp', index=1, rate_k_s=10 / 60.0, final_k=C0 + 100.0, final_time_s=60.0)
        g.call(g.A, 'oven.set_post_run', time_s=60.0, temperature_k=C0 + 120.0)
        g.keys('key_oven')
        g.to('oven_rate_1')
        g.keys('key_down')
        self.assertEqual(g.item(), 'oven_final_1')                                   # Final temp 1 appears after a non-zero rate
        g.step(200)
        g.keys('key_start')
        self.assertTrue(g.led('led_initial_temp')['on'])
        g.step(90)
        self.assertTrue(g.led('led_rate')['on'])
        g.step(100)
        self.assertTrue(g.led('led_final_temp')['on'])
        g.step(60)
        self.assertEqual(g.state('run.state'), 'post')                               # the run ends with the program (4 min)
        self.assertTrue(g.led('led_post_run')['on'])
        self.assertAlmostEqual(g.state('oven.setpoint_k'), C0 + 120.0)
        g.step(61)
        self.assertEqual(g.state('run.state'), 'idle')
        self.assertAlmostEqual(g.state('oven.setpoint_k'), C0 + 80.0)


# ---------------------------------------------------------------- firmware functions behind the menus
class Functions(unittest.TestCase):
    def test_column_calibration_corrects_a_trimmed_column(self):
        g = Rig()
        g.call(g.E, 'plant.configure', column_length_m=28.5)                        # 1.5 m trimmed off; the configuration still says 30 m
        g.step(60)
        actual = g.call(g.E, 'truth.inspect')['process']['actual_column_flow_m3_s']
        self.assertGreater(actual / ML, 1.03)                                        # more flow than the 1.000 shown
        g.keys('key_options')
        g.to('opt_cal')
        g.keys('key_enter')
        g.to('cal_column')
        g.keys('key_enter', 'key_mode_type', 'key_down', 'key_enter')               # mode Length
        g.to('cal_col_flow')
        self.assertTrue(g.enter(f'{actual / ML:.3f}'))                               # measured flow (NTP)
        cal = g.state('calibration.state')
        self.assertEqual(cal['column_mode'], 'length')
        self.assertAlmostEqual(cal['column']['length'], 28.5, delta=0.05)
        g.step(60)
        self.assertAlmostEqual(g.call(g.E, 'truth.inspect')['process']['actual_column_flow_m3_s'] / ML, 1.0, places=2)

    def test_flow_sensor_zero_and_gas_type(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', inlet_flow_offset_m3_s=2.0 * ML)
        g.step(1)
        split = g.call(g.E, 'truth.inspect')['process']['legacy']['flows_m3_s']['split_vent']
        self.assertAlmostEqual(split / ML, 18.0, places=6)                           # 20:1 x 1 mL/min less the uncorrected 2 mL/min
        self.assertAlmostEqual(g.state('inlet.split_flow_m3_s') / ML, 20.0, places=6)   # the reading still says 20
        g.keys('key_options')
        g.to('opt_cal')
        g.keys('key_enter', 'key_enter')
        g.to('cal_flow_zero')
        g.keys('key_on')
        g.step(1)
        self.assertAlmostEqual(g.call(g.E, 'truth.inspect')['process']['legacy']['flows_m3_s']['split_vent'] / ML, 20.0, places=6)
        g.call(g.E, 'plant.configure', carrier_gas='H2')                             # hydrogen supplied, helium configured
        g.step(30)
        self.assertGreater(g.call(g.E, 'truth.inspect')['process']['actual_column_flow_m3_s'] / ML, 1.5)
        g.keys('key_config', 'key_front_inlet')
        g.to('cfg_inlet_gas')
        g.keys('key_mode_type', 'key_down', 'key_enter')                             # Gas type: Hydrogen (p.147)
        g.step(30)
        self.assertAlmostEqual(g.call(g.E, 'truth.inspect')['process']['actual_column_flow_m3_s'] / ML, 1.0, places=3)

    def test_lit_offset_too_high_shuts_the_hydrogen_off(self):
        g = Rig()
        g.flame_on()
        g.to('det_output')
        self.assertTrue(g.lcd()[[i for i, ln in enumerate(g.lcd()) if ln.startswith('Output')][0]].endswith('10.0<'))
        g.keys('key_up')
        self.assertEqual(g.item(), 'det_lit_offset')
        self.assertTrue(g.enter('15'))                                               # above the lit baseline: false flame-out (p.167)
        g.step(30)
        self.assertIn('flame', g.state('status.shutdowns'))
        self.assertFalse(g.state('detector.method')['h2_on'])
        self.assertFalse(g.state('detector.flame_lit'))
        self.assertEqual(g.state('alarm.tone'), 'continuous')
        g.keys('key_clear')                                                          # Clear stops a continuous tone (p.13)
        self.assertEqual(g.state('alarm.tone'), 'none')
        self.assertGreater(g.state('emf.counters')['detector.ignitor']['value'], 3)

    def test_thermal_shutdown_needs_a_power_cycle(self):
        g = Rig()
        g.call(g.E, 'fault.inject', fault='oven_overtemp', active=True)
        g.keys('key_oven')
        fr = g.frame()['displays']['lcd']
        self.assertTrue(fr['blink'][1] and 'Off' in fr['lines'][1])
        self.assertFalse(g.press('key_off'))
        self.assertIn('Power cycle the GC', g.lcd()[3])
        g.call(g.E, 'fault.inject', fault='oven_overtemp', active=False)
        g.switch('off')
        g.switch('on')
        self.assertEqual(g.state('status.shutdowns'), {})

    def test_schedule_sleep_wake_and_clock_table(self):
        g = Rig()
        g.call(g.A, 'thermal.set_temperature', zone='oven', temperature_k=C0 + 60.0)
        g.call(g.A, 'method.store', slot='SLEEP')
        g.call(g.A, 'thermal.set_temperature', zone='oven', temperature_k=C0 + 150.0)
        g.keys('key_clock_table', 'key_enter')
        self.assertEqual(g.lcd()[0].strip(), 'INST SCHEDULE')
        self.assertTrue(g.lcd()[1].startswith('Go to sleep now.'))                   # p.111
        g.to('sched_add')
        g.keys('key_mode_type')                                                      # new item: day, function, time (p.111-112)
        g.keys('key_enter')                                                          # Monday
        g.keys('key_enter')                                                          # Go to Sleep
        self.assertTrue(g.enter('0840'))
        self.assertEqual(g.state('schedule.entries'), [dict(day='Monday', time_min=520, function='sleep')])
        self.assertTrue(g.led('led_clock_table')['on'])
        g.step(16 * 60)                                                              # the GC clock passes Monday 08:40
        self.assertTrue(g.state('schedule.asleep'))
        self.assertAlmostEqual(g.state('oven.method')['temp_k'], C0 + 60.0)
        g.keys('key_clock_table', 'key_enter')
        self.assertEqual(g.item(), 'sched_wake_restore')
        g.keys('key_enter')                                                          # Wake up now (restore method) (p.116)
        self.assertFalse(g.state('schedule.asleep'))
        self.assertAlmostEqual(g.state('oven.method')['temp_k'], C0 + 150.0)
        g.to('sched_0')
        g.keys('key_delete')
        self.assertIn('Delete this event? YES/NO', g.lcd()[3])                       # p.113
        g.keys('key_on')
        self.assertEqual(g.state('schedule.entries'), [])
        g.call(g.A, 'clock.add_event', time_hhmm=905, action='load_method', slot='SLEEP')
        g.step(30 * 60)
        self.assertAlmostEqual(g.state('oven.method')['temp_k'], C0 + 60.0)

    def test_emf_counter_service_due_and_logs(self):
        g = Rig()
        g.keys('key_service_mode', 'key_enter')
        g.to('svc_instrument')
        g.keys('key_enter')
        g.to('emf_instrument_run_count')
        g.keys('key_enter')
        g.to('emf_instrument_run_count_due')
        self.assertTrue(g.enter('1'))
        g.keys('key_start', 'key_stop')
        g.step(1)
        self.assertTrue(g.led('led_service_due')['on'])
        g.keys('key_service_mode', 'key_enter')
        g.to('svc_instrument')
        g.keys('key_enter')
        self.assertTrue(any(ln.startswith('*Run count') for ln in g.lcd()))         # passed counters are marked * (p.126)
        g.to('emf_instrument_run_count')
        g.keys('key_off')                                                            # Off/No resets the counter (p.126)
        self.assertFalse(g.led('led_service_due')['on'])
        g.keys('key_logs')
        g.to('logs_maintenance')
        g.keys('key_enter')
        self.assertIn('entries', g.lcd()[1])
        self.assertGreaterEqual(g.state('logs.counts')['maintenance'], 3)
        g.keys('key_logs')
        g.to('logs_event')
        g.keys('key_enter', 'key_down')
        self.assertIn('Power on successful', g.text())

    def test_run_log_records_keypad_changes_during_a_run(self):
        g = Rig()
        g.keys('key_start', 'key_oven')
        g.enter('85')
        self.assertTrue(g.led('led_run_log')['on'])                                  # p.35
        self.assertIn('oven temperature', g.state('logs.entries')['run'][0]['text'])
        g.keys('key_stop')
        g.step(600)
        g.keys('key_start')
        self.assertFalse(g.led('led_run_log')['on'])                                 # cleared at the start of each run

    def test_user_key_macro(self):
        g = Rig()
        g.keys('key_prog', 'key_user_1')                                             # record: [Prog][User Key 1] ... [Prog]
        g.keys('key_front_det', 'key_down')
        g.keys('key_prog')
        self.assertEqual(g.state('macro.keys')['1'], 2)
        g.keys('key_status', 'key_user_1')
        self.assertEqual(g.screen(), 'front_det')
        self.assertEqual(g.item(), 'det_h2')
        self.assertFalse(g.press('key_user_2'))
        self.assertIn('User key not programmed', g.lcd()[3])

    def test_column_compensation_and_analog_output(self):
        g = Rig()
        g.flame_on()
        g.call(g.A, 'oven.set_initial_time', time_s=120.0)
        g.keys('key_column_comp')
        g.to('comp_run')
        g.keys('key_enter')
        self.assertEqual(g.state('run.state'), 'blank')
        g.step(130)
        self.assertGreater(g.state('column_comp.points'), 100)
        g.keys('key_analog_out1', 'key_mode_type', 'key_down', 'key_enter')         # signal: front det less column comp
        self.assertEqual(g.state('analog.outputs')['1']['signal'], 'column_comp')
        self.assertAlmostEqual(g.state('analog.outputs')['1']['value_a'], 0.0, places=15)

    def test_gas_saver_prep_run_and_pulsed_splitless(self):
        g = Rig()
        g.call(g.A, 'inlet.configure', gas_saver=True, saver_flow_m3_s=10 * ML, saver_time_s=60.0)
        g.flame_on()
        g.keys('key_start')
        g.call(g.A, 'sample.aspirate', fraction=0.01)
        g.call(g.A, 'syringe.insert')
        g.call(g.A, 'syringe.depress')
        g.step(61)
        self.assertTrue(g.led('led_gas_saver')['on'])
        self.assertLess(g.state('inlet.split_flow_m3_s'), 10 * ML)
        g.keys('key_stop', 'key_prep_run')                                           # Prep Run restores normal flow (p.24)
        self.assertFalse(g.led('led_gas_saver')['on'])
        g.call(g.A, 'syringe.retract')
        g.keys('key_front_inlet', 'key_mode_type', 'key_down', 'key_down', 'key_down', 'key_enter')   # Pulsed splitless
        self.assertEqual(g.state('inlet.method')['mode'], 'pulsed_splitless')
        base = g.call(g.E, 'truth.inspect')['process']['actual_column_flow_m3_s']
        g.keys('key_start')
        g.step(1)
        self.assertGreater(g.call(g.E, 'truth.inspect')['process']['actual_column_flow_m3_s'], 1.5 * base)   # 25 psi pulse for 0.5 min
        g.step(40)
        self.assertAlmostEqual(g.call(g.E, 'truth.inspect')['process']['actual_column_flow_m3_s'] / base, 1.0, places=3)

    def test_ignore_ready_equilibration_and_heater_off(self):
        g = Rig()
        g.call(g.A, 'config.oven', equilibration_time_s=120.0)
        g.call(g.A, 'thermal.set_temperature', zone='oven', temperature_k=C0 + 90.0)
        for _ in range(200):
            g.step(5)
            if abs(g.state('oven.temperature_k') - (C0 + 90.0)) < 0.5:
                break
        self.assertIn('Oven equilibrating', g.status())                            # within the band, but not yet for 2 min (p.144)
        g.step(130)
        self.assertTrue(g.state('run.ready'))
        g.call(g.A, 'thermal.set_temperature', zone='inlet', temperature_k=C0 + 300.0)
        g.step(5)
        self.assertFalse(g.state('run.ready'))
        g.keys('key_config', 'key_front_inlet')
        g.to('cfg_inlet_ignore')
        g.keys('key_on')                                                             # Ignore Ready = TRUE (p.142)
        self.assertIn('TRUE', next(ln for ln in g.lcd() if ln.startswith('Ignore Ready')))
        g.step(6)
        self.assertTrue(g.state('run.ready'))

    def test_method_store_delete_and_sequence_delete(self):
        g = Rig()
        g.keys('key_method')
        g.to('store_9')
        g.keys('key_store')
        self.assertIn('Store method 9? YES/NO', g.lcd()[3])
        g.keys('key_on')
        self.assertIn('9', g.state('methods.stored'))
        g.keys('key_delete', 'key_on')
        self.assertNotIn('9', g.state('methods.stored'))
        g.call(g.A, 'sequence.store', slot='2')
        g.keys('key_seq')
        g.to('seqslot_2')
        g.keys('key_delete', 'key_off')
        self.assertIn('2', g.state('sequence.stored'))
        g.keys('key_delete', 'key_on')
        self.assertNotIn('2', g.state('sequence.stored'))


class MoreFunctions(unittest.TestCase):
    def test_column_flow_program_and_undefined_column(self):
        g = Rig()
        g.keys('key_col1')
        g.to('col_mode')
        g.keys('key_mode_type', 'key_down', 'key_enter')                             # Ramped flow (datasheet: flow program, 3 ramps)
        self.assertEqual(g.state('column.method')['mode'], 'ramped_flow')
        g.to('col_frate_1')
        self.assertTrue(g.enter('0.5'))
        g.to('col_ffinal_1')
        self.assertTrue(g.enter('2'))
        g.keys('key_start')
        g.step(120)
        self.assertAlmostEqual(g.state('column.flow_m3_s') / ML, 2.0, places=3)
        g.keys('key_stop')
        g.keys('key_config', 'key_col1')
        g.to('cfg_col_diam')
        self.assertTrue(g.enter('0'))                                                # 0 = column not defined (p.151)
        self.assertFalse(g.state('column.defined'))
        self.assertEqual(g.state('column.method')['mode'], 'constant_pressure')
        g.keys('key_front_inlet')
        g.to('inlet_split_ratio')
        self.assertFalse(g.enter('50'))
        self.assertIn('Column not defined', g.lcd()[3])

    def test_ignore_ignitor_manual_ignition_and_gas_lines(self):
        g = Rig()
        g.keys('key_config', 'key_front_det')
        g.to('cfg_det_ignitor')
        g.keys('key_on')                                                             # Ignore Ignitor = TRUE (p.168)
        g.keys('key_front_det')
        g.to('det_flame')
        g.keys('key_on')
        g.step(30)
        self.assertFalse(g.state('detector.flame_lit'))                              # the GC does not light it
        g.call(g.A, 'detector.manual_ignite')
        self.assertTrue(g.state('detector.flame_lit'))
        g.call(g.A, 'config.detector', ignore_ignitor=False)
        g.to('det_h2')
        g.keys('key_off')                                                            # H2 Off: the flame goes out
        self.assertFalse(g.state('detector.flame_lit'))
        g.keys('key_on')
        self.assertTrue(g.state('detector.method')['h2_on'])
        g.keys('key_front_inlet')
        g.to('inlet_temp')
        g.keys('key_off')                                                            # heater Off: not waited for
        self.assertFalse(g.state('inlet.method')['on'])
        self.assertTrue(g.lcd()[[i for i, ln in enumerate(g.lcd()) if ln.startswith('Temperature')][0]].endswith('Off<'))

    def test_auto_prep_door_leak_check_reboot_sleep_priority(self):
        g = Rig()
        g.keys('key_config')
        g.to('cfg_instrument')
        g.keys('key_enter')
        g.to('cfg_auto_prep')
        g.keys('key_on')
        g.step(1)
        self.assertTrue(g.state('run.prep'))                                         # Auto prep run (p.183)
        g.call(g.A, 'oven.set_door', open=True)
        self.assertIn('Oven door open', g.status())
        g.call(g.A, 'oven.set_door', open=False)
        g.keys('key_service_mode')
        g.to('svc_leak_check')
        g.keys('key_on')
        self.assertIn('Leak check passed', g.lcd()[3])
        g.call(g.E, 'fault.inject', fault='inlet_leak', active=True)
        g.keys('key_on')
        self.assertIn('Leak check failed', g.lcd()[3])
        g.keys('key_options')
        g.to('opt_comm')
        g.keys('key_enter')
        g.to('comm_ip')
        self.assertTrue(g.enter('192.168.1.20'))
        self.assertEqual(g.state('options.communication')['ip'], '10.1.1.101')
        g.to('comm_reboot')
        g.keys('key_on')
        self.assertIn('Reboot GC? Press On/Yes', g.lcd()[3])
        g.keys('key_on')                                                             # Reboot GC: On/Yes, On/Yes (p.198)
        self.assertEqual(g.state('options.communication')['ip'], '192.168.1.20')
        self.assertEqual(g.screen(), 'status')
        g.keys('key_clock_table', 'key_enter')
        g.keys('key_enter')                                                          # Go to sleep now (p.115)
        self.assertTrue(g.state('schedule.asleep'))
        g.keys('key_seq', 'key_seq')
        g.to('seq_use_prio')
        g.keys('key_on')
        self.assertTrue(g.state('sequence.active')['use_priority'])

    def test_auto_flow_zero_reset_calibration_delete_clock_event_logs(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', inlet_flow_offset_m3_s=1.0 * ML)
        g.keys('key_options')
        g.to('opt_cal')
        g.keys('key_enter', 'key_enter')
        g.to('cal_auto_flow_zero')
        g.keys('key_on')
        g.call(g.A, 'oven.set_initial_time', time_s=30.0)
        g.keys('key_start')
        g.step(31)                                                                   # the run ends; Auto flow zero runs (p.191)
        self.assertEqual(g.h.cal['flow_zero'], 1.0 * ML)
        g.call(g.A, 'calibration.column', mode='length', measured_flow_m3_s=1.01 * ML)
        g.keys('key_options')
        g.to('opt_cal')
        g.keys('key_enter')
        g.to('cal_column')
        g.keys('key_enter', 'key_mode_type', 'key_up', 'key_enter')                 # Not calibrated (first choice)
        self.assertIsNone(g.state('calibration.state')['column'])
        g.call(g.A, 'clock.add_event', time_min=600, action='prep_run')
        g.keys('key_clock_table')
        g.to('ct_events')
        g.keys('key_enter')
        g.to('clk_0')
        g.keys('key_delete', 'key_on')
        self.assertEqual(g.state('clock.events'), [])
        entries = g.call(g.A, 'logs.read', log='event')['entries']
        self.assertTrue(any('Run complete' in e['text'] for e in entries))


# ---------------------------------------------------------------- sync audit 2026-10-19: one error model, settings that act
class SyncFixes(unittest.TestCase):
    def test_septum_purge_controller_holds_its_reading(self):
        g = Rig()
        sp = g.state('inlet.method')['septum_purge']
        g.call(g.E, 'sensor.configure_error', septum_flow_offset_m3_s=0.5 * ML)
        g.step(1)
        truth = g.call(g.E, 'truth.inspect')['process']['legacy']['flows_m3_s']
        self.assertAlmostEqual(truth['septum_purge'], sp - 0.5 * ML, places=15)      # the actual flow carries the error
        self.assertAlmostEqual(g.state('inlet.septum_flow_m3_s'), sp, places=15)     # the reading holds the set point
        r = g.state('inlet.total_flow_m3_s', 'column.flow_m3_s', 'inlet.split_flow_m3_s')
        self.assertAlmostEqual(r['inlet.total_flow_m3_s'], r['column.flow_m3_s'] + r['inlet.split_flow_m3_s'] + sp, places=15)
        g.keys('key_front_inlet')
        g.to('inlet_septum')
        self.assertEqual(g.lcd()[3].split()[2:4], [f'{sp / ML:.1f}', f'{sp / ML:.1f}<'])   # the panel shows the same reading
        self.assertIn(f'Total flow        {r["inlet.total_flow_m3_s"] / ML:.1f}', g.text())
        g.call(g.A, 'calibration.zero', sensor='septum_purge_flow')                 # manual zero (p.191): the error is removed
        g.step(1)
        self.assertAlmostEqual(g.call(g.E, 'truth.inspect')['process']['legacy']['flows_m3_s']['septum_purge'], sp, places=15)
        self.assertAlmostEqual(g.state('inlet.septum_flow_m3_s'), sp, places=15)

    def test_fast_peaks_sets_the_analog_output_bandwidth(self):
        def decay(fast):
            g = Rig()
            g.flame_on()
            if fast:
                g.keys('key_config')
                g.to('cfg_analog1')
                g.keys('key_enter')
                g.to('cfg_analog1_fast')
                g.keys('key_on')
                self.assertTrue(g.state('analog.outputs')['1']['fast_peaks'])
            g.step(1)
            v0 = g.state('analog.outputs')['1']['value_a']
            self.assertAlmostEqual(v0, g.state('detector.output_a'), places=17)        # settled: the analog output follows the signal
            g.call(g.A, 'detector.configure', electrometer=False)                     # the signal drops to 0 at once
            g.step(0.05)
            self.assertEqual(g.state('detector.output_a'), 0.0)                       # digital output: not filtered (p.169)
            return g.state('analog.outputs')['1']['value_a'] / v0
        self.assertAlmostEqual(decay(False), np.exp(-0.05 * 2 * np.pi * 3.0), places=9)  # standard: 3.0 Hz bandwidth
        self.assertAlmostEqual(decay(True), np.exp(-0.05 * 2 * np.pi * 8.0), places=9)   # Fast peaks: 8 Hz

    def test_require_host_connection_keeps_the_gc_not_ready(self):
        g = Rig()
        g.flame_on()
        g.step(10)
        self.assertTrue(g.state('run.ready'))
        g.keys('key_config')
        g.to('cfg_instrument')
        g.keys('key_enter')
        g.to('cfg_require_host')
        g.keys('key_on')
        self.assertTrue(g.state('config.state')['instrument']['require_host'])
        g.step(1)
        self.assertFalse(g.state('run.ready'))                                       # no data system reports Ready (p.183)
        self.assertIn('Host not ready', g.status())
        self.assertEqual(g.call(g.A, 'run.start', expect='rejected')['error']['code'], 'NOT_READY')
        self.assertEqual(g.state('run.state'), 'idle')
        g.call(g.A, 'config.instrument', require_host=False)
        g.step(1)
        self.assertTrue(g.state('run.ready'))
        self.assertNotIn('Host not ready', g.status())

    def test_another_agents_run_cannot_be_stopped_or_changed(self):
        """Sweep 2026-10-19 (lead rule): a run started on the keypad belongs to the gesturing agent; another agent's Stop key, direct run.stop /
        setpoint / Load are refused (state unchanged) and the refusal is shown on the display; physical actions (oven door; the main power
        switch, lead clarification 10-19: switching off ends the run as on power loss) and the evaluator are not restricted; the owner stops its
        own run."""
        g = Rig()
        g.flame_on()
        g.keys('key_prep_run')
        g.step(30)
        g.call(g.A, 'sample.aspirate', fraction=0.01)
        g.call(g.A, 'syringe.insert')
        g.keys('key_start')                                                          # A starts the run on the keypad
        g.call(g.A, 'syringe.depress')
        g.call(g.A, 'syringe.retract')
        g.step(60)
        self.assertEqual(g.state('run.state'), 'run')
        self.assertEqual(g.h.run_owner, 'agent')
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        before = (g.state('run.state'), g.state('run.time_s'), g.state('oven.method'), g.state('power.on'))
        self.assertFalse(g.call(B, 'panel.press', input_id='key_stop')['accepted'])   # B's Stop key
        self.assertEqual(g.lcd()[-1].strip(), 'Run belongs to another user')         # frame and pixels
        for cap, args in (('run.stop', {}), ('thermal.set_temperature', dict(zone='oven', temperature_k=C0 + 80.0)),
                          ('method.load', dict(slot='1'))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual((g.state('run.state'), g.state('run.time_s'), g.state('oven.method'), g.state('power.on')), before)
        g.call(B, 'oven.set_door', open=False)                                       # physical action: not refused
        g.call(B, 'panel.press', input_id='key_oven')                                # navigation stays free
        g.keys('key_clear', 'key_clear')
        g.keys('key_stop')                                                           # the owner stops its own run
        self.assertEqual(g.state('run.state'), 'idle')
        g.call(B, 'run.prep')                                                        # B's commands are free again once the run has ended
        self.assertTrue(g.state('run.prep'))
        g.call(g.A, 'run.start')                                                     # A's next run ...
        self.assertEqual(g.state('run.state'), 'run')
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])   # ... B's power switch: physical
        self.assertFalse(g.state('power.on'))                                        # the GC is off and the run has ended
        self.assertEqual(g.state('run.state'), 'off')

    def test_evaluator_may_stop_an_agents_run(self):
        g = Rig()
        g.flame_on()
        g.step(30)
        g.call(g.A, 'run.start')
        self.assertEqual(g.h.run_owner, 'agent')
        g.call(g.E, 'run.stop')
        self.assertEqual(g.state('run.state'), 'idle')

    def test_language_chinese_is_kept_and_shown_but_texts_stay_english(self):
        g = Rig()
        g.call(g.A, 'options.keyboard', language='Chinese')
        self.assertEqual(g.state('options.state')['language'], 'Chinese')
        g.keys('key_options')
        g.to('opt_kd')
        g.keys('key_enter')
        g.to('kd_language')
        self.assertIn('Chinese', g.text())
        self.assertIn('Language', g.text())


# ---------------------------------------------------------------- rendering, evidence, contract
class RenderAndEvidence(unittest.TestCase):
    def test_render_determinism_and_png(self):
        a, b = Rig(), Rig()
        for g in (a, b):
            g.keys('key_oven')
            g.type('12')
        self.assertTrue(np.array_equal(a.h.panel.render('lcd'), b.h.panel.render('lcd')))
        self.assertEqual(a.h.panel.png_bytes('lcd'), b.h.panel.png_bytes('lcd'))
        a.step(0.5)                                                                  # blink phase: the * goes dark
        self.assertNotEqual(a.frame()['displays']['lcd']['shown'], b.frame()['displays']['lcd']['shown'])
        cap = a.call(a.A, 'panel.capture', display_id='lcd')
        self.assertTrue(Path(cap['image']['path']).is_file())
        self.assertEqual(cap['image']['width'], LCD['margin'] * 2 + LCD['cols'] * LCD['cell'][0])

    def test_evidence_pngs(self):
        OUT.mkdir(parents=True, exist_ok=True)
        g = Rig()
        g.flame_on()
        g.step(60)
        g.keys('key_status')
        g.h.panel.save_png('lcd', OUT / 'home.png')
        g.keys('key_oven')
        g.type('150')
        g.h.panel.save_png('lcd', OUT / 'menu.png')
        g.keys('key_clear', 'key_load', 'key_method', 'key_5')
        self.assertFalse(g.press('key_on'))
        self.assertIn('Method not stored', g.lcd()[3])
        g.h.panel.save_png('lcd', OUT / 'error.png')
        renders = {}
        for name in ('home', 'menu', 'error'):
            renders[name] = str((OUT / f'{name}.png').relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')

    def test_panel_capabilities_and_rollback(self):
        self.assertEqual(sorted(rt.PANEL_CAPS), ['panel.capture', 'panel.frame', 'panel.press', 'panel.switch'])
        self.assertEqual(rt.PANEL_CAPS['panel.frame']['visibility'], 'evaluator')
        self.assertEqual(set(pu.spec_capabilities(rt.PANEL_SPEC)) - set(rt.CAPS), set())
        g = Rig()
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        before = runtime_state(g.h)
        r = g.call(g.A, 'oven.set_ramp', expect='rejected', index=1, rate_k_s=0.001, final_k=C0 + 450.0, final_time_s=59000.0)
        self.assertEqual(r['error']['code'], 'RUN_TOO_LONG')
        self.assertEqual(runtime_state(g.h), before)                                 # refused: nothing changed
        self.assertEqual(g.call(g.A, 'injector.configure', expect='unsupported')['error']['code'], 'UNSUPPORTED')


class SurveyCoverage(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        pass

    def test_zz_all_survey_procedures_done(self):
        missing = [t for t in SURVEY_TASKS if t not in DONE_TASKS]
        if len(DONE_TASKS) == 0:
            self.skipTest('run with the key procedure tests')
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
