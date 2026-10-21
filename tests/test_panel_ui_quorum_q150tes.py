"""front-panel tests for CHEM-062 quorum_q150tes (panel_ui.json + scripts/asset_runtime/_panel_ui.py + the 1.1.0 touch terminal of the runtime):
python3 -m pytest tests/test_panel_ui_quorum_q150tes.py -q

Every gesture goes through the session API (panel.touch / panel.switch, the runtime's normal invoke path); widgets are found by id or text in
the structured frame (panel.frame, evaluator) and tapped at their centre; overlay keys and buttons by their label.  Screen checks read the
frame and the pixels (a widget's pixels equal the widget drawn alone from the spec colours and the frame).  Themes: the seven key procedures
of the menu survey; profile, material, system and user editors; Run Profile mapped onto the coating model (vacuum settings, flush, pump
hold, clean current, automatic vent), the vent and vacuum shutdown profiles, inserts not fitted, run errors and reminders; the 1.1.1 fixes
(ramped-carbon and pulse-rod current profiles, rotary-only pumping, material density in the FTM, material type -> clean property, run
ownership on the panel and direct paths); panel = direct capability; refusals (privileges, ranges, names); no time-outs; render determinism;
evidence PNGs; contract entries."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import quorum_q150tes as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'q150tes_01'
OUT = ROOT / 'outputs/panel_ui/quorum_q150tes'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['run a default sputter profile', 'cancel a run', 'edit sputter time of the active profile', 'create a new profile',
                'turn stage rotation off for one profile', 'change to Administration level', 'check FTM crystal life']
DONE_TASKS = set()
TERM_SEEN = set()                                                            # terminal commands that ran (through the panel) without refusal
TOUCH = 'touch'
D = rt.PANEL_SPEC['displays'][TOUCH]


def _rgb(role):
    return tuple(D['colors'].get(role, pu.ROLE_COLORS.get(role, (0, 0, 0))))


def _contains(a, b):
    return a[0] <= b[0] and a[1] <= b[1] and a[0] + a[2] >= b[0] + b[2] and a[1] + a[3] >= b[1] + b[3]


def drawn_alone(w, widgets):
    """The widget drawn alone (over the widgets before it that contain it) from the spec colours and the frame entry."""
    from PIL import Image, ImageDraw
    img = Image.new('RGB', tuple(D['size']), _rgb('background'))
    dr = ImageDraw.Draw(img)
    s = D.get('text_scale', 2)
    for x in [u for u in widgets[:widgets.index(w)] if _contains(u['rect'], w['rect'])] + [w]:
        x0, y0, ww, hh = x['rect']
        if pu._draw_touch_widget(dr, x, s, D['colors'], _rgb('text'), _rgb('background'), _rgb('accent'), _rgb('panel')):
            continue
        assert x['type'] == 'button', x
        dr.rectangle([x0, y0, x0 + ww - 1, y0 + hh - 1], fill=_rgb('panel'), outline=_rgb('text'), width=1)
        pu._text(dr, x0 + (ww - pu.text_width(x['text'], s)) // 2, y0 + (hh - 7 * s) // 2, x['text'], s, _rgb('text'))
    x0, y0, ww, hh = w['rect']
    return np.asarray(img)[y0:y0 + hh, x0:x0 + ww]


class Rig:
    def __init__(self, source=None, closed=True, power=True):
        self.h = rt.QuorumQ150tesRuntime(INST)
        orig = self.h.invoke

        def recorded(cap, **a):
            out = orig(cap, **a)
            TERM_SEEN.add(cap)
            return out
        self.h.invoke = recorded
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.PLANT_EXAMPLE)
        self.call(self.E, 'source.install', **(source or rt.SOURCE_EXAMPLE))
        self.call(self.E, 'samples.load', **rt.SAMPLES_EXAMPLE)
        if closed:
            self.call(self.A, 'chamber.set', opened=False)
        if power:
            assert self.switch('on')
            self.step(6)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap                                           # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def step(self, s):
        t = 0.0
        while t < s - 1e-9:
            d = min(600.0, s - t)
            self.h.step(d)
            t += d

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    # screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][TOUCH]

    def screen(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def shows(self, wid, text=None):
        ws = self.disp()['widgets']
        w = next(x for x in ws if x['id'] == wid)
        if text is not None:
            assert w['text'] == text, (wid, w['text'], text)
        img = self.h.panel.render(TOUCH)
        x0, y0, ww, hh = w['rect']
        assert np.array_equal(img[y0:y0 + hh, x0:x0 + ww], drawn_alone(w, ws)), wid
        return True

    def overlay(self):
        return self.disp().get('overlay')

    # gestures
    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='mains_switch', position=pos)['accepted']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TOUCH, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def ov_row(self, label):
        """Tap a form row of the open dialog by its label."""
        for b in self.overlay()['buttons']:
            if b['id'].startswith('row:') and b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(label)

    def row(self, wid, first):
        """Tap the row of a list or form widget whose first cell is this text (dragging the rows until it shows)."""
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
        return {c[0]: (c[1] if len(c) > 1 else None) for c in self.widgets()[wid]['cells']}

    def all_cells(self, wid):
        """Every row of a form widget (scrolling down from the top)."""
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        for _ in range(10):
            if self.widgets()[wid]['scroll'] == 0:
                break
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
        out = {}
        for _ in range(10):
            out.update(self.cells(wid))
            w = self.widgets()[wid]
            if w['scroll'] + len(w['cells']) >= w['count']:
                break
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        return out

    def type(self, text):
        """Type on the open keyboard (the shown text is replaced)."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard'
        labels = {b['label'] for b in ov['buttons']}
        numeric = '7' in labels and 'q' not in labels and 'Q' not in labels
        if ov['field']:
            self.ov('Del')
            while self.overlay()['field']:
                self.ov('Del')
        for ch in text:
            if numeric:
                assert self.ov(ch), ch
                continue
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

    def enter(self, wid, first, text):
        self.row(wid, first)
        self.type(text)
        return self.ov('OK')

    def choose(self, wid, first, option):
        self.row(wid, first)
        return self.ov(option)

    def menu(self, top, item):
        assert self.tap({'Edit': 'mb_edit', 'Service': 'mb_service', 'Help': 'mb_help'}[top])
        return self.ov(item)

    def select_profile(self, name):
        self.row('f_select_row', 'Profile')
        return self.ov(name)

    def profile(self, name):
        return next(p for p in self.val('terminal.profiles') if p['name'] == name)

    def admin(self):
        self.menu('Edit', 'User > Level')
        self.ov_row('User level')
        self.ov('Administration')
        self.ov_row('Access code')
        self.type('admin')
        self.ov('OK')
        assert self.ov('OK')
        assert self.val('terminal.user_level') == 'Administration'

    def run_to_end(self, limit=1800.0):
        t = 0.0
        while self.screen()[0] == 'run' and t < limit:
            self.step(5.0)
            t += 5.0
        return t


