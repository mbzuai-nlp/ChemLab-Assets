"""front-panel tests for CHEM-055 ossila_doctorblade (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_ossila_doctorblade.py -q

Every gesture goes through the session API (panel.press / panel.switch); the host steps the clock, fixes the substrate, sets the blade gap with the
micrometers and fills the reservoir.  Each check looks at the runtime state and at the screen (structured frame: screen, menu item, editor;
rendered pixels).  Themes: the six key procedures of the menu survey (start-up and homing, stop mode SOFT, jog, a coating program, the blade gap
on the micrometer gauges, emergency stop and reset), the home reference / offset and coating towards the back, settings and Info, program and
step keys, refusals and power, time-outs, panel = direct capability, render determinism, evidence PNGs and contract entries."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import ossila_doctorblade as rt  # noqa: E402

INST = 'doctorblade_01'
OUT = ROOT / 'outputs/panel_ui/ossila_doctorblade'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
FIELDS = {it['id']: it for m in rt.PANEL_SPEC['menus'].values() for it in m['items']}
SURVEY_TASKS = ['start up and home', 'set stop mode to SOFT', 'jog the stage', 'write a coating program', 'set the blade gap', 'emergency stop and reset']
DONE_TASKS = set()


class Rig:
    def __init__(self, sample=True, charge=True, boot=True):
        self.h = rt.OssilaDoctorbladeRuntime(INST)
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
            self.call(self.E, 'substrate.configure', **rt.SAMPLE_EXAMPLE)
            self.call(self.A, 'gap.set', left_m=0.0001, right_m=0.0001)
            self.h.step(0.1)
        if charge:
            self.call(self.E, 'charge.configure', **rt.CHARGE_EXAMPLE)

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

    def switch(self, pos, input_id='power_switch'):
        return self.call(self.A, 'panel.switch', input_id=input_id, position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self, display='screen'):
        return [x.rstrip() for x in self.frame()['displays'][display]['lines']]

    def item(self):
        m = self.frame()['ui']['menu']
        return m['item'] if m else None

    def message(self):
        m = self.frame()['ui']['message']
        return m and m['text']

    def goto_item(self, iid):
        self.press('up', 8)
        for _ in range(8):
            if self.item() == iid:
                return
            self.press('down')
        raise AssertionError((iid, self.item()))

    def edit_digits(self, iid, target):
        """OK on the line, then each digit with up / down (the shorter way round) and right, then OK (manual 9.2.3 2., 9.2.4 3.)."""
        self.goto_item(iid)
        assert self.press('ok')
        f = FIELDS[iid]
        want = [int(c) for c in target if c.isdigit()]
        for i, t in enumerate(want):
            cur = [int(c) for c in self.h.panel._field_text(f, self.frame()['ui']['editor']['buffer'][iid]) if c.isdigit()][i]
            d = (t - cur) % 10
            self.press('up', d) if d <= 5 else self.press('down', 10 - d)
            if i < len(want) - 1:
                self.press('right')
        assert self.h.panel._field_text(f, self.frame()['ui']['editor']['buffer'][iid]) == target, (iid, target)
        return self.press('ok')

    def line_of(self, label):
        return next(x for x in self.lines() if label in x)


class SurveyProcedures(unittest.TestCase):
    def test_start_up_and_home(self):
        g = Rig(sample=False, charge=False, boot=False)
        self.assertEqual(g.screen(), 'off')
        self.assertTrue(g.switch('on'))                                       # 1 switch to 1: bootup screen
        self.assertEqual(g.screen(), 'boot')
        self.assertIn('Automatic Film Applicator', g.lines()[6])
        g.h.step(1.0)
        self.assertEqual(g.screen(), 'boot')
        g.h.step(1.1)                                                         # time-out: Reset System to HOME
        self.assertEqual((g.screen(), g.lines()[4].strip(), g.lines()[7].strip()), ('reset', 'Reset System to', 'Press OK to confirm'))
        g.call(g.A, 'gap.set', left_m=0.001, right_m=0.001)                  # the stage away from home first (host moves it)
        g.h.homed = True
        g.call(g.A, 'stage.move_to', position_m=0.05, speed_m_s=0.05)
        g.h.step(1.1)
        g.h.homed = False
        self.assertTrue(g.press('ok'))                                        # 2 OK: homing to the furthest back point
        self.assertEqual((g.screen(), g.lines()[5].strip(), g.val('cycle.stage')), ('homing', 'Homing......', 'homing'))
        g.h.step(1.0)
        self.assertEqual(g.screen(), 'homing')
        g.h.step(1.6)                                                         # 3-4 at home: the Program Mode page
        self.assertEqual((g.screen(), g.val('stage.homed'), g.val('stage.position_m'), g.lines()[0]), ('program', True, 0.0, 'Program Mode'))
        self.assertEqual(g.lines()[1:6], ['>Program 01', ' Step : 01   of 01', ' Coating length: 290.00 mm', ' Coating speed: 100.00 mm/s',
                                          ' Applicator del: 000.00 s'])
        DONE_TASKS.add('start up and home')

    def test_set_stop_mode_soft(self):
        g = Rig()
        self.assertTrue(g.press('key_s'))                                     # 1 S
        self.assertEqual((g.lines()[0], g.item()), ('Settings', 'bright'))
        g.press('down', 3)                                                    # 2 down to Stop mode
        self.assertEqual(g.item(), 'stopmode')
        g.press('ok')                                                         # 3 OK (edit), up / down to SOFT, OK
        g.press('up')
        self.assertEqual(g.lines()[4], '>Stop mode : SOFT')
        self.assertTrue(g.frame()['displays']['screen']['blink'][4])
        self.assertEqual(g.val('settings.state')['stop_mode'], 'hard')
        g.press('ok')
        self.assertEqual(g.val('settings.state')['stop_mode'], 'soft')
        g.call(g.A, 'method.configure', distance_m=0.26, speed_m_s=0.1)
        g.press('start')
        g.h.step(1.0)
        self.assertTrue(g.press('stop'))                                      # SOFT: decelerates over a short period
        self.assertEqual((g.val('cycle.phase'), g.val('cycle.stage')), ('running', 'stopping'))
        g.h.step(1.0)
        self.assertEqual((g.val('cycle.phase'), g.val('cycle.stage')), ('cancelled', 'stopped'))
        self.assertAlmostEqual(g.val('stage.position_m'), 0.1 + 0.1 * rt.SOFT_STOP_S / 2, places=9)
        h = Rig()
        h.call(h.A, 'method.configure', distance_m=0.26, speed_m_s=0.1)
        h.press('start')
        h.h.step(1.0)
        h.press('stop')                                                       # HARD (default): stops at once
        self.assertEqual((h.val('cycle.phase'), h.val('stage.position_m')), ('cancelled', 0.1))
        DONE_TASKS.add('set stop mode to SOFT')

    def test_jog_the_stage(self):
        g = Rig(charge=False)
        self.assertTrue(g.press('key_m'))                                     # 1 M
        self.assertEqual((g.lines()[0], g.lines()[1].strip(), g.item()), ('Manual Mode', 'Applicator', 'mspeed'))
        self.assertTrue(g.edit_digits('mspeed', '050.000'))                    # 2 speed: OK, left / right digit, up / down, OK
        self.assertAlmostEqual(g.val('settings.state')['manual_speed_m_s'], 0.05)
        self.assertEqual(g.lines()[2], '>Speed    : 050.000 mm/s')
        self.assertTrue(g.press('right', hold=1.0))                           # 3 right (not in edit mode): the stage moves
        g.h.step(2.0)
        self.assertAlmostEqual(g.val('stage.position_m'), 0.05, places=9)
        self.assertEqual(g.lines()[3], ' Position : 050.000 mm')
        g.press('left', hold=0.4)
        g.h.step(1.0)
        self.assertAlmostEqual(g.val('stage.position_m'), 0.03, places=9)
        self.assertTrue(g.press('return_key', hold=1.5))                      # hold Return: home offset = current location
        self.assertAlmostEqual(g.val('settings.state')['home_offset_m'], 0.03)
        g.press('key_s')
        self.assertEqual(g.lines()[2], ' Home offset : 30.00')
        g.press('key_m')
        g.press('right', hold=1.0)
        g.h.step(2.0)
        self.assertTrue(g.press('return_key'))                                # Return: back to the home position (the offset)
        g.h.step(5.0)
        self.assertAlmostEqual(g.val('stage.position_m'), 0.03, places=9)
        DONE_TASKS.add('jog the stage')

    def test_write_a_coating_program(self):
        g = Rig()
        self.assertTrue(g.press('key_p'))                                     # 1 P
        self.assertTrue(g.press('right'))                                     # program number with right, no edit mode
        self.assertEqual((g.lines()[1], g.val('programs.state')['program']), ('>Program 02', 2))
        g.goto_item('steps')                                                  # 2 steps line: OK, up / down, OK
        g.press('ok')
        g.press('up', 2)
        g.press('ok')
        self.assertEqual((g.val('programs.state')['count'], g.lines()[2]), (3, '>Step : 01   of 03'))
        g.edit_digits('length', '050.00')                                     # 3 length and speed of step 1
        g.edit_digits('speed', '020.00')
        g.goto_item('steps')
        g.press('right')                                                      # step 2
        self.assertEqual(g.lines()[2], '>Step : 02   of 03')
        g.edit_digits('delay', '002.00')                                      # 4 a delay step
        g.goto_item('steps')
        g.press('right')
        g.edit_digits('length', '100.00')
        g.edit_digits('speed', '050.00')
        steps = g.val('programs.state')['steps']
        self.assertEqual([(s['length_m'], s['speed_m_s'], s['delay_s']) for s in steps], [(0.05, 0.02, 0.0), (0.0, 0.0, 2.0), (0.1, 0.05, 0.0)])
        self.assertTrue(g.val('programs.state')['runnable'])
        self.assertFalse(g.frame()['displays']['led_coating']['on'])
        self.assertTrue(g.press('start'))                                     # 5 Start
        self.assertTrue(g.frame()['displays']['led_coating']['on'])
        g.h.step(2.0)
        self.assertEqual((g.val('cycle.stage'), round(g.val('stage.position_m'), 9)), ('coating', 0.04))
        g.h.step(1.5)
        self.assertEqual((g.val('cycle.stage'), round(g.val('stage.position_m'), 9), g.val('stage.speed_m_s')), ('delay', 0.05, 0.0))
        self.assertTrue(g.frame()['displays']['led_coating']['on'])
        g.h.step(2.5)                                                         # 2 s delay, then step 3 at 50 mm/s
        self.assertEqual((g.val('cycle.stage'), round(g.val('stage.position_m'), 9)), ('coating', 0.125))
        g.h.step(2.0)
        self.assertEqual((g.val('cycle.phase'), g.val('stage.position_m')), ('succeeded', 0.15))
        self.assertFalse(g.frame()['displays']['led_coating']['on'])
        film = g.call(g.E, 'product.read')
        self.assertGreater(film['film_solute_kg'], 0)
        self.assertAlmostEqual(film['initial_solute_kg'], film['bead_solute_kg'] + film['film_solute_kg'] + film['waste_solute_kg'], places=15)
        DONE_TASKS.add('write a coating program')

    def test_set_the_blade_gap_on_the_gauges(self):
        g = Rig(charge=False)
        self.assertEqual(g.lines('gauge_l'), [''])                            # off
        self.assertTrue(g.press('gauge_left_on'))                             # 'on'
        self.assertTrue(g.press('gauge_right_on'))
        self.assertEqual(g.lines('gauge_l'), ['  0.100 mm'])
        g.call(g.A, 'gap.set', left_m=0.000004, right_m=0.0)                  # lower the blade to (almost) zero gap
        g.h.step(0.1)
        self.assertEqual(g.lines('gauge_l'), ['  0.004 mm'])
        g.press('gauge_left_origin')                                          # a short press does nothing
        self.assertEqual(g.val('gauges.state')['left']['zero_m'], 0.0)
        g.press('gauge_left_origin', hold=1.5)                                # hold 'origin': zero
        g.press('gauge_right_origin', hold=1.5)
        self.assertEqual(g.lines('gauge_l'), ['  0.000 mm'])
        g.call(g.A, 'gap.set', left_m=0.000154, right_m=0.00015)              # turn both micrometers to the coating gap
        g.h.step(0.1)
        self.assertEqual((g.lines('gauge_l'), g.lines('gauge_r')), (['  0.150 mm'], ['  0.150 mm']))
        self.assertEqual(g.val('gauges.state')['left']['reading_m'], 0.00015)
        self.assertTrue(g.press('gauge_left_unit'))                           # mm / in
        self.assertEqual(g.lines('gauge_l'), [' 0.0059 in'])
        g.switch('off')                                                       # the gauges have their own battery
        self.assertTrue(g.press('gauge_left_unit'))
        self.assertEqual(g.lines('gauge_l'), ['  0.150 mm'])
        g.press('gauge_left_on')
        self.assertEqual(g.lines('gauge_l'), [''])
        self.assertFalse(g.press('gauge_left_unit'))                          # a switched-off gauge ignores its keys
        DONE_TASKS.add('set the blade gap')

    def test_emergency_stop_and_reset(self):
        g = Rig()
        g.press('start')
        g.h.step(1.0)
        self.assertTrue(g.switch('pressed', 'estop'))                         # 1 power cut
        self.assertEqual((g.screen(), g.val('cycle.phase'), g.val('power.enabled')), ('off', 'failed', False))
        self.assertEqual(int(g.h.panel.render('screen').max()), 0)
        self.assertTrue(g.switch('off'))
        self.assertFalse(g.switch('on'))                                      # still pressed: no restart
        self.assertEqual(g.screen(), 'off')
        self.assertTrue(g.switch('released', 'estop'))                        # 2 rotate clockwise to release
        self.assertEqual(g.screen(), 'off')
        g.switch('off')
        self.assertTrue(g.switch('on'))                                       # 3 off and on again: bootup
        self.assertEqual(g.screen(), 'boot')
        g.h.step(2.1)
        self.assertFalse(g.press('ok'))                                       # homing refused while the reservoir and film are on the stage
        self.assertEqual((g.screen(), g.message()), ('reset', 'Not possible now'))
        g.h.step(2.1)
        self.assertIsNone(g.message())                                        # the message clears after 2 s
        g.call(g.E, 'product.collect', recipient_id='slide')
        g.call(g.E, 'reservoir.empty')
        self.assertTrue(g.press('ok'))
        g.h.step(5.0)
        self.assertEqual((g.screen(), g.val('stage.homed'), g.val('stage.position_m')), ('program', True, 0.0))
        DONE_TASKS.add('emergency stop and reset')


class CallerRules(unittest.TestCase):
    """Keys pressed through a session act as that session's caller (1.3.0): a run, homing or jog started with a key belongs to the caller, and another
    agent can stop or switch off the operation neither through the keys nor directly; the emergency stop works for everyone."""
    def test_hard_power_switch_is_never_refused(self):
        """Lead rule 10-19 (1.3.1): the rear power switch is a physical action; another agent switching it off stops the run as a power loss, and
        power.set / power.toggle, which model that switch, follow it."""
        g = Rig()
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
        self.assertEqual(g.message(), 'Access denied')
        for cap, args in (('acquisition.stop', {}),):
            r = b.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        after = g.h.debug_snapshot()
        for k in ('phase', 'stage', 'powered', 'power_switch', 'q', 'target', 'operation_id', 'film', 'bead'):
            self.assertEqual(before[k], after[k], k)
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'succeeded')
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')

    def test_emergency_stop_and_evaluator_stop_any_run(self):
        g = Rig()
        g.press('start')
        op = g.h.operation_id
        g.h.register_principal('agent2', 'agent')
        b = g.h.session('agent2')
        r = b.invoke(instance_id=INST, capability_id='panel.switch', arguments=dict(input_id='estop', position='pressed'), request_id='b1')
        self.assertTrue(r['result']['accepted'])                               # safety: the emergency stop is open to everyone
        self.assertEqual((g.A.poll(op)['status'], g.val('power.enabled')), ('failed', False))
        g = Rig()
        g.press('start')
        op = g.h.operation_id
        g.call(g.E, 'acquisition.stop')
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')


class CrashWarningFault(unittest.TestCase):
    """Troubleshooting table, manual p.24: Crash Warning (the secondary safety switch) through the evaluator fault injection."""
    def test_accidental_trigger_stops_motion_and_clears_with_a_power_cycle(self):
        g = Rig()
        g.press('start')
        g.h.step(0.5)
        q = g.val('stage.position_m')
        r = g.call(g.E, 'fault.inject', code='crash_warning', cause='triggered')
        self.assertEqual((r['phase'], g.val('cycle.phase'), g.val('safety.crash_warning')), ('failed', 'failed', True))
        g.h.step(1.0)
        self.assertEqual(g.val('stage.position_m'), q)                        # the stage stopped
        self.assertEqual((g.screen(), g.lines()[3].strip()), ('crash_warning', 'Crash Warning'))
        self.assertFalse(g.frame()['displays']['led_coating']['on'])
        for key in ('key_s', 'key_p', 'return_key', 'start', 'ok'):
            self.assertFalse(g.press(key), key)                               # the coater keys do nothing
        self.assertTrue(g.press('gauge_left_on'))                             # the gauges have their own battery
        for cap, args in (('acquisition.start', {}), ('stage.home', {}), ('stage.jog', dict(direction='forward', duration_s=1.0)),
                          ('stage.move_to', dict(position_m=0.1, speed_m_s=0.01)), ('stage.set_home_offset', {})):
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'PRECONDITION_FAILED', cap)
        self.assertTrue(g.switch('off'))
        self.assertTrue(g.switch('on'))                                       # switched off and on: the warning has gone
        self.assertEqual((g.screen(), g.val('safety.crash_warning')), ('boot', False))

    def test_faulty_switch_shows_again_until_repaired(self):
        g = Rig(sample=False, charge=False)
        g.call(g.E, 'fault.inject', code='crash_warning', cause='switch_fault')
        self.assertEqual(g.val('truth.secondary_switch'), 'switch_fault')
        self.assertNotIn('truth.secondary_switch', g.A.read(instance_id=INST)['observations'])
        g.switch('off')
        g.switch('on')
        g.h.step(2.1)
        self.assertEqual((g.screen(), g.val('safety.crash_warning')), ('crash_warning', True))
        self.assertEqual(g.call(g.E, 'fault.clear')['cause'], 'switch_fault')   # repaired
        self.assertEqual(g.screen(), 'crash_warning')                        # still shown until switched off and on
        g.switch('off')
        g.switch('on')
        g.h.step(2.1)
        self.assertEqual((g.screen(), g.val('safety.crash_warning')), ('reset', False))
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='crash_warning', cause='triggered')['error']['code'], 'ACCESS_DENIED')


class PanelRules(unittest.TestCase):
    def test_front_home_reference_and_coating_towards_the_back(self):
        g = Rig(charge=False)
        g.press('key_m')
        g.press('right', hold=1.0)
        g.h.step(2.0)
        g.press('return_key', hold=1.5)
        self.assertAlmostEqual(g.val('settings.state')['home_offset_m'], 0.02)
        g.press('key_s')
        g.goto_item('homeref')
        g.press('ok')
        g.press('up')
        g.press('ok')                                                         # FRONT: the offset is reset
        self.assertEqual((g.val('settings.state')['home_reference'], g.val('settings.state')['home_offset_m']), ('front', 0.0))
        self.assertEqual(g.line_of('Home ref.'), '>Home ref. : FRONT')
        g.press('return_key')
        g.h.step(20.0)
        self.assertAlmostEqual(g.val('stage.position_m'), rt.TRAVEL, places=9)
        g.press('key_m')
        self.assertEqual(g.lines()[3], ' Position : 000.000 mm')             # measured from the home end
        g.call(g.E, 'charge.configure', **rt.CHARGE_EXAMPLE)
        g.call(g.A, 'program.set_step', step=1, length_m=0.2, speed_m_s=0.05)
        self.assertTrue(g.press('start'))
        g.h.step(1.0)
        self.assertAlmostEqual(g.val('stage.speed_m_s'), -0.05)
        g.h.step(4.0)
        self.assertEqual(g.val('cycle.phase'), 'succeeded')
        self.assertAlmostEqual(g.val('stage.position_m'), 0.09, places=9)
        p = g.call(g.E, 'product.read')
        self.assertGreater(p['film_solute_kg'], 0)
        prof = p['film_profile_m']
        self.assertGreater(prof[-1], 0)                                       # the strips at the front end are coated first
        self.assertAlmostEqual(p['initial_solute_kg'], p['bead_solute_kg'] + p['film_solute_kg'] + p['waste_solute_kg'], places=15)

    def test_settings_brightness_temperature_and_info(self):
        g = Rig(charge=False)
        g.press('key_s')
        self.assertEqual(g.lines()[1:7], ['>LCD brightness : 100 %', ' Home offset : 00.00', ' Home ref. : BACK', ' Stop mode : HARD',
                                          ' System temp : 25.0 C', ' Info'])
        full = int(g.h.panel.render('screen').max())
        g.press('ok')
        g.press('down', 2)                                                    # 5 % steps
        self.assertEqual(g.lines()[1], '>LCD brightness :  90 %')
        self.assertEqual(g.val('settings.state')['brightness_pct'], 100)
        g.press('ok')
        self.assertEqual(g.val('settings.state')['brightness_pct'], 90)
        self.assertLess(int(g.h.panel.render('screen').max()), full)
        g.goto_item('info')
        g.press('ok')
        self.assertEqual((g.screen(), g.lines()[4].strip(), g.lines()[11].strip()), ('info', 'Firmware ver.: 1.0.0', 'Press OK to Continue'))
        g.press('ok')
        self.assertEqual((g.screen(), g.item()), ('settings', 'info'))

    def test_program_and_step_keys(self):
        g = Rig(charge=False)
        g.press('key_p')
        g.press('left')                                                       # stops at 1
        self.assertEqual(g.val('programs.state')['program'], 1)
        g.press('right', 25)                                                  # stops at 20
        self.assertEqual(g.lines()[1], '>Program 20')
        g.goto_item('steps')
        g.press('right')                                                      # only one step
        self.assertEqual(g.lines()[2], '>Step : 01   of 01')
        g.press('ok')
        g.press('up', 4)
        g.press('ok')
        g.press('right', 4)
        self.assertEqual(g.lines()[2], '>Step : 05   of 05')
        g.press('ok')
        g.press('down', 2)
        g.press('ok')                                                         # fewer steps: the shown step follows
        self.assertEqual(g.lines()[2], '>Step : 03   of 03')
        self.assertFalse(g.val('programs.state')['runnable'])                 # steps 2 and 3 not programmed
        self.assertFalse(g.press('start'))
        self.assertEqual(g.message(), 'Not possible now')
        g.goto_item('speed')
        g.press('ok')
        g.press('right', 4)
        g.press('up')                                                         # 000.01 mm/s: below 0.05
        self.assertFalse(g.press('ok'))
        self.assertEqual(g.message(), 'Invalid value')

    def test_refusals_and_power(self):
        g = Rig(charge=False)
        self.assertFalse(g.press('start'))                                    # empty reservoir: cannot start
        self.assertEqual(g.message(), 'Not possible now')
        g.call(g.E, 'charge.configure', **rt.CHARGE_EXAMPLE)
        g.call(g.A, 'program.set_step', step=1, length_m=0.29, speed_m_s=0.01)
        g.press('start')
        g.press('key_p')
        self.assertFalse(g.edit_digits('speed', '020.00'))                     # the program runs
        self.assertEqual(g.message(), 'Program running')
        self.assertFalse(g.press('key_m') and g.press('right'))              # no jog during a run
        g.press('stop')
        g.switch('off')
        self.assertFalse(g.press('key_s'))
        self.assertEqual(int(g.h.panel.render('screen').max()), 0)
        self.assertFalse(g.frame()['displays']['led_coating']['on'])
        self.assertEqual(g.lines(), [''] * len(g.lines()))
        g.switch('on')
        self.assertEqual(g.screen(), 'boot')
        g.press('key_p')                                                      # the bootup screen ignores keys
        g.h.step(2.1)
        self.assertTrue(g.press('key_p'))                                     # the mode keys skip homing
        self.assertEqual((g.screen(), g.val('stage.homed')), ('program', False))

    def test_panel_and_direct_commands_give_the_same_state(self):
        p, d = Rig(), Rig()
        p.press('key_p')
        p.edit_digits('length', '200.00')
        p.edit_digits('speed', '050.00')
        p.press('key_s')
        p.goto_item('stopmode')
        p.press('ok')
        p.press('up')
        p.press('ok')
        p.press('start')
        d.call(d.A, 'program.set_step', step=1, length_m=0.2, speed_m_s=0.05)
        d.call(d.A, 'settings.configure', stop_mode='soft')
        d.call(d.A, 'program.select', program=1)
        d.call(d.A, 'acquisition.start')
        for g in (p, d):
            g.h.step(2.0)
            g.call(g.A, 'acquisition.stop')
            g.h.step(1.0)
        self.assertEqual(p.h.read('evaluator'), d.h.read('evaluator'))

    def test_method_configure_is_shown_on_the_panel(self):
        g = Rig(charge=False)
        g.call(g.A, 'method.configure', **rt.METHOD_EXAMPLE)
        g.press('key_p')
        self.assertEqual(g.lines()[3:5], [' Coating length: 260.00 mm', ' Coating speed: 020.00 mm/s'])
        self.assertTrue(g.val('method.configured'))

    def test_only_agent_capabilities_behind_the_panel(self):
        caps = rt._panel_ui.spec_capabilities(rt.PANEL_SPEC)
        self.assertEqual(sorted(caps), ['acquisition.start', 'acquisition.stop', 'emergency.release', 'emergency.stop', 'gauge.button', 'power.set',
                                        'program.select', 'program.set_step', 'program.set_steps_count', 'settings.configure', 'stage.home', 'stage.jog',
                                        'stage.set_home_offset'])
        self.assertTrue(all(rt.ALL_CAPS[k][5] == 'agent' for k in caps))
        g = Rig(sample=False, charge=False, boot=False)
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_render_determinism_and_capture(self):
        g = Rig(charge=False)
        a, b = g.h.panel.render('screen'), g.h.panel.render('screen')
        self.assertEqual(a.shape, (270, 360, 3))
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
        g = Rig(charge=False)
        g.press('gauge_left_on')
        shots = dict(home=g.h.panel.save_png('screen', OUT / 'home.png'), gauge=g.h.panel.save_png('gauge_l', OUT / 'gauge.png'))
        g.press('key_s')
        shots['menu'] = g.h.panel.save_png('screen', OUT / 'menu.png')
        g.press('key_m')
        g.press('ok')
        g.press('right')
        g.h.step(0.5)                                                         # the edited digit in its dark blink phase
        shots['edit'] = g.h.panel.save_png('screen', OUT / 'edit.png')
        g.press('ok')
        g.press('right', hold=1.0)
        g.h.step(2.0)
        shots['manual'] = g.h.panel.save_png('screen', OUT / 'manual.png')
        g.press('key_p')
        g.press('start')
        shots['error'] = g.h.panel.save_png('screen', OUT / 'error.png')
        g.call(g.E, 'fault.inject', code='crash_warning', cause='triggered')
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
        for sid in ('settings.state', 'programs.state', 'gauges.state', 'safety.crash_warning', 'truth.secondary_switch'):
            self.assertIn(sid, c['state_slots'])
        for k in rt.FAULT_CAPS:
            self.assertEqual(c['capabilities'][k]['visibility'], 'evaluator')
        self.assertEqual(sorted(f['id'] for f in c['extensions']['chemistry_lab.fixes']), ['crash_warning_fault', 'older_operations_pollable', 'panel_caller_rules',
                                                                                             'schema_grids', 'sweep_hard_power_switch'])
        for cid in ('led_coating', 'gauge_keys_left', 'gauge_keys_right'):
            self.assertEqual(c['components'][cid]['geometry_status'], 'absent')
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])


if __name__ == '__main__':
    unittest.main()
