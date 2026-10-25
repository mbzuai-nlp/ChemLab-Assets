"""front-panel tests for CHEM-031 biotage_extrahera (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_vacuum.py):
python3 -m pytest tests/test_panel_ui_biotage_extrahera.py -q

Every gesture goes through the session API (panel.touch / panel.switch, the runtime's normal invoke path); widgets are found by id or text in the
structured frame (panel.frame, evaluator) and tapped at their centre.  Themes: the seven key procedures of the menu survey; the software functions
(Data Administration libraries and solvent locks, method editor, System Administration reminders, Maintenance settings and instrument type, pump
calibration, flush, Manual Control interlocks, reports, About, Shut Down); panel = direct capability (same runtime state); refusals shown as the
device's message; time-outs (start-up screen, alarm flashing); render determinism and the evidence PNGs; device commands and contract entries."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
import basic40_runtime_vacuum as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'spe'
OUT = ROOT / 'outputs/panel_ui/biotage_extrahera'
CONTRACT = rt.EXTRAHERA_ASSET_DIR / 'asset.contract.json'
SCREEN = 'screen'
SURVEY_TASKS = ['create a new SPE method', 'prepare and start a run', 'prime a solvent inlet', 'pause and resume a run',
                'apply pressure manually for method development', 'shut down the system', 're-initialize after an abort or maintenance']
DONE_TASKS = set()
LIQUIDS = {'S1': 400e-6, 'S2': 400e-6, 'S3': 400e-6, 'S4': 400e-6, 'S5': 400e-6, 'P6-1': 90e-6, 'sample': 200e-6}


class Rig:
    """One Extrahera with an agent and an evaluator session; gestures only through panel.* capabilities."""

    def __init__(self, ready=True, liquids=True, utilities=True, itype='24 Positions, Column Rack'):
        self.scene = []
        self.h = rt.ExtraheraHost(scene_adapter=lambda i, u: self.scene.append(copy.deepcopy(u)))
        self.h.create_instance(INST)
        self.h.register_principal('agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        if utilities:
            self.h.connect_vacuum_source(INST, 'shared_pc3001')
            self.h.set_utilities(INST, vacuum_ready=True, compressed_air_ready=True, nitrogen_ready=True, evidence='test utilities')
        if liquids:
            self.h.initialize_liquids(INST, dict(LIQUIDS), evidence='test inventories')
        if ready:
            assert self.switch('on')
            self.step(rt.EXTRAHERA_BOOT_S + 1)
            if itype:
                self.call(self.A, 'settings.configure', settings={'instrument_type': itype})

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect in ('succeeded', 'accepted') else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        t = 0.0
        while t < dt - 1e-9:
            d = min(600.0, dt - t)
            self.h.step(d)
            t += d

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][SCREEN]

    def screen(self):
        return self.frame()['screen']

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

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='mains_switch', position=pos)['accepted']

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label and b['id'] != 'field':                    # the input field shows the typed text
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def row(self, wid, first):
        """Tap the row of a list or form widget whose first cell is this text (or '! ' + text), dragging the rows until it shows."""
        for _ in range(30):
            w = self.widgets()[wid]
            firsts = [c[0] for c in w['cells']]
            for k, cells in enumerate(w['cells']):
                if cells[0] in (first, '! ' + first) or (w['type'] == 'list' and first in cells):
                    return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, firsts)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def scroll_top(self, wid):
        for _ in range(30):
            w = self.widgets()[wid]
            if w['scroll'] == 0:
                return
            x, y, ww, hh = w['rect']
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)

    def cells(self, wid):
        """First cell -> second cell of every row of a list or form widget (all rows, scrolling through)."""
        self.scroll_top(wid)
        out = {}
        for _ in range(30):
            w = self.widgets()[wid]
            out.update({c[0]: (c[1] if len(c) > 1 else None) for c in w['cells']})
            if w['scroll'] + len(w['cells']) >= w['count']:
                break
            x, y, ww, hh = w['rect']
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        self.scroll_top(wid)
        return out

    def type(self, text):
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov
        labels = {b['label'] for b in ov['buttons']}
        if ov['field']:
            self.ov('Del')
            while self.overlay()['field']:
                self.ov('Del')
        numeric = '7' in labels and 'q' not in labels and 'Q' not in labels
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
                if mode != 'lower':
                    self.ov('abc' if mode == 'sym' else 'Shift')
                self.ov(ch)

    def set(self, wid, label, value):
        """Set one form row through its editor: a choice list option, or typed text / number and OK."""
        assert self.row(wid, label), label
        ov = self.overlay()
        if ov['kind'] == 'select':
            return self.ov(value)
        self.type(str(value))
        return self.ov('OK')

    def lib_id(self, kind, name):
        return next(e['id'] for e in self.val('library.entries')[kind] if e['name'] == name)

    def method(self, name):
        return next(m for m in self.val('methods.list') if m['name'] == name)

    def home(self):
        """Back to the main menu by the views' Back buttons."""
        for _ in range(8):
            if self.page() == ('gui', 'main'):
                return
            self.tap('back')
        raise AssertionError(self.page())


def build_spe_method(r, name='Test SPE'):
    """Manage Methods > User methods > New > SPE; fill the fields with the exclamation mark; Save (p.25-28)."""
    r.tap('m_methods')
    assert r.page() == ('gui', 'methods')
    r.tap('mm_new')
    r.tap('type_SPE')
    assert r.page() == ('gui', 'me_sample')
    head = r.cells('me_head')
    assert head['! Method name'] == '' and '! Extraction media' in head
    r.set('me_head', 'Method name', name)
    r.set('me_head', 'Sample plate/rack', '12 x 75 mm Test Tubes, 24')
    r.set('me_head', 'Extraction media', '1 mL Column Rack, 24')
    r.set('me_form', 'Sample type', 'Aqueous Sample')
    r.set('me_form', 'Sample pipette tip type', '1000 µL Biotage tip')
    for tab, solvent in (('conditioning', 'Methanol'), ('equilibration', 'Water'), ('wash', '2% Formic acid'), ('elution', 'Methanol')):
        r.tap(f'tab_{tab}')
        r.set('me_form', 'Step 1: Solvent', solvent)
        r.set('me_form', 'Step 1: Volume', 500)
        r.set('me_form', 'Step 1: Pressure', 1)
        r.set('me_form', 'Step 1: Positive pressure time', 30)
    r.tap('tab_load')
    r.set('me_form', 'Sample volume', 400)
    r.set('me_form', 'Pressure', 1)
    r.set('me_form', 'Positive pressure time', 60)
    r.tap('save')
    assert r.page() == ('gui', 'methods'), r.page()
    return r.method(name)


def prepare_run(r, name='Test SPE', prime=('S1', 'S2', 'S3'), individual=False):
    """Run Method > method > Prepare Run; set up positions 3, 1, 2, 5 (assign and prime), report; returns at the Prepare Run view."""
    r.tap('m_run_ind' if individual else 'm_run')
    r.row('run_list', name)
    r.tap('prepare_run')
    assert r.page() == ('gui', 'prepare'), r.page()
    r.tap('tile_3')
    if individual:
        r.set('f_pr_extraction', 'Number of samples (column 1)', 3)
    else:
        r.set('f_pr_extraction', 'Use all columns', 'No')
        assert r.row('f_pr_extraction', 'Columns')
        r.ov('[ ] 2')
        r.ov('[x] 1')
        r.ov('OK')
    r.tap('pr_extraction_save')
    r.tap('tile_1')
    r.tap('pr_solvent_tips_save')
    r.tap('tile_2')
    r.set('f_pr_sample_tips', 'Pipette tip type', '1000 µL Biotage tip')
    r.tap('pr_sample_tips_save')
    assert r.page() == ('gui', 'prepare')
    r.tap('tile_5')
    for b, solvent in zip(('S1', 'S2', 'S3'), ('Methanol', 'Water', '2% Formic acid')):
        r.set(f'f_pr_bottles_{b}', b, solvent)
    for b in prime:
        r.tap(f'prime_{b}')
        for _ in range(2):
            r.tap('prime_go')
        r.tap('prime_ok')
    r.tap('pr_bottles_save')
    assert r.page() == ('gui', 'prepare')


