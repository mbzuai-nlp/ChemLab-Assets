"""front-panel tests for CHEM-108 agilent_hs8697 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_analytical.py):
python3 -m pytest tests/test_panel_ui_agilent_hs8697.py -q

The HS has a Park button with its lamp, the status and rack LEDs and the On/Standby switch; its pages are the headspace pages of the host GC
touchscreen (display gc_touch).  Every gesture goes through the public API (panel.press / panel.switch / panel.touch); widgets are found by id
in the structured frame (panel.frame, evaluator).  Themes: the seven key procedures of the menu survey; the headspace method, sequence engine
with sequence actions and the Stop prompt, diagnostics, calibration, service mode, EMF and logs; panel = direct command; refusals and time-outs;
render determinism and evidence PNGs; every terminal command once; contract entries."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
import basic40_runtime_analytical as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

OUT = ROOT / 'outputs/panel_ui/agilent_hs8697'
CONTRACT = rt.HS8697_ASSET_DIR / 'asset.contract.json'
TOUCH = 'gc_touch'
ML = 1e-6 / 60
PSI = 6894.757293168
SURVEY_TASKS = ['Park the tray and load sample vials', 'Set HS temperatures in a method', 'Set vial size and shaking', 'Run the User Vial Leak Test',
                'Stop a running sequence from the GC touchscreen', 'Calibrate the tray', 'Zero the vial pressurization EPC flow sensor']
DONE_TASKS = set()
TMP = tempfile.mkdtemp(prefix='hs8697_panel_')


class Rig:
    def __init__(self, warm=True, vials=5):
        self.t = 0.0
        self.scene = []
        self.h = rt.AgilentHS8697('hs', scene_adapter=lambda i, s: self.scene.append(copy.deepcopy(s)), clock=lambda: self.t,
                                  ambient_temperature_K=293.15)
        self.h.panel_capture_dir = TMP
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('__agent__'), self.h.session('judge')
        self.n = 0
        for i in range(1, 9):
            self.h.register_sample(f'v{i}', volume_m3=20e-6)
        self.h.register_sample('small', volume_m3=10e-6)
        self.h.register_gc('gc_a')
        if warm:
            assert self.switch('on')
            self.step(25)
            self.call(self.A, 'lid.set_angle', angle_rad=rt.AGILENT_HS8697_LID_MAX_RAD)
            for i in range(1, vials + 1):
                self.call(self.A, 'sample.load', sample_id=f'v{i}', slot_id=f'tray_{i:02d}')
            self.call(self.A, 'lid.set_angle', angle_rad=0.0)
            for z, k in (('oven', 333.15), ('loop_valve', 343.15), ('transfer_line', 353.15)):
                self.call(self.A, 'thermal.set_temperature', zone=z, temperature_K=k)
            self.call(self.A, 'method.set_equilibration_time', duration_s=120.0)
            self.call(self.A, 'hs.method.configure', settings={'gc_cycle_s': 300.0})
            self.step(1800)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(state_ids=[sid])['observations'][sid]['value']

    def status(self):
        return self.val('hs.status')

    def step(self, dt):
        t = 0.0
        while t < dt - 1e-9:
            d = min(30.0, dt - t)
            self.t += d
            self.h.host_update()
            t += d

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][TOUCH]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=TOUCH, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        x, y, ww, hh = self.widgets()[wid]['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        for b in self.overlay()['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in self.overlay()["buttons"]]}')

    def row(self, wid, first, col=0):
        w = self.widgets()[wid]
        while w['scroll']:
            x, y, ww, hh = w['rect']
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
            w = self.widgets()[wid]
        for _ in range(40):
            w = self.widgets()[wid]
            firsts = [c[col] for c in w['cells']]
            if first in firsts:
                k = firsts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, firsts)
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def cells(self, wid):
        out = {}
        w = self.widgets()[wid]
        for _ in range(40):
            out.update({c[0]: (c[1] if len(c) > 1 else None) for c in w['cells']})
            if w['scroll'] + len(w['cells']) >= w['count']:
                break
            x, y, ww, hh = w['rect']
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
            w = self.widgets()[wid]
        return out

    def type(self, text):
        if self.overlay()['field']:
            self.ov('Del')
        for ch in text:
            assert self.ov(ch), ch

    def enter(self, wid, first, text):
        assert self.row(wid, first)
        self.type(text)
        return self.ov('OK')

    def choose(self, wid, first, option):
        assert self.row(wid, first)
        return self.ov(option)

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def park(self):
        return self.call(self.A, 'panel.press', input_id='park_button')['accepted']

    def tab(self, name):
        return self.tap(f'tab_{name}')

    def png(self):
        return Path(self.call(self.A, 'panel.capture', display_id=TOUCH)['image']['path']).read_bytes()


class SurveyProcedures(unittest.TestCase):
    def test_park_and_load(self):
        r = Rig(vials=0)
        self.assertTrue(r.park())
        self.assertTrue(r.val('hs.tray')['parked'])
        self.assertTrue(r.frame()['displays']['park_lamp']['shown'])
        self.assertIn('Tray parked', r.status()['reasons'])
        self.assertEqual(r.frame()['displays']['status_led']['color'], 'yellow')
        r.call(r.A, 'lid.set_angle', angle_rad=rt.AGILENT_HS8697_LID_MAX_RAD)
        r.call(r.A, 'sample.load', sample_id='v1', slot_id='tray_01')
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        self.assertTrue(r.park())
        self.assertFalse(r.frame()['displays']['park_lamp']['shown'])
        self.assertTrue(r.status()['ready'])
        self.assertEqual(r.frame()['displays']['status_led']['color'], 'green')
        self.assertTrue(r.frame()['displays']['rack_led']['shown'])
        r.tap('side_hs_vials')
        self.assertEqual(r.widgets()['vl_list']['cells'][0], ['Tray 01', 'v1', 'loaded'])
        DONE_TASKS.add('Park the tray and load sample vials')

    def test_set_temperatures(self):
        r = Rig()
        r.tab('method')
        r.enter('hm_form', 'Oven temperature', '70')
        r.enter('hm_form', 'Loop temperature', '80')
        r.enter('hm_form', 'Transfer Line temperature', '95')
        self.assertAlmostEqual(r.val('thermal.oven.setpoint_k'), 343.15)
        self.assertAlmostEqual(r.val('thermal.loop_valve.setpoint_k'), 353.15)
        self.assertAlmostEqual(r.val('thermal.transfer_line.setpoint_k'), 368.15)
        self.assertIn('Headspace Oven temperature not ready', r.status()['reasons'])
        r.step(2400)
        self.assertTrue(r.status()['ready'])
        r.tab('home')
        self.assertEqual(r.widgets()['st_list']['cells'][0][:2], ['Headspace Oven Temperature', '70.00 °C'])
        DONE_TASKS.add('Set HS temperatures in a method')

    def test_vial_size_and_shaking(self):
        r = Rig()
        r.tab('method')
        r.choose('hm_form', 'Vial Size', '10 mL')
        r.enter('hm_form', 'Shake Value', '5')
        m = r.val('hs.method')
        self.assertEqual((m['vial_size_m3'], m['shake']), (1e-5, 5))
        r.enter('hm_form', 'Shake Value', '12')
        self.assertEqual(r.overlay()['error'], 'Out of range')
        DONE_TASKS.add('Set vial size and shaking')

    def test_user_vial_leak_test(self):
        r = Rig()
        r.h.set_vial_leak('v3', 0.2 * ML)
        r.tab('diag')
        r.tap('dg_tests')
        r.tap('test_user_vial_leak')
        r.tap('tp_start')
        self.assertEqual(r.status()['state'], 'DIAGNOSTIC MODE')
        r.step(300)
        res = r.val('hs.diagnostics')['results']['user_vial_leak']
        self.assertEqual(res['result'], 'Passed')
        self.assertIn('Result: Passed', r.text('tp_state'))
        values = dict(map(tuple, r.widgets()['tp_values']['cells']))
        self.assertEqual(values['Vial 3'], '0.200 mL/min')
        self.assertIn('Suggested leak rate threshold', values)
        DONE_TASKS.add('Run the User Vial Leak Test')

    def test_stop_sequence(self):
        r = Rig()
        r.call(r.A, 'sequence.start', vials=[1, 2, 3])
        r.step(60)
        self.assertEqual(r.val('hs.sequence')['jobs'][0]['state'], 'equilibrating')
        self.assertEqual(r.text('tray_status'), 'HEADSPACE STATUS: SEQUENCE RUNNING')
        self.assertTrue(r.tap('stop'))
        self.assertEqual(r.overlay()['lines'], ['Stop the run, stop the sequence, or cancel?'])
        r.ov('Cancel')
        self.assertIsNotNone(r.val('hs.sequence'))
        r.tap('stop')
        r.ov('Stop the sequence')
        self.assertIsNone(r.val('hs.sequence'))
        self.assertEqual(r.val('oven.occupancy')[0], None)                             # vials back to the tray
        self.assertEqual(r.val('tray.occupancy')[0], 'v1')
        self.assertFalse(r.tap('stop'))                                                 # nothing running: Stop does nothing
        DONE_TASKS.add('Stop a running sequence from the GC touchscreen')

    def test_tray_calibration(self):
        r = Rig(vials=0)
        r.tab('settings')
        r.tap('set_cal_hs')
        r.row('cl_form', 'Start System and Tray Calibration')
        self.assertEqual(r.overlay()['dialog'], 'tray_cal')
        r.ov('Factory')
        self.assertEqual(r.status()['state'], 'CALIBRATION')
        r.step(310)
        self.assertIsNotNone(r.val('hs.calibration')['last_tray'])
        self.assertEqual(r.val('hs.emf')['tray_calibration']['resets'][-1]['how'], 'Maintenance Reset')
        r2 = Rig()
        r2.tab('settings')
        r2.tap('set_cal_hs')
        r2.row('cl_form', 'Start System and Tray Calibration')                         # vials in the tray: refused
        self.assertEqual(r2.overlay()['dialog'], 'err_vials_present')
        DONE_TASKS.add('Calibrate the tray')

    def test_zero_flow_sensor(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', errors={'vial_flow_offset_m3_s': 0.4 * ML, 'vial_pressure_offset_Pa': 100.0})
        self.assertEqual(r.val('hs.status')['state'], 'READY')
        r.tab('settings')
        r.tap('set_cal_hs')
        r.row('cl_form', 'Vial flow sensor')
        r.tab('home')
        self.assertEqual(r.widgets()['st_list']['cells'][3][2], '20.000 mL/min')       # zero applied to the reading
        r.tab('settings')
        r.tap('set_cal_hs')
        r.row('cl_form', 'Vial pressure sensor')
        self.assertEqual(r.overlay()['dialog'], 'err_supply')
        r.ov('OK')
        r.h.set_vial_gas_supply(False)
        r.row('cl_form', 'Vial pressure sensor')
        self.assertIn('vial_pressure', r.val('hs.calibration')['zero_times'])
        r.row('cl_form', 'Vial pressure sensor: Reset')
        self.assertNotIn('vial_pressure', r.val('hs.calibration')['zero_times'])
        DONE_TASKS.add('Zero the vial pressurization EPC flow sensor')


class SequenceEngine(unittest.TestCase):
    def test_full_sequence_injections_and_vial_status(self):
        r = Rig()
        r.call(r.A, 'sequence.start', vials=[1, 2])
        r.step(30)
        r.tab('home')
        r.tap('side_hs_vials')
        self.assertIn(['Oven 01', 'v1', 'equilibrating'], r.widgets()['vl_list']['cells'])
        r.step(900)
        self.assertIsNone(r.val('hs.sequence'))
        self.assertEqual(r.val('measurement.count'), 2)
        self.assertEqual([e['sample_id'] for e in r.val('injection.log')], ['v1', 'v2'])
        self.assertEqual(r.val('hs.emf')['run_count']['value'], 2)
        self.assertIn('Sequence completed', [e['text'] for e in r.val('hs.logs')['event']])

    def test_sequence_actions_missing_wrong_size_leak_and_park(self):
        r = Rig(vials=2)
        r.call(r.A, 'lid.set_angle', angle_rad=rt.AGILENT_HS8697_LID_MAX_RAD)
        r.call(r.A, 'sample.load', sample_id='small', slot_id='tray_04')
        r.call(r.A, 'lid.set_angle', angle_rad=0.0)
        r.h.set_vial_leak('v2', 2 * ML)
        r.call(r.A, 'sequence.start', vials=[1, 2, 3, 4])
        self.assertTrue(r.park())                                                       # parked: no vial moves (p.35)
        r.step(120)
        self.assertEqual(r.val('hs.sequence')['jobs'][0]['state'], 'waiting')
        r.park()
        r.step(2000)
        logs = [e['text'] for e in r.val('hs.logs')['event']]
        self.assertIn('Leak detected at tray_02: Skip', logs)
        self.assertIn('Vial missing at tray_03: Skip', logs)
        self.assertIn('Wrong vial size at tray_04: Skip', logs)
        self.assertEqual([e['sample_id'] for e in r.val('injection.log')], ['v1'])
        self.assertIn('Leak detected: vial tray_02', [a['text'] for a in r.val('hs.alerts')])

    def test_not_ready_wait_and_pause_resume(self):
        r = Rig()
        r.h.set_gc_ready(False)
        r.call(r.A, 'hs.method.configure', settings={'act_leak': 'Pause'})
        r.call(r.A, 'sequence.start', vials=[1])
        r.step(400)
        self.assertEqual(r.val('hs.sequence')['jobs'][0]['state'], 'equilibrating')    # Wait for Ready
        r.h.set_gc_ready(True)
        r.step(200)
        self.assertEqual(r.val('measurement.count'), 1)
        r.h.set_vial_leak('v2', 2 * ML)
        r.step(600)
        r.call(r.A, 'sequence.start', vials=[2])
        r.step(400)
        self.assertTrue(r.val('hs.sequence')['paused'])
        self.assertEqual(r.status()['state'], 'SEQUENCE PAUSED')
        r.h.set_vial_leak('v2', 0.0)
        r.call(r.A, 'sequence.pause', paused=False)
        r.step(400)
        self.assertEqual(r.val('measurement.count'), 2)

    def test_park_refuses_sequence_start(self):
        r = Rig()
        r.park()
        self.assertEqual(r.call(r.A, 'sequence.start', expect='rejected', vials=[1])['error']['code'], 'PARKED')


class PagesAndSettings(unittest.TestCase):
    def test_method_fill_and_extraction_visibility(self):
        r = Rig()
        r.tab('method')
        c = r.cells('hm_form')
        self.assertIn('Vial Fill Flow', c)
        self.assertNotIn('Fill Volume', c)
        r.choose('hm_form', 'Vial Fill Mode', 'Constant Volume')
        c = r.cells('hm_form')
        self.assertIn('Fill Volume', c)
        self.assertNotIn('Vial Fill Flow', c)
        r.choose('hm_form', 'Extraction mode', 'Concentrated')
        r.enter('hm_form', 'Number of extractions', '3')
        self.assertEqual(r.val('hs.method')['extractions'], 3)
        r.enter('hm_form', 'Vial Equilibration', '3.5')
        self.assertEqual(r.val('method.equilibration_time_s'), 210.0)

    def test_configuration_apply_and_barcode(self):
        r = Rig()
        r.tab('settings')
        r.tap('set_cfg_hs')
        r.choose('cf_form', 'Loop Volume', '3 mL')
        r.choose('cf_form', 'Gas Type', 'N2')
        r.tap('cf_apply')
        s = r.val('hs.settings')
        self.assertEqual((s['loop_volume_m3'], s['gas_type']), (3e-6, 'N2'))
        r.row('cf_form', 'Enable barcode checksum / Symbology')
        self.assertEqual(r.overlay()['lines'], ['No barcode reader is present.'])

    def test_service_factory_reset_emf_and_logs(self):
        r = Rig()
        r.tab('maint')
        r.row('mh_list', 'Probe')
        b = [b for b in r.overlay()['buttons'] if b['id'] == 'row:warn_on'][0]
        r.touch(b['rect'][0] + 5, b['rect'][1] + 5)
        r.ov('Enabled')
        r.ov('Apply')
        self.assertTrue(r.val('hs.emf')['probe']['warn_on'])
        r.row('mh_list', 'Probe')
        r.ov('Reset Counter')
        r.ov('Yes')
        self.assertEqual(r.val('hs.emf')['probe']['resets'][-1]['how'], 'User Reset')
        r.tab('logs')
        r.tap('lg_maintenance')
        self.assertIn('counter reset', r.widgets()['lgmaintenance_list']['cells'][0][1])
        r.tab('settings')
        r.tap('set_svc_hs')
        self.assertEqual(r.cells('sv_list')['Serial number'], 'SIM-8697-0001')
        r.tap('sv_reset')
        r.ov('Factory Reset')
        self.assertFalse(r.val('hs.emf')['probe']['warn_on'])
        self.assertEqual(r.page()[0], 'boot')
        r.step(30)
        self.assertEqual(r.page(), ('gui', 'hs_status'))

    def test_standby_switch_and_help(self):
        r = Rig()
        self.assertTrue(r.switch('standby'))
        self.assertFalse(r.val('power.enabled'))
        self.assertEqual(r.page()[0], 'off')
        self.assertFalse(r.park())                                                      # standby: only the switch reacts
        self.assertTrue(r.switch('on'))
        r.step(25)
        r.tab('help')
        self.assertEqual(r.overlay()['dialog'], 'help')

    def test_restriction_test_and_alert_clear(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', errors={'system_leak_m3_s': 0.5 * ML})
        r.tab('diag')
        r.tap('dg_tests')
        r.tap('test_restriction_decay')
        r.tap('tp_start')
        r.tap('tp_cancel')
        r.ov('No')
        r.step(130)
        self.assertEqual(r.val('hs.diagnostics')['results']['restriction_decay']['result'], 'Failed')


def template_rig(**method):
    """A warm HS with one aqueous template vial (toluene 0.01 mol/m3, 10 mL in a 20 mL vial) in oven position 1 and a 1 mL installed loop."""
    r = Rig(vials=0)
    r.h.register_template_sample('t', volume_m3=20e-6, liquid_volume_m3=10e-6, composition_mol_m3={'toluene': 0.01})
    r.h.configure_template_transfer(loop_volume_m3=1e-6, evidence='assumption')
    r.call(r.A, 'lid.set_angle', angle_rad=rt.AGILENT_HS8697_LID_MAX_RAD)
    r.call(r.A, 'sample.load', sample_id='t', slot_id='tray_01')
    r.call(r.A, 'lid.set_angle', angle_rad=0.0)
    if method:
        r.call(r.A, 'hs.method.configure', settings=method)
    return r


def direct_area(r):
    """One direct injection of the template vial (CHEM-108 measure): the FID peak area and the extraction the device made."""
    r.call(r.A, 'lid.set_angle', angle_rad=rt.AGILENT_HS8697_LID_MAX_RAD)
    if r.val('oven.occupancy')[0] is None:
        r.call(r.A, 'sample.transfer', source_slot='tray_01', target_slot='oven_01')
    r.call(r.A, 'lid.set_angle', angle_rad=0.0)
    res = r.call(r.A, 'measure', oven_slot='oven_01')
    return sum(p['area_C'] for p in res['chromatogram']['peaks']), r.val('hs.transfer')


class SyncAudit(unittest.TestCase):
    """Sync audit 2026-10-19: the injected amount follows the headspace method and the EPC errors act on the vial; Prep Run / Start reach the
    connected GC; panel commands run as the gesturing session."""

    def test_default_and_custom_loop_fill_set_the_amount(self):
        a_def, x_def = direct_area(template_rig())
        self.assertAlmostEqual(x_def['fill_Pa'], 15 * PSI, places=6)                  # Flow to Pressure to 15 psi
        self.assertAlmostEqual((x_def['final_loop_Pa'] + 101325.0) / (x_def['fill_Pa'] + 101325.0), 5 / 6)   # Default, 20 mL vial (p.91)
        self.assertEqual(x_def['sweep'], 1.0)
        a_cus, x_cus = direct_area(template_rig(loop_fill_mode='Custom', final_loop_Pa=5 * PSI))
        self.assertAlmostEqual(x_cus['final_loop_Pa'], 5 * PSI, places=6)
        self.assertAlmostEqual(a_cus / a_def, x_cus['factor'] / x_def['factor'], delta=0.02 * a_cus / a_def)
        self.assertLess(a_cus, 0.85 * a_def)                                          # a lower final loop pressure holds less sample
        a_cv, x_cv = direct_area(template_rig(fill_mode='Constant Volume', fill_volume_m3=2e-6))
        self.assertLess(x_cv['fill_Pa'], x_def['fill_Pa'])                            # 2 mL of gas: less than 15 psi
        self.assertGreater(x_cv['fill_Pa'], x_cv['natural_Pa'])

    def test_configured_loop_volume_and_sweep(self):
        a1, x1 = direct_area(template_rig())
        r = template_rig()
        r.call(r.A, 'hs.settings.configure', settings={'loop_volume_m3': 3e-6})        # configured 3 mL, installed 1 mL
        a3, x3 = direct_area(r)
        self.assertEqual(x3['loop_volume_configured_m3'], 3e-6)
        self.assertLess(x3['final_loop_Pa'], x1['final_loop_Pa'])                    # the HS lowers the final pressure to sweep 3 mL
        self.assertLess(a3, a1)
        _, xc = direct_area(template_rig(loop_fill_mode='Custom', final_loop_Pa=14.9 * PSI))
        self.assertLess(xc['sweep'], 1.0)                                             # hardly any pressure drop: the loop is not swept

    def test_epc_sensor_errors_act_on_the_vial(self):
        a0, x0 = direct_area(template_rig(loop_fill_mode='Custom', final_loop_Pa=5 * PSI))
        r = template_rig(loop_fill_mode='Custom', final_loop_Pa=5 * PSI)
        r.call(r.E, 'sensor.configure_error', errors={'vial_pressure_offset_Pa': 1 * PSI, 'aux_pressure_offset_Pa': 0.5 * PSI})
        a1, x1 = direct_area(r)
        self.assertAlmostEqual(x1['fill_Pa'], 14 * PSI, places=6)                    # the reading is held at 15 psi: the vial gets 14
        self.assertAlmostEqual(x1['final_loop_Pa'], 4.5 * PSI, places=6)
        self.assertNotAlmostEqual(a1 / a0, 1.0, places=3)
        r.h.set_vial_gas_supply(False)
        r.call(r.A, 'calibration.zero', sensor='vial_pressure', action='zero')
        r.call(r.A, 'calibration.zero', sensor='aux_pressure', action='zero')
        r.h.set_vial_gas_supply(True)
        r.call(r.E, 'sensor.configure_error', errors={'flow_restriction': 0.5})
        x2 = rt._hs_extraction(r.h, r.h._device, r.h._state, 't')
        self.assertAlmostEqual(x2['fill_Pa'], 15 * PSI, places=6)
        self.assertAlmostEqual(x2['fill_s'], 2 * rt._hs_extraction(template_rig().h, template_rig().h._device, r.h._state, 't')['fill_s'], places=6)

    def test_shaking_speeds_up_the_equilibration(self):
        areas = {}
        for shake in (0, 9):
            r = template_rig(shake=shake)
            r.call(r.A, 'method.set_equilibration_time', duration_s=60.0)
            areas[shake], x = direct_area(r)
        self.assertGreater(areas[9], 1.5 * areas[0])
        self.assertGreater(x['approach'], 0.7)

    def test_concentrated_and_multiple_extractions(self):
        def run(**method):
            r = template_rig(**method)
            r.call(r.A, 'sequence.start', vials=[1], **({'injections_per_vial': 2} if method.get('extraction_mode') == 'Multiple' else {}))
            areas, n = [], 0
            for _ in range(200):
                r.step(30)
                if r.val('measurement.count') > n:
                    n = r.val('measurement.count')
                    areas.append(sum(p['area_C'] for p in r.val('measurement.chromatogram')['peaks']))
                if r.val('hs.sequence') is None:
                    break
            return areas
        single = run()
        conc = run(extraction_mode='Concentrated', extractions=3)
        multi = run(extraction_mode='Multiple', vent_between=True)
        self.assertEqual((len(single), len(conc), len(multi)), (1, 1, 2))
        self.assertTrue(2.5 * single[0] < conc[0] < 3.0 * single[0])                  # three extractions in the trap, slightly depleted
        self.assertLess(multi[1], multi[0])                                           # the vial is depleted between extractions

    def test_prep_run_and_start_reach_the_gc(self):
        r = Rig()
        r.tab('home')
        self.assertTrue(r.widgets()['prep_run']['enabled'])
        r.tap('prep_run')
        self.assertEqual(r.overlay()['dialog'], 'err_gc')                             # no GC control registered: no answer
        r.ov('OK')
        calls = []
        gc = rt.AgilentGC8890B('gc_a', scene_adapter=lambda i, s: None)
        gc.set_gas_supply(carrier=True, hydrogen=True, air=True, makeup=True)
        gc.invoke(capability_id='power.set', arguments={'enabled': True}, request_id='on')
        gc.host_step(60)

        def control(action, principal):
            calls.append((action, principal))
            return gc.invoke(capability_id={'prep': 'run.prep', 'start': 'run.start'}[action], arguments={}, request_id=f'hs{len(calls)}')
        r.h.register_gc_control(control)
        r.tap('prep_run')
        self.assertEqual(calls, [('prep', '__agent__')])
        self.assertTrue(gc.read(state_ids=['gc.status'])['observations']['gc.status']['value']['prep'])
        r.tap('start')
        self.assertEqual(calls[-1], ('start', '__agent__'))
        if gc.read(state_ids=['gc.run'])['observations']['gc.run']['value'] is None:   # the GC refused (not ready): its answer is shown
            self.assertEqual(r.overlay()['dialog'], 'err_gc_refused')

    def test_panel_commands_run_as_the_gesturing_session(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        b = r.h.session('agent_b')
        seen = []
        r.h.register_gc_control(lambda action, principal: seen.append(principal) or {'status': 'succeeded'})
        r.tab('home')
        x, y, w, h = r.widgets()['prep_run']['rect']
        b.invoke(capability_id='panel.touch', arguments=dict(display_id=TOUCH, x=x + w // 2, y=y + h // 2), request_id='b1')
        self.assertEqual(seen, ['agent_b'])
        self.assertIsNone(r.h._caller)


class Callers(unittest.TestCase):
    """Consistency sweep 2026-10-19: a sequence belongs to the caller that started it; another caller can neither stop, pause nor change it,
    on the touchscreen or the direct path; the refusal shows as a dialog; the On/Standby switch is physical; the evaluator is unrestricted."""
    def test_sequence_belongs_to_its_caller(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        r.call(r.A, 'sequence.start', vials=[1, 2, 3])
        self.assertEqual(r.h._device['sequence']['owner'], '__agent__')
        r.step(60)
        seq = r.val('hs.sequence')
        for cap, args in (('sequence.stop', dict(scope='sequence')), ('sequence.pause', dict(paused=True)),
                          ('hs.method.configure', dict(settings={'gc_cycle_s': 600.0})), ('thermal.set_temperature', dict(zone='oven', temperature_K=343.15))):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.val('hs.sequence'), seq)
        x, y, w, h = r.widgets()['stop']['rect']                                       # B: Stop, Stop the sequence on the touchscreen
        r.call(B, 'panel.touch', display_id=TOUCH, x=x + w // 2, y=y + h // 2)
        b = next(b for b in r.overlay()['buttons'] if b['label'] == 'Stop the sequence')
        self.assertFalse(r.call(B, 'panel.touch', display_id=TOUCH, x=b['rect'][0] + 5, y=b['rect'][1] + 5)['accepted'])
        self.assertEqual(r.val('hs.sequence'), seq)
        self.assertEqual((r.overlay()['title'], r.overlay()['lines']), ('Not allowed', ["Another user's sequence, test or", 'calibration is in progress.']))
        r.ov('OK')
        r.call(r.E, 'sequence.pause', paused=True)                                     # the evaluator is unrestricted
        self.assertTrue(r.val('hs.sequence')['paused'])
        self.assertTrue(r.call(B, 'panel.switch', input_id='power_switch', position='standby')['accepted'])   # On/Standby rocker: physical
        self.assertFalse(r.val('power.enabled'))


class EquivalenceRefusals(unittest.TestCase):
    def test_panel_equals_direct(self):
        a, b = Rig(), Rig()
        a.tab('method')
        a.enter('hm_form', 'GC Cycle', '12.5')
        a.choose('hm_form', 'Purge Flow Mode', 'Custom')
        b.call(b.A, 'hs.method.configure', settings={'gc_cycle_s': 750.0})
        b.call(b.A, 'hs.method.configure', settings={'purge_mode': 'Custom'})
        self.assertEqual(a.val('hs.method'), b.val('hs.method'))

    def test_every_terminal_command_once(self):
        r = Rig()
        meta = rt.export_agilent_hs8697_contract_metadata()
        done = set()
        order = ['sensor.configure_error', 'hs.method.configure', 'hs.settings.configure', 'clock.set', 'emf.configure', 'emf.reset',
                 'diagnostics.start_test', 'diagnostics.cancel_test', 'calibration.zero', 'tray.park', 'sequence.start', 'sequence.pause',
                 'sequence.stop', 'gc.run_control']
        r.h.register_gc_control(lambda action, principal: {'status': 'succeeded'})
        for cap in order:
            m = meta['capabilities'][cap]
            args = dict(m['example_arguments'][0])
            if cap == 'calibration.zero':
                args = dict(sensor='vial_flow', action='zero')
            if cap == 'tray.park':
                args = dict(parked=False)
            if cap == 'sequence.pause':
                args = dict(paused=True)
            sess = r.E if m['visibility'] != 'agent' else r.A
            before = r.E.read()['observations']
            res = r.call(sess, cap, **args)
            self.assertEqual(validate_value(res, m['output_schema']), [], cap)
            after = r.E.read()['observations']
            changed = {k for k in after if after[k]['value'] != before[k]['value']} - {'hs.status', 'hs.zones', 'hs.device_datetime'}
            self.assertTrue(changed <= set(m['state_ids']) | {'hs.logs'}, (cap, changed - set(m['state_ids'])))
            done.add(cap)
        r2 = Rig(vials=0)
        r2.call(r2.A, 'calibration.tray', action='start')
        r2.call(r2.A, 'calibration.tray_step', action='factory')
        r2.step(301)
        r2.h.set_vial_leak('v1', 0.0)
        r.step(1)
        r.call(r.A, 'service.factory_reset')
        r.h._device['alerts'].append(dict(id=99, level='warning', text='x', causes='', time=''))
        r.call(r.A, 'diagnostics.resolve', alert_id=99)
        done |= {'calibration.tray', 'calibration.tray_step', 'service.factory_reset', 'diagnostics.resolve'}
        self.assertEqual(done, set(rt._HS_TERMINAL))
        self.assertEqual(r.call(r.A, 'sensor.configure_error', expect='rejected', errors={'flow_restriction': 0.0})['error']['code'], 'ACCESS_DENIED')

    def test_refusals_leave_state_unchanged(self):
        r = Rig()
        before = r.h.debug_snapshot()
        for cap, args in (('hs.method.configure', dict(settings={'shake': 10})), ('sequence.pause', dict(paused=True)),
                          ('sequence.stop', dict(scope='run')), ('diagnostics.cancel_test', {}), ('calibration.tray', dict(action='start')),
                          ('calibration.zero', dict(sensor='vial_pressure', action='zero')), ('emf.configure', dict(counter='probe', warn=1e6)),
                          ('diagnostics.resolve', dict(alert_id=5))):
            r.call(r.A, cap, expect='rejected', **args)
        self.assertEqual(before, r.h.debug_snapshot())


class RenderEvidence(unittest.TestCase):
    def test_render_determinism_and_evidence(self):
        r = Rig()
        a, b = r.png(), r.png()
        self.assertEqual(a, b)
        self.assertEqual(hashlib.sha256(Rig().png()).hexdigest(), hashlib.sha256(a).hexdigest())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {}
        r.h.panel.save_png(TOUCH, OUT / 'home.png')
        shots['home.png'] = 'Home > Headspace status entries'
        r.tab('method')
        r.h.panel.save_png(TOUCH, OUT / 'menu.png')
        shots['menu.png'] = 'Method > Headspace (live method editor)'
        r.tab('home')
        r.call(r.A, 'sequence.start', vials=[1])
        r.step(60)
        r.tap('stop')
        r.h.panel.save_png(TOUCH, OUT / 'error.png')
        shots['error.png'] = 'Stop prompt during a sequence'
        r.ov('Cancel')
        r.park()
        for led in ('status_led', 'park_lamp', 'rack_led'):
            r.h.panel.save_png(led, OUT / f'{led}.png')
            shots[f'{led}.png'] = led.replace('_', ' ')
        (OUT / 'renders.json').write_text(json.dumps({k: dict(caption=v, sha256=hashlib.sha256((OUT / k).read_bytes()).hexdigest())
                                                      for k, v in shots.items()}, indent=1) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_has_the_panel(self):
        c = json.loads(CONTRACT.read_text())
        meta = rt.export_agilent_hs8697_contract_metadata()
        self.assertIn('chemistry_lab.panel_ui', c['extensions'])
        for cap in list(rt._HS_TERMINAL) + sorted(rt.HS8697_PANEL_CAPS):
            self.assertEqual(c['capabilities'][cap]['input_schema'], meta['capabilities'][cap]['input_schema'], cap)
        for comp in ('park_button', 'power_switch', 'status_led', 'rack_led', 'gc_touchscreen'):
            self.assertIn(comp, c['components'])
        self.assertEqual(c['asset_version'], rt.HS8697_ASSET_VERSION)


if __name__ == '__main__':
    unittest.main()
