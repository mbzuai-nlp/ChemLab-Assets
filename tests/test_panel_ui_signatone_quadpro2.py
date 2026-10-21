"""Front-panel tests for CHEM-142 signatone_quadpro2 (the Keithley 2450 SourceMeter in the QuadPro2 rack: panel_ui.json + _panel_ui.py on
scripts/asset_runtime/signatone_quadpro2.py and its 2450 layer signatone_quadpro2_k2450.py): python3 -m pytest tests/test_panel_ui_signatone_quadpro2.py -q

Every gesture goes through the session API (panel.press / panel.turn / panel.touch / panel.switch; panel.frame for the evaluator); widgets are
found by id in the structured frame and tapped at their centre.  Themes: the key procedures of the menu survey done only with gestures and
checked on the runtime state, the frame and the pixels; panel = direct smu.* commands; remote control (read-only panel, prompt, OUTPUT key);
refusals shown as the device's popup / dialog; popups and the backlight dimmer in time; the menu functions (buffers, calculations, trigger
methods, templates, TriggerFlow blocks, sweeps, configuration lists, setups and macros, event log, communication, system settings, resets,
graph / histogram / reading table); render determinism and the evidence PNGs; contract entries."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from scripts.asset_runtime import signatone_quadpro2 as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

OUT = ROOT / 'outputs/panel_ui/signatone_quadpro2'
CONTRACT = rt.ASSET / 'asset.contract.json'
SCR = 'smu_screen'
SURVEY_TASKS = ['make a 4-wire resistance reading with Quick Setup', 'source a current and measure voltage manually', 'set 4-wire sense',
                'set the output-off state', 'change display digits', 'run an automatic wafer map (QuadPro2)']
DONE_TASKS = set()
RENDERS = {}


class Rig:
    """A QuadPro2 with a wafer under the probe; an agent and an evaluator session; panel gestures only through panel.* capabilities."""

    def __init__(self, contact=True, power=True):
        self.h = rt.SignatoneQuadpro2Runtime()
        self.h.panel_capture_dir = str(OUT / 'captures')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        for c, a in [('plant.configure', rt.PLANT_EXAMPLE), ('wafer.configure', rt.WAFER_EXAMPLE), ('probe.configure', rt.PROBE_EXAMPLE),
                     ('sample.load', rt.SAMPLE_EXAMPLE), ('enclosure.set_open', {'opened': False})]:
            self.call(self.E, c, **a)
        if power:
            self.switch('on')
        self.step(3.0)
        if contact:
            self.call(self.A, 'probe.set_contact', contact=True)
            self.step(3.0)

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(instance_id=self.h.instance_id, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def val(self, sid):
        return self.E.read(instance_id=self.h.instance_id, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        self.h.step(dt)

    # ---- gestures
    def press(self, key, **kw):
        return self.call(self.A, 'panel.press', input_id=key, **kw)['accepted']

    def turn(self, n):
        return self.call(self.A, 'panel.turn', input_id='nav_knob', detents=n)['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCR, x=int(x), y=int(y), **kw)['accepted']

    def swipe(self, direction):
        a, b = {'left': ((600, 350), (200, 350)), 'right': ((200, 350), (600, 350)), 'down': ((400, 300), (400, 420)), 'up': ((400, 420), (400, 300))}[direction]
        return self.touch(*a, to_x=b[0], to_y=b[1])

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCR]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        return self.frame()['ui']['page']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        """Tap a button of the top overlay (key, option, dialog button) by its label."""
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def type(self, text):
        for ch in text:
            self.ov(ch)
        return self.ov('OK')

    def row(self, wid, first):
        """Tap the row of a form or list widget whose first cell is this text (dragging the rows until it shows)."""
        for _ in range(20):
            w = self.widgets()[wid]
            firsts = [c[0] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                x, y, ww, _ = w['rect']
                return self.touch(x + ww // 2, y + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            if w['scroll'] + hh // w['row_h'] >= w['count']:
                break
            self.touch(x + ww // 2, y + hh - 10, to_x=x + ww // 2, to_y=y + 10)
        raise AssertionError(f'{first!r} not in {wid}: {firsts}')

    def menu(self, icon):
        self.press('menu_key')
        return self.tap(f'mn_{icon}')

    def settings(self):
        return self.val('smu.settings')

    def capture(self, name):
        info = self.h.panel.save_png(SCR, OUT / f'{name}.png')
        RENDERS[name] = dict(file=f'{name}.png', sha256=info['sha256'], page=self.page(), time_s=self.h.time_s)
        return info


class KeyProcedures(unittest.TestCase):
    def test_quickset_ohmmeter_4_wire(self):
        """p.124: QUICKSET, Ohmmeter, 4-wire; the output turns on and readings start; OUTPUT ON/OFF ends it."""
        g = Rig()
        g.press('terminals_key')                                                # the probe cable is on the rear triax (QuadPro2 wiring)
        self.assertEqual(g.settings()['globals']['terminals'], 'rear')
        g.press('quickset_key')
        self.assertEqual(g.page(), 'quickset')
        g.tap('qs_ohm')
        g.ov('4-Wire')
        st = g.settings()
        self.assertEqual((st['function'], st['meas']['measure.sense'], g.val('smu.state')['output']), ('SIMR', '4W', True))
        g.step(1.0)
        net = g.h._smu_network()
        r = g.val('smu.reading')
        self.assertEqual(r['function'], 'resistance')
        i = 1e-7                                                                # Ohmmeter quick setup: 0.1 uA source (p.49)
        expect = (rt.PLANT_EXAMPLE['voltage_gain'] * i * net['r_sense'] + rt.PLANT_EXAMPLE['voltage_offset_v']) / (rt.PLANT_EXAMPLE['current_gain'] * i)
        self.assertAlmostEqual(r['value'], expect, delta=0.05)                  # the thermal offset is in the reading ...
        g.call(g.A, 'smu.set', setting='measure.offset_comp', value=True)
        g.step(0.5)
        self.assertAlmostEqual(g.val('smu.reading')['value'], rt.PLANT_EXAMPLE['voltage_gain'] * net['r_sense'], delta=0.05)   # ... until offset compensation
        self.assertEqual(g.page(), 'home')
        self.assertIn('Ω', g.text('m_value'))
        self.assertEqual(g.text('m_title'), 'MEASURE RESISTANCE 4-WIRE')
        g.capture('home')
        g.press('output_key')
        self.assertFalse(g.val('smu.state')['output'])
        self.assertEqual(g.text('s_value'), 'Output Off')
        DONE_TASKS.add(SURVEY_TASKS[0])

    def test_source_current_measure_voltage(self):
        """p.125-126: FUNCTION source current / measure voltage, Source and Limit with the keypad, measure range, OUTPUT ON."""
        g = Rig()
        g.press('terminals_key')
        g.press('function_key')
        g.tap('fn_IV')
        self.assertEqual(g.settings()['function'], 'SIMV')
        g.press('home_key')
        g.tap('s_level')
        self.assertEqual(g.overlay()['kind'], 'keyboard')
        g.type('1E-4')
        g.tap('s_limit')
        g.type('2')
        g.tap('m_range')
        g.ov('200mV')
        st = g.settings()
        self.assertEqual((st['src']['source.level'], st['src']['source.limit'], st['meas']['measure.range_auto'], st['meas']['measure.range']),
                         (1e-4, 2.0, False, 0.2))
        g.press('output_key')
        g.step(0.5)
        v = g.val('smu.reading')['value']
        net = g.h._smu_network()
        self.assertAlmostEqual(v, 1.03 * 1e-4 * net['r_force'] + 2e-6, delta=2e-7)    # 2-wire: the force voltage
        self.assertEqual(g.text('s_value'), '+100.000 µA')
        self.assertEqual(g.text('s_limit'), '2.00000V')
        led = g.frame()['displays']['output_led']
        self.assertTrue(led['on'])
        DONE_TASKS.add(SURVEY_TASKS[1])

    def test_set_4_wire_sense_and_output_off_state(self):
        """p.53 (MENU > Measure Settings > Sense) and p.122 (MENU > Source Settings > Output Off, then HOME)."""
        g = Rig()
        g.menu('meas_settings')
        self.assertEqual(g.page(), 'meas_settings')
        g.row('w_meas', 'Sense')
        g.ov('4-Wire')
        self.assertEqual(g.settings()['meas']['measure.sense'], '4W')
        DONE_TASKS.add(SURVEY_TASKS[2])
        g.menu('src_settings')
        g.row('w_src', 'Output Off')
        g.ov('Hi Impedance')
        g.press('home_key')
        self.assertEqual((g.settings()['src']['source.offmode'], g.page()), ('high_z', 'home'))
        DONE_TASKS.add(SURVEY_TASKS[3])

    def test_display_digits_from_the_settings_swipe(self):
        """p.70: swipe to SETTINGS, Display Digits, select the digits."""
        g = Rig()
        g.swipe('left')
        self.assertEqual(g.frame()['ui']['vars']['swipe'], 'settings')
        g.capture('settings_swipe')
        g.tap('b_digits')
        g.ov('6.5')
        self.assertEqual(g.settings()['meas']['measure.digits'], 6)
        g.step(0.2)
        self.assertEqual(len(g.text('m_value').split()[0].replace('+', '').replace('-', '').replace('.', '')), 7)   # 6.5 digits: 7 places
        DONE_TASKS.add(SURVEY_TASKS[4])

    def test_wafer_map_puts_the_2450_in_remote(self):
        """The QuadPro2 software maps the wafer through the 2450 (p.2-3 of the data sheet): remote indicator, its readings on the display,
        the panel read-only with a prompt, and taking local control ends the job."""
        g = Rig(contact=False)
        for c, a in [('method.configure', dict(rt.METHOD_EXAMPLE, require_calibration=False)), ('map.configure', rt.MAP_EXAMPLE)]:
            g.call(g.A, c, **a)
        g.call(g.A, 'acquisition.start')
        g.step(8.0)
        self.assertEqual(g.text('st_comm'), 'USBTMC')
        self.assertTrue(g.frame()['displays']['remote_led']['on'])
        self.assertEqual(g.text('m_title'), 'MEASURE VOLTAGE 4-WIRE')
        self.assertGreater(g.val('smu.buffers')['list'][0]['count'], 0)
        g.capture('remote')
        g.swipe('left')                                                         # viewing and swiping stay possible (p.160)
        self.assertEqual(g.frame()['ui']['vars']['swipe'], 'settings')
        n = g.val('smu.buffers')['list'][0]['readings_total']
        g.tap('t_filter')
        self.assertEqual(g.overlay()['dialog'], 'remote')
        self.assertFalse(g.settings()['meas']['measure.filter'])
        g.capture('error')
        g.ov('No')
        self.assertEqual(g.h.phase, 'running')
        g.step(300.0)
        self.assertEqual(g.h.phase, 'succeeded')
        self.assertGreater(g.val('smu.buffers')['list'][0]['readings_total'], n)
        DONE_TASKS.add(SURVEY_TASKS[5])

    def test_taking_local_control_cancels_a_running_map(self):
        g = Rig(contact=False)
        g.call(g.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, require_calibration=False))
        g.call(g.A, 'map.configure', **rt.MAP_EXAMPLE)
        g.call(g.A, 'acquisition.start')
        g.step(5.0)
        g.press('menu_key')
        g.tap('mn_src_settings')
        g.row('w_src', 'High Capacitance')
        g.ov('On')
        self.assertEqual(g.overlay()['dialog'], 'remote')
        g.ov('Yes')
        self.assertEqual((g.h.phase, g.h.error, g.val('smu.state')['control']), ('cancelled', 'LOCAL_CONTROL', 'local'))
        self.assertFalse(g.frame()['displays']['remote_led']['on'])

    def test_output_key_in_remote_switches_to_local(self):
        g = Rig(contact=False)
        g.call(g.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, require_calibration=False))
        g.call(g.A, 'map.configure', **rt.MAP_EXAMPLE)
        g.call(g.A, 'acquisition.start')
        g.step(5.0)
        self.assertTrue(g.press('output_key'))
        self.assertEqual((g.h.phase, g.val('smu.state')['control'], g.val('smu.state')['output']), ('cancelled', 'local', False))


class PanelEqualsDirect(unittest.TestCase):
    def test_same_state_as_the_smu_commands(self):
        g, d = Rig(), Rig()
        g.press('terminals_key')
        g.press('function_key')
        g.tap('fn_VI')
        g.press('home_key')
        g.tap('s_level')
        g.type('0.01')
        g.swipe('left')
        g.tap('t_filter')
        g.tap('b_nplc')
        g.type('2')
        g.press('output_key')
        for k, v in (('terminals', 'rear'), ('function', 'SVMI'), ('source.level', 0.01), ('measure.filter', True), ('measure.nplc', 2.0)):
            d.call(d.A, 'smu.set', setting=k, value=v)
        d.call(d.A, 'smu.output', on=True)
        g.step(2.0)
        d.step(2.0)
        self.assertEqual(g.settings(), d.settings())
        self.assertEqual(g.val('smu.reading'), d.val('smu.reading'))
        self.assertEqual(g.h.invoke('ledger.read'), d.h.invoke('ledger.read'))
        st = g.val('smu.buffers')['list'][0]
        self.assertEqual(st['count'], d.val('smu.buffers')['list'][0]['count'])


class Refusals(unittest.TestCase):
    def test_power_off_only_the_switch(self):
        g = Rig(power=False, contact=False)
        self.assertEqual(g.frame()['screen'], 'off')
        self.assertFalse(g.press('menu_key'))
        self.assertFalse(g.touch(400, 240))
        self.assertTrue(g.switch('on'))
        self.assertEqual((g.frame()['screen'], g.page()), ('gui', 'home'))
        self.assertTrue(g.val('power.enabled'))

    def test_unavailable_setting_logs_and_pops_up(self):
        g = Rig()
        g.call(g.A, 'smu.set', expect='rejected', setting='measure.offset_comp', value=True)
        ev = g.val('smu.events')
        self.assertEqual((ev['entries'][0]['severity'], ev['popup']['severity']), ('error', 'error'))
        self.assertTrue(any(w['id'] == 'popup' for w in g.disp()['widgets']))
        self.assertEqual(g.text('st_event'), '!')
        g.step(3.5)
        self.assertFalse(any(w['id'] == 'popup' for w in g.disp()['widgets']))          # popups are brief (3 s)
        self.assertEqual(g.val('smu.display')['event'], 'error')                       # until the event log is viewed
        g.menu('eventlog')
        self.assertEqual(g.val('smu.display')['event'], 'none')
        self.assertIn('Settings conflict', g.widgets()['ev_list']['cells'][0][2])

    def test_keypad_refuses_out_of_range_and_default_buffers_stay(self):
        g = Rig()
        g.tap('s_limit')
        g.type('500')
        self.assertEqual(g.overlay()['error'], 'Out of range')
        g.ov('Cancel')
        self.assertEqual(g.settings()['src']['source.limit'], 105e-6)
        g.menu('buffers')
        g.row('buf_list', 'defbuffer1')
        self.assertEqual(g.page(), 'buffer_cfg')
        g.tap('bc_delete')
        self.assertIn('defbuffer1', [b['name'] for b in g.val('smu.buffers')['list']])
        self.assertEqual(g.val('smu.events')['entries'][0]['text'], 'Settings conflict: DEFAULT_BUFFER')

    def test_trigger_key_in_continuous_asks_for_the_method(self):
        g = Rig()
        g.press('trigger_key')
        ov = g.overlay()
        self.assertEqual(ov['dialog'], 'trig_method')
        g.ov('Manual Trigger Mode')
        self.assertEqual(g.text('st_trigger'), 'MAN')
        n = g.val('smu.buffers')['list'][0]['readings_total']
        g.step(5.0)
        self.assertEqual(g.val('smu.buffers')['list'][0]['readings_total'], n)        # manual: no readings until TRIGGER
        g.press('trigger_key')
        self.assertEqual(g.val('smu.buffers')['list'][0]['readings_total'], n + 1)
        g.press('trigger_key', duration_s=2.5)                                    # hold 2 s: the method dialog (p.242)
        self.assertEqual(g.overlay()['dialog'], 'trig_method')

    def test_usb_functions_show_the_missing_flash_drive(self):
        g = Rig()
        g.press('enter_key', held=['home_key'])
        self.assertEqual(g.overlay()['dialog'], 'no_usb')
        g.ov('OK')
        g.menu('scripts_manage')
        g.tap('man_to_usb')
        self.assertEqual(g.overlay()['dialog'], 'no_usb')


class TimeAndDisplay(unittest.TestCase):
    def test_dimmer_and_brightness(self):
        g = Rig()
        self.assertEqual(g.disp()['brightness'], 50)                               # remote default ON50 (p.381)
        g.menu('sys_settings')
        g.row('w_sys', 'Backlight Dimmer')
        g.ov('1 Hour')
        g.step(3599.0)
        self.assertEqual(g.disp()['brightness'], 50)
        g.step(2.0)
        self.assertEqual(g.disp()['brightness'], 25)
        g.press('home_key')
        self.assertEqual(g.disp()['brightness'], 50)

    def test_no_menu_time_out_and_render_determinism(self):
        g = Rig()
        g.menu('calc')
        g.step(600.0)
        self.assertEqual(g.page(), 'calc')
        a = g.h.panel.render(SCR)
        self.assertTrue((a == g.h.panel.render(SCR)).all())
        cap = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertEqual((cap['image']['width'], cap['image']['height']), (800, 480))
        self.assertTrue(Path(cap['image']['path']).is_file())
        g.press('menu_key')
        g.capture('menu')

    def test_interlock_and_terminal_leds(self):
        g = Rig()
        f = g.frame()['displays']
        self.assertTrue(f['interlock_led']['on'])
        self.assertEqual(f['terminals_led']['color'], 'green')
        g.call(g.E, 'probe.set_contact', contact=False)
        g.step(3.0)
        g.call(g.E, 'enclosure.set_open', opened=True)
        self.assertFalse(g.frame()['displays']['interlock_led']['on'])
        g.press('terminals_key')
        self.assertEqual(g.frame()['displays']['terminals_led']['color'], 'yellow')

    def test_interlock_limits_the_output_to_42_v(self):
        g = Rig(contact=False)
        g.call(g.E, 'enclosure.set_open', opened=True)
        for k, v in (('source.limit', 1.05), ('source.level', 100.0)):
            g.call(g.A, 'smu.set', setting=k, value=v)
        g.call(g.A, 'smu.output', on=True)
        self.assertEqual(g.val('smu.events')['entries'][0]['code'], 5074)
        self.assertEqual(g.h.smu.solve(None)['v_force'], 42.0)                   # p.109: below 42 V without the interlock
        g.call(g.E, 'enclosure.set_open', opened=False)
        g.step(3.0)
        self.assertEqual(g.h.smu.solve(None)['v_force'], 100.0)


class MenuFunctions(unittest.TestCase):
    def test_reading_buffers_new_resize_fill_once(self):
        g = Rig()
        g.menu('buffers')
        g.tap('buf_new')
        g.row_dialog = None
        ov = g.overlay()
        self.assertEqual(ov['dialog'], 'new_buffer')
        x, y, w, h = next(b for b in ov['buttons'] if b['id'] == 'row:name')['rect']
        g.touch(x + w // 2, y + h // 2)
        g.ov('{shift}') if False else None
        for ch in 'run':
            g.ov(ch)
        g.ov('OK')
        x, y, w, h = next(b for b in g.overlay()['buttons'] if b['id'] == 'row:capacity')['rect']
        g.touch(x + w // 2, y + h // 2)
        g.ov('Del')
        g.type('20')
        g.ov('Create')
        b = {x['name']: x for x in g.val('smu.buffers')['list']}
        self.assertEqual((b['run']['capacity'], b['run']['fill_mode'], g.val('smu.buffers')['active']), (20, 'once', 'run'))
        g.step(5.0)
        b = {x['name']: x for x in g.val('smu.buffers')['list']}
        self.assertEqual(b['run']['count'], 20)
        self.assertIn(4915, [e['code'] for e in g.val('smu.events')['entries']])        # fill once: full buffer stops (p.170)
        g.press('home_key')
        g.tap('st_buffer')
        g.row('bs_list', 'defbuffer2')
        self.assertEqual(g.val('smu.buffers')['active'], 'defbuffer2')

    def test_calculations_rel_filter_math_limits(self):
        g = Rig()
        g.press('terminals_key')
        g.press('function_key')
        g.tap('fn_IV')
        g.press('home_key')
        g.tap('s_level')
        g.type('1E-4')
        g.press('output_key')
        g.step(1.0)
        raw = g.val('smu.reading')['raw']
        g.menu('calc')
        g.row('w_calc', 'Rel Acquire')
        m = g.settings()['meas']
        self.assertEqual((m['measure.rel'], m['measure.rel_level']), (True, raw))
        g.step(0.5)
        self.assertAlmostEqual(g.val('smu.reading')['value'], 0.0, delta=1e-9)
        g.row('w_calc', 'Rel')
        g.ov('Off')
        g.row('w_calc', 'Math')
        g.ov('On')
        g.row('w_calc', 'Math Format')
        g.ov('mx+b')
        g.row('w_calc', 'm(Scalar)')
        g.ov('Del')
        g.type('1000')
        g.row('w_calc', 'Limit 1')
        g.ov('On')
        g.row('w_calc', 'Limit 1 Low Value')
        g.ov('Del'), g.ov('Del')
        g.type('0')
        g.row('w_calc', 'Limit 1 High Value')
        g.ov('Del')
        g.type('1')
        g.step(0.5)
        r = g.val('smu.reading')
        self.assertAlmostEqual(r['value'], raw * 1000, delta=1e-6)
        self.assertEqual((r['unit'], 'L1FAIL' in r['flags']), ('X', raw * 1000 > 1))
        g.press('home_key')
        self.assertIn('MATH', g.text('m_ind'))

    def test_simple_loop_template_runs_with_trigger(self):
        g = Rig()
        g.menu('trig_templates')
        g.row('w_templ', 'Templates')
        g.ov('SimpleLoop')
        g.row('w_templ', 'Count')
        g.ov('Del'), g.ov('Del')
        g.type('5')
        self.assertEqual(g.val('smu.trigger')['params']['count'], 5)
        g.press('trigger_key')
        self.assertEqual(g.val('smu.trigger')['state'], 'running')
        g.step(2.0)
        tr = g.val('smu.trigger')
        self.assertEqual((tr['state'], g.val('smu.buffers')['list'][0]['count']), ('idle', 5))
        g.menu('trig_config')
        self.assertEqual([c[1] for c in g.widgets()['tc_blocks']['cells']], ['Buffer Clear', 'Delay', 'Measure', 'Branch Counter'])
        g.row('tc_blocks', '2')
        g.tap('tc_delay')
        g.type('0.5')
        self.assertEqual(g.val('smu.trigger')['blocks'][1]['delay_s'], 0.5)
        g.row('tc_blocks', '4')
        g.tap('tc_ins_after')
        g.ov('Log Event')
        self.assertEqual(g.val('smu.trigger')['blocks'][4]['name'], 'Log Event')
        g.tap('tc_delete')
        g.tap('tc_clear')
        self.assertEqual(g.val('smu.trigger')['blocks'], [])
        g.capture('trigger_model')

    def test_sweep_generate_and_run(self):
        g = Rig()
        g.press('terminals_key')
        g.menu('sweep')
        g.row('w_sweep', 'Stop')
        g.ov('Del')
        g.type('0.01')
        g.row('w_sweep', 'Points')
        g.ov('Del'), g.ov('Del')
        g.type('5')
        g.row('w_sweep', 'Delay Auto')                                          # 0.2 s per point: the plant's 10 ms voltage response settles
        g.ov('Off')
        g.row('w_sweep', 'Delay')
        g.ov('Del')
        g.type('0.2')
        g.tap('sweep_generate')
        self.assertEqual(g.val('smu.lists')['source'], {'VoltLinSweepList': 5})
        self.assertEqual(g.val('smu.trigger')['template'], 'Sweep')
        g.press('trigger_key')
        g.step(5.0)
        rows = g.val('smu.buffers')['recent']
        self.assertEqual(len(rows), 5)
        self.assertEqual([round(r['level'], 6) for r in g.val('smu.lists')['details']['source']], [0.0, 0.0025, 0.005, 0.0075, 0.01])
        self.assertEqual([round(r['source'], 6) for r in rows], [round(1.03 * x + 2e-6, 6) for x in (0.0, 0.0025, 0.005, 0.0075, 0.01)])   # readback
        self.assertFalse(g.val('smu.state')['output'])

    def test_config_lists_store_and_recall(self):
        g = Rig()
        g.menu('src_lists')
        g.row('w_source_lists', 'New List')
        for ch in 'abc':
            g.ov(ch)
        g.ov('OK')
        g.tap('source_store')
        g.call(g.A, 'smu.set', setting='source.level', value=1.5)
        g.tap('source_store')
        self.assertEqual(g.val('smu.lists')['source'], {'abc': 2})
        g.tap('source_recall')
        g.type('1')
        self.assertEqual(g.settings()['src']['source.level'], 0.0)
        g.row('source_idx', '2')
        g.tap('source_details')
        self.assertIn('level 1.5', g.overlay()['lines'])

    def test_setup_and_macro_scripts(self):
        g = Rig()
        g.call(g.A, 'smu.set', setting='measure.nplc', value=5.0)
        g.menu('scripts_setup')
        g.tap('cs_create')
        for ch in 'myset':
            g.ov(ch)
        g.ov('OK')
        g.menu('scripts_record')
        g.tap('rec_start')
        self.assertEqual(g.val('smu.display')['script'], 'Recording')
        g.call(g.A, 'smu.set', setting='measure.digits', value=3)
        g.press('home_key')
        g.tap('st_script')
        g.ov('Stop Macro')
        for ch in 'mac':
            g.ov(ch)
        g.ov('OK')
        self.assertEqual(g.val('smu.scripts')['kinds'], {'mac': 'macro', 'myset': 'setup'})
        g.call(g.A, 'smu.system', action='reset')
        self.assertEqual(g.settings()['meas']['measure.nplc'], 1.0)
        g.menu('scripts_run')
        g.row('run_list', 'myset')
        g.tap('run_selected')
        self.assertEqual(g.settings()['meas']['measure.nplc'], 5.0)
        g.row('run_list', 'mac')
        g.tap('run_powerup')
        g.switch('off')
        g.switch('on')
        self.assertEqual(g.settings()['meas']['measure.digits'], 3)                  # autoexec ran at power-up
        self.assertEqual(g.settings()['meas']['measure.nplc'], 1.0)

    def test_system_communication_and_resets(self):
        g = Rig()
        g.menu('comm_gpib')
        g.row('w_gpib', 'Address')
        g.ov('Del'), g.ov('Del')
        g.type('7')
        g.tap('tab_comm_lan')
        g.row('w_lan', 'TCP/IP Mode')
        g.ov('Manual')
        g.row('w_lan', 'Apply Settings')
        self.assertEqual(g.val('smu.state')['lan_applied']['comm.lan_mode'], 'manual')
        g.tap('tab_comm_tsplink')
        g.row('w_tsp', 'Initialize')
        self.assertEqual(g.val('smu.state')['tsplink_nodes'], 1)
        g.menu('sys_settings')
        g.row('w_sys', 'Date')
        ov = g.overlay()
        self.assertEqual(ov['kind'], 'spinner')
        g.ov('+')
        g.ov('OK')
        self.assertTrue(g.val('smu.state')['datetime'].startswith('2026-01-02'))
        g.row('w_sys', 'Reading Format')
        g.ov('Exponent')
        g.step(0.2)
        self.assertIn('E', g.val('smu.display')['reading'])
        g.menu('info_manage')
        g.row('w_info', 'System Reset')
        g.ov('Yes')
        self.assertEqual(g.settings()['nv']['comm.gpib_address'], 7)                 # nonvolatile
        self.assertEqual(g.settings()['nv']['system.reading_format'], 'exponent')
        g.call(g.A, 'smu.set', setting='system.password', value='secret')
        g.row('w_info', 'Password Reset')
        self.assertEqual(g.settings()['nv']['system.password'], 'admin')

    def test_views_graph_histogram_table_and_help(self):
        g = Rig()
        g.step(3.0)
        g.menu('graph')
        plot = g.widgets()['graph_plot']
        self.assertGreater(len(plot['series'][0]['points']), 10)
        g.tap('tab_graph_data')
        g.row('w_gdata', 'Add Trace')
        g.ov('defbuffer2')
        self.assertEqual(g.settings()['globals']['graph.traces'], ['defbuffer2'])
        g.tap('tab_graph_scale')
        g.row('w_gscale', 'Y-Axis Scale Format')
        g.ov('Log')
        g.menu('histogram')
        self.assertTrue(g.widgets()['hist_plot']['series'][0]['points'])
        g.menu('table')
        g.row('table_rows', g.widgets()['table_rows']['cells'][0][0])
        self.assertEqual(g.overlay()['dialog'], 'reading_detail')
        g.press('exit_key')
        g.press('help_key')
        self.assertIn('Readings of the selected buffer', ' '.join(g.overlay()['lines']))

    def test_knob_and_enter_select_on_the_menu(self):
        g = Rig()
        g.press('menu_key')
        g.turn(2)
        self.assertEqual(g.frame()['ui']['focus'], 'mn_src_settings')
        g.press('enter_key')
        self.assertEqual(g.page(), 'src_settings')
        g.press('exit_key')
        self.assertEqual(g.page(), 'menu')
        g.press('exit_key')
        self.assertEqual(g.page(), 'home')


def mapping(g):
    """A wafer map started directly by the agent session A (the 2450 goes to remote control)."""
    g.call(g.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, require_calibration=False))
    g.call(g.A, 'map.configure', **rt.MAP_EXAMPLE)
    op = g.call(g.A, 'acquisition.start')['operation_id']
    g.step(5.0)
    return op


def source_current(g, level=1e-4, method='manual'):
    for k, v in (('terminals', 'rear'), ('function', 'SIMV'), ('source.level', level), ('trigger_method', method)):
        g.call(g.A, 'smu.set', setting=k, value=v)


class CallerRules(unittest.TestCase):
    """A panel gesture acts as the session's caller: another agent may not end a running map by the power switch, the OUTPUT key or by
    taking local control from the remote dialog, as the direct power.set / smu.output / smu.system local are refused (ACCESS_DENIED)."""

    def test_foreign_power_output_and_local_takeover_refused(self):
        g = Rig(contact=False)
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        op = mapping(g)
        state = lambda: (g.h.phase, g.val('smu.state')['control'], g.val('power.enabled'))   # noqa: E731
        before = state()
        self.assertEqual(before, ('running', 'USBTMC', True))
        self.assertFalse(g.call(B, 'panel.press', input_id='output_key')['accepted'])
        g.press('menu_key')
        g.tap('mn_src_settings')
        g.row('w_src', 'High Capacitance')
        g.ov('On')
        self.assertEqual(g.overlay()['dialog'], 'remote')
        w = next(b for b in g.overlay()['buttons'] if b['label'] == 'Yes')
        x, y, ww, hh = w['rect']
        self.assertFalse(g.call(B, 'panel.touch', display_id=SCR, x=x + ww // 2, y=y + hh // 2)['accepted'])   # B's Yes
        for cap, args in (('smu.output', dict(on=False)), ('smu.system', dict(action='local')),
                          ('smu.login', dict(password='admin')), ('acquisition.stop', {})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(state(), before)
        self.assertEqual(g.A.poll(op)['status'], 'running')
        self.assertTrue(g.press('output_key'))                                  # the owner's own OUTPUT key ends its map (p.608)
        self.assertEqual((g.h.phase, g.h.error), ('cancelled', 'LOCAL_CONTROL'))

    def test_power_switch_is_physical(self):
        g = Rig(contact=False)
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        op = mapping(g)
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])   # never refused
        self.assertFalse(g.val('power.enabled'))
        self.assertNotEqual(g.A.poll(op)['status'], 'running')                  # the map ends as on a power loss

    def test_refusal_opens_the_access_denied_dialog(self):
        g = Rig(contact=False)
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        mapping(g)
        self.assertFalse(g.call(B, 'panel.press', input_id='output_key')['accepted'])
        ov = g.overlay()                                                         # assumed text, the 2450's dialog style
        self.assertEqual((ov['dialog'], g.h.phase, g.val('smu.state')['control']), ('denied', 'running', 'USBTMC'))
        g.ov('OK')
        self.assertIsNone(g.overlay())


class AccessMode(unittest.TestCase):
    """System Settings Access Mode (p.160-161): in Protected / Lockout the front panel needs the password to take control from the remote
    interface; Full / Exclusive do not.  OUTPUT ON/OFF stays always active (p.608)."""

    def test_protected_asks_for_the_password(self):
        g = Rig(contact=False)
        g.call(g.A, 'smu.set', setting='system.password', value='secret')
        g.call(g.A, 'smu.set', setting='system.access_mode', value='protected')
        g.call(g.A, 'smu.remote_login', password='secret')                     # the software logs in on its interface (p.161-162)
        mapping(g)
        g.press('menu_key')
        g.tap('mn_src_settings')
        g.row('w_src', 'High Capacitance')
        g.ov('On')
        g.ov('Yes')
        ov = g.overlay()
        self.assertEqual((ov['kind'], g.h.phase, g.val('smu.state')['control']), ('keyboard', 'running', 'USBTMC'))
        g.type('wrong')
        self.assertEqual((g.h.phase, g.val('smu.state')['control']), ('running', 'USBTMC'))
        self.assertEqual(g.val('smu.events')['entries'][0]['text'], 'Invalid password')
        g.row('w_src', 'High Capacitance')
        g.ov('On')
        g.ov('Yes')
        g.type('secret')
        self.assertEqual((g.h.phase, g.h.error, g.val('smu.state')['control']), ('cancelled', 'LOCAL_CONTROL', 'local'))

    def test_direct_commands_and_output_key_in_lockout(self):
        g = Rig(contact=False)
        g.call(g.A, 'smu.set', setting='system.access_mode', value='lockout')
        g.call(g.A, 'smu.remote_login', password='admin')
        mapping(g)
        self.assertEqual(g.call(g.A, 'smu.system', expect='rejected', action='local')['error']['code'], 'PASSWORD_REQUIRED')
        self.assertEqual(g.call(g.A, 'smu.login', expect='rejected', password='nope')['error']['code'], 'PASSWORD_INVALID')
        self.assertEqual(g.h.phase, 'running')
        self.assertTrue(g.press('output_key'))                                  # always active
        self.assertEqual(g.val('smu.state')['control'], 'local')
        h = Rig(contact=False)                                                    # Full (default): Yes switches at once, no password
        mapping(h)
        self.assertEqual(h.call(h.A, 'smu.login', password='')['control'], 'local')


class RemoteInterface(unittest.TestCase):
    """The direct map / calibration commands are the QuadPro2 software on its remote interface: with an access mode other than Full it must log
    in before it takes the 2450 from the front panel (p.161-162): Exclusive without a password, Protected / Lockout with it; reads stay allowed;
    a change to local control logs it out.  Full (default): no login."""

    def start(self, g, expect='succeeded'):
        g.call(g.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, require_calibration=False))
        g.call(g.A, 'map.configure', **rt.MAP_EXAMPLE)
        return g.call(g.A, 'acquisition.start', expect=expect)

    def test_login_before_the_software_takes_the_instrument(self):
        g = Rig(contact=False)
        g.call(g.A, 'smu.set', setting='system.access_mode', value='exclusive')
        self.assertEqual(self.start(g, 'rejected')['error']['code'], 'LOGIN_REQUIRED')
        self.assertEqual((g.h.phase, g.val('smu.state')['control']), ('idle', 'local'))      # reads stay allowed
        self.assertTrue(g.call(g.A, 'smu.remote_login', password='')['logged_in'])          # Exclusive: no password
        self.assertIn('operation_id', self.start(g))
        self.assertEqual(g.val('smu.state')['control'], 'USBTMC')
        g.call(g.A, 'acquisition.stop')
        self.assertTrue(g.press('output_key'))                                  # the front panel takes control: logged out
        self.assertFalse(g.val('smu.state')['remote_login'])
        self.assertEqual(self.start(g, 'rejected')['error']['code'], 'LOGIN_REQUIRED')
        g.call(g.A, 'smu.set', setting='system.access_mode', value='protected')
        self.assertEqual(g.call(g.A, 'smu.remote_login', expect='rejected', password='nope')['error']['code'], 'PASSWORD_INVALID')
        g.call(g.A, 'smu.remote_login', password='admin')
        self.assertIn('operation_id', self.start(g))

    def test_full_needs_no_login(self):
        g = Rig(contact=False)
        self.assertIn('operation_id', self.start(g))


class SourceDelayAndResponse(unittest.TestCase):
    """Source delay (p.120, p.425) before the first reading after output on or a source magnitude change, and the QuadPro2 plant's voltage
    input response (voltage_response_tau_s) on the 2450 readings."""

    def test_set_and_auto_source_delay(self):
        g = Rig()
        source_current(g)
        g.call(g.A, 'smu.set', setting='source.delay', value=0.5)              # a set delay turns Auto off (p.425)
        self.assertFalse(g.settings()['src']['source.delay_auto'])
        g.press('output_key')
        t_on = g.h.time_s
        g.press('trigger_key')
        self.assertAlmostEqual(g.val('smu.reading')['time_s'], t_on + 0.5)
        g.step(1.0)
        g.press('trigger_key')
        self.assertAlmostEqual(g.val('smu.reading')['time_s'], g.h.time_s)    # no change of the source: no delay
        g.call(g.A, 'smu.set', setting='source.level', value=-1e-4)            # same magnitude: no delay
        g.press('trigger_key')
        self.assertAlmostEqual(g.val('smu.reading')['time_s'], g.h.time_s)
        g.call(g.A, 'smu.set', setting='source.delay_auto', value=True)
        g.call(g.A, 'smu.set', setting='source.level', value=2e-4)             # auto: 1 ms on the 1 mA range, current source (p.120)
        t = g.h.time_s
        g.press('trigger_key')
        self.assertAlmostEqual(g.val('smu.reading')['time_s'], t + 0.001)
        g.call(g.A, 'smu.set', setting='source.highc', value=True)
        g.call(g.A, 'smu.set', setting='source.level', value=1e-4)             # high capacitance column: 10 ms on the 100 uA range
        t = g.h.time_s
        g.press('trigger_key')
        self.assertAlmostEqual(g.val('smu.reading')['time_s'], t + 0.01)

    def test_continuous_readings_wait_the_delay(self):
        g = Rig()
        source_current(g, method='continuous')
        g.call(g.A, 'smu.set', setting='source.delay', value=0.5)
        g.press('output_key')
        t_on = g.h.time_s
        n = g.val('smu.buffers')['list'][0]['readings_total']
        g.step(0.4)
        self.assertEqual(g.val('smu.buffers')['list'][0]['readings_total'], n)
        g.step(0.5)
        self.assertGreater(g.val('smu.buffers')['list'][0]['readings_total'], n)
        self.assertGreaterEqual(g.h.smu.buffers['defbuffer1']['data'][n][0], t_on + 0.5)

    def test_voltage_input_response(self):
        g = Rig()
        source_current(g)
        g.press('output_key')
        g.press('trigger_key')
        g.step(1.0)
        g.call(g.A, 'smu.set', setting='source.level', value=2e-4)
        t = g.h.time_s
        g.press('trigger_key')                                                   # 1 ms auto delay against the plant's 10 ms response
        net = g.h._smu_network()
        target = lambda i: rt.PLANT_EXAMPLE['voltage_gain'] * i * net['r_force'] + rt.PLANT_EXAMPLE['voltage_offset_v']   # noqa: E731
        r = g.val('smu.reading')
        expect = target(2e-4) + (target(1e-4) - target(2e-4)) * math.exp(-(r['time_s'] - t) / rt.PLANT_EXAMPLE['voltage_response_tau_s'])
        self.assertAlmostEqual(r['value'], expect, delta=0.01 * (target(2e-4) - target(1e-4)))
        self.assertLess(r['value'], 0.5 * (target(1e-4) + target(2e-4)))       # still near the old level
        g.step(0.2)
        g.press('trigger_key')
        self.assertAlmostEqual(g.val('smu.reading')['value'], target(2e-4), delta=0.001 * target(2e-4))


class GraphStyleAndScale(unittest.TestCase):
    """Graph > Data Draw Style and Graph > Scale X / Y-Axis Method (p.59-60, p.148) change the graph."""

    def test_draw_style_and_axis_methods(self):
        g = Rig()
        source_current(g, method='continuous')
        g.press('output_key')
        g.step(20.0)
        g.menu('graph')
        plot = lambda: g.widgets()['graph_plot']                                 # noqa: E731
        line = g.h.panel.render(SCR)
        self.assertEqual(plot()['draw'], 'line')
        g.tap('tab_graph_data')
        g.row('w_gdata', 'Draw Style')
        g.ov('Marker')
        g.menu('graph')
        self.assertEqual(plot()['draw'], 'marker')
        self.assertFalse((g.h.panel.render(SCR) == line).all())
        smart = plot()['x_range']
        self.assertAlmostEqual(smart[1] - smart[0], 199 * g.h.smu._period(), delta=1e-6)   # SmartScale: the latest 200 readings
        for method, span in (('All', None), ('Track Latest', 10.0)):
            g.tap('tab_graph_scale')
            g.row('w_gscale', 'X-Axis Method')
            g.ov(method)
            g.menu('graph')
            x = plot()['x_range']
            if span is None:
                self.assertAlmostEqual(x[0], 0.0, delta=0.05)                   # All: from the first reading of the buffer
            else:
                self.assertAlmostEqual(x[1] - x[0], span)
        g.tap('tab_graph_scale')
        g.row('w_gscale', 'X-Axis Method')
        g.ov('Off')
        g.row('w_gscale', 'Y-Axis Method')
        g.ov('Off')
        g.menu('graph')
        held = plot()['x_range'], plot()['y_range']['left']
        g.call(g.A, 'smu.set', setting='source.level', value=5e-4)            # larger readings, later times
        g.step(5.0)
        self.assertEqual((plot()['x_range'], plot()['y_range']['left']), held)  # Off: not adjusted any more
        g.call(g.A, 'smu.set', setting='graph.x_method', value='smartscale')
        self.assertNotEqual(plot()['x_range'], held[0])
        self.assertEqual(plot()['y_range']['left'], held[1])                    # Y Off still holds while the newest readings are shown
        g.call(g.A, 'smu.set', setting='graph.y_method', value='smartscale')
        self.assertGreater(plot()['y_range']['left'][1], held[1][1])


class Contract(unittest.TestCase):
    def test_panel_capabilities_in_the_contract(self):
        c = json.loads(CONTRACT.read_text())
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for k in list(rt.PANEL_CAPS) + list(rt.SMU_CAPS):
            self.assertIn(k, c['capabilities'])
            self.assertEqual(c['capabilities'][k]['input_schema'], rt.ALL_CAPS[k]['input_schema'])
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k, v in rt.SMU_CAPS.items():
            self.assertEqual(validate_value(v['example_arguments'], v['input_schema']), [], k)


class Renders(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        old = json.loads((OUT / 'renders.json').read_text()) if (OUT / 'renders.json').exists() else {}
        old.update(RENDERS)
        (OUT / 'renders.json').write_text(json.dumps(old, indent=1) + '\n')

    def test_evidence_pngs(self):
        g = Rig()
        g.capture('home_default')
        self.assertTrue((OUT / 'home_default.png').is_file())


if __name__ == '__main__':
    unittest.main()
