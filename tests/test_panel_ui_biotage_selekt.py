"""front-panel tests for CHEM-034 biotage_selekt (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/biotage_selekt.py):
python3 -m pytest tests/test_panel_ui_biotage_selekt.py -q

Every gesture goes through the session API (panel.touch / panel.switch, the runtime's normal invoke path); widgets are found by id or text in the
structured frame (panel.frame, evaluator) and tapped at their centre.  The host (evaluator) declares the plant, mounts the column, loads the sample
while the Sample Load dialog is open and steps the clock.  Themes: the six key procedures of the menu survey (assign solvents and prime, set up a
purification, start a purification, end or abort a run, purge a column after a run, shut down); run control (pause, hold, manual fractionation,
UV zero, extend, Confirm Cleanup, Auto Extend, flushes after the run, monitoring); Results (search, favorites, Analysis, pictures, Report Setup,
Export, Create Run); Data Administration, System Settings, Maintenance, Flushes; panel = direct capability; refusals shown as the device's message
(state unchanged); unsupported network / licence / detector functions; time-outs; render determinism and the evidence PNGs; contract entries."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import biotage_selekt as rt  # noqa: E402

INST = 'selekt_01'
SCR = 'screen'
OUT = ROOT / 'outputs/panel_ui/biotage_selekt'
SURVEY_TASKS = ['Assign solvents and prime', 'Set up a purification', 'Start a purification', 'End or abort a run', 'Purge a column after a run', 'Shut down']
COL = 'Sfar Silica D 10g'


class Rig:
    """A Selekt with an agent and an evaluator session; the touch screen and the power switch only through panel.* capabilities."""

    def __init__(self, on=True, column=True):
        self.h = rt.BiotageSelektRuntime(INST)
        self.h.panel_capture_dir = tempfile.mkdtemp(prefix='selekt_panel_')
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **rt.EXAMPLE)
        self.call(self.E, 'tray.set_extension', extension_m=0.12)
        self.call(self.E, 'rack.configure', positions=144, vessel_volume_m3=9e-6)
        self.call(self.E, 'tray.set_extension', extension_m=0.0)
        if column:
            self.call(self.E, 'column.configure', **rt.COLUMN_EXAMPLE)
        if on:
            assert self.switch('on')
            self.step(21.0)
            assert self.page() == ('main', 'run'), self.page()

    # ---- sessions
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
        while dt > 1e-9:
            d = min(dt, 600.0)
            self.h.step(d)
            dt -= d

    def until(self, cond, limit=4000.0, dt=1.0):
        t = 0.0
        while not cond():
            assert t < limit, 'time-out'
            self.h.step(dt)
            t += dt

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

    def labels(self):
        ov = self.overlay()
        return [b['label'] for b in ov['buttons']] if ov else []

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
        """Type on the open keyboard (the shown text is replaced)."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov
        labels = {b['label'] for b in ov['buttons']}
        while self.overlay()['field']:
            self.ov('Del')
        if 'q' not in labels and 'Q' not in labels:
            for ch in text:
                assert self.ov(ch), ch
            return
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit() or ch in '$@#%&/():;_+=\'"!?,.-':
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

    def png(self, display=SCR):
        r = self.call(self.A, 'panel.capture', display_id=display)
        return Path(r['image']['path']).read_bytes()

    # ---- procedures through the screen
    def menu(self, item):
        if self.page()[1] != 'menu':
            self.tap('menu')
        self.tap(item)

    def owner_login(self, password='1234'):
        assert self.overlay()['dialog'] == 'd_login'
        self.ov('Password')
        self.enter(password)
        self.ov('Log In')

    def solvents(self, s1='Heptane', s2='Ethyl acetate'):
        self.menu('m_solvents')
        for k, name in ((1, s1), (2, s2)):
            self.row(f'sol{k}_form', f'S{k}')
            self.ov(name)
        self.tap('sol_back')
        self.tap('menu_back')

    def setup_run(self, flow='30', uv=('300', '350')):
        """Run setup of the shown channel: user, column type, flow, solvents A / B, wavelengths (manual p.8-11)."""
        self.tap('p_info')
        self.ov('User')
        self.ov('System Owner')
        self.ov('Close')
        self.tap('p_column')
        self.ov('Type')
        self.ov(COL)
        self.ov('Flow Rate')
        self.enter(flow)
        self.ov('Close')
        self.tap('sol_a')
        self.ov('Heptane')
        self.tap('sol_b')
        self.ov('Ethyl acetate')
        self.tap('p_coll')
        self.ov('UV1 Wavelength')
        self.enter(uv[0])
        self.ov('UV2 Wavelength')
        self.enter(uv[1])
        self.ov('Close')

    def start(self, equil=True):
        """Start: equilibration (if on), the Sample Load dialog, the host loads the sample, Start Gradient."""
        self.tap('start')
        if equil:
            self.assertion(self.val('cycle.stage') == 'equilibration')
            self.until(lambda: self.val('run.pending_sample_load') is not None)
            assert self.overlay() is None or self.overlay().get('dialog') != 'd_sample_load'
            self.tap('st_load')
        assert self.overlay()['dialog'] == 'd_sample_load', self.overlay()
        self.call(self.E, 'sample.configure', **rt.SAMPLE_EXAMPLE)
        self.ov('Start Gradient')
        assert self.val('cycle.stage') == 'gradient'

    @staticmethod
    def assertion(c):
        assert c


def prepared(equil=False, **kw):
    g = Rig(**kw)
    g.solvents()
    g.setup_run()
    if not equil:
        g.tap('equil')
        g.ov('Off')
    return g


