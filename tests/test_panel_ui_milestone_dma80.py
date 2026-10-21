"""front-panel tests for CHEM-086 milestone_dma80 (EasyCONTROL terminal; panel_ui.json + scripts/asset_runtime/_panel_ui.py):
python3 -m pytest tests/test_panel_ui_milestone_dma80.py -q

Every gesture goes through the session API (panel.switch / panel.touch, the runtime's normal invoke path); widgets are found by id in the
structured frame (panel.frame, evaluator) and tapped at their centre.  Host actions that are not terminal operations (placing boats with the
dust cover, opening the oxygen valve, stepping the clock) use the evaluator / agent commands as in the instrument tests.  Themes: the five key
procedures of the menu survey; the sample table (entries, [ug/Kg] from the entered amount, statistics), method, calibration and audit trail;
panel = direct capability (same runtime state); refusals shown as the terminal's dialogs; power gate; no time-outs; unsupported tabs; render
determinism and the evidence PNGs; contract entries.  The spec is approximate (login-only manual)."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import milestone_dma80 as rt  # noqa: E402

INST = 'dma80_01'
TERM = 'terminal'
OUT = ROOT / 'outputs/panel_ui/milestone_dma80'
CONTRACT = ROOT / 'assets/expansion_batch04/t3_analytical/milestone_dma80/asset.contract.json'
SURVEY_TASKS = ['switch on and warm up', 'check system blank', 'run a sample', 'clean crucibles', 'calibrate with solid SRM']
DONE_TASKS = set()
UBA = dict(dry_t=100.0, dry_s=0.05, dec_t=850.0, dec_s=180.0, purge_s=60.0)   # UBA p.3: 100 C - 0 s - 850 C - 180 s - 60 s (0 s below the runtime minimum)


class Rig:
    """One analyzer with its terminal, an agent and an evaluator session."""

    def __init__(self, method=None, **plant):
        self.h = rt.MilestoneDma80Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **(rt.PLANT_EXAMPLE | plant))
        self.call(self.A, 'method.configure', **(rt.METHOD_EXAMPLE | (method or {})))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        ok = r['status'] in ('succeeded', 'accepted')
        assert ok if expect == 'succeeded' else r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap                                          # commands and gestures take no simulated time
        return r['result'] if ok else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    # ---- host actions (not terminal operations)
    def load(self, i, kind='sample', mass_kg=1e-4):
        a = {'blank': rt.BLANK_EXAMPLE, 'sample': rt.SAMPLE_EXAMPLE, 'standard': rt.SAMPLE_EXAMPLE | dict(kind='standard')}[kind]
        self.call(self.E, 'dust_cover.set', opened=True)
        self.call(self.E, 'sample.load', **(a | dict(slot_index=i, mass_kg=mass_kg if kind != 'blank' else a['mass_kg'])))
        self.call(self.E, 'dust_cover.set', opened=False)

    def warm(self):
        self.switch('on')
        self.call(self.A, 'gas.set', enabled=True)                              # oxygen supply valve (external)
        self.h.step(.3)
        assert self.val('instrument.ready')

    def run(self):
        for _ in range(200000):
            if self.h.phase != 'running':
                return self.h.phase
            self.h.step(.05)
        raise AssertionError('run did not finish')

    # ---- gestures
    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='main_switch', position=pos)['accepted']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TERM, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def row(self, wid, first):
        """Tap the row of a list or form widget whose first cell is this text (dragging the rows until it shows)."""
        for _ in range(12):
            w = self.widgets()[wid]
            firsts = [c[0] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            down = w['scroll'] + len(w['cells']) < w['count']
            if down:
                self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
            else:
                self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
        raise AssertionError(first)

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def type(self, text):
        """Type on the open keyboard (the shown text, selected on opening, is cleared first)."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard'
        if ov['field']:
            self.ov('Del')
        numeric = 'q' not in {b['label'] for b in ov['buttons']}
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
            else:
                if mode == 'sym':
                    self.ov('abc')
                if (self.overlay()['mode'] == 'upper') != ch.isupper():
                    self.ov('Shift')
                self.ov(ch)
        assert self.overlay()['field'] == text, (self.overlay()['field'], text)

    def entry(self, pos, name, grams, stats=False):
        """Sample-table row through the terminal: select the row, Edit, name and amount on the keyboards, Save."""
        self.page_to('measure')
        while self.frame()['ui']['vars']['sel_pos'] != pos - 1:
            self.row('m_table', str(pos))
        assert self.tap('m_edit')
        self.ov('Samplename')
        self.type(name)
        self.ov('OK')
        self.ov('Amount')
        self.type(f'{grams:.4f}')
        self.ov('OK')
        if stats:
            self.ov('Statistics (Sigma)')
            self.ov('yes')
        return self.ov('Save')

    def page_to(self, tab):
        """Top tab (measure / method / cal / system) of the current page; from the System pages through the back arrow."""
        ws = self.widgets()
        if not any(k.endswith('_tab_' + tab) for k in ws):
            self.tap('o_back' if 'o_back' in ws else 't_back')
            ws = self.widgets()
        return self.tap(next(k for k in ws if k.endswith('_tab_' + tab)))

    def method(self, **values):
        """Method tab: type each program value into its row, then Save."""
        self.page_to('method')
        labels = dict(dry_t='Drying temperature', dry_s='Drying time', dec_t='Incineration temperature', dec_s='Incineration time',
                      purge_s='Rinsing (purge) time', amal_s='Amalgam heating', rec_s='Measurement time')
        for k, v in values.items():
            self.row('me_form', labels[k])
            self.type(f'{v:g}')
            assert self.ov('OK'), (k, v, self.overlay())
        return self.tap('me_save')

    def start(self):
        self.page_to('measure')
        return self.tap('m_start')

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][TERM]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def overlay(self):
        return self.disp().get('overlay')

    def dialog(self):
        ov = self.overlay()
        return ov.get('dialog') if ov else None

    def table(self):
        """Sample table rows as shown: {pos: cells} (scrolling through all rows)."""
        self.page_to('measure')
        out = {}
        w = self.widgets()['m_table']
        x, y, ww, hh = w['rect']
        while self.widgets()['m_table']['scroll'] > 0:
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
        for _ in range(10):
            w = self.widgets()['m_table']
            out.update({c[0]: c for c in w['cells']})
            if w['scroll'] + len(w['cells']) >= w['count']:
                break
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        return out

    def png(self):
        r = self.call(self.A, 'panel.capture', display_id=TERM)
        return Path(r['image']['path']).read_bytes()


