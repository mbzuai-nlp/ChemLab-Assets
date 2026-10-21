"""front-panel tests for CHEM-192 tuttnauer_3870elvd (panel_ui.json + scripts/asset_runtime/_panel_ui.py on the runtime's ELV-D controller):
python3 -m pytest tests/test_panel_ui_tuttnauer_3870elvd.py -q

Every gesture goes through the session API (panel.press with held keys for UP + DOWN together); the screen is read from the structured frame
(panel.frame, evaluator).  Themes: the eight key procedures of the menu survey (select a programme with the door open, close the door and start,
stop a cycle, enter the main menu as Admin, change the sterilisation time of Custom 1, set date and time, programme a delayed start, export the
history to USB); system parameters and the screen saver; history list, printer functions and version information; new user and wrong code;
panel = direct commands; refusals as messages; power off; render determinism and the evidence PNGs; contract entries."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import tuttnauer_3870elvd as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'tuttnauer_3870elvd_01'
OUT = ROOT / 'outputs/panel_ui/tuttnauer_3870elvd'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['Select a programme', 'Close the door and start a cycle', 'Stop a cycle', 'Enter the main menu as Admin', 'Change sterilisation time of Custom 1',
                'Set date and time', 'Programme a delayed start', 'Export cycle history to USB']
DONE_TASKS = set()


class Rig:
    def __init__(self, power=True):
        self.h = rt.TuttnauerElvdRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        if power:
            self.press('main_switch')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.t
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        assert self.h.t == t0, cap
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def w(self, wid=None):
        ws = {x['id']: x for x in self.frame()['displays']['screen']['widgets']}
        return ws if wid is None else ws[wid]['text']

    def var(self, name):
        return self.frame()['ui']['vars'][name]

    def press(self, key, held=()):
        return self.call(self.A, 'panel.press', input_id=key, held=list(held))['accepted']

    def chord(self):
        return self.press('down', held=['up'])

    def selected(self):
        return [x['text'] for x in self.frame()['displays']['screen']['widgets'] if x.get('fill') == 'selection']

    def pick(self, text):
        """Move the list cursor to the line with this text and press START/STOP."""
        rows = [x['text'] for x in self.frame()['displays']['screen']['widgets'] if x['id'].startswith(('row', 'u'))]
        assert text in rows, (text, rows)
        for _ in range(40):
            if text in self.selected():
                return self.press('start_stop')
            self.press('down' if rows.index(text) > rows.index(self.selected()[0]) else 'up')
        raise AssertionError(text)

    def login(self, code='0001'):
        self.chord()
        assert self.screen() == 'select_user'
        self.pick('Admin')
        for i, d in enumerate(reversed(code)):                                 # right digit first, START/STOP moves the cursor left
            for _ in range(int(d)):
                self.press('up')
            self.press('start_stop')
        assert self.var('cf') == 4                                              # on Set
        self.press('up')

    def set_value(self, target, step):
        """CONFIGURE PARAMETER: UP / DOWN to the value, START/STOP to Set, UP to confirm."""
        assert self.screen() == 'param'
        for _ in range(400):
            v = self.var('pv')
            if abs(v - target) < 1e-9:
                break
            self.press('up' if v < target else 'down')
        self.press('start_stop')
        self.press('up')

    def open_door(self):
        self.press('up')
        self.h.step(3.0)

    def close_door(self):
        self.call(self.A, 'lid.set', open=False)                               # push the lid down (robot)
        self.h.step(3.0)


def done(task):
    DONE_TASKS.add(task)


class KeyProcedures(unittest.TestCase):
    def test_select_a_programme(self):
        r = Rig()
        self.assertEqual((r.w('m_status'), r.w('m_sub')), ('System Ready', 'Press ^ to open'))
        self.assertFalse(r.press('down'))                                      # door closed: no programme change
        r.press('up')                                                          # UP opens the door (electric lock)
        self.assertTrue(r.val('lid.angle_rad') == 0.0 and r.h.lid_open)
        r.h.step(1.0)
        self.assertEqual(r.w('m_status'), 'Wait... Door Opening')
        r.h.step(2.0)
        self.assertEqual(r.w('m_door'), 'DOOR')
        r.press('up')
        self.assertEqual((r.val('program.current')['program'], r.w('m_head')), ('unwrapped_instruments', 'Unwrapped instruments'))
        self.assertEqual((r.w('m_tv'), r.w('m_mv')), ('134.0 C', '4.0 min'))
        r.press('down')
        self.assertEqual(r.w('m_head'), 'Liquid B')
        r.press('down')
        self.assertEqual((r.w('m_head'), r.w('m_mv')), ('Liquid A', '20.0 min'))
        done('Select a programme')

    def test_close_door_start_and_stop(self):
        r = Rig()
        r.open_door()
        r.press('up')                                                          # 1 Unwrapped instruments
        r.close_door()
        self.assertEqual(r.w('m_status'), 'System Ready')
        r.press('start_stop')
        self.assertEqual(r.val('cycle.status')['stage'], 'water_inlet')
        self.assertEqual(r.w('m_status'), 'Insert water')
        r.h.step(30.0)
        self.assertIn(r.w('m_status'), ('Pulse H', 'Pulse L'))
        self.assertEqual(r.w('m_head'), 'Unwrapped instruments')
        self.assertFalse(r.press('up'))                                        # running: the door stays shut
        done('Close the door and start a cycle')
        r.press('start_stop')                                                  # stop
        self.assertEqual(r.val('cycle.status')['result'], 'CYCLE FAIL')
        t0 = r.h.t
        while r.val('cycle.status')['stage'] not in ('complete', 'failed') and r.h.t - t0 < 3600:
            r.h.step(10.0)
        self.assertEqual((r.w('m_status'), r.w('m_sub')), ('Cycle failed', 'Cancelled by user'))
        r.press('start_stop')                                                  # acknowledge
        self.assertEqual(r.w('m_status'), 'System Ready')
        self.assertEqual(r.val('records.stored'), 1)
        done('Stop a cycle')

    def test_admin_menu_and_custom_1_time(self):
        r = Rig()
        r.open_door()
        for _ in range(7):
            r.press('up')                                                      # 7 Custom 1
        self.assertEqual(r.w('m_head'), 'Custom 1')
        r.close_door()
        r.login()
        self.assertEqual(r.screen(), 'main_menu')
        self.assertEqual(r.selected(), ['Cycle parameters (Custom 1)'])
        done('Enter the main menu as Admin')
        r.pick('Cycle parameters (Custom 1)')
        r.pick('Sterilization')
        r.pick('Sterilization time')
        self.assertEqual((r.w('p_name'), r.w('p_value')), ('Sterilization time', '4.0 min'))
        r.set_value(6.5, 0.5)
        self.assertEqual(r.val('program.current')['time_s'], 390.0)
        self.assertEqual(r.screen(), 'd_ster')
        r.pick('FoMode enabled')
        r.set_value(1, 1)
        self.assertTrue(r.val('program.parameters')['f0_mode'])
        r.pick('Exit')
        r.pick('Create pulse')
        r.pick('Pulse A count')
        r.press('start_stop')
        r.press('start_stop')                                                  # Exit: nothing written
        r.press('up')
        self.assertEqual((r.screen(), r.val('program.parameters')['pulse_a_count']), ('d_pulse', 3))
        r.chord()                                                              # UP + DOWN together leaves the directory
        self.assertEqual(r.screen(), 'cycle_params')
        r.pick('Cooling')
        r.pick('Cooling mode')
        r.set_value(2, 1)
        self.assertEqual(r.w('message'), 'Not available for this model')
        self.assertEqual((r.screen(), r.val('program.parameters')['exhaust']), ('param', 'fast'))   # refused: the value screen stays
        for _ in range(4):
            r.chord()
        self.assertEqual(r.screen(), 'main')
        self.assertEqual(r.w('m_mv'), '6.5 min')
        done('Change sterilisation time of Custom 1')

    def test_set_date_and_time(self):
        r = Rig()
        r.login()
        r.pick('Maintenance')
        r.pick('Set date and time')
        self.assertEqual((r.var('dtf'), r.w('dt_h'), r.w('dt_s')), (2, '08', '00'))
        r.press('up')                                                          # seconds 01
        r.press('start_stop')
        r.press('down')                                                        # day 18
        for _ in range(3):
            r.press('start_stop')                                              # month, year, Set
        self.assertEqual(r.var('dtf'), 6)
        r.press('up')
        self.assertEqual(r.val('clock.wall'), '2026-10-18 08:00:01')
        self.assertEqual(r.screen(), 'datetime')
        r.press('start_stop')
        r.press('up')                                                          # Exit
        self.assertEqual(r.screen(), 'maintenance')
        done('Set date and time')

    def test_programme_a_delayed_start(self):
        r = Rig()
        r.login()
        r.pick('Advanced options')
        r.pick('Start timed cycle')
        self.assertEqual((r.var('tmf'), r.w('tm_h'), r.w('tm_m')), (1, '08', '00'))
        r.press('up')
        r.press('up')                                                          # 08:02
        r.press('start_stop')
        r.press('up')                                                          # Enabled
        self.assertEqual(r.val('timer.setting'), dict(enabled=True, time='08:02', due=False))
        self.assertEqual(r.w('tm_state'), 'Timed cycle enabled: 08:02')
        for _ in range(2):
            r.press('start_stop')
        r.press('up')                                                          # Exit
        r.chord()
        r.chord()
        self.assertEqual(r.screen(), 'main')
        self.assertEqual(r.w('m_timer'), 'Timed cycle 08:02')
        r.press('start_stop')
        self.assertEqual(r.w('message'), 'Timed cycle: the cycle starts at the set time')
        self.assertIsNone(r.val('cycle.status')['cycle_number'])
        r.h.step(150.0)
        self.assertEqual(r.val('cycle.status')['cycle_number'], 1)
        self.assertFalse(r.val('timer.setting')['enabled'])
        done('Programme a delayed start')

    def test_export_history_to_usb(self):
        r = Rig()
        r.press('start_stop')
        r.h.step(60.0)
        r.press('start_stop')
        r.h.step(1800.0)
        r.press('start_stop')
        r.login()
        r.pick('History')
        r.pick('Export history to USB')
        self.assertEqual(r.screen(), 'info')
        self.assertIn('Export to USB completed.', r.w('i_text'))
        f = r.val('usb.files')[-1]
        self.assertEqual((f['content'], f['entries']), ('history', 1))
        r.press('start_stop')
        self.assertEqual(r.screen(), 'history')
        done('Export cycle history to USB')


class Pages(unittest.TestCase):
    def test_system_parameters_and_screen_saver(self):
        r = Rig()
        r.login()
        r.pick('System parameters')
        r.pick('Screensaver')
        self.assertEqual(r.w('p_value'), '90 min')
        r.set_value(2, 1)
        self.assertEqual(r.val('settings.system')['screensaver_s'], 120.0)
        r.pick('Total print rate')
        r.set_value(5, 1)
        self.assertEqual(r.val('settings.system')['print_rate_total_s'], 300.0)
        r.chord()
        r.chord()
        self.assertEqual(r.screen(), 'main')
        r.h.step(119.0)
        self.assertEqual(r.screen(), 'main')
        r.h.step(2.0)
        self.assertEqual(r.screen(), 'saver')
        self.assertEqual(int(r.h.panel.render('screen').max()), 0)
        self.assertTrue(r.press('down'))                                        # any key wakes the screen (and does nothing else)
        self.assertEqual(r.screen(), 'main')

    def test_history_printer_version_users(self):
        r = Rig()
        r.press('start_stop')
        r.h.step(30.0)
        r.press('start_stop')
        r.h.step(1800.0)
        r.press('start_stop')
        r.login()
        r.pick('History')
        r.pick('Show history of old cycles')
        self.assertTrue(r.w('h0').startswith('No.1  26-10-19 08:00  Liquids 121 (ELV)'))
        self.assertTrue(r.w('h0').endswith('FAIL'))
        r.press('start_stop')
        self.assertIn('No printer connected.', r.w('i_text'))
        self.assertIn('optional printer', r.frame()['ui']['unsupported'])
        r.press('start_stop')
        r.press('down')
        r.press('start_stop')                                                  # Exit
        r.pick('Exit')
        r.pick('Maintenance')
        r.pick('Printer test')
        self.assertIn('No printer connected.', r.w('i_text'))
        r.press('start_stop')
        r.pick('Export gain and offset to USB')
        self.assertEqual(r.val('usb.files')[-1]['entries'], 4)
        r.press('start_stop')
        r.pick('Exit')
        r.pick('Version information')
        r.pick('Show current version information')
        self.assertEqual(r.w('v4'), 'OS    Microsoft Windows CE 6.0')
        r.press('start_stop')
        r.chord()
        r.pick('Exit')
        self.assertEqual(r.screen(), 'main')
        r.chord()
        r.pick('Create new...')
        self.assertEqual(r.w('title'), 'ENTER CODE (USER: NEW USER)')
        r.press('up')
        for _ in range(4):
            r.press('start_stop')
        r.press('up')                                                          # code 0001 is Admin's
        self.assertEqual(r.w('message'), 'Code already in use')
        r.press('start_stop')
        r.press('start_stop')
        r.press('up')                                                          # 0002
        for _ in range(4):
            r.press('start_stop')
        r.press('up')
        self.assertEqual((r.screen(), r.val('users.names')), ('select_user', ['Admin', 'User 1']))
        r.pick('User 1')
        r.press('up')
        r.press('up')
        for _ in range(4):
            r.press('start_stop')
        r.press('up')
        self.assertEqual(r.screen(), 'main_menu')

    def test_wrong_code_and_non_custom_cycle_parameters(self):
        r = Rig()
        r.chord()
        r.pick('Admin')
        for _ in range(4):
            r.press('start_stop')
        r.press('up')                                                          # 0000
        self.assertEqual((r.screen(), r.w('message')), ('enter_code', 'Wrong code'))
        r.press('start_stop')
        r.press('up')                                                          # Exit
        self.assertEqual(r.screen(), 'select_user')
        r.chord()
        r.login()
        r.pick('Cycle parameters (Liquids 121 (ELV))')
        self.assertEqual((r.screen(), r.w('message')), ('main_menu', 'Cycle parameters: Custom 1 and Custom 2 only'))


class EquivalenceAndRules(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        p, d = Rig(), Rig()
        p.open_door()
        for _ in range(8):
            p.press('up')                                                      # 8 Custom 2
        p.close_door()
        p.login()
        p.pick('Cycle parameters (Custom 2)')
        p.pick('End')
        p.pick('Multiple cycles')
        p.set_value(2, 1)
        p.chord()
        p.chord()
        p.chord()
        p.press('start_stop')
        d.call(d.A, 'lid.set', open=True)
        d.h.step(3.0)
        d.call(d.A, 'program.select', program='custom_2')
        d.call(d.A, 'program.set_parameters', program='custom_2', repeat_cycles=2)
        d.call(d.A, 'lid.set', open=False)
        d.h.step(3.0)
        d.call(d.A, 'cycle.start')
        self.assertEqual(p.h.read('evaluator'), d.h.read('evaluator'))
        p.h.step(600.0)
        d.h.step(600.0)
        self.assertEqual(p.h.read('evaluator'), d.h.read('evaluator'))

    def test_refusals_and_power(self):
        r = Rig()
        r.press('start_stop')
        r.h.step(120.0)
        r.press('start_stop')                                                  # stop: exhaust under pressure
        self.assertTrue(r.val('lid.locked'))
        r.press('start_stop')                                                  # still venting: START/STOP stops nothing more
        r.h.step(4000.0)
        r.press('start_stop')                                                  # acknowledge
        r.call(r.A, 'lid.set', open=True)
        r.h.step(3.0)
        r.press('start_stop')
        self.assertEqual(r.w('message'), 'Door open. Close the door')
        r.press('main_switch')
        self.assertEqual(r.screen(), 'off')
        self.assertFalse(r.press('up'))
        self.assertEqual(int(r.h.panel.render('screen').max()), 0)
        self.assertTrue(r.press('main_switch'))
        self.assertEqual(r.screen(), 'main')
        self.assertEqual(r.call(r.A, 'panel.press', expect='rejected', input_id='nope')['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_another_agent_cannot_stop_the_cycle(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.close_door()
        r.press('start_stop')                                                  # A starts on the panel: the cycle is A's
        r.h.step(30.0)
        before = r.val('cycle.status')
        self.assertFalse(r.call(B, 'panel.press', input_id='start_stop')['accepted'])   # B's STOP: refused
        self.assertEqual(r.w('message'), 'Cycle started by another user')               # shown on the message line for 3 s (text ours)
        self.assertEqual(r.val('cycle.status'), before)
        r.h.step(3.5)
        self.assertEqual(r.w('message'), '')
        before = r.val('cycle.status')
        for cap in ('cycle.stop', 'cycle.toggle'):
            self.assertEqual(r.call(B, cap, expect='rejected')['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.val('cycle.status'), before)
        self.assertTrue(r.val('power.on'))
        r.press('start_stop')                                                  # A stops its own cycle
        self.assertEqual(r.val('cycle.status')['result'], 'CYCLE FAIL')
        r.h.step(3600.0)
        r.press('start_stop')                                                  # A acknowledges and starts again
        r.press('start_stop')
        r.h.step(30.0)
        self.assertIsNone(r.val('cycle.status')['result'])
        self.assertTrue(r.call(B, 'panel.press', input_id='main_switch')['accepted'])   # the main switch is a hard switch: never refused,
        self.assertFalse(r.val('power.on'))                                             # a power failure for A's cycle
        self.assertEqual(r.h.cycle['failure'], 'POWER_FAIL')
        with self.assertRaises(rt.ActionRejected):
            r.A.poll('x')

    def test_locked_door_message(self):
        r = Rig()
        r.press('start_stop')
        r.h.step(300.0)
        r.press('start_stop')
        r.h.step(1.0)
        self.assertFalse(r.press('up'))                                        # cycle still ending (exhaust): no door opening
        self.assertTrue(r.val('lid.locked'))


class Operations(unittest.TestCase):
    """Final sync 2026-10-19 (rule 1): every cycle is an operation of the session that started it (cycle.start, START on the panel, a timed start
    of the session that set the timer, repeats); the starter polls and cancels it, other agents may not."""

    def test_direct_start_returns_an_operation(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        r.close_door()
        res = r.A.invoke(instance_id=INST, capability_id='cycle.start', arguments={}, request_id='s1')
        self.assertEqual(res['status'], 'accepted')
        op = res['operation_id']
        self.assertEqual((res['result']['operation_id'], res['result']['cycle_number']), (op, 1))      # the immediate fields stay
        self.assertEqual(r.val('cycle.status')['operation_id'], op)
        r.h.step(30.0)
        self.assertEqual(r.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        with self.assertRaises(rt.ActionRejected):
            B.cancel(op, 'b1')
        self.assertEqual(r.A.cancel(op, 'c1')['status'], 'succeeded')
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')

    def test_panel_start_belongs_to_the_caller(self):
        r = Rig()
        r.close_door()
        r.press('start_stop')
        op = r.val('cycle.status')['operation_id']
        self.assertEqual(r.h._op_owners[op], 'agent')
        r.h.step(4 * 3600.0)
        self.assertIn(r.A.poll(op)['status'], ('succeeded', 'failed'))
        self.assertEqual(r.A.poll(op)['result']['operation_id'], op)


class DocumentedMessages(unittest.TestCase):
    """Displayed messages of the manual's tables (p.54, p.81-84) without a physical cause in the simulation: evaluator-only fault.inject msg_*."""

    def test_cycle_message_fails_the_running_cycle(self):
        r = Rig()
        self.assertEqual(r.call(r.E, 'fault.inject', expect='rejected', fault='msg_air_error', active=True)['error']['code'], 'NO_CYCLE')
        r.close_door()
        r.press('start_stop')
        r.h.step(30.0)
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', fault='msg_air_error', active=True)['error']['code'], 'ACCESS_DENIED')
        r.call(r.E, 'fault.inject', fault='msg_air_error', active=True)
        self.assertEqual(r.val('cycle.status')['result'], 'CYCLE FAIL')
        r.h.step(3600.0)
        self.assertEqual(r.w('m_sub'), 'Air error')                            # Cycle failed + the message until START/STOP
        self.assertTrue(r.press('start_stop'))

    def test_service_message_is_latched_and_blocks_starts(self):
        r = Rig()
        r.close_door()
        r.call(r.E, 'fault.inject', fault='msg_io_board_failure', active=True)
        self.assertEqual(r.w('m_sub'), 'I/O board failure')
        self.assertIn('MSG_IO_BOARD_FAILURE', r.val('alarms.active'))
        before = r.val('cycle.status')
        self.assertFalse(r.press('start_stop'))
        self.assertEqual(r.w('message'), 'Call service')
        self.assertEqual(r.call(r.A, 'cycle.start', expect='rejected')['error']['code'], 'SERVICE_REQUIRED')
        self.assertEqual(r.val('cycle.status'), before)
        r.call(r.E, 'fault.inject', fault='msg_io_board_failure', active=False)   # serviced
        self.assertNotEqual(r.w('m_sub'), 'I/O board failure')
        self.assertTrue(r.press('start_stop'))
        r.h.step(30.0)
        r.call(r.E, 'fault.inject', fault='msg_chamber_pressure_range', active=True)   # during a cycle: the cycle fails
        self.assertEqual(r.val('cycle.status')['result'], 'CYCLE FAIL')

    def test_notices_are_shown_and_do_not_block(self):
        r = Rig()
        r.call(r.E, 'fault.inject', fault='msg_maintenance_due', active=True)
        self.assertEqual(r.w('m_sub'), 'Maintenance date passed - call service')
        r.call(r.E, 'fault.inject', fault='msg_maintenance_due', active=False)
        r.call(r.E, 'fault.inject', fault='msg_rtc_set', active=True)
        self.assertEqual(r.w('m_sub'), 'RTC error - set date and time')
        r.call(r.A, 'clock.set', date='19:10:26', time='12:00:00')               # the remedy of p.83
        self.assertNotEqual(r.w('m_sub'), 'RTC error - set date and time')
        r.close_door()
        self.assertTrue(r.press('start_stop'))


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        r = Rig()
        r.press('start_stop')
        r.h.step(600.0)
        a = r.h.panel.png_bytes('screen')[0]
        self.assertEqual(a, r.h.panel.png_bytes('screen')[0])
        self.assertEqual(copy.deepcopy(r.h).panel.png_bytes('screen')[0], a)
        OUT.mkdir(parents=True, exist_ok=True)
        renders = {'home': r.h.panel.save_png('screen', OUT / 'home.png')}
        r.login()
        renders['menu'] = r.h.panel.save_png('screen', OUT / 'menu.png')
        r.pick('System parameters')
        r.pick('Screensaver')
        renders['parameter'] = r.h.panel.save_png('screen', OUT / 'parameter.png')
        r.chord()
        r.chord()
        r.chord()
        r.press('start_stop')
        r.h.step(4000.0)
        renders['error'] = r.h.panel.save_png('screen', OUT / 'error.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')

    def test_capture_through_the_session(self):
        r = Rig()
        cap = r.call(r.A, 'panel.capture', display_id='screen')
        self.assertEqual((cap['image']['width'], cap['image']['height']), (480, 272))

    def test_spec_and_contract(self):
        spec = json.loads((rt.ASSET_DIR / 'panel_ui.json').read_text())
        self.assertEqual(pu.check_spec(spec, set(rt.CAPS)), [])
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + rt.APP_CAPS + pu.spec_capabilities(spec):
            self.assertIn(k, c['capabilities'])
        self.assertIn('button_down', c['components'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        self.assertEqual(sorted(set(SURVEY_TASKS) - DONE_TASKS), [], 'run the whole module')


if __name__ == '__main__':
    unittest.main()
