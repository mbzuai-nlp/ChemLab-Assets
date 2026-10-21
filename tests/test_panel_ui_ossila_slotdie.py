"""front-panel tests for CHEM-056 ossila_slotdie (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_ossila_slotdie.py -q

Every gesture goes through the session API (panel.press / panel.switch); the host steps the clock, places the substrate, sets the head gap with the
micrometers and installs the filled syringe.  Each check looks at the runtime state and at the screen (structured frame: screen, menu item, editor;
rendered pixels).  Themes: the seven key procedures of the menu survey (power on and home, hotplate on at a set temperature, temperature offset,
syringe size, manual stage jog and syringe priming, a coating program, run and stop), the warnings (length, rate, travel, speed not set, diameter
not set, Motor Stalled), Stage Home, the height gauges, emergency stop, refusals and power, time-outs, panel = direct capability, render determinism,
evidence PNGs and contract entries."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import ossila_slotdie as rt  # noqa: E402

INST = 'slotdie_01'
OUT = ROOT / 'outputs/panel_ui/ossila_slotdie'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
FIELDS = {it['id']: it for m in rt.PANEL_SPEC['menus'].values() for it in m['items']}
SURVEY_TASKS = ['power on and home the stage', 'switch the hotplate on and set 100 C', 'apply a temperature offset', 'select syringe size',
                'jog stage and prime syringe manually', 'program a coating step', 'run and stop a program']
DONE_TASKS = set()


class Rig:
    def __init__(self, sample=True, ink=True, prime=True, boot=True):
        self.h = rt.OssilaSlotdieRuntime(INST)
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
        if ink:
            self.call(self.E, 'ink.configure', **rt.INK_EXAMPLE)
        if ink and prime:
            self.call(self.A, 'pump.dispense', volume_m3=1e-7, flow_m3_s=2e-8)
            self.h.step(6.0)

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
        """OK on the line, then each position (a sign with up / down, digits the shorter way round) and right, then OK (manual 8.2.2-8.2.4)."""
        self.goto_item(iid)
        assert self.press('ok')
        f = FIELDS[iid]
        text = lambda: self.h.panel._field_text(f, self.frame()['ui']['editor']['buffer'][iid])  # noqa: E731
        want = [c for c in target if c.isdigit() or c in '+-']
        for i, t in enumerate(want):
            cur = [c for c in text() if c.isdigit() or c in '+-'][i]
            if t in '+-':
                if cur != t:
                    self.press('up')
            else:
                d = int(t) - int(cur)                                         # straight up / down (a digit leaving the range does not move)
                self.press('up', d) if d > 0 else self.press('down', -d)
            if i < len(want) - 1:
                self.press('right')
        assert text() == target, (iid, target, text())
        return self.press('ok')

    def line_of(self, label):
        return next(x for x in self.lines() if label in x)

    def program(self, *steps):
        """Steps (length mm, speed mm/s, rate ul/s, delay s) of program 1 written directly (setting up other tests)."""
        self.call(self.A, 'program.set_steps_count', count=len(steps))
        for i, (l, v, r, d) in enumerate(steps, 1):
            self.call(self.A, 'program.set_step', step=i, length_m=l / 1000, speed_m_s=v / 1000, rate_m3_s=r * 1e-9, delay_s=d)


class SurveyProcedures(unittest.TestCase):
    def test_power_on_and_home_the_stage(self):
        g = Rig(sample=False, ink=False, boot=False)
        self.assertEqual(g.screen(), 'off')
        self.assertTrue(g.switch('on'))                                       # 1 switch to 1: bootup screen
        self.assertEqual((g.screen(), g.lines()[6].strip()), ('boot', 'Slot Die Coater'))
        g.h.step(2.1)                                                         # time-out: Reset System to HOME
        self.assertEqual((g.screen(), g.lines()[4].strip(), g.lines()[7].strip()), ('reset', 'Reset System to', 'Press OK to confirm'))
        g.h.homed = True
        g.call(g.A, 'stage.move_to', position_m=0.02, speed_m_s=0.02)        # the stage away from home first
        g.h.step(1.1)
        g.h.homed = False
        self.assertTrue(g.press('ok'))                                        # 2 OK: homing to the furthest back point
        self.assertEqual((g.screen(), g.lines()[5].strip()), ('homing', 'Homing......'))
        g.h.step(1.0)
        self.assertEqual(g.screen(), 'homing')
        g.h.step(1.1)                                                         # 3-4 at home: the Program Mode page
        self.assertEqual((g.screen(), g.val('stage.homed'), g.val('stage.position_m')), ('program', True, 0.0))
        self.assertEqual(g.lines()[:7], ['Program Mode            5 ml', '>Program 01', ' Step : 01 of 01', ' Coating length: 000.000 mm',
                                         ' Coating speed: 000.000 mm/s', ' Dispense rate: 0000.000 ul/s', ' Stage delay   : 000.000 s'])
        self.assertEqual(g.lines()[-1], '~~~   25 /  25 C')                 # the hotplate line at the bottom
        DONE_TASKS.add('power on and home the stage')

    def test_switch_the_hotplate_on_and_set_100_c(self):
        g = Rig(ink=False)
        self.assertTrue(g.press('key_s'))                                     # 1 S
        self.assertEqual((g.lines()[0], g.item()), ('Settings Mode', 'hotplate'))
        self.assertTrue(g.press('ok'))                                        # 2 OK on Hotplate: ON
        self.assertEqual((g.val('thermal.enabled'), g.lines()[1]), (True, '>Hotplate      : ON'))
        self.assertTrue(g.edit_digits('htemp', '100'))                        # 3 Hotplate temp: OK, digits, OK
        self.assertAlmostEqual(g.val('thermal.target_k'), 373.15)
        lamp = g.frame()['displays']['led_heater']
        self.assertTrue(lamp['on'] and lamp['blink'])                         # heating: the Heater LED blinks
        self.assertTrue(any(g.frame()['displays']['screen']['blink']))      # the hotplate icon blinks while the hotplate is on
        for _ in range(60):
            g.h.step(10.0)
        lamp = g.frame()['displays']['led_heater']
        self.assertTrue(lamp['on'] and not lamp['blink'])                     # at the set temperature: steady
        self.assertIn('  99 / 100 C', g.line_of('~~~'))                     # the proportional heater settles just below the set point
        g.goto_item('hotplate')
        g.press('ok')                                                         # OK again: OFF
        self.assertFalse(g.val('thermal.enabled'))
        self.assertFalse(g.frame()['displays']['led_heater']['on'])
        DONE_TASKS.add('switch the hotplate on and set 100 C')

    def test_apply_a_temperature_offset(self):
        g = Rig(ink=False)
        g.press('key_s')
        self.assertTrue(g.edit_digits('toff', '+05'))
        self.assertEqual((g.val('thermal.offset_k'), g.line_of('Temp offset')), (5.0, '>Temp offset   : +05 C'))
        self.assertIn('  25 /  25 C', g.line_of('~~~'))                     # 1.3.0: the offset shifts no reading, it moves the controlled temperature
        g.goto_item('hotplate')
        g.press('ok')
        g.edit_digits('htemp', '060')
        for _ in range(60):
            g.h.step(10.0)
        self.assertIn('  64 /  60 C', g.line_of('~~~'))                     # manual p.23 note: the displayed temperature has the offset applied (above the set point)
        self.assertEqual(g.val('thermal.indicated_k'), g.h.temp)               # the sensor itself is held at set point + offset
        self.assertFalse(g.frame()['displays']['led_heater']['blink'])        # at its (offset) target: the Heater LED is steady
        g.goto_item('toff')
        g.press('ok')
        g.press('down')                                                       # the sign: up / down change it
        self.assertEqual(g.line_of('Temp offset'), '>Temp offset   : -05 C')
        g.press('ok')
        self.assertEqual(g.val('thermal.offset_k'), -5.0)
        for _ in range(90):
            g.h.step(10.0)
        self.assertIn('  55 /  60 C', g.line_of('~~~'))
        g.goto_item('toff')
        g.press('ok')
        g.press('right')
        g.press('up')                                                         # -15 is outside +/-10: the digit does not move
        self.assertEqual(g.line_of('Temp offset'), '>Temp offset   : -05 C')
        g.press('ok')
        DONE_TASKS.add('apply a temperature offset')

    def test_select_syringe_size(self):
        g = Rig(ink=False)
        g.press('key_s')
        g.goto_item('syringe')
        g.press('ok')
        g.press('left', 2)                                                    # scroll the pre-set sizes: 5 -> 2 -> 1
        self.assertEqual(g.line_of('Syringe size'), '>Syringe size  : 1 ml')
        g.press('ok')
        self.assertEqual(g.val('settings.state')['syringe'], '1')
        g.press('key_p')
        self.assertEqual(g.lines()[0], 'Program Mode            1 ml')       # shown at the top right of Program Mode
        g.press('key_m')
        self.assertIn('0017.303 ul/s', g.line_of('Rate'))                    # the manual rate follows the syringe size (plunger speed kept)
        g.press('key_s')
        g.goto_item('syringe')
        g.press('down')
        self.assertEqual(g.item(), 'stagehome')                               # Diameter: selectable only with Cust
        g.press('up')
        g.press('ok')
        g.press('right', 6)
        g.press('ok')                                                         # Cust without a diameter: warning
        self.assertEqual((g.screen(), g.lines()[3].strip()), ('warn', 'Diameter not set'))
        g.press('ok')
        self.assertEqual((g.screen(), g.item()), ('settings', 'syringe'))
        self.assertTrue(g.edit_digits('diam', '12.000'))
        self.assertAlmostEqual(g.val('settings.state')['diameter_m'], 0.012)
        g.press('key_p')
        self.assertEqual(g.lines()[0], 'Program Mode            Cust')
        DONE_TASKS.add('select syringe size')

    def test_jog_stage_and_prime_syringe_manually(self):
        g = Rig(prime=False)
        self.assertTrue(g.press('key_m'))                                     # 1 M: the stage pre-selected
        self.assertEqual((g.lines()[0], g.item()), ('Manual Mode', 'mspeed'))
        self.assertEqual(g.line_of('Speed'), '>Speed    : 010.000 mm/s')      # 2 default 10 mm/s
        self.assertTrue(g.edit_digits('mspeed', '020.000'))
        g.press('right', hold=1.0)                                            # 3 right while Stage is highlighted: the stage moves
        g.h.step(2.0)
        self.assertAlmostEqual(g.val('stage.position_m'), 0.02, places=9)
        self.assertEqual(g.line_of('Position'), ' Position : 020.000 mm')
        g.press('left', hold=1.0)
        g.h.step(2.0)
        self.assertEqual(g.val('stage.position_m'), 0.0)
        g.press('down')                                                       # 4 select the Syringe
        self.assertEqual(g.item(), 'mrate')
        self.assertEqual(g.line_of('Rate'), '>Rate     : 0086.378 ul/s')      # 5 rate from the syringe size
        self.assertTrue(g.edit_digits('mrate', '0020.000'))
        self.assertAlmostEqual(g.val('settings.state')['manual_rate_m3_s'], 2e-8)
        self.assertTrue(g.press('right', hold=5.0))                           # 6 right: the plunger moves, the volume is shown
        g.h.step(6.0)
        self.assertAlmostEqual(g.val('pump.delivered_m3'), 1e-7, places=15)
        self.assertEqual(g.line_of('Volume'), ' Volume   : 100.000 ul')
        p = g.call(g.E, 'product.read')
        self.assertGreater(p['line_solvent_kg'], 0)
        self.assertGreater(p['bead_solvent_kg'], 0)                           # meniscus formed under the head
        g.press('left', hold=1.0)                                             # left: the clamped plunger draws the line back
        g.h.step(2.0)
        self.assertEqual(g.line_of('Volume'), ' Volume   : 080.000 ul')
        q = g.call(g.E, 'product.read')
        self.assertGreater(q['syringe_solvent_kg'], p['syringe_solvent_kg'])
        total = lambda x: sum(x[k] for k in x if k.endswith('_solvent_kg') and not k.startswith(('initial', 'evaporated')))  # noqa: E731
        self.assertAlmostEqual(total(p), total(q), places=15)                 # nothing created or lost
        DONE_TASKS.add('jog stage and prime syringe manually')

    def test_program_a_coating_step(self):
        g = Rig()
        g.press('key_p')                                                      # 1 P
        g.press('right', 2)                                                   # 2 program number with right, no edit mode
        self.assertEqual(g.lines()[1], '>Program 03')
        g.goto_item('steps')                                                  # 3 number of steps
        g.press('ok')
        g.press('up')
        g.press('ok')
        self.assertEqual(g.lines()[2], '>Step : 01 of 02')
        g.edit_digits('length', '050.000')                                    # 5 coating length, speed, rate, delay of step 1
        g.edit_digits('speed', '012.000')
        g.edit_digits('rate', '0005.000')
        g.edit_digits('delay', '000.200')
        g.goto_item('steps')
        g.press('right')                                                      # 4 the next step (left / right, not in edit mode)
        self.assertEqual(g.lines()[2], '>Step : 02 of 02')
        g.edit_digits('length', '120.000')                                    # above the remaining travel: warning, the remaining length
        self.assertEqual((g.screen(), g.lines()[3].strip()), ('warn', 'Max length exceeded'))
        g.press('ok')
        self.assertEqual((g.screen(), g.item()), ('program', 'length'))
        self.assertEqual(g.line_of('Coating length'), '>Coating length: 100.000 mm')
        g.edit_digits('speed', '075.000')
        g.edit_digits('rate', '0300.000')                                     # above the 5 ml maximum 265 ul/s: corrected
        self.assertEqual(g.lines()[3].strip(), 'Max Rate Exceeded')
        g.press('ok')
        self.assertEqual(g.line_of('Dispense rate'), '>Dispense rate: 0265.000 ul/s')
        steps = g.val('programs.state')['steps']
        self.assertEqual([(s['length_m'], s['speed_m_s'], s['rate_m3_s'], s['delay_s']) for s in steps],
                         [(0.05, 0.012, 5e-9, 0.2), (0.1, 0.075, 2.65e-7, 0.0)])
        g.edit_digits('speed', '000.010')                                     # below 0.05 mm/s: refused
        self.assertEqual(g.message(), 'Invalid value')
        DONE_TASKS.add('program a coating step')

    def test_run_and_stop_a_program(self):
        g = Rig()
        g.program((50, 10, 20, 1.0), (40, 20, 40, 0.0))
        g.press('key_p')
        self.assertTrue(g.press('start'))                                     # 1 Start: running page with the progress bar
        self.assertEqual(g.screen(), 'running')
        self.assertTrue(g.frame()['displays']['led_coating']['on'])
        g.h.step(0.5)
        lines = g.lines()
        self.assertEqual(lines[:2], ['Running Prog 01         5 ml', 'Step 01 of 02'])
        self.assertEqual(lines[2], 'Elapsed time : 00:00.5 [D]')            # the stage-delay icon blinks during the delay
        self.assertTrue(g.frame()['displays']['screen']['blink'][2])
        self.assertEqual(lines[3:7], ['Speed        : 010.000 mm/s', 'Distance     : 050.000 mm', 'Dispense rate: 020.000 ul/s', 'Dispense vol : 0010.000 ul'])
        self.assertEqual(g.val('stage.position_m'), 0.0)                      # the stage waits, the syringe dispenses
        g.h.step(3.5)
        self.assertEqual((g.val('cycle.stage'), round(g.val('stage.position_m'), 9)), ('coating', 0.03))
        self.assertIn('[##########----------]  50 %', g.lines()[7])          # 4 s of 1 + 5 + 2 s
        g.h.step(2.5)                                                         # step 2 at 20 mm/s
        self.assertEqual(g.lines()[1], 'Step 02 of 02')
        self.assertAlmostEqual(g.val('stage.position_m'), 0.06, places=9)
        self.assertTrue(g.press('stop'))                                      # 3 Stop
        self.assertEqual((g.val('cycle.phase'), g.screen()), ('cancelled', 'program'))
        self.assertFalse(g.frame()['displays']['led_coating']['on'])
        film = g.call(g.E, 'product.read')
        self.assertGreater(film['film_solute_kg'], 0)
        DONE_TASKS.add('run and stop a program')


class Warnings(unittest.TestCase):
    def test_speed_not_set_and_travel_exceeded(self):
        g = Rig(ink=False)
        g.call(g.A, 'stage.move_to', position_m=0.06, speed_m_s=0.05)       # coating may start anywhere (p.38): the stage at 60 mm
        g.h.step(2.0)
        g.call(g.E, 'ink.configure', **rt.INK_EXAMPLE)
        g.call(g.A, 'pump.dispense', volume_m3=1e-7, flow_m3_s=2e-8)
        g.h.step(6.0)
        g.press('key_p')
        g.press('start')                                                      # the coating speed is not set
        self.assertEqual((g.screen(), g.lines()[3].strip()), ('warn', 'Coating speed not set'))
        self.assertEqual(g.val('cycle.phase'), 'succeeded')
        g.press('ok')
        g.program((100, 10, 20, 0.0))
        g.press('start')                                                      # 60 mm + 100 mm > 150 mm
        self.assertEqual((g.screen(), g.lines()[3:5]), ('warn', ['  Distance travel exceeded', '  Please reset to HOME']))
        g.press('ok')
        g.edit_digits('length', '080.000')                                    # or reduce the coating length
        self.assertTrue(g.press('start'))
        self.assertEqual(g.screen(), 'running')

    def test_motor_stalled(self):
        g = Rig(ink=False)
        g.call(g.E, 'ink.configure', **dict(rt.INK_EXAMPLE, solvent_kg=0.0001, solute_kg=0.00001))
        g.call(g.A, 'pump.dispense', volume_m3=6e-8, flow_m3_s=2e-8)
        g.h.step(4.0)
        g.program((140, 10, 20, 0.0))
        g.press('key_p')
        g.press('start')
        g.h.step(20.0)                                                        # the syringe runs empty during the run
        self.assertEqual((g.screen(), g.lines()[3].strip(), g.val('safety.fault')), ('stalled', 'Motor Stalled', 'SYRINGE_EMPTY'))
        g.press('ok')
        self.assertEqual(g.screen(), 'program')
        self.assertFalse(g.press('start'))                                    # until the syringe is refilled
        g.call(g.E, 'fluid.drain')
        self.assertEqual(g.val('safety.fault'), '')


class CallerRules(unittest.TestCase):
    """Keys pressed through a session act as that session's caller (1.3.0): a run, homing, jog or priming started with a key belongs to the caller, and
    another agent can stop or switch off the operation neither through the keys nor directly; the emergency stop works for everyone."""
    def test_hard_power_switch_is_never_refused(self):
        """Lead rule 10-19 (1.3.1): the rear power switch is a physical action; another agent switching it off stops the run as a power loss, and
        power.set / power.toggle, which model that switch, follow it."""
        g = Rig()
        g.program((50, 10, 20, 0.0))
        g.press('key_p')
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
        g.program((50, 10, 20, 0.0))
        g.press('key_p')
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
        for cap, args in (('acquisition.stop', {}),
                          ('thermal.set_temperature', dict(temperature_k=313.15)), ('thermal.set_enabled', dict(enabled=True)),
                          ('thermal.set_offset', dict(offset_k=3.0))):               # lead rule 10-19: nor change the hotplate under it
            r = b.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        after = g.h.debug_snapshot()
        for k in ('phase', 'stage', 'powered', 'power_switch', 'q', 'target', 'operation_id', 'film', 'syringe', 'delivered', 'target_temp', 'heating', 'offset'):
            self.assertEqual(before[k], after[k], k)
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'succeeded')
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')

    def test_priming_belongs_to_the_caller_and_the_emergency_stop_works_for_everyone(self):
        g = Rig(prime=False)
        g.press('key_m')
        g.press('down')                                                       # the Syringe
        self.assertTrue(g.press('right', hold=2.0))                           # priming: an operation of the caller
        op = g.h.operation_id
        self.assertEqual((g.val('cycle.stage'), g.h._owners.get(op)), ('priming', 'agent'))
        g.h.register_principal('agent2', 'agent')
        b = g.h.session('agent2')
        r = b.invoke(instance_id=INST, capability_id='panel.switch', arguments=dict(input_id='estop', position='pressed'), request_id='b1')
        self.assertTrue(r['result']['accepted'])
        self.assertEqual((g.A.poll(op)['status'], g.val('power.enabled')), ('failed', False))


class CrashWarningFault(unittest.TestCase):
    """Troubleshooting table, manual p.43: Crash Warning (the secondary safety switch) through the evaluator fault injection."""
    def test_accidental_trigger_stops_stage_and_pump_and_clears_with_a_power_cycle(self):
        g = Rig()
        g.program((50, 10, 20, 1.0))
        g.press('key_p')
        g.press('start')
        g.h.step(0.5)
        q, v = g.val('stage.position_m'), g.val('pump.delivered_m3')
        r = g.call(g.E, 'fault.inject', code='crash_warning', cause='triggered')
        self.assertEqual((r['phase'], g.val('cycle.phase'), g.val('safety.crash_warning')), ('failed', 'failed', True))
        g.h.step(1.0)
        self.assertEqual((g.val('stage.position_m'), g.val('pump.delivered_m3')), (q, v))   # stage and syringe pump stopped
        self.assertEqual((g.screen(), g.lines()[3].strip()), ('crash_warning', 'Crash Warning'))
        for key in ('key_s', 'key_m', 'return_key', 'start', 'ok'):
            self.assertFalse(g.press(key), key)
        self.assertTrue(g.press('gauge_left_on'))                             # the gauges have their own battery
        for cap, args in (('acquisition.start', {}), ('stage.home', {}), ('stage.jog', dict(direction='forward', duration_s=1.0)),
                          ('stage.move_to', dict(position_m=0.05, speed_m_s=0.01)), ('pump.dispense', dict(volume_m3=1e-8, flow_m3_s=2e-8)),
                          ('pump.jog', dict(direction='push', duration_s=1.0)), ('stage.set_home', {})):
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'PRECONDITION_FAILED', cap)
        g.switch('off')
        g.switch('on')
        self.assertEqual((g.screen(), g.val('safety.crash_warning')), ('boot', False))

    def test_plunger_pusher_on_the_switch_needs_the_lead_screw_turned(self):
        g = Rig(sample=False, ink=False)
        g.call(g.E, 'fault.inject', code='crash_warning', cause='plunger_pusher')
        self.assertNotIn('truth.secondary_switch', g.A.read(instance_id=INST)['observations'])
        g.switch('off')
        g.switch('on')
        g.h.step(2.1)
        self.assertEqual((g.screen(), g.val('truth.secondary_switch')), ('crash_warning', 'plunger_pusher'))   # still pressed: shown again
        self.assertEqual(g.call(g.E, 'fault.clear')['cause'], 'plunger_pusher')   # lead screw turned by hand
        self.assertEqual(g.screen(), 'crash_warning')
        g.switch('off')
        g.switch('on')
        g.h.step(2.1)
        self.assertEqual((g.screen(), g.val('safety.crash_warning')), ('reset', False))


class HotplateOffset(unittest.TestCase):
    """Manual p.23: the surface may differ from the displayed reading; the temperature offset sets the exact surface temperature (1.3.0)."""
    def test_offset_compensates_a_surface_error(self):
        g = Rig(ink=False)
        g.call(g.E, 'sensor.configure_error', surface_rise_error=-0.1)          # the surface rise is 10 % short of the sensor's
        g.call(g.A, 'thermal.set_temperature', temperature_k=333.15)
        g.call(g.A, 'thermal.set_enabled', enabled=True)
        for _ in range(60):
            g.h.step(10.0)
        surface = g.val('truth.surface_k')                                    # what an external thermometer reads
        self.assertLess(surface, 333.15 - 3.0)
        self.assertNotIn('truth.surface_k', g.A.read(instance_id=INST)['observations'])
        offset = round(333.15 - surface)
        g.call(g.A, 'thermal.set_offset', offset_k=float(offset))
        for _ in range(90):
            g.h.step(10.0)
        self.assertAlmostEqual(g.val('truth.surface_k'), 333.15, delta=0.6)    # the surface now reaches the set point
        self.assertGreater(g.val('thermal.indicated_k'), 333.15 + offset - 1.0)  # and the display reads above it


class PanelRules(unittest.TestCase):
    def test_stage_home(self):
        g = Rig(ink=False)
        g.press('key_m')
        g.press('right', hold=1.0)
        g.h.step(2.0)
        g.press('key_s')
        g.goto_item('stagehome')
        self.assertTrue(g.press('ok'))                                        # OK: edit mode
        self.assertTrue(g.press('return_key'))                                # Return: the new home is the current position
        self.assertEqual((g.val('settings.state')['home_m'], g.line_of('StageHome')), (0.01, '>StageHome     : 10.000'))
        self.assertTrue(g.frame()['ui']['editor'] is not None)
        g.press('ok')
        g.press('key_m')
        g.press('right', hold=2.0)
        g.h.step(3.0)
        g.press('return_key')                                                 # Return now goes to the new home
        g.h.step(5.0)
        self.assertAlmostEqual(g.val('stage.position_m'), 0.01, places=9)
        g.switch('off')
        g.switch('on')                                                        # the new home lasts for this operation only
        self.assertEqual(g.val('settings.state')['home_m'], 0.0)

    def test_height_gauges(self):
        g = Rig(ink=False)
        self.assertEqual(g.lines('gauge_l'), [''])
        self.assertTrue(g.press('gauge_left_on'))                             # OFF/ON
        self.assertTrue(g.press('gauge_right_on'))
        self.assertEqual(g.lines('gauge_l'), ['  0.100 mm'])
        g.call(g.A, 'gap.set', left_m=0.0, right_m=0.0)                      # the head lowered onto the substrate: Zero
        g.h.step(0.1)
        g.press('gauge_left_zero')
        g.press('gauge_right_zero')
        g.call(g.A, 'gap.set', left_m=0.00008, right_m=0.00008)              # raise to the gap
        g.h.step(0.1)
        self.assertEqual((g.lines('gauge_l'), g.lines('gauge_r')), (['  0.080 mm'], ['  0.080 mm']))
        g.switch('off')                                                       # LR44 battery: independent of the mains
        g.press('gauge_left_on')
        self.assertEqual(g.lines('gauge_l'), [''])
        self.assertFalse(g.press('gauge_left_zero'))

    def test_emergency_stop_and_reset(self):
        g = Rig()
        g.program((50, 10, 20, 0.0))
        g.press('key_s')
        g.press('ok')                                                         # hotplate on
        g.press('key_p')
        g.press('start')
        g.h.step(1.0)
        self.assertTrue(g.switch('pressed', 'estop'))                         # power cut: run and heater stop
        self.assertEqual((g.screen(), g.val('cycle.phase'), g.val('thermal.enabled')), ('off', 'failed', False))
        self.assertFalse(g.switch('on'))
        g.switch('released', 'estop')
        g.switch('off')
        self.assertTrue(g.switch('on'))
        self.assertEqual(g.screen(), 'boot')

    def test_refusals_and_power(self):
        g = Rig(ink=False)
        g.program((50, 10, 20, 0.0))
        g.press('key_p')
        self.assertFalse(g.press('start'))                                    # no syringe installed
        self.assertEqual(g.message(), 'Not possible now')
        g.h.step(2.1)
        self.assertIsNone(g.message())                                        # the message clears after 2 s
        g.press('key_m')
        g.press('down')
        self.assertFalse(g.press('right'))                                    # no syringe to dispense from
        g.switch('off')
        self.assertFalse(g.press('key_s'))
        self.assertEqual(int(g.h.panel.render('screen').max()), 0)
        self.assertEqual(g.lines(), [''] * len(g.lines()))
        g.switch('on')
        g.h.step(2.1)
        self.assertTrue(g.press('key_s'))                                     # the mode keys skip homing
        self.assertEqual((g.screen(), g.val('stage.homed')), ('settings', False))

    def test_panel_and_direct_commands_give_the_same_state(self):
        p, d = Rig(), Rig()
        p.press('key_p')
        p.edit_digits('length', '060.000')
        p.edit_digits('speed', '010.000')
        p.edit_digits('rate', '0020.000')
        p.press('start')
        d.call(d.A, 'program.set_step', step=1, length_m=0.06, speed_m_s=0.01, rate_m3_s=2e-8)
        d.call(d.A, 'program.select', program=1)
        d.call(d.A, 'acquisition.start')
        for g in (p, d):
            g.h.step(3.0)
        self.assertEqual(p.h.read('evaluator'), d.h.read('evaluator'))

    def test_method_configure_is_shown_on_the_panel(self):
        g = Rig(ink=False)
        g.call(g.A, 'method.configure', **rt.METHOD_EXAMPLE)
        g.press('key_p')
        self.assertEqual(g.lines()[3:7], [' Coating length: 140.000 mm', ' Coating speed: 010.000 mm/s', ' Dispense rate: 0020.000 ul/s',
                                          ' Stage delay   : 000.200 s'])
        self.assertTrue(g.val('method.configured'))

    def test_only_agent_capabilities_behind_the_panel(self):
        caps = rt._panel_ui.spec_capabilities(rt.PANEL_SPEC)
        self.assertEqual(sorted(caps), ['acquisition.start', 'acquisition.stop', 'emergency.release', 'emergency.stop', 'gauge.button', 'power.set',
                                        'program.select', 'program.set_step', 'program.set_steps_count', 'pump.jog', 'settings.configure', 'stage.home',
                                        'stage.jog', 'stage.set_home', 'thermal.set_enabled', 'thermal.set_offset', 'thermal.set_temperature'])
        self.assertTrue(all(rt.ALL_CAPS[k][5] == 'agent' for k in caps))
        g = Rig(sample=False, ink=False, boot=False)
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_render_determinism_and_capture(self):
        g = Rig(ink=False)
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
        g = Rig()
        g.press('gauge_left_on')
        shots = dict(home=g.h.panel.save_png('screen', OUT / 'home.png'), gauge=g.h.panel.save_png('gauge_l', OUT / 'gauge.png'))
        g.press('key_s')
        shots['menu'] = g.h.panel.save_png('screen', OUT / 'menu.png')
        g.press('key_m')
        shots['manual'] = g.h.panel.save_png('screen', OUT / 'manual.png')
        g.press('key_p')
        g.goto_item('length')
        g.press('ok')
        g.press('up')
        g.h.step(0.5)
        shots['edit'] = g.h.panel.save_png('screen', OUT / 'edit.png')
        g.press('ok')
        g.press('start')
        shots['error'] = g.h.panel.save_png('screen', OUT / 'error.png')
        g.press('ok')
        g.program((60, 10, 20, 1.0))
        g.press('start')
        g.h.step(2.0)
        shots['running'] = g.h.panel.save_png('screen', OUT / 'running.png')
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
        for sid in ('settings.state', 'programs.state', 'gauges.state', 'safety.crash_warning', 'truth.secondary_switch', 'truth.surface_k'):
            self.assertIn(sid, c['state_slots'])
        for k in rt.SIM_CAPS:
            self.assertEqual(c['capabilities'][k]['visibility'], 'evaluator')
        self.assertNotIn('理想显示偏置', c['capabilities']['thermal.set_offset']['function_zh'])
        self.assertEqual(sorted(f['id'] for f in c['extensions']['chemistry_lab.fixes']), ['crash_warning_fault', 'hotplate_offset_controls', 'panel_caller_rules',
                                                                                             'sweep_hard_power_switch'])
        for cid in ('led_coating', 'led_heater', 'gauge_keys_left', 'gauge_keys_right'):
            self.assertEqual(c['components'][cid]['geometry_status'], 'absent')
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])


if __name__ == '__main__':
    unittest.main()
