"""Front-panel tests for CHEM-149 mt_rc1mxhfcal (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/mt_rc1mxhfcal.py):
python3 -m pytest tests/test_panel_ui_mt_rc1mxhfcal.py -q

Gestures only through the session API (panel.touch / panel.press); the host (evaluator) declares the installation, charges water and flags a
manipulation.  Themes: the eight key procedures of the menu survey (turn on, select the reactor type, set T safe, start an experiment, change
Tj, Tr and R, end an experiment); the manipulation warning; safety limits, refusals and the emergency programs (button, Tr max, stirrer off
for 3 min); Notes, Graph, Experiment & Export, Info, Tools and Dose / Charge; access of a second agent; panel = direct command; rendering."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import mt_rc1mxhfcal as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'rc1mx_01'
TS = 'touchscreen'
OUT = ROOT / 'outputs/panel_ui/mt_rc1mxhfcal'
CONTRACT = rt.ASSET / 'asset.contract.json'
C0 = 273.15
WATER = dict(rt.CHARGE_EXAMPLE, substances=[dict(substance_id='water', mass_kg=1.0)])
QUIET = dict(rt.PLANT_EXAMPLE, sensor_noise_k=0.0, tr_offset_k=0.0, tj_offset_k=0.0, tc_offset_k=0.0)


class Rig:
    def __init__(self, on=True, water=True):
        self.h = rt.MtRc1mxhfcalRuntime()
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('agent2', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.B, self.E = self.h.session('agent'), self.h.session('agent2'), self.h.session('judge')
        self.n = 0
        self.host('plant.configure', **QUIET)
        if water:
            self.host('reactor.charge', **WATER)
        if on:
            assert self.press('key_power')
            self.h.step(60)

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
        return self.frame()['displays'][TS]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, sess=None, **kw):
        return self.call(sess or self.A, 'panel.touch', display_id=TS, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid, sess=None):
        x, y, ww, hh = self.widgets()[wid]['rect']
        return self.touch(x + ww // 2, y + hh // 2, sess)

    def press(self, key, sess=None):
        return self.call(sess or self.A, 'panel.press', input_id=key, duration_s=0.1)['accepted']

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label, sess=None):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label and b['id'] != 'field':
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2, sess)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def ov_row(self, label):
        for b in self.overlay()['buttons']:
            if b.get('style') == 'row' and b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(label)

    def msg(self):
        return ' '.join(self.overlay()['lines'])

    def form_row(self, wid, label):
        """Tap a row of a form widget (in a dialog: the overlay's row buttons)."""
        ov = self.overlay()
        if ov is not None and ov['kind'] == 'dialog':
            return self.ov_row(label)
        w = self.widgets()[wid]
        labels = [c[0] for c in w['cells']]
        k = labels.index(label)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def type(self, text):
        ov = self.overlay()
        labels = {b['label'] for b in ov['buttons']}
        if 'q' not in labels and 'Q' not in labels and '7' in labels:
            for ch in text:
                assert self.ov(ch), ch
            return
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit() or ch == '_':
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if ch.isupper() != (self.overlay()['mode'] == 'upper'):
                    self.ov('Shift')
                self.ov(ch)

    def enter(self, value):
        """Keypad / keyboard open: type the value and OK."""
        self.type(str(value))
        return self.ov('OK')

    def set_field(self, label, value):
        """In an open dialog form: tap the row, then type (keypad) or pick (choice list)."""
        self.ov_row(label)
        if self.overlay()['kind'] == 'keyboard':
            assert self.enter(value), self.overlay()
        else:
            assert self.ov(value), (label, value)

    def task(self, cell, **fields):
        """Main screen: tap a value field, enter the values, Start."""
        self.tap(cell)
        for label, value in fields.items():
            self.set_field(label, value)
        return self.ov('Start')

    def start_experiment(self, name):
        self.tap('home_title')
        self.set_field('Experiment name', name)
        return self.ov('Start')

    def safety(self, button, value):
        self.tap('a_reactor')
        self.tap('rs_safety')
        self.tap(button)
        ok = self.enter(value)
        return ok

    def back_home(self):
        while self.page()[1] != 'home':
            assert self.overlay() is None, self.overlay()
            w = self.widgets()
            bid = next(k for k in w if k.endswith('_back'))
            self.tap(bid)


class SurveyProcedures(unittest.TestCase):
    def test_turn_on_the_device(self):
        r = Rig(on=False)
        self.assertEqual(r.page(), ('off', 'dark'))
        self.assertFalse(r.frame()['displays']['status_ok']['on'])
        self.assertFalse(r.tap('dark'))                                     # dark screen: touches do nothing
        self.assertTrue(r.press('key_power'))
        self.assertTrue(r.val('power.enabled'))
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertTrue(r.frame()['displays']['status_ok']['on'])
        r.h.step(60)
        self.assertIn('°C', r.text('c_tr'))
        self.assertEqual(r.text('home_clock'), '1/5/2026 8:01 AM')
        self.assertTrue(r.press('key_power'))
        self.assertEqual((r.page(), r.val('power.enabled')), (('off', 'dark'), False))

    def test_select_the_reactor_type(self):
        r = Rig()
        r.tap('a_reactor')
        self.assertEqual(r.page()[1], 'reactor')
        self.assertIn('AP01-2', r.text('rs_type'))
        r.tap('rs_type')
        self.assertEqual(r.page()[1], 'reactor_type')
        self.assertTrue(r.tap('rt_ap012'))
        self.assertEqual(r.page()[1], 'reactor')
        r.tap('rs_type')
        r.tap('rt_all')
        self.assertIn('Only the AP01-2 reactor', r.msg())
        self.assertIn('AP01-2', r.frame()['ui']['unsupported'])
        r.ov('OK')
        r.tap('rt_cancel')
        self.assertEqual(r.page()[1], 'reactor')

    def test_set_the_safety_temperature(self):
        r = Rig()
        before = r.h.read('evaluator')
        self.assertTrue(r.safety('sf_tsafe_b', 30))
        self.assertAlmostEqual(r.val('safety.tsafe_k'), 303.15)
        self.assertIn('30.0 °C', r.text('sf_tsafe_b'))
        after = r.h.read('evaluator')
        for k in ('safety.tr_min_k', 'safety.tr_max_k', 'safety.tj_min_k', 'safety.tj_max_k', 'safety.rmax_rpm', 'safety.rsafe_rpm', 'safety.rmin_rpm',
                  'safety.tc_max_k'):
            self.assertEqual(after[k], before[k], k)

    def test_start_an_experiment(self):
        r = Rig()
        self.assertEqual(r.text('home_title'), 'New experiment')
        self.assertTrue(r.start_experiment('hydrolysis_1'))
        self.assertTrue(r.val('experiment.running'))
        self.assertEqual(r.text('home_title'), 'hydrolysis_1')
        self.assertEqual(r.text('home_run'), 'Stop')
        r.h.step(65)
        self.assertEqual(r.text('home_sb_timer'), 'Exp.\n0:01:05')

    def test_change_tj(self):
        r = Rig()
        self.assertTrue(r.task('c_tj', **{'End temperature Tj': 40}))
        self.assertAlmostEqual(r.val('thermostat.tj_setpoint_k'), 313.15)
        self.assertIsNone(r.overlay())
        self.assertTrue(r.task('c_tj', **{'End temperature Tj': 50, 'Ramp time': 10}))
        self.assertTrue(r.val('thermostat.ramp_active'))
        self.assertIn('End = 50.0 °C   0:10:00', r.text('c_tj'))
        r.h.step(600)
        self.assertIn('Set = 50.0 °C', r.text('c_tj'))
        self.assertAlmostEqual(r.val('thermostat.tj_setpoint_k'), 323.15)

    def test_change_tr(self):
        r = Rig()
        self.assertTrue(r.task('c_tr', **{'End temperature Tr': 35, 'Control parameters': 'Organic (P)'}))
        self.assertEqual(r.val('thermostat.mode'), 'tr')
        self.assertAlmostEqual(r.val('thermostat.tr_setpoint_k'), 308.15)
        self.assertEqual(r.h.panel_read()['host.tr_controller'], 'organic')
        self.assertEqual(r.widgets()['c_tr']['fill'], 'active_cell')
        r.h.step(1800)
        self.assertAlmostEqual(r.val('reactor.indicated_k'), 308.15, delta=1.0)

    def test_change_stirrer_speed(self):
        r = Rig()
        self.assertTrue(r.task('c_r', **{'Stirrer speed R': 250}))
        self.assertEqual(r.val('stirrer.setpoint_rpm'), 250)
        r.h.step(10)
        self.assertIn('250 rpm', r.text('c_r'))
        self.assertEqual(r.text('home_sb_stirrer'), 'Stirrer\non')
        self.assertTrue(r.press('key_stirrer'))                             # stirrer on/off button
        self.assertEqual(r.val('stirrer.setpoint_rpm'), 0.0)
        self.assertTrue(r.press('key_stirrer'))
        self.assertEqual(r.val('stirrer.setpoint_rpm'), 250)

    def test_end_an_experiment(self):
        r = Rig()
        r.task('c_r', **{'Stirrer speed R': 200})
        r.task('c_tj', **{'End temperature Tj': 45})
        r.start_experiment('run_a')
        r.h.step(120)
        r.tap('home_run')
        self.assertEqual(r.page()[1], 'stop')
        self.assertEqual(r.widgets()['f_stop_rows']['cells'][0], ['Name', 'run_a'])
        self.assertEqual(r.widgets()['f_stop_rows']['cells'][1][1], 'Tj = T safe and R = off')
        self.assertEqual(r.widgets()['f_stop_rows']['cells'][2][1], 'Valid')
        self.assertTrue(r.tap('stop_ok'))
        self.assertEqual(r.page()[1], 'home')
        self.assertFalse(r.val('experiment.running'))
        self.assertAlmostEqual(r.val('thermostat.tj_setpoint_k'), r.val('safety.tsafe_k'))
        self.assertEqual(r.val('stirrer.setpoint_rpm'), 0.0)
        r.start_experiment('run_b')
        r.tap('home_title')
        r.form_row('f_stop_rows', 'End parameters')
        r.ov('Keep current Tj / Tr and R')
        r.tap('stop_ok')
        self.assertFalse(r.val('experiment.running'))
        self.assertAlmostEqual(r.val('thermostat.tj_setpoint_k'), r.val('safety.tsafe_k'))
        rows = r.h.panel_read()['host.experiments']
        self.assertEqual([x['name'] for x in rows], ['run_b', 'run_a'])


class Warning(unittest.TestCase):
    def test_manipulation_warning_acknowledge_and_decline(self):
        r = Rig(on=False)
        r.host('manipulation.flag', active=True)
        r.press('key_power')
        self.assertEqual(r.page(), ('warning', 'warning'))
        self.assertTrue(r.val('touchscreen.warning'))
        self.assertTrue(r.tap('mw_decline'))
        self.assertEqual(r.page(), ('main', 'home'))
        r.press('key_power')
        r.press('key_power')
        self.assertEqual(r.page()[0], 'warning')                            # declined: shown again at the next power-up
        self.assertTrue(r.tap('mw_ack'))
        self.assertEqual(r.page(), ('main', 'home'))
        r.press('key_power')
        r.press('key_power')
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertFalse(r.val('touchscreen.warning'))


class SafetyAndRefusals(unittest.TestCase):
    def test_all_safety_values(self):
        r = Rig()
        for button, value, slot, want in (('sf_tr_max_b', 90, 'safety.tr_max_k', 363.15), ('sf_tr_min_b', 0, 'safety.tr_min_k', 273.15),
                                          ('sf_tj_max_b', 120, 'safety.tj_max_k', 393.15), ('sf_tj_min_b', -5.5, 'safety.tj_min_k', 267.65),
                                          ('sf_tdiff_b', 30, 'safety.tdiff_max_k', 30.0), ('sf_rmax_b', 600, 'safety.rmax_rpm', 600)):
            self.assertTrue(r.safety(button, value), button)
            self.assertAlmostEqual(r.val(slot), want, msg=button)
            r.back_home()
        r.tap('a_reactor')
        r.tap('rs_safety')
        r.tap('sf_rsafe_b')
        r.set_field('R safe', 'User-defined rpm')
        r.set_field('User-defined speed', 100)
        self.assertTrue(r.ov('OK'))
        self.assertEqual(r.val('safety.rsafe_rpm'), 100)
        self.assertIn('100 rpm', r.text('sf_rsafe_b'))
        r.tap('sf_rsafe_b')
        r.set_field('R safe', 'Hold rpm')
        r.ov('OK')
        self.assertIsNone(r.val('safety.rsafe_rpm'))
        self.assertIn('Hold rpm', r.text('sf_rsafe_b'))

    def test_refusals_leave_the_state(self):
        r = Rig()
        before = r.h.read('evaluator')
        r.tap('a_reactor')
        r.tap('rs_safety')
        r.tap('sf_tj_max_b')
        r.type('20')                                                        # Tj is ~25 °C now
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.msg(), 'The current Tr or Tj is already outside the new limit.')
        r.ov('OK')
        r.tap('sf_tsafe_b')
        r.type('999')
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.overlay()['error'], f'{rt.TR_RANGE_K[0] - C0:.1f} ... {rt.TR_RANGE_K[1] - C0:.1f} °C')
        r.ov('Cancel')
        r.back_home()
        self.assertFalse(r.task('c_tj', **{'End temperature Tj': 160}))     # above Tj max 150 °C
        self.assertEqual(r.msg(), 'The value is outside the safety limits (Reactor > Safety).')
        r.ov('OK')
        r.ov('Cancel')
        self.assertFalse(r.task('c_r', **{'Stirrer speed R': 10}))
        self.assertEqual(r.msg(), 'The stirrer speed must be 0 or between 30 rpm and R max.')
        r.ov('OK')
        r.ov('Cancel')
        self.assertFalse(r.press('key_stirrer'))                            # no speed set yet
        self.assertEqual(r.msg(), 'No stirrer speed set yet: enter R on the touchscreen first.')
        r.ov('OK')
        r.tap('a_notes')
        r.tap('nt_add')
        self.assertEqual(r.msg(), 'No experiment running: start an experiment first.')
        r.ov('OK')
        after = r.h.read('evaluator')
        for s in (before, after):
            for k in [k for k in s if k.startswith(('truth.', 'reactor.', 'jacket.', 'cooling_oil.', 'stirrer.indicated', 'stirrer.angle',
                                                   'calorimetry.'))]:
                s.pop(k)
        self.assertEqual(before, after)

    def test_tr_without_mass_and_unsupported_cells(self):
        r = Rig(water=False)
        self.assertFalse(r.task('c_tr', **{'End temperature Tr': 30}))
        self.assertEqual(r.msg(), 'Tr control needs at least 0.5 L in the reactor (Tr sensor immersed).')
        r.ov('OK')
        r.ov('Cancel')
        for cell, text in (('c_diff', 'no reflux condenser'), ('c_sample', 'No EasySampler'), ('a_seq', 'iControl')):
            r.tap(cell)
            self.assertIn(text, r.msg())
            self.assertIsNotNone(r.frame()['ui']['unsupported'])
            r.ov('OK')


