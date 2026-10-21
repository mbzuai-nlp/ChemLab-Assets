"""front-panel tests for CHEM-025 buchi_lyovapor_l250 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_vacuum.py):
python3 -m pytest tests/test_panel_ui_buchi_lyovapor_l250.py -q

Every gesture goes through the session API (panel.touch / panel.turn / panel.press / panel.switch); widgets are found by id in the structured frame
(panel.frame, evaluator) and tapped at their centre.  Themes: the eight key procedures of the menu survey; the other panel functions (methods:
general, steps, phases, temperature difference end point, copy / delete / activate; favourites; messages and journal; live graph and progression;
settings; leak test; defrost; sample protection and UNPROT; skip; power failure message); panel = direct commands; refusals as device messages;
timed statuses; render determinism and the evidence PNGs; contract entries."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
import basic40_runtime_vacuum as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'fz'
OUT = ROOT / 'outputs/panel_ui/buchi_lyovapor_l250'
CONTRACT = rt.LYOVAPOR_ASSET_DIR / 'asset.contract.json'
SCREEN = 'screen'
SURVEY_TASKS = ['condition the instrument', 'run manual freeze-drying', 'change a parameter while running', 'create and edit a method step',
                'run a method (pro)', 'end freeze-drying', 'shut down and drain', 'vacuum test']
DONE_TASKS = set()


class Rig:
    def __init__(self, on=True, sample=True, kinetics=True):
        self.scene = []
        self.h = rt.LyovaporHost(scene_adapter=lambda i, s: self.scene.append(copy.deepcopy(s)))
        self.h.create_instance(INST)
        self.h.register_principal('agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        if sample:
            self.call(self.A, 'chamber.set_open', open=True)
            self.h.initialize_sample(INST, dry_mass_kg=.001, ice_mass_kg=.0001, bound_water_mass_kg=.00005, pre_frozen=True, evidence='test vial')
            self.call(self.A, 'chamber.set_open', open=False)
        self.h.connect_vacuum(INST, source_instance_id='pump', asset_type_id='external.synthetic_compatible_vacuum', minimum_pressure_pa=3, evidence='test')
        self.boundary()
        if kinetics and sample:
            self.h.configure_kinetics(INST, sublimation_rate_kg_s=1e-8, desorption_rate_kg_s=5e-9, evidence='test rates')
        if on:
            assert self.switch('on')

    def boundary(self, **kw):
        v = dict(pressure_pa=20.0, shelf_temperature_k=253.15, sample_temperature_k=243.15, condenser_temperature_k=188.15, evidence='test boundary')
        v.update(kw)
        self.h.set_boundary(INST, **v)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        t = 0.0
        while t < dt - 1e-9:
            d = min(3600.0, dt - t)
            self.h.step(d)
            t += d

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCREEN]

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=SCREEN, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def turn(self, n):
        return self.call(self.A, 'panel.turn', input_id='nav_knob', detents=n)['accepted']

    def push(self):
        return self.call(self.A, 'panel.press', input_id='nav_knob')['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='master_switch', position=pos)['accepted']

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        for b in self.overlay()['buttons']:
            if b['label'] == label and b['id'] != 'field':
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in self.overlay()["buttons"]]}')

    def row(self, wid, first):
        for _ in range(20):
            w = self.widgets()[wid]
            for k, cells in enumerate(w['cells']):
                if cells[0] == first or (w['type'] == 'list' and first in cells):
                    return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, w['cells'])
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def type(self, text):
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov
        if ov['field']:
            self.ov('Del')
            while self.overlay()['field']:
                self.ov('Del')
        labels = {b['label'] for b in ov['buttons']}
        numeric = 'q' not in labels and 'Q' not in labels
        for ch in text:
            if numeric:
                assert self.ov(ch), ch
                continue
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit():
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
                if mode != 'lower':
                    self.ov('abc' if mode == 'sym' else 'Shift')
                self.ov(ch)

    def set(self, wid, label, value):
        assert self.row(wid, label), label
        if self.overlay()['kind'] == 'select':
            return self.ov(value)
        self.type(str(value))
        return self.ov('Save')

    def menu(self, name):
        return self.tap({'Start': 'm_start', 'Favourites': 'm_fav', 'Method': 'm_method', 'Configuration': 'm_config', 'Messages': 'm_msg',
                         'Graph': 'm_graph'}[name])

    def condition(self):
        self.menu('Start')
        assert self.tap('fn_condition')
        self.step(rt.LYOVAPOR_CONDITIONING_S)
        assert self.val('system.status') == 'Unload / Load'

    def new_method(self, name='Test method'):
        self.menu('Method')
        self.tap('fn_new' if 'fn_new' in self.widgets() else 'm_new')
        mid = self.val('methods.list')[-1]['id']
        self.row('method_list', 'Method 1' if len(self.val('methods.list')) == 1 else self.val('methods.list')[-1]['name'])
        if self.page() != ('gui', 'method'):
            self.tap('m_open')
        self.tap('act_info')
        self.set('f_info', 'Name', name)
        return mid


class KeyProcedures(unittest.TestCase):
    def test_condition_the_instrument(self):
        r = Rig()
        self.assertEqual(r.text('status'), 'Standby')
        r.tap('fn_condition')
        self.assertEqual(r.val('system.status'), 'Conditioning')
        r.step(600)
        self.assertEqual(r.text('status_clock'), '00:20:00')                    # remaining time
        self.assertIn('fn_skip', r.widgets())
        r.step(1200)
        self.assertEqual(r.text('status'), 'Unload / Load')
        self.assertEqual(r.val('messages.notifications')[-1]['text'], 'Conditioning is completed.')
        DONE_TASKS.add('condition the instrument')

    def test_run_manual_freeze_drying_and_change_a_parameter_while_running(self):
        r = Rig()
        r.condition()
        r.tap('fn_manual')
        r.tap('row_pressure')                                                    # tap the parameter: numeric input
        r.type('0.3')
        r.ov('Save')
        self.assertEqual(r.val('vacuum.target_pressure_pa'), 30.0)
        r.tap('fn_start')
        self.assertEqual(r.text('status'), 'Manual Drying')
        self.assertIn('run_bg', r.widgets())                                     # black background while running
        self.assertTrue(r.val('cycle.running'))
        r.step(125)
        self.assertEqual(r.text('status_clock'), '00:02:05')                    # the clock counts up
        DONE_TASKS.add('run manual freeze-drying')
        # change the shelf temperature with the navigation control: turn to the row, EDIT, turn, press = Save
        r.turn(1)
        r.turn(1)
        self.assertTrue(r.frame()['ui']['focus'] == 'row_shelf')
        r.tap('fn_edit')
        ov = r.overlay()
        self.assertEqual((ov['kind'], ov['field']), ('keyboard', '-20.0'))
        r.turn(55)                                                               # 0.1 C per detent
        self.assertEqual(r.overlay()['field'], '-14.5')
        self.assertTrue(r.push())
        self.assertAlmostEqual(r.val('thermal.target_shelf_temperature_k'), 273.15 - 14.5, places=6)
        self.assertTrue(r.val('cycle.running'))
        DONE_TASKS.add('change a parameter while running')
        r.tap('fn_aerate')
        r.ov('YES')
        self.assertEqual(r.text('status'), 'Aerating')
        self.assertEqual(r.val('cycle.phase'), 'stopped')
        r.step(rt.LYOVAPOR_AERATE_S)
        self.assertEqual(r.text('status'), 'Unload / Load')
        DONE_TASKS.add('end freeze-drying')

    def test_create_and_edit_a_method_step(self):
        r = Rig()
        mid = r.new_method('Vials 1')
        m = next(x for x in r.val('methods.list') if x['id'] == mid)
        self.assertEqual(m['name'], 'Vials 1')
        r.tap('back_method')
        r.tap('act_steps')
        self.assertEqual(r.text('step_no'), 'Step 1 / 2')
        r.set('f_step', 'Term', 240)
        r.set('f_step', 'Shelf temperature', -15)
        r.tap('st_right')
        self.assertEqual(r.text('step_no'), 'Step 2 / 2')
        r.set('f_step', 'Pressure zone', 'Minimum')
        self.assertNotIn('Pressure', [c[0] for c in r.widgets()['f_step']['cells']])   # regulated pressure settings hidden
        r.tap('st_new')
        self.assertEqual(r.text('step_no'), 'Step 3 / 3')
        r.tap('st_left')
        r.tap('st_delete')
        r.ov('OK')
        steps = next(x for x in r.val('methods.list') if x['id'] == mid)['steps']
        self.assertEqual(len(steps), 2)
        self.assertEqual((steps[0]['term_min'], round(steps[0]['shelf_temperature_k'] - 273.15, 2), steps[1]['pressure_zone']), (240.0, -15.0, 'Minimum'))
        DONE_TASKS.add('create and edit a method step')

    def test_run_a_method_with_gas_and_hold(self):
        r = Rig()
        mid = r.new_method('Run me')
        r.tap('back_method')
        r.tap('act_general')
        r.set('f_general', 'Gas type', 'Nitrogen')
        r.set('f_general', 'Sample collapse temperature', -30)
        r.tap('back_method')
        r.tap('act_steps')
        r.set('f_step', 'Term', 2)
        r.tap('st_right')
        r.set('f_step', 'Term', 1)
        r.menu('Method')
        r.tap('m_activate')
        self.assertEqual(r.val('methods.active'), mid)
        r.condition()
        r.tap('fn_start')
        self.assertEqual(r.overlay()['dialog'], 'gas')
        r.ov('YES')
        self.assertEqual(r.text('status'), 'Primary drying - Run me')
        self.assertEqual(r.val('cycle.recipe'), {'pre_freeze_s': 0.0, 'primary_drying_s': 120.0, 'secondary_drying_s': 60.0})
        r.step(60)
        self.assertEqual(r.val('process.step')['index'], 0)
        r.step(70)
        self.assertEqual(r.val('system.status'), 'Secondary drying')
        self.assertEqual(r.val('vacuum.target_pressure_pa'), 10.0)
        r.step(60)
        self.assertEqual(r.val('system.status'), 'Hold')
        self.assertEqual(r.val('vacuum.target_pressure_pa'), 50.0)                # hold phase set points
        self.assertEqual(r.val('cycle.endpoint_status'), 'schedule_complete_unverified')
        DONE_TASKS.add('run a method (pro)')
        # the running (locked) method cannot be changed
        r.tap('fn_aerate')
        r.ov('YES')
        r.step(60)
        self.assertEqual(r.val('system.status'), 'Unload / Load')

    def test_shut_down_drain_and_switch_off(self):
        r = Rig()
        r.condition()
        r.tap('fn_off')
        r.ov('OK')
        self.assertEqual(r.text('status'), 'Shutting down')
        r.step(rt.LYOVAPOR_SHUTDOWN_S)
        self.assertEqual(r.text('status'), 'Standby')
        self.assertTrue(r.switch('off'))
        self.assertFalse(r.val('power.enabled'))
        self.assertEqual(r.frame()['screen'], 'off')
        DONE_TASKS.add('shut down and drain')

    def test_vacuum_test(self):
        r = Rig(sample=False)
        r.condition()
        r.menu('Configuration')
        r.tap('cfg_servicing')
        r.tap('srv_vt')
        r.set('f_vt', 'Vacuum', 0.1)
        r.set('f_vt', 'Time', 5)
        r.tap('vt_start')
        self.assertEqual(r.text('status'), 'Vacuum Test')
        r.boundary(pressure_pa=50000.0)
        r.step(10)
        self.assertTrue(r.val('service.state')['vacuum_test']['running'])
        r.boundary(pressure_pa=9.0)
        r.step(10)
        self.assertEqual(r.val('service.state')['vacuum_test']['result'], 'passed')
        self.assertEqual(r.text('vt_result'), 'Vacuum test: passed')
        self.assertEqual(r.text('status'), 'Unload / Load')
        # 500 mbar not reached after 30 s: aborted with 6275
        r.tap('vt_start')
        r.boundary(pressure_pa=80000.0)
        r.step(40)
        self.assertEqual(r.val('service.state')['vacuum_test']['result'], 'aborted')
        self.assertEqual(r.val('messages.notifications')[-1]['code'], '6275')
        DONE_TASKS.add('vacuum test')


class PanelFunctions(unittest.TestCase):
    def test_leak_test_passes_with_a_tight_system(self):
        r = Rig(sample=False)
        r.condition()
        r.menu('Configuration')
        r.tap('cfg_servicing')
        r.tap('srv_lt')
        r.set('f_lt', 'Volume', 43.41)
        r.tap('lt_start')
        self.assertEqual(r.text('status'), 'Leak Test')
        r.step(rt.LYOVAPOR_LEAK_TEST_S)
        lt = r.val('service.state')['leak_test']
        self.assertEqual((lt['result'], lt['rate'], lt['volume_l']), ('passed', 0.0, 43.41))
        r.tap('lt_start')
        r.boundary(pressure_pa=25.0)                                             # 0.05 mbar rise in 45 min, 43.41 L: 2.9 mbar*L/h
        r.step(rt.LYOVAPOR_LEAK_TEST_S)
        self.assertEqual(r.val('service.state')['leak_test']['result'], 'passed')
        r.tap('lt_start')
        r.boundary(pressure_pa=60.0)
        r.step(rt.LYOVAPOR_LEAK_TEST_S)
        self.assertEqual(r.val('service.state')['leak_test']['result'], 'failed')
        self.assertIn('failed', r.text('lt_result'))

    def test_favourites_messages_settings_graph_and_system_info(self):
        r = Rig()
        r.menu('Configuration')
        r.tap('cfg_settings')
        r.set('f_settings', 'Pressure sensor', 'Inficon Porter CDG020 D')
        self.assertEqual(r.val('settings.values')['pressure_sensor'], 'Inficon Porter CDG020 D')
        self.assertEqual(r.val('messages.notifications')[-1]['code'], '6283')      # the pressure difference kit's sensor is not fitted
        r.tap('fav_settings')
        r.tap('back_config')
        r.tap('cfg_servicing')
        r.tap('fav_servicing')
        self.assertEqual(r.val('favourites.list'), ['settings', 'servicing'])
        r.menu('Favourites')
        r.row('fav_list', 'Settings')
        self.assertEqual(r.page(), ('gui', 'settings'))
        r.menu('Favourites')
        r.row('fav_list', 'Maintenance / Servicing' if False else 'Servicing')
        r.menu('Favourites')
        r.tap('fav_delete')
        self.assertEqual(r.val('favourites.list'), ['settings'])
        r.condition()
        r.menu('Messages')
        self.assertIn('Conditioning is completed.', [c[1] for c in r.widgets()['msg_list']['cells']])
        r.tap('msg_ok')
        self.assertEqual(r.val('messages.notifications'), [])
        r.tap('msg_journal')
        self.assertEqual(len(r.widgets()['journal_list']['cells']), 2)          # 6283 and the end of conditioning
        r.step(600)
        r.menu('Graph')
        series = r.widgets()['live_graph']['series']
        self.assertEqual([s['label'] for s in series], ['Ice condenser C', 'Sample C', 'Shelf C', 'Pressure mbar'])
        self.assertGreaterEqual(len(series[0]['points']), 10)
        self.assertEqual(series[0]['points'][-1][1], -85.0)
        r.menu('Configuration')
        r.tap('cfg_system_info')
        self.assertIn('L-250 Pro', r.text('si_0'))
        r.tap('back_config')
        r.tap('cfg_service')
        r.tap('srv_enter')
        self.assertEqual(r.overlay()['dialog'], 'service_code')
        self.assertTrue(r.frame()['ui']['unsupported'])

    def test_methods_copy_delete_activate_phases_and_temperature_difference_end_point(self):
        r = Rig()
        mid = r.new_method('Base')
        r.tap('back_method')
        r.tap('act_phase')
        r.tap('ph_primary')
        r.set('f_phase_primary', 'Pressure action', 'Message')
        r.row('f_phase_primary', 'End point definition')
        r.tap('ep_pdt')
        self.assertEqual(r.overlay()['dialog'], 'no_kit')
        r.ov('OK')
        r.tap('ep_tdt')
        r.set('f_tdt_primary', 'Temperature difference test', 'Yes')
        r.set('f_tdt_primary', 'Start time', 30)
        r.set('f_tdt_primary', 'Temperature difference limit', 3)
        r.set('f_tdt_primary', 'Term', 1)
        m = next(x for x in r.val('methods.list') if x['id'] == mid)
        self.assertEqual(m['phases']['primary']['tdt'], {'enabled': True, 'start_time_min': 30.0, 'limit_k': 3.0, 'term_min': 1.0, 'continue': True,
                                                         'message': True})
        self.assertEqual(m['phases']['primary']['pressure_action'], 'Message')
        r.menu('Method')
        r.tap('m_copy')
        self.assertEqual(len(r.val('methods.list')), 2)
        copy_id = r.val('methods.list')[-1]['id']
        r.row('method_list', 'Base copy')
        r.tap('m_delete')
        r.ov('Confirm')
        self.assertEqual([x['id'] for x in r.val('methods.list')], [mid])
        self.assertNotEqual(copy_id, mid)
        # run with the end point: primary drying of 120 min ends once shelf - sample < 3 K for 1 min within the last 30 min
        r.call(r.A, 'methods.step', method_id=mid, action='set', index=0, values={'term_min': 120.0})
        r.row('method_list', 'Base')
        r.tap('m_activate')
        r.condition()
        r.tap('fn_start')
        r.step(60 * 60)
        self.assertEqual(r.val('system.status'), 'Primary drying')
        r.boundary(sample_temperature_k=252.0)                                   # shelf 253.15 - sample 252 = 1.15 K
        r.step(60 * 29)                                                          # the test begins 30 min before the end of primary drying
        self.assertEqual(r.val('system.status'), 'Primary drying')
        r.step(180)
        self.assertEqual(r.val('system.status'), 'Secondary drying')
        self.assertLess(r.val('cycle.elapsed_s'), 100 * 60)
        self.assertIn('temperature difference test', r.val('messages.journal')[-1]['text'])

    def test_sample_protection_message_unprot_and_skip(self):
        r = Rig()
        mid = r.new_method('Protect')
        r.call(r.A, 'methods.step', method_id=mid, action='set', index=0, values={'pressure_limit_pa': 10.0, 'pressure_duration_s': 300.0})
        r.call(r.A, 'methods.activate', method_id=mid)
        r.condition()
        r.menu('Start')
        r.tap('fn_start')
        r.boundary(pressure_pa=35.0)                                             # above the 30 Pa set point but inside the 10 Pa limit
        r.step(600)
        self.assertFalse(r.val('protection.state')['pressure'])
        r.boundary(pressure_pa=80.0)                                             # outside the limit: protection after the 300 s duration
        r.step(240)
        self.assertFalse(r.val('protection.state')['pressure'])
        r.step(120)
        self.assertTrue(r.val('protection.state')['pressure'])
        self.assertIn(' P', r.text('status_symbols'))
        e0 = r.val('cycle.elapsed_s')
        r.step(60)
        self.assertEqual(r.val('cycle.elapsed_s'), e0)                           # held
        r.tap('fn_unprot')
        r.step(60)
        self.assertGreater(r.val('cycle.elapsed_s'), e0)
        self.assertFalse(r.val('protection.state')['pressure'])
        r.tap('fn_skip')                                                          # skips the step in progress
        self.assertEqual(r.val('system.status'), 'Secondary drying')
        # with Message the process goes on and the control panel shows a message
        r.tap('fn_aerate')
        r.ov('YES')
        r.step(60)
        r.call(r.A, 'methods.phase', method_id=mid, phase='primary', values={'pressure_action': 'Message'})
        r.tap('fn_start')
        r.step(60)
        self.assertEqual(r.val('messages.notifications')[-1]['text'], 'The pressure is outside the limits.')
        self.assertFalse(r.val('protection.state')['pressure'])
        e1 = r.val('cycle.elapsed_s')
        r.step(600)
        self.assertGreater(r.val('cycle.elapsed_s'), e1)

    def test_run_step_delete_and_lock(self):
        r = Rig()
        mid = r.new_method('Three')
        r.call(r.A, 'methods.step', method_id=mid, action='add', index=1, values={'term_min': 5.0})
        r.call(r.A, 'methods.activate', method_id=mid)
        r.condition()
        r.menu('Start')
        r.tap('fn_start')
        r.step(30)
        self.assertEqual(r.call(r.A, 'methods.save', expect='rejected', method_id=mid, values={'name': 'x'})['error']['code'], 'METHOD_ACTIVE')
        r.row('run_steps', 'Primary drying')                                     # the running step: cannot be deleted
        r.tap('fn_delete_step')
        r.ov('OK')
        self.assertEqual(r.overlay()['dialog'], 'err_locked')
        r.ov('OK')
        w = r.widgets()['run_steps']
        r.touch(w['rect'][0] + 10, w['rect'][1] + 2 * w['row_h'] + 5)            # step 3 (secondary drying): a coming step
        r.tap('fn_delete_step')
        r.ov('OK')
        self.assertEqual(len(next(x for x in r.val('methods.list') if x['id'] == mid)['steps']), 2)
        self.assertEqual(r.val('cycle.recipe')['secondary_drying_s'], 0.0)

    def test_defrost_extend_reduce_and_drain(self):
        r = Rig(sample=False)
        r.h.disconnect_vacuum(INST)
        r.tap('fn_defrost')
        self.assertEqual(r.text('status'), 'Defrosting')
        r.tap('fn_defrost_plus')
        r.tap('fn_defrost_plus')
        self.assertEqual(r.val('defrost.state')['remaining_s'], 7200.0)
        r.tap('fn_defrost_minus')
        self.assertEqual(r.text('status_clock'), '01:30:00')
        for _ in range(8):
            r.tap('fn_defrost_plus')
        self.assertEqual(r.overlay()['dialog'], 'err_limit')                    # at most 300 min in total
        r.ov('OK')
        r.h._instances[INST].state['condenser.ice_mass_kg'] = 0.002
        r.boundary(condenser_temperature_k=290.0)
        r.step(300 * 60)
        self.assertEqual(r.text('status'), 'Standby')
        self.assertEqual(r.val('condenser.drained_mass_kg'), 0.002)

    def test_power_failure_message(self):
        r = Rig()
        r.condition()
        r.tap('fn_manual')
        r.tap('fn_start')
        r.switch('off')
        self.assertEqual(r.val('cycle.phase'), 'aborted')
        r.switch('on')
        self.assertEqual(r.val('messages.notifications')[-1]['code'], '6042')
        self.assertEqual(r.text('status'), 'Standby')


class RefusalsAndLocks(unittest.TestCase):
    def test_refusals_are_shown_as_device_messages(self):
        r = Rig(sample=False)
        r.tap('fn_condition')
        r.step(rt.LYOVAPOR_CONDITIONING_S)
        r.tap('fn_method')
        r.menu('Start')
        r.tap('fn_start')
        self.assertEqual(r.overlay()['dialog'], 'err_method')                    # no method activated
        r.ov('OK')
        r.tap('fn_manual')
        r.tap('fn_start')
        self.assertEqual(r.overlay()['dialog'], 'err_pre')                       # no frozen sample loaded
        r.ov('OK')
        before = r.val('system.status')
        self.assertFalse(r.tap('fn_start'))
        self.assertEqual(r.val('system.status'), before)

    def test_power_off_ignores_touches(self):
        r = Rig(on=False)
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertFalse(r.touch(100, 100))
        self.assertFalse(r.turn(1))


class PanelEqualsDirect(unittest.TestCase):
    def test_same_state_through_panel_and_direct_commands(self):
        a, b = Rig(), Rig()
        a.condition()
        a.tap('fn_manual')
        a.tap('row_pressure')
        a.type('0.25')
        a.ov('Save')
        a.tap('fn_start')
        b.call(b.A, 'process.condition')
        b.step(rt.LYOVAPOR_CONDITIONING_S)
        b.call(b.A, 'process.select_mode', mode='manual')
        b.call(b.A, 'process.set_parameter', pressure_pa=25.0)
        b.call(b.A, 'process.start')
        for sid in ('system.status', 'vacuum.target_pressure_pa', 'thermal.target_shelf_temperature_k', 'cycle.phase', 'cycle.recipe', 'process.manual'):
            self.assertEqual(a.val(sid), b.val(sid), sid)


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_evidence(self):
        a, b = Rig(), Rig()
        self.assertEqual(a.h.panel(INST).png_bytes(SCREEN)[0], b.h.panel(INST).png_bytes(SCREEN)[0])
        self.assertEqual(a.h.panel(INST).render(SCREEN).shape, (480, 800, 3))
        OUT.mkdir(parents=True, exist_ok=True)
        renders = {}
        r = Rig()
        r.condition()
        r.tap('fn_manual')
        renders['home'] = r.h.panel(INST).save_png(SCREEN, OUT / 'home.png')
        r.new_method('Vials')
        r.tap('back_method')
        r.tap('act_steps')
        renders['menu'] = r.h.panel(INST).save_png(SCREEN, OUT / 'menu.png')
        r.menu('Start')
        r.tap('fn_method')
        r.menu('Start')
        r.tap('fn_start')
        renders['error'] = r.h.panel(INST).save_png(SCREEN, OUT / 'error.png')
        r.ov('OK')
        r.call(r.A, 'methods.activate', method_id=r.val('methods.list')[0]['id'])
        r.menu('Start')
        r.tap('fn_start')
        r.step(1800)
        renders['running'] = r.h.panel(INST).save_png(SCREEN, OUT / 'running.png')
        r.menu('Graph')
        renders['graph'] = r.h.panel(INST).save_png(SCREEN, OUT / 'graph.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')

    def test_metadata_and_contract(self):
        meta = rt.export_lyovapor_contract_metadata()['asset_types'][rt.LYOVAPOR_ASSET_TYPE_ID]
        self.assertEqual(set(meta['device_capabilities']), set(rt.LYOVAPOR_DEVICE_ARGS))
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.turn', 'panel.switch', 'panel.touch', 'panel.capture', 'panel.frame'})
        for name, cap in {**meta['device_capabilities'], **meta['panel_capabilities']}.items():
            self.assertEqual(validate_value(cap['example_arguments'][0], cap['input_schema']), [], name)
        c = json.loads(CONTRACT.read_text())
        if 'chemistry_lab.panel_ui' in c['extensions']:
            for name in list(meta['device_capabilities']) + list(meta['panel_capabilities']):
                self.assertIn(name, c['capabilities'], name)
            self.assertEqual(c['asset_version'], rt.LYOVAPOR_ASSET_VERSION)
        r = Rig()
        caps = r.A.describe(instance_id=INST)['capabilities']
        self.assertIn('panel.turn', caps)
        self.assertNotIn('panel.frame', caps)
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')


class SyncAudit20261019(unittest.TestCase):
    """Fixes of the 2026-10-19 front-panel sync audit: Safety temp. Time, Shelf load temp., the leak / vacuum test set points and the absent
    pressure-difference sensor act (rule 3); panel gestures act as the calling session (rule 2)."""

    def protected_run(self, safety_min):
        r = Rig()
        mid = r.new_method('Safety')
        r.call(r.A, 'methods.save', method_id=mid, values={'collapse_temperature_k': 238.15})      # -35 C, safety temperature 2 K
        r.call(r.A, 'methods.phase', method_id=mid, phase='primary', values={'safety_time_min': float(safety_min)})
        r.call(r.A, 'methods.activate', method_id=mid)
        r.condition()
        r.boundary(sample_temperature_k=233.15)
        r.menu('Start')
        r.tap('fn_start')
        r.step(45 * 60)                                                          # 15 min of the 60 min primary drying left
        r.boundary(sample_temperature_k=243.15)                                  # -30 C: above collapse + safety temperature
        e0 = r.val('cycle.elapsed_s')
        r.step(120)
        return r, e0

    def test_safety_temp_time_lifts_the_temperature_protection_before_the_end_of_primary_drying(self):
        r, e0 = self.protected_run(0)
        self.assertTrue(r.val('protection.state')['temperature'])
        self.assertEqual(r.val('cycle.elapsed_s'), e0)                           # held by the sample protection
        r, e0 = self.protected_run(20)
        self.assertFalse(r.val('protection.state')['temperature'])
        self.assertAlmostEqual(r.val('cycle.elapsed_s'), e0 + 120.0)            # within the last 20 min the protection is inactive (p.47)

    def test_shelf_load_temperature_is_the_set_point_at_unload_load(self):
        r = Rig()
        mid = r.new_method('Load cold')
        r.tap('back_method')
        r.tap('act_general')
        r.set('f_general', 'Shelf load temp.', -40)
        self.assertAlmostEqual(r.val('methods.list')[0]['shelf_load_temperature_k'], 233.15)
        r.call(r.A, 'methods.activate', method_id=mid)
        self.assertEqual(r.val('thermal.target_shelf_temperature_k'), 253.15)  # Standby: not yet
        r.condition()
        self.assertAlmostEqual(r.val('thermal.target_shelf_temperature_k'), 233.15)
        r.menu('Start')
        r.tap('fn_start')
        self.assertAlmostEqual(r.val('thermal.target_shelf_temperature_k'), 263.15)   # the first step takes over

    def test_leak_and_vacuum_tests_drive_their_set_points(self):
        r = Rig(sample=False)
        r.condition()
        before = (r.val('vacuum.target_pressure_pa'), r.val('thermal.target_shelf_temperature_k'))
        r.call(r.A, 'service.leak_test', action='start', condenser_pa=20.0, shelf_temperature_k=263.15, shelf_heating=True)
        self.assertEqual((r.val('vacuum.target_pressure_pa'), r.val('thermal.target_shelf_temperature_k')), (20.0, 263.15))
        r.call(r.A, 'service.leak_test', action='stop')
        self.assertEqual((r.val('vacuum.target_pressure_pa'), r.val('thermal.target_shelf_temperature_k')), before)
        r.call(r.A, 'service.leak_test', action='start', condenser_pa=5.0, shelf_heating=False)
        self.assertEqual((r.val('vacuum.target_pressure_pa'), r.val('thermal.target_shelf_temperature_k')), (10.0, before[1]))
        r.step(rt.LYOVAPOR_LEAK_TEST_S)
        self.assertEqual(r.val('service.state')['leak_test']['result'], 'passed')
        self.assertEqual((r.val('vacuum.target_pressure_pa'), r.val('thermal.target_shelf_temperature_k')), before)
        r.call(r.A, 'service.vacuum_test', action='start', target_pa=15.0, time_s=600.0)
        self.assertEqual(r.val('vacuum.target_pressure_pa'), 15.0)
        r.boundary(pressure_pa=14.0)
        r.step(10)
        self.assertEqual(r.val('service.state')['vacuum_test']['result'], 'passed')
        self.assertEqual(r.val('vacuum.target_pressure_pa'), before[0])

    def test_absent_pressure_difference_sensor_reports_6283(self):
        r = Rig()
        r.call(r.A, 'settings.configure', settings={'pressure_sensor': 'Inficon Porter CDG020 D'})
        self.assertEqual(r.val('messages.notifications')[-1]['text'], '6283 Pressure sensor is disconnected.')
        r.call(r.A, 'settings.configure', settings={'dhcp': False})
        self.assertEqual(len(r.val('messages.notifications')), 1)               # reported once, when it is selected

    def test_panel_gesture_acts_as_the_calling_session(self):
        r = Rig()
        seen = []
        orig = rt.LyovaporSession._apply

        def spy(sess, inst, cap, args, rid):
            if not cap.startswith('panel.'):
                seen.append((sess._LyovaporSession__principal_id, cap))
            return orig(sess, inst, cap, args, rid)
        with mock.patch.object(rt.LyovaporSession, '_apply', spy):
            r.tap('fn_condition')
        self.assertEqual(seen, [('agent', 'process.condition')])
        self.assertIsNone(r.h._panel_caller)


class Sweep20261019(unittest.TestCase):
    """2026-10-19 consistency sweep: the process started by one agent cannot be stopped, powered off or changed by another agent (panel and
    direct path, ACCESS_DENIED, state unchanged, shown as a message); the documented error messages p.85-88 as evaluator fault.inject /
    fault.clear (E errors stop the process and block the starts, W errors only notify, 6278 fails a running leak test)."""

    def b_tap(self, r, B, wid):
        x, y, w, h = r.widgets()[wid]['rect']
        return r.call(B, 'panel.touch', display_id=SCREEN, x=x + w // 2, y=y + h // 2)['accepted']

    def b_ov(self, r, B, label):
        for b in r.overlay()['buttons']:
            if b['label'] == label and b['id'] != 'field':
                x, y, w, h = b['rect']
                return r.call(B, 'panel.touch', display_id=SCREEN, x=x + w // 2, y=y + h // 2)['accepted']
        raise AssertionError(label)

    def test_another_agents_process_cannot_be_stopped_or_changed(self):
        r = Rig()
        r.h.register_principal('agent_b')
        B = r.h.session('agent_b')
        r.tap('fn_condition')                                                    # agent A conditions the instrument on the screen
        r.step(60)
        self.assertEqual(r.h._instances[INST].g03['run_owners'], {'process': 'agent'})
        before = copy.deepcopy(r.h._instances[INST].state)
        self.assertFalse(self.b_tap(r, B, 'fn_skip'))                            # B's Skip: refused with the device's message
        self.assertEqual(r.overlay()['dialog'], 'err_access')
        self.assertIn('started by another user', ' '.join(r.overlay()['lines']))
        r.ov('OK')
        for cap, args in (('process.skip', {}), ('process.set_parameter', dict(pressure_pa=50.0)), ('process.shutdown', {}),
                          ('vacuum.set_pressure', dict(pressure_pa=50.0)), ('defrost.control', dict(action='start'))):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h._instances[INST].state, before)                     # state unchanged
        self.assertTrue(self.b_tap(r, B, 'm_msg'))                               # navigation is free
        r.call(B, 'favourites.add', target='vacuum_test')                        # so are favourites and messages
        r.call(r.E, 'process.set_parameter', pressure_pa=40.0)                   # the evaluator is not restricted
        self.assertTrue(r.call(B, 'panel.switch', input_id='master_switch', position='off')['accepted'])   # hard switch: physical
        self.assertEqual(r.val('system.status'), 'Off')                          # A's conditioning ends as on a power failure
        self.assertNotIn('run_owners', r.h._instances[INST].g03)
        self.assertTrue(r.switch('on'))
        self.assertEqual(r.val('system.status'), 'Standby')
        self.assertEqual(r.val('messages.notifications')[-1]['code'], '6042')    # p.85: power failure has occurred
        r.condition()                                                            # A's new process
        self.assertEqual(r.h._instances[INST].g03['run_owners'], {'process': 'agent'})
        self.assertTrue(self.b_tap(r, B, 'fn_off'))                              # B opens the Shut down question (navigation) ...
        self.assertFalse(self.b_ov(r, B, 'OK'))                                  # ... and confirms: a soft shut down of A's process, refused
        self.assertEqual(r.overlay()['dialog'], 'err_access')
        r.ov('OK')
        r.tap('fn_off')                                                          # A may shut down its own process
        r.ov('OK')
        self.assertEqual(r.val('system.status'), 'Shutting down')

    def test_injected_errors_stop_notify_and_block_until_cleared(self):
        r = Rig()
        self.assertNotIn('fault.inject', r.A.describe(instance_id=INST)['capabilities'])
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='6197')['error']['code'], 'ACCESS_DENIED')
        r.condition()
        r.tap('fn_manual')
        r.tap('fn_start')
        self.assertTrue(r.val('cycle.running'))
        r.call(r.E, 'fault.inject', code='6197')                                 # E: compressor has no power
        self.assertFalse(r.val('cycle.running'))
        self.assertEqual(r.val('system.status'), 'Standby')
        self.assertEqual(r.val('messages.notifications')[-1], dict(code='6197', type='E', text='6197 Compressor has no power.',
                                                                   time=r.val('messages.notifications')[-1]['time']))
        self.assertTrue(r.text('status').endswith('(!)') or '(!)' in json.dumps(r.disp()))
        r.menu('Start')
        r.tap('fn_condition')                                                    # starts are refused until the cause is removed
        self.assertEqual(r.overlay()['dialog'], 'err_fault')
        r.ov('OK')
        self.assertEqual(r.val('system.status'), 'Standby')
        self.assertEqual(r.call(r.A, 'lyophilization.start', expect='rejected')['error']['code'], 'PRECONDITION_FAILED')
        r.call(r.E, 'fault.clear', code='6197')
        self.assertEqual(r.val('faults.active'), [])
        self.assertEqual(r.call(r.E, 'fault.clear', expect='rejected', code='6197')['error']['code'], 'NO_FAULT')
        r.menu('Messages')
        r.tap('msg_ok')                                                          # acknowledged
        self.assertEqual(r.val('messages.notifications'), [])
        r.menu('Start')
        self.assertTrue(r.tap('fn_condition'))
        self.assertEqual(r.val('system.status'), 'Conditioning')
        r.call(r.E, 'fault.inject', code='6170')                                 # W: message only, the process goes on
        self.assertEqual(r.val('system.status'), 'Conditioning')
        self.assertEqual(r.val('messages.journal')[-1]['text'], '6170 High sample load. High ambient temperature.')
        r.step(rt.LYOVAPOR_CONDITIONING_S)
        r2 = Rig(sample=False)
        r2.condition()
        r2.call(r2.A, 'service.leak_test', action='start', condenser_pa=20.0)
        r2.call(r2.E, 'fault.inject', code='6278')                               # pressure rise during the leak test
        self.assertEqual(r2.val('service.state')['leak_test']['result'], 'failed')
        self.assertEqual(r2.val('system.status'), 'Unload / Load')


if __name__ == '__main__':
    unittest.main()
