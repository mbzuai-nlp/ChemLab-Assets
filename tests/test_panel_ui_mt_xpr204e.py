"""front-panel tests for CHEM-001 mt_xpr204e (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_measure.py):
python3 -m pytest tests/test_panel_ui_mt_xpr204e.py -q

Every gesture goes through the session API (panel.press / panel.touch, the runtime's normal invoke path); widgets are found by id or text in the
structured frame (panel.frame, evaluator) and tapped at their centre.  Themes: the eight key procedures of the menu survey; the terminal
functions (settings, tolerance profiles, doors, units, methods and tasks, results, routine tests and adjustments as procedures, users and login,
blocking, date / time / language / keyboard layout, standby, leveling aid, messages, reset); panel = direct capability (same runtime state);
refusals shown as the device's own message; time-outs; render determinism and the evidence PNGs; every terminal command's output and declared
effects; contract entries."""
import copy
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
import basic40_runtime_measure as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

G = 9.80665
INST = 'xpr'
OUT = ROOT / 'outputs/panel_ui/mt_xpr204e'
CONTRACT = rt.XPR204E_ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['zero the balance', 'tare a sample vessel', 'weigh and record a result', 'perform an internal adjustment', 'level the balance',
                'create and run a General weighing method', 'enter / exit standby', 'external adjustment with test weight']
DONE_TASKS = set()
TERM = 'terminal'