class HardwareFaults(unittest.TestCase):
    """Sweep 2026-10-19 (1.2.0): RC1e table 5.6 error causes behind a hardware fault, by evaluator fault.inject / fault.clear."""
    def test_oil_level_high_runs_program_b2(self):
        r = Rig()
        r.task('c_r', **{'Stirrer speed R': 200})
        r.task('c_tj', **{'End temperature Tj': 45})
        r.h.step(300)
        tj0 = r.val('jacket.indicated_k')
        r.host('fault.inject', fault='oil_level_high')
        r.h.step(4)
        self.assertEqual((r.val('emergency.program'), r.val('emergency.cause')), ('B2', 'OIL_LEVEL_HIGH'))
        self.assertEqual(r.val('thermostat.mode'), 'tj')
        r.tap('c_emerg')
        self.assertIn('Oil level too high', r.msg())                            # the manual wording on the touchscreen
        r.ov('Reset')
        self.assertTrue(r.val('emergency.active'))                              # the cause is still there: no reset
        r.ov('OK')
        r.h.step(120)
        self.assertLess(r.val('jacket.indicated_k'), tj0 - 5.0)                  # cooling towards Tr min + 3 K at 0.1 K/s
        r.host('fault.clear', fault='oil_level_high')                           # oil level normal again: a further 2 K, held
        target = r.h.tj_task['end']
        self.assertAlmostEqual(target, r.h.meas['tj'] - 2.0, places=6)
        r.tap('c_emerg')
        r.ov('Reset')
        self.assertFalse(r.val('emergency.active'))

    def test_hardware_faults_select_their_program(self):
        for fault, prog in (('oil_level_low', 'A'), ('pc_connection', 'C'), ('tj_sensor', 'B1'), ('heater_relay', 'A')):
            r = Rig()
            r.host('fault.inject', fault=fault)
            r.h.step(4)
            self.assertEqual((r.val('emergency.program'), r.val('emergency.cause')), (prog, rt.RC1E_FAULTS[fault][1]), fault)
            r.host('fault.clear', fault=fault)
            r.tap('c_emerg')
            r.ov('Reset')
            self.assertFalse(r.val('emergency.active'), fault)
        r = Rig()
        r.host('fault.inject', fault='tj_sensor')
        r.host('fault.inject', fault='tjs_sensor')                              # both sensors defective: A (more than one error)
        r.h.step(4)
        self.assertEqual(r.val('emergency.program'), 'A')
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', fault='oil_level_low')['error']['code'], 'ACCESS_DENIED')


