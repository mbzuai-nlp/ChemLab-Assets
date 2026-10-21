"""Front-panel tests for CHEM-059 linari_rtadvanced (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/linari_rtadvanced.py):
python3 -m pytest tests/test_panel_ui_linari_rtadvanced.py -q

Every gesture goes through the session API (panel.touch / panel.press / panel.switch); widgets are found by id or text in the structured frame
(panel.frame, evaluator) and tapped at their centre.  The spec is approximate (no public manual): the tests cover the functions the product page
lists (process parameters, climate, run start / stop, needle cleaning, named users, recipes, audit trail, telemetry, alarms, emergency stop),
panel = direct capability, refusals shown as the tablet's dialog, no time-out, render determinism and the contract entries."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import linari_rtadvanced as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'rtadvanced_01'
TAB = 'tablet'
OUT = ROOT / 'outputs/panel_ui/linari_rtadvanced'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['switch on and log in', 'set the process parameters', 'set the climate targets', 'start and stop a run', 'clean the needles',
                'save and load a recipe', 'manage users', 'read the audit trail', 'read the run telemetry', 'emergency stop and reset']
DONE_TASKS = set()


class Rig:
    """A runtime with the host configuration done by the evaluator (plant, collector, one loaded syringe, exhaust on, door closed) and an
    agent that only uses panel gestures."""

    def __init__(self, login=True, door_closed=True):
        self.h = rt.LinariRtadvancedRuntime()
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        self.call(self.E, 'collector.configure', **rt.COLLECTOR_EXAMPLE)
        self.call(self.E, 'solution.load', **rt.SOLUTION_EXAMPLE)
        self.call(self.E, 'vent.set', enabled=True)
        if door_closed:
            self.call(self.E, 'door.set', opened=False)
        if login:
            assert self.switch('on')
            assert self.row('lg_list', 'Administrator')

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap                                          # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        self.h.step(dt)

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][TAB]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TAB, x=int(x), y=int(y), **kw)['accepted']

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
            if b['label'] == label and b['id'] != 'field':                    # not the keyboard's input field
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def ov_row(self, field_label):
        """Tap a form row inside the top dialog."""
        for b in self.overlay()['buttons']:
            if b.get('style') == 'row' and b['label'] == field_label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(field_label)

    def row(self, wid, first):
        """Tap the row of a list or form whose first cell is this text (dragging the rows until it shows)."""
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

    def cells(self, wid):
        return [c for c in self.widgets()[wid]['cells']]

    def value(self, wid, first):
        """Value cell of a form row (scrolling the form to the top first, then down until the row shows)."""
        x, y, ww, hh = self.widgets()[wid]['rect']
        for _ in range(20):
            if self.widgets()[wid]['scroll'] == 0:
                break
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
        for _ in range(20):
            w = self.widgets()[wid]
            for c in w['cells']:
                if c[0] == first:
                    return c[1]
            assert w['scroll'] + len(w['cells']) < w['count'], first
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def type(self, text):
        """Type on the open keyboard (the shown text is selected and replaced by the first key)."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard'
        labels = {b['label'] for b in ov['buttons']}
        if 'q' not in labels and 'Q' not in labels and '7' in labels:          # numeric keyboard
            for ch in text:
                assert self.ov(ch), ch
            return
        if ov['field']:
            self.ov('Del')
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit() or ch in '$@#%&/():;_+=\'"!?,':
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if ch.isupper() != (self.overlay()['mode'] == 'upper'):
                    self.ov('Shift')
                self.ov(ch)
        assert self.overlay()['field'] == text, (self.overlay()['field'], text)

    def enter(self, form_wid, first, text):
        self.row(form_wid, first)
        self.type(text)
        return self.ov('OK')

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='main_switch', position=pos)['accepted']

    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def png(self, display=TAB):
        r = self.call(self.A, 'panel.capture', display_id=display)
        return Path(r['image']['path']).read_bytes()


def msg(r):
    """Text of the top dialog (its lines joined; long messages are wrapped on the screen)."""
    return ' '.join(r.overlay()['lines'])


