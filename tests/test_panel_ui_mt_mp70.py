"""Front-panel tests for CHEM-006 mt_mp70 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/mt_mp70.py):
python3 -m pytest tests/test_panel_ui_mt_mp70.py -q

Gestures only through the session API (panel.touch / panel.press); the host (evaluator) inserts capillaries at T(start), handles the SD card and
sets the furnace sensor error.  Themes: the five key procedures of the menu survey (start up and log in, analysis with a method, manual method,
stop an analysis, shut down); lock / unlock and Reset on a locked instrument, screen saver, forced shut-down, Info key, shortcuts, manual Set,
melting range, results (include / exclude, statistics, select analysis, diagram), calibration and adjustment with an injected sensor offset,
Manual operations, Setup settings and users, maintenance; panel = direct command; refusals as the device message; rendering; contract."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import mt_mp70 as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'mp70_01'
TERM = 'terminal'
OUT = ROOT / 'outputs/panel_ui/mt_mp70'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['start up and log in', 'run an analysis with a method', 'run a manual method', 'stop an analysis', 'shut down']
DONE_TASKS = set()
VAN = dict(substance_id='vanillin', mass_kg=2e-6, purity=1.0, particle_size_m=50e-6, decomposition_onset_k=None, decomposition_source='')
METHOD = dict(title='Vanillin', type='Melting point', start_c=70, waiting_s=10, rate_c_min=1.0, end_c=90, determination='Automatic', criterion='transmission',
              comment='', calibration=None)


class Rig:
    def __init__(self, on=True):
        self.h = rt.MtMp70Runtime()
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        if on:
            assert self.press('key_power')
            self.h.step(10)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        return r['result'] if expect == 'succeeded' else r

    def host(self, cap, **args):
        return self.call(self.E, cap, **args)

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

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

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label and b['id'] != 'field':
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def ov_row(self, label):
        for b in self.overlay()['buttons']:
            if b.get('style') == 'row' and b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(label)

    def msg(self):
        return ' '.join(self.overlay()['lines'])

    def row(self, wid, first):
        for _ in range(20):
            w = self.widgets()[wid]
            firsts = [c[0] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, firsts)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def type(self, text):
        ov = self.overlay()
        labels = {b['label'] for b in ov['buttons']}
        if 'q' not in labels and 'Q' not in labels and '7' in labels:
            for ch in text:
                assert self.ov(ch), ch
            return
        if ov['field']:
            self.ov('Del')
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit():
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if ch.isupper() != (self.overlay()['mode'] == 'upper'):
                    self.ov('Shift')
                self.ov(ch)

    def set_row(self, wid, label, value):
        self.row(wid, label)
        if self.overlay()['kind'] == 'keyboard':
            self.type(str(value))
            assert self.ov('OK'), self.overlay()
        else:
            assert self.ov(value), (label, value)

    def home(self):
        return self.press('key_home_right')

    def new_method(self, **values):
        """Methods > New > fields > Save."""
        self.home()
        self.tap('h_methods')
        self.tap('m_new')
        for label, value in values.items():
            self.set_row('mf_rows', label, value)
        assert self.tap('mf_save'), self.overlay()

    def run_method(self, method_id='M0001', channels=(0, 1), substance=VAN):
        """Methods > method > Start > Start analysis > Start; T(start) reached; insert; Start."""
        self.home()
        self.tap('h_methods')
        self.row('m_list', method_id)
        self.tap('me_start')
        assert self.tap('sw_start'), self.overlay()
        self.h.step(150)
        assert self.text('a_status') == 'T(start) reached', self.text('a_status')
        for ch in channels:
            self.host('capillary.insert', channel=ch, **substance)
        assert self.tap('a_start')

    def finish(self, limit=3000):
        t = 0
        while self.h.phase == 'running' and t < limit:
            self.h.step(30)
            t += 30


def make(r, **over):
    r.call(r.A, 'method.save', method_id=None, values=dict(METHOD, **over))


class SurveyProcedures(unittest.TestCase):
    def test_start_up_and_log_in(self):
        r = Rig(on=False)
        self.assertEqual(r.page(), ('off', 'dark'))
        self.assertFalse(r.frame()['displays']['power_led']['on'])
        self.assertTrue(r.press('key_power'))
        self.assertEqual(r.page(), ('starting', 'starting'))
        self.assertEqual(r.text('st_text'), 'Starting up the system')
        self.assertFalse(r.touch(300, 300))
        r.h.step(10)
        self.assertEqual(r.page(), ('main', 'home'))                               # default user without password
        r.home()
        r.tap('h_setup')
        r.tap('su_global')
        r.tap('gl_policies')
        r.set_row('set_policies_rows', 'Login without password at start (default user)', 'Off')
        r.tap('set_policies_save')
        r.press('key_power', 2.0)                                                   # 1...4 s: shut down
        r.press('key_power')
        r.h.step(10)
        self.assertEqual(r.page(), ('login', 'login'))
        self.assertTrue(r.tap('lg_login'))                                          # the user name from the list, no password
        self.assertEqual((r.page(), r.val('users.state')['current']), (('main', 'home'), 'Administrator'))
        DONE_TASKS.add('start up and log in')

    def test_run_an_analysis_with_a_method(self):
        r = Rig()
        r.new_method(**{'Title': 'Vanillin', 'Start temperature': '70', 'Waiting time': '10', 'End temperature': '90'})
        self.assertEqual(r.val('methods.catalog')[0]['title'], 'Vanillin')
        r.home()
        r.tap('h_methods')
        r.row('m_list', 'M0001')
        r.tap('me_start')
        r.row('sw_rows', 'Analysis comment')
        r.type('lot 7')
        r.ov('OK')
        r.tap('sw_start')
        self.assertEqual((r.page(), r.text('a_status')), (('main', 'analysis'), 'Go to T(start)'))
        self.assertEqual(r.widgets()['tb_task']['fill'], 'yellow')                  # the Task panel shows a running task (p.9)
        r.h.step(150)
        self.assertEqual(r.text('a_status'), 'T(start) reached')
        for ch in (0, 1, 2):
            r.host('capillary.insert', channel=ch, **VAN)
        self.assertTrue(r.tap('a_start'))
        self.assertEqual(r.text('a_status'), 'Waiting time')
        r.h.step(12)
        self.assertEqual(r.text('a_status'), 'Ramp')
        r.finish()
        self.assertEqual(r.text('a_status'), 'Create report')
        self.assertEqual([r.text(f'a_cap{i}') for i in range(4)], ['82.7', '82.7', '82.7', '-'])
        r.tap('a_results')
        self.assertEqual(r.widgets()['r_list']['cells'], [['1', '82.7', 'included'], ['2', '82.7', 'included'], ['3', '82.7', 'included']])
        res = r.val('results.analyses')[-1]
        self.assertEqual((res['comments']['analysis'], res['method_id']), ('lot 7', 'M0001'))
        r.tap('r_back')
        self.assertEqual((r.page(), r.val('analysis.state')), (('main', 'home'), None))
        DONE_TASKS.add('run an analysis with a method')

    def test_run_a_manual_method(self):
        r = Rig()
        r.home()
        r.tap('h_manual')
        r.tap('mn_method')
        r.set_row('mm_rows', 'Start temperature', '70')
        r.set_row('mm_rows', 'End temperature', '90')
        r.set_row('mm_rows', 'Method type', 'Melting range')
        self.assertEqual(r.val('manual.method')['type'], 'Melting range')
        r.tap('mm_start')
        r.h.step(150)
        r.host('capillary.insert', channel=0, **VAN)
        r.tap('a_start')
        r.finish()
        self.assertRegex(r.text('a_cap0'), r'^8\d\.\d/8\d\.\d$')                    # start / end of melting (p.10)
        r.tap('a_results')
        self.assertRegex(r.widgets()['r_list']['cells'][0][1], r'^8\d\.\d - 8\d\.\d$')
        DONE_TASKS.add('run a manual method')

    def test_stop_an_analysis(self):
        r = Rig()
        make(r)
        r.run_method()
        r.h.step(100)
        self.assertTrue(r.press('key_reset'))
        self.assertEqual(r.msg(), 'Do you want to stop the analysis?')
        r.ov('No')
        self.assertEqual(r.val('cycle.phase'), 'running')
        r.tap('a_stop')
        r.ov('Yes')
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        self.assertEqual(r.msg(), 'Do you want to save the results?')
        r.ov('Yes')
        self.assertEqual(r.page(), ('main', 'results'))
        self.assertTrue(r.val('results.analyses')[-1]['stopped'])
        DONE_TASKS.add('stop an analysis')

    def test_shut_down(self):
        r = Rig()
        r.tap('h_logout')
        self.assertEqual(r.page(), ('login', 'login'))
        self.assertTrue(r.tap('lg_shutdown'))
        self.assertEqual((r.val('power.enabled'), r.page()), (False, ('off', 'dark')))
        DONE_TASKS.add('shut down')


class Functions(unittest.TestCase):
    def test_lock_and_reset_while_locked(self):
        r = Rig()
        self.assertFalse(r.tap('h_lock'))
        self.assertEqual(r.msg(), 'Only users with a password can lock the instrument.')
        r.ov('OK')
        r.tap('h_userdata')
        r.ov('Change password')
        r.ov_row('New password')
        r.type('pw')
        r.ov('OK')
        r.ov_row('Confirm password')
        r.type('pw')
        r.ov('OK')
        r.ov('OK')
        make(r)
        r.run_method()
        r.home()
        self.assertTrue(r.tap('h_lock'))
        self.assertEqual((r.page(), r.text('lk_user')), (('locked', 'locked'), 'Locked by Administrator'))
        r.h.step(30)
        self.assertEqual(r.val('cycle.phase'), 'running')                           # running tasks continue (p.16)
        self.assertTrue(r.press('key_reset'))                                       # Reset ends the task; the instrument stays locked
        self.assertEqual((r.val('cycle.phase'), r.page()[0]), ('cancelled', 'locked'))
        r.row('lk_rows', 'Password')
        r.type('xx')
        r.ov('OK')
        self.assertFalse(r.tap('lk_login'))
        r.ov('OK')
        r.row('lk_rows', 'Password')
        r.type('pw')
        r.ov('OK')
        r.tap('lk_login')
        self.assertEqual(r.page()[0], 'main')

    def test_screen_saver_forced_shutdown_and_info(self):
        r = Rig()
        self.assertTrue(r.press('key_info'))
        self.assertIn('Firmware 2.10', r.overlay()['lines'])
        r.ov('OK')
        self.assertTrue(r.press('key_power', 0.3))
        self.assertEqual(r.page(), ('saver', 'dark'))
        self.assertTrue(r.frame()['displays']['power_led']['blink'])                # the light fades (p.6)
        r.press('key_power', 0.3)
        self.assertEqual(r.page()[0], 'main')
        make(r)
        r.run_method()
        self.assertFalse(r.press('key_power', 2.0))                                 # 1...4 s: refused while a task runs
        self.assertTrue(r.val('power.enabled'))
        r.ov('OK')
        self.assertTrue(r.press('key_power', 6.0))                                  # > 5 s: forced shut-down
        self.assertEqual((r.val('power.enabled'), r.val('cycle.phase')), (False, 'cancelled'))

    def test_shortcuts_direct_and_indirect(self):
        r = Rig()
        make(r)
        r.home()
        r.tap('h_methods')
        r.row('m_list', 'M0001')
        r.tap('me_home')
        r.ov('Direct shortcut')
        r.tap('me_home')
        r.ov('Indirect shortcut')
        r.home()
        self.assertEqual(r.widgets()['h_shortcuts']['cells'], [['Vanillin', 'direct'], ['Vanillin', 'indirect']])
        r.row('h_shortcuts', 'Vanillin')                                            # direct: the analysis window opens (p.20)
        self.assertEqual((r.page(), r.val('analysis.state')['stage']), (('main', 'analysis'), 'go_to_tstart'))

    def test_manual_determination_set(self):
        r = Rig()
        make(r, determination='Manual')
        r.run_method(channels=(0,))
        r.h.step(30)
        self.assertTrue(r.tap('a_cap0'))
        self.assertAlmostEqual(r.val('analysis.state')['capillaries'][0]['manual_k'], r.val('thermal.indicated_temperature_k'), places=2)
        self.assertFalse(r.tap('a_cap0'))                                           # once per capillary
        r.finish()
        self.assertEqual(r.val('results.analyses')[-1]['capillaries'][0]['value_k'], r.val('analysis.state')['capillaries'][0]['manual_k'])

    def test_results_status_statistics_select_diagram(self):
        r = Rig()
        make(r)
        r.run_method(channels=(0, 1, 2))
        r.tap('a_diagram')
        r.h.step(60)
        self.assertEqual(sum(1 for x in r.widgets()['dg_plot']['series'] if x['points']), 3)
        r.tap('dg_back')
        r.finish()
        r.tap('a_results')
        r.row('r_list', '2')
        self.assertEqual(r.widgets()['r_list']['cells'][1][2], 'excluded')
        r.tap('r_stats')
        self.assertEqual(r.overlay()['lines'][0], 'n = 2')
        r.ov('OK')
        self.assertFalse(r.tap('r_print'))
        self.assertEqual(r.msg(), 'No printer is connected.')
        r.ov('OK')
        r.tap('r_select')
        self.assertEqual(len(r.widgets()['rsel_list']['cells']), 1)

    def test_calibration_and_adjustment(self):
        r = Rig()
        r.host('sensor.configure_error', offset_k=2.0)                              # the sensor reads 2 K high
        for sub, start, end in (('Benzophenone', 40, 55), ('Benzoic acid', 115, 130)):
            make(r, title=sub, start_c=start, end_c=end, calibration=sub)
        r.run_method('M0001', channels=(0, 1), substance=dict(VAN, substance_id='benzophenone'))
        r.finish()
        r.tap('a_results')
        self.assertEqual(r.text('r_cal'), 'Calibration Benzophenone: NOT within the tolerance')
        r.tap('r_back')
        r.tap('h_manual')
        r.tap('mn_off')                                                             # cool down, take the capillaries out
        r.h.step(900)
        self.assertTrue(r.val('display.screensaver'))                             # 1.2.0: 15 min without a touch (default delay)
        r.press('key_power')
        for ch in (0, 1):
            r.host('sample.unload', channel=ch)
        r.run_method('M0002', channels=(0, 1), substance=dict(VAN, substance_id='benzoic_acid'))
        r.finish()
        r.home()
        r.tap('h_setup')
        r.tap('su_adj')
        self.assertEqual(len(r.widgets()['ad_list']['cells']), 2)
        self.assertTrue(r.tap('ad_adjust'))
        adj = r.val('adjustment.state')
        self.assertAlmostEqual(adj['a'], 1.0, delta=0.01)
        self.assertTrue(-3.0 < adj['b'] < -2.0, adj)                                # the 2 K sensor offset plus the clear-point bias of the method
        r.ov('OK')
        r.home()
        r.tap('h_manual')
        r.tap('mn_off')
        r.h.step(1500)
        self.assertTrue(r.val('display.screensaver'))                             # 1.2.0: 25 min without a touch, the screen saver is on
        r.press('key_power')                                                        # a short press on the power button ends it
        self.assertFalse(r.val('display.screensaver'))
        for ch in (0, 1):
            r.host('sample.unload', channel=ch)
        r.run_method('M0001', channels=(0, 1), substance=dict(VAN, substance_id='benzophenone'))
        r.finish()
        r.tap('a_results')
        self.assertEqual(r.text('r_cal'), 'Calibration Benzophenone: within the tolerance')   # the adjusted instrument passes (p.41)

    def test_manual_operations(self):
        r = Rig()
        r.home()
        r.tap('h_manual')
        r.tap('mn_set')
        r.ov_row('Furnace temperature')
        r.type('120')
        r.ov('OK')
        r.ov('OK')
        r.h.step(200)
        self.assertAlmostEqual(r.val('thermal.indicated_temperature_k') - 273.15, 120, delta=0.1)
        self.assertTrue(r.tap('mn_off'))
        self.assertFalse(r.val('furnace.on'))
        self.assertFalse(r.widgets()['mn_off']['enabled'])                          # becomes inactive (p.41)
        r.h.step(300)
        self.assertLess(r.val('thermal.indicated_temperature_k') - 273.15, 120 - 50)
        r.tap('mn_view')
        self.assertEqual(r.text('fv_hole0'), 'free')

    def test_setup_settings_users_substances_maintenance(self):
        r = Rig()
        r.home()
        r.tap('h_setup')
        r.tap('su_global')
        r.tap('gl_dt')
        r.set_row('set_datetime_rows', 'Date format', 'YYYY-MM-DD')
        r.tap('set_datetime_save')
        self.assertEqual(r.val('device.settings')['region.date_format'], 'YYYY-MM-DD')
        r.tap('gl_users')
        r.tap('ul_new')
        r.row('ue_rows', 'Name')
        r.type('Ana')
        r.ov('OK')
        r.tap('ue_save')
        self.assertEqual([u['user_name'] for u in r.val('users.state')['users']], ['Administrator', 'Ana'])
        r.tap('ul_back')
        r.tap('gl_back')
        r.tap('su_cal')
        r.tap('cs_new')
        r.ov_row('Name')
        r.type('Vanillin')
        r.ov('OK')
        r.ov_row('Nominal melting point')
        r.type('81.7')
        r.ov('OK')
        r.ov('Save')
        self.assertIn({'name': 'Vanillin', 'substance_id': '', 'nominal_c': 81.7, 'tol_c': 0.5}, r.val('calibration.substances'))
        r.tap('cs_back')
        r.tap('su_maint')
        r.tap('mt_export')
        self.assertEqual(len(r.val('sd_card.state')['files']), 1)
        r.ov('OK')
        self.assertFalse(r.tap('mt_service'))
        self.assertTrue(r.frame()['ui']['unsupported'])
        r.ov('OK')
        r.tap('mt_reset')
        r.ov('Yes')
        self.assertEqual(len(r.val('users.state')['users']), 1)

    def test_no_sd_card(self):
        r = Rig()
        make(r)
        r.host('sd_card.set', inserted=False)
        r.home()
        r.tap('h_methods')
        r.row('m_list', 'M0001')
        r.tap('me_start')
        self.assertFalse(r.tap('sw_start'))
        self.assertEqual(r.msg(), 'Insert an SD card.')
        self.assertIsNone(r.val('analysis.state'))


class PanelEqualsDirect(unittest.TestCase):
    def test_analysis_equals_commands(self):
        a, b = Rig(), Rig()
        for r in (a, b):
            make(r)
        a.run_method()
        b.call(b.A, 'analysis.prepare', method_id='M0001')
        b.h.step(150)
        for ch in (0, 1):
            b.host('capillary.insert', channel=ch, **VAN)
        b.call(b.A, 'analysis.start')
        for r in (a, b):
            r.finish()
        sa, sb = a.h.read('evaluator'), b.h.read('evaluator')
        for s in (sa, sb):
            s['analysis.state']['comments'] = None
            s['results.analyses'][0]['comments'] = None
        self.assertEqual(sa, sb)

    def test_keys_equal_commands(self):
        a, b = Rig(on=False), Rig(on=False)
        a.press('key_power')
        b.call(b.A, 'power.set', enabled=True)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))


class SyncAuditFixes(unittest.TestCase):
    """1.2.0: screen saver delay, keyboard layouts, language stored only, Furnace inside view images, adjustment-expiry warning (rules 3 / 4 and
    the suspect) and caller rules (rule 2)."""

    def test_screen_saver_after_the_user_delay(self):
        r = Rig()
        r.call(r.A, 'preferences.configure', preferences={'screensaver_min': 1})
        r.h.step(59)
        self.assertFalse(r.val('display.screensaver'))
        r.h.step(2)
        self.assertTrue(r.val('display.screensaver'))
        self.assertEqual(r.frame()['screen'], 'saver')
        self.assertTrue(r.frame()['displays']['power_led'].get('blink'))
        r.press('key_power')                                                        # the power button ends it
        self.assertEqual((r.val('display.screensaver'), r.frame()['screen']), (False, 'main'))
        r.h.step(50)
        r.press('key_home_right')                                                   # a key press restarts the delay
        r.h.step(50)
        self.assertFalse(r.val('display.screensaver'))
        make(r)
        r.call(r.A, 'analysis.prepare', method_id='M0001')
        r.h.step(300)                                                              # not while an analysis runs
        self.assertFalse(r.val('display.screensaver'))

    def test_keyboard_setting_selects_the_layout(self):
        r = Rig()
        for kb, first in (('English', 'qwertyuiop'), ('Deutsch', 'qwertzuiop'), ('Français', 'azertyuiop')):
            r.call(r.A, 'preferences.configure', preferences={'keyboard': kb})
            r.home()
            r.tap('h_methods')
            r.tap('m_new')
            r.row('mf_rows', 'Title')
            labels = [b['label'] for b in r.overlay()['buttons'] if b['id'] != 'field']
            self.assertEqual(''.join(labels[:10]), first, kb)
            r.ov('Cancel')
            r.tap('mf_cancel')

    def test_language_is_stored_and_shown_but_texts_stay_english(self):
        r = Rig()
        r.home()
        r.tap('h_setup')
        r.tap('su_user')
        r.set_row('us_rows', 'Language', 'Deutsch')
        self.assertEqual(r.val('user.preferences')['language'], 'Deutsch')
        self.assertIn(['Language', 'Deutsch'], r.widgets()['us_rows']['cells'])
        r.home()
        self.assertEqual(r.text('h_methods'), 'Methods')

    def test_furnace_inside_view_images(self):
        r = Rig()
        r.host('sample.configure', channel=0, substance_id='benzoic_acid', mass_kg=2e-6, purity=1.0, particle_size_m=50e-6,
               decomposition_onset_k=None, decomposition_source='')
        r.home()
        r.tap('h_manual')
        r.tap('mn_view')
        refl, trans = r.widgets()['fv_reflected'], r.widgets()['fv_transmitted']
        self.assertEqual((refl['pw'], refl['ph'], trans['pw'], trans['ph']), (64, 40, 64, 8))
        self.assertEqual(refl['pixels'][32][9], 230)                               # capillary 1: white powder
        self.assertEqual(refl['pixels'][32][23], 12)                               # slot 2 empty
        self.assertLess(trans['pixels'][3][9], 20)                                 # powder blocks the light of hole 1
        self.assertGreater(trans['pixels'][3][23], 230)                            # hole 2 open
        r.call(r.A, 'thermal.set_temperature', temperature_k=140 + 273.15)
        r.h.step(600)
        refl, trans = r.widgets()['fv_reflected'], r.widgets()['fv_transmitted']
        self.assertLess(refl['pixels'][32][9], 50)                                 # molten and clear
        self.assertGreater(trans['pixels'][3][9], 230)
        self.assertNotIn('Furnace inside view video', [u['item'] for u in rt.PANEL_SPEC['unsupported']])

    def test_adjustment_expiry_warning_and_block(self):
        r = Rig()
        make(r)
        r.h.dev['adjustment']['time'] = '2020-01-01T00:00:00'                       # an adjustment older than its validity (365 d)
        r.call(r.A, 'analysis.prepare', method_id='M0001')
        self.assertTrue(r.val('analysis.state')['adjustment_expired'])
        r.home()
        r.tap('tb_task')
        self.assertIn('a_warn', r.widgets())
        r.call(r.A, 'analysis.stop')
        r.call(r.A, 'analysis.save', save=False)
        r.call(r.A, 'analysis.close')
        r.call(r.A, 'settings.configure', settings={'adj.expiry_action': 'Block'})
        self.assertEqual(r.call(r.A, 'analysis.prepare', expect='rejected', method_id='M0001')['error']['code'], 'ADJUSTMENT_EXPIRED')
        r.call(r.A, 'settings.configure', settings={'adj.expiry_action': 'None'})
        r.call(r.A, 'analysis.prepare', method_id='M0001')
        self.assertFalse(r.val('analysis.state')['adjustment_expired'])

    def test_terminal_analysis_belongs_to_the_caller(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        make(r)
        r.run_method('M0001', channels=(0,))
        op = r.val('analysis.state')['operation_id']
        self.assertEqual((r.h._owners.get(op), r.A.poll(op)['status']), ('agent', 'running'))
        before = r.h.read('evaluator')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        r.tap('a_stop')
        x, y, w, h = next(b for b in r.overlay()['buttons'] if b['label'] == 'Yes')['rect']
        r.call(B, 'panel.touch', display_id=TERM, x=x + w // 2, y=y + h // 2)
        self.assertEqual(r.msg(), 'This analysis was started by another user: it cannot be stopped or changed here.')
        r.ov('OK')
        for cap, args in (('analysis.stop', {}), ('acquisition.stop', {}), ('power.shutdown', {'forced': True}), ('analysis.set_point', {'channel': 0})):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h.read('evaluator'), before)
        self.assertEqual(r.A.cancel(op, 'c1')['status'], 'cancelled')

    def test_mains_supply_is_a_hard_power_cut(self):
        """Sweep 2026-10-19: power.set models the mains supply at the power inlet (a plug / hard power cut): another agent's switch-off is not
        refused and cancels the analysis as a power loss; the soft power key (shut down) stays owner-guarded."""
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        B = r.h.session('agent_b')
        make(r)
        r.run_method('M0001', channels=(0,))
        op = r.val('analysis.state')['operation_id']
        self.assertEqual(r.call(B, 'power.shutdown', expect='rejected', forced=False)['error']['code'], 'ACCESS_DENIED')
        r.call(B, 'power.set', enabled=False)
        self.assertFalse(r.val('power.enabled'))
        self.assertNotEqual(r.val('cycle.phase'), 'running')


class Rendering(unittest.TestCase):
    def test_render_determinism_and_evidence(self):
        a, b = Rig(), Rig()
        for r in (a, b):
            make(r)
            r.run_method()
            r.finish()
        self.assertEqual(a.call(a.A, 'panel.capture', display_id=TERM)['image']['sha256'], b.call(b.A, 'panel.capture', display_id=TERM)['image']['sha256'])
        cap = a.call(a.A, 'panel.capture', display_id='power_led')
        self.assertEqual(validate_value(cap, rt.CAPS['panel.capture'][1]), [])
        a.h.panel.save_png(TERM, OUT / 'analysis.png')
        a.tap('a_results')
        a.h.panel.save_png(TERM, OUT / 'results.png')
        a.home()
        a.h.panel.save_png(TERM, OUT / 'home.png')
        a.tap('h_setup')
        a.h.panel.save_png(TERM, OUT / 'menu.png')
        a.tap('su_back')
        a.tap('h_lock')
        a.h.panel.save_png(TERM, OUT / 'error.png')
        a.ov('OK')
        a.tap('h_manual')
        a.tap('mn_view')
        a.h.panel.save_png(TERM, OUT / 'furnace.png')                               # 1.2.0: reflected / transmitted light images
        (OUT / 'renders.json').write_text(json.dumps({k: str(OUT.relative_to(ROOT) / f'{k}.png') for k in ('home', 'menu', 'analysis', 'results', 'error',
                                                                                                           'furnace')},
                                                     indent=1) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_has_panel_layer(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + list(rt.TERMINAL):
            self.assertEqual(c['capabilities'][k]['input_schema'], rt.CAPS[k][0])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])

    def test_slot_values_match_schemas(self):
        r = Rig()
        make(r)
        r.run_method()
        r.h.step(200)
        for k, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, rt.SLOTS[k][0]), [], k)


if __name__ == '__main__':
    unittest.main()