class EmergencyPrograms(unittest.TestCase):
    def test_safeguard_button_and_reset(self):
        r = Rig()
        r.task('c_r', **{'Stirrer speed R': 200})
        self.assertTrue(r.press('key_emergency'))
        self.assertEqual((r.val('emergency.program'), r.val('emergency.cause')), ('A', 'EMERGENCY_BUTTON'))
        f = r.frame()
        self.assertTrue(f['displays']['status_alarm']['on'])
        self.assertFalse(f['displays']['status_ok']['on'])
        self.assertEqual(r.text('c_emerg'), 'Emergency program A\nTap to reset')
        self.assertEqual(r.text('home_sb_reactor'), 'EMERG.\nA')
        self.assertFalse(r.task('c_tj', **{'End temperature Tj': 30}))
        self.assertEqual(r.msg(), 'An emergency program is active: no changes can be made until it is reset.')
        r.ov('OK')
        r.ov('Cancel')
        r.tap('c_emerg')
        self.assertIn('EMERGENCY_BUTTON', r.msg())
        self.assertTrue(r.ov('Reset'))
        self.assertFalse(r.val('emergency.active'))
        self.assertTrue(r.frame()['displays']['status_ok']['on'])
        self.assertEqual(r.text('c_emerg'), '\n')

    def test_tr_max_program_a_cause_persists(self):
        r = Rig()
        r.task('c_r', **{'Stirrer speed R': 250})
        r.safety('sf_tr_max_b', 30)
        r.back_home()
        r.task('c_tj', **{'End temperature Tj': 60})
        t = 0
        while not r.val('emergency.active') and t < 3600:
            r.h.step(20)
            t += 20
        self.assertEqual((r.val('emergency.program'), r.val('emergency.cause')), ('A', 'TR_ABOVE_MAX'))
        r.tap('c_emerg')
        self.assertFalse(r.ov('Reset'))
        self.assertEqual(r.msg(), 'The cause of the emergency program is still present: it cannot be reset yet.')
        r.ov('OK')
        while r.val('reactor.indicated_k') > 302.0 and t < 7200:
            r.h.step(20)
            t += 20
        r.tap('c_emerg')
        self.assertTrue(r.ov('Reset'))
        self.assertFalse(r.val('emergency.active'))

    def test_stirrer_off_for_three_minutes_triggers_b1(self):
        """Time-out: with R min > 0 a stirrer switched off with its button starts program B1 after 3 min (RC1e 5.6)."""
        r = Rig()
        r.task('c_r', **{'Stirrer speed R': 200})
        r.call(r.A, 'safety.configure', **dict(rt.SAFETY_EXAMPLE, rmin_rpm=50.0))
        r.h.step(20)
        r.press('key_stirrer')
        r.h.step(170)
        self.assertFalse(r.val('emergency.active'))
        r.tap('home_info')
        self.assertEqual(r.widgets()['in_list']['cells'][0][2], 'Stirrer off (R min > 0)')
        r.h.step(20)
        self.assertEqual((r.val('emergency.program'), r.val('emergency.cause')), ('B1', 'STIRRER_OFF_3MIN'))
        self.assertEqual(r.text('in_state'), 'Emergency program B1: STIRRER_OFF_3MIN')
        self.assertEqual(r.widgets()['in_list']['cells'][0][2], 'Emergency program B1')


