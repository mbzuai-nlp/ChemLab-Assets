"""front-panel tests for CHEM-100 gas_flavourspec25gc (panel_ui.json + scripts/asset_runtime/_panel_ui.py): python3 -m pytest tests/test_panel_ui_gas_flavourspec25gc.py -q

Every gesture goes through the session API (panel.touch on the touch screen, panel.turn / panel.press on the rotary knob, panel.switch on the power
switch).  Each check looks at the runtime state and at the screen (panel.frame; pixels for brightness).  Themes: the six key procedures of the menu
survey (EPC gas type, select a program, create a program, manual-injection run, copy files to USB, cleaning mode); Defaults window with the number
pad (off by clearing), drift voltage polarity, averaging and trigger duration; recording and storage minutes; trigger mode with generated sample names;
standby and remote mode; System settings forms (IP, view, brightness, screen saver, fan, overheat, contamination, column, tube, septum, alarm), snapshot
and thresholds, date / time, transfer (delete files, connection test), error information window and log; refusals (no program, recording active,
drift gas flow, power off) and the low-pressure alarm; panel = direct capability; render determinism and the PNGs of outputs/panel_ui/gas_flavourspec25gc/;
contract entries of the panel capabilities."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import gas_flavourspec25gc as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'gcims_01'
SCR = 'screen'
ASSET_DIR = ROOT / 'assets/expansion_batch04/t3_analytical/gas_flavourspec25gc'
OUT = ROOT / 'outputs/panel_ui/gas_flavourspec25gc'
SPEC = rt.PANEL_SPEC
SURVEY_TASKS = ['Set the EPC gas type', 'Select a measurement program', 'Create a measurement program', 'Run a measurement with manual injection',
                'Copy measurement files to USB', 'Start cleaning mode']
DONE_TASKS = set()


class Rig:
    """A configured GC-IMS (plant, method, loaded vial) with an agent and an evaluator session; gestures only through panel.* capabilities."""

    def __init__(self, on=True, gas=True, sample=True, plant=None):
        self.h = rt.GasFlavourspec25gcRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        self.call(self.E, 'plant.configure', **(plant or copy.deepcopy(rt.PLANT_EXAMPLE)))
        self.call(self.A, 'method.configure', **copy.deepcopy(rt.METHOD_EXAMPLE))
        if sample:
            self.call(self.E, 'sample.load', **copy.deepcopy(rt.SAMPLE_EXAMPLE))
        if on:
            assert self.switch('on')
            self.step(30.0)
            if gas:
                self.call(self.A, 'gas.set', enabled=True)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        before = self.h.read('evaluator')
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in ((expect,) if expect != 'succeeded' else ('succeeded', 'accepted')), (cap, args, r['status'], r['error'])
        if r['status'] in ('succeeded', 'accepted'):
            assert self.h.time_s == t0, cap
        if r['status'] in ('succeeded', 'accepted') and cap in rt.FW_ERRORS:     # the firmware and panel commands declare all their immediate effects                                      # commands and gestures take no simulated time
            after = self.h.read('evaluator')
            changed = {k for k in after if json.dumps(after[k], sort_keys=True, default=str) != json.dumps(before[k], sort_keys=True, default=str)}
            assert not changed - set(rt.CAPS[cap][7]), (cap, sorted(changed - set(rt.CAPS[cap][7])))
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def step(self, s):
        self.h.step(float(s))

    def run_to_end(self, limit=600):
        for _ in range(limit):
            if self.h.phase != 'running':
                return
            self.step(1.0)

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def state(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

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
        x, y, ww, hh = self.widgets()[wid]['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label and b['id'] != 'field':                       # never the edited text itself
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def ov_row(self, field_id):
        for b in self.overlay()['buttons']:
            if b['id'] == 'row:' + field_id:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(field_id)

    def form_row(self, wid, field_id):
        w = self.widgets()[wid]
        k = w['row_ids'].index(field_id)
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def row(self, wid, k):
        w = self.widgets()[wid]
        return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def type(self, text, ok=True):
        """Type on the open keyboard (the shown text is replaced); '' clears it (the dedicated off / no action value)."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov['kind']
        labels = {b['label'] for b in ov['buttons']}
        if ov['field']:
            for _ in range(len(ov['field'])):
                self.ov('Del')
        for ch in text:
            if '7' in labels and 'q' not in labels:
                assert self.ov(ch), ch
                continue
            mode = self.overlay()['mode']
            if ch.isdigit():
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            elif ch == '_':
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if ch.isupper() != (self.overlay()['mode'] == 'upper'):
                    self.ov('Shift')
                self.ov(ch)
        return self.ov('OK') if ok else True

    def dialog(self):
        ov = self.overlay()
        return ov and ov.get('kind') == 'dialog' and (ov['title'], ov['lines'])

    def system(self, page):
        self.tap('tab_sys_info')
        cur = self.page()[1]
        return self.tap(f'side_{cur}_{page}')

    def settings_page(self, n):
        self.system('sys_settings1')
        for k in range(2, n + 1):
            self.tap(f'nav_sys_settings{k}_dn')
        assert self.page() == ('gui', f'sys_settings{n}'), self.page()

    def create_program(self, name='QC', end=120.0):
        self.tap('tab_programs')
        self.tap('pg_new')
        self.ov_row('pg_name')
        self.type(name)
        self.ov('OK')
        rows = self.widgets()['pg_rows']['cells']
        self.row('pg_rows', len(rows) - 1)                                          # the end-of-program line
        self.tap('pa_edit')
        self.ov_row('pe_end')
        self.type(f'{end:g}')
        self.ov('OK')