PROCESS = [('Group 1 high voltage', '15'), ('Group 2 high voltage', '12.5'), ('Pump 1 flow (group 1)', '0.2'), ('Collector speed', '600'),
           ('Group 1 needle stroke low', '50'), ('Group 1 needle stroke high', '450'), ('Group 2 needle stroke high', '450'),
           ('Group 1 needle-collector distance', '40'), ('Group 2 needle-collector distance', '40'), ('Run time', '10')]


FLOW = 0.2 * (1e-6 / 3600.0)                                                  # 0.2 mL/h as the tablet converts it
ARGS = dict(voltage_v=[15000.0, 12500.0], flows_m3_s=[FLOW] + [0.0] * 7, rpm=600, needle_low_m=[50 * 0.001, 0.0], needle_high_m=[450 * 0.001] * 2,
            gap_m=[40 * 0.001] * 2, duration_s=10)


def nav(r, target):
    """Tap a button of the top bar (Home, Process, Climate, Light, Settings) on whatever page is shown."""
    wid = next(w for w in r.widgets() if w.endswith('_hd_' + target))
    return r.tap(wid)


def set_process(r, rows=PROCESS, direction=None):
    assert nav(r, 'process')
    assert r.page() == ('main', 'process')
    for label, value in rows:
        assert r.enter('f_process_rows', label, value), label
    if direction:
        r.row('f_process_rows', 'Collector direction')
        r.ov(direction)
    return r.tap('pr_save')


def ready_to_run(r):
    """Process set, high voltage on (door closed, exhaust on)."""
    assert set_process(r)
    r.ov('OK')
    nav(r, 'home')
    assert r.tap('a_hv')
    r.step(5)


