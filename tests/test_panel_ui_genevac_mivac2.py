"""front-panel tests for CHEM-036 genevac_mivac2 (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_genevac_mivac2.py -q

Every gesture goes through the session API (panel.press / panel.turn / panel.switch).  Each check looks at the runtime state and at the screen: the
structured frame (LCD fields, icons, LEDs) and the rendered pixels of the LEDs.  Themes: the six procedures of the menu survey (temperature 45 C,
run type H2O, AUTO run, MANUAL run, pre-heat with latch, HEAT display mode in AUTO) done only with the keys and the SELECT selector; the 30 s
auto-accept; heat time not available below 30 C; standby by STOP; power-on segment test; refusal with the lid open (double beep); panel = direct
capability; render determinism and the PNGs of outputs/panel_ui/genevac_mivac2/; contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import genevac_mivac2 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'mivac_01'
ASSET_DIR = ROOT / 'assets/expansion_batch03/t2_vacuum/genevac_mivac2'
OUT = ROOT / 'outputs/panel_ui/genevac_mivac2'
SPEC = rt.PANEL_SPEC
LEDS = ['led_preheat', 'led_manual', 'led_auto']
SURVEY_TASKS = ['Set the chamber temperature to 45 C', 'Choose the run type for water', 'Run a timed (AUTO) evaporation', 'Run until stopped (MANUAL)',
                'Pre-heat the empty chamber and keep it warm', 'Show heat time instead of total time during AUTO']
DONE_TASKS = set()
METHOD = dict(rt.METHOD_EXAMPLE, duration_s=600.0, heating_time_s=300.0, timeout_s=86400.0, endpoint_remaining_fraction=0.0)


class Rig:
    def __init__(self, sample=True, on=True, method=None):
        self.h = rt.GenevacMivac2Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        self.call(self.E, 'rotor.configure', configuration='two_swing_six_shallow', accessory_compatibility_reviewed=True)
        self.call(self.A, 'method.configure', **copy.deepcopy(method or METHOD))
        if sample:
            self.call(self.E, 'sample.configure', **rt.example_sample())
        self.call(self.A, 'lid.set_open', open=False)
        if on:
            assert self.switch('on')
            self.step(3.0)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        if r['status'] in ('succeeded', 'accepted'):
            assert self.h.time_s == t0, cap                                      # commands and gestures take no simulated time
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def press(self, key, d=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=d)['accepted']

    def turn(self, n):
        return self.call(self.A, 'panel.turn', input_id='knob', detents=n)['accepted']

    def push(self, d=0.1):
        return self.press('knob_push', d)

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def step(self, s):
        self.h.step(float(s))

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lcd(self):
        d = self.frame()['displays']['lcd']
        return {k: v['text'] for k, v in d['fields'].items()}

    def icons(self):
        return {k for k, v in self.frame()['displays']['lcd']['icons'].items() if v['on']}

    def leds(self):
        f = self.frame()['displays']
        on = {k for k in LEDS if f[k]['shown']}
        px = {k for k in LEDS if [int(v) for v in self.h.panel.render(k)[8, 8]] == SPEC['displays'][k]['colors']['on']}
        assert on == px, (on, px)
        return on

    def screen(self):
        return self.frame()['screen']

    def state(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def select(self, position, value_turns=None):
        """Turn SELECT to a parameter (0 temperature, 1 run type, 2 total, 3 heat), press, turn, press."""
        assert self.turn(1)
        if position:
            assert self.turn(position)
        if value_turns is not None:
            assert self.push()
            assert self.turn(value_turns)
            assert self.push()


def runtime_state(h):
    v = h.read('evaluator')
    return json.dumps(dict(read=v, method=h.method, preheat=h.preheat, latched=h.preheat_latched, mode=h.run_mode), sort_keys=True, default=str)


class KeyProcedures(unittest.TestCase):
    def test_set_the_temperature_to_45(self):
        g = Rig()
        self.assertEqual((g.screen(), g.lcd()), ('home', {'method': '---', 'temp': '30', 'time': '00:10'}))
        g.turn(1)
        self.assertEqual((g.screen(), g.lcd()['temp']), ('select', '40'))                 # [ ] on the temperature: its set point
        self.assertTrue({'br_temp_l', 'br_temp_r'} <= g.icons())
        g.push()
        self.assertTrue(g.frame()['displays']['lcd']['icons']['br_temp_l']['blink'])        # the [ ] flash while the value is adjusted
        g.turn(5)
        self.assertEqual(g.lcd()['temp'], '45')
        g.push()
        self.assertEqual((g.screen(), g.lcd()['temp']), ('home', '30'))                   # back to the current chamber temperature
        self.assertAlmostEqual(g.h.method['temperature_k'], 318.15)
        DONE_TASKS.add('Set the chamber temperature to 45 C')

    def test_choose_run_type_water(self):
        g = Rig()
        g.select(1, value_turns=1)                                                          # --- -> H2O (wraps)
        self.assertEqual((g.h.method['program'], g.lcd()['method']), ('water', 'H2O'))
        g.select(1, value_turns=2)
        self.assertEqual((g.h.method['program'], g.lcd()['method']), ('other', '---'))
        g.select(1, value_turns=1)
        DONE_TASKS.add('Choose the run type for water')

    def test_timed_auto_run(self):
        g = Rig()
        g.select(0, value_turns=5)                                                          # 45 C
        g.select(2, value_turns=-5)                                                         # TOTAL 0:05
        g.select(3, value_turns=-2)                                                         # HEAT 0:03
        self.assertEqual((g.h.method['duration_s'], g.h.method['heating_time_s']), (300.0, 180.0))
        self.assertTrue(g.press('key_auto'))
        self.assertEqual((g.state('cycle.phase'), g.leds()), ('running', {'led_auto'}))
        self.assertEqual(g.lcd()['time'], '00:05')
        g.step(61.0)
        self.assertEqual(g.lcd()['time'], '00:04')                                          # counts down
        g.step(250.0)
        self.assertEqual((g.state('cycle.phase'), g.leds(), g.lcd()['time']), ('succeeded', set(), '00:05'))
        self.assertGreater(g.state('thermal.temperature_k'), 310.0)
        DONE_TASKS.add('Run a timed (AUTO) evaporation')

    def test_manual_run_until_stop(self):
        g = Rig()
        g.select(2, value_turns=-8)                                                         # TOTAL 0:02: MANUAL ignores it
        self.assertTrue(g.press('key_manual'))
        self.assertEqual((g.state('cycle.phase'), g.leds(), g.lcd()['time']), ('running', {'led_manual'}, '00:00'))
        g.step(185.0)
        self.assertEqual((g.state('cycle.phase'), g.lcd()['time']), ('running', '00:03'))  # counts up
        self.assertTrue(g.press('key_stop'))
        self.assertEqual(g.state('cycle.phase'), 'cancelled')
        DONE_TASKS.add('Run until stopped (MANUAL)')

    def test_preheat_and_latch(self):
        g = Rig(sample=False)
        self.assertTrue(g.press('key_preheat'))
        self.assertEqual((g.leds(), g.state('controls.state')['preheat']), ({'led_preheat'}, True))
        g.step(200.0)
        self.assertAlmostEqual(g.state('thermal.temperature_k'), 313.15, places=2)        # pre-heated to the control temperature
        g.press('key_preheat')
        self.assertEqual(g.leds(), set())
        self.assertTrue(g.press('key_preheat', 5.0))                                        # held 5 s: latched
        self.assertTrue(g.state('controls.state')['preheat_latched'])
        g.press('key_preheat')
        self.assertEqual((g.state('controls.state')['preheat'], g.state('controls.state')['preheat_latched']), (False, False))
        DONE_TASKS.add('Pre-heat the empty chamber and keep it warm')

    def test_heat_time_display_in_auto(self):
        g = Rig()
        g.select(3)
        self.assertEqual((g.lcd()['time'], 'heat' in g.icons()), ('00:05', True))
        self.assertTrue(g.push(5.0))                                                        # hold SELECT 5 s in Heat Time
        self.assertEqual(g.state('controls.state')['auto_display'], 'heat')
        self.assertEqual(g.frame()['ui']['beeps'], 1)
        g.step(31.0)
        self.assertEqual(g.screen(), 'home')
        g.press('key_auto')
        self.assertEqual((g.lcd()['time'], 'heat' in g.icons(), 'total' in g.icons()), ('00:05', True, False))
        g.step(301.0)
        self.assertEqual((g.lcd()['time'], 'total' in g.icons()), ('00:05', True))          # heat time over: the remaining TOTAL time
        DONE_TASKS.add('Show heat time instead of total time during AUTO')


class Rules(unittest.TestCase):
    def test_auto_accept_and_heat_time_below_30(self):
        g = Rig()
        g.select(0)
        g.push()
        g.turn(-20)
        self.assertEqual(g.lcd()['temp'], '--')
        g.step(30.0)                                                                        # accepted automatically after 30 s
        self.assertEqual((g.screen(), g.state('controls.state')['heat_off']), ('home', True))
        g.turn(1)
        g.turn(3)                                                                           # heat time is skipped below 30 C
        self.assertEqual(g.frame()['ui']['menu']['item'], 'total')
        g.step(31.0)
        self.assertEqual(g.screen(), 'home')

    def test_power_test_standby_and_refusals(self):
        g = Rig(on=False)
        self.assertEqual((g.screen(), g.lcd()), ('off', {'method': '', 'temp': '', 'time': ''}))
        self.assertFalse(g.press('key_auto'))
        g.switch('on')
        self.assertEqual((g.screen(), g.lcd()), ('boot', {'method': '888', 'temp': '88', 'time': '88:88'}))
        g.step(2.5)
        self.assertEqual(g.screen(), 'home')
        g.call(g.A, 'lid.set_open', open=True)
        self.assertFalse(g.press('key_auto'))                                               # lid open: double beep, the rotor does not spin
        self.assertEqual((g.frame()['ui']['beeps'], g.state('cycle.phase')), (2, 'idle'))
        g.call(g.A, 'lid.set_open', open=False)
        g.press('key_stop')
        self.assertEqual((g.screen(), g.lcd()['temp']), ('standby', ''))
        g.press('key_auto')                                                                 # any key wakes, nothing else
        self.assertEqual((g.screen(), g.state('cycle.phase')), ('home', 'idle'))
        g.press('key_auto')
        self.assertEqual(g.state('cycle.phase'), 'running')
        g.select(0)                                                                         # settings are refused during a run
        g.push()
        g.turn(2)
        self.assertFalse(g.push())
        self.assertAlmostEqual(g.h.method['temperature_k'], 313.15)

    def test_unknown_inputs_rejected(self):
        g = Rig()
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='key_x')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.A.invoke(instance_id=INST, capability_id='panel.frame', arguments={}, request_id='f1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_calls(self):
        a, b = Rig(), Rig()
        a.select(0, value_turns=5)
        a.select(1, value_turns=1)
        a.select(2, value_turns=-5)
        a.press('key_preheat')
        b.call(b.A, 'run.configure', temperature_k=318.15)
        b.call(b.A, 'run.configure', program='water')
        b.call(b.A, 'run.configure', duration_s=300.0)
        b.call(b.A, 'preheat.set', on=True)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.press('key_auto')
        b.call(b.A, 'run.start', mode='auto')
        a.step(60.0)
        b.step(60.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


class CallerRules(unittest.TestCase):
    """Sync audit 2026-10-19 (rule 2): a run started with MANUAL / AUTO belongs to the caller; another agent cannot stop or switch it off."""

    def test_panel_started_run_belongs_to_the_caller(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertTrue(g.press('key_auto'))
        op = g.h.operation_id
        self.assertEqual(g.h._owners, {op: 'agent'})
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        g.step(5.0)
        before = runtime_state(g.h)
        r = g.call(B, 'panel.press', input_id='key_stop', duration_s=0.1)              # B's STOP key: refused, the run goes on
        self.assertFalse(r['accepted'])
        self.assertEqual(g.lcd()['time'], 'dEnY')                                         # sweep 10-19: the refusal shows a short code for 2 s
        self.assertEqual(g.frame()['ui']['message']['text'], 'dEnY')
        self.assertEqual(g.call(B, 'acquisition.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(runtime_state(g.h), before)
        g.step(2.5)
        self.assertEqual(g.lcd()['time'], '00:10')                                        # the code goes after 2 s: the AUTO time again
        self.assertEqual(g.A.cancel(op, 'c1')['status'], 'cancelled')                   # the owner cancels its own panel-started run

    def test_hard_power_switch_is_never_refused(self):
        """Lead rule 10-19: the rear power switch is a physical action; B switching it off cancels A's run as a power loss."""
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertTrue(g.press('key_auto'))
        g.step(5.0)
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])
        self.assertEqual((g.state('power.enabled'), g.state('cycle.phase')), (False, 'cancelled'))
        self.assertTrue(g.switch('on'))
        g.step(3.0)                                                                       # power-on segment test
        self.assertTrue(g.press('key_auto'))                                              # a new run (agent A)
        g.call(B, 'power.set', enabled=False)                                             # power.set models the same hard switch
        self.assertEqual(g.state('cycle.phase'), 'cancelled')

    def test_owner_and_evaluator_may_stop(self):
        g = Rig()
        self.assertTrue(g.press('key_manual'))
        op = g.h.operation_id
        self.assertEqual(g.h._owners[op], 'agent')
        self.assertTrue(g.press('key_stop'))
        self.assertEqual(g.state('cycle.phase'), 'cancelled')
        g.call(g.A, 'run.start', mode='auto')                                           # a direct start: the evaluator may still switch off
        g.call(g.E, 'power.set', enabled=False)
        self.assertEqual(g.state('cycle.phase'), 'cancelled')