def runtime_state(h):
    r = h.read('evaluator')
    for k in ('device.clock', 'device.log'):
        r.pop(k)
    return json.dumps(dict(read=r, method=h.method), sort_keys=True, default=str)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_set_the_epc_gas_type(self):
        g = Rig()
        g.settings_page(3)
        g.tap('s3_epc')
        self.assertEqual(g.dialog()[0], 'EPC Settings')
        g.ov_row('epc_sel')
        g.ov('GC (carrier gas)')
        g.ov_row('epc_gas')
        g.ov('Air')
        p0 = g.state('device.parameters')['epc_gc_pressure_kpa']
        g.ov('Apply')
        self.assertEqual((g.state('device.settings')['epc.gc_gas'], g.state('device.settings')['epc.ims_gas']), ('air', 'nitrogen'))
        self.assertGreater(g.state('device.parameters')['epc_gc_pressure_kpa'], p0)          # air is more viscous than nitrogen
        self.assertEqual(g.text('s3_epc_v'), 'IMS nitrogen / GC air')
        DONE_TASKS.add('Set the EPC gas type')

    def test_create_and_select_a_program(self):
        g = Rig()
        g.create_program('QC', 120)
        g.tap('pa_new')                                                              # a flow ramp: E_GC 10 mL/min at 60 s
        g.ov_row('ac_time')
        g.type('60')
        g.ov_row('ac_gc')
        g.type('10')
        g.ov('OK')
        g.tap('pg_avg99')
        p = g.state('programs.list')['programs'][0]
        self.assertEqual((p['name'], p['end_s'], p['averages']), ('QC', 120.0, 99))
        self.assertEqual(p['actions'], [dict(time_s=0.0, rec='rec', e_ims=150.0, e_gc=2.0), dict(time_s=60.0, rec=None, e_ims=None, e_gc=10.0)])
        self.assertEqual(g.widgets()['pg_rows']['cells'][1], ['01:00.000', '|', '|', '10.0'])
        DONE_TASKS.add('Create a measurement program')
        g.create_program('Blank', 60)
        self.assertEqual(g.widgets()['pg_list']['cells'], [['QC (AVG 99)', '02:00'], ['Blank (AVG 6)', '01:00']])
        g.row('pg_list', 0)                                                          # select a program, then Spectra
        self.assertEqual(g.state('programs.list')['selected'], 0)
        g.tap('tab_spectra')
        self.assertEqual(g.text('sp_prog'), 'QC')
        DONE_TASKS.add('Select a measurement program')
        g.tap('tab_programs')
        g.tap('pa_view')
        self.assertEqual(g.page(), ('gui', 'program_view'))
        pts = {s['label']: s['points'] for s in g.widgets()['pv_plot']['series']}
        self.assertEqual(pts['EPC_GC'], [[0.0, 2.0], [60.0, 10.0], [120.0, 10.0]])

    def test_run_a_measurement_with_manual_injection(self):
        g = Rig()
        g.create_program('QC', 120)
        g.tap('tab_spectra')
        self.assertEqual(g.widgets()['sp_start']['fill'] if 'fill' in g.widgets()['sp_start'] else 'primary', 'primary')   # flows match: no flashing
        g.tap('sp_start')
        self.assertEqual(g.dialog()[0], 'Manual injection')
        g.ov('YES')
        self.assertEqual(g.state('acquisition.phase'), 'running')
        self.assertTrue(g.text('sp_stop').startswith('Stop'))
        g.run_to_end()
        self.assertEqual(g.state('acquisition.phase'), 'succeeded')
        f = g.state('files.stored')
        self.assertEqual((len(f), f[0]['program'], f[0]['duration_s'], f[0]['name'][-4:]), (1, 'QC', 120.0, '.mea'))
        self.assertGreater(len(g.state('signal.integrate') if False else g.h.points), 0)
        DONE_TASKS.add('Run a measurement with manual injection')

    def test_copy_files_to_usb(self):
        g = Rig()
        g.system('sys_transfer')
        g.tap('tr_raw')
        self.assertEqual(g.dialog(), ('Error', ['No measurements stored.']))
        g.ov('OK')
        g.tap('tab_spectra')
        g.tap('sp_rec')
        g.step(30.0)
        g.tap('sp_rec')
        g.system('sys_transfer')
        self.assertEqual(g.text('tr_meas'), '1')
        g.tap('tr_raw')
        self.assertEqual(g.dialog()[0], 'Copy')
        g.ov('USB')
        self.assertEqual(g.dialog(), ('Error', ['No USB-Storage found.']))
        self.assertEqual(g.frame()['ui']['unsupported'], 'no USB storage volume is connected in the simulated configuration')
        g.ov('OK')
        DONE_TASKS.add('Copy measurement files to USB')

    def test_start_cleaning_mode(self):
        g = Rig()
        g.system('sys_modes')
        g.form_row('md_form', 'md_cl_h')
        g.type('1')
        self.assertEqual(g.state('device.settings')['modes.cleaning_h'], 1)
        g.tap('md_cl')
        self.assertEqual(g.page(), ('cleaning', 'clean'))
        self.assertEqual(g.text('mw_rem'), 'Remaining 01:00:00')
        self.assertEqual((g.h._target_k('ims'), g.h._target_k('injector')), (393.15, 473.15))   # heaters to their maxima, drift voltage off
        self.assertFalse(g.state('device.parameters')['drift_voltage_on'])
        g.tap('mw_hv')
        self.assertTrue(g.state('device.parameters')['drift_voltage_on'])
        g.step(1800.0)
        self.assertGreater(g.state('device.parameters')['t1_c'], 100.0)
        g.step(1800.0)
        self.assertEqual((g.page()[0], g.state('device.mode')['mode']), ('gui', 'normal'))
        DONE_TASKS.add('Start cleaning mode')