class KeyProcedures(unittest.TestCase):
    def test_create_new_spe_method(self):
        r = Rig()
        m = build_spe_method(r)
        self.assertTrue(m['complete'], m['missing'])
        self.assertEqual((m['type'], m['folder'], m['conditioning1_volume'], m['sample_volume']), ('SPE', 'user', 500.0, 400.0))
        self.assertEqual(m['extraction_media'], r.lib_id('extraction_media', '1 mL Column Rack, 24'))
        cells = r.widgets()['method_list']['cells']
        self.assertIn(['', 'Test SPE', 'SPE'], cells)
        DONE_TASKS.add('create a new SPE method')

    def test_incomplete_method_is_marked_and_hidden_from_run(self):
        r = Rig()
        r.tap('m_methods')
        r.tap('mm_new')
        r.tap('type_SLE')
        r.set('me_head', 'Method name', 'Half done')
        r.tap('save')
        self.assertFalse(r.method('Half done')['complete'])
        self.assertIn(['!', 'Half done', 'SLE'], r.widgets()['method_list']['cells'])
        r.home()
        r.tap('m_run')
        names = [c[0] for c in r.widgets()['run_list']['cells']]
        self.assertNotIn('Half done', names)

    def test_prepare_and_start_a_run_to_completion(self):
        r = Rig()
        build_spe_method(r)
        r.home()
        prepare_run(r)
        s = r.val('run.setup')
        self.assertEqual(s['wells'], 4)                                           # one column of a 24-position rack (4 rows)
        self.assertTrue(all(s['saved'].values()), s['saved'])
        r.tap('run_btn')
        self.assertEqual(r.overlay()['dialog'], 'run')
        r.ov('Run')
        self.assertEqual(r.screen(), 'run')
        self.assertEqual(r.val('method.status'), 'running')
        sources0 = r.val('fluid.sources_m3')
        r.step(20)
        self.assertEqual(r.widgets()['run_op']['text'], 'Conditioning')
        self.assertIn('Methanol', r.widgets()['run_action']['text'])
        r.step(4 * 3600)
        self.assertEqual(r.screen(), 'done')
        self.assertEqual(r.text('done_title'), 'Run completed')
        self.assertEqual(r.val('method.status'), 'completed')
        # liquid: 4 wells x (500 x 4 solvent steps + 400 sample) uL left the sources; elution went to collection
        moved = sources0 - r.val('fluid.sources_m3')
        self.assertAlmostEqual(moved, 4 * (4 * 500e-9 + 400e-9), delta=1e-12)
        self.assertAlmostEqual(r.val('fluid.collection_m3'), 4 * 500e-9, delta=1e-12)
        self.assertAlmostEqual(r.val('fluid.extraction_m3'), 0.0, delta=1e-15)
        total = sum(r.val(f'fluid.{n}_m3') for n in ('sources', 'pipette', 'extraction', 'waste', 'collection'))
        self.assertAlmostEqual(total, r.val('fluid.initial_total_m3'), delta=1e-15)
        rep = r.val('reports.list')[-1]
        self.assertEqual((rep['name'], rep['status']), ('Test SPE', 'Completed'))
        r.tap('done_close')
        self.assertEqual(r.page(), ('gui', 'main'))
        DONE_TASKS.add('prepare and start a run')

    def test_prime_a_solvent_inlet(self):
        r = Rig()
        build_spe_method(r)
        r.home()
        prepare_run(r, prime=())
        self.assertEqual(r.widgets()['tile_5']['style'], 'warn')                # orange reservoir: needs priming
        r.tap('tile_5')
        self.assertEqual(r.widgets()['res_S1']['style'], 'warn')
        r.tap('prime_S1')
        self.assertEqual(r.page(), ('gui', 'pr_prime'))
        r.set('f_prime', 'Prime volume', 15)
        r.tap('prime_go')
        self.assertAlmostEqual(r.val('inlets.state')['S1']['primed_m3'], 15e-6)
        self.assertIsNone(r.val('inlets.state')['S1']['line'])
        r.tap('prime_go')
        self.assertEqual(r.val('inlets.state')['S1']['line'], r.lib_id('solvents', 'Methanol'))
        self.assertIn('line filled', r.text('prime_state'))
        r.tap('prime_ok')
        self.assertEqual(r.text('res_S1'), 'Primed')
        self.assertIsNone(r.widgets()['res_S1'].get('style'))
        # 26 mL in one go is refused by the keypad (the reservoir holds at most 25 mL)
        r.tap('prime_S2')
        r.row('f_prime', 'Prime volume')
        r.type('26')
        self.assertFalse(r.ov('OK'))
        self.assertEqual(r.overlay()['error'], 'Out of range')
        DONE_TASKS.add('prime a solvent inlet')

    def test_pause_and_resume_a_run(self):
        r = Rig()
        build_spe_method(r)
        r.home()
        prepare_run(r)
        r.tap('run_btn')
        r.ov('Run')
        r.step(20)
        r.tap('run_pause')
        self.assertTrue(r.val('run.state')['pause_queued'])
        self.assertEqual(r.text('run_pause'), 'Cancel Pause')
        r.tap('run_pause')                                                     # Cancel Pause undoes the queued pause
        self.assertFalse(r.val('run.state')['pause_queued'])
        r.tap('run_pause')
        r.step(300)
        self.assertEqual(r.screen(), 'pause')
        self.assertEqual(r.val('run.state')['pause_reason'], 'user')
        self.assertIsNone(r.val('alarm.state')['color'])                       # a user pause does not raise the alarm
        r.tap('pause_open_door')
        self.assertFalse(r.widgets()['pause_resume']['enabled'])
        r.step(30)
        r.set('f_pause', 'Comment', 'checked wells')
        r.tap('pause_close_door')
        r.tap('pause_resume')
        self.assertEqual(r.screen(), 'run')
        r.step(4 * 3600)
        rep = r.val('reports.list')[-1]
        self.assertEqual(rep['pause_comments'], ['checked wells'])
        self.assertAlmostEqual(rep['door_open_s'], 30.0)
        DONE_TASKS.add('pause and resume a run')

    def test_apply_pressure_manually(self):
        r = Rig()
        r.tap('m_maint')
        r.tap('mt_manual')
        self.assertEqual(r.page(), ('gui', 'manual'))
        r.tap('ph_apply')                                                      # pressure unit still in: refused by the runtime
        self.assertEqual(r.overlay()['dialog'], 'err_pre')
        r.ov('OK')
        r.tap('pu_out')
        self.assertEqual(r.val('manifold.z_m'), 0.0)
        r.tap('wv_open')
        self.assertEqual(r.val('manual.state')['waste_valve'], 'open')
        r.set('f_press', 'Pressure (bar)', 2.5)
        r.tap('ph_apply')
        self.assertEqual(r.val('pressure.target_pa'), 250000.0)
        self.assertIn('2.5 bar', r.text('ph_head'))
        r.tap('ph_stop')
        self.assertEqual(r.val('pressure.target_pa'), 0.0)
        r.tap('pd_start')
        self.assertEqual((r.val('pressure.target_pa'), r.val('manual.state')['plate_dry']), (500000.0, True))
        r.tap('pd_stop')
        r.tap('pu_in')
        self.assertEqual(r.val('manifold.z_m'), rt.EXTRAHERA_PARK_Z_M)
        DONE_TASKS.add('apply pressure manually for method development')

    def test_shut_down_the_system(self):
        r = Rig()
        r.tap('m_shutdown')
        self.assertEqual(r.overlay()['dialog'], 'shutdown')
        r.ov('Yes')
        self.assertEqual(r.screen(), 'shutdown')
        self.assertEqual(r.val('system.state'), 'shut_down')
        self.assertFalse(r.val('lights.state')['upper'])
        self.assertFalse(r.touch(600, 400))                                     # the software is shut down
        self.assertTrue(r.switch('off'))
        self.assertFalse(r.val('power.enabled'))
        self.assertEqual(r.screen(), 'off')
        self.assertTrue(r.switch('on'))
        self.assertEqual(r.screen(), 'boot')
        r.step(rt.EXTRAHERA_BOOT_S)
        self.assertEqual(r.page(), ('gui', 'main'))
        DONE_TASKS.add('shut down the system')

    def test_abort_restore_and_reinitialize(self):
        r = Rig()
        build_spe_method(r)
        r.home()
        prepare_run(r)
        r.tap('run_btn')
        r.ov('Run')
        r.step(20)
        r.tap('run_abort')
        self.assertTrue(r.val('run.state')['abort_queued'])
        self.assertFalse(r.widgets()['run_pause']['enabled'])
        r.step(300)
        self.assertEqual(r.screen(), 'abort')
        r.set('f_abort', 'Comment', 'wrong plate')
        r.tap('abort_restore')
        self.assertEqual(r.screen(), 'done')
        self.assertEqual(r.text('done_title'), 'Run aborted')
        self.assertEqual(r.val('method.status'), 'cancelled')
        self.assertEqual(r.val('reports.list')[-1]['abort_comment'], 'wrong plate')
        self.assertEqual(r.val('manifold.z_m'), rt.EXTRAHERA_PARK_Z_M)
        r.tap('done_close')
        r.tap('m_maint')
        r.tap('mt_manual')
        r.tap('pip_left')
        self.assertEqual(r.val('gantry.x_m'), 0.0)
        r.home()
        r.tap('m_maint')
        r.tap('mt_reinit')
        r.ov('Yes')
        self.assertEqual(r.val('gantry.x_m'), rt.EXTRAHERA_JOINTS['gantry.x_m'][2])
        self.assertEqual(r.val('manual.state')['pipette_head'], 'right')
        DONE_TASKS.add('re-initialize after an abort or maintenance')