class OtherPages(unittest.TestCase):
    def test_notes_graph_experiments_info_tools(self):
        r = Rig()
        r.start_experiment('run_n')
        r.task('c_tj', **{'End temperature Tj': 40})
        r.h.step(300)
        r.tap('a_notes')
        self.assertEqual(r.text('nt_exp'), 'Experiment: run_n')
        r.tap('nt_add')
        r.set_field('Note', 'heating started')
        r.ov('Save')
        self.assertEqual(r.widgets()['nt_list']['cells'][0][1], 'heating started')
        self.assertEqual(r.h.experiment['notes'][0]['text'], 'heating started')
        r.back_home()
        r.tap('a_graph')
        plot = r.widgets()['gr_plot']
        self.assertGreater(len(r.h.panel_read()['host.trend']['tr']), 100)
        self.assertEqual(plot['type'], 'plot')
        r.back_home()
        r.tap('a_exp')
        self.assertEqual(r.widgets()['ex_list']['cells'][0][0], 'run_n')
        r.form_row('ex_list', 'run_n')
        r.ov('Export')
        self.assertEqual(r.msg(), 'No USB storage device connected to the RC1mx.')
        self.assertIn('USB', r.frame()['ui']['unsupported'])
        r.ov('OK')
        r.back_home()
        r.tap('home_info')
        codes = [c[2] for c in r.widgets()['in_list']['cells']]
        self.assertIn('Tj task', codes)
        self.assertIn('Note added', codes)
        r.back_home()
        r.tap('home_tools')
        self.assertIn('MidTemp', r.text('tl_3'))
        r.tap('tl_service')
        self.assertIn('service engineer', r.msg())

    def test_dose_and_stop(self):
        r = Rig()
        r.task('c_r', **{'Stirrer speed R': 200})
        r.tap('c_dose')
        self.assertEqual(r.page()[1], 'dose')
        for label, value in (('Substance', 'acetic_acid'), ('Rate', 6), ('Amount', 30)):
            r.form_row('f_dose_rows', label)
            r.enter(value)
        self.assertTrue(r.tap('ds_start'))
        self.assertEqual(r.page()[1], 'home')
        self.assertTrue(r.val('dosing.active'))
        self.assertAlmostEqual(r.val('dosing.rate_kg_s'), 1e-4)
        r.h.step(60)
        self.assertIn('/ 30.0 g acetic_acid', r.text('c_dose'))
        self.assertEqual(r.text('home_sb_dosing'), 'Dosing')
        r.tap('c_dose')
        self.assertTrue(r.tap('ds_stop'))
        self.assertFalse(r.val('dosing.active'))
        self.assertIn('Last dose', r.text('ds_state'))
        r.form_row('f_dose_rows', 'Substance')
        r.enter('nothing')
        self.assertFalse(r.tap('ds_start'))
        self.assertEqual(r.msg(), 'Unknown substance (not in the substance library).')


