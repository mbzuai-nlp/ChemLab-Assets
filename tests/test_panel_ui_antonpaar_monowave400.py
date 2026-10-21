"""front-panel tests for CHEM-015 antonpaar_monowave400 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on
scripts/asset_runtime/antonpaar_monowave400.py): python3 -m pytest tests/test_panel_ui_antonpaar_monowave400.py -q

The panel follows the facility SOP (Task list, [A], Copy, Details, Home, Insert steps, Menu > Data Memory > Browse Results > Export, log in)
and the specification sheet; the status area and the run screen are assumed.  Every gesture goes through the session API (panel.switch /
panel.touch); widgets are found by id or text in the structured frame (panel.frame, evaluator).  Themes: the six key procedures of the menu
survey; runs with results; panel = direct capability; refusals shown as the device's own message; time behaviour; render determinism;
contract entries."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import antonpaar_monowave400 as rt  # noqa: E402
from antonpaar_panel_rig import Rig as _Rig  # noqa: E402
from asset_tool import validate_value  # noqa: E402

CONTRACT = ROOT / 'assets/expansion_batch03/t5_automation/antonpaar_monowave400/asset.contract.json'
SURVEY_TASKS = ['create a new experiment', 'add another step to an experiment', 'copy an existing experiment', 'view experiment details',
                'export results', 'switch the reactor on']
DONE_TASKS = set()


class Rig(_Rig):
    def __init__(self, power=True, login='Operator'):
        super().__init__(rt, 'AntonpaarMonowave400Runtime', power=('mains_switch', 'on') if power else None)
        if power and login:
            self.row(login)

    def vial(self):
        """The trusted host inserts a sealed G30 vial with ethanol, closes and locks the lid and connects the cooling air."""
        self.call(self.A, 'lid.set_lift', lift_m=.15)
        self.call(self.E, 'sample.configure', **rt.EXAMPLE)
        self.call(self.A, 'lid.set_lift', lift_m=0.)
        self.call(self.A, 'lock.set_travel', travel_m=.01)
        self.call(self.E, 'gas.configure', **rt.CAPS['gas.configure'][6])

    def home(self):
        for _ in range(3):
            if self.overlay():
                self.ov(self.overlay()['buttons'][0]['label'])
        if self.page() != ('main', 'home'):
            self.tap_text('Home')
        if self.page() != ('main', 'home'):
            self.tap_text('Home')
        assert self.page() == ('main', 'home'), self.page()

    def new(self, name, temperature='80', hold='30', cool='50', vial=None):
        """Task list > [A] > name > OK > Standard > OK > Add Experiment (steps, vial type) > OK (sop p.1 step 6)."""
        self.home()
        self.tap_text('Task list')
        self.row('[A]', 'exp_add')
        self.enter('Experiment name', name)
        self.tap_text('OK')
        self.tap_text('OK')
        self.enter('Temperature', temperature)
        self.enter('Hold time', hold)
        self.enter('Cool down to', cool)
        if vial:
            self.choose('Vial type', vial)
        self.tap_text('OK')

    def exps(self):
        return {e['name']: e for e in self.val('experiments.list')}


# ---------------------------------------------------------------- the key procedures of the survey
class SurveyProcedures(unittest.TestCase):
    def test_switch_the_reactor_on(self):
        r = Rig(power=False)
        self.assertEqual(r.page()[0], 'off')
        self.assertFalse(r.frame()['displays']['status_led']['on'])
        r.call(r.A, 'panel.switch', input_id='mains_switch', position='on')   # switch on the left side (sop step 4)
        self.assertEqual(r.page()[0], 'login')                                 # log in to your account (step 5)
        self.assertTrue(r.frame()['displays']['status_led']['on'])
        r.row('Operator')
        self.assertEqual((r.page(), r.val('device.users')['current']), (('main', 'home'), 'Operator'))
        self.assertEqual(r.text('home_user'), 'Operator')
        DONE_TASKS.add('switch the reactor on')

    def test_create_a_new_experiment(self):
        r = Rig()
        r.tap_text('Task list')
        r.row('[A]', 'exp_add')                                                # [A] in the last row: the name dialog opens
        self.assertEqual(r.page(), ('main', 'exp_name'))
        r.enter('Experiment name', 'EtOH 80')
        r.tap_text('OK')
        self.assertEqual(r.cells(), {'Experiment type': 'Standard'})           # select Standard, OK
        r.tap_text('OK')
        self.assertEqual(r.page(), ('main', 'exp_add'))                        # the Add Experiment dialog
        r.enter('Temperature', '80')
        r.enter('Hold time', '30')
        r.enter('Cool down to', '50')
        r.choose('Vial type', 'G30')
        r.tap_text('OK')
        e = r.exps()['EtOH 80']
        self.assertEqual((e['type'], e['temperature_c'], e['hold_s'], e['cool_to_c'], e['vial']), ('Standard', 80, 30, 50, 'G30'))
        self.assertEqual(e['steps'], ['Heat to 80 °C as fast as possible', 'Hold time 30 s', 'Cool down to 50 °C'])
        self.assertEqual(r.rows('exp_rows'), [['EtOH 80', 'Heat to 80 °C as fast as possible']])
        DONE_TASKS.add('create a new experiment')

    def test_add_another_step(self):
        r = Rig()
        r.new('Ramp')
        r.row('Ramp', 'exp_rows')
        r.home()
        r.tap_text('Insert steps')                                             # <Insert steps> on the main screen
        r.row('Heat to temperature in time')
        r.enter('Temperature', '90')
        r.enter('Time', '120')
        r.tap_text('OK')
        e = r.exps()['Ramp']
        self.assertEqual((e['heating'], e['temperature_c'], e['heat_time_s']), ('In time', 90, 120))
        r.tap_text('Insert steps')
        r.row('Hold time')
        r.enter('Time', '300')
        r.tap_text('OK')
        self.assertEqual(r.exps()['Ramp']['steps'], ['Heat to 90 °C in 120 s', 'Hold time 300 s', 'Cool down to 50 °C'])
        DONE_TASKS.add('add another step to an experiment')

    def test_copy_an_existing_experiment(self):
        r = Rig()
        r.new('XXX')
        r.row('XXX', 'exp_rows')
        r.tap_text('Copy')
        self.assertEqual([c[0] for c in r.rows('exp_rows')], ['XXX', 'XXX_copy1'])   # the first copy is named XXX_copy1 (sop step 7)
        r.tap_text('Copy')
        self.assertIn('XXX_copy2', r.exps())
        self.assertEqual({k: v for k, v in r.exps()['XXX_copy1'].items() if k != 'name'}, {k: v for k, v in r.exps()['XXX'].items() if k != 'name'})
        DONE_TASKS.add('copy an existing experiment')

    def test_view_experiment_details(self):
        r = Rig()
        r.new('EtOH 80')
        r.vial()
        r.row('EtOH 80', 'exp_rows')
        r.tap_text('Start')
        self.assertEqual(r.page()[0], 'run')
        r.step(300)
        self.assertEqual(r.page(), ('main', 'home'))
        r.tap_text('Task list')
        r.row('EtOH 80', 'exp_rows')
        r.tap_text('Details')                                                  # graph and conditions (sop step 8)
        self.assertEqual(r.page(), ('main', 'details'))
        w = r.widgets()
        self.assertGreater(len(w['det_plot']['series'][0]['points']), 20)
        self.assertEqual([c[0] for c in r.rows('det_steps')], ['Heat to 80 °C as fast as possible', 'Hold time 30 s', 'Cool down to 50 °C'])
        r.tap_text('Home')                                                     # Home back to the Task list
        self.assertEqual(r.page(), ('main', 'task_list'))
        DONE_TASKS.add('view experiment details')

    def test_export_results(self):
        r = Rig()
        r.new('EtOH 80')
        r.vial()
        r.row('EtOH 80', 'exp_rows')
        r.tap_text('Start')
        r.step(300)
        r.tap_text('Menu')
        r.row('Data Memory')
        r.row('Browse Results')                                                # Menu > Data Memory > Browse Results (sop step 9a)
        self.assertEqual(r.rows('result_rows')[0][1:3], ['EtOH 80', 'succeeded'])
        r.row('1', 'result_rows')
        r.tap_text('Details')
        self.assertEqual(r.page(), ('main', 'result_details'))
        r.tap_text('Back')
        r.tap_text('Export')                                                   # needs a USB storage device or a printer
        self.assertEqual(r.dialog(), 'no_usb')
        DONE_TASKS.add('export results')


# ---------------------------------------------------------------- runs, refusals and timing
class RunsAndRefusals(unittest.TestCase):
    def test_run_follows_the_program_and_stores_the_result(self):
        r = Rig()
        r.new('EtOH 80')
        r.vial()
        r.row('EtOH 80', 'exp_rows')
        r.tap_text('Start')
        m = r.val('method.target_k')
        self.assertAlmostEqual(m, 353.15)
        self.assertEqual((r.val('method.stir_rpm'), r.val('method.pressure_limit_pa')), (600.0, 3e6))
        r.step(20)
        rs = r.val('run.state')
        self.assertEqual((rs['experiment'], rs['stage'], rs['temperature_c']), ('EtOH 80', 'holding', 80.0))
        self.assertIn('°C', r.text('run_values'))
        self.assertEqual(r.text('cam_text'), 'Camera: stirring')
        r.tap_text('Record')                                                   # camera recording needs a USB key
        self.assertEqual(r.dialog(), 'no_usb_record')
        r.ov('OK')
        r.step(300)
        res = r.val('results.list')[-1]
        self.assertEqual((res['experiment'], res['user'], res['status']), ('EtOH 80', 'Operator', 'succeeded'))
        self.assertAlmostEqual(res['max_temperature_c'], 80.0, delta=0.5)
        self.assertLessEqual(r.val('truth.temperature_k'), 323.15 + 0.01)     # cooled down to 50 °C

    def test_stop_and_pressure_limit(self):
        r = Rig()
        r.new('Stop me')
        r.vial()
        r.row('Stop me', 'exp_rows')
        r.tap_text('Start')
        r.step(5)
        r.tap_text('Stop')
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        r.step(1)
        self.assertEqual((r.page(), r.val('results.list')[-1]['status']), (('main', 'home'), 'cancelled'))

    def test_refusals_leave_the_state_unchanged(self):
        r = Rig()
        r.new('Dry')
        r.row('Dry', 'exp_rows')
        before = copy.deepcopy(r.h.read('evaluator'))
        r.tap_text('Start')                                                    # lid not locked
        self.assertEqual(r.dialog(), 'err_LID_OPEN')
        r.ov('OK')
        self.assertEqual(r.h.read('evaluator'), before)
        r.call(r.A, 'lock.set_travel', travel_m=.01)
        r.tap_text('Start')                                                    # no vial
        self.assertEqual(r.dialog(), 'err_NO_VIAL')
        r.ov('OK')
        r.call(r.A, 'lock.set_travel', travel_m=0.)
        r.new('Wide', vial='G30 Wide Neck')
        r.vial()
        r.row('Wide', 'exp_rows')
        r.tap_text('Start')
        self.assertEqual(r.dialog(), 'err_VIAL_TYPE')
        r.ov('OK')
        r.call(r.A, 'lock.set_travel', travel_m=0.)
        r.row('Dry', 'exp_rows')
        r.tap_text('Start')
        self.assertEqual(r.dialog(), 'err_LID_OPEN')
        r.ov('OK')
        r.new('Dry')                                                           # the name exists
        self.assertEqual(r.dialog(), 'err_IN_USE')
        r.ov('OK')
        r.tap_text('Cancel')                                                   # the Add Experiment dialog stays open until Cancel
        self.assertEqual(r.page(), ('main', 'task_list'))
        r.call(r.A, 'experiment.start', expect='NOT_FOUND', name='Nothing')

    def test_logon_required_and_logoff(self):
        r = Rig()
        r.tap_text('Menu')
        r.row('Log off')
        self.assertEqual(r.page()[0], 'login')
        r.call(r.A, 'experiment.save', expect='LOGON_REQUIRED', experiment=dict(rt.EXPERIMENT_DEFAULT, name='X'))
        r.row('Administrator')
        self.assertEqual(r.val('device.users')['current'], 'Administrator')
        r.call(r.A, 'panel.switch', input_id='mains_switch', position='off')
        r.call(r.A, 'panel.switch', input_id='mains_switch', position='on')
        self.assertEqual((r.page()[0], r.val('device.users')['current']), ('login', None))   # log in again after switching on

    def test_insert_steps_needs_a_highlighted_experiment(self):
        r = Rig()
        r.tap_text('Insert steps')
        self.assertEqual(r.dialog(), 'select_first')
        r.ov('OK')
        self.assertEqual(r.page(), ('main', 'task_list'))


class PanelEqualsDirect(unittest.TestCase):
    def test_experiment_and_run_equal_direct_commands(self):
        a, b = Rig(), Rig()
        a.new('E1')
        b.call(b.A, 'experiment.save', experiment=dict(rt.EXPERIMENT_DEFAULT, name='E1', temperature_c=80.0, hold_s=30.0, cool_to_c=50.0))
        self.assertEqual(a.val('experiments.list'), b.val('experiments.list'))
        for r in (a, b):
            r.vial()
        a.row('E1', 'exp_rows')
        a.tap_text('Start')
        b.call(b.A, 'experiment.start', name='E1')
        for r in (a, b):
            r.step(300)
        strip = lambda xs: [{k: v for k, v in x.items()} for x in xs]   # noqa: E731
        self.assertEqual(strip(a.val('results.list')), strip(b.val('results.list')))
        self.assertEqual(a.val('truth.temperature_k'), b.val('truth.temperature_k'))


class CallerRules(unittest.TestCase):
    """An experiment started on the panel belongs to that session's agent (poll, cancel); another agent's Stop and direct acquisition.stop are
    refused (ACCESS_DENIED, state unchanged) with a message box; the mains switch is a hard switch (never refused, the run ends as on a power
    loss); the evaluator is not restricted."""

    def b_touch(self, r, B, text):
        w = r.button(text)
        x, y, ww, hh = w['rect']
        return r.call(B, 'panel.touch', display_id='screen', x=x + ww // 2, y=y + hh // 2)['accepted']

    def test_foreign_stop_refused_mains_switch_physical(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        r.new('Mine')
        r.vial()
        r.row('Mine', 'exp_rows')
        r.tap_text('Start')
        op = r.h.operation_id
        self.assertEqual((r.h._owners.get(op), r.A.poll(op)['status']), ('agent', 'running'))
        r.step(5)
        before = r.h.read('evaluator')
        self.assertFalse(self.b_touch(r, B, 'Stop'))
        self.assertEqual(r.dialog(), 'err_ACCESS_DENIED')                     # message box (assumed text)
        r.ov('OK')
        self.assertEqual(r.call(B, 'acquisition.stop', expect='ACCESS_DENIED')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.h.read('evaluator'), before)
        self.assertTrue(r.call(B, 'panel.switch', input_id='mains_switch', position='off')['accepted'])   # a hard switch: physical
        self.assertEqual((r.val('power.enabled'), r.val('cycle.phase')), (False, 'cancelled'))

    def test_evaluator_is_not_restricted(self):
        r = Rig()
        r.new('Mine')
        r.vial()
        r.row('Mine', 'exp_rows')
        r.tap_text('Start')
        r.step(2)
        r.call(r.E, 'acquisition.stop')
        self.assertEqual(r.val('cycle.phase'), 'cancelled')


class RenderingAndContract(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        r = Rig()
        r.new('E1')
        r.vial()
        r.row('E1', 'exp_rows')
        r.tap_text('Start')
        r.step(10)
        p1, p2 = r.png(), r.png()
        self.assertEqual(p1, p2)
        cap = r.call(r.A, 'panel.capture', display_id='screen')
        self.assertEqual((cap['image']['width'], cap['image']['height']), (800, 600))
        self.assertEqual(cap['image']['sha256'], hashlib.sha256(p1).hexdigest())
        self.assertEqual(r.call(r.A, 'panel.capture', display_id='status_led')['image']['width'], 24)

    def test_frame_is_evaluator_only_and_gestures_validate(self):
        r = Rig()
        self.assertEqual(r.call(r.A, 'panel.frame', expect='ACCESS_DENIED')['error']['code'], 'ACCESS_DENIED')
        r.call(r.A, 'panel.touch', expect='INVALID_ARGUMENT', display_id='screen', x=900, y=10)
        r.call(r.A, 'panel.switch', expect='INVALID_ARGUMENT', input_id='mains_switch', position='middle')
        caps = r.A.describe(instance_id=r.h.instance_id)['capabilities']
        self.assertIn('panel.touch', caps)
        self.assertNotIn('panel.frame', caps)

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + list(rt.DEVICE_CAPS):
            self.assertIn(k, c['capabilities'])
        for k in ('status_led', 'front_panel', 'button_power'):
            self.assertIn(k, c['components'])
        meta = rt.export_antonpaar_monowave400_contract_metadata()
        for k, v in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        r = Rig()
        for sid, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, meta['state_slots'][sid]['value_schema']), [], sid)


if __name__ == '__main__':
    unittest.main()
