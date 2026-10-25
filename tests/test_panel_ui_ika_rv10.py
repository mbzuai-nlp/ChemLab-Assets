"""front-panel tests for CHEM-024 ika_rv10_digital_v (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_vacuum.py):
python3 -m pytest tests/test_panel_ui_ika_rv10.py -q

Every gesture goes through the session API (panel.press / panel.turn / panel.switch); the display is checked in the structured frame (panel.frame,
evaluator) and in the rendered pixels.  Themes: the five key procedures of the menu survey; the other drive functions (Power stand-by, version
display, interval direction changes, timer pause, E01 / E02 errors and their reset at the main switch, the approximate HB 10 bath panel);
refusals and time-outs; panel = direct commands; render determinism and the evidence PNGs; contract entries."""
import json
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
import basic40_runtime_vacuum as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'rv10'
OUT = ROOT / 'outputs/panel_ui/ika_rv10'
CONTRACT = rt.RV10_ASSET_DIR / 'asset.contract.json'
RPM = 30.0 / math.pi
SURVEY_TASKS = ['switch on and start rotation at 120 rpm', 'lower the flask into the bath', 'set a 30 min timer', 'set interval (left/right) operation',
                'set the lower lift end stop']
DONE_TASKS = set()


class Rig:
    def __init__(self, on=True, bath=True):
        self.h = rt.RV10Host()
        self.h.create_instance(INST)
        self.h.register_principal('agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        if bath:
            self.h.configure_bath(INST, effective_heat_capacity_j_k=16000.0, initial_temperature_k=293.15, heat_loss_w_k=4.0,
                                  evidence='engineering_assumption: test bath')
        if on:
            assert self.switch('on')
            self.h.step(3)                                                         # version display, then the parameter display

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def press(self, key, duration_s=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=duration_s)['accepted']

    def turn(self, knob, detents):
        return self.call(self.A, 'panel.turn', input_id=knob, detents=detents)['accepted']

    def switch(self, pos, which='on_off_switch'):
        return self.call(self.A, 'panel.switch', input_id=which, position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def drive(self):
        return self.frame()['displays']['drive']

    def field(self, fid):
        return self.drive()['fields'][fid]['text']

    def set_speed(self, rpm):
        """Turn the knob until the display shows the speed (5 rpm per detent)."""
        for _ in range(80):
            shown = int(self.field('speed'))
            if shown == rpm:
                return
            self.turn('knob', 1 if shown < rpm else -1)
        raise AssertionError(rpm)

    def lift_settle(self, s=10.0):
        self.h.step(s)
        return self.val('lift.position_m')


class KeyProcedures(unittest.TestCase):
    def test_switch_on_and_start_rotation_at_120_rpm(self):
        r = Rig(on=False)
        self.assertEqual(r.screen(), 'off')
        self.assertTrue(r.switch('on'))
        self.assertEqual(r.screen(), 'version')                                    # software version, then the parameter display
        r.h.step(2.5)
        self.assertEqual(r.screen(), 'main')
        r.set_speed(120)
        self.assertFalse(r.val('rotation.running'))
        self.assertTrue(r.press('knob'))                                           # press the knob: rotation starts
        r.h.step(1)
        self.assertTrue(r.val('rotation.running'))
        self.assertAlmostEqual(r.val('rotation.indicated_speed_rad_s') * RPM, 120.0, places=0)
        d = r.drive()
        self.assertEqual(d['fields']['speed']['text'], '120')
        self.assertTrue(d['icons']['rotation']['on'])
        self.assertTrue(r.press('knob'))                                           # press again: it stops
        self.assertFalse(r.val('rotation.running'))
        DONE_TASKS.add('switch on and start rotation at 120 rpm')

    def test_lower_the_flask_into_the_bath(self):
        r = Rig()
        self.assertTrue(r.press('key_lift_up', 10.0))                             # L held: up to the top
        self.assertAlmostEqual(r.lift_settle(), rt.RV10_MAXIMUM_LIFT_M)
        self.assertTrue(r.press('key_lift_down', 3.0))                            # M held 3 s at the lift speed, then released
        self.assertAlmostEqual(r.lift_settle(), rt.RV10_MAXIMUM_LIFT_M - 3.0 * r.h.lift_speed_m_s, places=6)
        self.assertFalse(r.val('lift.moving'))
        DONE_TASKS.add('lower the flask into the bath')

    def test_set_a_30_min_timer(self):
        r = Rig()
        r.set_speed(100)
        self.assertTrue(r.press('key_timer'))
        self.assertEqual(r.screen(), 'timer_edit')
        self.assertTrue(r.drive()['icons']['timer_label']['blink'])               # TIMER flashes
        r.turn('knob', 30)
        self.assertEqual(r.field('timer'), '30')
        self.assertTrue(r.press('key_timer'))                                      # saved
        self.assertEqual(r.screen(), 'main')
        self.assertEqual(r.val('timer.setting_s'), 1800.0)
        self.assertIsNone(r.val('timer.remaining_s'))                             # counts only with the rotation
        r.press('key_lift_down', 4.0)
        r.h.step(5)
        r.press('knob')
        r.h.step(600)
        self.assertEqual(r.field('timer'), '20')
        r.h.step(1199)
        self.assertTrue(r.val('rotation.running'))
        beeps = r.val('drive.beeps')
        r.h.step(2)
        self.assertFalse(r.val('rotation.running'))                                # end: beep, rotation stops, lift goes up
        self.assertEqual(r.val('drive.beeps'), beeps + 1)
        self.assertEqual(r.val('lift.target_position_m'), rt.RV10_MAXIMUM_LIFT_M)
        self.assertEqual(r.field('timer'), '30')                                  # the saved value appears again
        r.press('key_timer')
        r.turn('knob', -40)
        r.press('key_timer')                                                       # 0 deactivates
        self.assertEqual(r.val('timer.setting_s'), 0.0)
        self.assertEqual(r.field('timer'), '')
        DONE_TASKS.add('set a 30 min timer')

    def test_set_interval_operation(self):
        r = Rig()
        r.set_speed(250)
        self.assertTrue(r.press('key_int'))
        self.assertTrue(r.drive()['icons']['int_label']['blink'])
        r.turn('knob', 10)
        self.assertTrue(r.press('key_int'))
        self.assertEqual(r.val('interval.setting_s'), 10)
        self.assertEqual(r.field('int'), '10')
        self.assertEqual(r.field('speed'), '200')                                  # interval mode limits the speed to 200 rpm
        r.turn('knob', 5)
        self.assertEqual(r.field('speed'), '200')
        r.press('knob')
        r.h.step(5)
        self.assertEqual(r.val('rotation.direction'), 'right')
        r.h.step(10)
        self.assertEqual(r.val('rotation.direction'), 'left')
        self.assertEqual(r.drive()['icons']['rotation']['direction'], 'ccw')
        r.press('key_int')
        r.turn('knob', -20)
        r.press('key_int')                                                         # 0 deactivates
        self.assertEqual(r.val('interval.setting_s'), 0)
        r.turn('knob', 4)
        self.assertEqual(r.field('speed'), '220')
        DONE_TASKS.add('set interval (left/right) operation')

    def test_set_the_lower_lift_end_stop(self):
        r = Rig()
        r.press('key_lift_down', 10.0)
        self.assertAlmostEqual(r.lift_settle(), 0.0)
        r.press('key_lift_up', 1.5)                                               # to the wanted lower position (30 mm)
        self.assertAlmostEqual(r.lift_settle(), .03)
        self.assertTrue(r.turn('end_stop', 30))                                    # centre button, move the stop to 30 mm
        self.assertAlmostEqual(r.val('lift.lower_stop_m'), .03)
        r.press('key_lift_up', 10.0)                                               # up to the upper end
        self.assertAlmostEqual(r.lift_settle(), rt.RV10_MAXIMUM_LIFT_M)
        r.press('key_lift_down', 20.0)                                             # check: the drive stops at the stop
        self.assertAlmostEqual(r.lift_settle(), .03)
        r.call(r.A, 'lift.set_position', position_m=0.0)                           # the stop also holds direct targets
        self.assertAlmostEqual(r.lift_settle(), .03)
        r.turn('end_stop', 100)
        self.assertAlmostEqual(r.val('lift.lower_stop_m'), rt.RV10_LOWER_STOP_MAX_M)   # 0-6 cm
        DONE_TASKS.add('set the lower lift end stop')


class DriveFunctions(unittest.TestCase):
    def test_power_key_stand_by_and_back(self):
        r = Rig()
        r.set_speed(150)
        r.press('knob')
        self.assertTrue(r.press('key_power'))
        self.assertEqual(r.screen(), 'standby')
        self.assertFalse(r.val('power.enabled'))
        self.assertFalse(r.val('rotation.running'))
        self.assertEqual(r.field('speed'), '')                                     # dark drive display
        self.assertFalse(r.press('key_timer'))
        self.assertFalse(r.press('key_lift_up', 1.0))                             # no lift in stand-by
        self.assertTrue(r.press('key_power'))
        self.assertEqual(r.screen(), 'version')
        r.h.step(2.5)
        self.assertEqual(r.field('speed'), '150')                                  # most recent target speed
        r.call(r.A, 'power.set', enabled=False)                                    # the display follows a direct command
        self.assertEqual(r.screen(), 'standby')

    def test_timer_pauses_with_the_rotation_and_starts_running(self):
        r = Rig()
        r.press('knob')
        r.press('key_timer')
        r.turn('knob', 5)
        r.press('key_timer')
        self.assertEqual(r.val('timer.remaining_s'), 300.0)                       # set while rotating: it counts at once
        r.h.step(120)
        r.press('knob')
        r.h.step(600)
        self.assertEqual(r.val('timer.remaining_s'), 180.0)
        self.assertEqual(r.field('timer'), '3')
        r.press('knob')
        r.h.step(181)
        self.assertFalse(r.val('rotation.running'))

    def test_error_e01_locks_the_drive_until_the_main_switch(self):
        r = Rig()
        r.call(r.E, 'drive.configure_fault', cause='motor_blocked')
        r.press('key_lift_down', 3.0)
        r.h.step(5)
        self.assertTrue(r.press('knob'))
        self.assertEqual(r.val('drive.error'), 'E01')
        self.assertFalse(r.val('rotation.running'))
        self.assertEqual(r.val('lift.target_position_m'), rt.RV10_MAXIMUM_LIFT_M)  # lift to the top
        self.assertEqual(r.field('speed'), 'E01')
        self.assertFalse(r.press('knob'))
        self.assertFalse(r.turn('knob', 2))
        self.assertFalse(r.press('key_timer'))
        self.assertEqual(r.call(r.A, 'rotation.start', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')
        self.assertTrue(r.press('key_lift_down', 1.0))                            # the lift can be operated again
        r.call(r.E, 'drive.configure_fault', cause='none')                         # corrective measure
        r.switch('off')
        r.switch('on')
        r.h.step(3)
        self.assertIsNone(r.val('drive.error'))
        self.assertTrue(r.press('knob'))
        self.assertTrue(r.val('rotation.running'))

    def test_error_e02_without_bath_link(self):
        r = Rig()
        r.call(r.E, 'drive.configure_fault', cause='ir_link_blocked')
        self.assertEqual(r.val('drive.error'), 'E02')
        self.assertEqual(r.field('speed'), 'E02')
        r.switch('off')
        r.switch('on')
        self.assertEqual(r.val('drive.error'), 'E02')                             # cause not corrected: the error comes back
        r2 = Rig(bath=False)
        r2.call(r2.E, 'drive.configure_fault', cause='ir_link_blocked')
        self.assertIsNone(r2.val('drive.error'))

    def test_bath_panel_set_temperature_and_heating(self):
        r = Rig()
        self.assertTrue(r.turn('bath_knob', 40))
        self.assertAlmostEqual(r.val('thermal.target_temperature_k'), 333.15)
        bath = r.frame()['displays']['bath']                                     # datasheet photo: temperature large, SET value below
        self.assertEqual((bath['fields']['temp']['text'], bath['fields']['set']['text']), ('20.0', '60'))
        self.assertTrue(bath['fields']['set']['blink'])                           # the new set value flashes for a while
        r.h.step(4)
        bath = r.frame()['displays']['bath']
        self.assertEqual((bath['fields']['temp']['text'], bath['fields']['set']['text'], bath['fields']['set']['blink']), ('20.0', '60', False))
        self.assertTrue(r.switch('on', 'bath_switch'))
        r.h.step(600)
        self.assertGreater(r.val('thermal.temperature_k'), 300.0)
        self.assertTrue(r.frame()['displays']['bath']['icons']['heat']['on'])
        self.assertTrue(r.switch('off', 'bath_switch'))
        self.assertFalse(r.val('thermal.enabled'))
        r.turn('bath_knob', -100)
        self.assertAlmostEqual(r.val('thermal.target_temperature_k'), 293.15)    # not below room temperature


class RefusalsAndTimeouts(unittest.TestCase):
    def test_main_switch_off_only_the_switch_reacts(self):
        r = Rig(on=False)
        for key in ('key_power', 'knob', 'key_timer', 'key_lift_up'):
            self.assertFalse(r.press(key), key)
        self.assertFalse(r.turn('knob', 3))
        self.assertFalse(r.val('power.enabled'))
        self.assertEqual(r.field('speed'), '')

    def test_entries_time_out_unsaved(self):
        r = Rig()
        r.press('key_timer')
        r.turn('knob', 12)
        r.h.step(11)
        self.assertEqual(r.screen(), 'main')
        self.assertEqual(r.val('timer.setting_s'), 0.0)
        r.press('key_int')
        r.turn('knob', 12)
        r.h.step(11)
        self.assertEqual(r.val('interval.setting_s'), 0)

    def test_refusals(self):
        r = Rig(bath=False)
        self.assertFalse(r.switch('on', 'bath_switch'))                            # no bath model configured
        self.assertEqual(r.frame()['displays']['bath']['fields']['temp']['text'], '---')
        r.call(r.A, 'interval.set', interval_s=5)
        self.assertEqual(r.call(r.A, 'rotation.set_speed', expect='rejected', speed_rad_s=250 / RPM)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(r.call(r.A, 'timer.set_duration', expect='rejected', duration_s=90.0)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'drive.configure_fault', expect='rejected', cause='none')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call(r.A, 'vacuum.set_pressure', expect='unsupported', pressure_pa=5000.0)['error']['code'], 'UNSUPPORTED')

    def test_render_determinism_and_evidence(self):
        a, b = Rig(), Rig()
        self.assertEqual(a.h.panel(INST).png_bytes('drive')[0], b.h.panel(INST).png_bytes('drive')[0])
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        r.set_speed(120)
        r.press('key_int')
        r.turn('knob', 10)
        r.press('key_int')
        r.press('key_timer')
        r.turn('knob', 30)
        r.press('key_timer')
        r.press('knob')
        r.turn('bath_knob', 40)
        r.switch('on', 'bath_switch')
        r.h.step(60.0)                                                             # whole second: blinking fields in their shown phase
        p = r.h.panel(INST)
        renders = {'home': p.save_png('drive', OUT / 'home.png'), 'home_bath': p.save_png('bath', OUT / 'bath.png')}
        r.press('key_timer')
        renders['menu'] = p.save_png('drive', OUT / 'menu.png')                   # Timer entry (TIMER flashing, shown phase)
        r.press('key_timer')
        r.call(r.E, 'drive.configure_fault', cause='motor_blocked')
        r.h.step(0.5)
        renders['error'] = p.save_png('drive', OUT / 'error.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')


class PanelEqualsDirectAndContract(unittest.TestCase):
    def test_panel_equals_direct(self):
        a, b = Rig(), Rig(on=False)
        b.call(b.A, 'mains.set', on=True)
        b.h.step(3)
        a.set_speed(120)
        a.press('key_timer')
        a.turn('knob', 30)
        a.press('key_timer')
        a.press('key_int')
        a.turn('knob', 8)
        a.press('key_int')
        a.turn('end_stop', 20)
        a.press('knob')
        b.call(b.A, 'rotation.set_speed', speed_rad_s=120 / RPM)
        b.call(b.A, 'timer.set_duration', duration_s=1800.0)
        b.call(b.A, 'interval.set', interval_s=8)
        b.call(b.A, 'lift.set_lower_stop', stop_m=.02)
        b.call(b.A, 'rotation.start')
        for h in (a.h, b.h):
            h.step(100)
        for sid in ('rotation.target_speed_rad_s', 'rotation.running', 'timer.setting_s', 'timer.remaining_s', 'interval.setting_s',
                    'rotation.direction', 'lift.lower_stop_m', 'lift.target_position_m', 'power.enabled'):
            self.assertAlmostEqual(a.val(sid), b.val(sid), msg=sid) if isinstance(a.val(sid), float) else self.assertEqual(a.val(sid), b.val(sid), sid)

    def test_metadata_and_contract(self):
        meta = rt.export_rv10_contract_metadata()['asset_types'][rt.RV10_ASSET_TYPE_ID]
        self.assertEqual(set(meta['device_capabilities']), set(rt.RV10_DEVICE_ARGS))
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.turn', 'panel.switch', 'panel.capture', 'panel.frame'})
        self.assertEqual(meta['capabilities']['timer.set_duration']['availability'], 'implemented')
        for name, cap in {**meta['device_capabilities'], **meta['panel_capabilities'], 'timer.set_duration': meta['capabilities']['timer.set_duration']}.items():
            self.assertEqual(validate_value(cap['example_arguments'][0], cap['input_schema']), [], name)
        c = json.loads(CONTRACT.read_text())
        if 'chemistry_lab.panel_ui' in c['extensions']:
            for name in list(meta['device_capabilities']) + list(meta['panel_capabilities']):
                self.assertIn(name, c['capabilities'], name)
            self.assertEqual(c['asset_version'], rt.RV10_ASSET_VERSION)
        r = Rig()
        caps = r.A.describe(instance_id=INST)['capabilities']
        self.assertIn('panel.turn', caps)
        self.assertIn('mains.set', caps)
        self.assertNotIn('drive.configure_fault', caps)
        self.assertNotIn('drive.fault_cause', r.A.read(instance_id=INST)['observations'])


class SyncAudit20261019(unittest.TestCase):
    """2026-10-19 front-panel sync audit, rule 2: a panel gesture's commands act as the calling session (not a fixed panel principal)."""

    def test_panel_gesture_acts_as_the_calling_session(self):
        r = Rig()
        r.set_speed(120)
        seen = []
        orig = rt.RV10Session._invoke_checked

        def spy(sess, inst, cap, args, rid):
            if not cap.startswith('panel.'):
                seen.append((sess._RV10Session__principal_id, cap))
            return orig(sess, inst, cap, args, rid)
        with mock.patch.object(rt.RV10Session, '_invoke_checked', spy):
            self.assertTrue(r.press('knob'))
        self.assertTrue(seen)
        self.assertEqual({p for p, _ in seen}, {'agent'})
        self.assertIsNone(r.h._panel_caller)
        self.assertTrue(r.val('rotation.running') or r.h._instances[INST].state['rotation.running'])


class Sweep20261019(unittest.TestCase):
    """2026-10-19 consistency sweep (lead rule): the rotation / bath run started by one agent cannot be stopped, powered off or changed by
    another agent on the panel or the direct path (ACCESS_DENIED, state unchanged, dEn shown 2 s); the Power stand-by key (soft) is refused, the
    On/Off main switch (hard, lead clarification 10-19) and the lower end stop (mechanical) are physical and not refused; the evaluator is not
    restricted; the owner stops its own run."""

    def test_another_agents_run_cannot_be_stopped_or_changed(self):
        r = Rig()
        r.h.register_principal('agent_b')
        B = r.h.session('agent_b')
        r.set_speed(120)
        self.assertTrue(r.press('knob'))                                           # agent A starts the rotation on the drive
        r.h.step(1)
        self.assertEqual(r.h._instances[INST].g03['run_owners'], {'run': 'agent'})
        state = lambda: {k: v for k, v in r.h._instances[INST].state.items() if k not in ('rotation.angle_rad',)}   # noqa: E731
        before = state()
        gestures = (('panel.press', dict(input_id='knob')), ('panel.turn', dict(input_id='knob', detents=2)),
                    ('panel.press', dict(input_id='key_power')), ('panel.press', dict(input_id='key_lift_up', duration_s=1.0)),
                    ('panel.switch', dict(input_id='bath_switch', position='on')))
        for cap, args in gestures:
            self.assertFalse(r.call(B, cap, **args)['accepted'], (cap, args))      # refused ...
            self.assertEqual(r.field('speed'), 'dEn', (cap, args))                 # ... and shown on the drive display (frame)
            self.assertEqual(state(), before, (cap, args))                         # state unchanged
        for cap, args in (('rotation.stop', {}), ('rotation.set_speed', dict(speed_rad_s=5.0)), ('power.set', dict(enabled=False)),
                          ('thermal.start', {}), ('lift.jog', dict(direction='up', duration_s=1.0)),
                          ('timer.set_duration', dict(duration_s=600.0))):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(state(), before)
        r.call(B, 'lift.set_lower_stop', stop_m=0.02)                             # mechanical end stop: not refused
        r.call(B, 'panel.press', input_id='key_timer')                            # navigation is free
        self.assertEqual(r.screen(), 'timer_edit')
        r.h.step(11)                                                              # the entry times out unsaved
        self.assertEqual(r.field('speed'), '120')
        self.assertTrue(r.press('knob'))                                           # A stops its own run
        self.assertFalse(r.val('rotation.running'))
        self.assertNotIn('run_owners', r.h._instances[INST].g03)
        self.assertTrue(r.call(B, 'panel.press', input_id='knob')['accepted'])    # now B may start its own run ...
        self.assertTrue(r.val('rotation.running'))
        self.assertFalse(r.press('knob'))                                          # ... which A cannot stop
        self.assertEqual(r.field('speed'), 'dEn')
        r.call(r.E, 'rotation.set_speed', speed_rad_s=8.0)                         # the evaluator is not restricted
        self.assertTrue(r.switch('off'))                                           # the On/Off main switch is physical: A may cut it ...
        self.assertFalse(r.val('power.enabled'))
        self.assertFalse(r.val('rotation.running'))                                # ... the drive is de-energised and B's run ends (p.22)
        self.assertNotIn('run_owners', r.h._instances[INST].g03)



class FixRoundB(unittest.TestCase):
    """chemlab finding (2026-10-22): vacuum.set_pressure was agent-visible and 'declared' (planned) but this instrument has no
    pressure controller; it is 'unsupported' now, in the runtime metadata and the contract."""
    def test_set_pressure_is_unsupported(self):
        meta = rt.export_rv10_contract_metadata()['asset_types'][rt.RV10_ASSET_TYPE_ID]['capabilities']['vacuum.set_pressure']
        self.assertEqual(meta['availability'], 'unsupported')
        c = json.loads((ROOT / 'assets/basic40_batch02/t2_vacuum/ika_rv10/asset.contract.json').read_text())
        self.assertEqual(c['capabilities']['vacuum.set_pressure']['availability'], 'unsupported')
        g = Rig()
        r = g.A.invoke(instance_id=INST, capability_id='vacuum.set_pressure', arguments={'pressure_pa': 5000.0}, request_id='fixb_vp')
        self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))

if __name__ == '__main__':
    unittest.main()