class SurveyProcedures(unittest.TestCase):
    """The seven key procedures of the menu survey, done only with panel gestures (the host loads the sample, the target and closes the lid)."""

    def test_run_a_default_sputter_profile(self):
        r = Rig(power=False)
        self.assertEqual(r.screen()[0], 'dark')
        self.assertTrue(r.switch('on'))
        self.assertEqual(r.screen()[0], 'init')
        r.shows('i_text', 'Initialising...')
        r.step(6)
        self.assertEqual(r.screen(), ('gui', 'standby'))
        self.assertTrue(r.select_profile('QT Time Chromium'))
        self.assertEqual(r.val('terminal.active_profile'), 'QT Time Chromium')
        r.shows('f_select_row')
        self.assertTrue(r.tap('run_profile'))
        self.assertEqual(r.screen(), ('run', 'running'))
        r.shows('r_title', 'Running:- QT Time Chromium')
        self.assertEqual(r.text('r_stage'), 'Pumping')
        r.step(200)
        self.assertEqual(r.val('cycle.stage'), 'deposition')
        r.shows('r_stage', 'Coating Sample')
        r.shows('r_req', '20')                                                   # profile sputter current 20 mA
        self.assertEqual(r.text('r_meas'), '20')
        self.assertTrue(r.widgets()['r_th']['text'])                              # FTM fitted: thickness control box
        r.step(130)
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        self.assertAlmostEqual(r.val('cycle.deposition_s'), 120.0)             # profile sputter time
        self.assertTrue(r.val('terminal.run')['venting'])
        self.assertIn('v_abort', r.widgets())
        r.run_to_end()
        self.assertEqual(r.screen(), ('gui', 'standby'))
        self.assertGreater(r.val('vacuum.pressure_pa'), 0.999 * rt.PLANT_EXAMPLE['ambient_pa'])
        self.assertFalse(r.val('vacuum.venting'))
        self.assertGreater(sum(r.call(r.E, 'product.read')['lots'][0]['sample_kg']), 0.0)
        DONE_TASKS.add('run a default sputter profile')

    def test_cancel_a_run(self):
        r = Rig()
        r.tap('run_profile')
        r.step(200)
        self.assertEqual(r.val('cycle.stage'), 'deposition')
        self.assertTrue(r.tap('r_cancel'))
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        self.assertEqual(r.val('source.current_a'), 0.0)
        self.assertEqual(r.screen()[0], 'run')                                    # the chamber vents
        r.run_to_end()
        self.assertEqual(r.screen(), ('gui', 'standby'))
        DONE_TASKS.add('cancel a run')

    def test_edit_sputter_time_of_the_active_profile(self):
        r = Rig()
        self.assertTrue(r.tap('edit_profile'))
        self.assertEqual(r.screen(), ('gui', 'pf_timed_sputter'))
        self.assertEqual(r.cells('pf_timed_sputter_rows')['Sputter Time (seconds)'], '120')
        r.row('pf_timed_sputter_rows', 'Sputter Time (seconds)')
        self.assertEqual(r.overlay()['title'], 'Sputter Time (seconds)  Min 1 Max 3600')   # the dialog shows the allowed values
        r.type('4000')
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.overlay()['error'], 'Out of range')
        r.type('60')
        self.assertTrue(r.ov('OK'))
        self.assertEqual(r.profile('QT Time Chromium')['parameters']['sputter_time'], 120.0)   # not saved before OK on the profile page
        self.assertTrue(r.tap('pf_timed_sputter_ok'))
        self.assertEqual(r.profile('QT Time Chromium')['parameters']['sputter_time'], 60.0)
        self.assertEqual(r.screen(), ('gui', 'standby'))
        r.tap('edit_profile')                                                     # Cancel restores the previous value
        r.enter('pf_timed_sputter_rows', 'Sputter Time (seconds)', '90')
        r.tap('pf_timed_sputter_cancel')
        self.assertEqual(r.profile('QT Time Chromium')['parameters']['sputter_time'], 60.0)
        r.tap('run_profile')
        r.step(400)
        self.assertAlmostEqual(r.val('cycle.deposition_s'), 60.0)
        DONE_TASKS.add('edit sputter time of the active profile')

    def test_create_a_new_profile(self):
        r = Rig()
        r.menu('Edit', 'Profiles')
        self.assertEqual(r.screen(), ('gui', 'profiles'))
        self.assertTrue(r.tap('pl_new'))
        self.assertEqual(r.overlay()['title'], 'New Profile')
        r.ov_row('Select Required Process')
        r.ov('Ramped carbon')
        r.ov_row('Name')
        r.type('My carbon coat')
        r.ov('OK')
        self.assertTrue(r.ov('OK'))
        p = r.profile('My carbon coat')
        self.assertEqual((p['process'], p['parameters']['evaporation_current'], p['parameters']['current_ramp_rate']), ('ramped_carbon', 46.0, 0.2))
        self.assertEqual(r.all_cells('pl')['My carbon coat'], 'Ramped carbon')
        self.assertTrue(r.tap('pl_edit'))
        self.assertEqual(r.screen(), ('gui', 'pf_ramped_carbon'))
        r.enter('pf_ramped_carbon_rows', 'Evaporation Current (A)', '40')
        r.tap('pf_ramped_carbon_ok')
        self.assertEqual(r.profile('My carbon coat')['parameters']['evaporation_current'], 40.0)
        # name rules (table 21)
        r.tap('pl_new')
        r.ov_row('Name')
        r.type('A name longer than nineteen')
        r.ov('OK')
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.overlay()['lines'], ['Name is too long'])
        r.ov('OK')
        r.ov_row('Name')
        r.type('My carbon coat')
        r.ov('OK')
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.overlay()['lines'], ['Name already exists'])
        r.ov('OK')
        r.ov('Cancel')
        r.row('pl', 'My carbon coat')
        self.assertTrue(r.tap('pl_delete'))
        self.assertNotIn('My carbon coat', r.all_cells('pl'))
        self.assertNotIn('My carbon coat', [p['name'] for p in r.val('terminal.profiles')])
        DONE_TASKS.add('create a new profile')

    def test_turn_stage_rotation_off_for_one_profile(self):
        r = Rig()
        r.admin()
        r.menu('Edit', 'Profiles')
        r.row('pl', 'QT Time Chromium')
        self.assertTrue(r.tap('pl_system'))
        self.assertEqual(r.overlay()['lines'], ['Change in this Profile Only'])
        self.assertTrue(r.ov('Yes'))
        self.assertEqual(r.screen(), ('gui', 'override'))
        self.assertEqual(r.cells('ov_rows')['Stage Rotate'], 'Yes')
        r.choose('ov_rows', 'Stage Rotate', 'No')
        r.tap('ov_ok')
        self.assertEqual(r.profile('QT Time Chromium')['system'], {'gas_bleed_time': 15.0, 'bleed_vacuum': 0.01, 'clean_current': 150.0, 'clean_time': 30.0,
                                                                   'stage_rotate': False, 'stage_rotate_speed': 12.0})
        self.assertTrue(r.val('terminal.system')['hardware']['stage_rotate'])      # the system setting is unchanged
        r.tap('pl_ok')
        r.tap('run_profile')
        r.step(400)
        self.assertFalse(r.val('rotation.enabled'))
        self.assertEqual(r.val('stage.angle_rad'), 0.0)
        r.run_to_end()
        r.select_profile('QT Chromium 5nm')                                      # other profiles still rotate
        r.tap('run_profile')
        self.assertTrue(r.val('rotation.enabled'))
        DONE_TASKS.add('turn stage rotation off for one profile')

    def test_change_to_administration_level(self):
        r = Rig()
        self.assertEqual(r.val('terminal.user_level'), 'User')                     # the terminal starts in User mode
        r.tap('edit_profile')
        rows = r.widgets()['pf_timed_sputter_rows']
        self.assertIn(rows['row_ids'].index('clean_target'), rows['disabled_rows'])  # grey: needs Administration
        self.assertFalse(r.row('pf_timed_sputter_rows', 'Clean target'))
        r.tap('pf_timed_sputter_cancel')
        r.menu('Edit', 'User > Level')
        r.ov_row('User level')
        r.ov('Administration')
        r.ov_row('Access code')
        r.type('wrong')
        r.ov('OK')
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.overlay()['lines'], ['Access code incorrect'])
        r.ov('OK')
        r.ov_row('Access code')
        r.type('admin')
        r.ov('OK')
        self.assertTrue(r.ov('OK'))
        self.assertEqual(r.val('terminal.user_level'), 'Administration')
        r.shows('s_user', 'User level: Administration')
        r.tap('edit_profile')
        self.assertTrue(r.choose('pf_timed_sputter_rows', 'Clean target', 'Yes'))
        r.tap('pf_timed_sputter_ok')
        self.assertTrue(r.profile('QT Time Chromium')['parameters']['clean_target'])
        r.switch('off')
        r.switch('on')
        r.step(6)
        self.assertEqual(r.val('terminal.user_level'), 'User')                     # back to User after a power cycle
        DONE_TASKS.add('change to Administration level')

    def test_check_ftm_crystal_life(self):
        r = Rig()
        r.menu('Service', 'FTM')
        self.assertEqual(r.overlay()['lines'], ['FTM channel 1', 'Crystal OK', 'Crystal % used 0'])
        r.ov('OK')
        r.h.lots[-1]['crystal_kg'] = 0.5 * rt.FTM_LIFE_KG_M2 * rt.PLANT_EXAMPLE['qcm_area_m2']
        r.menu('Service', 'FTM')
        self.assertEqual(r.overlay()['lines'], ['FTM channel 1', 'Crystal OK', 'Crystal % used 50'])
        r.ov('OK')
        r.h.lots[-1]['crystal_kg'] = 1.01 * rt.FTM_LIFE_KG_M2 * rt.PLANT_EXAMPLE['qcm_area_m2']
        r.menu('Service', 'FTM')
        self.assertEqual(r.overlay()['lines'][1], 'Crystal fail')
        r.ov('OK')
        r.select_profile('QT Chromium 5nm')
        self.assertFalse(r.tap('run_profile'))
        self.assertEqual(r.overlay()['lines'], ['FTM crystal not oscillating. Please check or replace'])
        DONE_TASKS.add('check FTM crystal life')


