"""front-panel tests for CHEM-167 biorad_cfxopus (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/biorad_cfxopus.py):
python3 -m pytest tests/test_panel_ui_biorad_cfxopus.py -q

Every gesture goes through the session API (panel.touch / panel.switch, the runtime's normal invoke path); widgets are found by id or text in the
structured frame (panel.frame, evaluator) and tapped at their centre; plates are prepared and loaded with the robot skills.  Themes: the seven key
procedures of the menu survey (start up and log in, load a plate, create and run a protocol, add a gradient, run a saved protocol, pause / skip /
stop, shut down); the other touch-screen functions (Create User and passwords, password requirement, User Profile, File Browser folders / copy /
rename / delete, Select Location, Time Remaining and View Curves, Run Reports, System Logs, System Settings and the clock, screen saver, User
Management, Email Settings, Diagnostic Test, Demo, power-failure message, status bar); panel = direct capability (same runtime state); refusals
shown as the device's own message box (state unchanged); unsupported network / update / shipping functions; time-outs; render determinism and the
evidence PNGs; contract entries."""
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import biorad_cfxopus as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'cfx_opus_01'
SCR = 'screen'
OUT = ROOT / 'outputs/panel_ui/biorad_cfxopus'
SPEC = rt.PANEL_SPEC
LEDS = ('status_bar_green', 'status_bar_blue', 'status_bar_red')
SURVEY_TASKS = ['start up and log in', 'load a plate', 'create and run a protocol', 'add a gradient to a step', 'run a saved protocol',
                'pause, skip or stop a run', 'shut down']
DONE_TASKS = set()
WELLS = [dict(well='A1', sample_type='standard', sample_id='gapdh_std', dilution=1.0, template_volume_m3=2e-9, reaction_volume_m3=2e-8, assays=['hs_gapdh'],
              chemistry='sybr', quantity_copies=1.0e6), dict(well='A2', sample_type='ntc', reaction_volume_m3=2e-8, assays=['hs_gapdh'], chemistry='sybr')]