class SoftwareFunctions(unittest.TestCase):
    def test_data_administration_new_copy_edit_delete_and_lock(self):
        r = Rig()
        r.tap('m_data')
        r.tap('da_solvents')
        r.tap('solvents_new')
        r.set('f_solvents', 'Solvent name', 'Ethyl acetate')
        r.set('f_solvents', 'Highly volatile?', 'Yes')
        r.set('f_solvents', 'Aspiration flow rate', 8)
        r.tap('solvents_save')
        e = next(x for x in r.val('library.entries')['solvents'] if x['name'] == 'Ethyl acetate')
        self.assertEqual((e['volatile'], e['asp_flow'], e['predefined'], e['upper_gap_pause']), (True, 8.0, False, 300.0))
        # predefined entries are viewed, not edited
        r.row('solvents_list', 'Methanol')
        self.assertEqual(r.text('solvents_edit'), 'View')
        self.assertFalse(r.widgets()['solvents_delete']['enabled'])
        r.tap('solvents_copy')
        self.assertIn('Methanol copy', [x['name'] for x in r.val('library.entries')['solvents']])
        # lock Ethyl acetate to S2, then it cannot be deleted
        r.tap('solvents_lock')
        r.set('f_lock', 'Reservoir S2', 'Ethyl acetate')
        r.tap('lock_save')
        self.assertEqual(r.val('solvent.locks')['S2'], e['id'])
        r.row('solvents_list', 'Ethyl acetate')
        r.tap('solvents_delete')
        r.ov('Yes')
        self.assertEqual(r.overlay()['dialog'], 'err_in_use')
        r.ov('OK')
        r.row('solvents_list', 'Methanol copy')
        r.tap('solvents_delete')
        r.ov('Yes')
        self.assertNotIn('Methanol copy', [x['name'] for x in r.val('library.entries')['solvents']])

    def test_tune_pipetting_heights_moves_the_head_and_takes_the_height(self):
        r = Rig()
        r.tap('m_data')
        r.tap('da_extraction_media')
        r.tap('extraction_media_new')
        r.set('f_extraction_media', 'Name', 'My plate')
        r.row('f_extraction_media', 'Tune Pipetting Heights...')
        z0 = r.val('gantry.z_m')
        for _ in range(3):
            r.ov('Down 1 mm')
        self.assertAlmostEqual(r.val('gantry.z_m'), z0 - 0.003, places=6)
        r.ov('Set dispensation height')
        r.ov('Done')
        r.tap('extraction_media_save')
        e = next(x for x in r.val('library.entries')['extraction_media'] if x['name'] == 'My plate')
        self.assertAlmostEqual(e['disp_height'], round((rt.EXTRAHERA_JOINTS['gantry.z_m'][1] - (z0 - 0.003)) * 1000, 1))

    def test_method_copy_view_delete_and_ppt_mode(self):
        r = Rig()
        r.tap('m_methods')
        r.tap('fold_bio')
        r.row('method_list', 'EVOLUTE ABN (acid, base, neutral), 1 mL columns, 24')
        self.assertEqual(r.text('mm_edit'), 'View')
        r.tap('mm_edit')
        self.assertNotIn('save', r.widgets())                                   # Biotage methods are viewed only
        r.tap('back')
        r.tap('mm_copy')
        self.assertEqual(r.frame()['ui']['vars']['folder'], 'user')
        copy_ = r.method('EVOLUTE ABN (acid, base, neutral), 1 mL columns, 24 copy')
        self.assertTrue(copy_['complete'])
        r.row('method_list', copy_['name'])
        r.tap('mm_delete')
        r.ov('Yes')
        self.assertFalse(any(m['name'] == copy_['name'] for m in r.val('methods.list')))
        # PPT/PLD mode is taken from Maintenance when a method is created
        r.home()
        r.tap('m_maint')
        r.set('f_maint', 'PPT/PLD Mode: Add solvent first', 'No')
        self.assertFalse(r.val('settings.values')['ppt_solvent_first'])
        r.home()
        r.tap('m_methods')
        r.tap('mm_new')
        self.assertEqual(r.text('type_PPT_PLD'), 'PPT/PLD (Sample First)')
        r.tap('type_PPT_PLD')
        r.set('me_head', 'Method name', 'PPT B')
        r.tap('save')
        self.assertEqual(r.method('PPT B')['ppt_mode'], 'Sample First')

    def test_maintenance_settings_and_instrument_type(self):
        r = Rig(itype=None)
        r.tap('m_maint')
        r.set('f_maint', 'Upper lamps', 'Off')
        self.assertEqual(r.val('lights.state'), {'upper': False, 'lower': True})
        r.set('f_maint', 'Audible Alarm', 'Off')
        self.assertFalse(r.val('settings.values')['audible_alarm'])
        r.set('f_itype', 'Instrument Type', '24 Positions, Column Rack')
        self.assertEqual(r.val('settings.values')['instrument_type'], '96 Positions, Plate/Columns')   # not yet: Set and Yes
        r.tap('itype_set')
        r.ov('Yes')
        self.assertEqual(r.val('settings.values')['instrument_type'], '24 Positions, Column Rack')
        r.home()
        r.tap('m_run')                                                          # only methods of the configured format
        self.assertEqual([c[0] for c in r.widgets()['run_list']['cells']], ['EVOLUTE ABN (acid, base, neutral), 1 mL columns, 24'])

    def test_system_administration_calibration_reminder_at_startup_and_before_run(self):
        r = Rig()
        r.tap('m_sysadmin')
        r.set('f_sysadmin', 'Notifications?', 'Yes')
        r.set('f_sysadmin', 'Time between notifications', '1 month')
        r.home()
        r.step(31 * 86400)                                                     # a month later
        r.switch('off')
        r.switch('on')
        r.step(rt.EXTRAHERA_BOOT_S)
        self.assertEqual(r.screen(), 'notice')
        self.assertIn('expired', r.text('notice_text'))
        r.tap('notice_ok')
        self.assertEqual(r.page(), ('gui', 'main'))
        r.tap('m_maint')
        r.tap('m_maint') if False else None
        build = build_spe_method
        r.home()
        build(r)
        r.home()
        prepare_run(r)
        r.tap('run_btn')
        self.assertEqual(r.overlay()['dialog'], 'cal_before_run')
        r.ov('OK')
        self.assertEqual(r.overlay()['dialog'], 'run')
        r.ov('Run')
        r.step(4 * 3600)
        self.assertTrue(r.val('reports.list')[-1]['expired_calibration'])

    def test_pump_calibration_corrects_the_run_volumes(self):
        r = Rig()
        r.call(r.E, 'pump.configure_error', gain=-0.05, offset_m3=0.0)       # the pump delivers 5 % too little (evaluator)
        r.tap('m_maint')
        r.tap('mt_cal')
        for label, v in (('Desired volume one', 100), ('Obtained volume one', 95), ('Desired volume two', 1000), ('Obtained volume two', 950)):
            r.set('f_cal', label, v)
        self.assertFalse(r.widgets()['cal_save']['enabled'])
        r.tap('cal_calculate')
        self.assertIn('Calculated coefficient: 1.0526', r.text('cal_info'))
        r.tap('cal_apply')
        r.tap('cal_save')
        cal = r.val('pump.calibration')
        self.assertAlmostEqual(cal['coefficient'], 1 / 0.95, places=5)
        self.assertEqual(len(cal['history']), 1)
        # restore the previous values
        r.row('cal_history', '2026-01-01 00:00')
        r.tap('cal_restore')
        r.tap('cal_save')
        self.assertEqual(r.val('pump.calibration')['coefficient'], 1.0)
        # with the calibration applied a run delivers the nominal volumes
        r.tap('cal_restore') if False else None
        r.call(r.A, 'pump.calibration', action='restore', history_index=1)
        r.call(r.A, 'pump.calibration', action='save')
        r.home()
        build_spe_method(r)
        r.home()
        prepare_run(r)
        s0 = r.val('fluid.sources_m3')
        r.tap('run_btn')
        r.ov('Run')
        r.step(4 * 3600)
        self.assertAlmostEqual(s0 - r.val('fluid.sources_m3'), 4 * (4 * 500e-9 + 400e-9), delta=1e-11)

    def test_flush_empties_a_line_which_then_needs_priming(self):
        r = Rig()
        build_spe_method(r)
        r.home()
        prepare_run(r)
        r.tap('back')
        r.tap('m_maint')
        r.tap('mt_flush')
        r.set('f_flush_S1', 'S1 flush volume', 10)
        r.tap('flush_S1')
        self.assertIsNotNone(r.val('inlets.state')['S1']['line'])               # 10 mL does not empty the line
        r.set('f_flush_S1', 'S1 flush volume', 25)
        r.tap('flush_S1')
        self.assertIsNone(r.val('inlets.state')['S1']['line'])
        self.assertEqual(r.text('flush_S1_state'), 'S1: empty')

    def test_reports_view_export_delete_and_about(self):
        r = Rig()
        build_spe_method(r)
        r.home()
        prepare_run(r)
        r.tap('run_btn')
        r.ov('Run')
        r.step(4 * 3600)
        r.tap('done_close')
        r.tap('m_reports')
        r.row('report_list', 'Test SPE')
        r.tap('rep_view')
        self.assertIn('Status: Completed', r.overlay()['lines'][2])
        r.ov('Close')
        r.tap('rep_export')
        self.assertEqual(r.overlay()['dialog'], 'no_usb')
        self.assertTrue(r.frame()['ui']['unsupported'])
        r.ov('OK')
        r.tap('rep_delete')
        r.ov('Yes')
        self.assertEqual(r.val('reports.list'), [])
        r.home()
        r.tap('m_about')
        self.assertIn('Instrument type: 24 Positions, Column Rack', r.overlay()['lines'])

    def test_individual_samples_scheduled_pause_and_alarm(self):
        r = Rig()
        build_spe_method(r, 'Pausing SPE')
        m = r.method('Pausing SPE')
        r.call(r.A, 'methods.save', method_id=m['id'], method_type=None, values={'wash1_pause': True})
        r.home()
        prepare_run(r, 'Pausing SPE', individual=True)
        self.assertEqual(r.val('run.setup')['wells'], 3)
        r.tap('run_btn')
        r.ov('Run')
        r.step(4 * 3600)
        self.assertEqual(r.screen(), 'pause')
        self.assertEqual(r.val('run.state')['pause_reason'], 'scheduled')
        self.assertEqual(r.val('alarm.state'), {'color': 'blue', 'sounding': True})
        t = r.h.time_s
        shown = {r.widgets().get('flash_blue') is not None for _ in [0]}
        r.step(1.0)
        shown.add(r.widgets().get('flash_blue') is not None)
        self.assertEqual(shown, {True, False}, t)                               # the background flashes blue
        self.assertTrue(r.touch(600, 400))                                      # the first touch silences the alarm
        self.assertFalse(r.val('alarm.state')['sounding'])
        self.assertEqual(r.val('run.state')['state'], 'paused')
        r.tap('pause_resume')
        r.step(4 * 3600)
        self.assertEqual(r.val('run.state')['state'], 'completed')

    def test_out_of_sample_tips_pause_and_reload(self):
        r = Rig(itype='96 Positions, Plate/Columns')
        lib = r.lib_id
        r.call(r.A, 'methods.save', method_id=None, method_type='SPE', values=dict(
            name='Plate SPE', sample_plate=lib('sample_plates', '2 mL Sample Plate, 96'), extraction_media=lib('extraction_media', 'Extraction Plate, 96'),
            sample_type=lib('sample_types', 'Aqueous Sample'), sample_tip=lib('pipette_tips', '1000 µL Biotage tip'), pretreatment_on=True,
            pre_solvent=lib('solvents', 'Water'), pre_volume=20.0, mix_times=1, mix_volume=20.0, conditioning_on=False, equilibration_on=False,
            wash_on=False, elution1_solvent=lib('solvents', 'Methanol'), elution1_volume=20.0, elution1_pressure=1.0, elution1_pp_time=30.0,
            sample_volume=20.0, load_pressure=1.0, load_pp_time=30.0))
        r.tap('m_run')
        r.row('run_list', 'Plate SPE')
        r.tap('prepare_run')
        r.tap('tile_3')
        r.tap('pr_extraction_save')                                             # Use all columns: 12 columns x 8 rows
        r.tap('tile_1')
        r.tap('pr_solvent_tips_save')
        r.tap('tile_2')
        r.set('f_pr_sample_tips', 'Pipette tip type', '1000 µL Biotage tip')
        r.tap('pr_sample_tips_save')
        self.assertIn('Tips needed: 192', r.text('tile_2'))                    # mix and load, no tip reuse
        r.tap('tile_5')
        for b, solvent in (('S1', 'Methanol'), ('S2', 'Water')):
            r.set(f'f_pr_bottles_{b}', b, solvent)
            r.tap(f'prime_{b}')
            r.tap('prime_go')
            r.tap('prime_go')
            r.tap('prime_ok')
        r.tap('pr_bottles_save')
        self.assertEqual(r.val('run.setup')['wells'], 96)
        r.tap('run_btn')
        r.ov('Run')
        r.step(6 * 3600)
        self.assertEqual(r.screen(), 'pause')                                   # out of sample tips after the 96 mixing tips
        self.assertEqual(r.val('run.state')['pause_reason'], 'tips')
        self.assertIn('Please load 96 sample pipette tips of type 1000 µL Biotage tip into position 2', r.text('pause_text'))
        self.assertEqual(r.val('alarm.state')['color'], 'blue')
        r.touch(10, 790)                                                       # silences the alarm
        r.tap('pause_open_door')
        r.tap('pause_close_door')
        r.tap('pause_resume')                                                   # tips loaded
        r.step(6 * 3600)
        self.assertEqual(r.val('run.state')['state'], 'completed')
        self.assertEqual(r.val('run.state')['sample_tips_left'], 0)

    def test_utility_loss_is_a_red_system_error(self):
        r = Rig()
        build_spe_method(r)
        r.home()
        prepare_run(r)
        r.tap('run_btn')
        r.ov('Run')
        r.step(30)
        r.h.set_utilities(INST, vacuum_ready=True, compressed_air_ready=False, nitrogen_ready=True, evidence='test: air lost')
        r.step(5)
        self.assertEqual(r.screen(), 'done')
        self.assertEqual(r.val('alarm.state')['color'], 'red')
        self.assertEqual(r.text('done_title'), 'Error')
        self.assertEqual(r.val('method.status'), 'failed')
        r.touch(600, 400)
        r.tap('done_close')
        self.assertEqual(r.page(), ('gui', 'main'))