class Windows(unittest.TestCase):
    def test_defaults_window(self):
        g = Rig()
        g.tap('tab_defaults')
        g.tap('df_t1')
        g.type('50')
        g.tap('df_t4')
        g.type('')                                                                   # cleared: off
        g.tap('df_ims')
        g.type('100')
        g.tap('df_avg')
        g.type('12')
        g.tap('df_tr')
        g.type('200')
        g.tap('df_dv')
        d = g.state('device.defaults')
        self.assertEqual((d['t1_c'], d['t4_c'], d['epc_ims_ml_min'], d['averaging'], d['trigger_us'], d['polarity']), (50, None, 100, 12, 200, 'negative'))
        self.assertAlmostEqual(g.h.method['ims_k'], 323.15)
        self.assertEqual((g.widgets()['df_t4']['value'], g.text('df_t4_off')), (None, 'off'))
        g.tap('df_t3')
        g.type('250')
        self.assertEqual(g.overlay()['error'], 'Range: off, 5 - 200 °C')
        g.ov('Cancel')
        self.assertEqual(g.state('device.defaults')['t3_c'], 85.0)
        g.step(60.0)
        g.tap('tab_spectra')
        self.assertEqual(g.text('sp_hv'), 'Drift voltage -')

    def test_recording_and_program_refusals(self):
        g = Rig()
        g.tap('sp_start')
        g.ov('YES')
        self.assertEqual(g.dialog(), ('Message', ['No program selected.']))
        g.ov('OK')
        g.create_program('QC', 60)
        g.tap('tab_spectra')
        free = g.state('recording.state')['free_min']
        self.assertEqual(g.text('sp_rec_min'), f'{int(free)} min')
        g.tap('sp_rec')
        self.assertEqual((g.text('sp_rec'), g.state('recording.state')['on']), ('STOP', True))
        g.tap('sp_start')
        g.ov('YES')
        self.assertEqual(g.dialog(), ('Message', ['Stop recording first.']))
        g.ov('OK')
        g.step(60.0)
        g.tap('sp_rec')
        self.assertEqual(g.state('files.stored')[0]['duration_s'], 60.0)
        g.call(g.A, 'gas.set', enabled=False)
        g.tap('sp_start')
        g.ov('YES')
        self.assertEqual(g.dialog(), ('Message', ['Drift gas flow too low. Aborting...']))

    def test_trigger_mode_with_sample_names(self):
        g = Rig()
        g.create_program('T', 60)
        g.system('sys_modes')
        g.tap('md_trig')
        self.assertEqual(g.state('device.mode')['mode'], 'trigger')
        g.tap('tab_spectra')
        self.assertFalse(g.widgets()['sp_start']['enabled'])                        # the autosampler is the master
        g.tap('sp_names_btn')
        g.ov('Autogenerate samplenames')
        self.assertEqual(g.widgets()['sp_names']['cells'][:2], [['mea_1'], ['mea_2']])
        g.call(g.A, 'acquisition.start')                                             # the autosampler trigger
        g.run_to_end()
        f = g.state('files.stored')[-1]
        self.assertEqual((f['sample_name'], f['program'], f['kind']), ('mea_1', 'T', 'trigger'))
        self.assertEqual(g.widgets()['sp_names']['selected_rows'], [1])
        g.system('sys_modes')
        g.tap('md_trig')
        self.assertEqual(g.state('device.mode')['mode'], 'normal')

    def test_standby_and_remote_windows(self):
        g = Rig()
        g.system('sys_modes')
        g.form_row('md_form', 'md_sb_h')
        g.type('')
        g.tap('md_sb')
        self.assertEqual(g.page(), ('standby', 'standby'))
        self.assertEqual(g.text('mw_rem'), 'Remaining infinite')
        self.assertEqual(g.state('device.parameters')['epc_gc_ml_min'], 5.0)
        g.tap('mw_stop')
        self.assertEqual(g.page()[0], 'gui')
        g.system('sys_modes')
        g.tap('md_rem')
        self.assertEqual(g.page(), ('remote', 'remote'))
        g.call(g.A, 'acquisition.start', expect='rejected')
        g.tap('rm_stop')
        self.assertEqual(g.state('device.mode')['mode'], 'normal')

    def test_system_settings_forms(self):
        g = Rig()
        g.system('sys_info')
        self.assertEqual(g.text('in_ver'), '4.80')
        g.system('sys_settings1')
        g.form_row('s1_form', 's1_ip')
        g.type('10.0.0.7')
        self.assertEqual(g.state('device.settings')['network.ip'], '10.0.0.7')
        g.form_row('s1_form', 's1_ip')
        g.type('300.1.1.1')
        self.assertEqual(g.dialog(), ('Message', ['Invalid value.']))
        g.ov('OK')
        g.form_row('s1_form', 's1_bright')
        g.type('2')
        self.assertEqual(g.disp()['brightness'], 40.0)
        g.form_row('s1_form', 's1_fan_start')
        g.type('60')
        self.assertEqual(g.dialog(), ('Message', ['Invalid value.']))                 # start must stay below max
        g.ov('OK')
        g.form_row('s1_form', 's1_saver')
        g.type('1')
        g.step(61.0)
        self.assertEqual(g.page()[0], 'saver')
        g.touch(320, 240)
        self.assertEqual(g.page(), ('gui', 'spectra'))
        g.system('sys_settings1')
        g.form_row('s1_form', 's1_view')
        g.ov('Simplified')
        g.form_row('s1_form', 's1_append')
        g.ov('On')
        g.tap('tab_spectra')
        g.tap('sv_id')
        g.type('Batch7')
        self.assertEqual(g.state('device.settings')['view.id'], 'Batch7')
        g.settings_page(2)
        g.form_row('s2_form', 's2_after')
        g.ov('STANDBY')
        g.form_row('s2_form', 's2_cont_thr')
        g.type('150')
        g.tap('s2_ls')
        self.assertEqual(g.dialog()[1], ['Prepared for further use!', 'Currently not in function!'])
        g.ov('OK')
        g.settings_page(3)
        g.form_row('s3_form', 's3_sep')
        g.type('250')
        g.form_row('s3_form', 's3_tube')
        g.type('98')
        g.form_row('s3_form', 's3_alarm')
        g.type('880')
        g.form_row('s3_form', 's3_gas')
        g.ov('Air')
        s = g.state('device.settings')
        self.assertEqual((s['after_seq'], s['contamination.threshold_mv'], s['septum.max_injections'], s['drift.tube_mm'], s['alarm.frequency_hz'], s['drift.gas']),
                         ('standby', 150, 250, 98, 880, 'air'))
        self.assertGreater(g.state('device.parameters')['rip_position_ms'], 14.0)         # t = L^2 / (K V): a longer tube
        g.tap('s3_col')
        self.assertEqual(g.page(), ('gui', 'column'))
        g.tap('co_preset')
        g.ov('MXT-WAX 30 m x 0.53 mm')
        g.tap('co_apply')
        s = g.state('device.settings')
        self.assertEqual((s['column.name'], s['column.length_m'], s['column.polarity'], g.page()[1]), ('MXT-WAX', 30.0, 'polar', 'sys_settings3'))

    def test_date_time_and_default(self):
        g = Rig()
        g.system('sys_settings1')
        g.tap('s1_dt')
        g.ov_row('dt_zone')
        g.ov('Asia/Tokyo')
        self.assertEqual(g.state('device.clock')[11:13], '09')
        g.ov('Close')
        g.create_program('QC', 60)
        g.system('sys_settings1')
        g.tap('s1_default')
        g.ov('OK')
        self.assertEqual(g.state('programs.list')['programs'], [])
        g.tap('s1_save')
        self.assertEqual(g.dialog(), ('Error', ['No USB-Storage found.']))

    def test_snapshot_window_and_error_information(self):
        g = Rig()
        g.step(1.0)
        self.assertEqual([e['text'] for e in g.state('device.errors')], ['No snapshot found for current device settings'])
        fills = set()
        for _ in range(2):
            fills.add(g.widgets()['tab_errors']['fill'])
            g.step(1.0)
        self.assertEqual(fills, {'red', 'panel'})                                    # the ! tab blinks red
        g.tap('tab_errors')
        self.assertEqual(g.widgets()['er_list_current']['cells'][0][1:], ['DeviceSupervision', 'report', 'No snapshot found for current device settings'])
        g.settings_page(2)
        g.tap('s2_snap')
        self.assertEqual(g.text('sn_msg'), 'No snapshot found for current device settings')
        g.tap('sn_take')
        g.step(1.0)
        self.assertEqual(g.text('sn_msg'), 'All checked values in range')
        g.tap('sn_epc_gc_pressure')
        g.ov_row('th_hi')
        g.type('101')
        g.ov('OK')
        self.assertEqual(g.state('device.settings')['snapshot.epc_gc_pressure_hi'], 101)
        g.call(g.A, 'defaults.configure', epc_gc_ml_min=2.0)
        g.call(g.A, 'settings.configure', settings={'column.id_mm': 0.45})
        g.step(1.0)
        self.assertEqual(g.text('sn_msg'), 'At least one checked value is out of range')
        g.tap('tab_errors')
        g.tap('er_events')
        texts = [c[3] for c in g.widgets()['er_list_events']['cells']]
        self.assertIn('At least one checked value is out of range', texts)
        g.tap('sb_log')
        self.assertEqual(g.page(), ('gui', 'log'))
        self.assertTrue(any('New snapshot taken' in c[1] for c in g.widgets()['lg_list']['cells']))
        g.tap('lg_close')
        self.assertEqual(g.text('sb_log'), 'Log')

    def test_files_delete_and_connection_test(self):
        g = Rig()
        g.tap('sp_rec')
        g.step(10.0)
        g.tap('sp_rec')
        g.system('sys_transfer')
        g.tap('tr_delete')
        self.assertEqual(g.page(), ('gui', 'files'))
        g.row('fl_list', 0)
        g.tap('fl_del')
        self.assertEqual(g.state('files.stored'), [])
        g.tap('fl_back')
        g.tap('tr_server')
        g.ov_row('sv_ip')
        g.type('192.168.1.20')
        g.ov('Apply')
        self.assertEqual(g.state('device.settings')['server.ip'], '192.168.1.20')
        g.tap('tr_test')
        self.assertEqual(g.dialog(), ('Error', ['smb connection failed:']))
        g.ov('OK')
        g.tap('tr_mea')
        self.assertTrue(g.state('device.settings')['export.auto'])
        self.assertEqual(g.text('sb_export'), 'Exp on')

    def test_rotary_knob_moves_the_selection_and_activates(self):
        g = Rig()
        g.tap('tab_defaults')
        self.assertTrue(g.call(g.A, 'panel.turn', input_id='knob', detents=1)['accepted'])
        foc = g.frame()['ui']['focus']
        self.assertEqual(foc, 'df_t1')
        self.assertTrue(g.call(g.A, 'panel.press', input_id='knob_push')['accepted'])
        self.assertEqual(g.overlay()['kind'], 'keyboard')


