"""front-panel tests for CHEM-053 ossila_spincoater (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_ossila_spincoater.py -q

Every gesture goes through the session API (panel.press / panel.switch); the host steps the clock and moves the lid / places the substrate.  Each
check looks at the runtime state and at the screen (structured frame, rendered pixels).  Themes: the five key procedures of the menu survey (user,
program, a 2-step recipe, run, abort); the cursor and the six Value keys with their limits; the program store per user (presets of Table 8.1);
lid warning and power; panel = direct capability; render determinism, evidence PNGs and contract entries."""
import copy
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import ossila_spincoater as rt  # noqa: E402

INST = 'spincoater_01'
OUT = ROOT / 'outputs/panel_ui/ossila_spincoater'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
RPM = rt.RPM
SURVEY_TASKS = ['select a user profile', 'select a program', 'edit a 2-step recipe (2000 rpm 30 s, 5000 rpm 5 s)', 'run a program', 'abort a run']
DONE_TASKS = set()


class Rig:
    def __init__(self, sample=True):
        self.h = rt.OssilaSpincoaterRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        if sample:
            self.lid(1.5)
            self.call(self.E, 'sample.configure', **copy.deepcopy(rt.SAMPLE_EXAMPLE))
            self.lid(0.0)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        ok = r['status'] in ('succeeded', 'accepted')
        assert ok if expect == 'succeeded' else r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if ok else r

    def lid(self, angle):
        self.call(self.A, 'lid.set_angle', angle_rad=angle)
        self.h.step(1.2)

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def press(self, key, n=1):
        ok = True
        for _ in range(n):
            ok = self.call(self.A, 'panel.press', input_id=key)['accepted'] and ok
        return ok

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def texts(self):
        return {w['id']: w['text'] for w in self.frame()['displays']['screen']['widgets']}

    def cursor_line(self):
        t = self.texts()
        return [i for i in range(5) if f'e_cursor{i}' in t]

    def goto_line(self, i):
        while self.cursor_line() != [i]:
            self.press('select_down' if self.cursor_line()[0] < i else 'select_up')

    def set_value(self, line, target, field):
        """Value keys only (large, medium, small; up or down) until the shown number is the target."""
        self.goto_line(line)
        for size, unit in (('large', 1000 if line == 3 else 100), ('medium', 100 if line == 3 else 10), ('small', 10 if line == 3 else 1)):
            for _ in range(20):
                cur = int(self.texts()[field].split(':')[1].split()[0]) if ':' in self.texts()[field] else int(self.texts()[field].split()[0])
                if cur + unit <= target:
                    self.press(f'value_{size}_up')
                elif cur - unit >= target and cur - unit >= (120 if line == 3 else 1):
                    self.press(f'value_{size}_down')
                else:
                    break


