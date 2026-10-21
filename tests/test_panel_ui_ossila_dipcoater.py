"""front-panel tests for CHEM-054 ossila_dipcoater (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_ossila_dipcoater.py -q

Every gesture goes through the session API (panel.press / panel.switch); the host steps the clock, places the bath and clamps the substrate.
Each check looks at the runtime state and at the screen (structured frame: screen, menu item, editor; rendered pixels).  Themes: the six key
procedures of the menu survey (start-up and homing, coating length, ruler positions, units and drying gap, manual jog, program and run), the
settings warnings, the drying gap reset at switch-off, default page / program, brightness, Free Motion, Return, the end messages (completed,
stopped, crash), refusals, panel = direct capability, render determinism, evidence PNGs and contract entries."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import ossila_dipcoater as rt  # noqa: E402

INST = 'dipcoater_01'
OUT = ROOT / 'outputs/panel_ui/ossila_dipcoater'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['start up and home', 'set coating length', 'set solution height and substrate position', 'change speed units / drying gap',
                'jog the arm manually', 'program and run a dip cycle']
DONE_TASKS = set()
FIELDS = {}
for _m in rt.PANEL_SPEC['menus'].values():
    for _it in _m['items']:
        FIELDS[_it['id']] = _it
        for _sub in _it.get('items', []):
            FIELDS[_sub['id']] = _sub


class Rig:
    def __init__(self, sample=True, boot=True):
        self.h = rt.OssilaDipcoaterRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, mass_transfer_m_s=0.0))
        if boot:
            self.switch('on')
            self.h.step(2.1)
            self.press('ok')
            self.h.step(0.05)
        if sample:
            self.call(self.E, 'bath.configure', **rt.BATH_EXAMPLE)
            self.call(self.A, 'clamp.set_gap', gap_m=0.012)
            self.h.step(0.7)
            self.call(self.E, 'substrate.configure', **rt.SAMPLE_EXAMPLE)
            self.call(self.A, 'clamp.set_gap', gap_m=0.0)
            self.h.step(0.7)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        ok = r['status'] in ('succeeded', 'accepted')
        assert ok if expect == 'succeeded' else r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if ok else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def press(self, key, n=1, hold=0.1):
        ok = True
        for _ in range(n):
            ok = self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted'] and ok
        return ok

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self):
        return [x.rstrip() for x in self.frame()['displays']['screen']['lines']]

    def item(self):
        m = self.frame()['ui']['menu']
        return m['item'] if m else None

    def goto_item(self, iid):
        for _ in range(12):
            if self.item() == iid:
                return
            self.press('down')
        raise AssertionError((iid, self.item()))

    def edit_digits(self, iid, target):
        """OK, then each digit with up / down (the shorter way round) and right, then OK (manual 8.4.2)."""
        self.goto_item(iid)
        assert self.press('ok')
        f = FIELDS[iid]
        radices = [r for _, r in f['places']] if f.get('places') else [10] * f['digits']
        want = [int(c) for c in target if c.isdigit()]
        for i, (t, r) in enumerate(zip(want, radices)):
            cur = [int(c) for c in self.h.panel._field_text(f, self.frame()['ui']['editor']['buffer'][iid]) if c.isdigit()][i]
            d = (t - cur) % r
            self.press('up', d) if d <= r // 2 else self.press('down', r - d)
            if i < len(want) - 1:
                self.press('right')
        assert self.h.panel._field_text(f, self.frame()['ui']['editor']['buffer'][iid]) == target, (iid, target)
        return self.press('ok')


class SurveyProcedures(unittest.TestCase):
    def test_start_up_and_home(self):
        g = Rig(sample=False, boot=False)
        self.assertEqual(g.screen(), 'off')
        self.assertTrue(g.switch('on'))                                       # 1 switch to 1: logo
        self.assertEqual(g.screen(), 'logo')
        g.h.step(2.1)
        self.assertEqual((g.screen(), g.lines()[2].strip()), ('home_confirm', 'Reset Position to'))
        self.assertTrue(g.press('ok'))                                        # 2 OK: homing
        self.assertEqual((g.screen(), g.val('cycle.stage')), ('homing', 'homing'))
        g.h.step(0.05)                                                        # 3 at Home: Settings Mode page
        self.assertEqual((g.screen(), g.val('stage.homed'), g.lines()[0]), ('settings', True, 'Settings Mode'))
        self.assertTrue(g.frame()['displays']['led_on']['on'])
        DONE_TASKS.add('start up and home')

    def test_set_coating_length(self):
        g = Rig(sample=False)
        self.assertTrue(g.press('key_s'))                                     # 1 S
        self.assertEqual(g.item(), 'coating')
        self.assertTrue(g.edit_digits('coating', '040.00'))                   # 2 OK, left / right, up / down, OK
        self.assertAlmostEqual(g.val('settings.state')['coating_length_m'], 0.04)
        self.assertIn('040.00 mm', g.lines()[1])
        DONE_TASKS.add('set coating length')

    def test_set_solution_height_and_substrate_position(self):
        g = Rig()
        g.press('key_s')
        g.press('down')
        self.assertEqual(g.lines()[2].split()[-2:], ['---.--', 'mm'])         # not entered yet
        self.assertTrue(g.edit_digits('position', '245.00'))                  # substrate bottom on the ruler (arm at the top)
        g.press('down')
        self.assertTrue(g.edit_digits('solution', '139.00'))                  # solution surface on the ruler
        st = g.val('settings.state')
        self.assertEqual((round(st['substrate_position_m'], 6), round(st['solution_height_m'], 6), st['warning']), (0.245, 0.139, None))
        lo, hi = g.h._targets() if g.h.method else (None, None)
        g.call(g.A, 'program.select', program=1)
        lo, hi = g.h._targets()
        self.assertAlmostEqual(lo, 0.175 - (0.245 - 0.139 + 0.05))            # dip travel from the entered values
        self.assertAlmostEqual(hi, 0.175 - (0.245 - 0.139) + 0.005)
        DONE_TASKS.add('set solution height and substrate position')

    def test_change_speed_units_and_drying_gap(self):
        g = Rig(sample=False)
        g.press('key_s')
        g.goto_item('advanced')
        self.assertTrue(g.press('ok'))                                        # Advanced highlighted, OK
        self.assertEqual(g.item(), 'gap')
        g.goto_item('unit')
        g.press('ok')
        self.assertTrue(g.press('right'))                                     # left / right choose mm/min
        g.press('ok')
        self.assertEqual(g.val('settings.state')['speed_unit'], 'mm/min')
        g.press('up')
        g.press('ok')
        g.press('up', 5)                                                      # drying gap 5 -> 10 mm
        g.press('ok')
        self.assertAlmostEqual(g.val('settings.state')['drying_gap_m'], 0.010)
        g.goto_item('back')
        g.press('ok')                                                         # Back
        self.assertEqual(g.item(), 'advanced')
        g.press('key_m')
        self.assertIn('0600.0 mm/min', g.lines()[2])                          # 10 mm/s shown in mm/min
        g.switch('off')                                                       # the drying gap resets at switch-off
        self.assertAlmostEqual(g.val('settings.state')['drying_gap_m'], 0.005)
        DONE_TASKS.add('change speed units / drying gap')

    def test_jog_the_arm_manually(self):
        g = Rig(sample=False)
        g.press('key_m')                                                      # 1 M
        g.goto_item('imm')
        g.edit_digits('imm', '020.00')                                        # 2 immersion speed 20 mm/s
        self.assertAlmostEqual(g.val('settings.state')['manual_immersion_speed_m_s'], 0.02)
        g.press('up', 2)
        self.assertTrue(g.press('left'))                                      # 3 left selects the diagram
        self.assertTrue(g.frame()['ui']['vars']['jog'])
        self.assertTrue(g.press('down', hold=2.0))                            # 4 down held 2 s: 40 mm at 20 mm/s
        g.h.step(2.5)
        self.assertAlmostEqual(g.val('stage.position_m'), 0.135, places=6)
        self.assertIn('135.00', g.lines()[1])
        self.assertTrue(g.press('up', hold=1.0))                              # up at the withdrawal speed (10 mm/s)
        g.h.step(1.5)
        self.assertAlmostEqual(g.val('stage.position_m'), 0.145, places=6)
        g.press('ok')
        self.assertFalse(g.frame()['ui']['vars']['jog'])
        self.assertTrue(g.press('return_key'))                                # Return: arm to the top
        g.h.step(4.0)
        self.assertAlmostEqual(g.val('stage.position_m'), 0.175, places=6)
        DONE_TASKS.add('jog the arm manually')

    def test_program_and_run_a_dip_cycle(self):
        g = Rig()
        g.press('key_s')
        g.edit_digits('coating', '040.00')
        self.assertTrue(g.press('key_p'))                                     # 1 P
        g.goto_item('cycle')
        g.edit_digits('cycle', '0002')                                        # 3 Cycle
        g.press('down')
        g.edit_digits('dip', '050.00')                                        # 4 Dip Speed
        g.press('down')
        g.edit_digits('dwell', '00:00:01.0')                                  # 5 Dwell Time
        g.goto_item('to_wd')
        g.press('ok')                                                         # 6 Withdrawal page
        self.assertEqual((g.screen(), g.item()), ('program_wd', 'to_imm'))
        g.press('up')
        g.edit_digits('dry', '00:00:00.5')                                    # 9 dry time
        g.press('up', 2)
        g.edit_digits('final', '020.00')                                      # 8 final speed
        g.press('up', 2)
        g.edit_digits('init', '010.00')                                       # 7 initial speed
        g.goto_item('to_imm')
        g.press('ok')                                                         # 10 back to Immersion
        self.assertEqual(g.screen(), 'program_imm')
        p = g.val('programs.state')
        self.assertEqual((p['cycles'], p['immersion_speed_m_s'], p['dwell_s'], p['withdrawal_initial_m_s'], p['withdrawal_final_m_s'], p['dry_s']),
                         (2, 0.05, 1.0, 0.01, 0.02, 0.5))
        self.assertTrue(g.press('start'))                                     # 11 START
        self.assertEqual((g.screen(), g.val('cycle.phase')), ('running', 'running'))
        self.assertTrue(g.frame()['displays']['led_coating']['on'])
        g.h.step(3.0)
        self.assertEqual(g.lines()[1].strip(), 'Cycle : 0001 of 0002')
        for _ in range(200):
            if g.val('cycle.phase') != 'running':
                break
            g.h.step(1.0)
        self.assertEqual((g.val('cycle.phase'), g.screen(), g.lines()[2].strip()), ('succeeded', 'done', 'Coating Completed.'))   # 12
        self.assertGreater(g.call(g.E, 'product.read')['coated_area_m2'], 0.0)
        g.press('ok')
        self.assertEqual(g.screen(), 'program_imm')
        DONE_TASKS.add('program and run a dip cycle')


class CallerRules(unittest.TestCase):
    """Keys pressed through a session act as that session's caller (1.3.0): a run, homing or jog started with a key belongs to the caller, and another
    agent can stop or switch off the operation neither through the keys nor directly."""
    def test_hard_power_switch_is_never_refused(self):
        """Lead rule 10-19 (1.3.1): the rear power switch is a physical action; another agent switching it off stops the run as a power loss, and
        power.set / power.toggle, which model that switch, follow it."""
        g = Rig()
        g.press('key_s')
        g.edit_digits('coating', '040.00')
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
        g.press('key_s')
        g.edit_digits('coating', '040.00')
        self.assertTrue(g.press('start'))
        op = g.h.operation_id
        self.assertEqual((g.val('cycle.phase'), g.h._owners.get(op)), ('running', 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        g.h.register_principal('agent2', 'agent')
        b = g.h.session('agent2')
        with self.assertRaises(rt.ActionRejected):
            b.poll(op)
        g.h.step(0.5)
        before = g.h.debug_snapshot()
        r = b.invoke(instance_id=INST, capability_id='panel.press', arguments=dict(input_id='stop', duration_s=0.1), request_id='b1')
        self.assertFalse(r['result']['accepted'])
        self.assertEqual(g.frame()['ui']['message']['text'], 'Access denied')
        for cap, args in (('acquisition.stop', {}),):
            r = b.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        after = g.h.debug_snapshot()
        for k in ('phase', 'stage', 'powered', 'q', 'target', 'cycle_index', 'operation_id', 'film', 'bath'):
            self.assertEqual(before[k], after[k], k)
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'succeeded')
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')

    def test_jog_belongs_to_the_caller_and_the_evaluator_may_stop_it(self):
        g = Rig(sample=False)
        g.press('key_m')
        g.press('up', 2)
        g.press('left')                                                       # the arm diagram
        self.assertTrue(g.press('down', hold=2.0))                            # jog down: an operation of the caller
        op = g.h.operation_id
        self.assertEqual((g.val('cycle.stage'), g.h._owners.get(op)), ('manual', 'agent'))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        g.call(g.E, 'acquisition.stop')                                        # the evaluator may stop any operation
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')


class SystemErrorFault(unittest.TestCase):
    """Troubleshooting table, manual p.34-35: System error (the secondary safety switch was triggered) through the evaluator fault injection."""
    def test_system_error_stops_motion_and_needs_the_cause_removed_and_a_power_cycle(self):
        g = Rig()
        g.press('key_s')
        g.edit_digits('coating', '040.00')
        g.press('start')
        g.h.step(0.5)
        q = g.val('stage.position_m')
        r = g.call(g.E, 'fault.inject', code='system_error')
        self.assertEqual((r['phase'], g.val('cycle.phase'), g.val('safety.system_error')), ('failed', 'failed', True))
        g.h.step(1.0)
        self.assertEqual(g.val('stage.position_m'), q)                        # the motor stopped
        self.assertEqual((g.screen(), g.lines()[2].strip()), ('system_error', 'System error'))
        self.assertFalse(g.frame()['displays']['led_coating']['on'])
        for key in ('key_s', 'key_p', 'return_key', 'start', 'ok'):
            self.assertFalse(g.press(key), key)                               # keys do nothing
        self.assertEqual(g.screen(), 'system_error')
        for cap, args in (('acquisition.start', {}), ('stage.home', {}), ('stage.jog', dict(direction='up', duration_s=1.0, mode='manual')),
                          ('stage.move_to', dict(position_m=0.1, speed_m_s=0.01))):
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'PRECONDITION_FAILED', cap)
        self.assertNotIn('truth.secondary_switch', g.A.read(instance_id=INST)['observations'])
        self.assertTrue(g.val('truth.secondary_switch'))
        self.assertTrue(g.switch('off'))
        self.assertTrue(g.switch('on'))                                       # the cause is still there: the error shows again
        g.h.step(2.1)
        self.assertEqual((g.screen(), g.val('safety.system_error')), ('system_error', True))
        self.assertEqual(g.call(g.E, 'fault.clear')['cause'], 'system_error')  # the arm moved towards the centre of travel by hand
        self.assertEqual(g.screen(), 'system_error')                         # the message stays until the unit is switched off and on
        g.switch('off')
        g.switch('on')
        g.h.step(2.1)
        self.assertEqual((g.screen(), g.val('safety.system_error'), g.val('truth.secondary_switch')), ('home_confirm', False, False))

    def test_fault_capabilities_are_evaluator_only(self):
        g = Rig(sample=False)
        for cap in ('fault.inject', 'fault.clear'):
            self.assertEqual(rt.ALL_CAPS[cap][5], 'evaluator')
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='system_error')['error']['code'], 'ACCESS_DENIED')
        self.assertFalse(g.val('safety.system_error'))


class PanelRules(unittest.TestCase):
    def test_settings_warnings(self):
        g = Rig(sample=False)
        g.press('key_s')
        g.edit_digits('coating', '172.00')                                    # 172 + 5 mm gap > 175: corrected, warning
        self.assertEqual((g.screen(), g.lines()[2].strip()), ('warning', 'Insufficient travel dist.'))
        self.assertAlmostEqual(g.val('settings.state')['coating_length_m'], 0.170)
        g.press('ok')
        self.assertEqual(g.screen(), 'settings')
        g.edit_digits('coating', '050.00')
        g.press('down')
        g.edit_digits('position', '100.00')
        g.press('down')
        g.edit_digits('solution', '120.00')                                   # substrate below the solution
        self.assertEqual((g.screen(), g.lines()[3].strip()), ('warning', 'below solution height.'))
        g.press('ok')
        g.goto_item('position')
        g.edit_digits('position', '250.00')                                   # 130 + 50 + 5 > 175
        self.assertEqual(g.lines()[3].strip(), 'Please reduce Substrate')

    def test_default_page_program_and_brightness(self):
        g = Rig(sample=False)
        g.press('key_p')
        g.press('ok')
        g.press('up', 2)
        g.press('ok')                                                         # program 03
        self.assertEqual(g.val('programs.state')['program'], 3)
        g.press('key_s')
        g.goto_item('advanced')
        g.press('ok')
        g.goto_item('page')
        g.press('ok')
        g.press('right')                                                      # Default Page: Program
        g.press('ok')
        g.goto_item('prog')
        g.press('ok')
        g.press('right')                                                      # Default Prog: First
        g.press('ok')
        g.goto_item('bright')
        g.press('ok')
        g.press('down', 2)
        g.press('ok')
        self.assertEqual(g.val('settings.state')['brightness_pct'], 60)
        self.assertEqual(g.frame()['displays']['screen']['brightness'], 60)
        g.switch('off')
        g.switch('on')
        g.h.step(2.1)
        g.press('ok')
        g.h.step(0.05)
        self.assertEqual((g.screen(), g.val('programs.state')['program']), ('program_imm', 1))
        g.press('key_s')
        g.goto_item('advanced')
        g.press('ok')
        g.goto_item('about')
        g.press('ok')
        self.assertEqual((g.screen(), g.lines()[2].strip()), ('about', 'Ossila Dip Coater'))
        g.press('ok')
        self.assertEqual((g.screen(), g.item()), ('settings', 'about'))

    def test_free_motion(self):
        g = Rig(sample=False)
        g.press('key_m')
        g.goto_item('freemotion')
        g.press('ok')
        self.assertEqual((g.screen(), g.item()), ('free', 'pos'))
        g.press('down')
        g.edit_digits('free', '030.00')
        self.assertAlmostEqual(g.val('settings.state')['free_speed_m_s'], 0.03)
        g.press('left')
        g.press('down', hold=1.0)
        g.h.step(1.5)
        self.assertAlmostEqual(g.val('stage.position_m'), 0.145, places=6)
        g.press('ok')
        g.goto_item('back')
        g.press('ok')
        self.assertEqual((g.screen(), g.item()), ('manual', 'freemotion'))

    def test_stop_and_crash_messages(self):
        g = Rig()
        g.press('key_s')
        g.edit_digits('coating', '040.00')
        g.press('start')
        g.h.step(0.5)
        self.assertFalse(g.press('key_s'))                                    # keys do nothing while a program runs
        self.assertTrue(g.press('stop'))
        self.assertEqual((g.screen(), g.lines()[2].strip()), ('done', 'Operation Stopped!'))
        g.press('ok')
        g.press('start')
        g.h.step(0.5)
        g.call(g.E, 'safety.set_crash', triggered=True)                       # crash switch released
        g.h.step(0.01)
        self.assertEqual((g.screen(), g.lines()[2].strip(), g.lines()[3].strip()), ('done', 'Crash Detected!', 'Operation is stopped.'))
        self.assertFalse(g.press('ok'))                                       # switch still released: the message stays
        self.assertEqual(g.screen(), 'done')
        g.call(g.E, 'safety.set_crash', triggered=False)
        self.assertTrue(g.press('ok'))
        self.assertEqual((g.screen(), g.val('safety.fault')), ('program_imm', False))

    def test_refusals_and_power(self):
        g = Rig(sample=False)
        self.assertFalse(g.press('start'))                                    # no bath / substrate: cannot start
        self.assertEqual(g.frame()['ui']['message']['text'], 'Not possible now')
        g.switch('off')
        self.assertFalse(g.press('key_s'))
        self.assertEqual(int(g.h.panel.render('screen').max()), 0)
        self.assertFalse(g.frame()['displays']['led_on']['on'])

    def test_panel_and_direct_commands_give_the_same_state(self):
        p, d = Rig(), Rig()
        p.press('key_s')
        p.edit_digits('coating', '040.00')
        p.press('key_p')
        p.goto_item('dip')
        p.edit_digits('dip', '050.00')
        p.press('start')
        d.call(d.A, 'settings.configure', coating_length_m=0.04)
        d.call(d.A, 'program.configure', immersion_speed_m_s=0.05)
        d.call(d.A, 'program.select', program=1)
        d.call(d.A, 'acquisition.start')
        for g in (p, d):
            g.h.step(6.0)
        self.assertEqual(p.h.read('evaluator'), d.h.read('evaluator'))

    def test_method_configure_is_shown_on_the_panel(self):
        g = Rig(sample=False)
        g.call(g.A, 'method.configure', **rt.METHOD_EXAMPLE)
        g.press('key_p')
        self.assertIn('0002', g.lines()[2])
        self.assertEqual(g.val('settings.state')['coating_length_m'], rt.METHOD_EXAMPLE['coating_length_m'])

    def test_only_agent_capabilities_behind_the_panel(self):
        caps = rt._panel_ui.spec_capabilities(rt.PANEL_SPEC)
        self.assertEqual(sorted(caps), ['acquisition.start', 'acquisition.stop', 'fault.acknowledge', 'power.set', 'program.configure', 'program.select',
                                        'settings.configure', 'stage.home', 'stage.jog', 'stage.move_to'])
        self.assertTrue(all(rt.ALL_CAPS[k][5] == 'agent' for k in caps))
        g = Rig(sample=False, boot=False)
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_render_determinism_and_capture(self):
        g = Rig(sample=False)
        a, b = g.h.panel.render('screen'), g.h.panel.render('screen')
        self.assertEqual(a.shape, (240, 320, 3))
        self.assertTrue((a == b).all())
        r = g.call(g.A, 'panel.capture', display_id='screen')
        self.assertEqual(hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest(), r['image']['sha256'])
        c = copy.deepcopy(g.h)
        self.assertTrue((c.panel.render('screen') == a).all())
        before = g.h.debug_snapshot()
        g.h.read()
        g.h.panel.frame()
        self.assertEqual(g.h.debug_snapshot(), before)

    def test_evidence_renders(self):
        OUT.mkdir(parents=True, exist_ok=True)
        g = Rig()
        shots = dict(home=g.h.panel.save_png('screen', OUT / 'home.png'))
        g.press('key_p')
        g.goto_item('dwell')
        g.press('ok')
        g.press('right', 5)
        shots['menu'] = g.h.panel.save_png('screen', OUT / 'menu.png')
        g.press('ok')
        g.press('key_m')
        shots['manual'] = g.h.panel.save_png('screen', OUT / 'manual.png')
        g.press('key_s')
        g.edit_digits('coating', '173.00')
        shots['error'] = g.h.panel.save_png('screen', OUT / 'error.png')
        g.press('ok')
        g.edit_digits('coating', '040.00')
        g.press('start')
        g.h.step(2.0)
        shots['running'] = g.h.panel.save_png('screen', OUT / 'running.png')
        g.call(g.E, 'fault.inject', code='system_error')
        shots['fault'] = g.h.panel.save_png('screen', OUT / 'fault.png')
        for v in shots.values():
            self.assertEqual(v['sha256'], hashlib.sha256(Path(v['path']).read_bytes()).hexdigest())
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()}, indent=1) + '\n')

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['ui_class'], 'text_menu')
        for k in list(rt.PANEL_CAPS) + list(rt.PROGRAM_CAPS):
            self.assertIn(k, c['capabilities'])
            for cid in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][cid]['capability_ids'])
        for sid in ('settings.state', 'programs.state', 'safety.system_error', 'truth.secondary_switch'):
            self.assertIn(sid, c['state_slots'])
        for k in rt.FAULT_CAPS:
            self.assertEqual(c['capabilities'][k]['visibility'], 'evaluator')
        self.assertIn('panel_caller_rules', [f['id'] for f in c['extensions']['chemistry_lab.fixes']])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])


if __name__ == '__main__':
    unittest.main()