def calibrated():
    """Blank and three SRM standards entered, run and fitted only through the terminal (NIST 2448, 50 / 100 / 200 mg)."""
    r = Rig()
    r.load(0, 'blank')
    for i, m in ((1, 5e-5), (2, 1e-4), (3, 2e-4)):
        r.load(i, 'standard', m)
    r.warm()
    assert r.entry(1, 'blank', 0.0)
    for pos, g in ((2, 0.05), (3, 0.1), (4, 0.2)):
        assert r.entry(pos, f'srm {pos - 1}', g)
    assert r.start()
    assert r.run() == 'succeeded'
    r.page_to('cal')
    assert r.tap('c_new')
    assert r.row('c_list', 'cal_1')
    assert r.dialog() == 'd_cal_use'
    assert r.ov('Use')
    return r


class SurveyProcedures(unittest.TestCase):
    """The five key procedures of the menu survey, done only with terminal gestures (plus the host's boats, oxygen valve and clock)."""

    @classmethod
    def setUpClass(cls):
        cls.cal = calibrated()

    def test_switch_on_and_warm_up(self):
        r = Rig()
        img = r.h.panel.render(TERM)
        self.assertEqual(int(img.max()), 0)                                    # analyzer off: terminal dark
        self.assertFalse(r.touch(320, 240))                                    # and touches do nothing
        self.assertTrue(r.switch('on'))                                         # 1 main switch at the front: terminal starts
        self.assertEqual((r.val('power.enabled'), r.frame()['screen'], r.frame()['ui']['page']), (True, 'gui', 'measure'))
        self.assertEqual(r.text('m_status'), 'Not ready (heating up, cover, oxygen)')
        r.call(r.A, 'gas.set', enabled=True)                                    # oxygen supply open (before switching on, UBA p.3)
        r.h.step(.3)                                                            # 2 heaters reach start temperature
        self.assertEqual((r.val('instrument.ready'), r.text('m_status')), (True, 'Ready'))
        self.assertGreater(int(r.h.panel.render(TERM).max()), 0)
        r.page_to('system')
        self.assertIn('Power on', [c[1] for c in r.widgets()['t_list']['cells']])
        DONE_TASKS.add('switch on and warm up')

    def test_check_system_blank(self):
        r = Rig()
        r.warm()
        r.page_to('method')
        r.row('me_form', 'Drying time')
        r.type('0')
        self.assertFalse(r.ov('OK'))                                            # 0 s is below the runtime minimum: refused
        self.assertEqual(r.overlay()['error'], 'Out of range')
        r.ov('Cancel')
        self.assertTrue(r.method(**UBA))                                        # 1 program 100 C - 0.05 s - 850 C - 180 s - 60 s
        m = r.h.method
        self.assertEqual((m['drying_temperature_k'], m['drying_s'], m['decomposition_temperature_k'], m['decomposition_s'], m['purge_s']),
                         (373.15, 0.05, 1123.15, 180.0, 60.0))
        self.assertEqual({k: m[k] for k in ('catalyst_temperature_k', 'release_temperature_k', 'release_s', 'record_s', 'window_s')},
                         {k: rt.METHOD_EXAMPLE[k] for k in ('catalyst_temperature_k', 'release_temperature_k', 'release_s', 'record_s', 'window_s')})
        r.load(0, 'blank')                                                      # empty boat (the runtime's blank needs a boat)
        self.assertTrue(r.entry(1, 'blank', 0.0))
        self.assertTrue(r.start())                                              # 2 press start
        self.assertEqual(r.text('m_status')[:14], 'Measuring Pos ')
        self.assertEqual(r.run(), 'succeeded')
        row = r.table()['1']
        self.assertEqual((row[1], row[3][:3]), ('blank', 'ok '))               # 3 result shown in the table
        self.assertTrue(r.val('blank.valid'))
        self.assertEqual(r.val('table.entries')[0]['record_id'], 'record_1')
        DONE_TASKS.add('check system blank')

    def test_calibrate_with_solid_srm(self):
        r = copy.deepcopy(self.cal)
        self.assertTrue(r.val('calibration.ready'))
        curve = json.loads(r.call(r.A, 'calibration.read', curve_id='cal_1')['curve_json'])
        self.assertEqual([o['record_id'] for o in curve['observations']], ['record_2', 'record_3', 'record_4'])
        cells = {c[0]: c for c in r.widgets()['c_list']['cells']}
        self.assertEqual((cells['cal_1'][1], cells['cal_1'][4]), ('3', 'active'))
        d = Rig()                                                                # the same fit through the direct capability
        d.h = copy.deepcopy(self.cal.h)
        d.h._curves.clear()
        d.h._selected_curve = None
        d.A, d.E = d.h.session('agent'), d.h.session('judge')
        fit = d.call(d.A, 'calibration.fit', curve_id='cal_1', record_ids=['record_2', 'record_3', 'record_4'])
        self.assertAlmostEqual(fit['slope_s_kg'], curve['slope_s_kg'], places=6)
        DONE_TASKS.add('calibrate with solid SRM')

    def test_run_a_sample(self):
        r = copy.deepcopy(self.cal)
        r.load(4, 'sample', 8e-5)                                               # 1 weighed boat into the tray (host)
        self.assertTrue(r.entry(5, 'fish 1', 0.08, stats=True))                 # weight recorded in EasyCONTROL
        self.assertTrue(r.start())                                              # 2 run the program
        self.assertEqual(r.run(), 'succeeded')
        row = r.table()['5']
        hg_ng = r.val('result.mercury_mass_kg') * 1e12
        self.assertEqual(row[5], f'{hg_ng:.4f}')
        self.assertEqual(row[6], f'{hg_ng / 80.0 * 1e3:.4f}')                  # ug/kg = ng / mg * 1000
        self.assertEqual(row[7], '[x]')
        self.assertEqual(r.text('m_stat_mean'), f'm= {hg_ng / 80.0 * 1e3:.4f} ug/Kg')
        self.assertTrue(r.entry(5, 'fish 1', 0.1))                              # reprocessing: another amount recalculates ug/kg
        self.assertEqual(r.table()['5'][6], f'{hg_ng / 100.0 * 1e3:.4f}')
        r.tap('m_sub_result')
        self.assertEqual(r.text('r_v6'), f'{hg_ng:.4f}')
        DONE_TASKS.add('run a sample')

    def test_clean_crucibles(self):
        r = Rig()
        for i in range(3):
            r.load(i, 'blank')                                                  # 1 empty crucibles in the sample changer
        r.warm()
        self.assertTrue(r.method(**UBA))                                        # 2 program 100 C - 0.05 s - 850 C - 180 s - 60 s
        for pos in (1, 2, 3):
            self.assertTrue(r.entry(pos, 'boat', 0.0))
        self.assertTrue(r.start())                                              # 3 start: burned out one after another
        self.assertEqual(r.run(), 'succeeded')
        self.assertEqual([r.table()[str(p)][3][:2] for p in (1, 2, 3)], ['ok', 'ok', 'ok'])
        r.page_to('system')
        texts = [c[1] for c in r.widgets()['t_list']['cells']]
        self.assertIn('Sequence started (3 samples)', texts)
        DONE_TASKS.add('clean crucibles')


