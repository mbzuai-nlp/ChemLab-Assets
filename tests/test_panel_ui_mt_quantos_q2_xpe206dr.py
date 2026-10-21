"""terminal tests for CHEM-174 mt_quantos_q2 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_automation.py):
python3 -m pytest tests/test_panel_ui_mt_quantos_q2_xpe206dr.py -q

Every gesture goes through the session API (panel.press on the hardware keys and SmartSens, panel.touch on the screen, panel.switch on the head
height handle).  Checks look at the runtime state and at the screen: the structured frame (widgets, pop-ups, keyboards with their rects) and the
rendered pixels (determinism, the PNGs of outputs/panel_ui/mt_quantos_q2_xpe206dr/).  Themes: the seven key procedures of the menu survey
(switch on / off, install a head, dose powder, remove the head, change the user profile, change the tolerance mode, enter a protected menu), the
other terminal functions (function keys and their order, info fields, unit and resolution, sample counter, Write / Info / Copy head, Set content,
tapper, internal adjustment and history, doors, SmartSens, tare / zero / print and automatic records, date and time, standby, user and master
reset, access rights, language, brightness, touch function), panel = direct capability, refusals and messages, render determinism, contract."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_automation as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'qt_panel_01'
OUT = ROOT / 'outputs/panel_ui/mt_quantos_q2_xpe206dr'
ASSET = rt.QUANTOS_Q2_ASSET_DIR
SPEC = rt._qtp_panel()['spec']
SURVEY_TASKS = ['Switch on / off', 'Install a dosing head', 'Dose powder', 'Remove the dosing head', 'Change user profile', 'Change the tolerance mode',
                'Enter a protected menu']
DONE_TASKS = set()
TRAVEL = 0.11


class Rig:
    def __init__(self, on=True, head=True, container=True, rate=1e-5, source=0.002, scene=True):
        self.h = rt.QuantosQ2Runtime()
        self.scene = []
        self.h.create_instance(INST, scene_adapter=(lambda i, p: self.scene.append(p) or True) if scene else None)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if scene:
            self.h.host_configure_geometry(INST, door_travel_m=TRAVEL, evidence='synthetic door travel')
        if on:
            self.key('on_off')
        if head or container:
            self.key('front_door')
            if head:
                self.h.host_load_head(INST, head_id='head_1', source_mass_kg=source, free_flowing=True, evidence='synthetic free-flowing powder head')
            if container:
                self.h.host_load_container(INST, container_id='vial_1', position_id='balance_pan', content_mass_kg=0.0, evidence='synthetic vial on the pan')
            self.key('front_door')
        self.h.host_configure_dosing(INST, rate_kg_s=rate, rate_evidence='synthetic constant rate', observation_evidence='synthetic ideal mass observation')

    # ---- session calls
    def call(self, cap, sess=None, expect=('succeeded', 'accepted'), **args):
        self.n += 1
        t0 = self.h.time_s
        r = (sess or self.A).invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def key(self, k, d=0.1):
        return self.call('panel.press', input_id=k, duration_s=d)['accepted']

    def handle(self, pos):
        return self.call('panel.switch', input_id='head_handle', position=pos)['accepted']

    def touch(self, x, y, d=0.1, to=None):
        extra = dict(to_x=to[0], to_y=to[1]) if to else {}
        return self.call('panel.touch', display_id='terminal', x=x, y=y, duration_s=d, **extra)['accepted']

    def frame(self):
        return self.call('panel.frame', sess=self.E)['frame']

    def disp(self):
        return self.frame()['displays']['terminal']

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid].get('text')

    def tap(self, wid, d=0.1):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2, d)

    def fk(self, name):
        """Tap the function key with this name, paging with the arrows."""
        for _ in range(6):
            for i in range(5):
                w = self.widgets().get(f'fk{i}')
                if w is not None and w.get('text') == name:
                    return self.tap(f'fk{i}')
            if 'fk_next' not in self.widgets():
                break
            self.tap('fk_next')
        raise AssertionError(name)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label, d=0.1):
        ov = self.overlay()
        assert ov is not None, 'no pop-up'
        for b in ov['buttons']:
            if b['label'] == label or b['label'].strip().split(' ', 1)[-1] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2, d)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def type(self, text, ok=True):
        for ch in text:
            ov = self.overlay()
            labels = [b['label'] for b in ov['buttons']]
            if ch not in labels:
                mode = ov.get('mode')
                if ch.isalpha() and mode == 'sym':
                    self.ov('abc')
                elif not ch.isalpha() and mode in ('lower', 'upper'):
                    self.ov('12@')
                ov = self.overlay()
                if ch.isalpha() and (ch.isupper() != (ov.get('mode') == 'upper')):
                    self.ov('Shift')
            self.ov(ch)
        if ok:
            self.ov('OK')

    def row(self, wid, label):
        """Tap the form / list row whose first cell is this text (dragging until it shows)."""
        for _ in range(12):
            w = self.widgets()[wid]
            for i, cells in enumerate(w['cells']):
                if cells[0] == label:
                    x, y, ww, _ = w['rect']
                    return self.touch(x + ww // 2, y + i * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            self.touch(x + ww // 2, y + hh - 5, to=(x + ww // 2, y + 5))
        raise AssertionError(f'{label!r} not in {[c[0] for c in w["cells"]]}')

    def choose(self, option):
        """An option list: tap the option (then OK when the list has one)."""
        self.ov(option)
        if self.overlay() is not None and 'OK' in [b['label'] for b in self.overlay()['buttons']] and self.overlay().get('kind') == 'select':
            self.ov('OK')

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def step(self, s):
        while s > 1e-12:
            dt = min(s, 600.0)
            self.h.host_step(dt, world_time_s=self.h.time_s + dt)
            s -= dt

    def state(self, *ids):
        o = self.E.read(instance_id=INST, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def prof(self, key):
        return self.state('settings.profile')[key]

    def setting(self, key):
        return self.state('settings.system')[key]

    def menu(self, keyname, *rows):
        """Open a menu by its hardware key and follow form rows ('Application Setup > Function Keys' ...)."""
        self.key(keyname)
        for r in rows:
            self.row(self.form_widget(), r)

    def form_widget(self):
        return next(k for k, w in self.widgets().items() if w['type'] == 'form')

    def ready(self):
        """Direct commands: the host's vessel position and head selected (what the dosing steps do on the panel)."""
        self.call('container.select_position', position_id='balance_pan')
        self.call('head.select', head_id='head_1')

    def dose(self, target='50', tol='1', user='TRPF', sample='XK-414'):
        """[Start] and the factory Dosing steps up to the running dosing."""
        self.fk('Start')
        self.type(user)
        self.type(sample)
        self.type(target)
        self.type(tol)
        self.ov('OK')                                                                   # place container
        self.ov('OK')                                                                   # lowering head: tare + dosing