class Access(unittest.TestCase):
    def test_second_agent_cannot_stop_the_experiment(self):
        r = Rig()
        r.start_experiment('mine')
        eid = r.val('experiment.id')
        r.tap('home_run', sess=r.B)
        r.tap('stop_ok', sess=r.B)
        self.assertEqual(r.msg(), 'This experiment or operation was started by another user.')
        self.assertTrue(r.val('experiment.running'))
        self.assertEqual(r.call(r.B, 'experiment.data', expect='rejected', experiment_id=eid)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call(r.A, 'experiment.data', experiment_id=eid)['name'], 'mine')
        r.ov('OK', sess=r.B)
        self.assertTrue(r.press('key_emergency', sess=r.B))                 # anyone can press the safeguard button
        self.assertTrue(r.val('emergency.active'))

    def test_panel_dose_belongs_to_the_caller(self):
        """Sync audit rule 2: a dose started on the touchscreen belongs to the calling session (poll / cancel); the other agent can neither poll
        it nor stop it on the screen (state unchanged)."""
        r = Rig()
        r.task('c_r', **{'Stirrer speed R': 200})
        r.tap('c_dose')
        for label, value in (('Substance', 'acetic_acid'), ('Rate', 6), ('Amount', 30)):
            r.form_row('f_dose_rows', label)
            r.enter(value)
        self.assertTrue(r.tap('ds_start'))
        op = r.h.dosing['op']['operation_id']
        self.assertEqual(r.h._owners.get(op), 'agent')
        self.assertEqual(r.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            r.B.poll(op)
        r.h.step(10)
        before = r.h.read('evaluator')
        r.tap('c_dose', sess=r.B)
        r.tap('ds_stop', sess=r.B)
        self.assertEqual(r.msg(), 'This experiment or operation was started by another user.')
        self.assertEqual(r.call(r.B, 'dosing.stop', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.h.read('evaluator'), before)
        r.ov('OK', sess=r.B)
        r.A.cancel(op, 'c1')
        self.assertFalse(r.val('dosing.active'))

    def test_power_button_is_a_hard_switch(self):
        """Sweep 2026-10-19: the ON/OFF power button of the electronic cabinet cuts the power; another agent may switch off during an
        experiment it did not start (the reaction runs on without cooling)."""
        r = Rig()
        r.start_experiment('mine')
        self.assertTrue(r.press('key_power', sess=r.B))
        self.assertFalse(r.val('power.enabled'))
        self.assertIsNone(r.overlay())

    def test_power_off_gate(self):
        r = Rig()
        r.press('key_power')
        self.assertFalse(r.press('key_emergency'))
        self.assertFalse(r.press('key_stirrer'))
        self.assertFalse(r.val('emergency.active'))
        self.assertFalse(r.call(r.A, 'panel.touch', display_id=TS, x=400, y=300)['accepted'])


class PanelEqualsDirect(unittest.TestCase):
    def test_tasks_equal_commands(self):
        a, b = Rig(), Rig()
        a.safety('sf_tsafe_b', 30)
        a.back_home()
        a.start_experiment('eq')
        a.task('c_r', **{'Stirrer speed R': 220})
        a.task('c_tr', **{'End temperature Tr': 38, 'Ramp time': 5})
        a.press('key_stirrer')
        b.call(b.A, 'safety.configure', **dict(rt.SAFETY_EXAMPLE, tsafe_k=303.15))
        b.call(b.A, 'experiment.start', name='eq')
        b.call(b.A, 'stirrer.set', rpm=220.0)
        b.call(b.A, 'thermostat.set_tr', end_k=311.15, ramp_s=300.0, controller='aqueous')
        b.call(b.A, 'stirrer.toggle')
        for r in (a, b):
            r.h.step(900)
        a.tap('home_run')
        a.tap('stop_ok')
        b.call(b.A, 'experiment.end', end_mode='cool_to', end_temperature_k=303.15)
        b.call(b.A, 'stirrer.set', rpm=0.0)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))
        self.assertEqual(a.h.records, b.h.records)

    def test_keys_equal_commands(self):
        a, b = Rig(on=False), Rig(on=False)
        a.press('key_power')
        b.call(b.A, 'power.toggle')
        a.press('key_emergency')
        b.call(b.A, 'emergency.trigger')
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))