class SurveyFunctions(unittest.TestCase):
    """The tablet functions of the product page, done only with panel gestures."""

    def test_switch_on_and_log_in(self):
        r = Rig(login=False)
        self.assertEqual(r.page(), ('offline', 'offline'))
        self.assertEqual(r.text('off_1'), 'Machine not connected')
        self.assertFalse(r.touch(500, 300))                                       # nothing to touch while the machine is off
        self.assertFalse(r.frame()['displays']['power_light']['on'])
        self.assertTrue(r.switch('on'))
        self.assertTrue(r.val('power.enabled'))
        self.assertTrue(r.frame()['displays']['power_light']['on'])
        self.assertEqual(r.page(), ('login', 'login'))
        self.assertEqual(r.cells('lg_list'), [['Administrator', 'Administrator']])
        self.assertTrue(r.row('lg_list', 'Administrator'))
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertEqual(r.val('users.state')['current'], 'Administrator')
        self.assertEqual(r.disp()['title'], 'RT Advanced  [Administrator]')
        self.assertEqual(r.val('audit.trail')[-1]['event'], 'user.login')
        DONE_TASKS.add('switch on and log in')

    def test_set_the_process_parameters(self):
        r = Rig()
        self.assertEqual(r.text('h_hv1'), 'HV 0.0 kV  set --')
        self.assertTrue(set_process(r, direction='Anticlockwise'))
        self.assertEqual(msg(r), 'Process parameters saved')
        r.ov('OK')
        m = r.h.method
        self.assertEqual(m['voltage_v'], [15000.0, 12500.0])
        self.assertAlmostEqual(m['flows_m3_s'][0], 0.2e-6 / 3600, places=20)
        self.assertEqual(m['flows_m3_s'][1:], [0.0] * 7)
        self.assertEqual((m['rpm'], m['duration_s']), (-600, 10))
        self.assertEqual({k: m[k] for k in ('needle_low_m', 'needle_high_m', 'gap_m')}, {k: ARGS[k] for k in ('needle_low_m', 'needle_high_m', 'gap_m')})
        nav(r, 'home')
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertEqual((r.text('h_hv1'), r.text('h_hv2')), ('HV 0.0 kV  set 15.0 kV', 'HV 0.0 kV  set 12.5 kV'))
        self.assertEqual(r.text('h_f1'), 'P1 0.200  P2 0.000  ')
        self.assertEqual(r.text('h_rpm'), '0 rpm  set -600 rpm')
        nav(r, 'process')                                                  # the form shows the stored values again
        self.assertEqual([r.value('f_process_rows', k) for k in ('Group 1 high voltage', 'Pump 1 flow (group 1)', 'Collector speed', 'Collector direction')],
                         ['15 kV', '0.200 mL/h', '600 rpm', 'Anticlockwise'])
        DONE_TASKS.add('set the process parameters')

    def test_set_the_climate_targets(self):
        r = Rig()
        self.assertTrue(nav(r, 'climate'))
        self.assertTrue(r.enter('f_climate_rows', 'Chamber temperature', '35'))
        self.assertTrue(r.enter('f_climate_rows', 'Relative humidity', '55'))
        self.assertEqual(r.val('thermal.target_k'), 298.15)                      # written on Save only
        self.assertTrue(r.tap('cl_save'))
        r.ov('OK')
        self.assertAlmostEqual(r.val('thermal.target_k'), 308.15, places=9)
        self.assertAlmostEqual(r.val('climate.target_rh'), 0.55, places=9)
        self.assertTrue(r.tap('cl_onoff'))
        self.assertTrue(r.val('climate.enabled'))
        self.assertEqual(r.text('cl_onoff'), 'UTA ON')
        r.step(20)
        self.assertGreater(r.val('thermal.temperature_k'), 298.15)
        DONE_TASKS.add('set the climate targets')

    def test_start_and_stop_a_run(self):
        r = Rig()
        ready_to_run(r)
        self.assertTrue(r.val('hv.enabled'))
        self.assertTrue(r.tap('a_start'))
        self.assertEqual(r.val('cycle.phase'), 'running')
        r.step(3)
        self.assertEqual(r.text('h_state'), 'State: Running')
        self.assertTrue(r.text('h_hv1').startswith('HV 15.0 kV'))
        self.assertTrue(r.tap('a_stop'))
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        self.assertEqual(r.text('h_state'), 'State: Stopped')
        events = [e['event'] for e in r.val('audit.trail')]
        self.assertEqual(events[-3:], ['hv.set', 'acquisition.start', 'acquisition.stop'])
        DONE_TASKS.add('start and stop a run')

    def test_clean_the_needles(self):
        r = Rig()
        ready_to_run(r)
        r.tap('a_start')
        r.step(4)
        r.tap('a_stop')
        r.tap('a_hv')                                                              # high voltage off, let it decay
        r.step(25)
        cone = sum(r.h.cones[0])
        self.assertGreater(cone, 0)
        nav(r, 'settings')
        r.tap('s_clean')
        self.assertTrue(r.tap('cn_g1'))
        self.assertEqual(msg(r), 'Group 1 needles cleaned')
        self.assertEqual(sum(r.h.cones[0]), 0.0)
        DONE_TASKS.add('clean the needles')

    def test_save_and_load_a_recipe(self):
        r = Rig()
        set_process(r)
        r.ov('OK')
        nav(r, 'settings')
        r.tap('s_recipes')
        self.assertEqual(r.widgets()['rc_list'].get('empty_text'), 'No recipes saved')
        self.assertTrue(r.tap('rc_new'))
        self.assertEqual(r.overlay()['title'], 'Save current process as recipe')
        self.assertFalse(r.ov('Save'))                                            # the name is required
        self.assertEqual(msg(r), 'Enter a recipe name')
        r.ov('OK')
        r.ov_row('Recipe name')
        r.type('PCL 10pct')
        r.ov('OK')
        self.assertTrue(r.ov('Save'))
        self.assertEqual([x['name'] for x in r.val('recipes.list')], ['PCL 10pct'])
        self.assertEqual(r.cells('rc_list'), [['PCL 10pct', 'Administrator', '25.0 °C']])
        saved = copy.deepcopy(r.h.method)
        r.call(r.A, 'method.configure', **dict(rt.METHOD_EXAMPLE, rpm=100.0))   # another process meanwhile
        r.row('rc_list', 'PCL 10pct')
        self.assertTrue(r.ov('Load'))
        self.assertEqual(r.h.method, saved)
        r.row('rc_list', 'PCL 10pct')
        self.assertTrue(r.ov('Delete'))
        self.assertEqual(r.val('recipes.list'), [])
        DONE_TASKS.add('save and load a recipe')

    def test_manage_users(self):
        r = Rig()
        nav(r, 'settings')
        r.tap('s_users')
        r.tap('us_add')
        r.ov_row('User name')
        r.type('Ann')
        r.ov('OK')
        r.ov_row('Role')
        r.ov('Operator')
        self.assertTrue(r.ov('Save'))
        self.assertEqual(r.val('users.state')['accounts'], [{'name': 'Administrator', 'role': 'Administrator'}, {'name': 'Ann', 'role': 'Operator'}])
        r.row('us_list', 'Ann')
        self.assertTrue(r.ov('Log in'))                                           # switch user
        self.assertEqual(r.val('users.state')['current'], 'Ann')
        self.assertNotIn('us_add', r.widgets())                                   # an Operator cannot manage users
        r.row('us_list', 'Administrator')
        self.assertNotIn('Delete', [b['label'] for b in r.overlay()['buttons']])
        r.ov('Cancel')
        self.assertTrue(r.tap('us_logout'))
        self.assertEqual(r.page(), ('login', 'login'))
        r.row('lg_list', 'Administrator')
        nav(r, 'settings')
        r.tap('s_users')
        r.row('us_list', 'Administrator')
        self.assertFalse(r.ov('Delete'))                                          # not the logged-in user
        self.assertEqual(msg(r), 'You cannot delete the user who is logged in')
        r.ov('OK')
        r.row('us_list', 'Ann')
        self.assertTrue(r.ov('Delete'))
        self.assertEqual([u['name'] for u in r.val('users.state')['accounts']], ['Administrator'])
        DONE_TASKS.add('manage users')

    def test_read_the_audit_trail(self):
        r = Rig()
        set_process(r)
        r.ov('OK')
        nav(r, 'settings')
        r.tap('s_audit')
        rows = r.cells('au_list')
        self.assertEqual([c[1:3] for c in rows[:2]], [['Administrator', 'method.configure'], ['Administrator', 'user.login']])
        self.assertIn('voltage_v=15000/12500', rows[0][3])
        DONE_TASKS.add('read the audit trail')

    def test_read_the_run_telemetry(self):
        r = Rig()
        ready_to_run(r)
        r.tap('a_start')
        r.step(5.5)
        nav(r, 'settings')
        r.tap('s_telemetry')
        rows = r.cells('te_list')
        self.assertEqual(len(r.val('telemetry.log')['rows']), 6)
        self.assertEqual(rows[0][0], '5')                                         # newest first (run time 0 .. 5 s)
        self.assertEqual(rows[0][1], '15.0')
        self.assertFalse(r.tap('te_export'))                                       # no storage: the device's message
        self.assertEqual(msg(r), 'No storage device or network share connected.')
        DONE_TASKS.add('read the run telemetry')

    def test_emergency_stop_and_reset(self):
        r = Rig()
        ready_to_run(r)
        r.tap('a_start')
        r.step(1)
        self.assertTrue(r.press('emergency_stop'))
        self.assertTrue(r.val('safety.latched'))
        self.assertEqual(r.val('cycle.phase'), 'failed')
        self.assertEqual(r.widgets()['a_alarm']['style'], 'danger')
        self.assertEqual(r.text('h_alarm_text'), 'ALARM: emergency stop latched - release and reset')
        r.tap('a_alarm')
        self.assertFalse(r.tap('al_reset'))                                       # high voltage still decaying
        self.assertEqual(msg(r), 'Not possible now: check main switch, exhaust, chamber door, emergency stop, collector and process')
        self.assertTrue(r.val('safety.latched'))
        r.ov('OK')
        r.step(25)
        self.assertTrue(r.tap('al_reset'))
        self.assertFalse(r.val('safety.latched'))
        self.assertEqual(r.text('al_now'), 'No active alarm')
        self.assertIn('alarm', [e['event'] for e in r.val('audit.trail')])
        DONE_TASKS.add('emergency stop and reset')


