"""Front-panel tests for CHEM-150 ika_c6000 (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_ika_c6000.py -q

Every gesture goes through the session API (panel.touch on the touch screen, panel.press on the mains switch, the runtime's normal invoke path);
the host steps the clock and the robot / evaluator handles the decomposition vessel (scan, prepare, hang).  Each check looks at the runtime state
and at the screen: the structured frame (pages, widgets, list cells, form rows, overlays) and the rendered pixels.  Themes: the key procedures of
the menu survey (register a vessel, calibrate / set the C-value, create a measurement, start it and insert the vessel, change the working mode,
maintenance programs, switch off); panel = direct capability; refusals and errors; settings, users, aids, modules; unsupported functions; render
determinism and the PNGs of outputs/panel_ui/ika_c6000/; contract entries."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import ika_c6000 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'c6000_01'
RFID = 'C6012-0001'
OUT = ROOT / 'outputs/panel_ui/ika_c6000'
SURVEY_TASKS = ['register a decomposition vessel', 'calibrate / set the C-value', 'create a measurement', 'start a measurement and insert the vessel',
                'change the working mode', 'run maintenance programs', 'switch off']
DONE_TASKS = set()
PANEL_CALLS = set()                                                             # capabilities a panel gesture invoked successfully
_panel_invoke = rt.IkaC6000Runtime.panel_invoke


def _recording_panel_invoke(self, capability_id, arguments):
    out = _panel_invoke(self, capability_id, arguments)
    PANEL_CALLS.add(capability_id)
    return out


rt.IkaC6000Runtime.panel_invoke = _recording_panel_invoke
SPEC = rt.PANEL_SPEC
LOWER, UPPER = set('qwertyuiopasdfghjklzxcvbnm'), set('QWERTYUIOPASDFGHJKLZXCVBNM')


class Rig:
    def __init__(self, on=True, mode='isoperibol_25', **plant):
        self.h = rt.IkaC6000Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, **plant))
        if on:
            self.boot()
            if mode and self.h.mode != mode:
                self.change_mode(mode)

    # ---- session calls and the clock
    def call(self, sess, cap, expect=('succeeded', 'accepted'), **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        expect = (expect,) if isinstance(expect, str) else expect
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        if r['status'] in ('succeeded', 'accepted'):
            if cap.startswith('panel.'):
                assert self.h.time_s == t0, cap
            return r['result']
        return r

    def step(self, dt):
        self.call(self.E, 'sim.step', dt_s=dt)

    def run_to_end(self, limit=4000.0):
        t = self.h.time_s
        while self.h.run is not None and self.h.time_s - t < limit:
            self.step(5.0)

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays']['screen']

    def screen(self):
        return self.frame()['screen']

    def page(self):
        return self.disp()['page']

    def w(self, wid):
        for x in self.disp()['widgets']:
            if x['id'] == wid:
                return x
        raise KeyError(wid, [x['id'] for x in self.disp()['widgets']])

    def ids(self):
        return {x['id'] for x in self.disp()['widgets']}

    def overlay(self):
        return self.disp().get('overlay')

    def last_rejection(self):
        return self.frame()['ui']['last_gesture']['rejection']

    # ---- gestures
    def touch(self, x, y, to=None):
        args = dict(display_id='screen', x=int(x), y=int(y))
        if to is not None:
            args.update(to_x=int(to[0]), to_y=int(to[1]))
        return self.call(self.A, 'panel.touch', **args)['accepted']

    def tap(self, wid):
        x, y, ww, hh = self.w(wid)['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def ov_button(self, label=None, bid=None):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if (bid is not None and b['id'] == bid) or (label is not None and b['label'] == label):
                return b
        raise KeyError(label or bid, [(b['id'], b['label']) for b in ov['buttons']])

    def tap_ov(self, label=None, bid=None):
        x, y, ww, hh = self.ov_button(label, bid)['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def mains(self):
        return self.call(self.A, 'panel.press', input_id='mains_switch')['accepted']

    def pick(self, wid, text, col=0):
        """Tap the row of a list widget whose cell col shows text."""
        w = self.w(wid)
        for i, cells in enumerate(w['cells']):
            if cells[col] == text:
                x, y, ww, _ = w['rect']
                return self.touch(x + ww // 2, y + i * w['row_h'] + w['row_h'] // 2)
        raise KeyError(text, w['cells'])

    def row(self, wid, fid):
        """Tap the row of field fid in form widget wid (scrolling the form when the row is below the visible part)."""
        for _ in range(4):
            w = self.w(wid)
            if fid in w['row_ids']:
                x, y, ww, _ = w['rect']
                i = w['row_ids'].index(fid)
                return self.touch(x + ww // 2, y + i * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            if w['scroll'] > 0 and _ == 0:
                self.touch(x + ww // 2, y + 5, to=(x + ww // 2, y + hh - 5))          # back to the top first (the form keeps its scroll)
            else:
                self.touch(x + ww // 2, y + hh - 5, to=(x + ww // 2, y + 5))
        raise KeyError(fid)

    def row_value(self, wid, fid):
        for _ in range(4):
            w = self.w(wid)
            if fid in w['row_ids']:
                return w['cells'][w['row_ids'].index(fid)][1]
            x, y, ww, hh = w['rect']
            if w['scroll'] > 0 and _ == 0:
                self.touch(x + ww // 2, y + 5, to=(x + ww // 2, y + hh - 5))
            else:
                self.touch(x + ww // 2, y + hh - 5, to=(x + ww // 2, y + 5))
        raise KeyError(fid)

    def scroll_top(self, wid):
        x, y, ww, hh = self.w(wid)['rect']
        self.touch(x + ww // 2, y + 5, to=(x + ww // 2, y + hh - 5))

    def kb(self, text):
        """Type on the open keyboard (alpha: Shift and 12@ pages; numeric) and press OK."""
        for ch in text:
            ov = self.overlay()
            want = 'upper' if ch in UPPER else 'lower' if ch in LOWER else None
            if ov['mode'] != 'num' and want is None and not any(b['id'] == 'key:' + ('{space}' if ch == ' ' else ch) for b in ov['buttons']):
                want = 'sym'
            if want and ov['mode'] != want:
                if ov['mode'] == 'sym':
                    self.tap_ov(bid='key:{abc}')
                if want == 'sym':
                    self.tap_ov(bid='key:{sym}')
                elif self.overlay()['mode'] != want:
                    self.tap_ov(bid='key:{shift}')
            self.tap_ov(bid='key:' + ('{space}' if ch == ' ' else ch))
        self.tap_ov(bid='key:{ok}')

    def enter(self, wid, fid, value):
        """Set a form field: a choice by its text, or typed text / number."""
        self.row(wid, fid)
        if self.overlay()['kind'] == 'select':
            return self.tap_ov(label=value)
        return self.kb(value)

    # ---- procedures
    def boot(self):
        assert self.mains()
        assert self.screen() == 'booting'
        self.step(rt.SOFTWARE_LOAD_S + 1.0)
        assert self.screen() == 'systemtest', self.screen()
        self.step(rt.SYSTEM_TEST_S)
        assert self.tap('st_confirm')
        assert (self.screen(), self.page()) == ('gui', 'home'), (self.screen(), self.page())

    def change_mode(self, mode):
        assert self.tap('fb_mode')
        assert self.tap_ov(label=rt_mode_text(mode))
        assert self.screen() == 'systemtest'
        self.step(rt.MODE_TEST_S + 1.0)
        assert (self.screen(), self.h.test_result, self.h.mode) == ('gui', 'green', mode), (self.screen(), self.h.test_result)

    def register_vessel(self, name='V1', serial='0012345678'):
        self.call(self.A, 'vessel.scan', rfid=RFID)
        self.go_home()
        self.tap('t_vessels')
        self.tap('v_new')
        self.enter('f_vessel_w', 'vname', name)
        self.enter('f_vessel_w', 'serial', serial)
        return self.tap('vn_ok')

    def new_measurement(self, name, weight_g, kind='Calibration', vessel='V1', aid='IKA cotton wool thread'):
        if self.page() != 'measurements':
            self.go_home()
            self.tap('t_measurements')
        self.tap('m_new')
        self.enter('f_new_w', 'name_mode', 'User-defined')
        self.enter('f_new_w', 'name', name)
        self.enter('f_new_w', 'weight', f'{weight_g:.4f}')
        self.enter('f_new_w', 'kind', kind)
        self.enter('f_new_w', 'vessel', vessel)
        if aid:
            self.enter('f_new_w', 'ext1', aid)
        return self.tap('mn_ok')

    def go_home(self):
        while self.page() != 'home':
            back = [i for i in self.ids() if i.endswith('_back') or i.endswith('_cancel')]
            assert back, self.ids()
            self.tap(sorted(back)[0])

    def prepare(self, m=1.0e-3):
        return self.call(self.E, 'vessel.prepare', rfid=RFID, sample=dict(substance_id='benzoic_acid', mass_kg=m, form='pellet'), aids=[],
                         absorption_water_kg=0.0)['balance_sample_kg']

    def start_selected(self, name):
        """Measurements page: select the record, Start, then the robot screws, scans and hangs the vessel and the operator confirms."""
        self.pick('m_list', name)
        assert self.tap('m_start')
        assert self.overlay()['dialog'] == 'insert_vessel'
        self.call(self.A, 'vessel.scan', rfid=RFID)
        self.call(self.A, 'vessel.hang', rfid=RFID)
        assert self.tap_ov(label='Confirm')
        assert self.page() == 'run'

    def take_out(self):
        if self.h.error:
            self.tap('er_ok')
        self.call(self.A, 'vessel.remove')
        self.call(self.A, 'vessel.clean', rfid=RFID)

    def calibration(self, name):
        m = self.prepare()
        self.new_measurement(name, m * 1e3)
        self.start_selected(name)
        self.run_to_end()
        self.take_out()
        return next(k for k, r in self.h.records.items() if r['name'] == name)


def rt_mode_text(mode):
    kind, t = mode.split('_')
    return f'{kind.capitalize()} {t} C'


def lit(img, rect, thr=200):
    """Any dark text pixel inside a rect of the light TFT (text is drawn dark on the light background)."""
    x, y, w, h = rect
    return bool((img[y:y + h, x:x + w].astype(int).sum(axis=2) < thr).any())


class KeyProcedures(unittest.TestCase):
    def test_power_up_system_test_and_safety_confirmation(self):
        g = Rig(on=False)
        self.assertEqual(g.screen(), 'off')
        self.assertFalse(g.touch(400, 240))                               # dark screen: touches do nothing
        self.assertTrue(g.mains())
        self.assertEqual(g.screen(), 'booting')
        self.assertIn('The software is loading ...', [x['text'] for x in g.disp()['widgets']])
        g.step(rt.SOFTWARE_LOAD_S + 1.0)
        self.assertEqual((g.screen(), g.w('st_lamp')['text']), ('systemtest', 'System test running ...'))
        g.step(rt.SYSTEM_TEST_S)
        self.assertEqual(g.h.state, 'wait')
        self.assertEqual(g.screen(), 'systemtest')                        # the safety instructions still have to be confirmed
        self.assertEqual((g.w('st_lamp')['color'], g.h.test_result), ('yellow', 'yellow'))
        self.assertTrue(g.tap('st_confirm'))
        self.assertEqual((g.screen(), g.page()), ('gui', 'home'))
        self.assertEqual(g.w('h_progress')['text'], 'System test yellow: change the working mode')
        self.assertEqual(g.w('h_clock')['text'], '01.01.2026 00:05')
        # skip a running system test (function bar of the system test screen)
        g.tap('fb_test')
        self.assertEqual((g.screen(), g.h.state), ('systemtest', 'system_test'))
        self.assertTrue(g.tap('st_skip'))
        self.assertEqual((g.h.state, g.h.test_result), ('wait', 'yellow'))
        self.assertTrue(g.tap('st_confirm'))
        self.assertEqual(g.screen(), 'gui')

    def test_change_the_working_mode(self):
        g = Rig(mode=None)
        g.tap('fb_mode')
        self.assertEqual([b['label'] for b in g.overlay()['buttons']], ['Isoperibol 25 C', 'Dynamic 25 C', 'Close'])   # only modes for 21.5 C water
        g.tap_ov(label='Dynamic 25 C')
        self.assertEqual((g.h.mode, g.h.state, g.screen()), ('dynamic_25', 'system_test', 'systemtest'))
        g.step(rt.MODE_TEST_S + 1.0)
        self.assertEqual((g.screen(), g.h.test_result, g.w('h_mode')['text']), ('gui', 'green', 'Dynamic 25 C'))
        # and from the measurement settings (11.4.2): select the mode, save
        g.tap('t_settings')
        g.tap('s_meas')
        g.enter('f_ms_w', 'mode', 'Isoperibol 25 C')
        self.assertEqual(g.h.mode, 'dynamic_25')                          # nothing changes before Save
        g.tap('msx_ok')
        self.assertEqual((g.h.mode, g.screen()), ('isoperibol_25', 'systemtest'))
        DONE_TASKS.add('change the working mode')

    def test_register_vessel_create_start_and_calibrate(self):
        g = Rig()
        self.assertTrue(g.register_vessel())
        self.assertEqual(g.page(), 'vessels')
        reg = g.h.registry['V1']
        self.assertEqual((reg['rfid'], reg['serial_number'], reg['previous_ignitions'], reg['platinum_wire']), (RFID, '0012345678', 0, False))
        self.assertEqual(g.w('v_list')['cells'][0][:3], ['V1', '0012345678', '0'])
        DONE_TASKS.add('register a decomposition vessel')
        g.call(g.E, 'settings.configure', o2_flush=False, auto_vessel_detection=True, reference_value_j_kg=26434.0e3)
        g.step(3600.0)
        # create: name typed, weight from the balance of the robot's preparation, calibration, vessel V1, cotton thread (Total 50 J)
        m = g.prepare()
        g.tap('v_back')
        g.tap('t_measurements')
        g.tap('m_new')
        self.assertEqual(g.row_value('f_new_w', 'name_mode'), 'By date')   # factory setting: sample name from date and time
        self.assertEqual(g.row_value('f_new_w', 'name_date'), g.h._iso().replace('-', '').replace(':', '').replace('T', '')[2:])
        g.scroll_top('f_new_w')
        g.enter('f_new_w', 'name_mode', 'User-defined')
        g.enter('f_new_w', 'name', 'BA cal 1')
        g.enter('f_new_w', 'weight', f'{m * 1e3:.4f}')
        g.enter('f_new_w', 'kind', 'Calibration')
        g.enter('f_new_w', 'vessel', 'V1')
        g.enter('f_new_w', 'ext1', 'IKA cotton wool thread')
        self.assertEqual(g.row_value('f_new_w', 'total'), '50.0 J')
        self.assertTrue(g.tap('mn_ok'))
        rec = next(iter(g.h.records.values()))
        self.assertEqual((rec['name'], rec['kind'], rec['vessel_name'], rec['sample_mass_kg'], rec['status']), ('BA cal 1', 'calibration', 'V1', m, 'created'))
        self.assertEqual([a['aid_name'] for a in rec['aids']], ['IKA cotton wool thread'])
        self.assertEqual(g.w('m_list')['cells'][0][:3], ['BA cal 1', 'Calibration', 'created'])
        DONE_TASKS.add('create a measurement')
        # start and insert the vessel
        g.start_selected('BA cal 1')
        self.assertEqual((g.h.run['phase'], g.w('r_phase')['text']), ('closing', 'closing'))
        g.step(600.0)
        self.assertGreater(len(g.w('r_plot')['series'][0]['points']), 100)
        g.run_to_end()
        self.assertEqual(rec['status'], 'completed')
        self.assertEqual(g.w('r_none')['text'], 'No measurement is running.')
        DONE_TASKS.add('start a measurement and insert the vessel')
        # detail view and graph of the completed calibration
        g.tap('r_back')
        g.tap('h_progress_key')                                         # the progress bar opens the last measurement
        self.assertEqual(g.page(), 'meas_open')
        rows = {c[0]: c[1] for c in g.w('mo_rows')['cells']}
        self.assertEqual(rows['C-value'], f"{rec['c_value_j_k']:.1f} J/K")
        g.tap('mo_graph')
        self.assertGreater(len(g.w('mg_plot')['series'][0]['points']), 50)
        g.take_out()
        # second calibration, then Vessel > Edit > Open calibration > Select calibrations
        g.go_home()
        mid2 = g.calibration('BA cal 2')
        mid1 = rec['measurement_id']
        g.go_home()
        g.tap('t_vessels')
        g.pick('v_list', 'V1')
        g.tap('v_edit')
        g.tap_ov(label='Open calibration')
        self.assertEqual(g.page(), 'vessel_cal')
        self.assertEqual({c[0]: c[1] for c in g.w('vc_stats')['cells']}['Calibrations'], '0')
        g.tap('vc_select')
        self.assertEqual(g.overlay()['kind'], 'checklist')
        g.tap_ov(bid='all')
        g.tap_ov(bid='ok')
        mean = (g.h.records[mid1]['c_value_j_k'] + g.h.records[mid2]['c_value_j_k']) / 2
        self.assertEqual(g.h.registry['V1']['calibrations']['isoperibol_25'], [mid1, mid2])
        self.assertAlmostEqual(g.h.registry['V1']['c']['isoperibol_25'], mean, places=6)
        stats = {c[0]: c[1] for c in g.w('vc_stats')['cells']}
        self.assertEqual((stats['Calibrations'], stats['C-value']), ('2', f'{mean:.1f} J/K'))
        # manual C-value
        g.tap('vc_manual')
        g.enter('f_cvalue_w', 'c', '10050.5')
        g.tap('cv_ok')
        self.assertEqual((g.h.registry['V1']['c']['isoperibol_25'], g.h.registry['V1']['manual']['isoperibol_25']), (10050.5, True))
        self.assertEqual({c[0]: c[1] for c in g.w('vc_stats')['cells']}['C-value'], '10050.5 J/K (manual)')
        DONE_TASKS.add('calibrate / set the C-value')

    def test_maintenance_programs(self):
        g = Rig()
        g.tap('t_maintenance')
        g.tap('mt_programs')
        g.tap('pg_fill')
        g.tap('pg_stirrer')
        self.assertTrue(g.w('pg_fill')['selected'])
        self.assertFalse(g.h.maint_stirrer)                              # nothing runs before the arrow
        g.tap('pg_back')
        self.assertEqual(g.page(), 'maintenance')
        self.assertEqual((g.h.water_target, g.h.maint_stirrer), (g.h.fit['WATER_KG'], True))
        g.step(120.0)
        self.assertTrue(g.h.read()['stirrer.running'])
        g.tap('mt_programs')
        self.assertTrue(g.w('pg_stirrer')['selected'])                  # running programs are shown in blue
        self.assertIn('rpm', g.w('pg_info')['text'])
        g.tap('pg_stirrer')
        g.tap('pg_empty')
        g.tap('pg_back')
        self.assertEqual((g.h.maint_stirrer, g.h.water_target), (False, 0.0))
        # Change Seal needs oxygen: start, then end it; O2 Fill is refused while it runs, then fills the hung vessel; O2 Degas empties it
        g.call(g.A, 'vessel.hang', rfid=RFID)
        g.tap('mt_programs')
        g.tap('pg_seal')
        g.tap('pg_back')
        self.assertTrue(g.h.seal_change)
        g.tap('mt_programs')
        g.tap('pg_o2_fill')
        self.assertFalse(g.tap('pg_back'))
        self.assertEqual(g.last_rejection(), 'SEAL_CHANGE_ACTIVE')
        self.assertEqual(g.overlay()['lines'], ['Finish Change Seal first.'])
        g.tap_ov(label='OK')
        g.tap('pg_seal')
        g.tap('pg_back')
        self.assertFalse(g.h.seal_change)
        g.tap('mt_programs')
        g.tap('pg_o2_fill')
        self.assertTrue(g.tap('pg_back'))
        self.assertEqual(g.h.vessels[RFID]['state'], 'pressurized')
        self.assertGreater(g.h.read('evaluator')['truth.vessel_pressure_pa'], 2.5e6)
        g.tap('mt_programs')
        g.tap('pg_o2_degas')
        g.tap('pg_back')
        self.assertLess(g.h.read('evaluator')['truth.vessel_pressure_pa'], 2e5)
        g.call(g.A, 'vessel.remove')
        # Empty Device and Factory Reset
        g.tap('mt_empty')
        g.tap_ov(label='OK')
        self.assertEqual(g.h.water_target, 0.0)
        g.call(g.E, 'settings.configure', o2_flush=True, auto_vessel_detection=False, reference_value_j_kg=26000.0e3)
        g.tap('mt_reset')
        g.tap_ov(label='OK')
        self.assertEqual((g.h.settings['o2_flush'], g.h.mode, g.h.state, g.screen()), (False, 'isoperibol_22', 'system_test', 'systemtest'))
        DONE_TASKS.add('run maintenance programs')

    def test_switch_off(self):
        g = Rig()
        g.tap('fb_shutdown')
        self.assertEqual(g.overlay()['dialog'], 'shutdown')
        g.tap_ov(label='OK')
        self.assertEqual((g.h.state, g.screen()), ('shutdown', 'shutdown'))
        self.assertFalse(g.touch(400, 240))
        self.assertTrue(g.mains())
        self.assertEqual((g.h.power, g.screen()), (False, 'off'))
        self.assertEqual(int(g.h.panel.render('screen').max()), 0)          # the screen is dark
        DONE_TASKS.add('switch off')


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_calls(self):
        a, b = Rig(), Rig()
        a.register_vessel()
        b.call(b.A, 'vessel.scan', rfid=RFID)
        b.call(b.A, 'vessel.register', name='V1', serial_number='0012345678', previous_ignitions=0, platinum_wire=False)
        a.go_home()
        a.new_measurement('Coal 7', 0.8123, kind='Measurement')
        b.call(b.A, 'measurement.create', name='Coal 7', sample_mass_kg=0.8123e-3, kind='measurement', vessel_name='V1',
               aids=[dict(aid_name='IKA cotton wool thread', mass_kg=None)])
        a.go_home()
        a.tap('t_settings')
        a.tap('s_aids')
        a.tap('ai_tab2')
        a.tap('ai_new')
        a.enter('f_aid_w', 'aname', 'Gelatine capsule C 9')
        a.enter('f_aid_w', 'avalue', '19500')
        a.tap('af_ok')
        b.call(b.A, 'aid.define', list_no=2, name='Gelatine capsule C 9', specific_energy_j_kg=19500.0e3, absolute_energy_j=None)
        a.tap('ai_back')
        a.tap('s_ref')
        a.enter('f_ref_w', 'ref', '26434')
        a.tap('rf_ok')
        b.call(b.A, 'settings.configure', o2_flush=False, auto_vessel_detection=True, reference_value_j_kg=26434.0e3)
        for g in (a, b):
            g.step(30.0)
        ra, rb = a.h.read('evaluator'), b.h.read('evaluator')
        self.assertEqual(ra, rb)
        self.assertEqual(a.h.records, b.h.records)


class Rules(unittest.TestCase):
    def test_refusals_show_the_reason_and_change_nothing(self):
        g = Rig(mode=None)                                                 # Isoperibol 22 C with 21.5 C water: yellow
        g.register_vessel()
        g.go_home()
        g.new_measurement('A', 1.0)
        before = g.h.read('evaluator')
        g.pick('m_list', 'A')
        self.assertFalse(g.tap('m_start'))
        self.assertEqual(g.last_rejection(), 'RUN_MODE_NOT_POSSIBLE')
        self.assertEqual(g.overlay()['lines'], ['Working mode not possible with this cooling water.'])
        self.assertEqual(g.h.read('evaluator'), before)
        g.tap_ov(label='OK')
        # obligatory fields
        g.tap('m_new')
        g.enter('f_new_w', 'name_mode', 'User-defined')
        self.assertFalse(g.tap('mn_ok'))
        self.assertEqual(g.overlay()['dialog'], 'required')
        self.assertEqual(len(g.h.records), 1)
        g.tap_ov(label='OK')
        g.tap('mn_cancel')
        # the fixed cotton thread cannot be deleted; a 10-character serial number is required
        g.go_home()
        g.tap('t_settings')
        g.tap('s_aids')
        g.pick('ai_list', 'IKA cotton wool thread')
        g.tap('ai_edit')
        self.assertFalse(g.tap_ov(label='Delete'))
        self.assertEqual(g.last_rejection(), 'AID_FIXED')
        g.tap_ov(label='OK')
        g.go_home()
        g.tap('t_vessels')
        g.tap('v_new')
        g.row('f_vessel_w', 'serial')
        for ch in '12345':
            g.tap_ov(bid='key:{sym}') if g.overlay()['mode'] != 'sym' else None
            g.tap_ov(bid='key:' + ch)
        self.assertFalse(g.tap_ov(bid='key:{ok}'))
        self.assertEqual(g.overlay()['error'], 'Invalid value')

    def test_vessel_modify_delete_and_aid_delete(self):
        g = Rig()
        g.register_vessel()
        g.go_home()
        g.new_measurement('A', 1.0)
        g.go_home()
        g.tap('t_vessels')
        g.pick('v_list', 'V1')
        g.tap('v_edit')
        g.tap_ov(label='Modify')
        self.assertEqual(g.row_value('f_vessel_mod_w', 'serial'), '0012345678')
        g.enter('f_vessel_mod_w', 'vname', 'C6012 no 2')
        g.enter('f_vessel_mod_w', 'ignitions', '12')
        g.tap('vm_ok')
        self.assertEqual(list(g.h.registry), ['C6012 no 2'])
        self.assertEqual(g.h.registry['C6012 no 2']['previous_ignitions'], 12)
        self.assertEqual(next(iter(g.h.records.values()))['vessel_name'], 'C6012 no 2')     # the created measurement follows the new name
        self.assertEqual(g.w('v_list')['cells'][0][:3], ['C6012 no 2', '0012345678', '12'])
        g.pick('v_list', 'C6012 no 2')
        g.tap('v_edit')
        g.tap_ov(label='Delete')
        self.assertEqual(g.overlay()['lines'], ['Delete vessel C6012 no 2?'])
        g.tap_ov(label='OK')
        self.assertEqual((g.h.registry, next(iter(g.h.records.values()))['vessel_name']), ({}, None))
        self.assertEqual(g.w('v_list')['empty_text'], 'No vessel registered')
        # an aid of list 3: define, change its value, delete
        g.go_home()
        g.tap('t_settings')
        g.tap('s_aids')
        g.tap('ai_tab3')
        g.tap('ai_new')
        g.enter('f_aid_w', 'aname', 'Paraffin')
        g.enter('f_aid_w', 'akind', 'Absolute (J)')
        g.enter('f_aid_w', 'avalue', '120')
        g.tap('af_ok')
        self.assertEqual(g.h.aid_lists[3]['Paraffin']['value'], 120.0)
        self.assertEqual(g.w('ai_list')['cells'], [['Paraffin', '120.0 J']])
        g.pick('ai_list', 'Paraffin')
        g.tap('ai_edit')
        g.tap_ov(label='Delete')
        self.assertEqual(g.h.aid_lists[3], {})

    def test_panel_keys_act_as_the_session_caller(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        a_sess, b_sess = g.A, g.h.session('agent_b')
        g.register_vessel()
        g.go_home()
        g.new_measurement('A1', 1.0)                                         # made by agent A through the panel: A's record
        mid = next(iter(g.h.records))
        self.assertEqual(g.h._owners[mid], 'agent')
        g.A = b_sess                                                          # agent B at the same screen
        before = g.h.read('evaluator')
        for label in ('Delete', 'Modify', 'Measurement <-> calibration'):
            g.pick('m_list', 'A1')
            g.tap('m_edit')
            ok = g.tap_ov(label=label)
            if label == 'Delete':
                ok = g.tap_ov(label='OK')
            elif label == 'Modify':
                g.enter('f_mod_w', 'weight', '2.0000')
                ok = g.tap('mm_ok')
            self.assertFalse(ok, label)
            self.assertEqual(g.last_rejection(), 'ACCESS_DENIED', label)
            self.assertEqual(g.overlay()['lines'], ['Not allowed: this belongs to another user.'])
            g.tap_ov(label='OK')
            if label == 'Modify':
                g.tap('mm_cancel')
        self.assertEqual(g.h.read('evaluator'), before)                      # nothing of A's record changed
        g.A = a_sess                                                          # A may delete its own record
        g.pick('m_list', 'A1')
        g.tap('m_edit')
        g.tap_ov(label='Delete')
        g.tap_ov(label='OK')
        self.assertEqual(g.h.records, {})

    def test_the_mains_rocker_is_physical(self):
        """Sweep 2026-10-19: the mains rocker cuts the power (hard switch): another agent's switch-off is not refused; the measurement is aborted as
        at a power loss.  The menu Shutdown (soft) stays guarded."""
        g = Rig()
        g.register_vessel()
        g.go_home()
        g.new_measurement('A1', g.prepare())
        g.start_selected('A1')
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertEqual(g.call(B, 'system.shutdown', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.call(B, 'measurement.cancel', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        reg = dict(rt.CAPS['vessel.register']['example_arguments'], name='V9')
        self.assertEqual(g.call(B, 'vessel.register', expect='rejected', **reg)['error']['code'], 'MEASUREMENT_RUNNING')   # the device's own lock
        self.assertTrue(g.call(B, 'panel.press', input_id='mains_switch')['accepted'])
        self.assertFalse(g.h.power)
        self.assertIsNone(g.h.run)

    def test_injected_manual_error_codes(self):
        """Sweep 2026-10-19 (manual chapter 13): codes without a simulated cause are raised by the evaluator with the documented message and remedy."""
        g = Rig()
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='E22')['error']['code'], 'ACCESS_DENIED')   # evaluator only
        g.call(g.E, 'fault.inject', code='E22')                               # converter: confirm, then the system test runs again
        self.assertEqual((g.screen(), g.h.error), ('error', 'E22'))
        self.assertEqual(g.w('er_text')['text'], 'Error converter!')
        self.assertTrue(g.tap('er_ok'))
        self.assertEqual(g.h.state, 'system_test')
        g.step(rt.SYSTEM_TEST_S + 1.0)
        self.assertEqual(g.h.error, 'E22')                                    # the cause is still there
        g.call(g.E, 'fault.clear')
        self.assertTrue(g.tap('er_ok'))
        g.step(rt.SYSTEM_TEST_S + 1.0)
        self.assertIsNone(g.h.error)
        g.call(g.E, 'fault.inject', code='E14')                               # temperature sensor: switch the device off and on
        self.assertEqual(g.w('er_text')['text'], 'Temperature sensor at upper limit!')
        self.assertFalse(g.tap('er_ok'))
        self.assertEqual(g.overlay()['lines'], ['Switch the device off and on.'])
        g.tap_ov(label='OK')
        self.assertEqual(g.h.error, 'E14')
        g.mains()
        g.mains()
        g.step(rt.SOFTWARE_LOAD_S + 1.0)
        self.assertEqual(g.h.error, 'E14')                                    # found again at power-up
        g.call(g.E, 'fault.clear')
        g.mains()
        g.mains()
        g.step(rt.SOFTWARE_LOAD_S + rt.SYSTEM_TEST_S + 1.0)
        self.assertIsNone(g.h.error)
        g.call(g.E, 'fault.inject', code='E30')                               # one failed RFID read: confirm and scan again
        self.assertEqual(g.w('er_text')['text'], 'Error while reading the vessel ID!')
        self.assertTrue(g.tap('er_ok'))
        self.assertEqual((g.h.error, g.h.last_scanned, g.h.faults), (None, None, set()))

    def test_error_screen_and_restart(self):
        g = Rig(on=False, cooling_water_k=303.15)
        g.mains()
        g.step(rt.SOFTWARE_LOAD_S + rt.SYSTEM_TEST_S + 1.0)
        self.assertEqual((g.screen(), g.h.error), ('error', 'SYSTEM_TEST_RED'))
        self.assertEqual(g.w('er_text')['text'], 'Cooling water temperature is outside the permitted limits.')
        self.assertFalse(g.touch(5, 5))                                   # only OK reacts
        self.assertTrue(g.tap('er_ok'))
        self.assertEqual((g.h.error, g.h.state, g.screen()), (None, 'system_test', 'systemtest'))

    def test_unsupported_functions_show_not_connected(self):
        g = Rig()
        g.tap('t_maintenance')
        for wid, text in (('mt_service', 'Access only for authorised IKA service staff.'), ('mt_upd_sw', 'No USB drive connected.')):
            self.assertFalse(g.tap(wid))
            self.assertEqual(g.overlay()['lines'], [text])
            self.assertTrue(g.frame()['ui']['unsupported'])
            g.tap_ov(label='OK')
        g.go_home()
        g.tap('t_measurements')
        g.tap('m_new')
        self.assertFalse(g.row('f_new_w', 'balance'))
        self.assertEqual(g.overlay()['lines'], ['No balance connected.'])
        g.tap_ov(label='OK')
        g.tap('mn_cancel')
        g.go_home()
        g.tap('t_modules')
        g.tap('mod_rack')
        self.assertEqual(g.overlay()['lines'], ['The C 5020 sample rack is not connected.'])
        g.tap_ov(label='OK')
        g.tap('mod_printer')
        self.assertFalse(g.tap('pr_add'))
        self.assertEqual(g.overlay()['lines'], ['No printer is activated.'])

    def test_report_offers_din_51900_only(self):
        """Sync audit 2026-10-19: the Report list offers the standard our manual documents (DIN 51900); other standards' forms and formulas are not in
        the manual, so they are not offered (spec unsupported) and the direct command refuses them."""
        g = Rig()
        g.tap('t_settings')
        g.tap('s_units')
        g.row('f_prefs_w', 'report')
        self.assertEqual([b['label'] for b in g.overlay()['buttons'] if b['label'] not in ('OK', 'Cancel')], ['DIN 51900'])
        g.tap_ov(label='DIN 51900')
        before = json.dumps(g.h.prefs, sort_keys=True)
        r = g.call(g.A, 'preferences.configure', expect='rejected', **dict(g.h.prefs, report='ISO 1928'))
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(json.dumps(g.h.prefs, sort_keys=True), before)
        self.assertTrue(any(u['item'].startswith('Reports other than DIN 51900') for u in SPEC['unsupported']))

    def test_settings_users_units_language_date(self):
        g = Rig()
        g.tap('t_settings')
        g.tap('s_user')
        g.tap('u_new')
        g.enter('f_user_w', 'uname', 'Operator A')
        g.enter('f_user_w', 'abbr', 'OA')
        g.tap('uf_ok')
        self.assertEqual(g.h.users, [dict(name='Operator A', abbreviation='OA')])
        g.pick('u_list', 'Operator A')
        g.tap('u_edit')
        g.tap_ov(label='Change')
        g.enter('f_user_w', 'abbr', 'OPA')
        g.tap('uf_ok')
        self.assertEqual(g.h.users, [dict(name='Operator A', abbreviation='OPA')])
        g.tap('u_back')
        # the user is selected with a new measurement and shown on the main menu
        g.register_vessel()
        g.go_home()
        g.tap('t_measurements')
        g.tap('m_new')
        g.enter('f_new_w', 'user', 'Operator A')
        g.enter('f_new_w', 'weight', '1.0000')
        date_name = g.h._iso().replace('-', '').replace(':', '').replace('T', '')[2:]
        self.assertEqual(g.row_value('f_new_w', 'name_date'), date_name)
        g.tap('mn_ok')
        rec = next(iter(g.h.records.values()))
        self.assertEqual((rec['name'], rec['user'], g.h.current_user), (date_name, 'Operator A', 'Operator A'))
        g.go_home()
        self.assertEqual(g.w('h_user')['text'], 'User: Operator A')
        # units, report, language apply at once
        g.tap('t_settings')
        g.tap('s_units')
        g.enter('f_prefs_w', 'unit', 'cal/g')
        g.enter('f_prefs_w', 'report', 'DIN 51900')                                     # the only Report offered (sync audit 2026-10-19)
        self.assertEqual((g.h.prefs['unit'], g.h.prefs['report']), ('cal/g', 'DIN 51900'))
        g.enter('f_prefs_w', 'language', 'Deutsch')
        g.tap('p_back')
        g.tap('s_back')
        self.assertEqual((g.w('t_measurements')['text'], g.w('h_name')['text']), ('Messungen', 'Hauptmenue'))
        g.tap('t_settings')
        g.tap('s_lang')
        g.enter('f_prefs_w', 'language', 'English')
        g.tap('p_back')
        # date and time (spinners)
        before = g.h.read()['device.datetime']
        g.tap('s_date')
        g.row('f_dt_w', 'date')
        g.tap_ov(bid='inc:0')
        g.tap_ov(bid='ok')
        g.row('f_dt_w', 'time')
        g.tap_ov(bid='inc:0')
        g.tap_ov(bid='ok')
        g.tap('dt_ok')
        import datetime
        want = (datetime.datetime.fromisoformat(before) + datetime.timedelta(days=1, hours=1)).isoformat()[:16]
        self.assertEqual(g.h.read()['device.datetime'][:16], want)
        # audio: key tones and volume
        g.tap('s_audio')
        g.tap('au_tones')
        g.tap('au_minus')
        self.assertEqual((g.h.prefs['key_tones'], g.h.prefs['volume']), (False, rt.FACTORY_PREFERENCES['volume'] - 1))
        # user delete
        g.tap('au_back')
        g.tap('s_user')
        g.pick('u_list', 'Operator A')
        g.tap('u_edit')
        g.tap_ov(label='Delete')
        self.assertEqual((g.h.users, g.h.current_user), ([], None))

    def test_modules_tap_water_and_measurement_edit_menu(self):
        g = Rig()
        g.tap('t_modules')
        self.assertEqual(g.row_value('f_modules_w', 'tap_water'), 'activated')
        g.enter('f_modules_w', 'tap_water', 'deactivated')
        self.assertFalse(g.h.modules['tap_water'])
        g.go_home()
        g.tap('fb_test')
        g.step(rt.SYSTEM_TEST_S + 1.0)
        self.assertEqual(g.h.test_result, 'yellow')                       # the module setting does not match the tap water supply
        g.tap('st_confirm')
        g.tap('t_modules')
        g.enter('f_modules_w', 'tap_water', 'activated')
        g.go_home()
        # edit menu: modify, multiple selection + delete, measurement <-> calibration
        g.register_vessel()
        g.go_home()
        for name in ('A', 'B', 'C'):
            g.new_measurement(name, 1.0)
        g.pick('m_list', 'B')
        g.tap('m_edit')
        g.tap_ov(label='Modify')
        self.assertEqual(g.row_value('f_mod_w', 'weight'), '1.0000 g')
        g.enter('f_mod_w', 'weight', '1.2345')
        g.tap('mm_ok')
        b = next(r for r in g.h.records.values() if r['name'] == 'B')
        self.assertEqual((b['sample_mass_kg'], [a['aid_name'] for a in b['aids']]), (1.2345e-3, ['IKA cotton wool thread']))
        g.pick('m_list', 'B')
        g.tap('m_edit')
        g.tap_ov(label='Measurement <-> calibration')
        self.assertEqual(b['kind'], 'measurement')
        g.tap('m_edit')
        g.tap_ov(label='Multiple selection on/off')
        self.assertEqual(g.overlay()['kind'], 'checklist')
        g.tap_ov(label='[ ] A')
        g.tap_ov(label='[ ] C')
        g.tap_ov(bid='ok')
        self.assertEqual(g.w('m_multi')['text'], 'Multiple selection: 2 selected')
        g.tap('m_edit')
        g.tap_ov(label='Delete')
        g.tap_ov(label='OK')
        self.assertEqual([r['name'] for r in g.h.records.values()], ['B'])

    def test_simulation_evaluate_archive_equal_direct(self):
        g = Rig()
        g.register_vessel()
        g.call(g.E, 'settings.configure', o2_flush=False, auto_vessel_detection=True, reference_value_j_kg=26434.0e3)
        g.go_home()
        mid = g.calibration('BA')
        g.go_home()
        g.tap('t_measurements')
        g.pick('m_list', 'BA')
        g.tap('m_edit')
        g.tap_ov(label='Simulation')
        self.assertEqual(g.row_value('f_sim_w', 'dt'), f"{g.h.records[mid]['delta_t_k']:.4f} K")
        g.enter('f_sim_w', 'dt', '2.5000')
        g.tap('ms_ok')
        direct = g.h.invoke('measurement.simulate', measurement_id=mid, delta_t_k=2.5, ignition_energy_j=round(g.h.records[mid]['ignition_energy_j'], 1),
                            c_value_j_k=None, reference_value_j_kg=None)
        self.assertEqual(g.overlay()['lines'][0], f"C-value: {direct['c_value_j_k']:.1f} J/K")
        g.tap_ov(label='OK')
        # archive: completed records, filter, evaluate (DIN 51900 input form)
        g.go_home()
        g.tap('t_archive')
        self.assertEqual([c[0] for c in g.w('a_list')['cells']], ['BA'])
        for f in ('last 24 h', 'last 7 days', 'last 30 days', 'by name', 'all'):
            g.tap('a_filter')
            self.assertEqual(g.w('a_filter')['text'], 'Filter: ' + f)
        g.pick('a_list', 'BA')
        g.tap('a_edit')
        g.tap_ov(label='Evaluate')
        g.enter('f_eval_w', 'acid', '0.100')
        g.tap('me_ok')
        self.assertEqual(g.overlay()['dialog'], 'eval_result')
        self.assertEqual(g.h.last_evaluation['nitric_acid_j'], 0.1e-3 * rt.HNO3_J_MOL)
        self.assertEqual(g.overlay()['lines'][0], 'Report: DIN 51900')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.register_vessel()
            g.go_home()
            g.tap('t_measurements')
            return g
        a, b = scenario(), scenario()
        self.assertTrue((a.h.panel.render('screen') == b.h.panel.render('screen')).all())
        OUT.mkdir(parents=True, exist_ok=True)
        a.go_home()
        img = a.h.panel.render('screen')
        self.assertTrue(lit(img, a.w('h_name')['rect']))
        shots = {'home': a.h.panel.save_png('screen', OUT / 'home.png')}
        a.tap('t_measurements')
        a.tap('m_new')
        a.enter('f_new_w', 'name_mode', 'User-defined')
        a.row('f_new_w', 'name')
        shots['keyboard'] = a.h.panel.save_png('screen', OUT / 'keyboard.png')
        a.kb('Coal 7')
        shots['new_measurement'] = a.h.panel.save_png('screen', OUT / 'new_measurement.png')
        a.tap('mn_cancel')
        a.tap('m_edit')
        shots['menu'] = a.h.panel.save_png('screen', OUT / 'menu.png')
        a.tap_ov(label='Close')
        a.go_home()
        a.tap('t_settings')
        shots['settings'] = a.h.panel.save_png('screen', OUT / 'settings.png')
        a.go_home()
        a.tap('t_maintenance')
        a.tap('mt_programs')
        shots['maintenance_programs'] = a.h.panel.save_png('screen', OUT / 'maintenance_programs.png')
        r = Rig(on=False, cooling_water_k=303.15)
        r.mains()
        r.step(rt.SOFTWARE_LOAD_S + 30.0)
        shots['system_test'] = r.h.panel.save_png('screen', OUT / 'system_test.png')
        r.step(rt.SYSTEM_TEST_S)
        shots['error'] = r.h.panel.save_png('screen', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_run_screen_png(self):
        g = Rig()
        g.register_vessel()
        g.call(g.E, 'settings.configure', o2_flush=False, auto_vessel_detection=True, reference_value_j_kg=26434.0e3)
        g.go_home()
        m = g.prepare()
        g.new_measurement('BA run', m * 1e3)
        g.start_selected('BA run')
        g.step(420.0)
        self.assertEqual(g.h.run['phase'], 'main_trial')
        self.assertEqual(g.page(), 'run')
        img = g.h.panel.render('screen')
        self.assertTrue(lit(img, g.w('r_phase')['rect']))
        OUT.mkdir(parents=True, exist_ok=True)
        g.h.panel.save_png('screen', OUT / 'run.png')
        rec = next(iter(g.h.records.values()))
        self.assertTrue(g.tap('r_cancel'), (g.last_rejection(), g.h.run and g.h.run['phase']))
        g.run_to_end()
        self.assertEqual(rec['status'], 'cancelled_after_ignition' if rec['ignited'] else 'cancelled_before_ignition')

    def test_capture_and_copies(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='screen')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        self.assertEqual(g.call(g.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        self.assertNotIn('panel', vars(g.h))

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.touch', 'panel.capture', 'panel.frame'})
        m = rt.export_ika_c6000_contract_metadata()
        c = json.loads((rt.ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k, e in m['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'errors', 'state_effects', 'preconditions'):
                self.assertEqual(c['capabilities'][k][key], e[key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/ika_c6000.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('c6000_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)
            self.assertEqual(set(rt.TOUCH_CAPS) - PANEL_CALLS, set())       # every touch-screen function ran through the panel


if __name__ == '__main__':
    unittest.main()