class Rig:
    """A runtime with one balance, an agent and an evaluator session; gestures only through panel.* capabilities."""

    def __init__(self, scene=True):
        self.scene_calls = []
        self.h = rt.XPR204ERuntime(scene_adapter=(lambda i, u: self.scene_calls.append(copy.deepcopy(u))) if scene else None)
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.mass = 0.0
        self.call(self.A, 'power.set', enabled=True)
        self.step(12)

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap                                          # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt, mass=None):
        if mass is not None:
            self.mass = mass
        t = 0.0
        while t < dt - 1e-9:
            d = min(10.0, dt - t)
            self.h.step(d, pan_load_forces_N={INST: self.mass * G})
            t += d

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][TERM]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TERM, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def tap_text(self, text):
        """Tap the (first) widget whose text is exactly this."""
        for w in self.disp()['widgets']:
            if w['text'] == text and w['type'] == 'button':
                x, y, ww, hh = w['rect']
                return self.touch(x + ww // 2, y + hh // 2)
        raise AssertionError(f'{text!r} not on {self.page()}: {[w["text"] for w in self.disp()["widgets"]]}')

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        """Tap a button of the top overlay (keyboard key, option, dialog button) by its label."""
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def row(self, wid, first):
        """Tap the row of a list or form widget whose first cell is this text (dragging the rows until it shows)."""
        for _ in range(12):
            w = self.widgets()[wid]
            firsts = [c[0] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            down = w['scroll'] + len(w['cells']) < w['count']
            assert down, (first, firsts)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def cells(self, wid):
        w = self.widgets()[wid]
        return {c[0]: (c[1] if len(c) > 1 else None) for c in w['cells']}

    def type(self, text):
        """Type on the open keyboard: the shown text is replaced; upper-case letters through Shift, digits through 12@ on the alpha keyboard."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard'
        labels = {b['label'] for b in ov['buttons']}
        if '7' in labels and 'q' not in labels and 'Q' not in labels:           # numeric keyboard
            if ov['field']:
                self.ov('Del')
            for ch in text:
                assert self.ov(ch), ch
            return
        if ov['field']:
            self.ov('Del')
            if self.overlay()['field']:
                for _ in range(len(self.overlay()['field'])):
                    self.ov('Del')
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit() or ch in '$@#%&/():;_+=\'"!?,':
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            elif ch.isupper():
                if mode == 'sym':
                    self.ov('abc')
                if self.overlay()['mode'] != 'upper':
                    self.ov('Shift')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if self.overlay()['mode'] == 'upper':
                    self.ov('Shift')
                self.ov(ch)
        assert self.overlay()['field'] in (text, '*' * len(text)), (self.overlay()['field'], text)   # passwords are masked

    def enter(self, wid, first, text, confirm='Confirm'):
        self.row(wid, first)
        self.type(text)
        return self.ov(confirm)

    def choose(self, wid, first, option):
        self.row(wid, first)
        return self.ov(option)

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def home(self):
        return self.press('key_home')

    def png(self):
        r = self.call(self.A, 'panel.capture', display_id=TERM)
        return Path(r['image']['path']).read_bytes()

    def pixels(self):
        return self.h.panel(INST).render(TERM)


def settled(r, mass, seconds=15):
    r.step(seconds, mass)


class SurveyProcedures(unittest.TestCase):
    """The eight key procedures of the menu survey, done only with panel gestures."""

    def test_zero_the_balance(self):
        r = Rig()
        settled(r, 0.0013)                                                      # something left on the pan
        self.assertEqual(r.text('w_value'), '1.3000')
        self.assertTrue(r.press('key_door_right'))                              # 1 open the draft shield
        self.assertAlmostEqual(r.val('door.right.opening_m'), rt.XPR204E_DOOR_MAX_M)
        r.step(2, 0.0)                                                          # 2 clear the weighing pan
        self.assertTrue(r.press('key_door_right'))                              # 3 close the draft shield
        self.assertEqual(r.val('door.right.opening_m'), 0.0)
        self.assertTrue(r.press('key_zero'))                                    # 4 Zero: not yet stable -> Zeroing... until stable
        self.assertEqual((r.val('weighing.pending'), r.text('w_busy')), ('zero', 'Zeroing...'))
        r.step(15)
        self.assertEqual((r.val('weighing.pending'), r.text('w_value'), r.val('balance.net_kg')), (None, '0.0000', 0.0))
        self.assertEqual(r.pixels().shape, (480, 800, 3))
        DONE_TASKS.add('zero the balance')

    def test_tare_a_sample_vessel(self):
        r = Rig()
        r.press('key_zero')
        r.press('key_door_right')
        r.step(3, 0.0251234)                                                    # place the vessel
        r.press('key_door_right')
        self.assertTrue(r.press('key_tare'))
        self.assertEqual(r.text('w_busy'), 'Taring...')
        r.step(15)
        w = r.widgets()
        self.assertEqual((w['w_value']['text'], 'w_net' in w), ('0.0000', True))   # the Net icon appears
        self.assertAlmostEqual(r.val('balance.tare_kg'), 0.0251234, places=7)
        DONE_TASKS.add('tare a sample vessel')

    def test_weigh_and_record_a_result(self):
        r = Rig()
        r.press('key_zero')
        r.press('key_door_right')
        r.step(2, 0.0102345)
        r.press('key_door_right')
        r.step(15)
        self.assertTrue(r.tap('act_add'))                                       # Add result
        self.assertEqual(r.cells('res_list'), {'10.2345 g': None})
        self.assertTrue(r.tap('act_complete'))                                  # Complete: the Complete task dialog
        self.assertEqual(r.overlay()['title'], 'Complete task')
        self.assertFalse(r.ov('Print results manually'))                        # no printer: the device's message
        self.assertEqual(r.overlay()['lines'], ['No printer is connected.'])
        r.ov('OK')
        self.assertTrue(r.ov('Complete'))
        self.assertEqual((r.val('task.current')['results'], len(r.val('task.completed'))), ([], 1))
        self.assertEqual(r.val('task.completed')[0]['results'][0]['text'], '10.2345')
        DONE_TASKS.add('weigh and record a result')

    def test_perform_an_internal_adjustment(self):
        r = Rig()
        r.h.set_sensor_errors(INST, span_error_rel=1e-4)
        settled(r, 0.1)
        self.assertEqual(r.text('w_value'), '100.0100')                         # span error before the adjustment
        r.tap('act_more')
        self.assertTrue(r.ov('Start adjustment'))                               # More > Start adjustment
        self.assertEqual(r.page(), ('proc', 'proc'))
        self.assertEqual(r.disp()['title'], 'Internal adjustment')
        self.assertIn('being executed', r.text('pr_text'))
        r.step(25)
        self.assertEqual(r.text('pr_result'), 'Result: Done')                   # results overview
        self.assertFalse(r.tap('pr_print'))                                     # Print: no printer
        r.ov('OK')
        self.assertTrue(r.tap('pr_complete'))                                   # Complete
        self.assertEqual(r.page(), ('gui', 'main'))
        r.step(15)
        self.assertEqual(r.text('w_value'), '100.0000')                         # the stored correction is applied
        self.assertAlmostEqual(r.val('adjustment.last')['correction_rel'], 1e-4, places=9)
        self.assertEqual(r.val('history.adjustments')[-1]['type'], 'Internal adjustment')
        DONE_TASKS.add('perform an internal adjustment')

    def test_level_the_balance(self):
        r = Rig()
        r.h.set_environment(INST, tilt_rad=math.radians(0.5), tilt_direction_rad=math.pi / 2)
        r.step(1)
        self.assertEqual(r.text('msg_line'), 'Balance is out of level')
        self.assertEqual(r.widgets()['st_level']['color'], 'error')
        self.assertEqual(r.val('device.status_light'), 'yellow')
        r.tap('msg_line')
        r.row('msg_list', 'Warning')
        self.assertTrue(r.ov('Level the balance'))                              # 1 tap Level the balance: the leveling aid
        self.assertEqual(r.page(), ('gui', 'leveling'))
        w = r.widgets()
        self.assertGreater(w['lv_bubble']['xy'][0], 0.5)
        self.assertIn('leveling foot', w['lv_text']['text'])
        r.h.set_environment(INST, tilt_rad=0.0)                                 # 2 turn the feet (the host moves the support)
        r.step(1)
        w = r.widgets()
        self.assertEqual((w['lv_bubble']['xy'], w['lv_text']['text']), ([0.0, 0.0], 'The balance is leveled.'))
        self.assertEqual((r.val('device.messages'), r.val('device.status_light')), ([], 'green'))
        DONE_TASKS.add('level the balance')

    def test_create_and_run_a_general_weighing_method(self):
        r = Rig()
        r.tap('tab_methods')
        r.tap('ml_new')                                                         # New: the method wizard
        self.assertEqual(r.page()[1], 'mw_type')
        r.choose('f_method_new', 'Method type', 'General weighing')
        r.tap('mw_type_next')
        r.enter('f_method_new', 'Method name', 'my method')
        r.enter('f_method_new', 'Result description', 'Vial')
        r.tap('mw_ident_next')
        r.choose('f_method_new', 'Tolerance profile', 'Default profile')
        r.tap('mw_config_next')
        self.assertEqual(r.page()[1], 'mw_save')                                # General weighing: 4. Save
        self.assertTrue(r.widgets()['mw_s4']['selected'])
        r.tap('mw_finish')
        methods = {m['name']: m for m in r.val('methods.list')}
        self.assertEqual(methods['my method']['settings']['ids.result_1_description'], 'Vial')
        r.row('ml_list', 'my method')
        r.tap('ml_start')                                                       # Start method
        self.assertEqual((r.page(), r.text('st_method')), (('gui', 'main'), 'my method'))
        self.assertIn('Vial', r.cells('ids_rows'))
        r.press('key_zero')
        r.step(2, 0.0050001)
        r.tap('act_add')
        self.assertTrue(r.val('weighing.pending') is None or r.val('weighing.pending') == 'capture')
        r.step(15)
        self.assertEqual([x['text'] for x in r.val('task.current')['results']], ['5.0001'])
        r.tap('act_complete')
        r.ov('Complete')
        self.assertEqual(r.val('task.completed')[-1]['method_id'], methods['my method']['id'])
        DONE_TASKS.add('create and run a General weighing method')

    def test_enter_and_exit_standby(self):
        r = Rig()
        self.assertTrue(r.press('key_standby', hold=2.0))                       # 1 hold: standby, the display is dark
        self.assertTrue(r.val('device.standby'))
        self.assertEqual(int(r.pixels().max()), 0)
        self.assertEqual(r.page()[0], 'dark')
        self.assertFalse(r.press('key_zero'))                                   # only the standby key reacts
        self.assertFalse(r.touch(400, 240))
        self.assertTrue(r.val('power.enabled'))                                 # the balance stays on
        self.assertTrue(r.press('key_standby'))                                 # 2 press: the display is on again
        self.assertFalse(r.val('device.standby'))
        self.assertEqual(r.page(), ('gui', 'main'))
        self.assertFalse(r.press('key_standby'))                                # a short press while on does nothing (hold is needed)
        DONE_TASKS.add('enter / exit standby')

    def test_external_adjustment_with_a_test_weight(self):
        r = Rig()
        r.h.set_sensor_errors(INST, span_error_rel=-2e-4)
        define_weight(r, '200 g F2', 'W200', '200', 'F2')
        r.home()
        r.tap('tab_methods')
        r.tap('mp_adjustments')
        r.tap('ad_edit')                                                        # Adjustments > Edit > Strategy: External adjustment
        r.choose('f_adj', 'Strategy', 'External adjustment')
        r.choose('f_adj', 'Test weights', '200 g F2')
        r.tap('adj_save')
        self.assertEqual(r.val('adjustment.settings')['strategy'], 'External adjustment')
        r.tap('ad_start')                                                       # Start
        self.assertEqual(r.page(), ('proc', 'proc'))
        r.tap('pr_ok')                                                          # pan empty and clean, tools at hand: OK
        r.tap('pr_ok')                                                          # pan empty: OK -> zeroing
        r.step(10, 0.0)
        self.assertEqual(r.val('procedure.state')['step'], 'choose')
        r.row('pr_choices', '200 g F2')                                         # choose the test weight, OK
        r.tap('pr_ok')
        r.press('key_door_right')                                               # open the door and place the weight
        r.step(2, 0.2)
        self.assertEqual(r.val('door.right.opening_m'), 0.0)                    # the door closes and the adjustment starts
        r.step(20)
        self.assertEqual(r.val('procedure.state')['step'], 'prompt')            # remove the weight and tap OK
        r.step(15, 0.0)
        r.tap('pr_ok')
        r.step(10)
        self.assertEqual(r.text('pr_result'), 'Result: Done')
        r.tap('pr_complete')
        r.press('key_door_right')                                               # the adjustment opened the door again: close it
        settled(r, 0.2, 25)
        self.assertEqual(r.text('w_value'), '200.0000')                         # the correction is applied
        self.assertEqual(r.val('history.adjustments')[-1]['type'], 'External adjustment')
        DONE_TASKS.add('external adjustment with test weight')


def open_section(r, *path):
    """Balance menu > Settings > ... by the button texts."""
    r.home()
    r.tap('tab_balance')
    for label in path:
        assert r.tap_text(label), label


class TerminalFunctions(unittest.TestCase):
    def test_units_readability_environment_value_release(self):
        r = Rig()
        settled(r, 0.0123456)
        r.tap('act_more')
        r.ov('Change display unit')
        r.ov('Display unit')                                                    # the dialog's form row opens the choice list
        r.ov('mg')
        r.ov('Close')
        self.assertEqual((r.text('w_value'), r.text('w_unit'), r.val('balance.display_unit')), ('12345.6', 'mg', 'g'))
        open_section(r, 'Settings', 'Balance', 'Tolerance profiles')
        r.row('tp_list', 'Default')
        r.tap('tp_edit')
        r.choose('f_profile', 'Display readability', '10d')
        r.choose('f_profile', 'Environment', 'Very unstable')
        r.choose('f_profile', 'Value release', 'Very reliable')
        r.row('f_profile', 'Allowed units')
        r.ov('Deselect all'), r.ov('[ ] g'), r.ov('[ ] mg'), r.ov('[ ] ct'), r.ov('OK')
        r.tap('profile_save')
        p = r.val('profiles.list')[0]
        self.assertEqual((p['readability'], p['environment'], p['value_release'], p['units']), ('10d', 'Very unstable', 'Very reliable', ['g', 'mg', 'ct']))
        self.assertEqual(r.val('balance.filter_level'), 'High')                 # the PX class of the XPR environment
        cell = r.h._instances[INST]['balance']
        self.assertEqual((cell.environment, cell.settling_s), ('Very unstable', 3.0))
        r.home()
        settled(r, 0.0123456, 40)
        self.assertEqual(r.text('w_value'), '12346')                            # mg with 10 d = 1 mg steps
        r.call(r.A, 'display.set_unit', unit='g')
        self.assertEqual(r.text('w_value'), '12.346')
        self.assertEqual(r.call(r.A, 'display.set_unit', expect='rejected', unit='oz')['error']['code'], 'PRECONDITION_FAILED')   # not allowed

    def test_doors_drive_opening_keys_and_automatic(self):
        r = Rig()
        open_section(r, 'Settings', 'Balance', 'Doors', 'Door right')
        r.enter('f_set_door_right', 'Door opening', '50')
        r.choose('f_set_door_right', 'Door key left', 'Active')
        r.choose('f_set_door_right', 'Automatic (Tare / Zero / Result)', 'Active')
        r.tap('set_door_right_save')
        r.home()
        r.press('key_door_left')                                                # both keys now drive the right door; the left one its own
        self.assertEqual((r.val('door.left.opening_m'), r.val('door.right.opening_m')), (rt.XPR204E_DOOR_MAX_M, rt.XPR204E_DOOR_MAX_M * 0.5))
        r.press('key_door_left')
        self.assertEqual((r.val('door.left.opening_m'), r.val('door.right.opening_m')), (0.0, 0.0))
        r.press('key_door_right')
        r.step(3, 0.02)
        r.press('key_tare')                                                     # automatic: the door closes, tares when stable, opens again
        self.assertEqual(r.val('door.right.opening_m'), 0.0)
        r.step(20)
        self.assertAlmostEqual(r.val('balance.tare_kg'), 0.02, places=7)
        self.assertEqual(r.val('door.right.opening_m'), rt.XPR204E_DOOR_MAX_M * 0.5)
        open_section(r, 'Settings', 'Balance', 'Doors', 'Door left')
        r.choose('f_set_door_left', 'Drive mode', 'Manual')
        self.assertNotIn('Door opening', r.cells('f_set_door_left'))           # only for motorized doors
        r.tap('set_door_left_save')
        r.home()
        r.press('key_door_right')                                               # closes the right door only
        r.press('key_door_left')                                                # left door manual: does not move; right door opens
        self.assertEqual((r.val('door.left.opening_m'), r.val('door.right.opening_m')), (0.0, rt.XPR204E_DOOR_MAX_M * 0.5))
        r.call(r.A, 'scene.set_door', side='left', opening_m=0.05)              # opened by hand (scene)
        self.assertEqual(r.val('door.left.opening_m'), 0.05)

    def test_date_time_formats_language_keyboard(self):
        r = Rig()
        open_section(r, 'Settings', 'Balance', 'Date / Time / Language / Format')
        self.assertEqual(r.cells('f_clock'), {'Date': '1.Jan.2026', 'Time': '00:00'})
        r.row('f_clock', 'Date')
        r.ov('+') if False else None
        ov = r.overlay()
        inc_month = next(b for b in ov['buttons'] if b['id'] == 'inc:1')
        x, y, w, h = inc_month['rect']
        r.touch(x + 5, y + 5)
        r.ov('OK')
        r.row('f_clock', 'Time')
        ov = r.overlay()
        inc_hour = next(b for b in ov['buttons'] if b['id'] == 'dec:0')
        x, y, w, h = inc_hour['rect']
        r.touch(x + 5, y + 5)                                                   # 00 -> 23
        r.ov('OK')
        self.assertEqual(r.val('device.datetime')[:16], '2026-02-01T23:00')
        r.choose('f_set_region', 'Time format', '12:MM')
        r.choose('f_set_region', 'Date format', 'YYYY-MM-DD')
        r.choose('f_set_region', 'Language', 'Deutsch')
        r.choose('f_set_region', 'Keyboard layout', 'German')
        r.tap('set_region_save')
        r.home()
        w = r.widgets()
        self.assertEqual((w['st_time']['text'], w['act_add']['text'], w['tab_methods']['text']), ('11:00 PM', 'Ergebnis hinzufügen', 'Methoden'))
        self.assertEqual(r.val('device.language'), 'Deutsch')
        r.row('ids_rows', 'Sample ID')                                          # a text field: the German QWERTZ keyboard
        first_row = [b['label'] for b in r.overlay()['buttons'] if b['id'].startswith('key:')][:7]
        self.assertEqual(first_row, ['q', 'w', 'e', 'r', 't', 'z', 'u'])
        r.ov('Discard')

    def test_screen_brightness_status_light_and_key_sound(self):
        r = Rig()
        self.assertEqual(r.disp()['brightness'], 80)
        self.assertEqual(r.frame()['displays']['status_light']['color'], 'green')
        open_section(r, 'Settings', 'Balance', 'Screen / StatusLight / Sound')
        r.choose('f_set_screen', 'Screen brightness', '40 %')
        r.choose('f_set_screen', 'StatusLight', 'Active (without green light)')
        r.choose('f_set_screen', 'Sound on key press', 'Inactive')
        r.tap('set_screen_save')
        self.assertLessEqual(int(r.pixels().max()), int(255 * 0.4))
        self.assertEqual((r.val('device.status_light'), r.frame()['displays']['status_light']['on']), ('off', False))
        beeps = r.frame()['ui']['beeps']
        r.press('key_zero')
        self.assertEqual(r.frame()['ui']['beeps'], beeps)                       # no key sound
        r.call(r.A, 'settings.configure', settings={'screen.sound_key': True})
        r.press('key_zero')
        self.assertEqual(r.frame()['ui']['beeps'], beeps + 1)

    def test_users_groups_login_rights_and_logout(self):
        r = Rig()
        open_section(r, 'Settings', 'Balance', 'General')
        r.row('f_set_general', 'User management')
        r.ov('OK')                                                              # Activate user management
        r.ov('New password'), r.type('adm1'), r.ov('Confirm')
        r.ov('Confirm new password'), r.type('adm1'), r.ov('Confirm')
        r.ov('OK')
        self.assertTrue(r.val('users.state')['management'])
        r.tap('set_general_close')
        r.home()
        r.tap('tab_balance')
        r.tap('bm_users')                                                       # Users only with user management
        r.tap_text('Groups')
        r.tap('gl_new')
        r.enter('f_group', 'Group name', 'Operators')
        r.choose('f_group', 'Start routine tests', 'Active')
        r.tap('group_save')
        r.tap('gl_close')
        r.tap_text('Users')
        r.tap('ul_new')
        r.enter('f_user', 'User name', 'op')
        r.row('f_user', 'Assigned groups')
        r.ov('[x] Administrators'), r.ov('[ ] Operators'), r.ov('OK')
        r.tap('user_save')
        r.row('ul_list', 'op')
        r.tap('ul_pw')
        r.ov('New password'), r.type('op1'), r.ov('Confirm')
        r.ov('Confirm new password'), r.type('op9'), r.ov('Confirm')
        r.ov('OK')
        self.assertEqual(r.overlay()['lines'], ['The passwords do not match.'])
        r.ov('OK')
        r.ov('Confirm new password'), r.type('op1'), r.ov('Confirm')
        r.ov('OK')
        r.home()
        r.tap('tab_balance')
        r.tap('bm_exit')
        r.ov('Logout')
        self.assertEqual(r.page(), ('login', 'login'))
        r.row('lg_users', 'op')
        r.row('lg_form', 'Password'), r.type('bad'), r.ov('Confirm')
        self.assertFalse(r.tap('lg_login'))
        self.assertEqual(r.overlay()['lines'], ['Wrong user name or password.'])
        r.ov('OK')
        r.row('lg_form', 'Password'), r.type('op1'), r.ov('Confirm')
        self.assertTrue(r.tap('lg_login'))
        self.assertEqual((r.page(), r.text('st_user')), (('gui', 'main'), 'op'))
        r.tap('tab_balance')
        self.assertNotIn('bm_maintenance', r.widgets())                        # no Configure system / service rights
        r.tap('bm_settings')
        r.tap_text('Balance'), r.tap_text('General')
        r.choose('f_set_general', 'Standby', 'Inactive')
        self.assertFalse(r.tap('set_general_save'))
        self.assertEqual(r.overlay()['lines'], ['You do not have the rights for this action.'])
        r.ov('OK')
        r.press('key_standby', 2.0)                                             # standby logs the user out
        r.press('key_standby')
        self.assertEqual(r.page(), ('login', 'login'))
        self.assertFalse(r.val('users.state')['logged_in'])

    def test_block_and_unblock_with_unblocking_password(self):
        r = Rig()
        open_section(r, 'Settings', 'Balance', 'General')
        r.row('f_set_general', 'Unblocking password')
        r.ov('New password'), r.type('9999'), r.ov('Confirm')
        r.ov('Confirm new password'), r.type('9999'), r.ov('Confirm')
        r.ov('OK')
        r.home()
        r.tap('tab_balance')
        r.tap('bm_exit')
        r.ov('Block balance')
        r.ov('Unblocking password'), r.type('9999'), r.ov('Confirm')
        r.ov('Block balance')
        self.assertEqual((r.page(), r.val('device.blocked')), (('blocked', 'blocked'), True))
        r.tap('bl_cancel')                                                      # Cancel: main screen, still blocked
        self.assertEqual(r.page(), ('gui', 'main'))
        settled(r, 0.01)
        self.assertFalse(r.tap('act_add'))
        self.assertEqual(r.overlay()['lines'], ['The balance is blocked.'])
        r.ov('OK')
        r.tap('tab_balance')
        r.tap('bm_exit')
        r.ov('Unblock balance')
        r.ov('Unblocking password'), r.type('1234'), r.ov('Confirm')
        self.assertFalse(r.ov('Unblock balance'))
        self.assertEqual(r.overlay()['lines'], ['Wrong user name or password.'])
        r.ov('OK')
        self.assertEqual(r.page(), ('gui', 'balance_menu'))                     # still on the Balance menu
        r.tap('bm_exit'), r.ov('Unblock balance')
        r.ov('Unblocking password'), r.type('9999'), r.ov('Confirm')
        self.assertTrue(r.ov('Unblock balance'))
        self.assertFalse(r.val('device.blocked'))


def new_method(r, mtype, name, item_rows=()):
    """Methods > New: the wizard through to Finish."""
    r.home()
    r.tap('tab_methods')
    r.tap('ml_new')
    r.choose('f_method_new', 'Method type', mtype)
    r.tap('mw_type_next')
    r.enter('f_method_new', 'Method name', name)
    r.tap('mw_ident_next')
    r.tap('mw_config_next')
    for label, value, kind in item_rows:
        if kind == 'enter':
            r.enter('f_method_new', label, value)
        else:
            r.choose('f_method_new', label, value)
    if r.page()[1] == 'mw_item':
        r.tap('mw_item_next')
    assert r.tap('mw_finish')
    return next(m['id'] for m in r.val('methods.list') if m['name'] == name)


def start_method(r, name):
    r.home()
    r.tap('tab_methods')
    r.row('ml_list', name)
    assert r.tap('ml_start')


class MethodsTasksResults(unittest.TestCase):
    def test_ids_statistics_exclude_and_results_panel(self):
        r = Rig()
        r.tap('act_edit')                                                       # Edit (the running method): Weighing > Activate statistics
        r.tap('me_weighing_tab')
        r.choose('f_method_edit', 'Activate statistics', 'Active')
        r.tap('me_ids_tab')
        r.choose('f_method_edit', 'Result ID 1', 'Automatic counter')
        r.enter('f_method_edit', 'Prefix', 'S-')
        r.tap('method_edit_save')
        self.assertEqual(r.page(), ('gui', 'main'))
        r.enter('ids_rows', 'Task ID', 'Batch7')
        r.press('key_zero')
        for i, m in enumerate((0.0101, 0.0102, 0.0150)):
            r.step(15, m)
            self.assertEqual(r.cells('ids_rows')['Sample ID'], f'S-{i + 1}')
            r.tap('act_add')
        res = r.val('task.current')['results']
        self.assertEqual([x['result_ids'] for x in res], [['S-1'], ['S-2'], ['S-3']])
        self.assertEqual(r.val('task.current')['task_ids'], ['Batch7'])
        r.tap('tab_results')
        self.assertEqual(r.cells('rs_detail')['Result state'], 'OK')            # the latest result is selected
        r.row('rs_list', '(3) 15.0000 g')
        r.tap('rs_exclude')
        r.ov('Comment'), r.type('spilled'), r.ov('Confirm')
        r.ov('Exclude result')
        self.assertEqual((r.cells('rs_detail')['Result state'], r.val('task.current')['results'][2]['comment']), ('Excluded', 'spilled'))
        st = r.val('task.statistics')
        self.assertEqual((st['count'], round(st['average'], 6)), (2, 10.15))
        self.assertIn('n = 2', r.text('rs_stats'))

    def test_method_clone_lock_delete_and_in_use(self):
        r = Rig()
        r.tap('tab_methods')
        r.row('ml_list', 'General Weighing')
        r.tap('ml_clone')
        self.assertIn('General Weighing 1', [m['name'] for m in r.val('methods.list')])
        r.row('ml_list', 'General Weighing 1')
        r.tap('ml_edit')
        r.choose('f_method_edit', 'Lock method', 'Active')
        r.tap('method_edit_save')
        r.row('ml_list', 'General Weighing 1')
        r.tap('ml_edit')
        r.enter('f_method_edit', 'Comment', 'changed')
        self.assertFalse(r.tap('method_edit_save'))                             # locked: refused with the device message
        self.assertEqual(r.overlay()['lines'], ['The method is locked.'])
        r.ov('OK')
        r.tap('method_edit_close')
        r.row('ml_list', 'General Weighing')
        r.tap('ml_delete')
        self.assertEqual(r.overlay()['lines'], ['Delete method and cancel tasks?'])
        self.assertFalse(r.ov('OK'))                                            # the method in the background cannot be deleted
        self.assertEqual(r.overlay()['lines'], ['The item is in use and cannot be deleted.'])
        r.ov('OK')
        r.row('ml_list', 'General Weighing 1')
        r.tap('ml_delete')
        r.ov('OK')
        self.assertEqual([m['name'] for m in r.val('methods.list')], ['General Weighing'])

    def test_itemized_from_results_tasks_pause_and_continue(self):
        r = Rig()
        r.press('key_zero')
        for m in (0.005, 0.010):
            r.step(15, m)
            r.tap('act_add')
        r.tap('act_more')
        r.ov('Save as method (itemized)')
        r.ov('Method name'), r.type('two items'), r.ov('Confirm')
        r.ov('OK')
        meth = next(m for m in r.val('methods.list') if m['name'] == 'two items')
        self.assertEqual((meth['type'], [i['target'] for i in meth['items']]), ('General weighing (itemized)', [5.0, 10.0]))
        start_method(r, 'two items')                                            # the running task (2 results) is paused
        self.assertEqual(r.val('task.paused')[0]['results'], 2)
        self.assertTrue(r.widgets()['trac']['visible'] if 'visible' in r.widgets()['trac'] else True)
        self.assertEqual(r.text('trac_target'), '5 g')                          # the first item's target
        r.step(15, 0.005)
        r.tap('act_add')
        self.assertEqual(r.text('trac_target'), '10 g')                         # next item
        r.tap('tab_methods')
        r.tap('mp_tasks')
        r.row('tk_list', 'General Weighing')
        r.tap('tk_continue')
        self.assertEqual(len(r.val('task.current')['results']), 2)
        self.assertEqual(r.val('task.paused')[0]['method_name'], 'two items')
        r.tap('tab_methods')
        r.tap('mp_tasks')
        r.row('tk_list', 'two items')
        r.tap('tk_cancel')
        r.ov('OK')
        self.assertEqual(r.val('task.paused'), [])

    def test_piece_counting_reference_and_count(self):
        r = Rig()
        new_method(r, 'Piece Counting', 'pcs')
        start_method(r, 'pcs')
        r.press('key_zero')
        r.step(15, 0.005)                                                       # 10 reference pieces of 0.5 g
        r.tap('act_more')
        r.ov('Capture reference')
        r.ov('Reference PCS'), r.type('10'), r.ov('Confirm')
        r.ov('OK')
        self.assertAlmostEqual(next(m for m in r.val('methods.list') if m['name'] == 'pcs')['settings']['pcs.reference_apw'], 0.5, places=6)
        r.step(15, 0.0125)
        self.assertEqual((r.text('w_value'), r.text('w_unit')), ('25', 'PCS'))
        r.tap('act_add')
        self.assertEqual(r.val('task.current')['results'][0]['text'], '25')

    def test_density_of_a_solid(self):
        r = Rig()
        new_method(r, 'Density determination', 'dens')
        start_method(r, 'dens')
        self.assertEqual(r.text('act_density'), 'Start')
        r.tap('act_density')
        r.ov('Temperature'), r.type('20'), r.ov('Confirm')
        r.ov('OK')
        self.assertEqual(r.page(), ('proc', 'proc'))
        r.step(15, 0.010)                                                       # sample in air: 10 g
        r.tap('pr_ok')
        r.step(15, 0.00875)                                                     # in water: 8.75 g (buoyancy from the host)
        r.tap('pr_ok')
        rho_w = rt.xpr_water_density(20.0)
        expect = 0.99985 * 10.0 / 1.25 * (rho_w - 0.0012) + 0.0012
        self.assertAlmostEqual(r.val('procedure.state')['result']['density'], round(expect, 3), places=6)
        r.tap('pr_complete')
        self.assertEqual(r.val('task.current')['results'][0]['unit'], 'g/cm3')


class TestsAndAutomation(unittest.TestCase):
    def create_test(self, r, ttype, name, weight, extra=()):
        r.home()
        r.tap('tab_methods')
        r.tap('mp_tests')
        r.tap('te_new')
        r.choose('f_test', 'Test type', ttype)
        r.tap('tx_type_next')
        r.enter('f_test', 'Name', name)
        r.tap('tx_spec_tab')
        for label, value, kind in extra:
            (r.enter if kind == 'enter' else r.choose)('f_test', label, value)
        r.tap('tx_weights_tab')
        r.choose('f_test', 'Test weight', weight)
        assert r.tap('tx_weights_save')
        return next(t['id'] for t in r.val('tests.list') if t['name'] == name)

    def run_test(self, r, name, loads, unload=0.0):
        r.home()
        r.tap('tab_methods')
        r.tap('mp_tests')
        r.row('te_list', name)
        r.tap('te_start')
        r.tap('pr_ok'), r.tap('pr_ok')                                          # preparation, empty pan
        r.step(20, 0.0)
        r.row('pr_choices', r.val('test_weights.list')[0]['name'])
        r.tap('pr_ok')
        for m in loads:
            st = r.val('procedure.state')['step']
            if st == 'place':
                r.step(20, m)
            st = r.val('procedure.state')['step']
            if st == 'prompt':                                                  # remove the weight and OK
                r.step(15, unload)
                r.tap('pr_ok')
                r.step(10)
        return r.val('procedure.state')

    def test_sensitivity_and_repeatability_tests(self):
        r = Rig()
        define_weight(r, '200 g F2', 'W200', '200', 'F2')
        self.create_test(r, 'Sensitivity - 1 TP', 'sens', '200 g F2')
        p = self.run_test(r, 'sens', [0.2])
        self.assertEqual((p['step'], p['result']['status']), ('results', 'Passed'))
        r.tap('pr_complete')
        self.create_test(r, 'Repeatab. - 1 TP', 'rep', '200 g F2', [('Number of repetitions', '3', 'enter')])
        p = self.run_test(r, 'rep', [0.2, 0.2, 0.2, 0.2, 0.2, 0.2])
        self.assertEqual((p['result']['status'], len([x for x in p['readings'] if x.get('tp')])), ('Passed', 3))
        r.tap('pr_complete')
        self.assertEqual([h['result'] for h in r.val('history.tests')], ['Passed', 'Passed'])
        r.tap('tab_balance')
        r.tap('bm_history')
        r.tap_text('Tests')
        self.assertEqual(len(r.widgets()['h_tests_list']['cells']), 2)
        r.row('h_tests_list', r.widgets()['h_tests_list']['cells'][0][0])
        self.assertEqual(r.overlay()['title'], 'Test')

    def test_failed_sensitivity_test_blocks_the_balance(self):
        r = Rig()
        r.h.set_sensor_errors(INST, span_error_rel=1e-4)                        # 20 mg at 200 g: beyond the 1 mg control limit
        define_weight(r, '200 g F2', 'W200', '200', 'F2')
        tid = self.create_test(r, 'Sensitivity - 1 TP', 'sens', '200 g F2')
        r.call(r.A, 'test.configure', test_id=tid, settings={'block_balance': True, 'retries': 0})
        p = self.run_test(r, 'sens', [0.2])
        self.assertEqual(p['result']['status'], 'Failed')
        r.tap('pr_complete')
        self.assertEqual((r.page(), r.val('users.state')['block_reason']), (('blocked', 'blocked'), 'Routine test failed: sens'))
        r.tap('bl_unblock')                                                     # no unblocking password set
        self.assertEqual(r.page()[0], 'gui')

    def test_eccentricity_test_five_positions(self):
        r = Rig()
        define_weight(r, '100 g F2', 'W100', '100', 'F2')
        self.create_test(r, 'Eccentricity test', 'ecc', '100 g F2')
        r.home(), r.tap('tab_methods'), r.tap('mp_tests'), r.row('te_list', 'ecc'), r.tap('te_start')
        r.tap('pr_ok'), r.tap('pr_ok')
        r.step(10, 0.0)
        r.row('pr_choices', '100 g F2'), r.tap('pr_ok')
        for i in range(5):
            self.assertIn(f'position {i + 1}', r.text('pr_text'))
            r.step(3, 0.0)                                                      # lift and move the weight
            r.step(20, 0.1)
        r.step(10, 0.0)
        r.tap('pr_ok')
        r.step(10)
        p = r.val('procedure.state')
        self.assertEqual((p['result']['status'], len([x for x in p['readings'] if x.get('tp')])), ('Passed', 5))

    def test_automatic_tare_result_and_preset_tare_immediate_capture(self):
        r = Rig()
        r.tap('act_more')
        r.ov('Configure tare')
        r.ov('Tare Mode'), r.ov('Automatic tare')
        r.ov('Automatic tare threshold'), r.type('5'), r.ov('Confirm')
        r.ov('OK')
        r.call(r.A, 'method.configure', method_id='m1', settings={'auto.result': 'With sample tare', 'auto.result_threshold': 1.0})
        r.press('key_zero')
        r.step(15, 0.020)                                                       # container: tared automatically
        self.assertAlmostEqual(r.val('balance.tare_kg'), 0.020, places=7)
        r.step(15, 0.0225)                                                      # sample 2.5 g: result added automatically
        self.assertEqual([x['text'] for x in r.val('task.current')['results']], ['2.5000'])
        r.step(15, 0.020)                                                       # sample removed: tared again
        self.assertEqual(r.val('balance.net_kg'), 0.0)
        r2 = Rig()
        r2.tap('act_more')
        r2.ov('Configure tare')
        r2.ov('Tare Mode'), r2.ov('Preset tare')
        r2.ov('Preset tare value'), r2.type('5'), r2.ov('Confirm')
        r2.ov('OK')
        r2.call(r2.A, 'method.configure', method_id='m1', settings={'weighing.capture_mode': 'Immediate', 'weighing.capture_delay': 2})
        r2.step(15, 0.015)
        w = r2.widgets()
        self.assertEqual((w['w_value']['text'], 'w_calc' in w, 'w_net' in w), ('10.0000', True, True))   # preset tare: calculated value
        r2.step(0.3, 0.016)                                                     # moving
        r2.tap('act_add')
        self.assertEqual(r2.text('w_busy'), 'Capturing weight...')
        r2.step(2)
        res = r2.val('task.current')['results'][0]
        self.assertEqual((res['stable'], res['calculated']), (False, True))     # D: unstable value, *: calculated


class RefusalsTimeoutsEquivalence(unittest.TestCase):
    def test_forced_leveling_disables_add_result(self):
        r = Rig()
        open_section(r, 'Settings', 'Balance', 'Weighing / Quality')
        r.choose('f_set_weighing_quality', 'Leveling warning', 'Forced leveling')
        r.tap('set_weighing_quality_save')
        r.home()
        r.h.set_environment(INST, tilt_rad=math.radians(0.4))
        settled(r, 0.01)
        self.assertFalse(r.widgets()['act_add']['enabled'])                     # the green button is disabled (p.76)
        self.assertFalse(r.tap('act_add'))
        self.assertEqual(r.call(r.A, 'task.add_result', expect='rejected')['error']['code'], 'NOT_LEVEL')
        self.assertEqual(r.val('task.current')['results'], [])

    def test_unsupported_items_show_the_device_response(self):
        r = Rig()
        for path, last, lines in ((('Settings',), 'Print the settings', ['No printer is connected.']),
                                  (('Maintenance',), 'Import / Export', None),
                                  (('Settings', 'Devices / Printers'), 'Add device', ['Searching for devices...', 'No device found.'])):
            open_section(r, *path)
            accepted = r.tap_text(last)
            if lines is None:                                                   # Import / Export: Next needs a storage device
                accepted = r.ov('Next')
                lines = ['No USB storage device or file server is available.']
            self.assertEqual(r.overlay()['lines'], lines)
            if last != 'Add device':
                self.assertFalse(accepted)
                self.assertTrue(r.frame()['ui']['unsupported'])
            r.ov('OK' if last != 'Add device' else 'Close')
        open_section(r, 'Settings', 'Balance', 'Weighing / Quality')
        self.assertFalse(r.row('f_set_weighing_quality', 'GWP Approved mode'))   # service technician only
        self.assertEqual(r.overlay()['lines'], ['This function is reserved for METTLER TOLEDO service.'])

    def test_timeouts_standby_logout_and_stability(self):
        r = Rig()
        r.call(r.A, 'settings.configure', settings={'general.standby_wait': 1})
        r.step(59)
        self.assertFalse(r.val('device.standby'))
        r.press('key_home')                                                     # activity restarts the wait
        r.step(59)
        self.assertFalse(r.val('device.standby'))
        r.step(2)
        self.assertEqual((r.val('device.standby'), r.page()[0]), (True, 'dark'))   # automatic standby after the wait time (p.85)
        r.press('key_standby')
        r.call(r.A, 'settings.configure', settings={'general.standby': False, 'users.logout_wait': 1})
        r.call(r.A, 'user_management.set', active=True, password='a')
        r.step(61)
        self.assertEqual((r.val('users.state')['logged_in'], r.page()[0]), (False, 'login'))   # automatic logout (p.73)
        r.call(r.A, 'session.login', user_name='Administrator', password='a')
        r.h.set_environment(INST, vibration=True)                               # never stable: Zeroing... gives up after 40 s
        r.press('key_zero')
        r.step(41)
        self.assertEqual((r.val('weighing.pending'), r.val('device.messages')[-1]['text']), (None, 'The weight did not become stable.'))
        f = r.frame()
        self.assertIsNone(f['ui']['overlay'])                                  # menus have no time-out (none documented)
        r.call(r.A, 'settings.configure', settings={'users.automatic_logout': False})
        r.tap('tab_balance')
        r.step(600)
        self.assertEqual(r.page(), ('gui', 'balance_menu'))

    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(), Rig()
        settled(a, 0.0201), settled(b, 0.0201)
        a.press('key_tare')
        b.call(b.A, 'balance.request', action='tare')
        open_section(a, 'Settings', 'Balance', 'Screen / StatusLight / Sound')
        a.choose('f_set_screen', 'Screen brightness', '60 %')
        a.tap('set_screen_save')
        b.call(b.A, 'settings.configure', settings={'screen.brightness': 60})
        new_method(a, 'General weighing', 'same')
        b.call(b.A, 'method.create', type='General weighing', settings={'name': 'same', 'ids.result_1_description': 'Sample ID',
                                                                         'weighing.profile': None, 'item.unit': 'g', 'item.target': None,
                                                                         'item.tol_minus': 2.5, 'item.tol_plus': 2.5})
        a.home()
        for r in (a, b):
            r.step(5)
        sa, sb = (x.h.debug_snapshot()['instances'][INST]['state'] for x in (a, b))
        diff = {k for k in sa if sa[k] != sb[k]}
        self.assertEqual(diff, set(), diff)

    def test_render_determinism_capture_and_access(self):
        def run():
            r = Rig()
            settled(r, 0.0123)
            r.tap('tab_methods')
            r.tap('ml_new')
            r.row('f_method_new', 'Method type')
            return r, r.png()
        (r1, p1), (_, p2) = run(), run()
        self.assertEqual(hashlib.sha256(p1).hexdigest(), hashlib.sha256(p2).hexdigest())
        cap = r1.call(r1.A, 'panel.capture', display_id='status_light')
        self.assertEqual((cap['image']['width'], cap['image']['height']), (160, 14))
        self.assertEqual(r1.call(r1.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')   # evaluator only
        self.assertEqual(r1.call(r1.A, 'panel.touch', expect='rejected', display_id=TERM, x=900, y=10)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r1.call(r1.A, 'panel.press', expect='rejected', input_id='key_print')['error']['code'], 'INVALID_ARGUMENT')
        caps = r1.A.describe(instance_id=INST)['capabilities']
        self.assertIn('panel.touch', caps)
        self.assertNotIn('panel.frame', caps)
        self.assertIn('panel.frame', r1.E.describe(instance_id=INST)['capabilities'])

    def test_reset_and_change_history(self):
        r = Rig()
        r.call(r.A, 'settings.configure', settings={'quality.change_history': True})
        r.call(r.A, 'settings.configure', settings={'screen.brightness': 40})
        r.call(r.A, 'method.create', type='General weighing', settings={'name': 'x'})
        open_section(r, 'History', 'Changes')
        objs = [c[1] for c in r.widgets()['h_changes_list']['cells']]
        self.assertIn('Settings / screen.brightness', objs)
        r.tap('h_changes_filter')                                               # filter by user name
        r.ov('User name'), r.type('nobody'), r.ov('Confirm')
        r.ov('OK')
        self.assertEqual(r.widgets()['h_changes_list']['cells'], [])
        open_section(r, 'Maintenance', 'Reset')
        r.row('f_reset', 'Also delete change, test and adjustment history')
        r.ov('Active')
        r.tap('rs_next')
        r.ov('Reset balance')
        self.assertEqual(([m['name'] for m in r.val('methods.list')], r.val('device.settings')['screen.brightness'], r.val('history.changes')),
                         (['General Weighing'], 80, []))
        self.assertEqual(r.page(), ('gui', 'main'))


class TerminalCommands(unittest.TestCase):
    """Every terminal command once: output against its schema, changed slots within its declared effects, refusals leave the state."""

    def test_outputs_and_declared_effects(self):
        r = Rig()
        meta = rt.export_xpr204e_contract_metadata()['asset_types'][rt.XPR204E_TYPE]
        term = meta['terminal_capabilities']
        checked = set()

        def run(cap, sess=None, **args):
            before = r.h.debug_snapshot()['instances'][INST]['state']
            out = r.call(sess or r.A, cap, **args)
            after = r.h.debug_snapshot()['instances'][INST]['state']
            changed = {k for k in after if after[k] != before[k]}
            declared = {e['state_id'] for e in term[cap]['state_effects']}
            self.assertLessEqual(changed, declared, (cap, changed - declared))
            errors = []
            validate_value(out, term[cap]['output_schema'], cap, errors)
            self.assertEqual(errors, [], cap)
            checked.add(cap)
            return out
        settled(r, 0.001)
        run('balance.request', action='zero')
        run('display.set_unit', unit='mg')
        run('settings.configure', settings={'screen.brightness': 60, 'quality.change_history': True})
        run('clock.set', date='2026-10-18', time='09:30')
        run('door.key', key='right')
        run('door.operate', side='right', action='close')
        run('profile.save', profile_id=None, settings={'name': 'Fast', 'environment': 'Stable'})
        run('profile.set_default', profile_id='tp2')
        run('profile.set_default', profile_id='tp1')
        run('profile.delete', profile_id='tp2')
        mid = run('method.create', type='General weighing (itemized)', settings={'name': 'it'}, items=[{'sample_id': 'A', 'target': 1.0}])['method_id']
        run('method.item_save', method_id=mid, index=None, item={'sample_id': 'B', 'unit': 'g', 'target': 2.0})
        run('method.item_delete', method_id=mid, index=2)
        run('method.configure', method_id=mid, settings={'comment': 'c'})
        cid = run('method.clone', method_id=mid)['method_id']
        run('method.delete', method_id=cid)
        run('method.start', method_id='m1')
        run('task.set_id', kind='result', index=1, value='S1')
        settled(r, 0.01)
        run('task.add_result')
        settled(r, 0.011)
        run('task.add_result')
        run('task.exclude_result', index=1, comment='x')
        run('method.save_itemized', name='from results')
        run('task.complete')
        settled(r, 0.012)
        run('task.add_result')
        run('task.cancel')
        run('method.start', method_id=mid)
        settled(r, 0.001)
        run('task.add_result')
        run('method.start', method_id='m1')
        run('task.delete', method_id=mid)
        run('test_weight.save', weight_id=None, settings={'name': 'w1', 'weight_id': 'W1', 'nominal_g': 100.0, 'weight_class': 'F1'})
        run('test_weight.save', weight_id=None, settings={'name': 'w2', 'weight_id': 'W2', 'nominal_g': 100.0, 'weight_class': 'F1'})
        run('test_weight.save_combined', weight_id=None, name='w12', min_class='F2', weights=['tw1', 'tw2'])
        run('test_weight.delete', weight_id='tw3')
        tid = run('test.create', type='Sensitivity - 1 TP', settings={'name': 'sens', 'weight_1': 'tw1', 'tp1_nominal_g': 100.0})['test_id']
        run('test.configure', test_id=tid, settings={'automatic_print': False})
        run('adjustment.configure', settings={'as_left': True})
        run('adjustment.start')
        r.step(25)
        run('procedure.complete')
        run('test.start', test_id=tid)
        run('procedure.confirm')
        run('procedure.select_weight', weight_id='tw1') if False else None
        run('procedure.cancel')
        r.call(r.A, 'test.start', test_id=tid)
        r.call(r.A, 'procedure.confirm'), r.call(r.A, 'procedure.confirm')
        r.step(20, 0.0)
        run('procedure.select_weight', weight_id='tw1')
        r.call(r.A, 'procedure.cancel')
        run('test.delete', test_id=tid)
        pid = run('method.create', type='Piece Counting', settings={'name': 'p'})['method_id']
        run('method.start', method_id=pid)
        settled(r, 0.005)
        run('pcs.set_reference', pieces=10)
        did = run('method.create', type='Density determination', settings={'name': 'd'})['method_id']
        run('method.start', method_id=did)
        run('density.start', temperature_c=20.0)
        r.call(r.A, 'procedure.cancel')
        run('method.start', method_id='m1')
        r.h.set_environment(INST, tilt_rad=0.01)
        r.step(1)
        run('message.acknowledge', message_id=r.val('device.messages')[0]['id'])
        r.h.set_environment(INST, tilt_rad=0.0)
        run('unblocking_password.set', password='')
        run('balance.block', block=True)
        run('balance.block', block=False)
        run('user_management.set', active=True, password='a')
        run('group.save', group_name='G', permissions={'start_tests': True})
        run('user.save', user_name='u', settings={'groups': ['G']})
        run('user.set_password', user_name='u', password='p')
        run('session.logout')
        run('session.login', user_name='Administrator', password='a')
        run('user.delete', user_name='u')
        run('group.delete', group_name='G')
        run('user_management.set', active=False)
        run('standby.set', active=True)
        run('standby.set', active=False)
        run('maintenance.reset', delete_history=False)
        run('fault.inject', sess=r.E, code='performance_optimization')               # consistency sweep 2026-10-19: evaluator-only errors
        run('fault.clear', sess=r.E)
        missing = set(rt._XT_COMMANDS) - checked
        self.assertEqual(missing, set())
        before = r.h.debug_snapshot()
        for cap, args in (('method.delete', {'method_id': 'm1'}), ('test.start', {'test_id': 'nope'}), ('settings.configure', {'settings': {'x': 1}}),
                          ('display.set_unit', {'unit': 'PCS'}), ('procedure.confirm', {}), ('session.login', {'user_name': 'x', 'password': 'y'})):
            out = r.call(r.A, cap, expect='rejected', **args)
            self.assertEqual(out['error']['side_effects'], 'none')
            self.assertEqual(before, r.h.debug_snapshot(), cap)


class DeviceModel(unittest.TestCase):
    """Terminal functions checked through the commands the panel calls (the panel paths are covered above)."""

    def test_adjustment_schedule_as_found_left_and_blocking(self):
        r = Rig()
        r.h.set_sensor_errors(INST, span_error_rel=0.003)                       # 0.3 %: beyond the 0.1 % control limit
        r.call(r.A, 'adjustment.configure', settings={'as_found': True, 'as_left': True, 'block_balance': True, 'start_time_1': '00:30',
                                                      'preferred_days': list(rt.XPR_WEEKDAYS)})
        r.call(r.A, 'clock.set', date='2026-10-19', time='00:29')
        r.step(61)                                                              # the scheduled start time passes
        p = r.val('procedure.state')
        self.assertEqual((p['kind'], p['trigger']), ('internal', 'Schedule'))
        self.assertEqual(r.page(), ('proc', 'proc'))
        r.step(25)
        res = r.val('procedure.state')['result']
        self.assertEqual((res['as_found'], res['as_left'], res['status']), ('Failed', 'Passed', 'Done'))   # as found fails, adjusted, as left passes
        self.assertAlmostEqual(res['as_found_pct'], 0.3, places=6)
        r.call(r.A, 'procedure.complete')
        r.h.set_sensor_errors(INST, internal_weight_error_kg=0.001)              # a damaged internal weight: as left fails, the balance blocks
        r.call(r.A, 'adjustment.start')
        r.step(25)
        self.assertEqual(r.val('procedure.state')['result']['status'], 'Done')  # the gain follows the (wrong) weight; as left compares to it
        r.call(r.A, 'procedure.complete')
        r.h.set_environment(INST, tilt_rad=math.radians(0.5))
        r.step(1)
        r.call(r.A, 'adjustment.configure', settings={'start_after_leveling': True})
        r.h.set_environment(INST, tilt_rad=0.0)
        r.step(1)
        self.assertEqual(r.val('procedure.state')['trigger'], 'Leveling')       # start after leveling (p.129)

    def test_test_planning_weight_reminder_and_status_light(self):
        r = Rig()
        w = r.call(r.A, 'test_weight.save', weight_id=None, settings={'name': 'w', 'weight_id': 'W', 'nominal_g': 200.0, 'weight_class': 'F2',
                                                                       'next_calibration': '2026-01-20'})['weight_id']
        r.call(r.A, 'test.create', type='Sensitivity - 1 TP', settings={'name': 'daily', 'weight_1': w, 'planning': 'Daily', 'start_time': '00:10'})
        r.call(r.A, 'settings.configure', settings={'general.standby': False})  # else the display goes dark after 10 min
        r.step(11 * 60)
        msgs = {m['text'] for m in r.val('device.messages')}
        self.assertIn('Routine test is due: daily', msgs)
        self.assertTrue(r.val('tests.list')[0]['due'])
        self.assertEqual(r.val('device.status_light'), 'yellow')
        r.call(r.A, 'settings.configure', settings={'quality.weight_recal_reminder': True})
        r.step(1)
        self.assertIn('Test weight calibration due: w (2026-01-20)', {m['text'] for m in r.val('device.messages')})
        r.home()
        r.tap('tab_methods')
        r.tap('mp_tests')
        self.assertEqual(r.cells('te_list')['daily'], 'Sensitivity - 1 TP')

    def test_custom_unit_indicator_and_user_language(self):
        r = Rig()
        r.call(r.A, 'method.configure', method_id='m1', settings={'custom.active': True, 'custom.name': 'mm2', 'custom.factor': 2.5,
                                                                   'custom.readability': 0.01, 'item.unit': 'custom'})
        settled(r, 0.0123456)
        self.assertEqual((r.text('w_value'), r.text('w_unit')), ('30.86', 'mm2'))  # 12.3456 g x 2.5 in steps of 0.01
        r.call(r.A, 'profile.save', profile_id='tp1', settings={'indicator': 'Blue', 'indicator_text': 'GMP'})
        w = r.widgets()['w_indicator']
        self.assertEqual((w['text'], w['fill']), ('GMP', 'blue'))
        r.call(r.A, 'user_management.set', active=True, password='a')
        r.call(r.A, 'user.save', user_name='fr', settings={'language': 'Français', 'groups': ['Administrators']})
        r.call(r.A, 'user.set_password', user_name='fr', password='f')
        r.call(r.A, 'session.logout')
        r.call(r.A, 'session.login', user_name='fr', password='f')
        self.assertEqual((r.val('device.language'), r.text('act_add')), ('Français', 'Ajouter résultat'))   # user-specific language (p.77)

    def test_automation_zero_falling_trigger_tare_after_result_and_completion(self):
        r = Rig()
        r.call(r.A, 'method.configure', method_id='m1', settings={'auto.zero': True, 'auto.zero_threshold': 0.0005, 'auto.result': 'None'})
        r.h.set_sensor_errors(INST, zero_offset_kg=0.0000003)                   # 0.3 mg offset: within the automatic-zero threshold
        r.step(15, 0.0)
        self.assertEqual(r.text('w_value'), '0.0000')
        r.call(r.A, 'method.configure', method_id='m1', settings={'auto.result': 'Without sample tare', 'auto.result_threshold': 1.0,
                                                                   'auto.weight_trigger': 'Falling below', 'auto.tare_after_result': False})
        r.step(15, 0.0055)
        self.assertEqual(r.val('task.current')['results'], [])                  # not yet: the weight must fall below the threshold
        r.step(15, 0.0)
        self.assertEqual([x['text'] for x in r.val('task.current')['results']], ['5.5000'])
        mid = r.call(r.A, 'method.create', type='General weighing (itemized)', settings={'name': 'it', 'auto.task_completion': True,
                                                                                          'auto.tare_after_result': True},
                     items=[{'sample_id': 'A', 'target': 2.0}, {'sample_id': 'B', 'target': 3.0}])['method_id']
        r.call(r.A, 'method.start', method_id=mid)
        r.step(15, 0.002)
        r.call(r.A, 'task.add_result')
        self.assertAlmostEqual(r.val('balance.tare_kg'), 0.002, places=7)        # automatic tare after result
        r.step(15, 0.005)
        r.call(r.A, 'task.add_result')                                          # last item: the task completes automatically
        done = r.val('task.completed')[-1]
        self.assertEqual(([x['sample_id'] for x in done['results']], r.val('task.current')['results']), (['A', 'B'], []))

    def test_density_of_liquids_sinker_and_pycnometer(self):
        r = Rig()
        mid = r.call(r.A, 'method.create', type='Density determination', settings={'name': 's', 'density.type': 'Liquid (sinker)',
                                                                                    'density.sinker_volume': 10.0})['method_id']
        r.call(r.A, 'method.start', method_id=mid)
        r.call(r.A, 'density.start', temperature_c=20.0)
        r.step(15, 0.030)
        r.call(r.A, 'procedure.confirm')
        r.step(15, 0.020)
        r.call(r.A, 'procedure.confirm')
        self.assertAlmostEqual(r.val('procedure.state')['result']['density'], round(0.99985 * 10.0 / 10.0 + 0.0012, 3), places=6)
        r.call(r.A, 'procedure.complete')
        pid = r.call(r.A, 'method.create', type='Density determination', settings={'name': 'p', 'density.type': 'Liquid (pycnometer)',
                                                                                    'density.pyc_volume': 25.0, 'density.pyc_weight': 20.0,
                                                                                    'density.air_compensation': False, 'density.unit': 'kg/m3'})['method_id']
        r.call(r.A, 'method.start', method_id=pid)
        r.call(r.A, 'density.start', temperature_c=21.0)
        r.step(15, 0.0449)                                                      # pycnometer 20 g + 24.9 g liquid
        r.call(r.A, 'procedure.confirm')
        res = r.val('procedure.state')['result']
        self.assertEqual((res['density'], res['unit']), (996.0, 'kg/m3'))

    def test_tare_variants_of_sensitivity_and_repeatability(self):
        for ttype, plan, extra in (('Sensitivity - Tare - 2 TP', [0.01, 0.21, 0.01, 0.02], {'weight_2': None}),
                                   ('Repeatab. - Tare - 1 TP', [0.01, 0.21, 0.01, 0.01, 0.21, 0.01], {'repetitions': 2})):
            r = Rig()
            a = r.call(r.A, 'test_weight.save', weight_id=None, settings={'name': 'a', 'weight_id': 'A', 'nominal_g': 200.0, 'weight_class': 'F2'})['weight_id']
            b = r.call(r.A, 'test_weight.save', weight_id=None, settings={'name': 'b', 'weight_id': 'B', 'nominal_g': 10.0, 'weight_class': 'F2'})['weight_id']
            extra = {k: (b if k == 'weight_2' else v) for k, v in extra.items()}
            tid = r.call(r.A, 'test.create', type=ttype, settings=dict({'name': 't', 'weight_1': a, 'show_preparation': False, 'min_tare_g': 5.0},
                                                                         **extra))['test_id']
            r.call(r.A, 'test.start', test_id=tid)
            r.call(r.A, 'procedure.confirm')
            r.step(20, 0.0)
            while r.val('procedure.state')['step'] == 'choose':
                r.call(r.A, 'procedure.confirm')
            for m in plan:                                                      # container, weight, weight off (container stays), ...
                if r.val('procedure.state')['step'] == 'prompt':
                    r.call(r.A, 'procedure.confirm')
                r.step(25, m)
            p = r.val('procedure.state')
            self.assertEqual((p['status'], p['result']['status']), ('finished', 'Passed'), ttype)
            self.assertEqual([x['label'] for x in p['readings']][0], 'Tare')
            self.assertEqual({x['weight_g'] for x in p['readings'] if x['label'] == 'Tare'}, {10.0})


class StableMeansSettled(unittest.TestCase):
    """1.3.1 (2026-10-22): balance.stable rose about 1.5-13 s after a load change while the low-pass reading was still short of the settled value
    (Standard: 0.7463 g shown for 0.7531 g).  Whenever the twin reports stable, the reading is within one readability step of the settled value."""

    def test_stable_reading_is_within_readability_of_settled_value(self):
        for env in rt.XPR_ENVIRONMENTS:
            for mode in ('Universal', 'Sensor mode'):
                with self.subTest(environment=env, weighing_mode=mode):
                    r = Rig()
                    pid = r.call(r.A, 'profile.save', profile_id=None, settings={'name': 'P', 'environment': env, 'weighing_mode': mode,
                                                                                    'zero_drift': False})['profile_id']
                    r.call(r.A, 'profile.set_default', profile_id=pid)
                    r.step(30, 0.0)
                    seen = []
                    for _ in range(900):                                         # 0.7531 g placed, read every 0.1 s for 90 s
                        r.step(0.1, 0.0007531)
                        seen.append((r.val('balance.stable'), r.val('balance.net_kg')))
                    final = seen[-1][1]
                    self.assertTrue(seen[-1][0])
                    self.assertAlmostEqual(final, 0.0007531, delta=1e-7)
                    for stable, net in seen:
                        if stable:
                            self.assertLessEqual(abs(net - final), rt.XPR204E_READABILITY_KG + 1e-12, (env, mode, net, final))


class SyncAudit(unittest.TestCase):
    """Sync audit 2026-10-19: Start after temperature change, the time zone, panel commands as the gesturing session."""

    def test_start_after_temperature_change(self):
        r = Rig()
        r.h.set_sensor_errors(INST, span_tc_per_k=5e-6)                          # the sensitivity drifts 5 ppm/K away from 20 degC
        r.h.set_environment(INST, temperature_c=20.6)
        r.step(2)
        self.assertIsNone(r.val('procedure.state'))                              # below 1 degC: no adjustment
        settled(r, 0.1)
        before = float(r.text('w_value'))
        r.h.set_environment(INST, temperature_c=21.2)                            # 1.2 degC since the last adjustment (p.129)
        r.step(1, mass=0.0)
        self.assertEqual(r.val('procedure.state')['trigger'], 'Temperature')
        r.step(25)
        self.assertEqual(r.val('procedure.state')['result']['status'], 'Done')
        r.call(r.A, 'procedure.complete')
        self.assertAlmostEqual(r.val('history.adjustments')[-1]['temperature'], 21.2)
        settled(r, 0.1)
        self.assertEqual(r.text('w_value'), '100.0000')                          # corrected at the new temperature
        self.assertNotEqual(before, 100.0)
        r.h.set_environment(INST, temperature_c=22.5)
        r.call(r.A, 'adjustment.configure', settings={'start_after_temperature': False})
        r.step(2)
        self.assertNotEqual((r.val('procedure.state') or {}).get('status'), 'running')   # switched off: no start

    def test_time_zone_moves_the_shown_time(self):
        r = Rig()
        t0 = r.val('device.datetime')
        r.call(r.A, 'settings.configure', settings={'region.time_zone': 'Asia/Shanghai'})
        self.assertEqual(r.val('device.datetime')[11:13], f"{(int(t0[11:13]) + 8) % 24:02d}")
        r.call(r.A, 'settings.configure', settings={'region.time_zone': 'Asia/Kolkata'})
        self.assertEqual(r.val('device.datetime')[11:16], f"{(int(t0[11:13]) + 5) % 24:02d}:{int(t0[14:16]) + 30:02d}")

    def test_panel_commands_run_as_the_gesturing_session(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        b = r.h.session('agent_b')
        seen = []
        orig = rt.XPR204ESession._checked

        def spy(sess, iid, inst, cap, args, rid):
            if rid == 'panel':
                seen.append(sess._XPR204ESession__principal)
            return orig(sess, iid, inst, cap, args, rid)
        rt.XPR204ESession._checked = spy
        try:
            b.invoke(instance_id=INST, capability_id='panel.press', arguments={'input_id': 'key_zero'}, request_id='b1')
        finally:
            rt.XPR204ESession._checked = orig
        self.assertTrue(seen and all(p == 'agent_b' for p in seen), seen)
        self.assertIsNone(r.h._xt_caller)


class Sweep(unittest.TestCase):
    """Consistency sweep 2026-10-19: procedures and weighing tasks belong to the caller that started them; the error messages of p.134 are
    injected by the evaluator."""
    def gesture(self, r, sess, label):
        """A tap of sess on the button (page or overlay) with this label."""
        ov = r.overlay()
        items = ov['buttons'] if ov else [w for w in r.disp()['widgets'] if w['type'] == 'button']
        b = next(b for b in items if b.get('label', b.get('text')) == label)
        x, y, w, h = b['rect']
        return r.call(sess, 'panel.touch', display_id=TERM, x=x + w // 2, y=y + h // 2)['accepted']

    def test_procedure_and_task_belong_to_their_caller(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        settled(r, 0.1)
        r.tap('act_more')
        r.ov('Start adjustment')                                                     # A's internal adjustment on the terminal
        self.assertEqual(r.h._instances[INST]['device']['procedure']['owner'], 'agent')
        for cap, args in (('procedure.cancel', {}), ('standby.set', dict(active=True))):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.val('procedure.state')['status'], 'running')
        self.gesture(r, B, 'Cancel')                                                 # B: Cancel, Yes on the terminal
        self.assertFalse(self.gesture(r, B, 'Yes'))
        self.assertEqual((r.overlay()['title'], r.overlay()['lines']), ('Error', ['Started by another user.']))
        r.ov('OK')
        self.assertEqual(r.val('procedure.state')['status'], 'running')
        r.step(25)
        r.tap('pr_complete')                                                         # A completes its own adjustment
        mid = r.h._instances[INST]['device']['active_method']
        r.call(r.A, 'method.start', method_id=mid)                                   # A's weighing task
        settled(r, 0.05)
        r.call(r.A, 'task.add_result')
        settled(r, 0.05)
        for cap in ('task.add_result', 'task.cancel', 'task.complete'):
            self.assertEqual(r.call(B, cap, expect='rejected')['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.call(B, 'method.start', expect='rejected', method_id=mid)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(len(r.val('task.current')['results']), 1)
        r.tap('act_more')
        r.ov('Start adjustment')                                                     # A's second adjustment: B unplugs the balance
        r.call(B, 'power.set', enabled=False)                                        # physical (lead clarification 10-19): never refused
        self.assertIsNone(r.h._instances[INST]['device']['procedure'])               # the procedure is lost as at a power loss
        r.call(r.A, 'power.set', enabled=True)
        r.step(12)
        r.call(r.E, 'task.cancel')                                                   # the evaluator is unrestricted
        self.assertEqual(r.h._instances[INST]['device']['task'] and r.h._instances[INST]['device']['task']['results'], [])

    def test_error_messages_of_p134(self):
        r = Rig()
        settled(r, 0.05)
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='pairing_error')['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('fault.inject', r.A.describe(instance_id=INST)['capabilities'])
        r.call(r.E, 'fault.inject', code='weight_not_determined')
        self.assertEqual((r.text('w_value'), r.text('msg_line'), r.val('device.status_light')), ('--', 'Weight cannot be determined', 'red'))
        self.assertFalse(r.press('key_zero'))                                         # zero refused: the device's error dialog
        self.assertEqual(r.overlay()['lines'], ['Weight cannot be determined'])
        r.ov('OK')
        self.assertEqual(r.call(r.A, 'balance.tare', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')
        r.call(r.A, 'power.set', enabled=False)                                       # the remedy: disconnect and reconnect
        r.call(r.A, 'power.set', enabled=True)
        r.step(12)
        self.assertFalse([m for m in r.val('device.messages') if m['key'] == 'fault'])
        settled(r, 0.05)
        self.assertNotEqual(r.text('w_value'), '--')
        r.call(r.E, 'fault.inject', code='cannot_start_adjustment')
        r.tap('act_more')
        self.assertFalse(r.ov('Start adjustment'))
        self.assertEqual(r.overlay()['lines'], ['Cannot start adjustment'])
        r.ov('OK')
        r.call(r.E, 'fault.clear')
        r.call(r.E, 'fault.inject', code='pairing_error')                             # needs a new cable: not cleared by power
        r.call(r.A, 'power.set', enabled=False)
        r.call(r.A, 'power.set', enabled=True)
        r.step(12)
        self.assertEqual([m['text'] for m in r.val('device.messages') if m['key'] == 'fault'], ['Pairing error.'])
        r.call(r.E, 'fault.clear')
        self.assertFalse([m for m in r.val('device.messages') if m['key'] == 'fault'])


class EvidenceAndContract(unittest.TestCase):
    def test_evidence_renders(self):
        """home / menu (Settings) / error (out-of-level message) and a few more screens, deterministic, with renders.json."""
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        panel = r.h.panel(INST)
        shots = {}
        settled(r, 0.0102345)
        r.call(r.A, 'method.configure', method_id='m1', settings={'item.target': 10.0, 'item.tol_minus': 5.0, 'item.tol_plus': 5.0,
                                                                   'weighing.show_info': True})
        r.tap('act_add')
        shots['home'] = panel.save_png(TERM, OUT / 'home.png')
        open_section(r, 'Settings', 'Balance', 'Weighing / Quality')
        shots['menu'] = panel.save_png(TERM, OUT / 'menu.png')
        r.home()
        r.h.set_environment(INST, tilt_rad=math.radians(0.4), tilt_direction_rad=1.0)
        r.step(1)
        r.call(r.A, 'settings.configure', settings={'quality.leveling_warning': 'Forced leveling'})
        r.tap('act_add')                                                        # Add result disabled: nothing happens
        r.call(r.E, 'panel.frame')
        r.tap('msg_line')
        r.row('msg_list', 'Warning')
        shots['error'] = panel.save_png(TERM, OUT / 'error.png')
        r.ov('Level the balance')
        shots['leveling'] = panel.save_png(TERM, OUT / 'leveling.png')
        r.h.set_environment(INST, tilt_rad=0.0)
        r.step(1)
        r.home()
        r.tap('tab_balance')
        shots['balance_menu'] = panel.save_png(TERM, OUT / 'balance_menu.png')
        r.home()
        r.tap('tab_methods')
        r.tap('ml_new')
        r.row('f_method_new', 'Method type')
        shots['wizard'] = panel.save_png(TERM, OUT / 'wizard_choice.png')
        r.ov('Cancel')
        r.tap('mw_type_next')
        r.row('f_method_new', 'Method name')
        shots['keyboard'] = panel.save_png(TERM, OUT / 'keyboard.png')
        r.ov('Discard')
        r.home()
        r.tap('tab_results')
        shots['results'] = panel.save_png(TERM, OUT / 'results.png')
        r.home()
        r.tap('act_more')
        r.ov('Start adjustment')
        shots['adjustment'] = panel.save_png(TERM, OUT / 'adjustment.png')
        r.step(25)
        shots['adjustment_result'] = panel.save_png(TERM, OUT / 'adjustment_result.png')
        shots['status_light'] = panel.save_png('status_light', OUT / 'status_light.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_xpr204e_contract_metadata()['asset_types'][rt.XPR204E_TYPE]
        self.assertEqual(c['asset_version'], rt.XPR204E_ASSET_VERSION)
        for name, cap in meta['capabilities'].items():                         # the CHEM-001 command set keeps its meaning
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
        for name, cap in meta['terminal_capabilities'].items():
            self.assertIn(name, c['capabilities'])
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
            for e in cap['state_effects']:
                self.assertIn(e['state_id'], c['state_slots'], name)
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        spec = rt.XPR204E_PANEL_SPEC
        for k, v in list(spec['inputs'].items()) + list(spec['displays'].items()):
            comp = c['components'][v['component_id']]
            self.assertEqual(comp['geometry_status'], 'bound', k)   # XPR terminal parts (2026-10-20 geometry round): TFT, StatusLight, touch keys
            sels = {x['selector'] for x in comp['geometry']}
            self.assertTrue(any(s.startswith('mt_xpr204e__') for s in sels) and any(s.startswith('link:') for s in sels), (k, sels))
        self.assertIn('mt_xpr204e__terminal_display__tft', {x['selector'] for x in c['components']['terminal_display']['geometry']})
        for cid in ('control_panel', 'button_zero', 'button_tare', 'button_print', 'button_function', 'level_indicator'):
            self.assertNotIn(cid, c['components'])                     # PX keypad parts removed (the XPR204E has none)
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touch_gui', '0.1'))
        for aid in ('panel_ui', 'panel_engine', 'panel_ui_tests', 'panel_engine_tests', 'panel_ui_builder_py', 'panel_ui_contract_patch_py'):
            self.assertEqual(c['artifacts'][aid]['status'], 'available', aid)
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])            # the shared engine is not pinned per asset
        from asset_tool import validate_contract
        self.assertEqual(validate_contract(c, ROOT, check_files=True), [])


def define_weight(r, name, wid, nominal, klass):
    """Methods > Tests > Test weights > New: name, ID, nominal weight, class; Save."""
    r.home()
    r.tap('tab_methods')
    r.tap('mp_tests')
    r.tap('te_weights')
    r.tap('tw_new')
    r.enter('f_weight', 'Test weight name', name)
    r.enter('f_weight', 'Test weight ID', wid)
    r.enter('f_weight', 'Nominal weight', nominal)
    r.choose('f_weight', 'Weight class', klass)
    assert r.tap('weight_save')
    return next(w['id'] for w in r.val('test_weights.list') if w['name'] == name)


if __name__ == '__main__':
    unittest.main()