class CallerRules(unittest.TestCase):
    """Terminal gestures made through a session act as that session's caller (1.1.1): a sequence started with Start belongs to the caller, and another
    agent can neither stop it, switch the analyzer or the carrier off, delete its results nor reprocess them - on the terminal or directly."""
    def test_terminal_started_sequence_belongs_to_the_caller(self):
        r = Rig(method=dict(decomposition_s=5.0))
        r.load(0, 'blank')
        r.warm()
        r.entry(1, 'blank', 0.0)
        self.assertTrue(r.start())
        op = r.h.operation_id
        self.assertEqual((r.val('measurement.phase'), r.h._owners.get(op)), ('running', 'agent'))
        self.assertEqual(r.A.poll(op)['status'], 'running')
        r.h.register_principal('agent2', 'agent')
        a, b = r.A, r.h.session('agent2')
        with self.assertRaises(rt.ActionRejected):
            b.poll(op)
        r.h.step(.5)
        before = r.h.read('evaluator')
        r.A = b                                                                 # agent 2 at the terminal
        r.page_to('measure')
        self.assertFalse(r.tap('m_stop'))
        self.assertEqual(r.dialog(), 'd_err_access_denied')
        r.ov('OK')
        r.A = a
        for cap, args in (('measurement.stop', {}), ('gas.set', dict(enabled=False)),
                          ('table.set_entry', dict(slot_index=0, name='other', amount_kg=1e-4, in_statistics=True)),   # lead rule 10-19: nor change its rows
                          ('table.clear_entry', dict(slot_index=0))):
            res = b.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual((res['status'], res['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        self.assertEqual(r.h.read('evaluator'), before)
        self.assertEqual(r.A.cancel(op, 'a-cancel')['status'], 'succeeded')
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')
        res = b.invoke(instance_id=INST, capability_id='panel.switch', arguments=dict(input_id='main_switch', position='off'), request_id='b-main')
        self.assertTrue(res['result']['accepted'])                               # 1.2.0: the main switch is physical: never refused
        self.assertFalse(r.val('power.enabled'))

    def test_results_of_another_agent_cannot_be_deleted_or_reprocessed(self):
        r = Rig()
        r.load(0, 'blank')
        r.warm()
        r.entry(1, 'blank', 0.0)
        r.start()
        self.assertEqual(r.run(), 'succeeded')
        rid = r.val('table.entries')[0]['record_id']
        r.h.register_principal('agent2', 'agent')
        a, b = r.A, r.h.session('agent2')
        res = b.invoke(instance_id=INST, capability_id='records.remove', arguments=dict(record_id=rid), request_id='b1')
        self.assertEqual((res['status'], res['error']['code']), ('rejected', 'ACCESS_DENIED'))
        r.A = b
        self.assertFalse(r.entry(1, 'blank', 0.001))                            # a new weight would reprocess agent 1's result
        self.assertEqual(r.dialog(), 'd_err_access_denied')
        r.ov('OK')
        r.A = a
        self.assertEqual(r.val('table.entries')[0]['amount_kg'], 0.0)
        self.assertIn(rid, r.call(r.A, 'records.list')['record_ids'])
        self.assertTrue(r.call(r.A, 'records.remove', record_id=rid)['removed'])  # the owner may delete it


class EnteredWeight(unittest.TestCase):
    """Sync audit R1 (1.1.1): the weight typed into the sample table is the one the terminal divides by, in the stored record as on the screen."""
    def test_record_and_screen_use_the_entered_weight(self):
        r = calibrated()
        r.load(4, 'sample', 8e-5)                                               # an 80 mg boat ...
        self.assertTrue(r.entry(5, 'fish', 0.16))                               # ... typed in as 160 mg
        r.start()
        self.assertEqual(r.run(), 'succeeded')
        rec = r.call(r.A, 'result.read')
        hg = rec['mercury_mass_kg']
        self.assertIsNotNone(hg)
        self.assertEqual((rec['entered_amount_kg'], rec['sample_mass_kg']), (1.6e-4, 8e-5))
        self.assertAlmostEqual(rec['mercury_mass_fraction_kg_kg'], hg / 1.6e-4, delta=1e-12 * hg / 1.6e-4)
        self.assertEqual(r.table()['5'][6], f'{rec["mercury_mass_fraction_kg_kg"] * 1e9:.4f}')   # screen [ug/Kg] = stored record
        self.assertEqual(r.val('result.mass_fraction_kg_kg'), rec['mercury_mass_fraction_kg_kg'])
        self.assertTrue(r.entry(5, 'fish', 0.08))                               # reprocessing with the right weight
        again = r.call(r.A, 'records.read', record_id=rec['record_id'])
        self.assertEqual(again['entered_amount_kg'], 8e-5)
        self.assertAlmostEqual(again['mercury_mass_fraction_kg_kg'], hg / 8e-5, delta=1e-12 * hg / 8e-5)
        self.assertEqual(r.table()['5'][6], f'{again["mercury_mass_fraction_kg_kg"] * 1e9:.4f}')
        self.assertEqual(r.call(r.A, 'sequence.read')['completed_records'][-1]['mercury_mass_fraction_kg_kg'], again['mercury_mass_fraction_kg_kg'])

    def test_direct_run_without_a_table_row_keeps_the_host_weight(self):
        r = Rig()
        r.load(0, 'blank')
        r.warm()
        r.call(r.A, 'sequence.start', slot_indices=[0])
        self.assertEqual(r.run(), 'succeeded')
        rec = r.call(r.A, 'result.read')
        self.assertIsNone(rec['entered_amount_kg'])


class TerminalFunctions(unittest.TestCase):
    def test_entry_validation_clear_and_scroll(self):
        r = Rig()
        r.warm()
        r.row('m_table', '12')
        r.row('m_table', '12')
        self.assertEqual(r.dialog(), 'd_entry')
        r.ov('Samplename')
        r.type('crm')
        r.ov('OK')
        self.assertFalse(r.ov('Save'))                                          # amount missing: message, nothing stored
        self.assertEqual(r.dialog(), 'd_missing')
        r.ov('OK')
        r.ov('Amount')
        r.type('2')
        self.assertFalse(r.ov('OK'))                                            # above the 1.5 g boat range
        self.assertEqual(r.overlay()['error'], 'Out of range')
        r.type('0.0500')
        r.ov('OK')
        self.assertTrue(r.ov('Save'))
        self.assertEqual(r.val('table.entries'), [dict(slot_index=11, name='crm', amount_kg=5e-5, in_statistics=False, record_id=None)])
        self.assertEqual(r.table()['12'][1:3], ['crm', '0.0500g'])
        self.assertTrue(r.tap('m_clear'))
        self.assertEqual((r.val('table.entries'), r.table()['12'][1]), ([], ''))

    def test_refusals_show_terminal_dialogs(self):
        r = Rig()
        r.switch('on')
        self.assertFalse(r.start())                                             # empty table
        self.assertEqual(r.dialog(), 'd_empty')
        r.ov('OK')
        r.load(0, 'sample')
        r.entry(1, 'fish', 0.1)
        before = r.h.read('evaluator')
        self.assertFalse(r.start())                                             # cold, no oxygen
        self.assertEqual(r.dialog(), 'd_err_not_ready')
        self.assertEqual(r.h.read('evaluator'), before)
        r.ov('OK')
        r.call(r.A, 'gas.set', enabled=True)
        r.h.step(.3)
        self.assertFalse(r.start())                                             # a sample before any blank
        self.assertEqual(r.dialog(), 'd_err_matched_blank_required')
        r.ov('OK')
        r.entry(2, 'nothing', 0.1)                                              # row without a boat
        r.load(2, 'blank')
        r.entry(3, 'blank', 0.0)
        r.tap('m_clear')
        r.entry(1, 'fish', 0.1)
        self.assertFalse(r.start())
        self.assertEqual(r.dialog(), 'd_err_sample_missing_or_processed')
        r.ov('OK')
        self.assertEqual(r.val('measurement.phase'), 'idle')

    def test_busy_stop_and_power_off(self):
        r = Rig(method=dict(decomposition_s=5.0))
        r.load(0, 'blank')
        r.warm()
        r.entry(1, 'blank', 0.0)
        r.start()
        r.h.step(.5)
        self.assertEqual(r.widgets()['m_stop']['text'], 'STOP')
        self.assertNotIn('m_start', r.widgets())
        self.assertFalse(r.method(dry_t=120))                                   # method locked while measuring
        self.assertEqual(r.dialog(), 'd_err_busy')
        r.ov('OK')
        r.page_to('measure')
        self.assertTrue(r.tap('m_stop'))
        self.assertEqual(r.val('measurement.phase'), 'cancelled')
        self.assertEqual(r.table()['1'][3], 'stopped')
        r2 = Rig(method=dict(decomposition_s=5.0))
        r2.load(0, 'blank')
        r2.warm()
        r2.entry(1, 'blank', 0.0)
        r2.start()
        r2.h.step(.5)
        self.assertTrue(r2.switch('off'))                                       # main switch off cancels the run
        self.assertEqual((r2.val('measurement.phase'), r2.val('power.enabled')), ('cancelled', False))
        self.assertFalse(r2.touch(560, 380))
        self.assertEqual(int(r2.h.panel.render(TERM).max()), 0)
        r2.switch('on')
        self.assertEqual(r2.frame()['ui']['page'], 'measure')
        r2.page_to('system')
        texts = [c[1] for c in r2.widgets()['t_list']['cells']]
        self.assertIn('Measurement stopped: power removed', texts)

    def test_trail_filters_and_device_error(self):
        r = Rig(oxygen_supply_mol=1.5e-4)                                       # oxygen runs out during the run
        r.load(0, 'blank')
        r.warm()
        r.entry(1, 'blank', 0.0)
        r.start()
        self.assertEqual(r.run(), 'failed')
        r.page_to('system')
        texts = lambda: [c[1] for c in r.widgets()['t_list']['cells']]    # noqa: E731
        self.assertIn('Device error: oxygen supply depleted', texts())
        r.tap('t_f_errors')
        self.assertEqual(texts(), ['Device error: oxygen supply depleted'])
        r.tap('t_f_system')
        self.assertEqual(texts(), ['Power on'])
        r.tap('t_f_access')
        self.assertEqual(texts(), [])
        r.tap('t_f_application')
        self.assertIn('Sample table: Pos 1 blank', texts())
        self.assertEqual(r.table()['1'][3], 'error')

    def test_unsupported_items_and_no_usb(self):
        r = Rig()
        r.switch('on')
        for tab in ('m_tab_menu', 'm_sub_links'):
            before = r.frame()['ui']['page']
            self.assertFalse(r.tap(tab))
            f = r.frame()
            self.assertEqual(f['ui']['page'], before)
            self.assertIn('not documented', f['ui']['unsupported'])
        r.page_to('system')
        for tab in ('t_t_panel', 't_t_users', 't_t_update', 't_t_lan'):
            self.assertFalse(r.tap(tab))
            self.assertEqual((r.frame()['ui']['page'], r.text('o_title')), ('sys_other', tab[4:].upper() if tab == 't_t_lan' else tab[4:].title()))
            r.tap('o_t_trail')
        for tab, title in (('t_settings', 'Settings'), ('o_service', 'Service')):
            self.assertFalse(r.tap(tab))
            self.assertEqual(r.text('o_title'), title)
        self.assertNotIn('o_t_trail', r.widgets())                             # Terminal row hidden under Service
        r.tap('o_terminal')
        self.assertFalse(r.tap('t_save'))
        self.assertEqual(r.overlay()['lines'], ['No USB memory found.'])

    def test_no_time_outs(self):
        r = Rig()
        r.warm()
        r.page_to('cal')
        r.h.step(600.0)
        self.assertEqual(r.frame()['ui']['page'], 'calibration')

    def test_panel_and_direct_commands_give_the_same_state(self):
        p, d = Rig(), Rig()
        for r in (p, d):
            r.load(0, 'blank')
            r.load(1, 'sample', 8e-5)
            r.warm()
        p.method(dec_s=1.5, rec_s=1.0)
        p.entry(1, 'blank', 0.0)
        p.entry(2, 'fish', 0.08, stats=True)
        p.start()
        d.call(d.A, 'method.configure', **(rt.METHOD_EXAMPLE | dict(decomposition_s=1.5, record_s=1.0)))
        d.call(d.A, 'table.set_entry', slot_index=0, name='blank', amount_kg=0.0, in_statistics=False)
        d.call(d.A, 'table.set_entry', slot_index=1, name='fish', amount_kg=8e-5, in_statistics=True)
        d.call(d.A, 'sequence.start', slot_indices=[0, 1])
        for r in (p, d):
            r.run()
        self.assertEqual(p.h.read('evaluator'), d.h.read('evaluator'))
        self.assertEqual([(e['time_s'], e['text']) for e in p.h.trail], [(e['time_s'], e['text']) for e in d.h.trail])

    def test_only_agent_capabilities_behind_the_panel(self):
        caps = rt._panel_ui.spec_capabilities(rt.PANEL_SPEC)
        self.assertEqual(sorted(caps), ['calibration.fit', 'calibration.select', 'measurement.stop', 'method.configure', 'power.set',
                                        'sequence.start', 'table.clear_entry', 'table.set_entry'])
        self.assertTrue(all(rt.CAPS[k][5] == 'agent' for k in caps))
        self.assertTrue(all(rt.CAPS[k][5] == 'agent' for k in ('panel.switch', 'panel.touch', 'panel.capture')))
        self.assertEqual(rt.CAPS['panel.frame'][5], 'evaluator')
        r = Rig()
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call(r.A, 'panel.touch', expect='rejected', display_id=TERM, x=640, y=0)['error']['code'], 'INVALID_ARGUMENT')

    def test_render_determinism_and_capture(self):
        r = copy.deepcopy(SurveyProcedures.cal) if hasattr(SurveyProcedures, 'cal') else calibrated()
        r.page_to('measure')
        a, b = r.h.panel.render(TERM), r.h.panel.render(TERM)
        self.assertEqual(a.shape, (480, 640, 3))
        self.assertTrue((a == b).all())
        png = r.png()
        self.assertEqual(png, r.png())
        r.h.panel_capture_dir = None
        self.assertEqual(hashlib.sha256(png).hexdigest(), hashlib.sha256(r.h.panel.png_bytes(TERM)[0]).hexdigest())
        f1 = json.dumps(r.frame(), sort_keys=True)
        self.assertEqual(f1, json.dumps(r.frame(), sort_keys=True))
        r.tap('m_sub_signal')
        self.assertFalse((r.h.panel.render(TERM) == a).all())

    def test_evidence_renders(self):
        """home (sample table with results), menu (Method page), error (not-ready dialog), signal, calibration, trail and entry screens."""
        OUT.mkdir(parents=True, exist_ok=True)
        r = calibrated()
        shots = {}
        r.page_to('measure')
        shots['home'] = r.h.panel.save_png(TERM, OUT / 'home.png')
        r.page_to('method')
        shots['menu'] = r.h.panel.save_png(TERM, OUT / 'menu.png')
        r.page_to('cal')
        shots['calibration'] = r.h.panel.save_png(TERM, OUT / 'calibration.png')
        r.page_to('system')
        shots['trail'] = r.h.panel.save_png(TERM, OUT / 'trail.png')
        e = Rig()
        e.switch('on')
        e.load(0, 'blank')
        e.entry(1, 'blank', 0.0)
        e.start()
        shots['error'] = e.h.panel.save_png(TERM, OUT / 'error.png')
        e.ov('OK')
        e.row('m_table', '1')
        e.ov('Samplename')
        shots['entry'] = e.h.panel.save_png(TERM, OUT / 'entry.png')
        s = Rig(method=dict(decomposition_s=5.0))
        s.load(0, 'blank')
        s.warm()
        s.entry(1, 'blank', 0.0)
        s.start()
        s.h.step(1.0)
        s.tap('m_sub_signal')
        shots['signal'] = s.h.panel.save_png(TERM, OUT / 'signal.png')
        for k, v in shots.items():
            self.assertEqual(v['sha256'], hashlib.sha256(Path(v['path']).read_bytes()).hexdigest())
        (OUT / 'renders.json').write_text(json.dumps({k: dict(v, path=str(Path(v['path']).relative_to(ROOT))) for k, v in shots.items()},
                                                     indent=1) + '\n')

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['approximate']), ('panel_ui', 'touchscreen_gui', True))
        for k in list(rt.PANEL_CAPS) + ['table.set_entry', 'table.clear_entry']:
            self.assertIn(k, c['capabilities'])
            for cid in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][cid]['capability_ids'])
        term = c['components']['control_terminal']           # 2026-10-20 panel geometry: the terminal and its touch screen are modelled
        self.assertEqual(term['geometry_status'], 'bound')
        self.assertIn('milestone_dma80__terminal_screen', {x['selector'] for x in term['geometry']})
        self.assertIn('table.entries', c['state_slots'])
        self.assertIn('entered_amount_kg', c['capabilities']['records.read']['output_schema']['properties'])
        self.assertEqual(sorted(f['id'] for f in c['extensions']['chemistry_lab.fixes']), ['entered_weight_in_record', 'panel_caller_rules', 'panel_geometry_terminal_and_rocker', 'sweep_version_rule'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ROOT / c['artifacts']['panel_ui']['path']).read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