class SurveyProcedures(unittest.TestCase):
    def test_select_a_user_profile(self):
        g = Rig()
        g.switch('on')
        self.assertEqual(g.texts()['e_user'], 'User: 01')
        self.assertTrue(g.press('user_right', 2))                             # press the right User button
        self.assertEqual((g.texts()['e_user'], g.val('programs.state')['user']), ('User: 03', 3))
        self.assertTrue(g.press('user_left'))
        self.assertEqual(g.texts()['e_user'], 'User: 02')
        g.press('user_left')
        self.assertFalse(g.press('user_left'))                                # User 01 is the first profile
        DONE_TASKS.add('select a user profile')

    def test_select_a_program(self):
        g = Rig()
        g.switch('on')
        self.assertEqual(g.cursor_line(), [0])                                 # cursor on the Program line
        self.assertTrue(g.press('value_small_up'))                             # up: Program 02 (pre-set 2 steps)
        t = g.texts()
        self.assertEqual((t['e_program'], t['e_steps'], t['e_speed'], t['e_time']), ('Program 02', '02 Steps', 'Speed : 2000 rpm', 'Time  : 0030 s'))
        g.press('value_small_up', 3)
        self.assertEqual((g.texts()['e_program'], g.texts()['e_speed']), ('Program 05', 'Speed : 0000 rpm'))   # empty program
        g.press('value_large_up')
        self.assertEqual(g.val('programs.state')['program'], 10)              # 10 programs per user
        DONE_TASKS.add('select a program')

    def test_edit_a_2_step_recipe(self):
        g = Rig()
        g.switch('on')
        g.press('value_small_up', 4)                                           # Program 05 (empty)
        g.goto_line(1)
        g.press('value_small_up')                                              # 3. two steps
        self.assertEqual(g.texts()['e_steps'], '02 Steps')
        g.goto_line(2)                                                         # 4. Step 01
        g.set_value(3, 2000, 'e_speed')                                        # 5. Speed 2000
        g.set_value(4, 30, 'e_time')                                           # 6. Time 30
        g.goto_line(2)
        g.press('value_small_up')                                              # 7. Step 02
        self.assertEqual(g.texts()['e_step'], 'Step  : 02 of 02')
        g.set_value(3, 5000, 'e_speed')
        g.set_value(4, 5, 'e_time')
        p = g.val('programs.state')
        self.assertEqual([(round(s['speed_rad_s'] / RPM), s['duration_s']) for s in p['steps']], [(2000, 30.0), (5000, 5.0)])
        self.assertTrue(p['runnable'] and g.val('method.configured'))
        g.press('user_right')
        g.press('user_left')
        self.assertEqual(g.val('programs.state')['steps'][1]['speed_rad_s'], 5000 * RPM)   # stored in User 01 Program 05
        DONE_TASKS.add('edit a 2-step recipe (2000 rpm 30 s, 5000 rpm 5 s)')

    def test_run_a_program(self):
        g = Rig()
        g.switch('on')
        g.press('value_small_up')                                              # Program 02: 2000 rpm 30 s, 5000 rpm 5 s
        self.assertTrue(g.press('start'))                                      # lid closed: start
        self.assertEqual((g.frame()['screen'], g.val('cycle.phase')), ('run', 'running'))
        g.h.step(10.0)
        t = g.texts()
        self.assertEqual((t['r_title'], t['r_step'], t['r_speed'], t['r_elapsed'], t['r_current']),
                         ('Running Prog 02', 'Step  : 01 of 02', 'Speed : 2000 rpm', 'Elapsed Time  : 0010 s', 'Current Speed : 2000 rpm'))
        self.assertEqual(t['r_percent'], '28 %')
        g.h.step(22.0)
        self.assertEqual(g.texts()['r_step'], 'Step  : 02 of 02')
        g.h.step(3.5)                                                          # program end (35 s): STOPPING on the parameter page
        self.assertEqual((g.val('cycle.phase'), g.frame()['screen'], g.texts()['e_status']), ('stopping', 'edit', 'STOPPING'))
        g.h.step(10.0)
        self.assertEqual((g.val('cycle.phase'), g.texts()['e_status']), ('succeeded', 'READY'))
        DONE_TASKS.add('run a program')

    def test_abort_a_run(self):
        g = Rig()
        g.switch('on')
        g.press('start')
        g.h.step(3.0)
        self.assertTrue(g.press('stop'))                                       # 9. STOP
        self.assertEqual((g.val('cycle.phase'), g.frame()['screen'], g.texts()['r_status']), ('stopping', 'run', 'STOPPING'))
        self.assertFalse(g.press('start'))                                     # 10. no re-run while STOPPING
        g.h.step(30.0)
        self.assertEqual((g.val('cycle.phase'), g.frame()['screen']), ('cancelled', 'edit'))
        self.assertTrue(g.press('start'))
        self.assertEqual(g.val('cycle.step_index'), 1)
        DONE_TASKS.add('abort a run')