def obs_state(h):
    s = h.debug_snapshot()['instances'][INST]
    keep = {k: v for k, v in s['state'].items() if not k.startswith('clock.')}
    return json.dumps(keep, sort_keys=True, default=str)


class KeyProcedures(unittest.TestCase):
    def test_switch_on_and_off(self):
        r = Rig(on=False, head=False, container=False)
        self.assertEqual(r.page()[0], 'off')
        self.assertFalse(r.key('home'))                                                 # off: only On/Off acts
        self.assertTrue(r.key('on_off'))
        self.assertEqual((r.state('power.enabled'), r.page()), (True, ('ui', 'main')))
        self.assertFalse(r.key('on_off', 0.3))                                          # a short press while on: nothing
        self.assertTrue(r.state('power.enabled'))
        self.assertTrue(r.key('on_off', 2.0))                                           # held until "Off"
        self.assertEqual((r.state('power.enabled'), r.page()[0]), (False, 'off'))
        DONE_TASKS.add('Switch on / off')

    def test_install_a_dosing_head(self):
        r = Rig(head=False)
        self.assertTrue(r.key('front_door'))
        self.assertEqual((r.state('scene.door_opening_m'), r.scene[-1]), (TRAVEL, {'scene.door_opening_m': TRAVEL}))
        self.assertTrue(r.key('side_door_left'))
        self.assertEqual(r.state('door.side_opening_fraction'), 1.0)
        r.h.host_load_head(INST, head_id='head_9', source_mass_kg=0.001, free_flowing=True, evidence='synthetic head slid onto the support')
        self.assertEqual((r.state('head.locked'), r.state('head.data')['dose_limit'], r.state('head.data')['remaining_dosages']), (False, 250, 250))
        self.assertIn('[Head]', r.text('status'))
        r.key('side_door_right')
        r.key('front_door')
        self.assertEqual((r.state('door.side_opening_fraction'), r.state('scene.door_opening_m')), (0.0, 0.0))
        DONE_TASKS.add('Install a dosing head')

    def test_dose_powder(self):
        r = Rig()
        r.fk('Start')
        self.assertTrue(r.state('head.locked'))                                         # [Start] locks the head
        self.assertEqual(r.overlay()['title'], 'User ID')
        r.type('TRPF')
        r.type('XK-414')
        self.assertEqual(r.overlay()['title'], 'Target quantity [mg]')
        r.type('50')
        r.type('1')
        self.assertEqual(r.state('dosing.target_mass_kg', 'dosing.tolerance_percent', 'dosing.user_id', 'dosing.sample_id'),
                         {'dosing.target_mass_kg': 5e-05, 'dosing.tolerance_percent': 1.0, 'dosing.user_id': 'TRPF', 'dosing.sample_id': 'XK-414'})
        self.assertAlmostEqual(r.state('dosing.tolerance_kg'), 5e-07)
        self.assertEqual(r.overlay()['title'], 'Dosage preparation')
        r.ov('OK')
        r.handle('lowered')
        r.ov('OK')
        self.assertEqual((r.page()[0], r.state('dosing.status'), r.state('balance.tared')), ('dosing', 'running', True))
        r.step(2.0)
        self.assertEqual(r.text('d_value'), '20.000 mg')
        r.step(4.0)
        self.assertEqual(r.page()[0], 'result')
        self.assertEqual((r.text('r_value'), r.text('r_verdict')), ('50.000 mg', 'Within tolerance'))
        res = r.state('dosing.last_result')
        self.assertEqual((res['status'], res['valid'], res['sample_id']), ('succeeded', True, 'XK-414'))
        self.assertFalse(r.state('head.locked'))                                        # 'Unlock head' step: released after the dosing
        self.assertEqual(r.state('head.data')['remaining_dosages'], 249)
        rec = r.state('output.records')[-1]
        self.assertEqual((rec['device'], rec['kind']), ('printer', 'sample_protocol'))   # automatic sample protocol (factory)
        self.assertIn('Sample ID      XK-414', rec['lines'])
        r.tap('r_ok')
        self.assertEqual(r.page(), ('ui', 'main'))
        self.assertIn('in tolerance', r.text('result_line'))
        DONE_TASKS.add('Dose powder')

    def test_remove_the_dosing_head(self):
        r = Rig()
        r.fk('Un/Lock')
        self.assertTrue(r.state('head.locked'))
        with self.assertRaises(ValueError):
            r.key('front_door')
            r.h.host_unload_head(INST)                                                   # locked: the head cannot be taken off
        r.fk('Un/Lock')
        self.assertFalse(r.state('head.locked'))
        self.assertEqual(r.h.host_unload_head(INST)['head_id'], 'head_1')
        self.assertIsNone(r.state('head.data'))
        self.assertIn('???', r.text('info3'))                                            # no head: question marks
        DONE_TASKS.add('Remove the dosing head')

    def test_change_user_profile(self):
        r = Rig()
        r.key('user_profile')
        self.assertEqual(r.page(), ('ui', 'profiles'))
        r.tap('p_4')
        self.assertEqual((r.state('profile.active'), r.page()[1], r.text('appname')), ('User 4', 'main', 'Quantos  User 4'))
        r.call('settings.set_profile', key='display_unit', value='g')
        r.key('home')                                                                    # Home: back to the profile Home
        self.assertEqual((r.state('profile.active'), r.prof('display_unit')), ('Home', 'mg'))
        DONE_TASKS.add('Change user profile')

    def test_change_the_tolerance_mode(self):
        r = Rig()
        r.menu('configuration', 'Tolerance Mode')
        r.choose('0/+Tolerance')
        self.assertEqual(r.prof('tolerance_mode'), '0/+Tolerance')
        r.call('dosing.set_target', target_mass_kg=5e-05)
        r.call('dosing.set_tolerance_percent', percent=1.0)
        r.ready()
        r.call('balance.tare')
        r.call('dosing.start')
        r.step(10)
        self.assertFalse(r.state('measurement.out_of_tolerance'))                         # exactly on target: inside 0 / +1 %
        r.call('settings.set_profile', key='tolerance_mode', value='+/- Tolerance')
        DONE_TASKS.add('Change the tolerance mode')

    def test_enter_a_protected_menu(self):
        r = Rig()
        r.menu('settings')
        r.tap('us_system')
        r.row('system_rows', 'Administrator')
        self.assertEqual(r.overlay()['title'], 'Protected Area: Enter Administrator ID.')
        r.type('Z')
        self.assertEqual(r.overlay()['title'], 'Protected Area: Enter Admin. Password.')
        r.type('Y')                                                                      # wrong
        self.assertEqual(r.overlay()['title'], 'Error')
        r.ov('OK')
        r.type('Z')
        r.type('Z')
        self.assertEqual(r.page()[1], 'sy_admin')
        r.row('sy_admin_rows', 'Home Rights')
        r.row('sy_rights_rows', 'Application Setup')
        r.choose('User Protect.')
        self.assertEqual(r.setting('rights')['Home']['Application Setup'], 'User Protect.')
        r.key('home')
        r.key('configuration')                                                           # now protected at user level
        self.assertEqual(r.overlay()['title'], 'Protected Area: Enter ID.')
        r.type('0')
        r.type('0')
        self.assertEqual(r.page()[1], 'app_setup')
        r.key('home')
        r.key('configuration')
        r.type('Z')
        r.type('Z')                                                                      # the administrator codes open user areas too
        self.assertEqual(r.page()[1], 'app_setup')
        DONE_TASKS.add('Enter a protected menu')