def runtime_state(h):
    v = h.read('evaluator')
    v.pop('system.datetime', None)
    return json.dumps(v, sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_assign_solvents_and_prime(self):
        g = Rig()
        g.menu('m_solvents')
        self.assertEqual(g.page(), ('main', 'solvents'))
        g.row('sol1_form', 'S1')                                                   # manual p.7: solvent drop-down per inlet
        g.ov('Heptane')
        g.row('sol2_form', 'S2')
        g.ov('Ethyl acetate')
        self.assertEqual([x['solvent'] for x in g.val('solvents.inlets')], ['Heptane', 'Ethyl acetate', None, None])
        self.assertFalse(g.widgets()['prime3']['enabled'])                          # no solvent assigned to S3
        r0 = float(g.h.reservoir[0])
        g.tap('prime1')                                                             # 27 mL of the assigned solvent per prime
        self.assertEqual(g.val('cycle.stage'), 'prime')
        g.until(lambda: g.val('cycle.phase') != 'running')
        self.assertAlmostEqual((r0 - float(g.h.reservoir[0])) / rt.ML, 27.0, places=6)

    def test_set_up_a_purification(self):
        g = Rig()
        g.solvents()
        g.tap('ch1')
        g.setup_run()
        s = g.val('run.setups')[0]
        self.assertEqual((s['user'], s['column_type'], s['flow_ml_min'], s['solvent_a'], s['solvent_b'], s['uv1_nm'], s['uv2_nm']),
                         ('System Owner', COL, 30, 'Heptane', 'Ethyl acetate', 300.0, 350.0))
        n = len(s['segments'])
        g.tap('seg_add')                                                            # gradient table + / - (manual p.9)
        self.assertEqual(len(g.val('run.setups')[0]['segments']), n + 1)
        g.row('seg_table', '10.0 CV')                                               # tap a segment: the Segment dialog
        g.ov('End')
        g.enter('60')
        g.ov('Length (CV)')
        g.enter('8')
        g.ov('OK')
        seg = g.val('run.setups')[0]['segments'][1]
        self.assertEqual((seg['end_pct'], seg['length_cv']), (60.0, 8.0))
        g.tap('unit')                                                               # CV -> mL: lengths shown at the column type CV (15 mL)
        self.assertIn('120.0 mL', [c[2] for c in g.cells('seg_table')])
        g.tap('unit')
        g.tap('unit')
        self.assertEqual(g.val('run.setups')[0]['unit'], 'CV')
        g.tap('tlc')                                                                # TLC to Gradient (manual p.9-10)
        g.ov('Create')
        self.assertIn('Rf Impurity 1', g.msg())                                     # required field
        g.ok()
        g.ov('Rf Impurity 1')
        g.enter('0.5')
        g.ov('Create')
        segs = g.val('run.setups')[0]['segments']
        self.assertEqual([(x['start_pct'], x['end_pct'], x['length_cv']) for x in segs], [(5.0, 5.0, 1.0), (5.0, 40.0, 10.0), (40.0, 40.0, 2.0)])
        self.assertEqual(g.val('run.setups')[0]['tlc']['imp1_1'], 0.5)
        g.tap('p_rack')                                                             # Rack panel: assign racks
        g.ov('Racks')
        g.ov('[x] Rack C')
        g.ov('OK')
        g.ov('Close')
        self.assertEqual(g.val('run.setups')[0]['racks'], ['A', 'B'])
        self.assertEqual(g.val('run.issues'), [])

    def test_start_a_purification(self):
        g = Rig()
        g.tap('start')                                                              # disabled parameters: the warning list
        self.assertEqual(g.overlay()['dialog'], 'd_issues')
        self.assertIn('Select a user', g.msg())
        g.ov('Close')
        g.solvents()
        g.setup_run()
        g.tap('solv_waste')                                                         # estimated consumption (manual p.13)
        self.assertIn('S1:', g.msg())
        self.assertIn('Waste:', g.msg())
        g.ov('Close')
        g.start(equil=True)                                                         # equilibration, Sample Load, Start Gradient
        self.assertEqual(g.val('status.light'), 'white')
        g.until(lambda: g.val('cycle.phase') != 'running')
        self.assertEqual((g.val('cycle.phase'), g.val('cycle.stage')), ('succeeded', 'finished_residual_solute'))
        self.assertEqual(g.text('stage'), 'Finished')
        self.assertEqual(g.val('status.light'), 'green')
        res = g.val('results.list')
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]['status'], 'finished')
        self.assertGreater(g.h.fractionmol.sum(), 0.0)
        self.assertTrue(res[0]['tubes'])

    def test_end_or_abort_a_run(self):
        g = prepared(equil=True)
        g.tap('start')
        self.assertEqual(g.val('cycle.stage'), 'equilibration')
        g.step(5.0)
        g.tap('end')                                                                # End during the equilibration: Skip Equilibration
        self.assertTrue(g.val('run.paused'))
        self.assertIn('Skip Equilibration', g.labels())
        g.ov('Skip Equilibration')
        g.until(lambda: g.val('run.pending_sample_load') == 0)
        g.tap('st_load')
        g.call(g.E, 'sample.configure', **rt.SAMPLE_EXAMPLE)
        g.ov('Start Gradient')
        g.step(30.0)
        x0 = g.val('collector.x_m'), g.val('collector.y_m')
        g.tap('end')                                                                # the arm returns home, System Paused
        self.assertTrue(g.val('run.paused'))
        self.assertEqual((g.val('collector.x_m'), g.val('collector.y_m')), (0.0, 0.33))
        self.assertNotIn('Skip Equilibration', g.labels())
        snap = g.h._products()
        g.step(10.0)
        self.assertEqual(snap, g.h._products())                                    # paused: nothing moves
        g.ov('Abort')                                                               # end without flushes
        self.assertEqual((g.val('cycle.phase'), g.val('cycle.stage')), ('cancelled', 'aborted'))
        self.assertGreater(g.val('channel.c0.pressure_pa'), 0.0)                   # the column stays pressurized
        del x0

    def test_purge_a_column_after_a_run(self):
        g = prepared()
        g.start(equil=False)
        g.step(20.0)
        g.tap('end')
        g.ov('Abort')
        self.assertGreater(g.val('channel.c0.pressure_pa'), 1.0)
        g.menu('m_flushes')                                                         # Menu > Flushes and Purge > Purge (manual p.24)
        self.assertIn('bar', g.text('fl_head'))
        g.row('purge_form', 'Column Type')
        g.ov(COL)
        g.tap('purge_go')
        self.assertEqual(g.val('cycle.stage'), 'purging')
        self.assertEqual(g.text('fl_state'), 'Purge in progress')
        g.until(lambda: g.val('cycle.phase') != 'running')
        self.assertEqual(g.val('channel.c0.pressure_pa'), 0.0)
        g.tap('purge_go')                                                           # nothing left to release
        self.assertIn('no pressure', g.msg().lower())
        g.ok()

    def test_shut_down(self):
        g = Rig()
        g.menu('m_shutdown')
        g.ov('Yes')                                                                 # manual p.18: Menu, Shut Down, Yes
        self.assertEqual(g.page(), ('down', 'safe'))
        self.assertTrue(g.val('system.shutdown'))
        self.assertIsNone(g.val('status.light'))
        self.assertFalse(g.touch(500, 300))
        self.assertTrue(g.switch('off'))                                            # the power switch on the left side
        self.assertEqual(g.page(), ('off', 'dark'))
        self.assertTrue(g.switch('on'))
        self.assertEqual(g.page(), ('boot', 'logo'))
        g.step(21.0)
        self.assertEqual(g.page(), ('main', 'run'))
        self.assertFalse(g.val('system.shutdown'))