class Rendering(unittest.TestCase):
    def test_render_determinism_and_evidence(self):
        a, b = Rig(), Rig()
        for r in (a, b):
            r.task('c_r', **{'Stirrer speed R': 200})
            r.start_experiment('demo')
            r.task('c_tr', **{'End temperature Tr': 40, 'Ramp time': 20})
            r.h.step(300)
        self.assertEqual(a.call(a.A, 'panel.capture', display_id=TS)['image']['sha256'], b.call(b.A, 'panel.capture', display_id=TS)['image']['sha256'])
        cap = a.call(a.A, 'panel.capture', display_id='status_ok')
        self.assertEqual(validate_value(cap, rt.CAPS['panel.capture']['output_schema']), [])
        a.h.panel.save_png(TS, OUT / 'home.png')
        a.tap('a_reactor')
        a.tap('rs_safety')
        a.h.panel.save_png(TS, OUT / 'menu.png')
        a.back_home()
        a.tap('a_graph')
        a.h.panel.save_png(TS, OUT / 'graph.png')
        a.back_home()
        a.task('c_tj', **{'End temperature Tj': 200})
        a.h.panel.save_png(TS, OUT / 'error.png')
        a.ov('OK')
        a.ov('Cancel')
        a.press('key_emergency')
        a.tap('c_emerg')
        a.h.panel.save_png(TS, OUT / 'emergency.png')
        (OUT / 'renders.json').write_text(json.dumps({k: str(OUT.relative_to(ROOT) / f'{k}.png') for k in ('home', 'menu', 'graph', 'error', 'emergency')},
                                                     indent=1) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_has_panel_layer(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + ['experiment.note', 'warning.respond', 'manipulation.flag']:
            self.assertEqual(c['capabilities'][k]['input_schema'], rt.CAPS[k]['input_schema'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        self.assertIn('touchscreen.warning', c['state_slots'])

    def test_slot_values_match_schemas(self):
        r = Rig()
        r.start_experiment('s')
        r.h.step(100)
        for k, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, rt.SLOTS[k]['schema']), [], k)


if __name__ == '__main__':
    unittest.main()
