"""Front-panel tests for CHEM-019 syrris_asiadiscovery (panel_ui.json + _panel_ui.py on scripts/asset_runtime/syrris_asiadiscovery.py):
python3 -m pytest tests/test_panel_ui_syrris_asiadiscovery.py -q

Gestures through the session API (panel.turn / panel.press / panel.switch; panel.frame for the evaluator).  Themes: the eight key procedures of the
menu survey with knobs, the Cancel button and the power switch only (the USB stick is inserted with pump.usb, a hand at the back of the pump),
checked on the runtime state, the pump display, the LED displays and the indicator LEDs; panel = direct commands; refusals during a PC method
run, power off, syringe change without INITIALISE, OVER P; time-outs of the controllers; render determinism; contract entries."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
from asset_runtime import syrris_asiadiscovery as rt  # noqa: E402
from test_asset_syrris_asiadiscovery import prepared  # noqa: E402

OUT = ROOT / 'outputs/panel_ui/syrris_asiadiscovery'
CONTRACT = ROOT / 'assets/expansion_batch03/t5_automation/syrris_asiadiscovery/asset.contract.json'
ULMIN = rt.ULMIN
SURVEY_TASKS = ['set a pump flow rate', 'stop the pumps', 'change syringe size', 'zero the pump pressure sensors', 'set the back pressure',
                'set the chip temperature', 'inject reagent loops', 'export pump logs']
DONE_TASKS = set()
RENDERS = {}
LEDS = ('p_set_led', 'p_actual_led', 'c_set_led', 'c_actual_led', 'heat_led', 'cool_led', 'inj_left_led', 'inj_right_led')


class Rig:
    def __init__(self, **plant):
        self.h = prepared(**plant)
        self.h.panel_capture_dir = str(OUT / 'captures')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.frame()
        self.h.step(5.5)                                                         # the pump's start screen (pump p.20)
        assert self.screen() == 'home', self.screen()

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(instance_id=self.h.instance_id, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def val(self, sid):
        return self.E.read(instance_id=self.h.instance_id, state_ids=[sid])['observations'][sid]['value']

    def turn(self, inp, n):
        return self.call(self.A, 'panel.turn', input_id=inp, detents=n)['accepted']

    def push(self, inp, duration_s=0.1):
        return self.call(self.A, 'panel.press', input_id=inp, duration_s=duration_s)['accepted']

    def cancel(self):
        return self.push('pump_cancel')

    def switch(self, pos, held=()):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos, held=list(held))['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lcd(self):
        return self.frame()['displays']['pump_lcd']['lines']

    def seg(self, d):
        return self.frame()['displays'][d]['fields']['main']['text']

    def leds(self):
        d = self.frame()['displays']
        return sorted(k for k in LEDS if d[k]['on'])

    def home_to(self, target):
        """Twist the pump knob on the home screen until the arrows point at target (p1 f1 usb m p2 f2 log)."""
        self.turn('pump_knob', -10)
        order = [p for p in ('p1', 'f1', 'usb', 'm', 'p2', 'f2', 'log') if p != 'usb' or self.h.usb['inserted']]
        if order.index(target):
            self.turn('pump_knob', order.index(target))
        assert self.frame()['ui']['vars']['sel'] == target

    def set_flow(self, channel, clicks):
        self.home_to('f1' if channel == 1 else 'f2')
        assert self.push('pump_knob')
        while clicks:                                                            # a long turn in strokes of at most 1000 detents
            n = max(-1000, min(1000, clicks))
            self.turn('pump_knob', n)
            clicks -= n
        return self.push('pump_knob')

    def options(self, channel, item):
        """Open P1 / P2 options and put the arrow on item (index of the list, 0 = INITIALISE)."""
        self.home_to('p1' if channel == 1 else 'p2')
        assert self.push('pump_knob') and self.screen() == 'options'
        self.turn('pump_knob', item)

    def capture(self, name):
        from PIL import Image
        p = self.h.panel
        row = [p.render_image(d) for d in ('p_set_led', 'p_actual_led', 'pressure_led', 'c_set_led', 'c_actual_led', 'climate_led', 'heat_led', 'cool_led',
                                           'inj_left_led', 'inj_left', 'inj_right', 'inj_right_led')]
        lcd = p.render_image('pump_lcd')
        w = max(lcd.size[0], sum(i.size[0] + 8 for i in row))
        canvas = Image.new('RGB', (w + 16, lcd.size[1] + max(i.size[1] for i in row) + 24), (235, 236, 238))
        canvas.paste(lcd, (8, 8))
        x = 8
        for i in row:
            canvas.paste(i, (x, lcd.size[1] + 16 + (max(j.size[1] for j in row) - i.size[1]) // 2))
            x += i.size[0] + 8
        OUT.mkdir(parents=True, exist_ok=True)
        canvas.save(OUT / f'{name}.png')
        RENDERS[name] = dict(file=f'{name}.png', screen=self.screen(), time_s=self.h.time_s)


class KeyProcedures(unittest.TestCase):
    def test_set_flow_rate_and_stop(self):
        g = Rig()
        self.assertEqual(g.lcd(), ['→P1←0.0 µl/min    M ', ' P2 0.0 µl/min  LOG '])
        g.home_to('f1')
        self.assertTrue(g.lcd()[0].startswith(' P1→0.0←µl/min'))
        g.push('pump_knob')                                                      # the last set value (none yet: the syringe minimum)
        self.assertTrue(g.lcd()[0].startswith(' P1*50.0*µl/min'))                # arrows turn to stars (pump p.22)
        g.turn('pump_knob', 500)                                                 # 50.0 -> 99.9 by 0.1 (499 clicks), then by 1 ul/min
        self.assertTrue(g.lcd()[0].startswith(' P1*100*µl/min'))
        g.push('pump_knob')
        self.assertAlmostEqual(g.val('pump.flow_set_m3_s')[0], 100 * ULMIN)
        self.assertEqual(g.val('cycle.phase'), 'running')
        self.assertTrue(g.val('cycle.manual'))
        g.h.step(3.0)
        self.assertAlmostEqual(g.val('flow.actual_m3_s')[0], 100 * ULMIN)
        self.assertGreater(g.val('collection.waste_m3'), 0)                      # a manual run: the outlet goes to waste
        self.assertEqual(g.lcd()[0], ' P1→100←µl/min    M ')
        g.set_flow(2, 1500)                                                      # P2: 50 ul/min + 1500 clicks -> ml/min shown with two decimals
        self.assertIn('ml/min', g.lcd()[1])
        g.capture('home')
        DONE_TASKS.add(SURVEY_TASKS[0])
        g.options(2, 1)                                                          # 2) STOP 2
        self.assertEqual(g.lcd()[1], '→2) STOP 2')
        g.push('pump_knob')
        self.assertEqual(g.val('pump.flow_set_m3_s')[1], 0.0)
        self.assertEqual(g.val('cycle.phase'), 'running')                         # P1 still pumps
        g.cancel()                                                               # back to the home screen
        self.assertEqual(g.screen(), 'home')
        g.cancel()                                                               # Cancel on the home screen stops both channels (pump p.23)
        self.assertEqual(g.val('pump.flow_set_m3_s'), [0.0, 0.0])
        self.assertEqual(g.val('cycle.phase'), 'idle')
        self.assertEqual(g.lcd()[0][:14], ' P1 0.0 µl/min')
        DONE_TASKS.add(SURVEY_TASKS[1])

    def test_change_syringe_size_and_initialise(self):
        g = Rig()
        g.options(1, 5)
        self.assertEqual(g.lcd()[1], '→6) S. VOL 2.5,5.0ml')
        g.capture('menu')
        g.push('pump_knob')
        g.turn('pump_knob', -1)                                                  # red -> blue
        g.push('pump_knob')
        self.assertEqual(g.screen(), 'init_prompt')
        self.assertEqual(g.lcd()[0], 'INITIALISE PUMP1')
        self.assertEqual(g.val('pump.channels')[0]['syringe'], 'blue')
        g.turn('pump_knob', 1)                                                   # N: the channel stays inoperable (pump p.26 notice)
        g.push('pump_knob')
        g.cancel()
        self.assertFalse(g.set_flow(1, 10))
        self.assertEqual(g.val('pump.flow_set_m3_s')[0], 0.0)
        g.options(1, 0)
        self.assertEqual(g.lcd()[0], '→1) INITIALISE 1')
        g.push('pump_knob')
        self.assertEqual(g.lcd(), ['PUMP 1 INITIALISING', '    PLEASE WAIT'])
        g.h.step(10.5)
        self.assertEqual(g.screen(), 'home')                                     # back home when the routine is finished (pump p.24)
        self.assertTrue(g.val('pump.channels')[0]['initialised'])
        g.set_flow(1, 0)                                                         # blue: 10 ul/min minimum; syringes at the top: filled first (pump p.23)
        self.assertAlmostEqual(g.val('pump.flow_set_m3_s')[0], 10 * ULMIN)
        self.assertEqual(g.val('pump.channels')[0]['drive'], 'filling')
        self.assertEqual(g.val('flow.actual_m3_s')[0], 0.0)
        g.h.step(61.0)                                                           # 1000 ul at FILL@ 1.00 ml/min
        self.assertEqual(g.val('pump.channels')[0]['drive'], 'pumping')
        self.assertGreater(g.val('flow.actual_m3_s')[0], 0.0)
        self.assertEqual([r['text'] for r in g.val('pump.log')][-4:], ['PUMP1 S. VOL', 'PUMP1 INIT', 'PUMP1 FILLING', 'PUMP1 PUMPING'])
        DONE_TASKS.add(SURVEY_TASKS[2])

    def test_zero_pressure(self):
        g = Rig()
        g.set_flow(1, 0)
        g.options(1, 8)
        self.assertEqual(g.lcd()[1], '→9) ZERO PRESSURE 1')
        self.assertFalse(g.push('pump_knob'))                                    # still pumping: refused, no message (pump p.28 step 1)
        self.assertIsNone(g.val('pump.channels')[0]['zeroed_s'])
        g.cancel()
        g.cancel()                                                               # stop
        g.options(1, 8)
        self.assertTrue(g.push('pump_knob'))
        self.assertEqual(g.lcd()[1], 'PRESSURE ZEROED OK')
        self.assertEqual(g.val('pump.channels')[0]['zeroed_s'], g.h.time_s)
        g.capture('error')
        DONE_TASKS.add(SURVEY_TASKS[3])

    def test_back_pressure(self):
        g = Rig(gas_supply_pa=6e5)
        self.assertEqual(g.seg('pressure_led'), '2.0')
        self.assertIn('p_actual_led', g.leds())
        g.turn('pressure_knob', 1)                                               # turn: the set pressure for 3 s, no change (pc p.19)
        self.assertEqual(g.seg('pressure_led'), '2')
        self.assertIn('p_set_led', g.leds())
        g.h.step(3.2)
        self.assertEqual(g.seg('pressure_led'), '2.0')
        g.push('pressure_knob')
        g.turn('pressure_knob', 10)
        self.assertEqual(g.seg('pressure_led'), '6')                             # stops at the gas supply (pc p.16 note)
        g.push('pressure_knob')
        self.assertEqual(g.val('pressure.backpressure_pa'), 6e5)
        self.assertEqual(g.seg('pressure_led'), '6.0')
        g.push('pressure_knob')
        g.turn('pressure_knob', -2)
        g.h.step(5.5)                                                            # not validated: dropped
        self.assertEqual(g.val('pressure.backpressure_pa'), 6e5)
        g.push('pressure_knob')
        g.turn('pressure_knob', -20)
        self.assertEqual(g.seg('pressure_led'), 'OFF')
        g.push('pressure_knob')                                                  # OFF: vent to room pressure (pc p.20)
        self.assertFalse(g.val('pressure.enabled'))
        self.assertEqual(g.seg('pressure_led'), '0.0')
        g.push('pressure_knob', duration_s=3.5)                                  # kept pushed: firmware (pc p.27)
        self.assertEqual(g.seg('pressure_led'), '01.01')
        g.switch('off')
        g.switch('on', held=['pressure_push'])                                   # knob held at switch-on: tare (pc p.16)
        self.assertEqual(g.seg('pressure_led'), 'tArE')
        self.assertEqual(g.val('pressure.tare_time_s'), g.h.time_s)
        DONE_TASKS.add(SURVEY_TASKS[4])

    def test_chip_temperature(self):
        g = Rig()
        self.assertEqual(g.seg('climate_led'), '25.0')
        g.push('climate_knob')
        self.assertEqual(g.seg('climate_led'), '25')
        g.turn('climate_knob', 15)
        g.push('climate_knob')
        self.assertAlmostEqual(g.val('temperature.target_k'), 313.15)
        self.assertIn('heat_led', g.leds())
        g.h.step(60.0)
        self.assertEqual(g.seg('climate_led'), '40.0')
        self.assertNotIn('heat_led', g.leds())
        g.push('climate_knob')
        g.turn('climate_knob', -30)
        g.h.step(5.5)                                                            # not validated within 5 s: dropped (ccc p.21 note)
        self.assertEqual(g.screen(), 'home')
        self.assertAlmostEqual(g.val('temperature.target_k'), 313.15)
        g.push('climate_knob')
        g.turn('climate_knob', -30)                                              # 10 C: no low-temperature capability declared by the host
        self.assertFalse(g.push('climate_knob'))
        self.assertAlmostEqual(g.val('temperature.target_k'), 313.15)
        g.push('climate_knob')
        g.turn('climate_knob', -200)
        self.assertEqual(g.seg('climate_led'), 'OFF')
        g.push('climate_knob')                                                   # OFF: passive back to room temperature (ccc p.21)
        self.assertFalse(g.val('temperature.enabled'))
        g.h.step(100.0)
        self.assertAlmostEqual(g.val('temperature.indicated_k'), 298.15, places=1)
        self.assertNotIn('cool_led', g.leds())
        DONE_TASKS.add(SURVEY_TASKS[5])

    def test_inject_reagent_loops(self):
        g = Rig()
        self.assertEqual((g.seg('inj_left'), g.seg('inj_right')), ('F', 'F'))   # both valves in F (ri p.17 step 3)
        self.assertEqual([k for k in g.leds() if k.startswith('inj')], ['inj_left_led'])
        g.set_flow(1, 900)                                                       # 50.0 + 499 x 0.1 + 401 x 1 -> 500 ul/min
        g.set_flow(2, 900)
        g.turn('injector_knob', 2)                                               # left, right, both: both 'active' LEDs
        self.assertEqual([k for k in g.leds() if k.startswith('inj')], ['inj_left_led', 'inj_right_led'])
        g.push('injector_knob')
        self.assertEqual(g.val('injector.positions'), ['inject', 'inject'])
        self.assertEqual((g.seg('inj_left'), g.seg('inj_right')), ('I', 'I'))
        loops = g.val('truth.loop_remaining_m3')
        g.h.step(2.0)
        self.assertLess(g.val('truth.loop_remaining_m3')[0], loops[0])
        self.assertTrue(all(g.val('injection.valves')))
        g.turn('injector_knob', 1)                                               # left only
        g.push('injector_knob')
        self.assertEqual(g.val('injector.positions'), ['fill', 'inject'])
        g.push('injector_knob', duration_s=3.2)                                  # kept pushed: firmware X.X (ri p.22)
        self.assertEqual((g.seg('inj_left'), g.seg('inj_right')), ('1.', '3'))
        DONE_TASKS.add(SURVEY_TASKS[6])

    def test_export_pump_logs(self):
        g = Rig()
        g.set_flow(1, 0)
        g.call(g.A, 'pump.usb', action='insert')                                # the stick at the back of the pump (pump p.31)
        self.assertEqual(g.lcd()[0][-4:], 'ψ M ')
        g.home_to('usb')
        self.assertEqual(g.lcd()[0][-6:], '→ψ← M ')
        g.push('pump_knob')
        self.assertEqual(g.lcd(), ['→1) COPY DATA LOGS', ' 2) SAFELY REMOVE'])
        self.assertFalse(g.push('pump_knob'))                                    # the pump is pumping: no copy (pump p.31 step 1)
        self.assertEqual(g.screen(), 'usb')
        g.call(g.A, 'pump.action', channel='P1', action='stop')
        self.assertTrue(g.push('pump_knob'))
        self.assertEqual(g.lcd()[0], '    LOGS COPIED')
        files = g.val('pump.usb')['files']
        self.assertIn('logs/asia_system.txt', files)
        self.assertTrue(any(f.startswith('logs/AsiaPumpDataLog_') for f in files))
        g.cancel()
        g.turn('pump_knob', 1)
        g.push('pump_knob')                                                      # SAFELY REMOVE
        self.assertFalse(g.val('pump.usb')['inserted'])
        self.assertEqual(g.screen(), 'home')
        self.assertEqual(g.lcd()[0][-3:], '→M←')
        g.home_to('log')
        g.push('pump_knob')
        self.assertEqual(g.lcd()[0], '→1) PUMP1 PUMPING')
        g.turn('pump_knob', 2)
        self.assertEqual(g.lcd()[0], '→3) LOGS COPIED')
        DONE_TASKS.add(SURVEY_TASKS[7])


class EquivalenceAndRefusals(unittest.TestCase):
    def test_panel_equals_direct(self):
        g, d = Rig(), Rig()
        g.set_flow(1, 900)
        g.push('pressure_knob')
        g.turn('pressure_knob', 3)
        g.push('pressure_knob')
        g.push('climate_knob')
        g.turn('climate_knob', 5)
        g.push('climate_knob')
        g.push('injector_knob')
        d.call(d.A, 'pump.set_flow', channel='P1', flow_m3_s=500 * ULMIN)
        d.call(d.A, 'pressure.set', backpressure_pa=5e5)
        d.call(d.A, 'temperature.set', target_k=303.15)
        d.call(d.A, 'injector.set_valve', valve='left', position='inject')
        g.h.step(20.)
        d.h.step(20.)
        self.assertEqual(g.h.read('evaluator'), d.h.read('evaluator'))

    def test_method_run_power_and_over_pressure(self):
        g = Rig()
        g.call(g.A, 'acquisition.start')                                         # a PC method run: the fronts follow the method
        self.assertFalse(g.set_flow(1, 10))
        self.assertEqual(g.screen(), 'home')
        g.push('pressure_knob')
        g.turn('pressure_knob', 2)
        self.assertFalse(g.push('pressure_knob'))
        self.assertEqual(g.val('pressure.backpressure_pa'), 2e5)
        self.assertFalse(g.push('injector_knob'))
        g.cancel()                                                               # the pump stops: the method run ends
        self.assertEqual(g.val('cycle.phase'), 'cancelled')
        g.options(1, 6)                                                          # 7) MAX PRESSURE 20 -> 5 bar, 8) OVER P BOTH -> SINGLE
        g.push('pump_knob')
        g.turn('pump_knob', -15)
        g.push('pump_knob')
        g.turn('pump_knob', 1)
        g.push('pump_knob')
        g.turn('pump_knob', -1)
        g.push('pump_knob')
        self.assertEqual(g.lcd(), [' 7) MAX PRESSURE 5', '→8) OVER P SINGLE'])
        g.cancel()
        g.set_flow(1, 0)
        g.set_flow(2, 0)
        g.call(g.A, 'pressure.set', backpressure_pa=6e5)                         # above P1's MAX PRESSURE: P1 stops alone (pump p.27)
        g.h.step(0.1)
        ch = g.val('pump.channels')
        self.assertEqual((ch[0]['drive'], ch[0]['alarm'], ch[1]['drive']), ('idle', 'OVER P', 'pumping'))
        self.assertEqual(g.val('pump.log')[-1]['text'], 'PUMP1 OVER P')
        g.switch('off')
        self.assertEqual(g.screen(), 'off')
        self.assertFalse(g.turn('pump_knob', 1))
        self.assertFalse(g.push('climate_knob'))
        self.assertEqual(g.val('cycle.phase'), 'cancelled')
        self.assertEqual(g.val('pump.flow_set_m3_s'), [0.0, 0.0])

    def test_render_and_contract(self):
        g = Rig()
        a = g.h.panel.render('pump_lcd')
        self.assertTrue((a == g.h.panel.render('pump_lcd')).all())
        self.assertTrue(Path(g.call(g.A, 'panel.capture', display_id='pump_lcd')['image']['path']).is_file())
        c = json.loads(CONTRACT.read_text())
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for k in list(rt.PANEL_CAPS) + list(rt.FRONT_CAPS):
            self.assertIn(k, c['capabilities'])


class PressureSensors(unittest.TestCase):
    """ZERO PRESSURE (pump p.27) and the tare at switch-on (pc p.16) store the sensor's raw reading as its zero; the pump readings, OVER P and the
    controller act on the (offset, zeroed) readings.  The sensor offset is evaluator-only (default 0)."""

    def test_zero_pressure_cancels_the_offset_and_a_zero_under_pressure_is_wrong(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', sensor='P1', offset_pa=3e4)
        self.assertEqual(g.val('pressure.indicated_pa'), [3e4, 0.0])           # idle at room pressure: P1 shows its offset (0.3 bar)
        g.options(1, 8)
        self.assertTrue(g.push('pump_knob'))                                     # 9) ZERO PRESSURE 1
        self.assertEqual((g.val('pressure.indicated_pa'), g.val('truth.pressure_sensor_zero_pa')), ([0.0, 0.0], [3e4, 0.0, 0.0]))
        g.cancel()
        g.set_flow(1, 900)
        g.h.step(2.0)
        true = g.h.pressures()
        self.assertEqual(g.val('pressure.indicated_pa')[0], round(true[0] / 1e4) * 1e4)
        g.options(2, 8)
        g.push('pump_knob')                                                      # P2 zeroed while P1 pumps: the sensor sees the line pressure
        self.assertAlmostEqual(g.val('truth.pressure_sensor_zero_pa')[1], g.h.pressures()[1])
        g.cancel()
        g.set_flow(2, 900)
        g.h.step(2.0)
        r = g.h.readings()
        self.assertAlmostEqual(r[1], g.h.pressures()[1] - g.val('truth.pressure_sensor_zero_pa')[1])
        self.assertLess(g.val('pressure.indicated_pa')[1], round(g.h.pressures()[1] / 1e4) * 1e4)   # P2 reads low by the wrong zero

    def test_over_pressure_acts_on_the_reading(self):
        g, ref = Rig(), Rig()
        for x in (g, ref):
            x.call(x.A, 'pump.configure', channel='P1', max_pressure_pa=5e5)
            x.call(x.A, 'pressure.set', backpressure_pa=4e5)
        g.call(g.E, 'sensor.configure_error', sensor='P1', offset_pa=1.5e5)   # reads 5.5 bar where the line is at 4 bar
        for x in (g, ref):
            x.set_flow(1, 900)
            x.h.step(0.1)
        self.assertEqual((g.val('pump.channels')[0]['alarm'], ref.val('pump.channels')[0]['alarm']), ('OVER P', ''))
        self.assertEqual(ref.val('pump.channels')[0]['drive'], 'pumping')

    def test_controller_holds_its_reading_and_tare_at_switch_on(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', sensor='controller', offset_pa=5e4)
        self.assertEqual(g.seg('pressure_led'), '2.0')                          # the controller holds its own reading at 2 bar ...
        self.assertAlmostEqual(g.val('truth.backpressure_pa'), 1.5e5)           # ... so the line is at 1.5 bar
        self.assertAlmostEqual(g.val('pressure.vapor_margin_pa'), 1.5e5 + 101325 - g.val('pressure.vapor_pa'))
        g.call(g.A, 'pressure.enable', enabled=False)                            # OFF: vented, the display shows the offset
        self.assertEqual(g.seg('pressure_led'), '0.5')
        g.switch('off')
        g.switch('on', held=['pressure_push'])                                   # tare (pc p.16): the raw reading at room pressure becomes 0
        g.h.step(3.5)
        self.assertEqual((g.seg('pressure_led'), g.val('truth.pressure_sensor_zero_pa')[2]), ('0.0', 5e4))
        g.call(g.A, 'pressure.set', backpressure_pa=2e5)
        self.assertEqual((g.seg('pressure_led'), g.val('truth.backpressure_pa')), ('2.0', 2e5))
        d = Rig()                                                                # the direct tare gives the same zero
        d.call(d.E, 'sensor.configure_error', sensor='controller', offset_pa=5e4)
        d.call(d.A, 'pressure.tare')
        self.assertEqual(d.val('truth.pressure_sensor_zero_pa'), [0.0, 0.0, 5e4])


class CallerRules(unittest.TestCase):
    """A gesture made through a session acts as that session's caller: a manual run started by a flow on the pump belongs to the caller (panel or
    pump.set_flow), and another agent may not stop, switch off or change a run it does not own (module fronts and direct commands alike)."""

    def test_manual_run_belongs_to_the_caller_and_is_guarded(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.set_flow(1, 900)
        op = g.h.operation_id
        self.assertEqual((g.val('cycle.manual'), g.h._owners.get(op)), (True, 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        g.h.step(1.0)
        g.turn('pump_knob', -10)
        before = g.h.read('evaluator')
        self.assertFalse(g.call(B, 'panel.press', input_id='pump_cancel')['accepted'])   # B's Cancel on the home screen
        for cap, args in (('pump.action', dict(channel='P1', action='stop')), ('pump.set_flow', dict(channel='P2', flow_m3_s=100 * ULMIN)),
                          ('acquisition.stop', {}), ('pressure.set', dict(backpressure_pa=3e5))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(g.h.read('evaluator'), before)
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'cancelled')

    def test_rear_switch_is_physical(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.set_flow(1, 900)
        op = g.h.operation_id
        g.h.step(1.0)
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off', held=[])['accepted'])   # never refused
        self.assertFalse(g.val('power.enabled'))
        self.assertNotEqual(g.A.poll(op)['status'], 'running')                  # the run ends as on a power loss

    def test_direct_flow_run_and_method_run(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.call(g.A, 'pump.set_flow', channel='P1', flow_m3_s=100 * ULMIN)
        op = g.h.operation_id
        self.assertEqual(g.A.poll(op)['status'], 'running')                     # the direct starter can poll its manual run
        g.call(g.A, 'pump.set_flow', channel='P1', flow_m3_s=0.0)
        op = g.call(B, 'acquisition.start')['operation_id']                      # B's method run: A's pump STOP is refused
        self.assertEqual(g.call(g.A, 'pump.action', expect='rejected', channel='P1', action='stop')['error']['code'], 'ACCESS_DENIED')
        self.assertFalse(g.cancel())
        self.assertEqual(B.poll(op)['status'], 'running')

    def test_refusal_is_shown_on_the_module_display(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.set_flow(1, 900)
        g.h.step(1.0)
        self.assertFalse(g.call(B, 'panel.press', input_id='pump_cancel')['accepted'])      # pump: bottom LCD line (assumed text)
        self.assertEqual(g.lcd()[1], 'ACCESS DENIED')
        g.h.step(2.6)
        self.assertNotEqual(g.lcd()[1], 'ACCESS DENIED')
        before = g.val('pressure.backpressure_pa')
        g.call(B, 'panel.press', input_id='pressure_push')                              # pressure controller: dEnY on its display
        g.call(B, 'panel.turn', input_id='pressure_knob', detents=3)
        self.assertFalse(g.call(B, 'panel.press', input_id='pressure_push')['accepted'])
        self.assertEqual((g.seg('pressure_led'), g.val('pressure.backpressure_pa')), ('dEnY', before))
        self.assertNotEqual(g.lcd()[1], 'ACCESS DENIED')
        positions = g.val('injector.positions')
        self.assertFalse(g.call(B, 'panel.press', input_id='injector_push')['accepted'])  # injector: '-' on the valve display
        self.assertEqual((g.seg('inj_left'), g.val('injector.positions')), ('-', positions))
        g.h.step(2.6)
        self.assertNotEqual(g.seg('pressure_led'), 'dEnY')


class Renders(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        old = json.loads((OUT / 'renders.json').read_text()) if (OUT / 'renders.json').exists() else {}
        old.update(RENDERS)
        (OUT / 'renders.json').write_text(json.dumps(old, indent=1, ensure_ascii=False) + '\n')

    def test_screensaver_and_clock(self):
        g = Rig()
        g.home_to('m')
        g.push('pump_knob')
        g.turn('pump_knob', 3)
        g.push('pump_knob')                                                      # 4) SCREENSAVER
        self.assertEqual(g.screen(), 'screensaver')
        g.push('pump_knob')                                                      # locked on the shown display
        self.assertTrue(g.lcd()[0].startswith('🔒'))
        g.cancel()
        g.turn('pump_knob', 1)                                                   # a twist leaves
        self.assertEqual(g.screen(), 'home')
        g.home_to('m')
        g.push('pump_knob')
        g.turn('pump_knob', 2)
        g.push('pump_knob')                                                      # 3) SET DATE/TIME
        self.assertEqual(g.lcd(), ['→00←:00     1/1/2026', '    SET AND RESTART'])
        g.push('pump_knob')
        g.turn('pump_knob', 9)
        g.push('pump_knob')
        g.turn('pump_knob', 5)
        g.push('pump_knob')
        self.assertEqual(g.val('pump.clock')[11:16], '09:00')
        self.assertEqual(g.screen(), 'start')


if __name__ == '__main__':
    unittest.main()