# ---------------------------------------------------------------- run control (manual p.14-17, p.27)
class RunControl(unittest.TestCase):
    def test_pause_resume_hold_next_vessel_uv_zero_extend(self):
        g = prepared()
        g.start(equil=False)
        g.step(30.0)
        self.assertEqual(g.text('start'), 'Pause')
        g.tap('start')
        self.assertTrue(g.val('run.paused'))
        self.assertEqual(g.text('start'), 'Resume')
        self.assertEqual(g.val('status.light'), 'orange')                           # user action on channel 1
        self.assertIn('Paused by the user', g.text('state_msg'))
        t = g.val('cycle.gradient_s')
        g.step(20.0)
        self.assertEqual(g.val('cycle.gradient_s'), t)
        g.tap('st_resume')
        self.assertFalse(g.val('run.paused'))
        g.tap('hold')                                                               # isocratic segment: the gradient clock stops
        self.assertTrue(g.val('run.hold'))
        t = g.val('cycle.gradient_s')
        g.step(10.0)
        self.assertEqual(g.val('cycle.gradient_s'), t)
        self.assertGreater(g.val('flow.actual_m3_s'), 0.0)
        g.tap('hold')
        g.step(5.0)
        self.assertGreater(g.val('cycle.gradient_s'), t)
        g.until(lambda: g.val('collection.fraction_number') > 0, limit=600.0)
        n = g.val('collection.fraction_number')
        g.tap('next_vessel')                                                        # manual fractionation
        g.step(1.0)
        self.assertEqual(g.val('collection.fraction_number'), n + 1)
        g.tap('uv_zero')                                                            # manual UV zero (on by default)
        self.assertEqual((g.val('uv.absorbance_1'), g.val('uv.absorbance_2')), (0.0, 0.0))
        self.assertGreater(max(g.val('uv.zero_au')), 0.0)
        end = g.h.methods[0]['gradient'][-1]['time_s']
        g.tap('extend')                                                             # +25 % of the gradient length
        self.assertAlmostEqual(g.h.methods[0]['gradient'][-1]['time_s'], end * 1.25, places=6)
        g.until(lambda: g.val('cycle.phase') != 'running')
        self.assertEqual(g.val('cycle.phase'), 'succeeded')

    def test_confirm_cleanup_dialog_flushes_and_auto_extend(self):
        g = prepared()
        settings(g, {'Confirm Cleanup': 'On', 'Line Flush': 'To Waste', 'Detector Flush': 'To Waste'})
        g.tap('menu_back') if g.page()[1] == 'menu' else None
        g.start(equil=False)
        g.until(lambda: g.val('run.awaiting') == 'gradient_completed')             # Gradient Run Completed (manual p.17)
        self.assertEqual(g.text('state_msg'), 'Gradient Run Completed')
        self.assertEqual(g.val('status.light'), 'orange')
        t = g.val('cycle.gradient_s')
        g.step(30.0)
        self.assertEqual(g.val('cycle.gradient_s'), t)                              # waits for the operator
        g.tap('st_extend')
        self.assertEqual(g.val('cycle.stage'), 'gradient')
        g.until(lambda: g.val('run.awaiting') == 'gradient_completed')
        g.tap('st_cleanup')
        stages = []
        g.until(lambda: stages.append(g.val('cycle.stage')) or g.val('cycle.phase') != 'running')
        self.assertEqual([s for k, s in enumerate(stages) if k == 0 or s != stages[k - 1]][:3], ['line_flush', 'purging', 'detector_flush'])
        self.assertEqual(g.val('cycle.phase'), 'succeeded')

    def test_auto_extend_extends_while_collecting(self):
        g = prepared()
        settings(g, {'Auto Extend': 'On'})
        g.tap('menu_back')
        for length in ('10.0 CV', '2.0 CV'):                                       # a short gradient: the product still elutes at its end
            g.row('seg_table', length)
            g.ov('Length (CV)')
            g.enter('1')
            g.ov('OK')
        g.tap('p_coll')
        g.ov('Threshold')
        g.enter('2')
        g.ov('Close')
        g.start(equil=False)
        end = g.h.methods[0]['gradient'][-1]['time_s']
        g.until(lambda: g.h.term['extended'] or g.val('cycle.stage') != 'gradient')
        self.assertTrue(g.h.term['extended'])                                       # +25 % of the gradient with the final conditions
        self.assertAlmostEqual(g.h.methods[0]['gradient'][-1]['time_s'], 1.25 * end, places=6)
        self.assertEqual(g.h.methods[0]['gradient'][-1]['strong_fraction'], g.h.methods[0]['gradient'][-2]['strong_fraction'])
        g.until(lambda: g.val('cycle.phase') != 'running')

    def test_solvent_monitoring_notice_and_pause(self):
        g = Rig()
        settings(g, {'Solvent Monitoring': 'On'})
        g.tap('menu_back')
        g.solvents()
        g.menu('m_solvents')
        g.row('sol1_form', 'Capacity')
        g.enter('2000')
        g.row('sol1_form', 'Level')
        g.enter('300')
        g.row('sol2_form', 'Capacity')
        g.enter('2000')
        g.row('sol2_form', 'Level')
        g.enter('2000')
        g.tap('sol_back')
        g.tap('menu_back')
        g.setup_run()
        g.tap('equil')
        g.ov('Off')
        g.start(equil=False)
        self.assertIn('S1 Heptane below 20 %', g.val('notices'))                    # 300 / 2000 mL
        g.until(lambda: g.val('run.paused'), limit=2000.0)                          # 10 % left: paused, refill prompt
        self.assertEqual(g.val('run.pause_reason'), 'Refill solvent S1')
        self.assertIn('Refill solvent S1', g.text('state_msg'))
        g.menu('m_solvents')
        g.row('sol1_form', 'Level')                                                 # refilled
        g.enter('2000')
        g.tap('sol_back')
        g.tap('menu_back')
        g.tap('st_resume')
        self.assertFalse(g.val('run.paused'))

    def test_two_channels_and_create_run(self):
        g = prepared()
        g.start(equil=False)
        g.tap('ch2')
        self.assertEqual(g.text('stage'), '')
        self.assertFalse(g.widgets()['start']['enabled'])                           # channel 1 is processing
        g.tap('ch1')
        g.until(lambda: g.val('cycle.phase') != 'running')
        g.menu('m_results')                                                         # Results > Create Run on channel 2
        g.row('res_list', 'System Owner')
        g.tap('res_create')
        g.ov('Channel 2')
        self.assertEqual(g.page(), ('main', 'run'))
        self.assertEqual(g.frame()['ui']['vars']['ch'], 1)
        s1, s0 = g.val('run.setups')[1], g.val('run.setups')[0]
        self.assertEqual((s1['column_type'], s1['flow_ml_min'], s1['segments']), (s0['column_type'], s0['flow_ml_min'], s0['segments']))