class RefusalsAndLocks(unittest.TestCase):
    def test_run_refusals_shown_as_device_messages(self):
        r = Rig(liquids=False)
        build_spe_method(r)
        r.home()
        r.tap('m_run')
        r.row('run_list', 'Test SPE')
        r.tap('prepare_run')
        r.tap('run_btn')
        r.ov('Run')
        self.assertEqual(r.overlay()['dialog'], 'err_setup')                   # positions not saved
        r.ov('OK')
        r.tap('back')
        prepare_run(r, prime=())
        r.tap('run_btn')
        r.ov('Run')
        self.assertEqual(r.overlay()['dialog'], 'err_liquid')
        r.ov('OK')
        r.h.initialize_liquids(INST, dict(LIQUIDS), evidence='late')
        r.tap('run_btn')
        r.ov('Run')
        self.assertEqual(r.overlay()['dialog'], 'err_prime')
        r.ov('OK')
        r.tap('pr_open_door')
        r.tap('run_btn')
        r.ov('Run')
        self.assertEqual(r.overlay()['dialog'], 'err_door')
        r.ov('OK')
        self.assertEqual(r.val('run.state')['state'], 'prepare')

    def test_wrong_sample_tip_type_is_notified(self):
        r = Rig()
        r.call(r.A, 'library.save', kind='pipette_tips', entry_id=None, entry={'name': '200 µL tip', 'capacity': 200.0})
        build_spe_method(r)
        r.home()
        r.tap('m_run')
        r.row('run_list', 'Test SPE')
        r.tap('prepare_run')
        r.tap('tile_2')
        r.set('f_pr_sample_tips', 'Pipette tip type', '200 µL tip')
        r.tap('pr_sample_tips_save')
        self.assertEqual(r.overlay()['dialog'], 'wrong_tip')

    def test_manual_control_door_interlocks(self):
        r = Rig()
        r.tap('m_maint')
        r.tap('mt_manual')
        r.tap('ewc_raise')                                                      # pressure unit in: the collector cannot be raised
        self.assertEqual(r.overlay()['dialog'], 'err_interlock')
        r.ov('OK')
        r.tap('door_open')
        for wid in ('ewc_raise', 'pu_out', 'car_collect_A', 'ph_apply', 'pd_start'):
            self.assertFalse(r.widgets()[wid]['enabled'], wid)
        self.assertTrue(r.widgets()['wv_open']['enabled'])
        r.tap('wv_open')
        self.assertEqual(r.val('manual.state')['waste_valve'], 'open')
        r.tap('door_close')
        r.tap('pu_out')
        r.tap('pip_left')                                                       # pressure unit out: the pipette head cannot move
        self.assertEqual(r.overlay()['dialog'], 'err_interlock')
        r.ov('OK')
        r.tap('car_collect_B')
        self.assertEqual((r.val('manual.state')['carousel_position'], r.val('manual.state')['carousel_lifted']), ('B', True))
        r.tap('door_open')
        r.tap('door_close')                                                     # after closing: homed
        self.assertEqual(r.val('manual.state')['carousel_lifted'], False)
        self.assertEqual(r.val('manifold.z_m'), rt.EXTRAHERA_PARK_Z_M)

    def test_power_off_and_boot_ignore_touches_and_busy_run(self):
        r = Rig(ready=False)
        self.assertEqual(r.screen(), 'off')
        self.assertFalse(r.touch(100, 100))
        r.switch('on')
        self.assertEqual(r.screen(), 'boot')
        self.assertFalse(r.touch(100, 100))
        r.step(rt.EXTRAHERA_BOOT_S)
        self.assertEqual(r.screen(), 'gui')
        self.assertEqual(r.call(r.A, 'settings.configure', expect='rejected', settings={'audible_alarm': 1})['error']['code'], 'INVALID_ARGUMENT')

    def test_direct_commands_during_a_gui_run_are_busy(self):
        r = Rig()
        build_spe_method(r)
        r.home()
        prepare_run(r)
        r.tap('run_btn')
        r.ov('Run')
        self.assertEqual(r.call(r.A, 'gantry.move', expect='rejected', x_m=.1, y_m=.1, z_m=.1)['error']['code'], 'BUSY')
        self.assertEqual(r.call(r.A, 'methods.delete', expect='rejected', method_ids=['usr-001'])['error']['code'], 'BUSY')
        r.call(r.A, 'method.stop')                                             # the CHEM-031 stop cancels the panel run
        self.assertEqual(r.val('method.status'), 'cancelled')
        self.assertEqual(r.screen(), 'done')
        self.assertEqual(r.val('reports.list')[-1]['status'], 'Aborted')