class CallerRules(unittest.TestCase):
    """Keys pressed through a session act as that session's caller (1.2.1): a program started with START belongs to the caller, and another agent
    can stop or switch off the run neither through the keys nor directly."""
    def test_hard_power_switch_is_never_refused(self):
        """Lead rule 10-19 (1.2.2): the rear power switch is a physical action; another agent switching it off stops the run as a power loss, and
        power.set / power.toggle, which model that switch, follow it."""
        g = Rig()
        g.switch('on')
        self.assertTrue(g.press('start'))
        op = g.h.operation_id
        g.h.register_principal('agent2', 'agent')
        b = g.h.session('agent2')
        g.h.step(0.5)
        r = b.invoke(instance_id=INST, capability_id='panel.switch', arguments=dict(input_id='power_switch', position='off'), request_id='b2')
        self.assertTrue(r['result']['accepted'])
        self.assertFalse(g.val('power.enabled'))
        self.assertNotEqual(g.A.poll(op)['status'], 'running')
        r = b.invoke(instance_id=INST, capability_id='power.toggle', arguments={}, request_id='b3')   # the same switch, directly
        self.assertEqual(r['status'], 'succeeded')
        self.assertTrue(g.val('power.enabled'))

    def test_panel_started_run_belongs_to_the_caller(self):
        g = Rig()
        g.switch('on')
        self.assertTrue(g.press('start'))
        op = g.h.operation_id
        self.assertEqual((g.val('cycle.phase'), g.h._owners.get(op)), ('running', 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        g.h.register_principal('agent2', 'agent')
        b = g.h.session('agent2')
        with self.assertRaises(rt.ActionRejected):
            b.poll(op)
        g.h.step(2.0)
        before = g.h.debug_snapshot()
        r = b.invoke(instance_id=INST, capability_id='panel.press', arguments=dict(input_id='stop', duration_s=0.1), request_id='b1')
        self.assertFalse(r['result']['accepted'])
        self.assertEqual(g.texts().get('msg'), 'Access denied')                    # sweep 10-19 (1.2.2): the refusal is shown on the screen
        img = g.h.panel.render('screen')
        self.assertTrue((img[120, 160] == rt.PANEL_SPEC['displays']['screen']['colors']['error']).all())
        for cap, args in (('acquisition.stop', {}),):
            r = b.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        after = g.h.debug_snapshot()
        for k in ('phase', 'powered', 'target_speed', 'speed', 'step_index', 'operation_id', 'film'):
            self.assertEqual(before[k], after[k], k)
        self.assertEqual((g.frame()['screen'], g.texts()['r_title']), ('run', 'Running Prog 01'))
        g.h.step(2.5)
        self.assertNotIn('msg', g.texts())                                          # gone after 2 s
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'succeeded')
        self.assertEqual(g.A.poll(op)['status'], 'stopping')
        g.h.step(30.0)
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')

    def test_caller_and_evaluator_stop_the_run(self):
        g = Rig()
        g.switch('on')
        g.press('start')
        op = g.h.operation_id
        g.h.step(2.0)
        self.assertTrue(g.press('stop'))                                       # the caller's own STOP key
        self.assertEqual(g.A.poll(op)['status'], 'stopping')
        g.h.step(30.0)
        g.press('start')
        g.h.step(2.0)
        g.call(g.E, 'acquisition.stop')                                        # the evaluator may stop any run
        self.assertEqual(g.val('cycle.phase'), 'stopping')


class PanelRules(unittest.TestCase):
    def test_value_limits_and_cursor_ends(self):
        g = Rig(sample=False)
        g.switch('on')
        self.assertFalse(g.press('select_up'))                                 # first line
        g.goto_line(4)
        self.assertFalse(g.press('select_down'))                               # last line
        g.press('value_large_up', 12)
        self.assertEqual(g.texts()['e_time'], 'Time  : 1000 s')
        g.press('value_large_down', 12)
        self.assertEqual(g.texts()['e_time'], 'Time  : 0001 s')
        g.goto_line(3)
        g.press('value_large_up', 7)
        self.assertEqual(g.texts()['e_speed'], 'Speed : 6000 rpm')
        g.press('value_large_down', 7)
        self.assertEqual(g.texts()['e_speed'], 'Speed : 0120 rpm')
        g.goto_line(1)
        g.press('value_large_up')
        self.assertEqual(g.texts()['e_steps'], '50 Steps')
        g.goto_line(2)
        g.press('value_large_up')
        self.assertEqual(g.texts()['e_step'], 'Step  : 50 of 50')
        g.goto_line(1)
        g.press('value_medium_down', 5)
        self.assertEqual((g.texts()['e_steps'], g.texts()['e_step']), ('01 Steps', 'Step  : 01 of 01'))   # the edited step follows

    def test_lid_warning_unprogrammed_and_busy(self):
        g = Rig()
        g.switch('on')
        g.lid(1.5)
        self.assertEqual(g.texts()['e_status'], 'LID OPEN')
        self.assertFalse(g.press('start'))                                     # cycle will not start
        g.lid(0.0)
        g.press('value_small_up', 4)                                           # Program 05: 0000 rpm, not programmed
        self.assertFalse(g.press('start'))
        self.assertEqual(g.val('cycle.phase'), 'idle')
        g.press('value_small_down', 4)
        g.press('start')
        g.h.step(1.0)
        before = g.val('programs.state')
        self.assertFalse(g.press('value_large_up'))                            # running: the Value keys do nothing
        self.assertEqual(g.val('programs.state'), before)
        g.lid(1.5)                                                             # lid opened while running: the motor is forced to stop
        self.assertEqual((g.val('cycle.phase'), g.texts()['r_status']), ('stopping', 'STOPPING'))
        g.h.step(3.0)
        self.assertEqual((g.val('cycle.phase'), g.texts()['e_status']), ('cancelled', 'LID OPEN'))

    def test_power_switch(self):
        g = Rig()
        self.assertEqual(int(g.h.panel.render('screen').max()), 0)
        self.assertFalse(g.press('start'))
        self.assertTrue(g.switch('on'))
        self.assertTrue(g.val('power.enabled'))
        g.press('start')
        g.h.step(2.0)
        self.assertTrue(g.switch('off'))                                       # switched off: the motor coasts down
        self.assertEqual(g.val('cycle.phase'), 'stopping')
        self.assertEqual(int(g.h.panel.render('screen').max()), 0)

    def test_panel_and_direct_commands_give_the_same_state(self):
        p, d = Rig(), Rig()
        for g in (p, d):
            g.switch('on')
        p.press('value_small_up', 2)                                           # Program 03
        p.goto_line(1)
        p.press('value_small_up')
        p.goto_line(3)
        p.press('value_large_up', 3)
        p.goto_line(4)
        p.press('value_medium_up', 2)
        p.goto_line(2)
        p.press('value_small_up')
        p.goto_line(3)
        p.press('value_large_up', 2)
        p.goto_line(4)
        p.press('value_small_up', 7)
        p.press('start')
        d.call(d.A, 'program.select', user=1, program=3)
        d.call(d.A, 'program.set_steps_count', count=2)
        d.call(d.A, 'program.set_step', step=1, speed_rad_s=3000 * RPM, duration_s=20.0)
        d.call(d.A, 'program.set_step', step=2, speed_rad_s=2000 * RPM, duration_s=7.0)
        d.call(d.A, 'program.select', user=1, program=3)
        d.call(d.A, 'acquisition.start')
        for g in (p, d):
            g.h.step(15.0)
        a, b = p.h.read('evaluator'), d.h.read('evaluator')
        self.assertEqual(a, b)

    def test_method_configure_is_stored_in_the_selected_program(self):
        g = Rig()
        g.call(g.A, 'program.select', user=4, program=7)
        g.call(g.A, 'method.configure', **rt.METHOD_EXAMPLE)
        g.call(g.A, 'program.select', user=1, program=1)
        g.call(g.A, 'program.select', user=4, program=7)
        self.assertEqual([s['speed_rad_s'] for s in g.val('programs.state')['steps']], [s['speed_rad_s'] for s in rt.METHOD_EXAMPLE['steps']])
        g.call(g.A, 'program.set_step', expect='rejected', step=3, speed_rad_s=1000 * RPM)
        g.call(g.A, 'program.set_step', expect='rejected', step=1, speed_rad_s=50 * RPM)

    def test_only_agent_capabilities_behind_the_panel(self):
        caps = rt._panel_ui.spec_capabilities(rt.PANEL_SPEC)
        self.assertEqual(sorted(caps), ['acquisition.start', 'acquisition.stop', 'power.set', 'program.select', 'program.set_step', 'program.set_steps_count'])
        self.assertTrue(all(rt.ALL_CAPS[k][5] == 'agent' for k in caps))
        g = Rig()
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.call(g.A, 'panel.press', expect='rejected', input_id='nope')['error']['code'], 'INVALID_ARGUMENT')

    def test_render_determinism_and_capture(self):
        g = Rig()
        g.switch('on')
        a, b = g.h.panel.render('screen'), g.h.panel.render('screen')
        self.assertEqual(a.shape, (240, 320, 3))
        self.assertTrue((a == b).all())
        r1 = g.call(g.A, 'panel.capture', display_id='screen')
        self.assertEqual(r1['image'], g.call(g.A, 'panel.capture', display_id='screen')['image'])
        self.assertEqual(hashlib.sha256(Path(r1['image']['path']).read_bytes()).hexdigest(), r1['image']['sha256'])
        c = copy.deepcopy(g.h)
        self.assertTrue((c.panel.render('screen') == a).all())
        before = g.h.debug_snapshot()
        g.h.read()
        g.h.panel.frame()
        self.assertEqual(g.h.debug_snapshot(), before)

    def test_evidence_renders(self):
        OUT.mkdir(parents=True, exist_ok=True)
        g = Rig()
        g.switch('on')
        g.press('value_small_up')
        shots = dict(home=g.h.panel.save_png('screen', OUT / 'home.png'))
        g.goto_line(3)
        shots['menu'] = g.h.panel.save_png('screen', OUT / 'menu.png')
        g.press('start')
        g.h.step(10.0)
        shots['running'] = g.h.panel.save_png('screen', OUT / 'running.png')
        g.press('stop')
        shots['stopping'] = g.h.panel.save_png('screen', OUT / 'stopping.png')
        g.h.step(30.0)
        g.lid(1.5)
        shots['error'] = g.h.panel.save_png('screen', OUT / 'error.png')
        for v in shots.values():
            self.assertEqual(v['sha256'], hashlib.sha256(Path(v['path']).read_bytes()).hexdigest())
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()}, indent=1) + '\n')

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['ui_class'], 'parameter_cycle')
        for k in list(rt.PANEL_CAPS) + list(rt.PROGRAM_CAPS):
            self.assertIn(k, c['capabilities'])
            for cid in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][cid]['capability_ids'])
        self.assertIn('programs.state', c['state_slots'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        for i in range(10):
            self.assertIn('panel.press', c['components'][f'key_menu_{i}']['capability_ids'])


if __name__ == '__main__':
    unittest.main()