class PanelEqualsDirect(unittest.TestCase):
    """A panel action leaves the runtime in the same state as the direct capability call."""

    STRIP = ('audit.trail', 'users.state')

    def state(self, r):
        return {k: v for k, v in r.h.read('evaluator').items() if k not in self.STRIP}

    def test_process_form_equals_method_configure(self):
        a, b = Rig(), Rig()
        set_process(a)
        b.call(b.A, 'method.configure', **ARGS)
        self.assertEqual(a.h.method, b.h.method)
        self.assertEqual(self.state(a), self.state(b))

    def test_keys_equal_commands(self):
        a, b = Rig(), Rig()
        ready_to_run(a)
        b.call(b.A, 'method.configure', **ARGS)
        b.call(b.A, 'hv.set', enabled=True)
        b.step(5)
        for wid, cap, args in (('a_start', 'acquisition.start', {}), ('home_hd_light', 'lights.set', {'on': True}), ('a_uta', 'climate.set', {'enabled': True})):
            a.tap(wid)
            b.call(b.A, cap, **args)
            a.step(1)
            b.step(1)
        self.assertEqual(self.state(a), self.state(b))

    def test_switch_and_estop_equal_commands(self):
        a, b = Rig(login=False), Rig(login=False)
        a.switch('on')
        b.call(b.A, 'power.set', enabled=True)
        a.press('emergency_stop')
        b.call(b.A, 'safety.trip')
        self.assertEqual(self.state(a), self.state(b))
        a.switch('off')
        b.call(b.A, 'power.set', enabled=False)
        self.assertEqual(self.state(a), self.state(b))