class Rules(unittest.TestCase):
    def test_power_gate_and_low_pressure(self):
        g = Rig(on=False)
        self.assertEqual(g.page()[0], 'off')
        self.assertFalse(g.touch(320, 240))
        self.assertEqual(g.pixel_sum() if hasattr(g, 'pixel_sum') else int(g.h.panel.render(SCR).sum()), 0)
        g = Rig(plant=dict(rt.PLANT_EXAMPLE, gas_supply_mol=2e-4))
        g.step(400.0)
        g.step(20.0)
        self.assertTrue(g.state('device.parameters')['low_pressure'])
        self.assertIn('low_pressure', g.widgets())
        g.step(300.0)
        self.assertTrue(g.state('device.parameters')['heaters_off'])

    def test_unknown_inputs_rejected(self):
        g = Rig()
        r = g.call(g.A, 'panel.press', expect='rejected', input_id='key_x')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.A.invoke(instance_id=INST, capability_id='panel.frame', arguments={}, request_id='f1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_calls(self):
        a, b = Rig(), Rig()
        a.tap('tab_defaults')
        a.tap('df_t2')
        a.type('70')
        a.tap('df_gc')
        a.type('5')
        a.create_program('QC', 120)
        a.settings_page(3)
        a.form_row('s3_form', 's3_dv')
        a.type('2500')
        b.call(b.A, 'defaults.configure', t2_c=70.0)
        b.call(b.A, 'defaults.configure', epc_gc_ml_min=5.0)
        b.call(b.A, 'program.manage', op='create', name='QC')
        b.call(b.A, 'program.action', op='set_end', end_s=120.0)
        b.call(b.A, 'settings.configure', settings={'drift.voltage_v': 2500.0})
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        a.step(60.0)
        b.step(60.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))