class TerminalFunctions(unittest.TestCase):
    def test_function_keys_order_and_paging(self):
        r = Rig()
        self.assertEqual([r.text(f'fk{i}') for i in range(5)], ['Start', 'Un/Lock', 'Info head', 'Write head', 'Quantity'])
        r.tap('fk_next')
        self.assertEqual(r.text('fk0'), 'Tolerance')
        r.tap('fk_prev')
        r.menu('configuration', 'Function Keys', 'Function Keys')
        labels = [b['label'] for b in r.overlay()['buttons']]
        self.assertIn(' 1 Start', labels)
        self.assertIn(' - Tapper', labels)
        r.ov('STD')
        r.ov('Samples'), r.ov('Quantity'), r.ov('Tapper')
        r.ov('OK')
        self.assertEqual(r.prof('function_keys'), ['Start', 'Un/Lock', 'Info head', 'Write head', 'Tolerance', 'Tapper'])
        r.key('home')
        r.fk('Tapper')
        self.assertEqual(r.state('tapper.taps'), 1)

    def test_info_fields_unit_and_resolution(self):
        r = Rig()
        r.call('dosing.set_target', target_mass_kg=2.5e-05)
        r.ready()
        self.assertEqual(r.text('info0'), 'Target quantity: 25.000 mg')
        r.tap('info0')
        r.ov('Sample ID'), r.ov('Substance'), r.ov('Rem. dosages'), r.ov('Samples')
        r.ov('OK')
        self.assertEqual(r.prof('info_fields'), ['Target quantity', 'Tolerance', 'Rem. dosages', 'Samples'])
        self.assertEqual(r.text('info2'), 'Rem. dosages: 250')
        r.call('balance.tare')
        r.tap('unit')
        self.assertEqual((r.prof('display_unit'), r.text('weight'), r.text('unit')), ('g', '0.000000', 'g'))
        r.tap('unit')
        r.menu('configuration', 'Function Keys', 'Function Keys')
        r.ov('1/100d'), r.ov('OK')
        r.key('home')
        r.fk('1/100d')
        self.assertEqual((r.state('display.resolution'), r.text('weight')), ('1/100d', '0.0'))
        r.fk('1/100d')
        self.assertEqual(r.state('display.resolution'), '1d')

    def test_samples_counter_and_last_sample(self):
        r = Rig()
        r.call('settings.set_profile', key='function_keys', value=['Start', 'Samples'])
        r.fk('Samples')
        r.type('2')
        self.assertEqual(r.state('dosing.samples_total', 'dosing.samples_remaining'), {'dosing.samples_total': 2, 'dosing.samples_remaining': 2})
        for n in range(2):
            r.dose(target='10', sample=f'S{n}')
            r.step(2)
            r.tap('r_ok')
        self.assertEqual(r.overlay()['lines'], ['Last sample reached'])
        self.assertEqual(r.state('dosing.samples_total', 'dosing.samples_remaining'), {'dosing.samples_total': 0, 'dosing.samples_remaining': 0})

    def test_write_info_and_copy_head(self):
        r = Rig()
        r.call('dosing.set_target', target_mass_kg=1e-05)
        r.fk('Write head')
        r.type('Glucose')
        r.type('L-77')
        self.assertEqual(r.overlay()['kind'], 'spinner')                                 # Filling date: date picker
        r.ov('OK')
        r.ov('OK')                                                                       # Exp. date
        r.type('19.813')                                                                 # Content [mg]
        r.type('Storage')                                                                # Var 1 name
        r.type('5oC')
        r.type('100')                                                                    # Dose limit
        r.ov('OK')                                                                       # Retest date
        self.assertEqual(r.overlay()['lines'], ['Data transfer successful.'])
        r.ov('OK')
        head = r.state('head.data')
        self.assertEqual((head['substance'], head['lot_id'], head['dose_limit'], head['remaining_dosages'], head['var'][0]),
                         ('Glucose', 'L-77', 100, 100, {'name': 'Storage', 'value': '5oC'}))
        self.assertAlmostEqual(head['content_kg'], 1.9813e-05)
        self.assertTrue(r.state('head.locked'))                                          # writing locks the head
        r.fk('Info head')
        texts = [w['text'] for k, w in r.widgets().items() if k.startswith('ih')]
        self.assertIn('Substance: Glucose', texts)
        self.assertIn('Content [mg,g]: 19.813 mg', texts)
        r.tap('ih_ok')
        r.call('settings.set_profile', key='function_keys', value=['Start', 'Copy head', 'Un/Lock'])
        r.fk('Copy head')
        r.ov('OK')                                                                       # source head read
        self.assertTrue(r.state('head.copy_memory'))
        r.ov('OK')                                                                       # still the same head: the window stays
        self.assertEqual(r.overlay()['title'], 'Copy head')
        r.call('head.set_lock', locked=False)
        r.key('front_door')
        r.h.host_unload_head(INST)
        r.h.host_load_head(INST, head_id='head_2', source_mass_kg=0.001, free_flowing=True, evidence='synthetic new head')
        r.ov('OK')
        self.assertEqual(r.overlay()['lines'], ['The data has been copied.'])
        new = r.state('head.data')
        self.assertEqual((new['head_id'], new['substance'], new['remaining_dosages']), ('head_2', 'Glucose', 100))

    def test_head_limits_expiry_retest_and_quantity(self):
        r = Rig()
        r.call('clock.set', date='2026-10-19', time='08:00:00')
        r.call('head.write_data', data={'exp_date': '2026-10-01'})
        r.call('head.set_lock', locked=False)
        r.fk('Start')
        self.assertEqual(r.overlay()['lines'], ['Expiry date reached'])
        r.ov('Cancel')
        r.call('head.write_data', data={'exp_date': '2027-01-01', 'retest_date': '2026-10-10', 'content_kg': 2e-05})
        r.fk('Start')
        self.assertEqual(r.overlay()['lines'], ['Retest date reached'])
        r.ov('Continue')
        r.type('TRPF'), r.type('S1'), r.type('50'), r.type('1')
        r.ov('OK')
        r.ov('OK')
        self.assertEqual(r.overlay()['lines'], ['Remaining quantity too low'])            # 20 mg left for a 50 mg target
        r.ov('Cancel')
        self.assertEqual(r.state('dosing.status'), 'idle')
        r.call('head.write_data', data={'dose_limit': 1})
        r.call('head.set_lock', locked=False)
        r.ready()
        r.call('balance.tare')
        r.call('dosing.set_target', target_mass_kg=1e-05)
        r.call('dosing.start')
        r.step(5)
        r.fk('Start')
        self.assertEqual(r.overlay()['lines'], ['Head dosage limit reached'])
        self.assertEqual(r.call('dosing.start', expect=('rejected',))['error']['code'], 'PRECONDITION_FAILED')

    def test_set_content_tapper_adjustment_and_history(self):
        r = Rig()
        r.call('settings.set_profile', key='function_keys', value=['Set content', 'Tapper', 'Adjust.int', 'Test int', 'Adj. History', 'Adjust.ext'])
        r.fk('Set content')
        self.assertEqual(r.overlay()['title'], 'Message')                                  # nothing tared yet
        r.ov('OK')
        r.key('tare')
        r.fk('Set content')
        self.assertEqual(r.state('head.content_buffer_kg'), 0.0)
        r.fk('Tapper')
        self.assertEqual(r.state('tapper.taps'), 1)
        r.key('side_door_left')
        r.fk('Adjust.int')                                                                 # factory: doors linked with dosing only: refused
        self.assertEqual(r.overlay()['lines'], ['Adjustment abort'])
        r.ov('OK')
        r.call('settings.set_profile', key='side_doors_linked', value=['Dosing', 'Int. Adj, int. Tst'])
        r.fk('Adjust.int')                                                                 # side doors linked with Int. Adj close
        self.assertEqual((r.overlay()['lines'], r.state('door.side_opening_fraction')), (['Adjustments done'], 0.0))
        r.ov('OK')
        r.fk('Test int')
        r.ov('OK')
        self.assertEqual([e['type'] for e in r.state('balance.adjustment_history')], ['Adjust int', 'Test int'])
        r.fk('Adj. History')
        self.assertEqual([c[0] for c in r.widgets()['h_list']['cells']], ['Adjust int', 'Test int'])
        r.key('print')
        self.assertEqual(r.state('output.records')[-1]['kind'], 'adjustments')
        r.tap('h_ok')
        self.assertFalse(r.fk('Adjust.ext'))
        self.assertIn('external', r.frame()['ui']['unsupported'])

    def test_doors_smartsens_doorway(self):
        r = Rig()
        r.menu('settings', 'Door', 'Doorway')
        r.choose('50%')
        r.key('home')
        r.key('side_door_right')
        self.assertEqual(r.state('door.side_opening_fraction'), 0.5)
        self.assertFalse(r.key('smartsens_left'))                                         # factory: Off
        r.call('settings.set_profile', key='smartsens_left', value='Door')
        r.call('settings.set_profile', key='smartsens_right', value='Start')
        self.assertIn('L:Door', r.text('status'))
        self.assertTrue(r.key('smartsens_left'))
        self.assertEqual(r.state('door.side_opening_fraction'), 0.0)
        r.key('smartsens_right')                                                          # Start: the dosing steps
        self.assertEqual(r.overlay()['title'], 'User ID')
        self.assertGreaterEqual(r.frame()['ui']['beeps'], 2)

    def test_tare_zero_print_and_date_time(self):
        r = Rig()
        self.assertEqual(r.text('weight'), '------')
        r.key('zero')
        self.assertEqual((r.state('balance.tared'), r.state('balance.net_mode'), r.text('weight')), (True, False, '0.000'))
        r.key('tare')
        self.assertIn('Net', r.text('status'))
        r.key('front_door')
        self.assertFalse(r.key('tare'))                                                   # door open: refused with the message
        self.assertEqual(r.overlay()['title'], 'Message')
        r.ov('OK')
        r.key('print')
        self.assertEqual(r.state('output.records')[-1]['kind'], 'weight')
        r.tap('date')
        self.assertEqual(r.overlay()['kind'], 'spinner')
        r.ov('OK')
        self.assertEqual(r.state('clock.datetime')[:10], '2026-01-01')
        self.assertEqual(r.text('date'), '1. Jan 2026')
        r.call('settings.set_system', key='date_format', value='DD.MM.YYYY')
        r.call('settings.set_system', key='time_format', value='12:MM')
        r.step(13 * 3600 + 5 * 60)
        self.assertEqual((r.text('date'), r.text('time')), ('01.01.2026', '1:05 PM'))

    def test_standby_brightness_and_touch_function(self):
        r = Rig()
        self.assertEqual(r.disp()['brightness'], 80)
        r.step(901)
        self.assertEqual(r.disp()['brightness'], 20)                                      # 15 min without use: dimmed
        r.key('tare')
        self.assertEqual(r.disp()['brightness'], 80)
        r.call('settings.set_system', key='standby_min', value=30)
        r.step(30 * 60 + 1)
        self.assertEqual((r.state('power.enabled'), r.page()[0]), (False, 'off'))
        r.key('on_off')
        r.call('settings.set_profile', key='touch_function', value=False)
        self.assertFalse(r.tap('unit'))                                                   # touch off: the zones ignore the finger
        self.assertTrue(r.fk('Quantity'))                                                 # function keys still work

    def test_user_reset_master_reset_language_and_names(self):
        r = Rig()
        r.menu('settings', 'User', 'User Name')
        r.type('Dosing')
        self.assertEqual(r.state('profile.names')[0], 'Dosing')
        r.row('us_user_rows', 'Language')
        r.choose('Deutsch')
        r.key('home')
        self.assertEqual(r.prof('language'), 'Deutsch')
        r.key('configuration')
        self.assertIn('Applikation', json.dumps(r.disp(), ensure_ascii=False))                  # the screen texts follow the language
        r.key('home')
        r.call('settings.set_profile', key='user_name', value='User 1', expect=('rejected',))   # name already used by profile User 1
        r.menu('settings', 'User Reset')
        r.ov('OK')
        self.assertEqual((r.state('profile.names')[0], r.prof('language')), ('Home', 'English'))
        r.call('settings.set_system', key='standby_min', value=60)
        r.call('balance.adjust_internal')
        r.call('settings.master_reset')
        self.assertEqual((r.setting('standby_min'), len(r.state('balance.adjustment_history'))), ('Off', 1))   # adjustments kept


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        a, b = Rig(), Rig()
        a.dose(target='30', tol='2', user='U1', sample='S1')
        b.call('head.set_lock', locked=True)
        b.call('head.select', head_id='head_1')
        b.call('dosing.set_ids', user_id='U1', sample_id=None)
        b.call('dosing.set_ids', user_id='U1', sample_id='S1')
        b.call('dosing.set_target', target_mass_kg=3e-05)
        b.call('dosing.set_tolerance_percent', percent=2.0)
        b.call('container.select_position', position_id='balance_pan')
        b.call('door.set_side', open=False)
        b.call('balance.tare')
        b.call('dosing.start')
        self.assertEqual(obs_state(a.h), obs_state(b.h))
        a.step(4), b.step(4)
        self.assertEqual(obs_state(a.h), obs_state(b.h))