def settings(g, changes, login=True):
    """Menu > System Settings (System Owner), one row per setting."""
    g.menu('m_settings')
    if login and g.overlay():
        g.owner_login()
    assert g.page() == ('main', 'settings'), g.page()
    for label, value in changes.items():
        g.row('set_form', label)
        if g.overlay()['kind'] == 'keyboard':
            g.enter(value)
        else:
            g.ov(value)
    g.tap('adm_close')


# ---------------------------------------------------------------- Results, Data Administration, Settings, Maintenance, Flushes
class Views(unittest.TestCase):
    def test_results_search_favorite_analysis_report_export(self):
        g = prepared()
        g.start(equil=False)
        g.until(lambda: g.val('cycle.phase') != 'running')
        g.menu('m_results')
        self.assertEqual(len(g.cells('res_list')), 1)
        g.tap('res_analysis')
        self.assertIn('Select a report', g.msg())
        g.ok()
        g.row('res_list', 'System Owner')
        g.tap('res_star')
        self.assertTrue(g.val('results.list')[0]['favorite'])
        self.assertEqual(g.cells('res_list')[0][0], '*')
        g.tap('res_search')                                                         # filter on the chemistry type
        g.ov('Chemistry')
        g.ov('Reversed phase')
        g.ov('Search')
        self.assertEqual(g.cells('res_list'), [])
        g.tap('res_clear')
        self.assertEqual(len(g.cells('res_list')), 1)
        g.tap('res_analysis')                                                       # chromatogram and fractions
        w = g.widgets()
        self.assertTrue(w['an_plot']['series'][0]['points'])
        self.assertIn('Fractions: A1', w['an_frac']['text'])
        g.tap('an_pic')
        g.ok()
        g.tap('an_pic')
        g.ok()
        self.assertEqual(len(g.val('results.list')[0]['pictures']), 2)
        g.tap('an_view')
        g.row('pic_list', 'Picture 1')
        g.tap('pic_del')
        self.assertEqual(len(g.val('results.list')[0]['pictures']), 1)
        g.tap('pic_back')
        g.tap('an_back')
        g.tap('res_setup')                                                          # Report Setup: content, note, Apply
        g.row('rep_form', 'Log')
        g.ov('Off')
        g.row('rep_form', 'Note')
        g.enter('ok')
        g.row('rep_form', 'Apply Report Setup')
        g.ov('On')
        g.tap('rep_save')
        r = g.val('results.list')[0]
        self.assertEqual((r['note'], r['apply_report_setup'], r['report']['log']), ('ok', True, False))
        g.tap('res_export')
        self.assertIn('exported', g.msg())
        g.ok()
        self.assertTrue(any(f.endswith('.pdf') for f in g.val('usb.files')))

    def test_data_administration(self):
        g = Rig()
        g.menu('m_admin')
        g.owner_login('0000')                                                       # wrong password: refused, still locked
        self.assertIn('password is not correct', g.msg())
        g.ok()
        self.assertFalse(g.val('owner.unlocked'))
        g.owner_login()                                                             # the log-in dialog is still open
        self.assertEqual(g.page(), ('main', 'adm_columns'))
        g.tap('col_add')                                                            # a user-defined column type
        g.ov('Name')
        g.enter('My column')
        g.ov('Column Volume')
        g.enter('20')
        g.ov('Save')
        self.assertIn('My column', [c['name'] for c in g.val('library.columns')])
        g.row(f'adm_columns_list', COL)                                             # Biotage column: Visible only
        g.tap('col_edit')
        g.ov('Visible')
        g.ov('Off')
        g.ov('Save')
        self.assertFalse(next(c for c in g.val('library.columns') if c['name'] == COL)['visible'])
        g.tap('col_del')
        self.assertIn('cannot be edited or deleted', g.msg())
        g.ok()
        g.row('adm_columns_list', 'My column')
        g.tap('col_del')
        self.assertNotIn('My column', [c['name'] for c in g.val('library.columns')])
        g.tap('side_adm_solvents')
        g.tap('sol_add')
        g.ov('Name')
        g.enter('MTBE')
        g.ov('Save')
        self.assertIn('MTBE', [c['name'] for c in g.val('library.solvents')])
        g.tap('side_adm_racks')                                                     # vessel volume only lowered; Reset
        g.row('adm_racks_list', '48x13x100 mm (9 mL)')
        g.tap('rack_edit')
        g.ov('Vessel Volume')
        g.enter('12')
        g.ov('Save')
        self.assertIn('only be lowered', g.msg())
        g.ok()
        g.ov('Vessel Volume')
        g.enter('8')
        g.ov('Save')
        self.assertEqual(next(r for r in g.val('library.racks') if r['name'].startswith('48'))['vessel_volume_ml'], 8.0)
        g.tap('rack_edit')
        g.ov('Reset')
        self.assertEqual(next(r for r in g.val('library.racks') if r['name'].startswith('48'))['vessel_volume_ml'], 9.0)
        g.tap('side_adm_users')
        g.tap('user_add')
        g.ov('Name')
        g.enter('Chemist A')
        g.ov('Save')
        g.row('adm_users_list', 'Chemist A')
        g.tap('user_edit')                                                          # rename
        g.ov('Name')
        g.enter('Chemist B')
        g.ov('Save')
        names = [u['name'] for u in g.val('library.users')]
        self.assertIn('Chemist B', names)
        self.assertNotIn('Chemist A', names)
        g.row('adm_users_list', 'System Owner')
        g.tap('user_del')
        self.assertIn('System Owner account is required', g.msg())
        g.ok()
        g.tap('adm_close')
        self.assertFalse(g.val('owner.unlocked'))
        self.assertEqual(g.page(), ('main', 'menu'))

    def test_result_administration_delete(self):
        g = prepared()
        g.start(equil=False)
        g.until(lambda: g.val('cycle.phase') != 'running')
        g.menu('m_admin')
        g.owner_login()
        g.tap('side_adm_results')
        g.row('resdel_form', 'Results')
        g.ov('[ ] ' + g.val('results.list')[0]['name'])
        g.ov('OK')
        g.tap('resdel_go')
        g.ov('Yes')
        self.assertEqual(g.val('results.list'), [])

    def test_system_settings_pressure_unit_and_date(self):
        g = prepared()
        settings(g, {'Pressure Unit': 'psi'})
        g.tap('menu_back')
        g.start(equil=False)
        g.step(20.0)
        self.assertIn('psi', g.text('pflow'))
        g.tap('end')
        g.ov('Abort')
        g.menu('m_settings')
        g.owner_login()
        g.row('set_form', 'Date (yyyy-mm-dd)')                                       # restart required
        g.enter('2026-05-01')
        self.assertEqual(g.overlay()['dialog'], 'd_restart')
        self.assertEqual(g.val('settings.pending'), {'date': '2026-05-01'})
        g.ov('Shut Down')
        self.assertEqual(g.page()[0], 'down')
        g.switch('off')
        g.switch('on')
        g.step(21.0)
        self.assertTrue(g.val('system.datetime').startswith('2026-05-01'))

    def test_maintenance_backup_restore_logs_calibrate(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', uv_offset_au=[0.01, 0.02], uv_gain=0.0)
        g.menu('m_maint')
        g.owner_login()
        g.tap('mt_logs')
        self.assertIn('logs', g.msg())
        g.ok()
        g.tap('mt_uvcal')                                                           # zero on the clean flow cell
        g.ov('Calibrate')
        g.ok()
        self.assertEqual(g.val('uv.calibration')['offset'], [0.01, 0.02])
        g.tap('mt_restore')
        self.assertIn('No backup file', g.msg())
        g.ok()
        g.tap('mt_backup')
        g.ov('Yes')
        self.assertTrue(any('backup' in f for f in g.val('usb.files')))
        self.assertTrue(g.val('system.restart_required'))
        g.ov('Shut Down')
        self.assertEqual(g.page()[0], 'down')
        g.switch('off')
        g.switch('on')
        g.step(21.0)
        g.menu('m_maint')
        g.owner_login()
        g.tap('mt_restore')
        g.ov('Restore')
        g.ov('Yes')
        self.assertIn('restore has been completed', g.msg())

    def test_flush_and_air_flush(self):
        g = Rig()
        g.solvents()
        g.menu('m_flushes')
        g.row('flush_form', 'S4')                                                   # bypass, 30 mL: 100 % S1 instead of the preset S4
        g.enter('0')
        g.row('flush_form', 'S1')
        g.enter('100')
        r0 = float(g.h.reservoir[0])
        g.tap('flush_go')
        self.assertEqual(g.val('cycle.stage'), 'flush')
        g.until(lambda: g.val('cycle.phase') != 'running')
        self.assertAlmostEqual((r0 - float(g.h.reservoir[0])) / rt.ML, 30.0, places=6)
        g.row('air_form', 'Column Type')                                            # air flush: default volume of the column type
        g.ov(COL)
        g.tap('air_go')
        self.assertEqual(g.val('cycle.stage'), 'air_flush')
        g.until(lambda: g.val('cycle.phase') != 'running')
        self.assertAlmostEqual(float(g.h.colfluid[0].sum()), 0.0, places=12)

    def test_unsupported_functions_show_the_device_message(self):
        g = Rig()
        g.tap('sol_cd')                                                             # solvents C / D (the modifier M is supported since 1.2.0)
        self.assertIn('not', g.msg())
        g.ok()
        self.assertTrue(g.frame()['ui']['unsupported'])
        g.menu('m_settings')
        g.owner_login()
        for label, text in (('Network', 'No network connection'), ('Licenses: Install', 'No license file'), ('External detector', 'No external detector'),
                            ('Biotage Solvent Detector', 'No solvent detector')):
            before = runtime_state(g.h)
            g.row('set_form', label)
            self.assertIn(text, g.msg())
            g.ok()
            self.assertEqual(before, runtime_state(g.h))


class SyncFixes(unittest.TestCase):
    """Sync audit 2026-10-19: caller rules, the UV offset at the baseline, the modifier (M), automatic phase conversion, RFID rack detection, rack
    reinsertion and Empty."""
    def test_a_panel_started_run_belongs_to_the_caller_and_another_agent_cannot_end_it(self):
        g = prepared(equil=True)
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.tap('start')                                                              # equilibration started from the screen
        self.assertEqual(g.val('cycle.stage'), 'equilibration')
        op = g.h.operation_id
        self.assertEqual(g.h._owners.get(op), 'agent')
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        g.until(lambda: g.val('run.pending_sample_load') is not None)
        g.tap('st_load')
        g.call(g.E, 'sample.configure', **rt.SAMPLE_EXAMPLE)
        g.ov('Start Gradient')                                                      # the run started from the Sample Load dialog
        op = g.h.operation_id
        self.assertEqual(g.h._owners.get(op), 'agent')
        g.step(10.0)
        before = runtime_state(g.h)
        A, g.A = g.A, B                                                             # B's gestures on the same screen
        NOT_PERMITTED = "Not permitted: another user's run is in progress."
        self.assertFalse(g.tap('end'))                                              # End pauses first: refused
        self.assertEqual(g.msg(), NOT_PERMITTED)                                    # the refusal shows on the screen
        g.ov('OK')
        self.assertFalse(g.tap('start'))                                            # Pause
        self.assertEqual(g.msg(), NOT_PERMITTED)
        g.ov('OK')
        g.A = A
        for cap, args in (('acquisition.stop', {}), ('run.end', dict(option='abort')), ('run.pause', {}),
                          ('run.hold', dict(on=True)), ('run.extend', {}), ('run.setup', dict(channel=0, settings=dict(flow_ml_min=20)))):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(runtime_state(g.h), before)
        self.assertEqual(g.val('cycle.phase'), 'running')
        self.assertEqual(g.A.cancel(op, 'a1')['status'], 'cancelled')                # the owner may cancel it

    def test_uv_offset_shows_at_the_baseline(self):
        g = Rig()
        g.call(g.E, 'sensor.configure_error', uv_offset_au=[0.01, 0.02], uv_gain=0.0)
        self.assertEqual((g.val('uv.absorbance_1'), g.val('uv.absorbance_2')), (0.01, 0.02))   # no absorbing species yet
        g.step(5.0)
        self.assertEqual(g.val('uv.absorbance_1'), 0.01)

    def run_with_modifier(self, pct):
        g = prepared()
        g.menu('m_solvents')
        g.row('sol3_form', 'S3')
        g.ov('Methanol')
        g.tap('sol_back')
        g.tap('menu_back')
        if pct:
            g.tap('sol_m')                                                          # M / Modifier and its percentage (manual p.9)
            g.ov('M')
            g.ov('Methanol')
            g.ov('M %')
            g.enter(str(pct))
            g.ov('Close')
        self.assertEqual(g.val('run.issues'), [])
        g.r0 = g.h.reservoir.copy()
        g.start(equil=False)
        g.step(240.0)
        return g

    def test_a_modifier_is_pumped_and_elutes_faster(self):
        plain, mod = self.run_with_modifier(0), self.run_with_modifier(10)
        s = mod.val('run.setups')[0]
        self.assertEqual((s['modifier'], s['modifier_pct']), ('Methanol', 10.0))
        self.assertEqual(mod.h.methods[0]['modifier']['inlet'], 2)
        used = [float(x.r0[k] - x.h.reservoir[k]) for x in (plain, mod) for k in range(3)]
        self.assertEqual(used[2], 0.0)                                              # no modifier: S3 unused
        self.assertAlmostEqual(used[5] / (used[3] + used[4] + used[5]), 0.10, places=9)   # 10 % of the pumped flow from S3
        self.assertLess(mod.val('truth.column_amount_mol'), plain.val('truth.column_amount_mol'))   # methanol is stronger than ethyl acetate
        self.assertGreater(mod.val('run.estimate')['inlets_ml'][2], 0.0)            # Solvents and Waste: S3 consumption
        g = prepared()
        g.call(g.A, 'run.setup', channel=0, settings=dict(modifier='Methanol', modifier_pct=5.0))   # not assigned to an inlet
        self.assertIn('The modifier is not assigned to an inlet', g.val('run.issues'))

    def test_automatic_phase_conversion_between_channels(self):
        g = prepared()
        g.start(equil=False)
        g.until(lambda: g.val('cycle.phase') != 'running')                         # normal-phase run on channel 1 (silica)
        for inlet, name in ((2, 'Water'), (3, 'Methanol')):
            g.call(g.A, 'solvents.setup', inlet=inlet, solvent=name)
        g.call(g.E, 'column.configure', **dict(rt.COLUMN_EXAMPLE, channel=1, solvent_pair=[2, 3]))
        g.call(g.A, 'run.setup', channel=1, settings=dict(user='System Owner', column_type='Sfar C18 D 12g', flow_ml_min=12, solvent_a='Water',
                                                          solvent_b='Methanol', equilibration=False))
        self.assertEqual(g.val('run.issues'), []) if g.val('channel.selected') == 1 else None
        r = [float(x) for x in g.h.reservoir]
        waste = float(g.h.wastefluid.sum())
        g.call(g.A, 'run.prepare', channel=1)                                        # reversed phase after normal phase on the other channel
        self.assertEqual(g.val('cycle.stage'), 'conversion')
        g.tap('ch2')
        self.assertIn('Conversion', g.text('stage'))
        g.until(lambda: g.val('cycle.stage') == 'sample_load', limit=120.0, dt=0.5)
        self.assertAlmostEqual((r[1] - float(g.h.reservoir[1])) / rt.ML, rt.CONVERSION_ML, places=6)   # 100 % of the previous strong solvent
        self.assertAlmostEqual((r[3] - float(g.h.reservoir[3])) / rt.ML, rt.CONVERSION_ML, places=6)   # then 100 % of the current one
        self.assertAlmostEqual((float(g.h.wastefluid.sum()) - waste) / rt.ML, 2 * rt.CONVERSION_ML, places=6)
        self.assertEqual(g.val('run.pending_sample_load'), 1)
        h = prepared()                                                              # same chemistry or a modifier: no conversion
        h.call(h.A, 'run.prepare', channel=0)
        self.assertEqual(h.val('cycle.stage'), 'sample_load')

    def full_run(self):
        g = prepared()
        g.start(equil=False)
        g.until(lambda: g.val('cycle.phase') != 'running')
        self.assertGreater(g.val('rack.vessels_used')[0], 0)
        return g

    def test_rfid_on_clears_a_reinserted_rack_of_a_previous_run(self):
        g = self.full_run()
        self.assertEqual(g.val('rack.type'), rt.RFID_TYPE)
        total = g.val('truth.fractions_amount_mol')
        r = g.call(g.A, 'rack.reinsert', rack='A', vessels_emptied=True)
        self.assertTrue(r['cleared'])
        self.assertEqual(g.val('rack.vessels_used')[0], 0)
        self.assertEqual(float(g.h.fractionfluid[:48].sum()), 0.0)
        self.assertAlmostEqual(g.val('truth.fractions_amount_mol'), total, places=15)   # the fractions left with their vessels (collected)

    def test_rfid_off_needs_the_rack_type_and_empty(self):
        g = self.full_run()
        settings(g, {'RFID Rack Detection': 'Off'})
        g.tap('menu_back')
        self.assertIn('Select the rack type', g.val('run.issues'))
        g.tap('p_rack')
        g.ov('Rack Type')
        g.ov(rt.RFID_TYPE)
        self.assertNotIn('Select the rack type', g.val('run.issues'))
        n = g.val('rack.vessels_used')[0]
        self.assertFalse(g.call(g.A, 'rack.reinsert', rack='A', vessels_emptied=True)['cleared'])   # the reader is off: not cleared
        self.assertEqual(g.val('rack.vessels_used')[0], n)
        self.assertTrue(g.ov('Empty A'))                                            # Rack panel: Empty
        self.assertEqual(g.val('rack.vessels_used')[0], 0)

    def test_racks_during_a_run(self):
        g = prepared()
        g.start(equil=False)
        g.until(lambda: g.val('rack.vessels_used')[0] > 0, limit=600.0)
        self.assertEqual(g.call(g.A, 'rack.reinsert', expect='rejected', rack='A', vessels_emptied=False)['error']['code'], 'BUSY')   # pause first
        g.tap('start')                                                              # Pause
        self.assertTrue(g.val('run.paused'))
        self.assertFalse(g.call(g.A, 'rack.reinsert', rack='A', vessels_emptied=False)['cleared'])   # used by the run in progress
        self.assertTrue(g.call(g.A, 'rack.reinsert', rack='C', vessels_emptied=False)['cleared'])    # an unused rack

    def test_another_agent_may_take_a_rack_off_a_paused_run(self):
        """Sweep 2026-10-19: taking a rack off and putting it back is physical (not refused by the session rules); the instrument's own rule
        (the run must be paused) still applies."""
        g = prepared()
        g.start(equil=False)
        g.until(lambda: g.val('rack.vessels_used')[0] > 0, limit=600.0)
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        self.assertEqual(g.call(B, 'rack.reinsert', expect='rejected', rack='C', vessels_emptied=False)['error']['code'], 'BUSY')   # not paused
        g.tap('start')                                                              # the owner pauses
        self.assertTrue(g.call(B, 'rack.reinsert', rack='C', vessels_emptied=False)['cleared'])
        self.assertEqual(g.call(B, 'rack.empty', expect='rejected', rack='A')['error']['code'], 'ACCESS_DENIED')   # the software record: refused
        A, g.A = g.A, B
        self.assertTrue(g.switch('off'))                                           # the power switch is physical: not refused (a power loss)
        g.A = A
        self.assertFalse(g.val('power.enabled'))
        self.assertNotEqual(g.val('cycle.phase'), 'running')


# ---------------------------------------------------------------- equivalence, refusals, time-outs
class Contracts(unittest.TestCase):
    def test_panel_and_direct_calls_give_the_same_runtime_state(self):
        g = prepared()
        g.start(equil=False)
        g.step(60.0)
        g.tap('end')
        g.ov('Cleanup')
        g.until(lambda: g.val('cycle.phase') != 'running')
        d = Rig()
        for k, s in ((0, 'Heptane'), (1, 'Ethyl acetate')):
            d.call(d.A, 'solvents.setup', inlet=k, solvent=s)
        d.call(d.A, 'run.setup', channel=0, settings=dict(user='System Owner', column_type=COL))
        d.call(d.A, 'run.setup', channel=0, settings=dict(flow_ml_min=30))
        d.call(d.A, 'run.setup', channel=0, settings=dict(solvent_a='Heptane'))
        d.call(d.A, 'run.setup', channel=0, settings=dict(solvent_b='Ethyl acetate'))
        d.call(d.A, 'run.setup', channel=0, settings=dict(uv1_nm=300.0))
        d.call(d.A, 'run.setup', channel=0, settings=dict(uv2_nm=350.0))
        d.call(d.A, 'run.setup', channel=0, settings=dict(equilibration=False))
        d.call(d.A, 'run.prepare', channel=0)
        d.call(d.E, 'sample.configure', **rt.SAMPLE_EXAMPLE)
        d.call(d.A, 'run.begin')
        d.step(60.0)
        d.call(d.A, 'run.pause')
        d.call(d.A, 'run.end', option='cleanup')
        d.until(lambda: d.val('cycle.phase') != 'running')
        self.assertEqual(runtime_state(g.h), runtime_state(d.h))
        np.testing.assert_array_equal(g.h.fractionmol, d.h.fractionmol)

    def test_power_off_gate_and_switch(self):
        g = Rig(on=False)
        self.assertEqual(g.page(), ('off', 'dark'))
        self.assertFalse(g.touch(500, 300))
        self.assertFalse(g.switch('off'))                                           # already off
        self.assertTrue(g.switch('on'))
        self.assertEqual(g.page(), ('boot', 'logo'))
        self.assertFalse(g.touch(500, 300) and g.page()[0] != 'boot')
        g.step(19.0)
        self.assertEqual(g.page()[0], 'boot')                                       # 20 s start-up
        g.step(2.0)
        self.assertEqual(g.page(), ('main', 'run'))
        self.assertEqual(g.val('status.light'), 'white')

    def test_refusals_show_the_device_message_and_change_nothing(self):
        g = Rig()
        g.solvents()
        g.menu('m_flushes')
        g.row('flush_form', 'S1')
        g.enter('50')
        before = runtime_state(g.h)
        g.tap('flush_go')                                                           # 50 + 100 % (S4 preset)
        self.assertIn('add up to 100 %', g.msg())
        g.ok()
        self.assertEqual(before, runtime_state(g.h))
        g.tap('fl_back')
        g.tap('menu_back')
        g.setup_run()
        g.tap('equil')
        g.ov('Off')
        g.tap('start')
        g.ov('Start Gradient')                                                      # no sample on the column
        self.assertIn('Load the sample', g.msg())
        g.ok()
        self.assertEqual(g.val('cycle.phase'), 'idle')
        g.tap('st_load')
        g.ov('Cancel')
        self.assertIsNone(g.val('run.pending_sample_load'))
        g.menu('m_admin')
        g.ov('Cancel')
        before = runtime_state(g.h)
        g.call(g.A, 'columns.delete', expect='rejected', name=COL)                  # locked without System Owner
        self.assertEqual(before, runtime_state(g.h))

    def test_light_strip_colors(self):
        g = prepared()
        self.assertEqual(g.disp().get('kind'), 'touch_tft')
        led = g.frame()['displays']['light_strip']
        self.assertEqual((led['on'], led['color']), (True, [255, 255, 255]))
        g.tap('ch2')
        g.tap('start')
        self.assertEqual(g.overlay()['dialog'], 'd_issues')
        g.ov('Close')
        g.tap('ch1')
        g.tap('start')
        self.assertEqual(g.frame()['displays']['light_strip']['color'], [250, 140, 20])   # Sample Load: user action on channel 1
        settings_led = {'LED Light': 'Off'}
        g.ov('Cancel')
        settings(g, settings_led)
        self.assertFalse(g.frame()['displays']['light_strip']['on'])


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        g = prepared()
        g.start(equil=False)
        g.step(120.0)
        a = g.h.panel.png_bytes(SCR)[0]
        b = copy.deepcopy(g.h).panel.png_bytes(SCR)[0]
        self.assertEqual(a, b)
        self.assertEqual(g.png(), a)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'run_view.png').write_bytes(a)
        g.tap('end')
        (OUT / 'system_paused.png').write_bytes(g.png())
        g.ov('Cleanup')
        g.until(lambda: g.val('cycle.phase') != 'running')
        (OUT / 'home.png').write_bytes(g.png())
        g.tap('menu')
        (OUT / 'menu.png').write_bytes(g.png())
        g.tap('m_settings')
        g.owner_login('0')
        (OUT / 'error.png').write_bytes(g.png())
        g.ok()
        g.ov('Cancel')
        g.tap('m_results')
        g.row('res_list', 'System Owner')
        g.tap('res_analysis')
        (OUT / 'analysis.png').write_bytes(g.png())
        import hashlib
        (OUT / 'renders.json').write_text(json.dumps({f.name: dict(sha256=hashlib.sha256(f.read_bytes()).hexdigest(), size=[1024, 768])
                                                      for f in sorted(OUT.glob('*.png'))}, indent=1) + '\n')

    def test_spec_and_contract(self):
        spec = rt.PANEL_SPEC
        caps = set(rt.ALL_CAPS)
        self.assertEqual(rt._panel_ui.check_spec(spec, caps), [])
        self.assertTrue(set(rt._panel_ui.spec_capabilities(spec)) <= caps)
        for pid, p in spec['screens']['main']['nav']['pages'].items():
            self.assertTrue(p.get('evidence'), pid)
        meta = rt.export_biotage_selekt_contract_metadata()
        self.assertEqual(set(meta['capabilities']), caps)
        c = json.loads((rt.ASSET_DIR / 'asset.contract.json').read_text())
        if c['asset_version'] != '1.0.0':                                            # after the contract patch
            self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
            self.assertTrue(caps <= set(c['capabilities']))
            self.assertIn('chemistry_lab.panel_ui', c['extensions'])
            self.assertIsNone(c['artifacts']['panel_engine']['sha256'])

    def test_all_survey_procedures_done(self):
        names = {m for m in dir(KeyProcedures) if m.startswith('test_')}
        for t in SURVEY_TASKS:
            self.assertIn('test_' + t.lower().replace(' ', '_'), names, t)


if __name__ == '__main__':
    unittest.main()