class FaultCodes(unittest.TestCase):
    """Sweep 10-19 (1.2.0): the self-check error codes of manual section 8 (p.16) by evaluator fault.inject / fault.clear."""

    def test_fault_stops_the_run_and_shows_its_code(self):
        g = Rig()
        self.assertTrue(g.press('key_auto'))
        g.step(10.0)
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='Er05')['error']['code'], 'ACCESS_DENIED')   # evaluator only
        g.call(g.E, 'fault.inject', code='Er05')
        self.assertEqual((g.state('cycle.phase'), g.state('cycle.fault'), g.state('safety.error_code')), ('failed', 'Er05', 'Er05'))
        self.assertEqual(g.lcd()['time'], 'Er05')
        self.assertEqual(g.leds(), set())
        g.step(120.0)
        self.assertEqual(g.state('rotor.speed_rad_s'), 0.0)                               # the rotor ran down
        beeps = g.frame()['ui']['beeps']
        self.assertFalse(g.press('key_manual'))                                           # no new run while the code is shown: double beep
        self.assertEqual(g.frame()['ui']['beeps'], beeps + 2)
        self.assertEqual(g.call(g.A, 'run.start', expect='rejected', mode='auto')['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(g.lcd()['time'], 'Er05')

    def test_clear_needs_the_cause_removed_and_a_power_cycle(self):
        g = Rig()
        g.call(g.E, 'fault.inject', code='Er10')
        self.assertEqual(g.lcd()['time'], 'Er10')                                         # no run: the self-check shows it at once
        for wait in (1.0, 6.0):                                                           # cause still there: the code comes back at power-up
            g.switch('off')
            g.step(wait)
            g.switch('on')
            g.step(3.0)
            self.assertEqual(g.lcd()['time'], 'Er10')
        self.assertTrue(g.call(g.E, 'fault.clear')['cleared'])
        self.assertEqual(g.lcd()['time'], 'Er10')                                         # cleared cause: the code stays until a power cycle
        g.switch('off')
        g.step(2.0)
        g.switch('on')
        g.step(3.0)
        self.assertEqual(g.lcd()['time'], 'Er10')                                         # off for less than 5 s
        g.switch('off')
        g.step(5.0)
        g.switch('on')
        g.step(3.0)
        self.assertEqual((g.lcd()['time'], g.state('safety.error_code')), ('00:10', None))
        self.assertTrue(g.press('key_auto'))                                              # runs again

    def test_lid_fault_keeps_the_lid_locked_and_every_code_shows(self):
        g = Rig()
        g.call(g.E, 'fault.inject', code='Er09')
        g.step(30.0)
        self.assertEqual(g.call(g.A, 'lid.set_open', expect='rejected', open=True)['error']['code'], 'LID_INTERLOCK')   # Er 09: lid failed to unlock
        g.call(g.E, 'fault.clear')
        g.call(g.A, 'lid.set_open', open=True)
        for code in sorted(rt.FAULT_CODES):
            g.call(g.E, 'fault.inject', code=code)
            self.assertEqual(g.lcd()['time'], code, code)
        self.assertNotIn('Er07', rt.FAULT_CODES)                                          # Er 07 comes from a rotor imbalance in a run


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        for d in SPEC['displays']:
            self.assertTrue((a.h.panel.render(d) == b.h.panel.render(d)).all())
        self.assertEqual(a.h.panel.png_bytes('lcd'), b.h.panel.png_bytes('lcd'))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png('lcd', OUT / 'home.png')}
        a.turn(1)
        shots['menu'] = a.h.panel.save_png('lcd', OUT / 'menu.png')                       # [ ] on the chamber temperature
        a.step(31.0)                                                                        # back to the ready mode
        a.h.fault, a.h.phase = 'ROTOR_IMBALANCE', 'failed'                                 # a run stopped by an imbalance
        self.assertEqual(a.lcd()['time'], 'Er07')
        shots['error'] = a.h.panel.save_png('lcd', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='lcd')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())

    def test_spec_and_contract(self):
        meta = rt.export_genevac_mivac2_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.turn', 'panel.switch', 'panel.capture', 'panel.frame'})
        c = json.loads((ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in list(rt.PANEL_CAPS) + ['run.configure', 'run.start', 'preheat.set']:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'preconditions', 'errors'):
                self.assertEqual(c['capabilities'][k][key], meta['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class']), ('panel_ui', 'parameter_cycle'))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET_DIR / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('mivac_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 1:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