class RefusalsAndLocks(unittest.TestCase):
    def test_hv_refused_with_door_open(self):
        r = Rig(door_closed=False)
        set_process(r)
        r.ov('OK')
        nav(r, 'home')
        before = r.h.debug_snapshot()
        self.assertFalse(r.tap('a_hv'))
        self.assertEqual(r.overlay()['title'], 'Not possible')
        self.assertFalse(r.val('hv.enabled'))
        after = r.h.debug_snapshot()
        for k in ('hv', 'phase', 'audit', 'method'):
            self.assertEqual(before[k], after[k])
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'PRECONDITION_FAILED')

    def test_start_refused_without_process(self):
        r = Rig()
        self.assertFalse(r.tap('a_start'))
        self.assertEqual(r.val('cycle.phase'), 'idle')
        self.assertEqual(r.overlay()['style'], 'warn')

    def test_invalid_process_and_climate(self):
        r = Rig()
        self.assertFalse(set_process(r, rows=[('Run time', '5')]))                # no pump flow
        self.assertEqual(msg(r), 'Invalid process: set at least one pump flow and stroke low <= high')
        self.assertIsNone(r.h.method)
        self.assertEqual(r.page(), ('main', 'process'))                           # the form stays open
        r.ov('OK')
        r.row('f_process_rows', 'Run time')
        r.type('99999')
        self.assertFalse(r.ov('OK'))                                              # keypad range check
        self.assertEqual(r.overlay()['error'], 'Out of range')
        r.ov('Cancel')
        r.tap('pr_cancel')
        nav(r, 'climate')
        r.enter('f_climate_rows', 'Chamber temperature', '20')                    # below the 25 degC ambient
        self.assertFalse(r.tap('cl_save'))
        self.assertEqual(msg(r), 'Temperature below ambient: the UTA controls from ambient to 50 °C')
        self.assertEqual(r.val('thermal.target_k'), 298.15)

    def test_method_change_refused_while_running(self):
        r = Rig()
        ready_to_run(r)
        r.tap('a_start')
        self.assertFalse(set_process(r, rows=[('Run time', '20')]))
        self.assertEqual(msg(r), 'Not possible while running or while high voltage / rotation is present')
        self.assertEqual(r.h.method['duration_s'], 10)

    def test_operator_cannot_add_users_directly(self):
        r = Rig()
        r.call(r.A, 'user.add', name='Bo', role='Operator')
        r.call(r.A, 'user.login', name='Bo')
        res = r.call(r.A, 'user.add', expect='rejected', name='Cy', role='Operator')
        self.assertEqual(res['error']['code'], 'PERMISSION_DENIED')

    def test_offline_and_light(self):
        r = Rig()
        self.assertTrue(nav(r, 'light'))
        self.assertTrue(r.val('lights.on'))
        r.switch('off')
        self.assertFalse(r.val('lights.on'))                                      # switching off also switches the light off
        self.assertEqual(r.page(), ('offline', 'offline'))
        self.assertFalse(r.touch(100, 100))
        res = r.call(r.A, 'lights.set', expect='rejected', on=True)
        self.assertEqual(res['error']['code'], 'PRECONDITION_FAILED')
        r.switch('on')
        self.assertEqual(r.page(), ('main', 'home'))                              # still logged in

    def test_unsupported_items_show_the_device_response(self):
        r = Rig()
        nav(r, 'settings')
        for wid, title in (('s_blow', 'Option not installed'), ('s_thick', 'Option not installed')):
            self.assertFalse(r.tap(wid))
            self.assertEqual(r.overlay()['title'], title)
            self.assertTrue(r.frame()['ui']['unsupported'])
            r.ov('OK')
        r.tap('s_alarms')
        self.assertFalse(r.tap('al_mail'))
        self.assertEqual(msg(r), 'No mail server connected.')

    def test_bad_gestures(self):
        r = Rig()
        for args in (dict(display_id='power_light', x=1, y=1), dict(display_id=TAB, x=2000, y=1), dict(display_id=TAB, x=5, y=5, to_x=7)):
            res = r.call(r.A, 'panel.touch', expect='rejected', **args)
            self.assertEqual(res['error']['code'], 'INVALID_ARGUMENT')
        res = r.call(r.A, 'panel.frame', expect='rejected')
        self.assertEqual(res['error']['code'], 'ACCESS_DENIED')