class Rig:
    """A CFX Opus with an agent and an evaluator session; the touch screen and the rear switch only through panel.* capabilities."""

    def __init__(self, on=True, user='Admin'):
        self.h = rt.BioradCfxOpusRuntime(INST)
        self.h.panel_capture_dir = tempfile.mkdtemp(prefix='cfx_panel_')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        if on:
            assert self.switch('on')
            self.step(10.5)
            if user:
                self.login(user)

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0 + (args['dt_s'] if cap == 'sim.step' else 0.0), cap   # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        while dt > 1e-9:
            d = min(dt, 600.0)
            self.call(self.E, 'sim.step', dt_s=float(d))
            dt -= d

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='switch_power', position=pos)['accepted']

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCR]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCR, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def msg(self):
        ov = self.overlay()
        return '\n'.join(ov['lines']) if ov and ov.get('kind') == 'dialog' else None

    def ok(self):
        return self.ov('OK')

    def row(self, wid, text):
        """Tap the row of a list or form widget with a cell equal to this text (dragging the rows until it shows)."""
        for _ in range(12):
            w = self.widgets()[wid]
            for k, cells in enumerate(w['cells']):
                if text in cells:
                    return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (text, w['cells'])
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(text)

    def cells(self, wid):
        return [c for c in self.widgets()[wid]['cells']]

    def type(self, text):
        """Type on the open keyboard (the shown text is replaced): numeric and time keypads key by key, the alpha keyboard with Shift and 12@."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov
        labels = {b['label'] for b in ov['buttons']}
        if ov['field']:
            self.ov('Del')
            while self.overlay()['field'] and self.overlay()['field'] != '*' * 0:
                self.ov('Del')
        if 'q' not in labels and 'Q' not in labels:
            for ch in ([text] if text == 'Inf' else text):
                assert self.ov(ch), ch
            return
        for ch in text:
            mode = self.overlay()['mode']
            if ch.isdigit() or ch in '$@#%&/():;_+=\'"!?,':
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

    def enter(self, text):
        self.type(text)
        return self.ov('OK')

    def login(self, user, password=None):
        if self.page()[1] != 'login':
            self.tap('logout')
        self.row('users', user)
        if password is not None:
            self.enter(password)
        assert self.page() == ('main', 'home'), self.page()

    def png(self, display=SCR):
        r = self.call(self.A, 'panel.capture', display_id=display)
        return Path(r['image']['path']).read_bytes()

    # ---- robot skills and setup outside the panel
    def plate(self):
        self.call(self.E, 'sample.provide', sample_id='gapdh_std', kind='standard', copies_per_m3={'hs_gapdh': 5e14}, declared_copies_per_m3={'hs_gapdh': 5e14})
        self.call(self.A, 'plate.prepare', plate_id='p1', plate_type='white_lowprofile_96', seal='optical_film', wells=WELLS)

    def edit_step(self, col, part, text):
        self.tap(f'step{col}_{part}')
        return self.enter(text)

    def step_texts(self, col):
        w = self.widgets()
        return tuple(w[f'step{col}_{p}']['text'] for p in ('head', 'top', 'bottom')) if f'step{col}_head' in w else None

    def save_protocol(self, name, folder=None):
        self.tap('save')
        self.row_dialog('Protocol Name')
        self.enter(name)
        if folder is not None:
            self.row_dialog('Folder Location')
            self.go_folder(folder)
            self.tap('sl_select')
        self.ov('Save')

    def row_dialog(self, label):
        """Tap a form row of the dialog on top."""
        return self.ov(label)

    def go_folder(self, path):
        """Select Location / File Browser: the location in the side bar, then the folders down to path."""
        root, *rest = path.split('/')
        prefix = 'sl' if self.page()[1] == 'select_loc' else 'fb'
        self.tap({'Public': f'{prefix}_public', 'USB': f'{prefix}_usb', 'My Files': f'{prefix}_my'}[root])
        if root == 'My Files':
            rest = rest[1:]
        for name in rest:
            self.row(f'{prefix}_list', name)

    def run_setup_and_run(self, scan='All Channels'):
        assert self.page()[1] == 'run_setup'
        if scan != 'All Channels':
            self.row('rs_form', 'Scan Mode')
            self.ov(scan)
        self.tap('rs_run')


def runtime_state(h):
    v = h.read('evaluator')
    return json.dumps(v, sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_start_up_and_log_in(self):
        g = Rig(on=False)
        self.assertEqual(g.page(), ('off', 'dark'))
        self.assertFalse(g.touch(500, 300))                                        # dark screen
        self.assertTrue(g.switch('on'))                                            # guide p.42: startup screen, initialization tests
        self.assertEqual(g.page(), ('boot', 'startup'))
        g.step(5.0)
        self.assertFalse(g.touch(500, 300))
        g.step(5.0)
        self.assertEqual(g.page(), ('main', 'login'))                              # then the Login list
        self.assertEqual([c[0] for c in g.cells('users')], ['Admin', 'Guest', 'Service'])
        g.tap('create_user')                                                       # p.44: user name, then the password dialog
        g.enter('Analyst')
        self.assertEqual(g.overlay()['title'], 'Password')
        g.ov('Password')
        g.enter('pcr1234')
        g.ov('Confirm Password')
        g.enter('pcr1234')
        g.ov('Save Password')
        self.assertEqual((g.page(), g.text('user_name'), g.val('term.user')), (('main', 'home'), 'Analyst', 'Analyst'))
        g.tap('logout')
        self.assertEqual([c[0] for c in g.cells('users')][0], 'Analyst')         # the most recent user at the top (p.45)
        g.row('users', 'Analyst')
        self.assertEqual(g.overlay()['kind'], 'keyboard')                          # p.46: the keypad for the password
        self.assertFalse(g.enter('wrong1'))
        self.assertEqual(g.msg(), 'The password is not correct.')
        g.ok()
        self.assertEqual(g.page(), ('main', 'login'))
        g.row('users', 'Analyst')
        g.enter('pcr1234')
        self.assertEqual(g.page(), ('main', 'home'))
        g.login('Guest')                                                           # Guest: no keypad
        self.assertEqual(g.val('term.user'), 'Guest')
        DONE_TASKS.add('start up and log in')

    def test_load_a_plate(self):
        g = Rig()
        self.assertEqual(g.text('lid'), 'Open Lid')
        g.tap('lid')                                                               # p.47: Open Lid on the Home screen
        g.step(rt.LID_TIME_S + 0.2)
        self.assertEqual((g.val('lid.state'), g.text('lid')), ('open', 'Close Lid'))
        g.plate()
        g.call(g.A, 'plate.load', plate_id='p1')                                  # the plate is pressed into the block (robot)
        g.tap('lid')
        g.step(rt.LID_TIME_S + 0.2)
        self.assertEqual((g.val('lid.state'), g.val('plate.id'), g.text('lid')), ('closed', 'p1', 'Open Lid'))
        DONE_TASKS.add('load a plate')

    def test_create_and_run_a_protocol(self):
        g = Rig()
        g.tap('new_protocol')                                                      # p.88
        self.assertEqual((g.page()[1], g.disp()['title']), ('editor', 'New Protocol'))
        self.assertEqual([g.step_texts(j) for j in range(4)], [('1  Temperature', '95.0 °C', '3:00'), ('2  Temperature', '95.0 °C', '0:10'),
                                                               ('3  Temperature', '55.0 °C', '0:30'), ('4  GOTO', 'Step 2', '39 X')])
        g.tap('ed_name')
        g.enter('PCR1')
        g.tap('ed_volume')
        g.enter('20')
        g.edit_step(1, 'top', '94')                                                # tap the temperature of a step, keypad, OK
        g.edit_step(1, 'bottom', '0:15')
        g.tap('step2_head')                                                        # select step 3, Insert > Temperature
        g.tap('insert')
        g.ov('Temperature')
        self.assertEqual(g.step_texts(3), ('4  Temperature', '50.0 °C', '0:30'))   # p.89: a new step is 50 C for 0:30
        g.edit_step(3, 'top', '72')
        g.edit_step(3, 'bottom', '20')
        g.tap('step_next')
        self.assertEqual(g.step_texts(3), ('5  GOTO', 'Step 2', '39 X'))
        g.edit_step(3, 'bottom', '34')                                             # repeats of the GOTO step (p.99)
        g.save_protocol('PCR1')                                                    # Save As: name, My Files
        self.assertEqual(g.msg(), 'The protocol was saved.')
        g.ok()
        e = next(x for x in g.val('files.entries') if x['path'] == 'My Files/Admin/PCR1')
        self.assertEqual([s['kind'] for s in e['protocol']['steps']], ['temperature'] * 4 + ['goto'])
        self.assertEqual((e['protocol']['steps'][1]['temperature_k'], e['protocol']['steps'][1]['hold_s'], e['protocol']['steps'][4]['cycles']), (367.15, 15.0, 34))
        self.assertAlmostEqual(e['protocol']['sample_volume_m3'], 2e-8)
        g.tap('home')
        g.tap('lid')
        g.step(7.0)
        g.plate()
        g.call(g.A, 'plate.load', plate_id='p1')
        g.tap('lid')
        g.step(7.0)
        g.tap('files')
        g.row('fb_list', 'PCR1')
        g.tap('fb_edit')                                                           # Edit Protocol from the file, then Next
        self.assertEqual((g.disp()['title'], g.step_texts(1)[1]), ('Edit Protocol', '94.0 °C'))
        g.tap('next')                                                              # Run Setup (p.92)
        self.assertEqual(g.page()[1], 'run_setup')
        g.row('rs_form', 'Plate ID')
        g.enter('PL1')
        g.row('rs_form', 'Notification: Beep')
        g.ov('On')
        g.run_setup_and_run('SYBR/FAM')
        self.assertEqual((g.page()[1], g.val('run.state'), g.val('run.scan_mode')), ('run_status', 'lid_heating', 'sybr_fam'))
        g.step(600.0)
        self.assertEqual(g.val('run.state'), 'running')
        self.assertIn('Run In-Progress: PCR1', g.disp()['title'])
        DONE_TASKS.add('create and run a protocol')

    def test_add_a_gradient_to_a_step(self):
        g = Rig()
        g.tap('new_protocol')
        g.tap('step2_head')
        g.tap('options')                                                           # p.95: Step Options, Gradient checkbox
        self.assertEqual(g.overlay()['title'], 'Step Options')
        g.ov('Gradient')
        g.ov('On')
        labels = {b['label']: b.get('value') for b in g.overlay()['buttons']}
        self.assertEqual((labels['Gradient lower'], labels['Gradient upper']), ('50.0 °C', '60.0 °C'))
        g.ov('Gradient upper')
        g.enter('74')                                                              # within 24 C of the lower value
        g.ov('Gradient upper')
        self.assertFalse(g.enter('75'))
        self.assertIn('within 24 °C', g.msg())
        g.ok()
        labels = {b['label']: b.get('value') for b in g.overlay()['buttons']}
        self.assertEqual(labels['Gradient upper'], '74.0 °C')                      # refused: the step is unchanged
        g.ov('OK')
        self.assertEqual(g.step_texts(2), ('3  Gradient', '50.0-74.0 °C', '0:30'))
        g.save_protocol('GRAD', folder='Public')
        g.ok()
        e = next(x for x in g.val('files.entries') if x['path'] == 'Public/GRAD')
        self.assertEqual(e['protocol']['steps'][2], dict(kind='gradient', lower_k=323.15, upper_k=347.15, hold_s=30.0, plate_read=True, extend_s=0.0))
        DONE_TASKS.add('add a gradient to a step')

    def test_run_a_saved_protocol(self):
        g = Rig()
        g.tap('files')                                                             # p.111-114
        g.go_folder('Public/Bio-Rad qPCR')
        g.row('fb_list', 'CFX_2stepAmp+Melt')
        self.assertIn('Protocol File (read-only)', g.text('fb_details'))
        g.tap('fb_next')
        self.assertEqual((g.page()[1], g.val('protocol.path')), ('run_setup', 'Public/Bio-Rad qPCR/CFX_2stepAmp+Melt'))
        cells = dict((c[0], c[1]) for c in g.cells('rs_form'))
        self.assertEqual(cells['Save Location'], 'My Files/Admin')                 # the sample folder is read-only
        self.assertTrue(cells['Run File Name'].startswith('CFX_2stepAmp+Melt_20260101_'))
        g.tap('rs_run')
        self.assertEqual(g.val('run.state'), 'lid_heating')
        g.step(300.0)
        self.assertEqual(g.val('run.state'), 'running')
        DONE_TASKS.add('run a saved protocol')

    def test_pause_skip_or_stop_a_run(self):
        g = Rig()
        g.tap('files')
        g.go_folder('Public/Bio-Rad qPCR')
        g.row('fb_list', 'CFX_2stepAmp')
        g.tap('fb_next')
        g.tap('rs_run')
        g.step(300.0)
        self.assertEqual(g.page()[1], 'run_status')
        self.assertEqual((g.widgets()['rt_pause']['text'], g.frame()['displays']['status_bar_green']['on']), ('Pause Run', True))
        g.tap('rt_pause')                                                          # p.122: Pause / Resume
        self.assertEqual((g.val('run.state'), g.text('rt_pause'), g.frame()['displays']['status_bar_green']['blink']), ('paused', 'Resume Run', True))
        g.tap('rt_pause')
        self.assertEqual(g.val('run.state'), 'running')
        k0 = g.val('run.step')
        g.tap('rt_skip')                                                           # Skip Step
        self.assertNotEqual(g.val('run.step'), k0)
        g.tap('rt_stop')
        g.ov('Stop Run')                                                           # p.123: Stop Run
        self.assertEqual(g.val('run.state'), 'stopped')
        e = [x for x in g.val('files.entries') if x['kind'] == 'run']
        self.assertEqual((len(e), e[0]['parent'], e[0]['run']['status']), (1, 'My Files/Admin', 'stopped'))
        DONE_TASKS.add('pause, skip or stop a run')

    def test_shut_down(self):
        g = Rig()
        g.tap('logout')                                                            # p.49: log out, Shut Down, then the power switch
        g.tap('shut_down')
        self.assertEqual((g.page(), g.val('power.enabled'), g.val('term.shutdown')), (('off', 'dark'), False, True))
        self.assertFalse(g.touch(500, 300))
        self.assertTrue(g.switch('off'))
        self.assertTrue(g.switch('on'))
        self.assertEqual(g.page(), ('boot', 'startup'))
        DONE_TASKS.add('shut down')


# ---------------------------------------------------------------- other touch-screen functions of the guide
class OtherFunctions(unittest.TestCase):
    def test_password_requirement_and_user_profile(self):
        g = Rig(user=None)
        g.tap('create_user')
        g.enter('Tech')
        g.ov('Skip Password')                                                      # p.44: Skip Password
        self.assertFalse(g.val('term.users')[0]['has_password'])
        g.login('Admin')
        g.tap('tools')
        g.tap('tb_admin')
        g.tap('a_sys')
        g.row('sys_form', 'Password requirement')
        g.ov('Enabled')
        g.tap('sys_apply')
        self.assertEqual(g.msg(), 'The settings were applied.')
        g.ok()
        g.tap('back')
        g.tap('home')
        g.tap('logout')
        g.row('users', 'Tech')                                                     # p.57: prompted to create a password
        self.assertEqual(g.overlay()['title'], 'Password')
        g.ov('Password')
        g.enter('abcd1')
        g.ov('Confirm Password')
        g.enter('abcd1')
        g.ov('Save Password')
        self.assertEqual((g.page()[1], g.val('term.user'), g.val('term.users')[0]['has_password']), ('home', 'Tech', True))
        g.tap('tools')
        g.tap('t_profile')                                                         # p.73-74: Change Password (current, then new twice)
        g.row('profile_form', 'CFX Opus Password')
        g.ov('Current password')
        g.enter('abcd1')
        g.ov('OK')
        g.ov('New password')
        g.enter('efgh2')
        g.ov('Confirm password')
        g.enter('efgh2')
        g.ov('Save Password')
        self.assertEqual(g.msg(), 'The password was saved.')
        g.ok()
        g.row('profile_form', 'Email Address')                                     # p.74
        g.enter('tech@example.org')
        self.assertEqual(g.val('term.users')[0]['email'], 'tech@example.org')
        g.row('profile_form', 'BR.io Account')
        self.assertEqual(g.msg(), 'Unable to connect to BR.io:\nno network connection.')
        self.assertTrue(g.frame()['ui']['unsupported'])
        g.ok()

    def test_file_browser_folders_copy_rename_delete(self):
        g = Rig()
        g.tap('files')
        g.tap('fb_public')
        g.tap('fb_create')                                                         # p.132: Create Folder
        g.enter('Assays')
        g.row('fb_list', 'Assays')
        self.assertEqual(g.frame()['ui']['vars']['fb_loc'], 'Public/Assays')
        g.tap('fb_up')
        g.row('fb_list', 'Bio-Rad qPCR')
        g.row('fb_list', 'CFX_2stepAmp')
        g.tap('fb_options')                                                        # p.129: Options > Copy, Select Location
        g.ov('Copy')
        self.assertEqual(g.page()[1], 'select_loc')
        g.tap('sl_up')
        g.row('sl_list', 'Assays')
        g.tap('sl_select')
        self.assertEqual(g.msg(), 'Copied.')
        g.ok()
        self.assertIn('Public/Assays/CFX_2stepAmp', [x['path'] for x in g.val('files.entries')])
        g.tap('fb_options')                                                        # copy again: overwrite after Yes (p.130)
        g.ov('Copy')
        g.tap('sl_up')
        g.row('sl_list', 'Assays')
        g.tap('sl_select')
        self.assertEqual(g.overlay()['lines'], ['A file with the same name exists.', 'Overwrite it?'])
        g.ov('Yes')
        g.ok()
        g.tap('fb_options')
        g.ov('Rename')                                                             # read-only sample: refused
        g.enter('Mine')
        self.assertEqual(g.msg(), 'This location is read-only.')
        g.ok()
        g.tap('fb_up')
        g.row('fb_list', 'Assays')
        g.row('fb_list', 'CFX_2stepAmp')
        g.tap('fb_options')
        g.ov('Rename')                                                             # p.130: Rename
        g.enter('Mine')
        self.assertIn('Public/Assays/Mine', [x['path'] for x in g.val('files.entries')])
        g.row('fb_list', 'Mine')
        g.tap('fb_options')
        g.ov('Delete')
        g.ov('Delete')                                                             # p.130: confirmation
        self.assertEqual(g.msg(), 'The file was deleted.')
        g.ok()
        g.tap('fb_folder_opts')                                                    # p.134: delete the folder
        g.ov('Delete')
        g.ov('Delete')
        g.ok()
        self.assertEqual(g.frame()['ui']['vars']['fb_loc'], 'Public')
        self.assertNotIn('Public/Assays', [x['path'] for x in g.val('files.entries')])
        g.tap('fb_usb')
        self.assertEqual(g.widgets()['fb_list'].get('empty_text'), 'Empty folder')
        g.tap('fb_network')
        self.assertEqual(g.msg(), 'No network connection.')

    def test_run_views_reports_logs_and_curves(self):
        g = Rig()
        g.tap('lid')
        g.step(7.0)
        g.plate()
        g.call(g.A, 'plate.load', plate_id='p1')
        g.tap('lid')
        g.step(7.0)
        g.tap('files')
        g.go_folder('Public/Bio-Rad qPCR')
        g.row('fb_list', 'CFX_2stepAmp')
        g.tap('fb_next')
        g.tap('rs_run')
        g.step(1500.0)
        g.tap('rt_time')                                                           # p.121: Time Remaining
        t = g.text('tr_time')
        self.assertRegex(t, r'^\d\d:\d\d:\d\d$')
        g.tap('rt_curves')
        plot = g.widgets()['curve_plot']
        self.assertEqual(len([s for s in plot['series'] if s['points']]), 2)        # two SYBR wells in channel 1
        self.assertGreater(len(plot['series'][0]['points']), 5)
        g.tap('rt_status')
        self.assertEqual(g.page()[1], 'run_status')
        g.tap('rt_stop')
        g.ov('Stop Run')
        g.tap('home')
        g.tap('tools')
        g.tap('t_reports')                                                         # p.56: Run Reports
        g.row('rr_list', 'R001')
        g.tap('rr_export')
        self.assertEqual(g.msg(), 'The run report was exported to the USB drive.')
        g.ok()
        g.tap('rr_recover')
        g.ok()
        self.assertEqual(g.val('usb.files'), ['R001_CFX_2stepAmp.zpcr', 'RunReports/R001_CFX_2stepAmp_report.pdf'])
        g.tap('back')
        g.tap('t_logs')                                                            # System Logs
        msgs = [c[1] for c in g.cells('log_list')]
        self.assertIn('RUN_STOPPED', msgs)
        self.assertNotIn('LID_OPEN', msgs)                                         # Messages Log: run messages; Usage Log: every event
        g.tap('log_usage')
        self.assertIn('LID_OPEN', [c[1] for c in g.cells('log_list')])
        g.tap('log_export')
        g.ok()
        self.assertIn('Exports/SystemMessageLog.txt', g.val('usb.files'))
        g.tap('log_clear')
        g.ov('Clear')
        self.assertEqual(g.cells('log_list'), [])
        g.tap('back')
        g.tap('t_reports')
        g.row('rr_list', 'R001')
        g.tap('rr_trim')
        g.ov('Remove')
        self.assertEqual(g.val('runs.count'), 0)

    def test_system_settings_clock_name_and_screen_saver(self):
        g = Rig()
        g.tap('tools')
        g.tap('tb_admin')
        g.tap('a_sys')
        g.row('sys_form', 'Time zone')                                             # p.60: the time zone shifts the shown time
        g.ov('(UTC+08:00) Beijing, Chongqing, Hong Kong')
        g.row('sys_form', 'Instrument name')
        g.enter('qPCR1')
        g.row('sys_form', 'Screen saver')
        g.ov('Enabled')
        g.row('sys_form', 'Idle time')
        g.ov('1 min')
        g.tap('sys_apply')
        g.ok()
        self.assertEqual(g.val('term.settings'), dict(name='qPCR1', time_zone='(UTC+08:00) Beijing, Chongqing, Hong Kong', password_required=False,
                                                     screen_saver=True, screen_saver_idle_s=60.0))
        self.assertEqual(g.val('term.datetime')[11:13], '19')
        g.row('sys_form', 'Date')                                                  # p.61: calendar and time pickers
        g.ov('+')
        g.ov('OK')
        g.tap('sys_apply')
        g.ok()
        self.assertEqual(g.val('term.datetime')[:10], '2026-01-02')
        g.step(59.0)
        self.assertEqual(g.page()[0], 'main')
        g.step(1.0)
        self.assertEqual(g.page(), ('saver', 'saver_page'))                        # p.57: the screen saver after the idle time
        g.touch(500, 300)
        self.assertEqual(g.page(), ('main', 'system_settings'))                    # a touch returns to the page
        g.tap('back')
        self.assertEqual((g.page()[1], g.disp()['title']), ('home', 'qPCR1'))     # the Home screen shows the new name

    def test_user_management_email_updates_diagnostics(self):
        g = Rig(user=None)
        g.tap('create_user')
        g.enter('Bob')
        g.ov('Skip Password')
        g.login('Admin')
        g.tap('tools')
        g.tap('tb_admin')
        g.tap('a_users')                                                           # p.82-83
        g.row('um_list', 'Bob')
        g.tap('um_reset')
        g.ov('New password')
        g.enter('bob12')
        g.ov('Confirm password')
        g.enter('bob12')
        g.ov('Save Password')
        self.assertEqual(g.msg(), 'The password was reset.')
        g.ok()
        g.tap('um_remove')
        self.assertEqual(g.overlay()['lines'][0], 'Removing a user permanently deletes')
        g.ov('Remove')
        self.assertNotIn('Bob', [u['name'] for u in g.val('term.users')])
        g.tap('back')
        g.tap('a_email')                                                           # p.69-70
        g.row('email_form', 'SMTP server')
        g.enter('smtp.lab.org')
        g.row('email_form', 'Authentication')
        g.ov('Off')
        g.tap('email_save')
        g.ov('Yes')
        self.assertEqual(g.msg(), 'The email settings were saved.')
        g.ok()
        self.assertEqual(g.val('email.settings')['server'], 'smtp.lab.org')
        g.tap('email_test')
        self.assertIn('no network connection', g.msg())
        g.ok()
        g.tap('back')
        g.tap('a_updates')                                                         # p.151: no installer on the USB drive
        self.assertFalse(g.widgets()['upd_update']['enabled'])
        g.tap('upd_details')
        self.assertIn('No update package', g.msg())
        g.ok()
        g.tap('upd_cancel')
        g.call(g.E, 'fault.inject', fault='lid_obstructed', active=True)
        g.tap('a_diag')                                                            # p.58: diagnostic self tests
        g.tap('diag_run')
        res = {c[0]: c[1] for c in g.cells('diag_list')}
        self.assertEqual((res['Lid motor'], res['Block heating / cooling']), ('Fail', 'Pass'))
        g.tap('back')
        g.tap('a_net')                                                             # p.62-68: no network hardware
        g.tap('net_ip_config')
        g.ov('Test')
        self.assertEqual(g.msg(), 'No Internet connection found.')

    def test_demo_shipping_about_and_power_failure(self):
        g = Rig()
        g.tap('tools')
        g.tap('t_demo')                                                            # p.56: Real-Time PCR Demo
        g.tap('demo_sybr')
        g.step(20.0)
        pts = g.widgets()['demo_plot']['series'][0]['points']
        self.assertEqual(len(pts), 20)
        g.tap('back')
        g.tap('t_ship')                                                            # p.154-155
        g.tap('ship_lock')
        g.ov('No')
        g.tap('ship_lock')
        g.ov('Yes')
        self.assertIn('shipping plate', g.msg())
        g.ok()
        g.tap('back')
        g.tap('t_about')                                                           # p.55: About
        about = {c[0]: c[1] for c in g.cells('about_form')}
        self.assertEqual((about['Opus Version'], about['Serial Number']), (rt.SOFTWARE_VERSION, rt.SERIAL_NUMBER))
        g.tap('back')
        g.tap('home')
        g.tap('files')
        g.go_folder('Public/Bio-Rad qPCR')
        g.row('fb_list', 'CFX_2stepAmp')
        g.tap('fb_next')
        g.tap('rs_run')
        g.step(400.0)
        g.call(g.E, 'fault.inject', fault='power_loss', active=True)              # p.122: power failure during a run
        self.assertEqual(g.page(), ('off', 'dark'))
        g.step(60.0)
        g.call(g.E, 'fault.inject', fault='power_loss', active=False)
        g.step(10.5)
        self.assertEqual(g.page(), ('main', 'login'))
        self.assertEqual(g.overlay()['dialog'], 'd_power_failure')                # the warning message; the run resumes
        self.assertIn(g.val('run.state'), ('lid_heating', 'running'))


# ---------------------------------------------------------------- panel = direct capability
class Equivalence(unittest.TestCase):
    def test_panel_and_direct_calls_give_the_same_runtime_state(self):
        a, b = Rig(user=None), Rig(user=None)
        a.login('Admin')
        b.call(b.A, 'user.login', user='Admin')
        a.tap('new_protocol')
        a.edit_step(2, 'top', '60')
        a.save_protocol('EQ', folder='Public')
        a.ok()
        proto = copy.deepcopy(rt.NEW_PROTOCOL)
        proto['steps'][2]['temperature_k'] = 333.15
        b.call(b.A, 'files.save_protocol', folder='Public', name='EQ', protocol=dict(proto, name='New Protocol'), from_path=None)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.tap('next')
        name = dict((c[0], c[1]) for c in a.cells('rs_form'))['Run File Name']
        b.call(b.A, 'protocol.set', **dict(proto, name='EQ'))
        a.tap('rs_run')
        b.call(b.A, 'protocol.set', **dict(proto, name='EQ'))
        b.call(b.A, 'run.configure', folder='Public', run_file_name=name, plate_id='', notify_beep=False, notify_email=False)
        b.call(b.A, 'run.start', scan_mode='all_channels')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        for g in (a, b):
            g.step(300.0)
        a.tap('rt_pause')
        b.call(b.A, 'run.pause')
        a.tap('rt_pause')
        b.call(b.A, 'run.resume')
        a.tap('rt_stop')
        a.ov('Stop Run')
        b.call(b.A, 'run.stop')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.tap('home')
        a.tap('logout')
        a.tap('shut_down')
        b.call(b.A, 'user.logout')
        b.call(b.A, 'system.shutdown')
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


# ---------------------------------------------------------------- refusals, locks, time-outs
class Rules(unittest.TestCase):
    def test_power_off_gate_and_switch(self):
        g = Rig(on=False)
        before = runtime_state(g.h)
        self.assertFalse(g.touch(100, 100))
        self.assertFalse(g.switch('off'))                                          # already off
        self.assertEqual(runtime_state(g.h), before)
        g.call(g.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=50.0))
        self.assertFalse(g.switch('on'))                                           # no mains: stays dark
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'MAINS_OUT_OF_RANGE')

    def test_refusals_show_the_device_message_and_change_nothing(self):
        g = Rig()
        g.tap('files')
        g.go_folder('Public/Bio-Rad qPCR')
        g.row('fb_list', 'CFX_2stepAmp')
        g.tap('fb_next')
        g.tap('rs_lid')
        g.step(7.0)
        before = runtime_state(g.h)
        self.assertFalse(g.tap('rs_run'))                                          # lid open: the run is not started
        self.assertEqual(g.msg(), 'Close the lid before starting the run.')
        self.assertEqual(runtime_state(g.h), before)
        g.ok()
        g.tap('back')
        g.tap('home')
        g.tap('new_protocol')
        g.tap('step0_head')
        for _ in range(3):
            g.tap('remove')
        before = json.dumps(g.frame()['ui']['vars']['steps'])
        self.assertFalse(g.tap('remove'))                                          # the last step stays
        self.assertEqual(g.msg(), 'A protocol needs at least one step.')
        g.ok()
        self.assertEqual(json.dumps(g.frame()['ui']['vars']['steps']), before)
        g.tap('insert')
        g.ov('Temperature')
        g.tap('insert')
        g.ov('Temperature')                                                        # GOTO, 50 C, 50 C
        g.tap('step1_bottom')
        self.assertFalse(g.enter('Inf'))                                           # an infinite hold only in the last step
        self.assertIn('last step', g.msg())
        g.ok()
        g.save_protocol('X', folder='Public')
        self.assertEqual(g.msg(), 'GOTO must return to an earlier\ntemperature or gradient step.')   # the protocol is checked first
        g.ok()
        g.tap('step0_head')
        g.tap('remove')                                                            # without the GOTO step
        g.save_protocol('X', folder='Public/Bio-Rad qPCR')
        self.assertEqual(g.msg(), 'This location is read-only.')
        self.assertNotIn('Public/Bio-Rad qPCR/X', [x['path'] for x in g.val('files.entries')])

    def test_guest_rights_and_remote_logout(self):
        g = Rig(user='Guest')
        g.tap('files')
        self.assertNotIn('fb_my', g.widgets())                                     # p.127: no My Files for Guest
        self.assertEqual(g.frame()['ui']['vars']['fb_loc'], 'Public')
        g.tap('home')
        g.tap('tools')
        self.assertNotIn('tb_admin', g.widgets())
        g.tap('t_profile')
        self.assertEqual([c[0] for c in g.cells('profile_form')], ['Email Address'])   # Guest sets only the email
        g.call(g.A, 'user.logout')                                                 # logged out by a remote command: back to the Login list
        self.assertEqual(g.page(), ('main', 'login'))
        g.call(g.A, 'user.login', user='Admin')
        self.assertEqual(g.page(), ('main', 'home'))

    def test_time_outs(self):
        g = Rig(on=False)
        g.switch('on')
        g.step(9.9)
        self.assertEqual(g.page()[0], 'boot')
        g.step(0.2)
        self.assertEqual(g.page(), ('main', 'login'))
        g.login('Admin')
        g.tap('tools')
        g.step(7200.0)                                                             # the screen saver is off by default: no time-out
        self.assertEqual(g.page(), ('main', 'tools'))
        g.tap('t_logs')
        g.tap('log_export')
        g.step(600.0)
        self.assertIsNotNone(g.msg())                                              # message boxes wait for OK


# ---------------------------------------------------------------- rendering, capture, contract
class CallerRules(unittest.TestCase):
    """Sync audit 2026-10-19, rule 2: Run Reports export / recover / delete act as the session caller.  Another agent's run is refused on the panel
    ('Not allowed: the run belongs to another user.') and directly (ACCESS_DENIED), state unchanged; runs.export without a run exports only the
    caller's runs; the owner's work.  Navigation stays free during another agent's run (sweep 2026-10-19)."""
    NOT_ALLOWED = 'Not allowed: the run belongs to another user.'

    def test_another_agent_can_look_but_not_stop_the_run(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.tap('lid')
        g.step(7.0)
        g.plate()
        g.call(g.A, 'plate.load', plate_id='p1')
        g.tap('lid')
        g.step(7.0)
        g.tap('files')
        g.go_folder('Public/Bio-Rad qPCR')
        g.row('fb_list', 'CFX_2stepAmp')
        g.tap('fb_next')
        g.tap('rs_run')
        g.step(60.0)
        before = runtime_state(g.h)
        A, g.A = g.A, B                                                                   # B's gestures on the same screen
        g.tap('rt_stop')
        self.assertFalse(g.ov('Stop Run'))                                                # the invoked Stop Run is refused ...
        self.assertEqual(g.msg(), self.NOT_ALLOWED)                                       # ... with the device's message box
        g.ok()
        self.assertTrue(g.tap('home'))                                                    # looking around is free
        self.assertTrue(g.tap('files'))
        self.assertEqual(runtime_state(g.h), before)
        g.A = A
        for cap, args in (('protocol.set', rt.PROTOCOL_EXAMPLE), ('run.start', dict(scan_mode='sybr_fam'))):   # narrowed guard: the instrument's own lock
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'RUN_IN_PROGRESS')
        self.assertEqual(g.call(B, 'run.pause', expect='rejected')['error']['code'], 'ACCESS_DENIED')

    def test_run_reports_of_another_agent_are_refused(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.tap('lid')
        g.step(7.0)
        g.plate()
        g.call(g.A, 'plate.load', plate_id='p1')
        g.tap('lid')
        g.step(7.0)
        g.tap('files')
        g.go_folder('Public/Bio-Rad qPCR')
        g.row('fb_list', 'CFX_2stepAmp')
        g.tap('fb_next')
        g.tap('rs_run')
        g.step(60.0)
        g.tap('rt_stop')
        g.ov('Stop Run')
        self.assertEqual(g.h._owners.get('R001'), 'agent')                                  # the panel-started run belongs to A
        g.tap('home')
        g.tap('tools')
        g.tap('t_reports')
        g.row('rr_list', 'R001')
        before = runtime_state(g.h)
        A, g.A = g.A, B                                                                   # B's gestures on the same screen
        for wid in ('rr_export', 'rr_recover'):
            self.assertFalse(g.tap(wid))
            self.assertEqual(g.msg(), self.NOT_ALLOWED)
            g.ok()
        g.tap('rr_trim')
        self.assertFalse(g.ov('Remove'))
        self.assertEqual(g.msg(), self.NOT_ALLOWED)
        g.ok()
        for cap, args in (('runs.export', dict(run_id='R001')), ('runs.recover', dict(run_id='R001')), ('runs.delete', dict(run_ids=['R001']))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.call(B, 'runs.export', expect='rejected')['error']['code'], 'NO_RUN_DATA')    # no run of B's own
        self.assertEqual(runtime_state(g.h), before)
        g.A = A
        self.assertEqual(g.call(A, 'runs.export')['files'], ['RunReports/R001_CFX_2stepAmp_report.pdf'])
        g.row('rr_list', 'R001')
        g.tap('rr_trim')
        g.ov('Remove')
        self.assertEqual(g.val('runs.count'), 0)


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.tap('files')
            g.go_folder('Public/Bio-Rad qPCR')
            g.row('fb_list', 'CFX_2stepAmp+Melt')
            g.tap('fb_next')
            g.tap('rs_run')
            g.step(900.0)
            return g
        a, b = scenario(), scenario()
        for d in SPEC['displays']:
            ia, ib = a.h.panel.render(d), b.h.panel.render(d)
            self.assertEqual((ia.dtype.name, ia.shape[2]), ('uint8', 3))
            self.assertTrue((ia == ib).all())
        self.assertEqual(a.png(), b.png())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {}

        def save(name):
            shots[name] = a.h.panel.save_png(SCR, OUT / f'{name}.png')
            shots[name].update(path=str(Path(shots[name]['path']).relative_to(ROOT)), page=a.page()[1])
        save('run_status')
        a.tap('home')
        save('home')
        a.tap('new_protocol')
        save('editor')
        a.tap('step1_head')
        a.tap('options')
        save('step_options')
        a.ov('OK')
        a.tap('home')
        a.tap('tools')
        a.tap('tb_admin')
        save('menu')
        a.tap('a_sys')
        a.tap('sys_apply')
        a.ok()
        a.tap('back')
        a.tap('tb_user')
        a.tap('t_profile')
        a.row('profile_form', 'Network Folder')
        save('error')
        a.ok()
        shots['status_bar_green'] = a.h.panel.save_png('status_bar_green', OUT / 'status_bar_green.png')
        shots['status_bar_green']['path'] = str(Path(shots['status_bar_green']['path']).relative_to(ROOT))
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((ROOT / s['path']).read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True) + '\n')

    def test_capture_through_the_session(self):
        g = Rig(on=False)
        r = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        self.assertEqual((r['image']['width'], r['image']['height']), (1024, 600))
        g.switch('on')
        r2 = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])
        c = copy.deepcopy(g.h)                                                     # the panel state is copied with its host
        self.assertIs(c.panel.host, c)
        r = g.call(g.A, 'panel.frame', expect='rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        caps = g.A.describe(instance_id=INST)
        self.assertEqual({k for k in caps['capabilities'] if k.startswith('panel.')}, {'panel.touch', 'panel.switch', 'panel.capture'})
        self.assertEqual(caps['asset_version'], rt.ASSET_VERSION)

    def test_spec_and_contract(self):
        meta = rt.export_biorad_cfxopus_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.touch', 'panel.switch', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        for pid, p in SPEC['screens']['main']['nav']['pages'].items():
            self.assertTrue(p.get('evidence'), pid)
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k, v in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'state_effects', 'errors', 'availability'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        for k, v in list(SPEC['inputs'].items()) + list(SPEC['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((rt.ASSET_DIR / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('cfx_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:                                                   # (skipped when a single test is run alone)
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