class PanelEqualsDirect(unittest.TestCase):
    def test_panel_and_direct_commands_reach_the_same_state(self):
        a, b = Rig(), Rig()
        build_spe_method(a, 'Same')
        b.call(b.A, 'methods.save', method_id=None, method_type='SPE', values=dict(
            name='Same', sample_plate=b.lib_id('sample_plates', '12 x 75 mm Test Tubes, 24'), extraction_media=b.lib_id('extraction_media', '1 mL Column Rack, 24'),
            sample_type=b.lib_id('sample_types', 'Aqueous Sample'), sample_tip=b.lib_id('pipette_tips', '1000 µL Biotage tip'),
            conditioning1_solvent=b.lib_id('solvents', 'Methanol'), conditioning1_volume=500.0, conditioning1_pressure=1.0, conditioning1_pp_time=30.0,
            equilibration1_solvent=b.lib_id('solvents', 'Water'), equilibration1_volume=500.0, equilibration1_pressure=1.0, equilibration1_pp_time=30.0,
            wash1_solvent=b.lib_id('solvents', '2% Formic acid'), wash1_volume=500.0, wash1_pressure=1.0, wash1_pp_time=30.0,
            elution1_solvent=b.lib_id('solvents', 'Methanol'), elution1_volume=500.0, elution1_pressure=1.0, elution1_pp_time=30.0,
            sample_volume=400.0, load_pressure=1.0, load_pp_time=60.0))
        self.assertEqual(a.method('Same'), b.method('Same'))
        a.home()
        a.tap('m_maint')
        a.tap('mt_manual')
        a.tap('pu_out')
        a.set('f_press', 'Pressure (bar)', 1.2)
        a.tap('ph_apply')
        b.call(b.A, 'manual.control', action='pressure_unit_out')
        b.call(b.A, 'pressure.set', pressure_pa=120000)
        for sid in ('pressure.target_pa', 'manifold.z_m', 'manual.state', 'methods.list', 'settings.values'):
            self.assertEqual(a.val(sid), b.val(sid), sid)