class CallerRules(unittest.TestCase):
    """Sync audit 2026-10-19 (rule 2): a program started on the touch screen belongs to the caller; another agent cannot stop or switch it off."""

    def test_panel_started_program_belongs_to_the_caller(self):
        g = Rig()
        g.h.register_principal('agent_b', 'agent')
        B = g.h.session('agent_b')
        g.create_program('QC', 120)
        g.tap('tab_spectra')
        g.tap('sp_start')
        g.ov('YES')
        op = g.h.operation_id
        self.assertEqual((g.state('acquisition.phase'), g.h._owners), ('running', {op: 'agent'}))
        self.assertEqual(g.A.poll(op)['status'], 'running')
        with self.assertRaises(rt.ActionRejected):
            B.poll(op)
        g.step(5.0)
        before = runtime_state(g.h)
        x, y, w, h = g.widgets()['sp_stop']['rect']
        r = g.call(B, 'panel.touch', display_id=SCR, x=x + w // 2, y=y + h // 2)        # B taps Stop: refused with a message
        self.assertFalse(r['accepted'])
        self.assertEqual(g.dialog(), ('Message', ['Not permitted.']))
        g.ov('OK')
        for cap, args in (('acquisition.stop', {}), ('defaults.configure', {'t1_c': 50.0}),
                          ('settings.configure', {'settings': {'epc.gc_gas': 'air'}}), ('settings.reset_default', {})):
            self.assertEqual(g.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(runtime_state(g.h), before)
        g.call(B, 'settings.configure', settings={'display.brightness': 4})              # a display setting does not change the run
        self.assertEqual(g.A.cancel(op, 'c1')['status'], 'succeeded')                   # the owner cancels its own panel-started program
        self.assertEqual(g.state('acquisition.phase'), 'cancelled')
        g.tap('sp_start')                                                                # a new program of A ...
        g.ov('YES')
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])   # ... and B's rear power switch: physical
        self.assertEqual((g.state('power.enabled'), g.state('acquisition.phase')), (False, 'cancelled'))

    def test_owner_stops_on_the_screen(self):
        g = Rig()
        g.create_program('QC', 120)
        g.tap('tab_spectra')
        g.tap('sp_start')
        g.ov('YES')
        g.step(3.0)
        self.assertTrue(g.tap('sp_stop'))
        self.assertEqual(g.state('acquisition.phase'), 'cancelled')