class Editors(unittest.TestCase):
    def test_materials_editor_and_use_materials_defaults(self):
        r = Rig()
        r.menu('Edit', 'Materials')
        self.assertEqual(r.all_cells('ml')['Gold'], '19.32 g/cm3')
        r.tap('ml_new')
        r.ov_row('Name')
        r.type('Tin')
        r.ov('OK')
        self.assertTrue(r.ov('OK'))
        self.assertTrue(r.tap('ml_edit'))
        self.assertEqual(r.screen(), ('gui', 'material'))
        r.enter('mat_rows', 'Density (g/cm3)', '7.3')
        r.enter('mat_rows', 'Sputter Current (mA)', '35')
        r.choose('mat_rows', 'Material Type', 'noble')
        self.assertFalse(r.widgets()['mat_rows']['disabled_rows'] == [])         # cleaning mode / stop voltage are admin items
        r.tap('mat_ok')
        mat = next(m for m in r.val('terminal.materials') if m['name'] == 'Tin')['parameters']
        self.assertEqual((mat['density'], mat['sputter_current'], mat['material_type']), (7.3, 35.0, 'noble'))
        r.tap('ml_ok')
        r.tap('edit_profile')
        r.choose('pf_timed_sputter_rows', 'Material', 'Tin')
        self.assertEqual(r.overlay()['lines'], ['Use Materials Defaults'])
        r.ov('Yes')
        self.assertEqual(r.all_cells('pf_timed_sputter_rows')['Sputter Current (mA)'], '35')
        r.tap('pf_timed_sputter_ok')
        p = r.profile('QT Time Chromium')['parameters']
        self.assertEqual((p['material'], p['sputter_current']), ('Tin', 35.0))
        r.menu('Edit', 'Materials')
        r.row('ml', 'Tin')
        r.tap('ml_delete')
        r.tap('ml_ok')
        self.assertFalse(r.tap('run_profile'))
        self.assertEqual(r.overlay()['lines'], ['Selected material not found in material list cannot continue with coating'])

    def test_system_editor_categories_and_privileges(self):
        r = Rig()
        r.menu('Edit', 'System')
        self.assertEqual(list(r.cells('sl')), ['Network', 'Generic Sputter', 'Vacuum', 'Maintenance', 'Hardware'])
        r.row('sl', 'Network')
        r.tap('sl_edit')
        self.assertEqual(r.screen(), ('gui', 'sys_network'))
        rows = r.widgets()['sys_network_rows']
        self.assertIn(rows['row_ids'].index('ip_address'), rows['disabled_rows'])   # manual IP only without DHCP
        r.choose('sys_network_rows', 'Automatic IP (DHCP)', 'No')
        r.enter('sys_network_rows', 'IP address', '10.1.1.92')
        r.tap('sys_network_ok')
        self.assertEqual((r.val('terminal.system')['network']['dhcp'], r.val('terminal.system')['network']['ip_address']), (False, '10.1.1.92'))
        r.row('sl', 'Vacuum')
        r.tap('sl_edit')
        self.assertFalse(r.row('sys_vacuum_rows', 'Vent Time (Seconds)'))          # admin item: grey for User
        r.tap('sys_vacuum_cancel')
        r.tap('sl_ok')
        r.admin()
        r.menu('Edit', 'System')
        r.row('sl', 'Vacuum')
        r.tap('sl_edit')
        r.enter('sys_vacuum_rows', 'Extra Pump time', '30')
        r.row('sys_vacuum_rows', 'Operational Vacuum (mBar)')
        self.assertEqual(r.overlay()['field'], '1.0e-03')
        r.type('5E-3')
        r.ov('OK')
        rows = r.widgets()['sys_vacuum_rows']
        cells = r.all_cells('sys_vacuum_rows')
        self.assertEqual(cells['Operational Vacuum (mBar)'], '5.0e-03')
        self.assertFalse(r.row('sys_vacuum_rows', 'Max Pumpdown Time'))         # engineer level: grey for everybody
        r.tap('sys_vacuum_ok')
        v = r.val('terminal.system')['vacuum']
        self.assertEqual((v['extra_pump_time'], v['operational_vacuum']), (30.0, 0.005))
        r.row('sl', 'Hardware')
        r.tap('sl_edit')
        r.enter('sys_hardware_rows', 'Available Targets', '2')
        self.assertIn('Target B', r.all_cells('sys_hardware_rows'))
        r.choose('sys_hardware_rows', 'Target B', 'Gold')
        r.tap('sys_hardware_ok')
        hw = r.val('terminal.system')['hardware']
        self.assertEqual((hw['available_targets'], hw['target_b'], hw['target_c']), (2, 'Gold', ''))
        r.tap('sl_ok')
        r.tap('run_profile')                                                      # the vacuum settings drive the run
        t = 0.0
        while r.val('cycle.stage') in ('pumpdown', 'extra_pump') and t < 300:
            r.step(1.0)
            t += 1.0
        self.assertEqual(r.val('cycle.stage'), 'stabilizing')
        self.assertLess(t, 100.0)

    def test_maintenance_cleaning_reminder(self):
        r = Rig()
        r.admin()
        r.call(r.A, 'system.configure', category='maintenance', parameters={'cleaning_interval': 0.1})
        r.h.sputter_s = 0.1 * 3600
        r.step(1)
        self.assertEqual(r.screen(), ('reminder', 'reminder'))
        r.shows('m_title', 'Cleaning reminder')
        r.tap('m_ok')
        self.assertEqual(r.screen(), ('gui', 'standby'))
        r.step(15 * 60 - 5)
        self.assertEqual(r.screen()[0], 'gui')
        r.step(10)
        self.assertEqual(r.screen()[0], 'reminder')                             # repeats after the sleep time (15 min)
        r.tap('m_ok')
        r.call(r.A, 'chamber.set', opened=True)
        r.switch('off')
        r.call(r.E, 'samples.collect', recipient_id='holder')
        r.call(r.E, 'service.clean', accept_host_cleaning=True)
        r.h.sputter_s = 0.0
        self.assertEqual(r.val('terminal.reminder'), '')

    def test_user_groups(self):
        r = Rig()
        r.menu('Edit', 'User > Groups')
        self.assertEqual(r.overlay()['lines'], ['You need to be logged in at', 'Administrator level'])
        r.ov('OK')
        r.admin()
        r.menu('Edit', 'User > Groups')
        self.assertEqual(r.screen(), ('gui', 'groups'))
        r.tap('gl_new')
        r.ov_row('Group Name')
        r.type('Lab A')
        r.ov('OK')
        r.ov_row('Access code')
        r.type('lab1')
        r.ov('OK')
        self.assertTrue(r.ov('OK'))
        self.assertEqual(r.val('terminal.groups'), [{'name': 'Lab A', 'level': 'User'}])
        r.tap('gl_new')
        r.ov_row('Group Name')
        r.type('Lab A')
        r.ov('OK')
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.overlay()['lines'], ['Invalid group name'])
        r.ov('OK')
        r.ov('Cancel')
        r.row('gl', 'Lab A')
        r.tap('gl_edit')
        r.ov_row('Level')
        r.ov('Administration')
        r.ov('OK')
        self.assertEqual(r.val('terminal.groups'), [{'name': 'Lab A', 'level': 'Administration'}])
        r.tap('gl_ok')
        r.menu('Edit', 'User > Level')
        r.ov_row('User level')
        r.ov('Lab A')
        r.ov_row('Access code')
        r.type('lab1')
        r.ov('OK')
        r.ov('OK')
        self.assertEqual(r.val('terminal.user_level'), 'Lab A')
        r.menu('Edit', 'User > Groups')
        r.row('gl', 'Lab A')
        r.tap('gl_delete')
        self.assertEqual(r.val('terminal.groups'), [])
        self.assertEqual(r.val('terminal.user_level'), 'User')
        for i in range(rt.MAX_GROUPS):                                            # at most 20 groups
            r.h.groups.append(dict(name=f'g{i}', level='User', access_code=''))
        r.h.user_level = 'Administration'
        r.call(r.A, 'user.group_save', expect='rejected', name='one more', level='User', access_code='', original_name='')

    def test_set_time_about_information_and_service_items(self):
        r = Rig()
        r.menu('Service', 'Set Time')
        r.ov_row('Date')
        ov = r.overlay()
        self.assertEqual(ov['kind'], 'spinner')
        inc_day = next(b for b in ov['buttons'] if b['id'] == 'inc:0')
        x, y, w, h = inc_day['rect']
        for _ in range(17):
            r.touch(x + w // 2, y + h // 2)
        r.ov('OK')
        r.ov_row('Time')
        ov = r.overlay()
        inc_h = next(b for b in ov['buttons'] if b['id'] == 'inc:0')
        x, y, w, h = inc_h['rect']
        for _ in range(10):
            r.touch(x + w // 2, y + h // 2)
        r.ov('OK')
        self.assertTrue(r.ov('OK'))
        self.assertTrue(r.val('terminal.clock').startswith('2026-01-18T10:00'))
        r.shows('s_clock', '18 Jan 2026 ' + r.val('terminal.clock')[11:19])
        r.menu('Help', 'About')
        self.assertEqual(r.overlay()['lines'], ['Q150T ES Plus', 'Software version 1.00'])
        r.ov('OK')
        r.menu('Help', 'Information')
        self.assertEqual(r.overlay()['lines'], ['Q150T ES Plus sputter / carbon coater', 'Target A: Chromium', 'FTM fitted'])
        r.ov('OK')
        self.assertFalse(r.menu('Service', 'System'))                            # engineer only: message, not accepted
        self.assertEqual(r.overlay()['lines'], ['Engineer use only'])
        self.assertEqual(r.frame()['ui']['unsupported'], 'engineer use only')
        r.ov('OK')
        self.assertFalse(r.menu('Service', 'Connect Network'))
        self.assertEqual(r.overlay()['lines'], ['Network not connected'])


class ProfileRuns(unittest.TestCase):
    def test_ramped_carbon_profile_and_inserts(self):
        r = Rig()
        r.menu('Edit', 'Profiles')
        for name, msg in (('QT Pulse cord evap', 'Carbon String insert not found'), ('QT Controlled pulse', 'Carbon String insert not found'),
                          ('QT Metal evaporation', 'Evaporation insert not found'), ('QT Aperture cleaning', 'Evaporation insert not found'),
                          ('QT Pulse rod evap', 'Carbon Rod insert not found')):           # pulse rod runs on the carbon rod insert (1.1.1)
            r.tap('pl_ok')
            r.select_profile(name)
            self.assertFalse(r.tap('run_profile'))
            self.assertEqual(r.overlay()['lines'], [msg], name)
            r.ov('OK')
            r.menu('Edit', 'Profiles')
        r.tap('pl_ok')
        r.call(r.A, 'profile.create', name='Carbon', process='ramped_carbon')
        r.select_profile('Carbon')
        self.assertFalse(r.tap('run_profile'))                                   # sputter head fitted
        self.assertEqual(r.overlay()['lines'], ['Carbon Rod insert not found'])
        r.ov('OK')
        c = Rig(source=rt.CARBON_EXAMPLE)
        c.call(c.A, 'profile.create', name='Carbon', process='ramped_carbon')
        c.select_profile('Carbon')
        self.assertTrue(c.tap('run_profile'))
        c.step(220)
        self.assertEqual(c.val('cycle.stage'), 'deposition')
        c.shows('r_cur_l', 'Current (A)')
        c.tap('r_up')
        self.assertEqual(c.val('terminal.run')['requested_current_a'], 47.0)
        c.run_to_end(2000)
        self.assertEqual(c.val('cycle.phase'), 'succeeded')
        self.assertAlmostEqual(c.val('cycle.deposition_s'), 102.0)              # 12 s Pre Evap ramp (20 -> 32 A at 1 A/s), then evaporation time 1.5 min (table 27)

    def test_current_arrows_flush_pump_hold_and_clean_current(self):
        r = Rig()
        r.admin()
        r.tap('edit_profile')
        r.choose('pf_timed_sputter_rows', 'Clean target', 'Yes')
        r.choose('pf_timed_sputter_rows', 'Flush chamber', 'Yes')
        r.tap('pf_timed_sputter_ok')
        r.tap('run_profile')
        self.assertEqual(r.val('cycle.stage'), 'flush_pump')
        r.shows('r_stage', 'Flush pumping')
        t = 0.0
        while r.val('cycle.stage') == 'flush_pump' and t < 120:
            r.step(0.5)
            t += 0.5
        self.assertEqual(r.val('cycle.stage'), 'flush_vent')
        self.assertTrue(r.val('vacuum.venting'))
        r.step(75)
        self.assertEqual(r.val('cycle.stage'), 'pumpdown')
        while r.val('cycle.stage') != 'preclean' and t < 2000:
            r.step(1.0)
            t += 1.0
        r.step(0.5)
        self.assertAlmostEqual(r.val('source.current_a'), 0.150)                # target cleaned at the clean current
        r.shows('r_stage', 'Cleaning target')
        while r.val('cycle.stage') != 'deposition':
            r.step(1.0)
        r.step(0.5)
        self.assertAlmostEqual(r.val('source.current_a'), 0.020)
        self.assertTrue(r.tap('r_up'))
        self.assertTrue(r.tap('r_up'))
        r.step(0.5)
        self.assertAlmostEqual(r.val('source.current_a'), 0.022)
        r.shows('r_req', '22')
        r.tap('r_cancel')
        r.run_to_end()
        c = Rig(source=rt.CARBON_EXAMPLE)                                        # pump hold (ramped carbon)
        c.admin()
        c.call(c.A, 'profile.create', name='Hold', process='ramped_carbon')
        c.call(c.A, 'profile.edit', name='Hold', parameters={'pump_hold': True}, system={})
        c.select_profile('Hold')
        c.tap('run_profile')
        c.step(300)
        self.assertEqual(c.val('cycle.stage'), 'pump_hold')
        c.shows('h_text', 'Ready to outgas: press Continue')
        c.step(60)
        self.assertEqual(c.val('cycle.stage'), 'pump_hold')
        self.assertTrue(c.tap('h_continue'))
        self.assertEqual(c.val('cycle.stage'), 'preclean')

    def test_vent_and_vacuum_shutdown_profiles_and_abort(self):
        r = Rig()
        r.select_profile('QT Vacuum shutdown')
        r.tap('run_profile')
        self.assertEqual(r.text('r_stage'), 'Vacuum shutdown')
        r.step(60)
        self.assertLessEqual(r.val('vacuum.pressure_pa'), 100.0)
        self.assertFalse(r.val('vacuum.pump_enabled'))
        self.assertEqual(r.screen(), ('gui', 'standby'))
        r.select_profile('QT Vent chamber')
        r.tap('run_profile')
        self.assertTrue(r.val('terminal.run')['venting'])
        r.step(1)
        self.assertTrue(r.tap('v_abort'))
        self.assertFalse(r.val('vacuum.venting'))
        self.assertEqual(r.screen(), ('gui', 'standby'))
        r.tap('run_profile')
        r.run_to_end()
        self.assertGreater(r.val('vacuum.pressure_pa'), 0.999 * rt.PLANT_EXAMPLE['ambient_pa'])

    def test_run_errors(self):
        r = Rig(closed=False)
        self.assertFalse(r.tap('run_profile'))
        self.assertEqual(r.overlay()['title'], 'Warning')
        self.assertEqual(r.overlay()['lines'], ['Please close top cover. Press OK when done'])
        r.ov('OK')
        r.call(r.A, 'chamber.set', opened=False)
        r.tap('run_profile')
        r.step(200)
        r.h.opened = True                                                         # the top cover opened during the cycle (host)
        r.step(1)
        self.assertEqual(r.val('safety.warning'), 'PROCESS_INTERLOCK')
        self.assertEqual(r.screen(), ('run_error', 'error'))
        r.shows('e_text', 'Interlock has been activated process stopped')
        self.assertTrue(r.tap('e_ok'))
        self.assertEqual(r.screen(), ('gui', 'standby'))


class ProfileRunModels(unittest.TestCase):
    """1.1.1 fixes (sync audit 2026-10-19): the documented ramped-carbon and pulse-rod current profiles, rotary-only pumping without the turbo,
    the material density in the FTM and the material type copied as the clean property."""

    @staticmethod
    def _currents(r):
        """Rod current by deposition time (shutter open) until the run ends, and the outgas current."""
        while r.val('cycle.stage') != 'preclean':
            r.step(0.5)
        r.step(0.5)
        outgas = r.val('source.current_a')
        seen = {}
        while r.val('cycle.phase') == 'running':
            d = round(r.h.deposit_s, 2)
            r.step(0.02)                                                         # one integration step: the current set at deposition time d
            if r.val('cycle.stage') == 'deposition':
                seen.setdefault(d, r.val('source.current_a'))
        return outgas, seen

    def test_ramped_carbon_follows_the_four_phases(self):
        c = Rig(source=rt.CARBON_EXAMPLE)
        c.admin()
        c.call(c.A, 'profile.create', name='Ramp', process='ramped_carbon')
        c.select_profile('Ramp')
        c.tap('edit_profile')
        for label, value in (('Outgas Current (A)', '25'), ('Pre Evap Rate (A/S)', '2'), ('PreEvap End current (A)', '35'), ('Evaporation Current (A)', '40'),
                             ('Current Ramp Rate (A/S)', '0.5'), ('Evaporation time (min)', '0.5')):
            self.assertTrue(c.enter('pf_ramped_carbon_rows', label, value), label)
        c.tap('pf_ramped_carbon_ok')
        c.tap('run_profile')
        outgas, cur = self._currents(c)
        self.assertAlmostEqual(outgas, 25.0)                                    # 1: Outgas Current
        self.assertAlmostEqual(cur[0.0], 25.0)                                  # 2: from the outgas current at Pre Evap Rate ...
        self.assertAlmostEqual(cur[2.5], 30.0)
        self.assertAlmostEqual(cur[5.0], 35.0)                                  # ... to PreEvap End current after 5 s
        self.assertAlmostEqual(cur[9.0], 37.0)                                  # 3: then at Current Ramp Rate
        self.assertAlmostEqual(cur[20.0], 40.0)                                 # 4: held at Evaporation Current
        self.assertEqual(c.val('cycle.phase'), 'succeeded')
        self.assertAlmostEqual(c.val('cycle.deposition_s'), 35.0)              # 5 s ramp + evaporation time 30 s (starts at PreEvap End current)

    def test_pulse_rod_profile_runs_pulses(self):
        p = Rig(source=rt.CARBON_EXAMPLE)
        p.admin()
        p.call(p.A, 'profile.edit', name='QT Pulse rod evap', parameters={'number_of_pulses': 3, 'pulse_length': 2.0, 'pulse_interval': 5.0}, system={})
        p.select_profile('QT Pulse rod evap')
        hours = p.h.sputter_s
        self.assertTrue(p.tap('run_profile'))
        self.assertEqual(p.text('r_stage'), 'Pumping')
        outgas, cur = self._currents(p)
        self.assertAlmostEqual(outgas, 20.0)                                    # outgas current (rod default)
        on = sorted(t for t, a in cur.items() if a > 0)
        self.assertEqual({cur[t] for t in on}, {75.0})                          # pulse current (rod default 75 A)
        self.assertEqual((on[0], on[-1]), (0.0, 11.96))                         # (the step from 11.98 s ends the run)
        self.assertEqual([t for t in (1.98, 2.0, 4.98, 5.0, 6.98, 7.0, 10.0) if cur[t] > 0], [1.98, 5.0, 6.98, 10.0])   # 2 s pulses every 5 s
        self.assertEqual(p.val('cycle.phase'), 'succeeded')
        self.assertAlmostEqual(p.val('cycle.deposition_s'), 12.0)               # (3 - 1) x 5 s + 2 s
        self.assertAlmostEqual(p.h.sputter_s - hours, 240.0)                   # one pulse evaporation = 4 min of sputtering (table 15)
        self.assertGreater(p.val('ftm.thickness_m'), 0.0)
        self.assertGreater(sum(p.call(p.E, 'product.read')['lots'][0]['sample_kg']), 0.0)
        p.run_to_end()
        p.call(p.A, 'profile.edit', name='QT Pulse rod evap', parameters={'max_pulse_voltage': 3.0, 'outgas_source': False}, system={})
        p.tap('run_profile')
        while p.val('cycle.stage') != 'deposition':
            p.step(0.5)
        p.step(0.01)
        self.assertAlmostEqual(p.val('source.current_a'), 3.0 / rt.PLANT_EXAMPLE['carbon_resistance_ohm'])   # Max Pulse Voltage / rod resistance
        p.shows('r_req', '75')                                                   # requested pulse current
        self.assertTrue(p.tap('r_up'))
        self.assertEqual(p.val('terminal.run')['requested_current_a'], 76.0)

    def test_without_turbo_the_rotary_pump_cannot_reach_operational_vacuum(self):
        for key in ('use_turbo_pumping', 'turbo_present'):
            r = Rig()
            r.admin()
            r.call(r.A, 'system.configure', category='hardware', parameters={key: False})
            r.tap('run_profile')
            r.step(300)
            self.assertEqual(r.val('cycle.stage'), 'pumpdown', key)
            self.assertAlmostEqual(r.val('vacuum.pressure_pa'), rt.ROTARY_BASE_PA, places=6)   # 5x10-2 mbar
            self.assertEqual(r.val('vacuum.turbo_percent'), 0.0)
            r.step(310)                                                          # Max Pumpdown Time 600 s
            self.assertEqual((r.val('cycle.phase'), r.val('safety.warning')), ('failed', 'PUMP_TIMEOUT'))
            r.shows('r_fail', 'System failed to achieve operational Vacuum')     # on the run screen while the chamber vents
            r.run_to_end()
            self.assertEqual(r.screen(), ('run_error', 'error'))
            r.shows('e_text', 'System failed to achieve operational Vacuum')
        d = Rig()                                                                # the direct path uses the same pumping model
        d.h.system['hardware']['use_turbo_pumping'] = False
        d.call(d.A, 'vacuum.pump', enabled=True)
        d.step(300)
        self.assertAlmostEqual(d.val('vacuum.pressure_pa'), rt.ROTARY_BASE_PA, places=6)
        t = Rig()
        t.call(t.A, 'vacuum.pump', enabled=True)
        t.step(300)
        self.assertAlmostEqual(t.val('vacuum.pressure_pa'), 0.005, places=8)   # with the turbo: 5x10-5 mbar

    def test_material_density_drives_the_ftm(self):
        a, b = Rig(), Rig()
        b.menu('Edit', 'Materials')
        b.row('ml', 'Chromium')
        b.tap('ml_edit')
        b.enter('mat_rows', 'Density (g/cm3)', '14.38')                          # twice the Chromium density
        b.tap('mat_ok')
        b.tap('ml_ok')
        for r in (a, b):
            r.tap('run_profile')
            r.step(330)
            self.assertEqual(r.val('cycle.phase'), 'succeeded')
        self.assertAlmostEqual(b.val('ftm.thickness_m') / a.val('ftm.thickness_m'), 0.5)
        self.assertEqual(a.call(a.E, 'product.read')['lots'][0]['sample_kg'], b.call(b.E, 'product.read')['lots'][0]['sample_kg'])   # true film unchanged
        b.shows('r_den', '14.38')

    def test_material_type_sets_the_clean_property(self):
        r = Rig()                                                                # User level: Clean target is grey
        r.tap('edit_profile')
        r.choose('pf_timed_sputter_rows', 'Material', 'Titanium')               # oxidising
        r.ov('Yes')
        self.assertEqual(r.all_cells('pf_timed_sputter_rows')['Clean target'], 'Yes')
        self.assertTrue(r.tap('pf_timed_sputter_ok'))
        p = r.profile('QT Time Chromium')['parameters']
        self.assertEqual((p['material'], p['clean_target'], p['sputter_current']), ('Titanium', True, 50.0))
        r.tap('edit_profile')
        r.choose('pf_timed_sputter_rows', 'Material', 'Gold')                   # noble
        r.ov('Yes')
        self.assertTrue(r.tap('pf_timed_sputter_ok'))
        self.assertFalse(r.profile('QT Time Chromium')['parameters']['clean_target'])
        r.tap('edit_profile')
        r.choose('pf_timed_sputter_rows', 'Material', 'Titanium')
        r.ov('No')                                                               # just the material: the clean property stays
        self.assertTrue(r.tap('pf_timed_sputter_ok'))
        self.assertEqual((r.profile('QT Time Chromium')['parameters']['material'], r.profile('QT Time Chromium')['parameters']['clean_target']), ('Titanium', False))
        self.assertEqual(r.call(r.A, 'profile.edit', expect='rejected', name='QT Time Chromium', parameters={'material': 'Gold', 'clean_target': True},
                                system={})['error']['code'], 'ACCESS_LEVEL')    # not the material default: Administration only


class Ownership(unittest.TestCase):
    """Caller rules: a run started by a panel gesture or a direct profile.run belongs to that caller; another agent cannot stop, vent, switch off
    or change it on the panel or directly (state unchanged); the owner polls and cancels it."""

    def _b(self, r):
        r.h.register_principal('agent_b', 'agent')
        return r.h.session('agent_b')

    def _b_tap(self, r, B, wid):
        x, y, w, h = r.widgets()[wid]['rect']
        return r.call(B, 'panel.touch', display_id=TOUCH, x=x + w // 2, y=y + h // 2)['accepted']

    def test_panel_started_run_is_the_callers(self):
        r = Rig()
        B = self._b(r)
        r.tap('run_profile')
        op = r.h.operation_id
        self.assertEqual(r.h._owners[op], 'agent')
        self.assertEqual(r.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        r.step(200)
        self.assertEqual(r.val('cycle.stage'), 'deposition')
        snap = copy.deepcopy(r.h.debug_snapshot())
        self.assertFalse(self._b_tap(r, B, 'r_cancel'))                           # B's Cancel on the screen
        self.assertEqual((r.overlay()['title'], r.overlay()['lines']),             # the refusal shows as a dialog (sweep)
                         ('Information', ['This run was started by another user: it cannot be cancelled or changed here']))
        r.ov('OK')
        self.assertFalse(self._b_tap(r, B, 'r_up'))
        r.ov('OK')
        for cap, args in (('acquisition.stop', {}), ('profile.cancel', {}), ('vacuum.vent', {}),
                          ('vacuum.pump', {'enabled': False}), ('coating.set_current', {'current_a': 0.03}), ('rotation.set', {'enabled': False, 'rpm': 12.0})):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        now = r.h.debug_snapshot()
        for k in ('_requests', 'log', 'revision'):
            snap.pop(k), now.pop(k)
        dump = lambda d: json.dumps(d, sort_keys=True, default=lambda o: sorted(o) if isinstance(o, set) else str(o))   # noqa: E731
        self.assertEqual(dump(snap), dump(now))                                   # state unchanged
        self.assertTrue(r.val('power.enabled'))
        self.assertEqual(r.A.cancel(op, 'a-cancel')['status'], 'succeeded')      # the owner cancels its run
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        r.step(70)                                                               # vent after the turbo has slowed to 50 %
        self.assertTrue(r.val('terminal.run')['venting'])
        self.assertFalse(self._b_tap(r, B, 'v_abort'))                           # the automatic vent is part of A's run
        self.assertTrue(r.val('vacuum.venting'))
        r.ov('OK')                                                                # the refusal dialog
        self.assertTrue(r.tap('v_abort'))
        self.assertFalse(r.val('vacuum.venting'))

    def test_mains_switch_is_a_hard_switch(self):
        """Sweep 2026-10-19: the rear mains rocker cuts the power; another agent's switch-off (panel or power.set) is not refused and ends the run."""
        for direct in (False, True):
            r = Rig()
            B = self._b(r)
            r.tap('run_profile')
            op = r.h.operation_id
            r.step(200)
            if direct:
                r.call(B, 'power.set', enabled=False)
            else:
                self.assertTrue(r.call(B, 'panel.switch', input_id='mains_switch', position='off')['accepted'])
            self.assertFalse(r.val('power.enabled'))
            self.assertEqual(r.A.poll(op)['status'], 'cancelled')

    def test_direct_profile_run_returns_its_operation(self):
        r = Rig()
        B = self._b(r)
        resp = r.A.invoke(instance_id=INST, capability_id='profile.run', arguments={}, request_id='run-1')
        self.assertEqual((resp['status'], resp['operation_id']), ('accepted', r.h.operation_id))
        self.assertEqual(resp['result']['operation_id'], r.h.operation_id)
        self.assertEqual(r.A.poll(resp['operation_id'])['status'], 'running')
        self.assertEqual(r.call(B, 'profile.cancel', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertFalse(self._b_tap(r, B, 'r_cancel'))
        r.ov('OK')                                                                # the refusal dialog
        self.assertTrue(r.tap('r_cancel'))                                       # the owner's own panel Cancel
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        r.run_to_end()
        r.select_profile('QT Vent chamber')
        self.assertEqual(r.call(r.A, 'profile.run')['operation_id'], '')        # vent cycle: no coating operation, still the caller's run
        self.assertEqual(r.call(B, 'profile.cancel', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        r.call(r.E, 'profile.cancel')                                            # the evaluator is not restricted
        self.assertEqual(r.val('terminal.run')['profile'], '')


class InstrumentFaults(unittest.TestCase):
    """Sweep 2026-10-19 (1.2.0): the table 21 error messages behind an instrument fault, by evaluator fault.inject / fault.clear."""
    def test_fault_during_a_run_and_refused_start(self):
        r = Rig()
        r.tap('run_profile')                                                      # QT Time Chromium: a sputter run
        op = r.h.operation_id
        r.step(200)
        self.assertEqual(r.val('cycle.stage'), 'deposition')
        r.call(r.E, 'fault.inject', code='sputter_short')
        self.assertEqual((r.A.poll(op)['status'], r.val('safety.warning')), ('failed', 'SPUTTER_SHORT'))
        r.shows('r_fail', 'Short on Sputter output. Please check target')        # on the run screen while the chamber vents
        r.run_to_end()
        self.assertEqual(r.screen(), ('run_error', 'error'))
        r.shows('e_text', 'Short on Sputter output. Please check target')
        r.tap('e_ok')
        self.assertFalse(r.tap('run_profile'))                                    # the cause is still there: the next run is refused
        self.assertEqual((r.overlay()['title'], r.overlay()['lines']), ('Error', ['Short on Sputter output. Please check target']))
        r.ov('OK')
        self.assertEqual(r.call(r.A, 'profile.run', expect='rejected')['error']['code'], 'SPUTTER_SHORT')
        r.call(r.E, 'fault.clear', code='sputter_short')                          # target shield cleaned
        self.assertTrue(r.tap('run_profile'))
        self.assertEqual(r.val('cycle.phase'), 'running')

    def test_faults_apply_to_their_process_and_power_cycle(self):
        r = Rig()
        r.call(r.E, 'fault.inject', code='rod_voltage')                           # a carbon fault does not stop sputtering
        self.assertTrue(r.tap('run_profile'))
        r.call(r.A, 'profile.cancel')
        r.run_to_end()
        r.call(r.E, 'fault.clear', code='rod_voltage')
        r.call(r.E, 'fault.inject', code='psu_not_powered')                       # cleared by switching off for 10 s
        self.assertFalse(r.tap('run_profile'))
        self.assertEqual(r.overlay()['lines'], ['Interlock Present PSU not powered'])
        r.ov('OK')
        r.switch('off')
        r.step(5)
        r.switch('on')
        r.step(6)
        self.assertFalse(r.tap('run_profile'))                                    # off for 5 s only: still there
        r.ov('OK')
        r.switch('off')
        r.step(10)
        r.switch('on')
        r.step(6)
        self.assertEqual(r.val('terminal.faults'), [])
        self.assertTrue(r.tap('run_profile'))
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='sputter_short')['error']['code'], 'ACCESS_DENIED')   # evaluator only


class Refusals(unittest.TestCase):
    def test_dark_screen_busy_and_invalid_gestures(self):
        r = Rig(power=False)
        self.assertFalse(r.touch(100, 100))
        self.assertEqual(int(r.h.panel.render(TOUCH).max()), 0)
        r.switch('on')
        r.step(6)
        r.tap('run_profile')
        self.assertEqual(r.call(r.A, 'panel.touch', expect='rejected', display_id=TOUCH, x=640, y=0)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.switch', expect='rejected', input_id='mains_switch', position='half')['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call(r.A, 'profile.run', expect='rejected')['error']['code'], 'BUSY')
        self.assertEqual(r.call(r.A, 'profile.edit', expect='rejected', name='QT Time Chromium', parameters={'sputter_current': 80.0}, system={})['error']['code'],
                         'OUT_OF_RANGE')
        self.assertEqual(r.call(r.A, 'profile.edit', expect='rejected', name='QT Time Chromium', parameters={'clean_target': True}, system={})['error']['code'],
                         'ACCESS_LEVEL')
        self.assertEqual(r.call(r.A, 'system.configure', expect='rejected', category='vacuum', parameters={'max_pumpdown_time': 300.0})['error']['code'],
                         'ACCESS_LEVEL')
        self.assertEqual(r.call(r.A, 'profile.edit', expect='rejected', name='QT Pulse rod evap', parameters={'pulse_interval': 0.5}, system={})['error']['code'],
                         'OUT_OF_RANGE')


class TimeOuts(unittest.TestCase):
    def test_no_menu_time_out_and_initialising(self):
        r = Rig(power=False)
        r.switch('on')
        r.step(4.9)
        self.assertEqual(r.screen()[0], 'init')
        r.step(0.2)
        self.assertEqual(r.screen()[0], 'gui')
        r.menu('Edit', 'System')
        r.step(3600)
        self.assertEqual(r.screen(), ('gui', 'system'))                           # no time-out documented


class Equivalence(unittest.TestCase):
    def test_panel_and_direct_commands_give_the_same_state(self):
        a, b = Rig(), Rig()
        a.tap('edit_profile')
        a.enter('pf_timed_sputter_rows', 'Sputter Time (seconds)', '45')
        a.tap('pf_timed_sputter_ok')
        a.tap('run_profile')
        b.call(b.A, 'profile.edit', name='QT Time Chromium', parameters=dict(b.profile('QT Time Chromium')['parameters'], sputter_time=45.0), system={})
        b.call(b.A, 'profile.run')
        for r in (a, b):
            r.step(500)
        a.select_profile('QT Vacuum shutdown')
        a.tap('run_profile')
        b.call(b.A, 'profile.select', name='QT Vacuum shutdown')
        b.call(b.A, 'profile.run')
        for r in (a, b):
            r.step(100)
        sa, sb = (copy.deepcopy(r.h.debug_snapshot()) for r in (a, b))
        for s in (sa, sb):
            for k in ('log', '_requests', '_owners', 'invoke', 'revision'):
                s.pop(k, None)
        self.assertEqual(json.dumps(sa, sort_keys=True, default=str), json.dumps(sb, sort_keys=True, default=str))


class Rendering(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        def run():
            r = Rig()
            r.menu('Edit', 'Profiles')
            cap = r.call(r.A, 'panel.capture', display_id=TOUCH)
            return cap, Path(cap['image']['path']).read_bytes()
        (c1, p1), (_, p2) = run(), run()
        self.assertEqual(hashlib.sha256(p1).hexdigest(), hashlib.sha256(p2).hexdigest())
        self.assertEqual((c1['image']['width'], c1['image']['height']), (640, 480))
        r = Rig(power=False)
        self.assertIn('panel.touch', r.A.describe(instance_id=INST)['capabilities'])
        self.assertNotIn('panel.frame', r.A.describe(instance_id=INST)['capabilities'])

    def test_evidence_renders(self):
        """home (standby) / menu (Profile Editor) / error (run error) and the other screens, deterministic, with renders.json."""
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        panel = r.h.panel
        shots = {'home': panel.save_png(TOUCH, OUT / 'home.png')}
        r.tap('mb_edit')
        shots['edit_menu'] = panel.save_png(TOUCH, OUT / 'edit_menu.png')
        r.ov('Profiles')
        shots['menu'] = panel.save_png(TOUCH, OUT / 'menu.png')
        r.tap('pl_ok')
        r.tap('edit_profile')
        shots['profile'] = panel.save_png(TOUCH, OUT / 'profile.png')
        r.row('pf_timed_sputter_rows', 'Sputter Current (mA)')
        shots['keypad'] = panel.save_png(TOUCH, OUT / 'keypad.png')
        r.ov('Cancel')
        r.tap('pf_timed_sputter_cancel')
        r.tap('run_profile')
        r.step(220)
        shots['run'] = panel.save_png(TOUCH, OUT / 'run.png')
        r.step(140)
        shots['venting'] = panel.save_png(TOUCH, OUT / 'venting.png')
        r.run_to_end()
        r.tap('run_profile')
        r.step(200)
        r.h.opened = True
        r.step(1)
        shots['error'] = panel.save_png(TOUCH, OUT / 'error.png')
        for name, sh in shots.items():
            self.assertEqual(hashlib.sha256((OUT / Path(sh['path']).name).read_bytes()).hexdigest(), sh['sha256'], name)
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=2, sort_keys=True) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_quorum_q150tes_contract_metadata()
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for name, cap in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'errors'):
                self.assertEqual(c['capabilities'][name][key], cap[key], (name, key))
        for sid in meta['state_slots']:
            self.assertIn(sid, c['state_slots'])
        spec = rt.PANEL_SPEC
        for k, v in list(spec['inputs'].items()) + list(spec['displays'].items()):
            self.assertIn(v['component_id'], c['components'], k)
        self.assertIsNone(c['components']['touch_surface']['passive_reason'])
        self.assertEqual(set(pu.spec_capabilities(spec)) - set(c['capabilities']), set())
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', '0.1'))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        from asset_tool import validate_contract
        self.assertEqual(validate_contract(c, ROOT, check_files=False), [])


class ZTerminalCoverage(unittest.TestCase):
    def test_every_terminal_command_ran(self):
        if len(TERM_SEEN) > 10:                                                   # only when the whole suite ran
            self.assertEqual(set(rt.TERM_CAPS) - TERM_SEEN, set())


if __name__ == '__main__':
    unittest.main()
