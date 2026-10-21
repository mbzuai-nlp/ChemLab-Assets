"""Front-panel tests for CHEM-044 sonics_vcx750 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/sonics_vcx750.py):
python3 -m pytest tests/test_panel_ui_sonics_vcx750.py -q

Every gesture goes through the session API (panel.press; panel.frame for the evaluator).  Themes: the eight key procedures of the menu survey
done only with key presses and checked on the runtime state, the LCD lines and the pixels; panel = direct commands; refusals and stand-by-only
keys; power-up rating screen time-out; review cycle, pause and indicators; job memories across a power cycle; render determinism."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import sonics_vcx750 as rt  # noqa: E402

OUT = ROOT / 'outputs/panel_ui/sonics_vcx750'
CONTRACT = ROOT / 'assets/expansion_batch03/t1_thermal/sonics_vcx750/asset.contract.json'
SURVEY_TASKS = ['set amplitude to 40 % and run continuously', 'set a 30 min processing time', 'set pulse 10 s on / 10 s off', 'set a 35 C temperature limit',
                'set an energy endpoint', 'save the current parameters as job 6', 'recall job 6', 'read power and energy during a run']
DONE_TASKS = set()
RENDERS = {}


class Rig:
    def __init__(self, power=True, temp_probe=True):
        self.h = rt.SonicsVcx750Runtime()
        self.h.panel_capture_dir = str(OUT / 'captures')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        self.call(self.E, 'sample.configure', **rt.example_sample())
        self.call(self.E, 'mount.configure', converter_clamped_upper=True, probe_tight=True, vessel_fixed=True, temperature_probe_present=temp_probe)
        self.call(self.A, 'mount.set_immersion', fraction=.5)
        self.h.step(5.0)
        if power:
            self.keys('on')
            self.h.step(2.5)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(instance_id=self.h.instance_id, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def val(self, sid):
        return self.E.read(instance_id=self.h.instance_id, state_ids=[sid])['observations'][sid]['value']

    def press(self, key):
        return self.call(self.A, 'panel.press', input_id=f'key_{key}')['accepted']

    def keys(self, *seq):
        return [self.press(k) for k in seq]

    def type(self, digits):
        return self.keys(*digits)

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lines(self):
        return self.frame()['displays']['lcd']['lines']

    def screen(self):
        return self.frame()['screen']

    def led(self, name):
        return self.frame()['displays'][f'{name}_led']['on']

    def capture(self, name):
        info = self.h.panel.save_png('lcd', OUT / f'{name}.png')
        RENDERS[name] = dict(file=f'{name}.png', sha256=info['sha256'], screen=self.screen(), time_s=self.h.time_s)
        return info


class KeyProcedures(unittest.TestCase):
    def test_amplitude_40_and_continuous_run(self):
        """p.16: I, AMPL 4 0 ENTER, START; STOP."""
        g = Rig(power=False)
        self.assertEqual(g.lines(), ['', ''])
        g.press('on')
        self.assertEqual(g.lines(), ['VCX 750', '750 Watts'])
        g.h.step(2.5)
        self.assertEqual(g.lines(), ['Time -:--:--', 'Pulse -- -- Ampl -- %'])
        g.keys('ampl', '4', '0')
        self.assertEqual(g.lines()[1], 'Ampl 40 %')
        g.press('enter')
        self.assertEqual(g.lines(), ['Time -:--:--', 'Pulse -- -- Ampl 40 %'])
        self.assertEqual(g.val('sonication.amplitude_fraction'), .4)
        g.capture('home')
        g.press('start_stop')
        self.assertTrue(g.led('start'))
        g.h.step(3.0)
        self.assertEqual(g.val('cycle.phase'), 'running')
        g.press('start_stop')
        self.assertEqual((g.val('cycle.phase'), g.led('start')), ('cancelled', False))
        DONE_TASKS.add(SURVEY_TASKS[0])

    def test_timer_pulse_temperature_energy(self):
        g = Rig()
        g.keys('ampl', 'up', 'up', 'enter')                                       # ▲ / ▼ after AMPL (p.16)
        self.assertEqual(g.val('method.parameters')['amplitude_percent'], 22)
        g.press('timer')
        self.assertEqual(g.lines(), ['Time Setting', 'Hrs: - Min: -- Sec: --'])
        g.type('03000')
        self.assertEqual(g.lines()[1], 'Hrs: 0 Min: 30 Sec: 00')
        g.press('enter')
        self.assertEqual((g.lines()[0], g.val('method.parameters')['duration_s']), ('Time 0:30:00', 1800))
        DONE_TASKS.add(SURVEY_TASKS[1])
        g.press('pulser')
        self.assertEqual(g.lines(), ['Pulse on __ sec', 'Pulse off __ sec'])
        g.type('10')
        g.press('enter')
        self.assertEqual(g.lines()[0], 'Pulse on 10 sec')
        g.type('10')
        g.press('enter')
        self.assertEqual(g.lines(), ['Pulse on 10 sec', 'Pulse off 10 sec'])
        g.press('enter')
        self.assertEqual(g.lines()[1], 'Pulse 10 10 Ampl 22 %')
        DONE_TASKS.add(SURVEY_TASKS[2])
        g.press('temp')
        self.assertEqual(g.lines(), ['Probe Temperature 25°C', 'Temperature Setpoint __°C'])
        g.type('35')
        g.press('enter')
        self.assertEqual(g.lines()[0], 'Time 0:30:00 Temp 35°C')
        self.assertAlmostEqual(g.val('thermal.limit_k'), 308.15)
        DONE_TASKS.add(SURVEY_TASKS[3])
        g.press('energy')
        self.assertEqual(g.lines(), ['Energy Setting', '_,___,___ Joules'])
        g.type('6000')
        g.press('clear')                                                          # CLEAR clears the preceding entry
        g.press('enter')
        self.assertEqual(g.val('method.parameters')['energy_J'], 600)
        self.assertEqual(g.h.method['energy_endpoint_J'], 600.0)
        DONE_TASKS.add(SURVEY_TASKS[4])

    def test_save_and_recall_job_6(self):
        g = Rig()
        g.keys('ampl', '4', '0', 'enter', 'timer', '0', '3', '0', '0', '0', 'enter', 'pulser', '1', '0', 'enter', '1', '0', 'enter', 'enter')
        g.press('save')
        self.assertEqual(g.lines(), ['*RECALL JOB', ' SAVE JOB'])
        g.press('save')
        self.assertEqual(g.lines(), [' RECALL JOB', '*SAVE JOB'])
        g.press('enter')
        self.assertEqual(g.lines(), ['S# T__:__:__ T__ __°C', 'P____ E_,___,___ A___%'])
        g.press('6')
        self.assertEqual(g.lines()[0][:2], 'S6')
        g.press('enter')
        self.assertEqual(g.lines(), ['S6 Time 0:30:00', 'Pulse 10 10 Ampl 40 %'])
        self.assertEqual(g.val('program.slots')['6']['duration_s'], 1800)
        DONE_TASKS.add(SURVEY_TASKS[5])
        g.keys('ampl', '8', '0', 'enter')
        self.assertEqual(g.lines()[0], 'Time 0:30:00')                          # a new entry clears the S6 prefix
        g.keys('off', 'on')
        g.h.step(2.5)
        g.keys('save', 'enter', '6')
        self.assertEqual(g.lines(), ['R6 T0:30:00 T__°C', 'P1010 E0 A40%'])
        g.press('enter')
        self.assertEqual(g.lines(), ['R6 Time 0:30:00', 'Pulse 10 10 Ampl 40 %'])
        self.assertEqual(g.val('sonication.amplitude_fraction'), .4)
        DONE_TASKS.add(SURVEY_TASKS[6])
        g.keys('save', 'enter', '9', 'enter')                                     # empty memory: refused, back without a prefix
        self.assertEqual((g.screen(), g.lines()[0]), ('main', 'R6 Time 0:30:00'))

    def test_review_during_a_run_and_pause(self):
        g = Rig()
        g.keys('ampl', '5', '0', 'enter', 'pulser', '2', 'enter', '4', 'enter', 'enter', 'start_stop')
        g.h.step(2.5)
        self.assertTrue(g.led('pulser'))                                          # OFF portion
        self.assertEqual(g.lines()[0], 'Sonics in OFF Cycle')                     # OFF part longer than 3 s (p.17)
        g.press('enter')
        self.assertEqual(g.lines()[0], 'Amplitude 50%')
        g.press('enter')
        self.assertEqual(g.lines()[0], 'AMPLITUDE CONTROL')
        g.press('up')
        self.assertEqual(g.lines()[1], 'Ampl 51 %')
        g.keys('enter', 'enter', 'enter')
        m = g.call(g.A, 'sonication.measure')
        self.assertEqual(g.lines(), [f'{round(m["power_W"])} watts', f'{round(m["energy_J"])} Joules'])
        g.capture('review')
        DONE_TASKS.add(SURVEY_TASKS[7])
        g.press('enter')
        self.assertEqual(g.lines()[0], 'Elapsed time')
        g.press('enter')
        self.assertEqual(g.screen(), 'main')
        g.press('pause')
        self.assertTrue(g.led('pause'))
        g.press('pause')
        self.assertFalse(g.led('pause'))


class EquivalenceRefusals(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        g, d = Rig(), Rig()
        g.keys('ampl', '6', '0', 'enter', 'timer', '1', '0', '0', 'enter', 'energy', '9', '0', '0', 'enter', 'start_stop')
        for k, v in (('amplitude_percent', 60), ('duration_s', 60), ('energy_J', 900)):
            d.call(d.A, 'method.set_parameter', parameter=k, value=v)
        d.call(d.A, 'sonication.toggle')
        g.h.step(20.0)
        d.h.step(20.0)
        self.assertEqual(g.h.read('evaluator'), d.h.read('evaluator'))

    def test_refusals(self):
        g = Rig(temp_probe=False)
        g.keys('ampl', '1', '5', 'enter')                                        # below 20 %: refused, entry cleared
        self.assertEqual((g.screen(), g.val('method.parameters')['amplitude_percent']), ('ampl', None))
        g.keys('2', '0', 'enter')
        self.assertEqual(g.val('method.parameters')['amplitude_percent'], 20)
        g.keys('temp', '3', '5', 'enter')                                         # no temperature probe: not accepted
        self.assertEqual((g.screen(), g.val('thermal.limit_k')), ('main', None))
        self.assertEqual(g.lines()[0], 'Time -:--:--')
        g.keys('save', 'enter', '3', 'enter')                                     # recall of an empty memory
        self.assertEqual(g.screen(), 'main')
        g.press('start_stop')
        self.assertFalse(g.press('timer'))                                        # stand-by keys are not taken while running
        self.assertEqual(g.screen(), 'main')

    def test_power_off_and_rating_time_out(self):
        g = Rig()
        g.keys('ampl', '4', '0', 'enter', 'start_stop')
        g.press('off')                                                            # 0: STOP then main power off
        self.assertEqual((g.val('power.enabled'), g.val('cycle.phase'), g.screen()), (False, 'cancelled', 'off'))
        self.assertFalse(g.press('ampl'))
        g.press('on')
        g.h.step(1.9)
        self.assertEqual(g.screen(), 'rating')
        g.h.step(0.2)
        self.assertEqual(g.screen(), 'main')

    def test_render_determinism_and_contract(self):
        g = Rig()
        a = g.h.panel.render('lcd')
        self.assertTrue((a == g.h.panel.render('lcd')).all())
        cap = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertTrue(Path(cap['image']['path']).is_file())
        g.press('timer')
        g.capture('menu')
        g.keys('9', '9', '9', '9', '9', 'enter')                                  # 9:99:99 is beyond 9:59:59: refused, stays
        g.capture('error')
        self.assertEqual(g.screen(), 'timer')
        c = json.loads(CONTRACT.read_text())
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for k in list(rt.PANEL_CAPS) + ['method.set_parameter', 'program.save', 'program.recall']:
            self.assertIn(k, c['capabilities'])


class CallerRules(unittest.TestCase):
    """A key pressed through a session acts as that session's caller: the run it starts belongs to the caller, and another agent's key
    presses may not stop, pause or change it (the direct commands are refused the same way)."""

    def test_panel_run_belongs_to_the_caller_and_is_guarded(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.keys('ampl', '4', '0', 'enter', 'start_stop')
        op = g.h.operation_id
        self.assertEqual((g.val('cycle.phase'), g.h._owners.get(op)), ('running', 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        g.h.step(1.0)
        before = g.h.read('evaluator')
        for key in ('start_stop', 'pause', 'up', 'off'):                     # B's STOP / PAUSE / ▲ / 0 on A's run: refused, nothing changes
            self.assertFalse(g.call(B, 'panel.press', input_id=f'key_{key}')['accepted'], key)
        for cap in ('acquisition.stop', 'sonication.toggle', 'sonication.pause'):
            self.assertEqual(g.call(B, cap, expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.h.read('evaluator'), before)
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'cancelled')

    def test_refusal_is_shown_on_the_lcd(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.keys('ampl', '4', '0', 'enter', 'start_stop')
        g.h.step(1.0)
        self.assertFalse(g.call(B, 'panel.press', input_id='key_start_stop')['accepted'])
        lcd = g.frame()['displays']['lcd']
        self.assertEqual((lcd['shown'][1], lcd['inverse'][1]), ('Access denied', True))   # bottom line, assumed text
        self.assertEqual(g.val('cycle.phase'), 'running')
        g.h.step(3.1)
        self.assertEqual(g.lines()[1], 'Pulse -- -- Ampl 40 %')

    def test_direct_toggle_run_belongs_to_the_caller(self):
        g = Rig()
        g.call(g.A, 'method.set_parameter', parameter='amplitude_percent', value=40)
        g.call(g.A, 'sonication.toggle')
        op = g.h.operation_id
        self.assertEqual(g.A.poll(op)['status'], 'running')
        g.press('start_stop')                                                   # A's own STOP key
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')


class FaultScreens(unittest.TestCase):
    """OVERLOAD (manual p.28) by the evaluator-only fault.inject: the processor stops, OVERLOAD stays until 0 then I, and comes back at the
    next START while the cause is not removed (fault.clear)."""

    def test_overload_stop_display_restart(self):
        g = Rig()
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='OVERLOAD')['error']['code'], 'ACCESS_DENIED')
        g.keys('ampl', '4', '0', 'enter', 'start_stop')
        g.h.step(2.0)
        g.call(g.E, 'fault.inject', code='OVERLOAD')
        self.assertEqual((g.val('cycle.phase'), g.val('cycle.overload'), g.val('sonication.power_w'), g.lines()[0]), ('failed', True, 0., 'OVERLOAD'))
        self.assertFalse(g.press('start_stop'))                               # START does nothing while OVERLOAD is shown
        self.assertEqual(g.val('cycle.phase'), 'failed')
        g.press('off')
        g.press('on')
        g.h.step(2.5)
        self.assertEqual((g.val('cycle.overload'), g.lines()[0]), (False, 'Time -:--:--'))
        g.press('start_stop')                                                 # cause still there: overloads again at once
        self.assertEqual((g.val('cycle.phase'), g.lines()[0]), ('failed', 'OVERLOAD'))
        g.call(g.E, 'fault.clear')
        self.assertIsNone(g.val('truth.fault_cause'))
        g.press('off')
        g.press('on')
        g.h.step(2.5)
        g.press('start_stop')
        self.assertEqual((g.val('cycle.phase'), g.val('sonication.emitting')), ('running', True))


class Renders(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        old = json.loads((OUT / 'renders.json').read_text()) if (OUT / 'renders.json').exists() else {}
        old.update(RENDERS)
        (OUT / 'renders.json').write_text(json.dumps(old, indent=1) + '\n')

    def test_indicator_pixels(self):
        g = Rig()
        g.keys('ampl', '4', '0', 'enter', 'start_stop')
        img = g.h.panel.render('start_led')
        self.assertTrue(((img == [235, 50, 40]).all(axis=2)).any())


if __name__ == '__main__':
    unittest.main()