class DeviceFaults(unittest.TestCase):
    """Sweep 10-19 (1.3.0): the device errors of manual p.39-40 and p.244-247 by evaluator fault.inject (High pressure, Drift voltage supply.,
    Can't initalize drift supply., Hardware access failure)."""

    def start(self, g):
        g.create_program('QC', 120)
        g.tap('tab_spectra')
        g.tap('sp_start')
        g.ov('YES')

    def test_high_pressure_switches_temperatures_and_flows_off(self):
        g = Rig()
        self.start(g)
        g.step(5.0)
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', fault='high_pressure', active=True)['error']['code'], 'ACCESS_DENIED')
        g.call(g.E, 'fault.inject', fault='high_pressure', active=True)
        self.assertEqual((g.state('acquisition.phase'), g.state('safety.high_pressure')), ('failed', True))
        self.assertIn('high_pressure', g.widgets())                                     # the alarm box over the window
        g.step(1.0)
        p = g.state('device.parameters')
        self.assertEqual((p['epc_ims_ml_min'], p['epc_gc_ml_min']), (0.0, 0.0))        # flow controllers off
        self.assertAlmostEqual(g.h._target_k('ims'), g.h.plant['ambient_k'])            # temperature controllers off
        g.tap('high_pressure_ok')                                                         # the cause is still there: the box stays
        self.assertTrue(g.state('safety.high_pressure'))
        g.call(g.E, 'fault.inject', fault='high_pressure', active=False)
        self.assertTrue(g.tap('high_pressure_ok'))
        self.assertFalse(g.state('safety.high_pressure'))
        self.assertNotIn('high_pressure', g.widgets())
        g.step(1.0)
        self.assertGreater(g.state('device.parameters')['epc_ims_ml_min'], 0.0)
        g.call(g.E, 'fault.inject', fault='high_pressure', active=True)                  # a restart also resets it once the cause is gone
        g.call(g.E, 'fault.inject', fault='high_pressure', active=False)
        assert g.switch('off')
        assert g.switch('on')
        g.step(30.0)
        self.assertFalse(g.state('safety.high_pressure'))

    def test_drift_voltage_supply_is_fatal_until_a_restart(self):
        g = Rig()
        g.call(g.E, 'fault.inject', fault='drift_voltage_supply', active=True)
        self.assertFalse(g.state('device.parameters')['drift_voltage_on'])
        self.assertIn('Drift voltage supply.', [e['text'] for e in g.state('device.errors')])
        g.create_program('QC', 120)
        g.tap('tab_spectra')
        g.tap('sp_start')
        g.ov('YES')
        self.assertEqual(g.dialog(), ('Message', ['Drift voltage supply.']))
        g.ov('OK')
        g.call(g.E, 'fault.inject', fault='drift_voltage_supply', active=False)        # repaired: still fatal until a restart
        self.assertEqual(g.call(g.A, 'program.start', expect='rejected')['error']['code'], 'DRIFT_VOLTAGE_ERROR')
        assert g.switch('off')
        assert g.switch('on')
        g.step(30.0)
        self.assertTrue(g.state('device.parameters')['drift_voltage_on'])

    def test_drift_supply_init_and_hardware_access_failure(self):
        g = Rig()
        g.call(g.E, 'fault.inject', fault='drift_supply_init', active=True)
        g.create_program('QC', 120)
        g.tap('tab_spectra')
        g.tap('sp_start')
        g.ov('YES')
        self.assertEqual(g.dialog(), ('Message', ["Can't initalize drift supply. Program can not be started."]))
        g.ov('OK')
        self.assertEqual(g.state('acquisition.phase'), 'idle')
        g.call(g.E, 'fault.inject', fault='drift_supply_init', active=False)
        g.call(g.E, 'fault.inject', fault='hardware_access_failure', active=True)
        g.tap('tab_defaults')
        self.assertFalse(g.tap('df_dv'))                                                # the polarity switch fails
        self.assertEqual(g.dialog(), ('Message', ['Hardware access failure']))
        g.ov('OK')
        self.assertEqual(g.state('device.defaults')['polarity'], 'positive')
        g.call(g.E, 'fault.inject', fault='hardware_access_failure', active=False)
        g.tap('df_dv')
        self.assertEqual(g.state('device.defaults')['polarity'], 'negative')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        a.step(60.0)
        b.step(60.0)
        ia, ib = a.h.panel.render(SCR), b.h.panel.render(SCR)
        self.assertEqual((ia.dtype.name, ia.shape), ('uint8', (480, 640, 3)))
        self.assertTrue((ia == ib).all())
        self.assertEqual(a.h.panel.png_bytes(SCR), b.h.panel.png_bytes(SCR))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.h.panel.save_png(SCR, OUT / 'home.png')}
        a.system('sys_settings1')
        shots['menu'] = a.h.panel.save_png(SCR, OUT / 'menu.png')
        a.tap('tab_defaults')
        shots['defaults'] = a.h.panel.save_png(SCR, OUT / 'defaults.png')
        a.create_program('QC', 120)
        shots['programs'] = a.h.panel.save_png(SCR, OUT / 'programs.png')
        a.call(a.A, 'gas.set', enabled=False)
        a.tap('tab_spectra')
        a.tap('sp_start')
        a.ov('YES')
        self.assertEqual(a.dialog(), ('Message', ['Drift gas flow too low. Aborting...']))
        shots['error'] = a.h.panel.save_png(SCR, OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=SCR)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        self.assertEqual((r['image']['width'], r['image']['height']), (640, 480))

    def test_spec_and_contract(self):
        meta = rt.export_gas_flavourspec25gc_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.touch', 'panel.turn', 'panel.press', 'panel.switch', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        c = json.loads((ASSET_DIR / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        for k in list(rt.PANEL_CAPS) + list(rt.FW_ERRORS):
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids', 'state_effects', 'preconditions', 'errors'):
                self.assertEqual(c['capabilities'][k][key], meta['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET_DIR / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('gcims_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