class TimeoutsAndRendering(unittest.TestCase):
    def test_boot_screen_times_out_to_the_main_menu(self):
        r = Rig(ready=False)
        r.switch('on')
        r.step(rt.EXTRAHERA_BOOT_S - 1)
        self.assertEqual(r.screen(), 'boot')
        r.step(1)
        self.assertEqual(r.page(), ('gui', 'main'))

    def test_render_determinism_and_evidence_pngs(self):
        a, b = Rig(), Rig()
        for r in (a, b):
            r.tap('m_methods')
        p1, p2 = a.h.panel(INST).png_bytes(SCREEN)[0], b.h.panel(INST).png_bytes(SCREEN)[0]
        self.assertEqual(p1, p2)
        img = a.h.panel(INST).render(SCREEN)
        self.assertEqual(img.shape, (800, 1280, 3))
        cap = a.call(a.A, 'panel.capture', display_id=SCREEN)
        self.assertEqual((cap['image']['width'], cap['image']['height']), (1280, 800))
        # evidence renders
        OUT.mkdir(parents=True, exist_ok=True)
        renders = {}
        r = Rig()
        renders['home'] = r.h.panel(INST).save_png(SCREEN, OUT / 'home.png')
        build_spe_method(r)
        r.row('method_list', 'Test SPE')
        r.tap('mm_edit')
        r.tap('tab_wash')
        renders['menu'] = r.h.panel(INST).save_png(SCREEN, OUT / 'menu.png')
        r.tap('back')
        r.home()
        prepare_run(r)
        renders['prepare'] = r.h.panel(INST).save_png(SCREEN, OUT / 'prepare.png')
        r.tap('pr_open_door')
        r.tap('run_btn')
        r.ov('Run')
        renders['error'] = r.h.panel(INST).save_png(SCREEN, OUT / 'error.png')
        r.ov('OK')
        r.tap('pr_close_door')
        r.tap('run_btn')
        r.ov('Run')
        r.step(120)
        renders['running'] = r.h.panel(INST).save_png(SCREEN, OUT / 'running.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')


class Contract(unittest.TestCase):
    def test_device_commands_and_panel_capabilities_in_metadata_and_contract(self):
        meta = rt.export_extrahera_contract_metadata()['asset_types'][rt.EXTRAHERA_ASSET_TYPE_ID]
        self.assertEqual(set(meta['device_capabilities']), set(rt.EXTRAHERA_DEVICE_ARGS))
        self.assertEqual(set(meta['panel_capabilities']), {'panel.touch', 'panel.switch', 'panel.capture', 'panel.frame'})
        for name, cap in {**meta['device_capabilities'], **meta['panel_capabilities']}.items():
            self.assertEqual(validate_value(cap['example_arguments'][0], cap['input_schema']), [], name)
        c = json.loads(CONTRACT.read_text())
        if 'chemistry_lab.panel_ui' in c.get('extensions', {}):
            for name in list(meta['device_capabilities']) + list(meta['panel_capabilities']):
                self.assertIn(name, c['capabilities'], name)
            self.assertEqual(c['asset_version'], rt.EXTRAHERA_ASSET_VERSION)

    def test_agent_does_not_see_evaluator_commands_and_frame(self):
        r = Rig()
        caps = r.A.describe(instance_id=INST)['capabilities']
        self.assertIn('panel.touch', caps)
        self.assertNotIn('panel.frame', caps)
        self.assertNotIn('pump.configure_error', caps)
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call(r.A, 'pump.configure_error', expect='rejected', gain=0.0, offset_m3=0.0)['error']['code'], 'ACCESS_DENIED')
        self.assertIn('pump.configure_error', r.E.describe(instance_id=INST)['capabilities'])