class CallerRules(unittest.TestCase):
    """Sync audit rule 2 (1.1.1): a job started from the tablet belongs to the calling session (poll / cancel); another agent can neither
    stop it nor switch the high voltage or the climate off, on the tablet or directly (state unchanged); the emergency stop, the main switch
    (a hard switch, 1.1.2) and the evaluator stay unrestricted."""

    def started(self):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        r.B = r.h.session('agent_b')
        ready_to_run(r)
        self.assertTrue(r.tap('a_start'))
        op = r.val('telemetry.log')['operation_id']
        self.assertEqual(r.h._owners.get(op), 'agent')
        return r, op

    def tap_b(self, r, wid):
        x, y, w, h = r.widgets()[wid]['rect']
        return r.call(r.B, 'panel.touch', display_id=TAB, x=x + w // 2, y=y + h // 2)['accepted']

    def test_owner_polls_and_cancels_a_tablet_started_job(self):
        r, op = self.started()
        self.assertEqual(r.A.poll(op)['status'], 'running')
        r.step(2)
        self.assertEqual(r.A.cancel(op, 'c1')['result'], {'phase': 'cancelled'})
        self.assertEqual(r.val('cycle.phase'), 'cancelled')

    def test_other_agent_cannot_stop_or_change_the_job(self):
        r, op = self.started()
        r.step(1)
        before = r.h.read('evaluator')
        with self.assertRaises(rt.ActionRejected):
            r.B.poll(op)
        self.tap_b(r, 'a_stop')
        self.assertEqual(msg(r), 'This run was started by another user: it cannot be stopped or changed from here')
        r.ov('OK')
        for cap, args in (('acquisition.stop', {}), ('hv.set', {'enabled': False}),
                          ('climate.set_targets', {'temperature_k': 303.15, 'humidity': 0.5})):
            self.assertEqual(r.call(r.B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h.read('evaluator'), before)                          # nothing changed
        self.assertEqual(r.val('cycle.phase'), 'running')
        self.assertTrue(r.tap('a_stop'))                                          # the owner stops it on the tablet
        self.assertEqual(r.val('cycle.phase'), 'cancelled')

    def test_main_switch_is_a_hard_switch(self):
        """Sweep 2026-10-19: the main switch on top of the machine cuts the power; another agent's switch-off is not refused and stops the job."""
        for how in ('switch', 'power.set', 'power.toggle'):
            r, op = self.started()
            r.step(1)
            if how == 'switch':
                self.assertTrue(r.call(r.B, 'panel.switch', input_id='main_switch', position='off')['accepted'])
            else:
                r.call(r.B, how, **({'enabled': False} if how == 'power.set' else {}))
            self.assertFalse(r.val('power.enabled'), how)
            self.assertNotEqual(r.val('cycle.phase'), 'running', how)
            self.assertFalse(r.val('hv.enabled'), how)

    def test_emergency_stop_and_evaluator_stay_open(self):
        r, op = self.started()
        r.call(r.E, 'acquisition.stop')
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        r2, _ = self.started()
        self.assertTrue(r2.call(r2.B, 'panel.press', input_id='emergency_stop')['accepted'])
        self.assertTrue(r2.val('safety.latched'))
        self.assertEqual(r2.val('cycle.phase'), 'failed')


class TimeoutsAndRendering(unittest.TestCase):
    def test_no_timeout(self):
        r = Rig()
        nav(r, 'settings')
        r.tap('s_audit')
        r.step(900)
        self.assertEqual(r.page(), ('main', 'audit'))                             # no documented menu time-out

    def test_render_determinism_and_capture(self):
        a, b = Rig(), Rig()
        for r in (a, b):
            set_process(r)
        self.assertEqual(a.png(), b.png())
        self.assertEqual(a.h.panel.render(TAB).shape, (600, 1024, 3))
        cap = a.call(a.A, 'panel.capture', display_id='power_light')
        self.assertEqual((cap['image']['width'], cap['image']['height']), (24, 24))
        self.assertEqual(validate_value(cap, rt.CAPS['panel.capture'][1]), [])

    def test_evidence_renders(self):
        r = Rig()
        ready_to_run(r)
        r.tap('a_start')
        r.step(3)
        r.h.panel.save_png(TAB, OUT / 'home.png')
        nav(r, 'process')
        r.h.panel.save_png(TAB, OUT / 'menu.png')
        r.tap('pr_cancel')
        r.press('emergency_stop')
        r.tap('a_alarm')
        self.assertFalse(r.tap('al_reset'))                                       # still decaying: the refusal dialog
        r.h.panel.save_png(TAB, OUT / 'error.png')
        r.ov('OK')
        r.tap('al_back')
        nav(r, 'settings')
        r.h.panel.save_png(TAB, OUT / 'settings.png')
        (OUT / 'renders.json').write_text(json.dumps({k: str(OUT.relative_to(ROOT) / f'{k}.png') for k in ('home', 'menu', 'error', 'settings')}, indent=1) + '\n')
        for k in ('home', 'menu', 'error', 'settings'):
            self.assertTrue((OUT / f'{k}.png').is_file())


class ContractEntries(unittest.TestCase):
    def test_contract_has_panel_layer(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['approximate']), ('panel_ui', 'touchscreen_gui', True))
        for k in list(rt.PANEL_CAPS) + ['lights.set', 'recipe.save', 'recipe.load', 'recipe.delete', 'user.login', 'user.logout', 'user.add', 'user.remove']:
            self.assertIn(k, c['capabilities'])
            self.assertEqual(c['capabilities'][k]['input_schema'], rt.CAPS[k][0])
        for s in ('lights.on', 'users.state', 'recipes.list', 'audit.trail', 'telemetry.log'):
            self.assertIn(s, c['state_slots'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])

    def test_new_command_outputs_match_schemas(self):
        r = Rig()
        set_process(r)
        for cap, args in (('lights.set', {'on': True}), ('recipe.save', {'name': 'a'}), ('recipe.load', {'name': 'a'}), ('recipe.delete', {'name': 'a'}),
                          ('user.add', {'name': 'b', 'role': 'Operator'}), ('user.remove', {'name': 'b'}), ('user.logout', {}),
                          ('user.login', {'name': 'Administrator'})):
            out = r.call(r.A, cap, **args)
            self.assertEqual(validate_value(out, rt.CAPS[cap][1]), [], cap)
        for k, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, rt.SLOTS[k][0]), [], k)


if __name__ == '__main__':
    unittest.main()