class Rules(unittest.TestCase):
    def test_refusals_busy_and_access(self):
        r = Rig()
        r.dose()
        self.assertFalse(r.key('configuration'))                                          # dosing runs: keys ignored
        self.assertTrue(r.tap('d_cancel'))                                                # [C] aborts
        self.assertEqual((r.page()[0], r.text('r_verdict')), ('result', 'Dosing aborted'))
        self.assertFalse(r.state('head.locked'))
        caps = r.A.describe(instance_id=INST)['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.press', 'panel.touch', 'panel.switch', 'panel.capture'})
        self.assertEqual(r.call('panel.frame', expect=('rejected',))['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('security.credentials', r.A.read(instance_id=INST)['observations'])
        self.assertEqual(r.call('security.verify', expect=('rejected',), level='admin', id='Z', password='x')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call('panel.press', expect=('rejected',), input_id='nope')['error']['code'], 'INVALID_ARGUMENT')

    def test_powder_runs_out(self):
        r = Rig(source=2e-05)
        r.dose(target='50')
        r.step(10)
        self.assertEqual((r.page()[0], r.text('r_verdict')), ('result', 'Powder flow too low'))

    def test_unsupported_rows_and_keys(self):
        r = Rig()
        r.menu('configuration')
        self.assertFalse(r.row('app_setup_rows', 'MinWeigh'))
        self.assertIn('service engineer', r.frame()['ui']['unsupported'])
        r.key('home')
        r.menu('settings')
        r.tap('us_system')
        self.assertFalse(r.row('system_rows', 'Peripherals'))
        rr = Rig(scene=False, head=False, container=False)
        self.assertFalse(rr.key('front_door'))
        self.assertIn('not bound', rr.frame()['ui']['unsupported'])


def start_direct(r, sess=None, target=5e-05):
    """A dosing started by direct commands (host interface), as in Equivalence."""
    for cap, args in (('head.select', dict(head_id='head_1')), ('dosing.set_target', dict(target_mass_kg=target)), ('dosing.set_tolerance_percent', dict(percent=1.0)),
                      ('container.select_position', dict(position_id='balance_pan')), ('balance.tare', {}), ('dosing.start', {})):
        res = r.call(cap, sess=sess, **args)
    return [k for k, op in r.h._operations.items() if op['status'] == 'running'][0]


class Callers(unittest.TestCase):
    """Sync audit 2026-10-19: a terminal gesture acts as the session that makes it; [C] / dosing.abort only for the caller's own dosing."""
    def test_panel_started_dosing_belongs_to_the_gesturing_agent(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        r.dose()
        op = [k for k, v in r.h._operations.items() if v['status'] == 'running'][0]
        self.assertEqual(r.h._operations[op]['principal'], 'agent')
        self.assertEqual(r.A.poll(instance_id=INST, operation_id=op)['status'], 'running')
        before = obs_state(r.h)
        self.assertEqual(B.poll(instance_id=INST, operation_id=op)['error']['code'], 'UNKNOWN_OPERATION')
        self.assertEqual(B.cancel(instance_id=INST, operation_id=op, request_id='b1')['error']['code'], 'UNKNOWN_OPERATION')
        self.assertEqual(r.call('dosing.abort', sess=B, expect=('rejected',))['error']['code'], 'ACCESS_DENIED')     # lead rule 2026-10-19
        x, y, w, h = r.widgets()['d_cancel']['rect']
        self.assertFalse(r.call('panel.touch', sess=B, display_id='terminal', x=x + w // 2, y=y + h // 2)['accepted'])   # B's [C]: refused
        self.assertEqual(obs_state(r.h), before)
        self.assertEqual(r.overlay()['lines'], ["Another user's dosing is", 'in progress.'])                     # shown on the terminal
        r.ov('OK')
        self.assertEqual(r.A.cancel(instance_id=INST, operation_id=op, request_id='a1')['status'], 'cancelled')
        self.assertEqual((r.page()[0], r.text('r_verdict')), ('result', 'Dosing aborted'))

    def test_direct_dosing_shows_on_the_terminal_and_c_aborts_it(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        op = start_direct(r, sess=B)
        self.assertEqual(r.page()[0], 'dosing')                                         # the terminal follows the dosing
        self.assertFalse(r.tap('d_cancel'))                                              # A's [C] on B's dosing: refused
        self.assertEqual(r.state('dosing.status'), 'running')
        r.ov('OK')                                                                       # the refusal message
        x, y, w, h = r.widgets()['d_cancel']['rect']
        self.assertTrue(r.call('panel.touch', sess=B, display_id='terminal', x=x + w // 2, y=y + h // 2)['accepted'])
        self.assertEqual((B.poll(instance_id=INST, operation_id=op)['status'], r.page()[0]), ('cancelled', 'result'))


    def test_foreign_dosing_refusals_on_both_paths(self):
        """Lead rule 2026-10-19: during A's dosing B can neither power off nor change it, directly or on the terminal; the owner gets the device's
        own BUSY; the evaluator is unrestricted."""
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        op = start_direct(r)
        before = obs_state(r.h)
        for cap, args in (('power.set', dict(enabled=False)), ('dosing.set_target', dict(target_mass_kg=2e-5)), ('dosing.set_tolerance_percent', dict(percent=5.0)),
                          ('tapper.tap', {}), ('settings.set_profile', dict(key='algo', value='Algo P')), ('balance.tare', {})):
            self.assertEqual(r.call(cap, sess=B, expect=('rejected',), **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(obs_state(r.h), before)
        self.assertFalse(r.call('panel.press', sess=B, input_id='on_off', duration_s=2.0)['accepted'])   # B holds On/Off
        self.assertEqual((obs_state(r.h), r.overlay()['title']), (before, 'Message'))
        r.ov('OK')
        self.assertEqual(r.call('dosing.set_target', expect=('rejected',), target_mass_kg=2e-5)['error']['code'], 'BUSY')   # the owner: busy
        r.call('dosing.abort', sess=r.E)                                                 # the evaluator is unrestricted
        self.assertEqual(r.A.poll(instance_id=INST, operation_id=op)['status'], 'cancelled')


class Faults(unittest.TestCase):
    """Errors of p.89-92 injected by the evaluator (fault.inject / fault.clear)."""
    def test_actuator_blocked_stops_the_dosing_and_shows_the_message(self):
        r = Rig()
        op = start_direct(r)
        self.assertEqual(r.call('fault.inject', expect=('rejected',), code='No. 3')['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('fault.inject', r.A.describe(instance_id=INST)['capabilities'])
        self.assertEqual(r.call('fault.inject', sess=r.E, code='No. 3'), {'device.error': 'No. 3'})
        res = r.A.poll(instance_id=INST, operation_id=op)
        self.assertEqual((res['status'], res['error']['code'], res['error']['message']), ('failed', 'DEVICE_ERROR', 'Dosing head actuator blocked No. 3'))
        self.assertEqual(r.page()[0], 'fault')
        self.assertEqual(r.text('n_text'), 'Dosing head actuator blocked\nSee manual (Troubleshooting)\nNo. 3')
        self.assertEqual(r.call('fault.inject', sess=r.E, expect=('rejected',), code='No. 0')['error']['code'], 'PRECONDITION_FAILED')
        r.tap('n_ok')                                                                   # OK confirms the message; the cause stays
        self.assertEqual((r.page()[0], r.state('device.error')), ('ui', 'No. 3'))
        for cap in ('dosing.start', 'balance.tare', 'tapper.tap'):
            self.assertEqual(r.call(cap, expect=('rejected',))['error']['code'], 'PRECONDITION_FAILED', cap)
        r.call('fault.clear', sess=r.E)
        self.assertEqual((r.page()[0], r.state('device.error')), ('ui', None))
        r.call('balance.tare')

    def test_timeout_and_overload(self):
        r = Rig()
        r.call('fault.inject', sess=r.E, code='No. 0')
        self.assertEqual(r.text('n_text'), 'Timeout\nSee manual (Troubleshooting)\nNo. 0')
        r.tap('n_ok')
        r.call('fault.clear', sess=r.E)
        r.call('fault.inject', sess=r.E, code='Overload')                                 # no message window: in place of the weight
        self.assertEqual((r.page()[0], r.text('weight')), ('ui', 'Overload'))
        self.assertEqual(r.call('balance.zero', expect=('rejected',))['error']['code'], 'PRECONDITION_FAILED')
        r.call('fault.clear', sess=r.E)
        self.assertNotEqual(r.text('weight'), 'Overload')


class DosingBehaviour(unittest.TestCase):
    """Weighing parameters, algorithm and tapper act on the dosing (p.62, p.66-67, p.70; timing values are assumptions)."""
    def run_dose(self, settings, step_to=None):
        r = Rig()
        for k, v in settings.items():
            r.call('settings.set_profile', key=k, value=v)
        start_direct(r)
        return r

    def test_factory_release_is_immediate_and_slower_settings_settle(self):
        r = self.run_dose({})
        r.step(5.0)
        self.assertEqual(r.state('dosing.status'), 'succeeded')                          # factory: as the ideal 1.0 model
        r = self.run_dose({'value_release': 'Very reliable'})                            # 3.0 s release: 2.25 s more than the factory 0.75 s
        r.step(6.0)
        self.assertEqual((r.state('dosing.status'), r.state('dosing.phase'), r.page()[0]), ('running', 'settling', 'dosing'))
        self.assertEqual(r.text('d_phase'), 'Waiting for stability ...')
        self.assertAlmostEqual(r.state('measurement.actual_dosed_kg'), 5e-05)
        r.step(1.3)
        res = r.state('dosing.last_result')
        self.assertEqual((r.state('dosing.status'), r.state('dosing.phase'), r.state('dosing.elapsed_s')), ('succeeded', None, 5.0))
        self.assertAlmostEqual(res['duration_s'], 7.25)
        r = self.run_dose({'environment': 'Unstable', 'weighing_mode': 'Checkweighing'})  # 0.75 x 1.5 x 2 = 2.25 s: 1.5 s more
        r.step(6.4)
        self.assertEqual(r.state('dosing.status'), 'running')
        r.step(0.2)
        self.assertEqual(r.state('dosing.status'), 'succeeded')
        r = self.run_dose({'value_release': 'Very fast', 'weighing_mode': 'Dosing'})     # faster than the ideal reading: no change
        r.step(5.0)
        self.assertEqual(r.state('dosing.status'), 'succeeded')

    def test_algo_p_slows_the_last_ten_percent(self):
        r = self.run_dose({'algo': 'Algo P'})
        r.step(4.5)
        self.assertAlmostEqual(r.state('measurement.actual_dosed_kg'), 4.5e-05)
        r.step(1.5)
        self.assertEqual(r.state('dosing.status'), 'running')
        self.assertAlmostEqual(r.state('measurement.actual_dosed_kg'), 4.875e-05)        # a quarter of the rate after 90 %
        r.step(0.6)
        self.assertEqual(r.state('dosing.status'), 'succeeded')
        self.assertAlmostEqual(r.state('dosing.elapsed_s'), 6.5)

    def test_tapper_before_while_and_manual(self):
        r = self.run_dose({'tap_before_dosing': True, 'tapper_duration_s': 3, 'tapper_intensity': 80})
        self.assertEqual((r.state('dosing.phase'), r.state('tapper.last_tap')['trigger'], r.state('tapper.last_tap')['intensity_percent']),
                         ('tapping', 'before_dosing', 80))
        r.step(3.0)
        self.assertEqual((r.state('dosing.phase'), r.state('measurement.actual_dosed_kg')), ('dosing', 0.0))   # no powder while tapping
        r.step(5.0)
        self.assertEqual(r.state('dosing.status'), 'succeeded')
        self.assertAlmostEqual(r.state('dosing.last_result')['duration_s'], 8.0)
        dry = Rig(source=2e-05)                                                            # the powder stops at 2 s: automatic tap first (factory On)
        start_direct(dry)
        dry.step(2.5)
        self.assertEqual((dry.state('dosing.status'), dry.state('dosing.phase'), dry.state('tapper.last_tap')['trigger']), ('running', 'tapping', 'while_dosing'))
        dry.step(0.6)
        self.assertEqual(dry.state('dosing.status'), 'failed')
        off = Rig(source=2e-05)
        off.call('settings.set_profile', key='tap_while_dosing', value=False)
        start_direct(off)
        off.step(2.1)
        self.assertEqual((off.state('dosing.status'), off.state('tapper.taps')), ('failed', 0))
        m = Rig()
        m.call('settings.set_profile', key='tapper_duration_s', value=4)
        m.call('tapper.tap')
        self.assertEqual({k: m.state('tapper.last_tap')[k] for k in ('trigger', 'intensity_percent', 'duration_s')},
                         {'trigger': 'manual', 'intensity_percent': 50, 'duration_s': 4.0})


class AdjustmentsAndLabels(unittest.TestCase):
    def test_profact_time_criterion(self):
        r = Rig(container=False)
        r.call('clock.set', date='2026-10-19', time='08:58:00')                           # a Monday
        r.step(100)
        self.assertFalse(r.state('balance.adjust_pending'))
        r.step(50)                                                                         # 9:00 passed, 150 s without a key: ProFACT runs
        hist = r.state('balance.adjustment_history')
        self.assertEqual((hist[-1]['type'], hist[-1]['kind'], r.state('balance.adjust_pending')), ('Adjust int', 'Time Adjust.', False))
        rec = r.state('output.records')[-1]
        self.assertEqual(rec['kind'], 'adjustment_protocol')                              # Protocol Trigger On
        self.assertEqual(rec['lines'][:2], ['Adjustment     Adjust int', 'Result         done'])
        self.assertIn('Balance Type   Quantos Q2 / XPE206DR', rec['lines'])                # factory Protocol items (p.78)
        loaded = Rig()                                                                      # a vessel on the pan: the icon waits (p.36: unload)
        loaded.call('settings.set_system', key='profact_weekdays', value=['Monday'])
        loaded.call('clock.set', date='2026-10-19', time='08:59:00')
        loaded.step(300)
        self.assertTrue(loaded.state('balance.adjust_pending'))
        self.assertIn('ProFACT', loaded.text('status'))
        other = Rig(container=False)
        other.call('settings.set_system', key='profact_weekdays', value=['Tuesday'])
        other.call('clock.set', date='2026-10-19', time='08:59:00')
        other.step(300)
        self.assertEqual(other.state('balance.adjustment_history'), [])

    def test_history_selection_datasets_and_report(self):
        r = Rig()
        r.call('settings.set_profile', key='function_keys', value=['Adjust.int', 'Adj. History'])
        for _ in range(12):
            r.call('balance.adjust_internal')
        r.call('settings.set_system', key='adj_history_datasets', value=10)
        r.fk('Adj. History')
        self.assertEqual(len(r.widgets()['h_list']['cells']), 10)                            # memory 12, list the last 10
        r.tap('h_ok')
        r.call('settings.set_system', key='adj_history_selection', value=['Time Adjust.'])
        r.fk('Adj. History')
        self.assertEqual(r.widgets()['h_list']['cells'], [])
        self.assertEqual(len(r.state('balance.adjustment_history')), 12)                     # all recorded
        r.tap('h_ok')
        r.call('settings.set_system', key='adj_history_selection', value=['Manual adjust.'])
        r.call('settings.set_system', key='adjust_protocol', value=['Date/Time', 'Weight ID', 'Signature'])
        r.call('output.print', content='adjustments')
        lines = r.state('output.records')[-1]['lines']
        self.assertEqual(lines[:5], ['Adjustment     Adjust int', 'Result         done', 'Date/Time      %s' % lines[2].split(None, 1)[1],
                                     'Weight ID      Internal weight', 'Signature ________'])
        self.assertEqual(len(lines), 50)                                                     # 10 entries x 5 lines

    def test_sample_labels_layout_code_and_cutter(self):
        r = Rig()
        for k, v in (('sample_output_devices', ['Label']), ('sample_label_layout', 8), ('sample_label_barcode', 'Sample ID'), ('sample_label_quantity', 2),
                     ('sample_label_cutter', 'Series')):
            r.call('settings.set_profile', key=k, value=v)
        r.call('settings.set_profile', key='function_keys', value=['Start', 'Samples'])
        r.call('dosing.set_samples', count=2)
        for n in range(2):
            r.dose(target='10', sample=f'S{n}')
            r.step(2)
            r.tap('r_ok')
            if r.overlay():
                r.ov('OK')
        labels = [x for x in r.state('output.records') if x['kind'] == 'sample_label']
        self.assertEqual(len(labels), 4)
        self.assertEqual([len(x['lines']) for x in labels], [3, 3, 3, 3])                    # layout 8: 3 text rows
        self.assertEqual([x['code'] for x in labels[:2]], [{'type': 'code128', 'item': 'Sample ID', 'value': 'S0'}] * 2)
        self.assertEqual([x['cut'] for x in labels], [False, False, False, True])            # Series: cut at the end of the sequence
        self.assertEqual([x['label_index'] for x in labels], [1, 2, 1, 2])
        r.call('settings.set_profile', key='sample_label_layout', value=1)
        r.call('settings.set_profile', key='sample_label_quantity', value=1)
        r.dose(target='10', sample='S9')
        r.step(2)
        self.assertIsNone(r.state('output.records')[-1]['code'])                            # layout 1 without matrix items: no code
        self.assertTrue(r.state('output.records')[-1]['cut'])                               # no series running: cut
        r.tap('r_ok')
        r.call('settings.set_profile', key='sample_label_matrix', value=['Substance', 'Sample ID'])
        r.dose(target='10', sample='S10')
        r.step(2)
        self.assertEqual(r.state('output.records')[-1]['code'], {'type': 'matrix', 'items': {'Substance': None, 'Sample ID': 'S10'}})


class TerminalDisplay(unittest.TestCase):
    def test_optical_key_feedback_and_speedread(self):
        r = Rig()
        r.key('print')
        self.assertTrue(r.frame()['displays']['key_led_left']['shown'] and r.frame()['displays']['key_led_right']['shown'])
        r.step(0.5)
        self.assertFalse(r.frame()['displays']['key_led_left']['shown'])
        r.call('settings.set_profile', key='optical_key_feedback', value=False)
        r.key('print')
        self.assertFalse(r.frame()['displays']['key_led_right']['shown'])
        r.dose()
        self.assertEqual(r.widgets()['d_value']['color'], 'disabled')                        # Speedread On: lighter while unstable
        s = Rig()
        s.call('settings.set_profile', key='speedread', value=False)
        s.dose()
        self.assertEqual(s.widgets()['d_value']['color'], 'text')

    def test_password_change_reminder(self):
        r = Rig()
        r.call('clock.set', date='2026-10-19', time='08:00:00')
        r.call('settings.set_system', key='password_change_date', value='20.10.2026')
        self.assertEqual(r.page()[0], 'ui')
        r.step(24 * 3600)
        self.assertEqual(r.page()[0], 'pw_remind')
        r.tap('n_ok')
        self.assertEqual(r.page()[0], 'ui')
        r.step(2 * 3600)
        self.assertEqual(r.page()[0], 'ui')
        r.step(3600 + 1)
        self.assertEqual(r.page()[0], 'pw_remind')                                           # again after 3 hours

    def test_level_sensor_warning_and_leveling_assistant(self):
        r = Rig()
        r.call('dosing.set_target', target_mass_kg=2.5e-05)
        beeps = r.frame()['ui']['beeps']
        r.h.host_set_level(INST, bubble_x=0.6, bubble_y=-0.2, evidence='synthetic: the bench is not level')
        self.assertEqual((r.state('balance.level_ok'), r.page()[0]), (False, 'level_warn'))
        self.assertEqual(r.frame()['ui']['beeps'], beeps + 1)                                # Warning Beep Once
        beeps += 1
        r.tap('n_ok')
        self.assertEqual(r.page(), ('ui', 'main'))
        self.assertIn('Level!', r.text('status'))
        r.step(1800)
        self.assertEqual(r.page()[0], 'ui')                                                  # Once
        r.tap('info0')                                                                       # the information fields start the Leveling Assistant
        self.assertEqual(r.page(), ('ui', 'leveling'))
        w = r.widgets()
        self.assertEqual((w['lv_bubble']['xy'], w['lv_text']['text'][:23]), ([0.6, -0.2], 'The balance is not leve'))
        r.h.host_set_level(INST, bubble_x=0.1, bubble_y=0.0, evidence='footscrews turned')
        self.assertEqual((r.state('balance.level_ok'), r.text('lv_text')), (True, 'The balance is level.'))
        r.tap('lv_ok')
        r.call('settings.set_system', key='level_warning_text', value='Repeat')
        r.h.host_set_level(INST, bubble_x=0.0, bubble_y=0.9, evidence='tilted again')
        self.assertEqual(r.page()[0], 'level_warn')
        r.tap('n_ok')
        r.step(rt.QUANTOS_LEVEL_REPEAT_S)
        self.assertEqual(r.page()[0], 'level_warn')                                          # Repeat: every 15 minutes
        self.assertGreater(r.frame()['ui']['beeps'], beeps)
        r.tap('n_ok')
        r.call('settings.set_system', key='levelcontrol', value=False)
        self.assertEqual((r.state('balance.level_ok'), r.page()[0]), (None, 'ui'))
        self.assertNotIn('Level!', r.text('status'))


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        self.assertTrue((a.h.panel(INST).render('terminal') == b.h.panel(INST).render('terminal')).all())
        OUT.mkdir(parents=True, exist_ok=True)
        a.call('dosing.set_target', target_mass_kg=5e-05)
        a.call('dosing.set_tolerance_percent', percent=1.0)
        a.key('tare')
        shots = {'home': a.h.panel(INST).save_png('terminal', OUT / 'home.png')}
        a.key('configuration')
        shots['menu'] = a.h.panel(INST).save_png('terminal', OUT / 'menu.png')
        a.key('home')
        a.dose(target='50')
        a.step(6)
        shots['result'] = a.h.panel(INST).save_png('terminal', OUT / 'result.png')
        c = Rig()
        c.call('clock.set', date='2026-10-19', time='08:00:00')
        c.call('head.write_data', data={'exp_date': '2026-10-01'})
        c.call('head.set_lock', locked=False)
        c.fk('Start')
        shots['error'] = c.h.panel(INST).save_png('terminal', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        r = Rig()
        r.h.panel_capture_dir = str(OUT / 'captures')
        res = r.call('panel.capture', display_id='terminal')
        self.assertEqual(res['image']['sha256'], hashlib.sha256(Path(res['image']['path']).read_bytes()).hexdigest())

    def test_spec_and_contract(self):
        meta = rt.export_quantos_q2_contract_metadata(panel=True)
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities'])), [])
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for sid, slot in meta['state_slots'].items():
            self.assertEqual(c['state_slots'][sid.lower()]['value_schema'], slot['value_schema'], sid)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['ui_class'], 'touch_gui')
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime_code'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('qt_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 1:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