def tap_as(r, sess, wid):
    """Tap a widget through another session's panel.touch (the same physical screen)."""
    x, y, w, h = r.widgets()[wid]['rect']
    return r.call(sess, 'panel.touch', display_id=SCREEN, x=int(x + w // 2), y=int(y + h // 2))['accepted']


def ov_as(r, sess, label):
    for b in r.overlay()['buttons']:
        if b['label'] == label and b['id'] != 'field':
            x, y, w, h = b['rect']
            return r.call(sess, 'panel.touch', display_id=SCREEN, x=int(x + w // 2), y=int(y + h // 2))['accepted']
    raise AssertionError(label)


def spe_values(r, **extra):
    """The SPE method of build_spe_method as methods.save values (one column of the 24-position rack, 4 wells)."""
    lib = r.lib_id
    values = dict(name='Direct SPE', sample_plate=lib('sample_plates', '12 x 75 mm Test Tubes, 24'), extraction_media=lib('extraction_media', '1 mL Column Rack, 24'),
                  sample_type=lib('sample_types', 'Aqueous Sample'), sample_tip=lib('pipette_tips', '1000 µL Biotage tip'), sample_volume=400.0,
                  load_pressure=1.0, load_pp_time=60.0)
    for op, solvent in (('conditioning', 'Methanol'), ('equilibration', 'Water'), ('wash', '2% Formic acid'), ('elution', 'Methanol')):
        values.update({f'{op}1_solvent': lib('solvents', solvent), f'{op}1_volume': 500.0, f'{op}1_pressure': 1.0, f'{op}1_pp_time': 30.0})
    values.update(extra)
    return values


def direct_prepare(r, method_id, bottles):
    """Prepare Run by the device commands: column 1, tips, bottles assigned and primed (2 x 15 mL)."""
    tip = r.lib_id('pipette_tips', '1000 µL Biotage tip')
    r.call(r.A, 'run.prepare', method_id=method_id, individual=False)
    r.call(r.A, 'run.setup', section='extraction', values={'use_all_columns': False, 'columns': [1]})
    r.call(r.A, 'run.setup', section='solvent_tips', values={'tip_type': tip})
    r.call(r.A, 'run.setup', section='sample_tips', values={'tip_type': tip})
    r.call(r.A, 'run.setup', section='bottles', values=dict(bottles))
    for b in bottles:
        for _ in range(2):
            r.call(r.A, 'run.prime', inlet=b, volume_m3=15e-6)


def summary(r):
    return r.h._panel_values(INST)['host.run_summary']


class SyncAudit20261019(unittest.TestCase):
    """Fixes of the 2026-10-19 front-panel sync audit: one pipette pump model on every path (rule 1), panel runs owned by the gesturing session
    and guarded against other agents (rule 2), and the liquid-class and method fields that act on the run (rule 3)."""

    def test_direct_strokes_use_the_device_pump_model(self):
        r = Rig()
        r.call(r.E, 'pump.configure_error', gain=0.1, offset_m3=0.0)
        r.call(r.A, 'deck.configure', format='cartridges24', cartridge_volume_m3=1e-6, collection_plate='A', solvent_positions=['S1', 'S2', 'S3'])
        r.call(r.A, 'sample.aspirate', expect='accepted', solvent_position='S1', volume_m3=100e-9, flow_m3_s=1e-8)
        r.step(60)
        self.assertAlmostEqual(r.val('fluid.pipette_m3') * 1e9, 110.0, places=6)          # the same pump model as the runs (+10 %)
        r.call(r.A, 'sample.dispense', expect='accepted', destination='waste', volume_m3=100e-9, flow_m3_s=1e-8)
        r.step(60)
        self.assertAlmostEqual(r.val('fluid.pipette_m3'), 0.0, delta=1e-15)
        self.assertAlmostEqual(r.val('fluid.waste_m3') * 1e9, 110.0, places=6)
        # the calibration saved on the device compensates the error on the direct path as well
        r.call(r.A, 'pump.calibration', action='calculate', desired_1_ul=100, obtained_1_ul=110, desired_2_ul=1000, obtained_2_ul=1100)
        r.call(r.A, 'pump.calibration', action='apply')
        r.call(r.A, 'pump.calibration', action='save')
        r.call(r.A, 'sample.aspirate', expect='accepted', solvent_position='S2', volume_m3=500e-9, flow_m3_s=1e-8)
        r.step(120)
        self.assertAlmostEqual(r.val('fluid.pipette_m3') * 1e9, 500.0, delta=1e-3)
        r.call(r.A, 'sample.dispense', expect='accepted', destination='waste', volume_m3=500e-9, flow_m3_s=1e-8)
        r.step(120)
        # method.start strokes (aspirate, dispense, drain) go through the same pump
        r.call(r.E, 'pump.configure_error', gain=-0.05, offset_m3=0.0)
        r.call(r.A, 'pump.calibration', action='restore', history_index=0)
        r.call(r.A, 'pump.calibration', action='save')
        steps = [dict(phase=ph, solvent_position=pos, volume_m3=100e-9, flow_m3_s=1e-8, drain_flow_m3_s=1e-8, repetitions=1)
                 for ph, pos in (('load', 'S1'), ('wash', 'S2'), ('elute', 'S3'))]
        r.call(r.A, 'method.configure', steps=steps)
        r.call(r.A, 'manifold.set_position', z_m=0.0)
        r.call(r.A, 'pressure.set', pressure_pa=100000)
        s0, c0 = r.val('fluid.sources_m3'), r.val('fluid.collection_m3')
        r.call(r.A, 'method.start', expect='accepted')
        r.step(600)
        self.assertEqual(r.val('method.status'), 'completed')
        self.assertAlmostEqual((s0 - r.val('fluid.sources_m3')) * 1e9, 3 * 95.0, places=6)
        self.assertAlmostEqual((r.val('fluid.collection_m3') - c0) * 1e9, 95.0, places=6)

    def test_panel_run_belongs_to_the_caller_and_other_agents_cannot_stop_it(self):
        r = Rig()
        r.h.register_principal('other')
        B = r.h.session('other')
        build_spe_method(r)
        r.home()
        prepare_run(r)
        r.tap('run_btn')
        r.ov('Run')
        op = r.val('operation.active_id')
        self.assertEqual(r.h._instances[INST].operations[op]['principal_id'], 'agent')
        self.assertEqual(r.A.poll(instance_id=INST, operation_id=op)['status'], 'running')
        self.assertEqual(B.poll(instance_id=INST, operation_id=op)['error']['code'], 'ACCESS_DENIED')
        r.step(20)
        before = r.val('run.state')
        tap_as(r, B, 'run_abort')                                                # B taps Abort: refused, shown as the device's message
        self.assertEqual(r.overlay()['dialog'], 'err_access')
        ov_as(r, B, 'OK')
        tap_as(r, B, 'run_pause')
        self.assertEqual(r.overlay()['dialog'], 'err_access')
        ov_as(r, B, 'OK')
        self.assertEqual(r.val('run.state'), before)
        for cap, args in (('run.control', {'action': 'abort'}), ('method.stop', {})):   # the mains switch (power.set) is physical (10-19)
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.val('run.state'), before)
        self.assertTrue(r.val('power.enabled'))
        # the owner cancels its own panel-started run: the run closes as aborted, like method.stop
        res = r.A.cancel(instance_id=INST, operation_id=op, request_id='own-cancel')
        self.assertEqual(res['status'], 'cancelled')
        self.assertEqual(r.val('run.state')['state'], 'aborted')
        self.assertEqual(r.screen(), 'done')
        self.assertEqual(r.val('reports.list')[-1]['status'], 'Aborted')
        # B cannot delete A's report through the panel; A can
        r.tap('done_close')
        r.tap('m_reports')
        r.row('report_list', 'Test SPE')
        tap_as(r, B, 'rep_delete')
        ov_as(r, B, 'Yes')
        self.assertEqual(r.overlay()['dialog'], 'err_access')
        ov_as(r, B, 'OK')
        self.assertEqual(len(r.val('reports.list')), 1)
        r.row('report_list', 'Test SPE')
        r.tap('rep_delete')
        r.ov('Yes')
        self.assertEqual(r.val('reports.list'), [])

    def test_owner_controls_its_panel_run_and_evaluator_is_not_restricted(self):
        r = Rig()
        build_spe_method(r)
        r.home()
        prepare_run(r)
        r.tap('run_btn')
        r.ov('Run')
        r.step(20)
        r.call(r.A, 'run.control', action='pause')                              # the owner's direct command on its panel run
        self.assertTrue(r.val('run.state')['pause_queued'])
        r.call(r.E, 'run.control', action='cancel_pause')                        # an evaluator session is not restricted
        self.assertFalse(r.val('run.state')['pause_queued'])

    def test_liquid_class_times_act_on_each_pipetting_cycle(self):
        times = {}
        for slow in (False, True):
            r = Rig()
            st = r.lib_id('sample_types', 'Aqueous Sample')
            if slow:
                st = r.call(r.A, 'library.save', kind='sample_types', entry_id=None, entry={
                    'name': 'Viscous', 'asp_flow': 3.0, 'disp_flow': 3.0, 'upper_gap_pause': 2000.0, 'post_asp': True, 'post_asp_flow': 1.0,
                    'post_asp_volume': 100.0})['entry_id']
            mid = r.call(r.A, 'methods.save', method_id=None, method_type='SPE', values=spe_values(r, sample_type=st))['method_id']
            direct_prepare(r, mid, {'S1': r.lib_id('solvents', 'Methanol'), 'S2': r.lib_id('solvents', 'Water'), 'S3': r.lib_id('solvents', '2% Formic acid')})
            times[slow] = summary(r)['estimated_s']
            r.call(r.A, 'run.start', expect='accepted')
            r.step(4 * 3600)
            times[slow, 'run'] = r.val('reports.list')[-1]['duration_s']
        # one load cycle (4 wells, 4 channels): pause 0.3 -> 2.0 s and 100 uL of air at 1 mL/min after the dispense (6 s)
        self.assertAlmostEqual(times[True] - times[False], 1.7 + 6.0, places=6)
        self.assertAlmostEqual(times[True, 'run'] - times[False, 'run'], 1.7 + 6.0, places=6)

    def test_tip_conditioning_uses_solvent_and_time(self):
        r = Rig()
        meoh = r.lib_id('solvents', 'Methanol')
        cond = r.call(r.A, 'library.save', kind='solvents', entry_id=None, entry={
            'name': 'MeOH conditioned', 'tip_cond': True, 'cond_frequency': 'Before each step', 'cond_times': 2, 'cond_volume_pct': 50.0,
            'cond_flow': 10.0})['entry_id']
        mid = r.call(r.A, 'methods.save', method_id=None, method_type='SPE', values=spe_values(
            r, conditioning1_solvent=cond, tip_conditioning=True, conditioning_solvent=meoh))['method_id']
        direct_prepare(r, mid, {'S1': meoh, 'S2': r.lib_id('solvents', 'Water'), 'S3': r.lib_id('solvents', '2% Formic acid'), 'S4': cond})
        use = {c['name']: c['ml'] for c in summary(r)['consumption']}
        # solvent tips: 2 strokes x 4 tips x 500 uL; sample tips: 4 tips x 1 x 500 uL (Methanol's default conditioning settings)
        self.assertAlmostEqual(use['MeOH conditioned'], 4 * 0.5 + 4.0)
        self.assertAlmostEqual(use['Methanol'], 4 * 0.5 + 2.0)
        s0 = r.val('fluid.sources_m3')
        r.call(r.A, 'run.start', expect='accepted')
        r.step(4 * 3600)
        self.assertEqual(r.val('run.state')['state'], 'completed')
        self.assertAlmostEqual(s0 - r.val('fluid.sources_m3'), 4 * (4 * 500e-9 + 400e-9) + 6e-6, delta=1e-12)
        self.assertAlmostEqual(r.val('fluid.collection_m3'), 4 * 500e-9, delta=1e-12)
        total = sum(r.val(f'fluid.{n}_m3') for n in ('sources', 'pipette', 'extraction', 'waste', 'collection'))
        self.assertAlmostEqual(total, r.val('fluid.initial_total_m3'), delta=1e-15)

    def test_disposing_solvent_tips_needs_new_tip_sets(self):
        counts = {}
        for dispose in (False, True):
            r = Rig()
            mid = r.call(r.A, 'methods.save', method_id=None, method_type='SPE', values=spe_values(r, conditioning_dispose_tips=dispose))['method_id']
            direct_prepare(r, mid, {'S1': r.lib_id('solvents', 'Methanol'), 'S2': r.lib_id('solvents', 'Water'), 'S3': r.lib_id('solvents', '2% Formic acid')})
            counts[dispose] = summary(r)['solvent_tips']
            r.call(r.A, 'run.start', expect='accepted')
            r.step(4 * 3600)
            counts[dispose, 'disposed'] = r.val('reports.list')[-1]['solvent_tips_disposed']
        self.assertEqual(counts[False], 3 * 4)                                   # Methanol, Water, 2% formic acid; tips reused
        self.assertEqual(counts[True], 4 * 4)                                    # the Methanol set is disposed after conditioning: elution takes a new one
        self.assertEqual((counts[False, 'disposed'], counts[True, 'disposed']), (0, 4))

    def test_starting_sample_volume_must_cover_the_load(self):
        r = Rig()
        mid = r.call(r.A, 'methods.save', method_id=None, method_type='SPE', values=spe_values(r, starting_sample=300.0))['method_id']
        direct_prepare(r, mid, {'S1': r.lib_id('solvents', 'Methanol'), 'S2': r.lib_id('solvents', 'Water'), 'S3': r.lib_id('solvents', '2% Formic acid')})
        self.assertTrue(summary(r)['sample_short'])
        res = r.call(r.A, 'run.start', expect='rejected')
        self.assertEqual(res['error']['code'], 'NOT_ENOUGH_SAMPLE')
        self.assertEqual(r.val('run.state')['state'], 'prepare')
        r.call(r.A, 'run.control', action='exit_prepare')
        r.call(r.A, 'methods.save', method_id=mid, method_type=None, values={'starting_sample': 400.0})
        direct_prepare(r, mid, {})
        self.assertFalse(summary(r)['sample_short'])
        r.call(r.A, 'run.start', expect='accepted')

    def test_starting_sample_shortage_is_shown_on_the_prepare_view(self):
        r = Rig()
        build_spe_method(r)
        r.call(r.A, 'methods.save', method_id=r.method('Test SPE')['id'], method_type=None, values={'starting_sample': 300.0})
        r.home()
        prepare_run(r)
        self.assertEqual(r.widgets()['tile_4']['style'], 'warn')
        self.assertIn('Not enough sample', r.text('tile_4'))
        r.tap('run_btn')
        r.ov('Run')
        self.assertEqual(r.overlay()['dialog'], 'err_sample')

    def test_filtration_collection_plate_height_moves_the_lifter(self):
        r = Rig()
        mid = r.call(r.A, 'methods.save', method_id=None, method_type='Filtration', values=dict(
            name='Filter', extraction_media=r.lib_id('extraction_media', '3 mL Column Rack, 24'), collection_height=58.0, collection_pressure=1.0,
            collection_pp_time=60.0))['method_id']
        direct_prepare(r, mid, {})
        r.call(r.A, 'run.start', expect='accepted')
        r.step(30)
        ms = r.val('manual.state')
        self.assertEqual((ms['carousel_lifted'], ms['lifter_height_pct']), (True, 58.0))
        r.step(60)
        self.assertEqual(r.val('run.state')['state'], 'completed')
        self.assertEqual((r.val('manual.state')['carousel_lifted'], r.val('manual.state')['lifter_height_pct']), (False, None))



class Sweep20261019(unittest.TestCase):
    """2026-10-19 consistency sweep: another agent's refused gestures on a run (Abort) show the device's dialog with the reason in the frame and
    leave the run unchanged; the mains switch is a hard switch (lead clarification 10-19): another agent may switch it off and the run is
    aborted as on a power loss."""

    def test_refusals_of_another_agents_run_are_shown(self):
        r = Rig()
        r.h.register_principal('other')
        B = r.h.session('other')
        build_spe_method(r)
        r.home()
        prepare_run(r)
        r.tap('run_btn')
        r.ov('Run')
        r.step(20)
        before = copy.deepcopy(r.h._instances[INST].state)
        self.assertFalse(tap_as(r, B, 'run_abort'))
        self.assertEqual(r.overlay()['dialog'], 'err_access')
        self.assertEqual(' '.join(r.overlay()['lines']), 'This run was started by another user. It can be viewed, not changed.')
        ov_as(r, B, 'OK')
        self.assertEqual(r.h._instances[INST].state, before)
        op = r.val('operation.active_id')
        self.assertTrue(r.call(B, 'panel.switch', input_id='mains_switch', position='off')['accepted'])
        self.assertFalse(r.val('power.enabled'))
        self.assertEqual(r.val('reports.list')[-1]['abort_comment'], 'Aborted (power off)')
        self.assertNotEqual(r.h._instances[INST].operations[op]['status'], 'running')


class FixRoundB(unittest.TestCase):
    def test_prime_without_an_assigned_bottle_is_a_state_refusal(self):
        # run.prime on an inlet with no solvent bottle assigned was INVALID_ARGUMENT (a schema-style code for a state
        # problem); it is now PRECONDITION_FAILED with a message naming the cause; the state is unchanged.
        r = Rig()
        before = copy.deepcopy(r.h._instances[INST].state)
        e = r.call(r.A, 'run.prime', expect='rejected', inlet='S1', volume_m3=15e-6)['error']
        self.assertEqual(e['code'], 'PRECONDITION_FAILED')
        self.assertIn('No solvent bottle is assigned to inlet S1', e['message'])
        self.assertEqual(r.h._instances[INST].state, before)
        self.assertEqual(r.call(r.A, 'run.prime', expect='rejected', inlet='S1', volume_m3=30e-6)['error']['code'], 'INVALID_ARGUMENT')  # schema
        self.assertEqual(rt.EXTRAHERA_ASSET_VERSION, '1.3.1')


if __name__ == '__main__':
    unittest.main()
