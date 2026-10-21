"""front-panel tests for CHEM-098 thermo_dionexintegrion (panel_ui.json + scripts/asset_runtime/_panel_ui.py on the runtime's mobile-app model):
python3 -m pytest tests/test_panel_ui_thermo_dionexintegrion.py -q

Every gesture goes through the session API (panel.touch / panel.switch / panel.turn, the runtime's normal invoke path); widgets are found by id
in the structured frame (panel.frame, evaluator) and tapped at their centre; on/off states are also read back from the rendered pixels.  Themes:
the six key procedures of the menu survey; every app page (pump, accessory, valves, detector, electrolytics, temperature, plot, analog,
TTL/relay, TTL input, consumables, calibration, information, messages); panel = direct capability (same runtime state); refusals shown as the
app's message; the sequence lock and its interrupt warning; time-outs (start-up 10 s, screen saver 5 min); the CD read-out chain and its
calibration (an injected sensor error removed, an ideal sensor unchanged); render determinism and the evidence PNGs; contract entries."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import thermo_dionexintegrion as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'integrion_01'
OUT = ROOT / 'outputs/panel_ui/thermo_dionexintegrion'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
TAB = 'tablet'
SURVEY_TASKS = ['turn on the system', 'prime the pump from the app', 'set eluent concentration and start EGC', 'autozero the conductivity detector',
                'assign a TTL input function', 'view an error']
DONE_TASKS = set()
GREEN = pu.ROLE_COLORS['ok']


class Rig:
    """A runtime with an agent and an evaluator session; panel gestures only through panel.* capabilities."""

    def __init__(self, power=True, method=True, sample=True, plant=None):
        self.h = rt.ThermoDionexintegrionRuntime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.call(self.E, 'plant.configure', **(plant or rt.PLANT_EXAMPLE))
        if method:
            self.call(self.A, 'method.configure', **rt.METHOD_EXAMPLE)
        if sample:
            self.call(self.E, 'sample.load', **rt.SAMPLE_EXAMPLE)
        if power:
            self.switch('on')
            self.step(11)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap                                          # commands and gestures take no simulated time
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt, n=None):
        for _ in range(n or 1):
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
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2)

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power', position=pos)['accepted']

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        """Tap a button of the top overlay (keyboard key, dialog button) by its label."""
        ov = self.overlay()
        assert ov is not None, 'no overlay'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def enter(self, wid, text):
        """Tap a value, type it on the numeric keypad and confirm with OK."""
        self.tap(wid)
        assert self.overlay()['kind'] == 'keyboard', self.overlay()
        for ch in text:
            self.ov(ch)
        return self.ov('OK')

    def choose(self, wid, text):
        """Tap a drop-down and pick the option with this text from the list."""
        self.tap(wid)
        ov = self.overlay()
        assert ov['kind'] == 'choice', ov
        kx, ky, kw, kh = ov['rect']
        rows = ov['keys']
        r = [k[0] for k in rows].index(text)
        top = ky + 30
        return self.touch(kx + kw // 2, top + (r + 0.5) * (ky + kh - top) / len(rows))

    def dialog_lines(self):
        ov = self.overlay()
        return [] if ov is None else ov.get('lines', [])

    def is_on(self, wid):
        """Read an OFF/ON button from the pixels: green fill at the left edge of the button."""
        img = self.h.panel.render(TAB)
        x, y, w, h = self.widgets()[wid]['rect']
        return tuple(int(v) for v in img[y + h // 2, x + 4]) == GREEN

    def go_home(self):
        self.tap('t_home')


def done(task):
    DONE_TASKS.add(task)


class KeyProcedures(unittest.TestCase):
    def test_turn_on_the_system(self):
        r = Rig(power=False)
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertFalse(r.touch(600, 400))                                   # dark screen: touches do nothing
        self.assertEqual(int(r.h.panel.render(TAB).max()), 0)
        self.assertTrue(r.switch('on'))
        self.assertTrue(r.val('power.enabled'))
        self.assertEqual(r.page(), ('startup', 'startup'))                    # start-up screen during initialization
        self.assertIn('Initializing...', r.text('su_msg'))
        self.assertFalse(r.touch(600, 400))
        r.step(9.9)
        self.assertEqual(r.frame()['screen'], 'startup')
        r.step(0.2)
        self.assertEqual(r.page(), ('app', 'home'))                           # then the Home page
        f = r.frame()
        self.assertTrue(f['displays']['led_power']['on'] and f['displays']['led_connected']['on'])
        self.assertFalse(f['displays']['led_running']['on'] or f['displays']['led_alert']['on'])
        done('turn on the system')

    def test_prime_the_pump_from_the_app(self):
        r = Rig()
        self.assertLess(r.val('pump.indicated_pa'), 0.7e6)
        self.assertTrue(r.call(r.A, 'panel.turn', input_id='priming_valve', detents=-1)['accepted'])   # half a turn counterclockwise
        self.assertTrue(r.val('priming_valve.open'))
        r.tap('s_pump')
        self.assertEqual(r.page(), ('app', 'pump'))
        r.choose('p_prime_rate', '3.00 mL/min')                               # priming flow rate
        self.assertAlmostEqual(r.val('app.settings')['prime_flow_m3_s'], 3 * rt.ML_MIN)
        r.tap('p_prime')                                                      # PRIME ON
        self.assertTrue(r.val('pump.priming'))
        self.assertTrue(r.is_on('p_prime'))
        res0, waste0 = r.h.reservoir, r.h.waste
        r.step(10)
        self.assertAlmostEqual(res0 - r.h.reservoir, 3 * rt.ML_MIN * 10, 15)   # eluent drawn at the priming flow ...
        self.assertAlmostEqual(r.h.waste - waste0, 3 * rt.ML_MIN * 10, 15)
        self.assertEqual(r.val('pump.indicated_pa'), 0.0)                      # ... out through the open priming valve
        self.assertLess(abs(r.call(r.E, 'ledger.read')['volume_residual_m3']), 1e-14)
        r.tap('p_prime')                                                      # stop priming, close the valve
        self.assertFalse(r.val('pump.priming'))
        self.assertTrue(r.call(r.A, 'panel.turn', input_id='priming_valve', detents=1)['accepted'])
        self.assertFalse(r.val('priming_valve.open'))
        r.enter('p_rate_ml', '0.8')                                           # enter the flow rate and turn on the flow
        self.assertAlmostEqual(r.val('pump.target_m3_s'), 0.8 * rt.ML_MIN)
        r.tap('p_flow')
        self.assertTrue(r.val('pump.enabled'))
        r.step(1)
        self.assertAlmostEqual(r.val('pump.indicated_pa'), rt.PLANT_EXAMPLE['hydraulic_resistance_pa_s_m3'] * 0.8 * rt.ML_MIN)
        self.assertIn('psi', r.text('p_pressure'))
        done('prime the pump from the app')

    def test_set_eluent_concentration_and_start_egc(self):
        r = Rig()
        r.enter('h_egc_set', '12')                                            # EGC concentration on the keypad
        self.assertAlmostEqual(r.val('eluent.target_mol_m3'), 12.0)
        self.assertEqual(r.widgets()['h_egc_set']['value_text'], '12.00')
        r.tap('h_flow')                                                       # flow on
        self.assertTrue(r.val('pump.enabled'))
        r.tap('h_egc_on')
        r.tap('h_crtc_on')
        self.assertTrue(r.val('electrolytics.egc_on') and r.val('electrolytics.crtc_on'))
        self.assertTrue(r.is_on('h_egc_on') and r.is_on('h_crtc_on'))
        self.assertEqual(r.text('h_crtc_v'), '24.00 V')
        r.tap('h_flow')                                                       # flow off: EGC and CR-TC power go off with it
        self.assertFalse(r.val('electrolytics.egc_on') or r.val('electrolytics.crtc_on'))
        self.assertFalse(r.is_on('h_egc_on'))
        done('set eluent concentration and start EGC')

    def test_autozero_the_conductivity_detector(self):
        r = Rig()
        r.tap('h_flow')
        r.tap('h_egc_on')                                                     # suppressor off: the KOH eluent background is seen
        r.step(1, 30)
        total = r.val('detector.total_us')
        k = rt.METHOD_EXAMPLE['eluent_mol_m3'] * (r.h.templates['k_cation1']['molar_conductivity_S_m2_mol'] +
                                                  r.h.templates['oh_anion1']['molar_conductivity_S_m2_mol']) * 1e4
        self.assertAlmostEqual(total, k, 6)
        self.assertEqual(r.val('detector.offset_us'), total)
        r.tap('h_autozero')
        self.assertAlmostEqual(r.val('detector.zero_us'), total)
        self.assertAlmostEqual(r.val('detector.offset_us'), 0.0)
        self.assertEqual(r.text('h_cd_offset'), '0.00 µS')
        self.assertIn(f'{total:.2f}', r.text('h_cd_total'))                   # the total keeps the background
        done('autozero the conductivity detector')

    def test_assign_a_ttl_input_function(self):
        r = Rig()
        r.tap('t_acc')
        r.ov('TTL/RELAY')
        self.assertEqual(r.page(), ('app', 'ttl_relay'))
        r.tap('tr_to_input')
        self.assertEqual(r.page(), ('app', 'ttl_input'))
        r.tap('ti_1_fn2')                                                     # TTL 1: PUMP ON
        r.tap('ti_1_ty2')                                                     # control type NORMAL PULSE
        s = r.val('app.settings')
        self.assertEqual((s['ttl_function'][0], s['ttl_type'][0]), ('PUMP ON', 'NORMAL PULSE'))
        self.assertTrue(r.widgets()['ti_1_fn2']['selected'] and r.widgets()['ti_1_ty2']['selected'])
        # the assigned function works: a pulse (falling edge active) toggles the pump
        r.call(r.E, 'io.ttl_input', input=1, level_v=0)
        self.assertTrue(r.val('pump.enabled'))
        r.call(r.E, 'io.ttl_input', input=1, level_v=5)                       # the rising edge is ignored
        self.assertTrue(r.val('pump.enabled'))
        r.call(r.E, 'io.ttl_input', input=1, level_v=0)
        self.assertFalse(r.val('pump.enabled'))
        r.tap('ti_1_fn2')                                                     # tapping the selected function clears it
        self.assertEqual(r.val('app.settings')['ttl_function'][0], 'NONE')
        done('assign a TTL input function')

    def test_view_an_error(self):
        r = Rig()
        r.tap('s_pump')
        r.enter('p_low_psi', '3000')                                          # a low limit above the working pressure (1934 psi at 1 mL/min)
        r.tap('p_flow')
        r.step(1)
        self.assertFalse(r.val('pump.enabled'))                               # the pump stops ...
        self.assertEqual(r.val('alerts')[-1]['text'], 'Pressure fallen below lower limit')
        self.assertTrue(r.frame()['displays']['led_alert']['on'])             # ... the alert LED flashes and the warning icon lights
        self.assertEqual(r.widgets()['t_warn']['style'], 'warn')
        r.tap('t_warn')                                                       # single-message view
        self.assertEqual(r.dialog_lines(), ['Pressure fallen below lower limit'])
        self.assertEqual(r.overlay()['title'], 'Total Messages: 1')
        r.ov('LIST VIEW')
        self.assertEqual(r.page(), ('app', 'errors'))
        self.assertEqual(r.widgets()['x_list']['cells'][0][:2], ['Abort', 'Pressure fallen below lower limit'])
        x, y, w, h = r.widgets()['x_list']['rect']
        r.touch(x + 20, y + 10)                                               # a row opens the message
        self.assertEqual(r.dialog_lines(), ['Pressure fallen below lower limit'])
        r.ov('DELETE')
        self.assertEqual(r.val('alerts'), [])
        self.assertFalse(r.frame()['displays']['led_alert']['on'])
        done('view an error')


class Pages(unittest.TestCase):
    def test_pump_page_units_limits_vacuum_and_seal_wash_absent(self):
        r = Rig()
        r.tap('s_pump')
        r.tap('p_fu_ul_min')                                                  # µL/min for every page
        self.assertEqual(r.val('app.settings')['flow_unit'], 'uL/min')
        self.assertEqual(r.widgets()['p_rate_ul']['value_text'], '1000')
        r.enter('p_rate_ul', '500')
        self.assertAlmostEqual(r.val('pump.target_m3_s'), 0.5 * rt.ML_MIN)
        r.tap('p_pu_mpa')
        r.enter('p_high_mpa', '30')
        self.assertAlmostEqual(r.val('app.settings')['high_limit_pa'], 30e6)
        r.enter('p_low_mpa', '29.8')                                          # high must be >= low + 0.34 MPa
        self.assertEqual(r.dialog_lines(), ['High limit must be at least 0.34 MPa (50 psi) above the low limit.'])
        self.assertEqual(r.val('app.settings')['low_limit_pa'], 0.0)
        r.ov('OK')
        r.enter('p_high_mpa', '50')                                           # outside 0-41 MPa: the keypad refuses
        self.assertEqual(r.overlay()['error'], 'Out of range')
        r.ov('Cancel')
        r.tap('p_vac')
        self.assertFalse(r.val('app.settings')['degas_enabled'])
        self.assertEqual(r.text('p_vac_ok'), 'VACUUM NOT OK')
        r.tap('p_to_acc')
        self.assertEqual(r.page(), ('app', 'pump_acc'))
        self.assertEqual(r.text('pa_sw_none'), 'NOT INSTALLED')                 # optional seal wash pump not installed (RFIC configuration)
        self.assertNotIn('pa_prime', r.widgets())
        self.assertNotIn('seal_wash_prime', r.val('app.settings'))
        self.assertEqual(r.call(r.A, 'pump.configure', expect='rejected', seal_wash_prime=True)['error']['code'], 'INVALID_ARGUMENT')
        r.tap('pa_to_pump')
        self.assertEqual(r.page(), ('app', 'pump'))

    def test_upper_limit_trips_and_closed_valve_prime(self):
        r = Rig()
        r.tap('s_pump')
        r.tap('p_prime')                                                      # priming with the valve closed goes through the column ...
        r.step(1)
        self.assertFalse(r.val('pump.priming'))                               # ... 6 mL/min exceeds 6000 psi: the pump stops
        self.assertEqual(r.val('alerts')[-1]['text'], 'Upper pressure limit exceeded')

    def test_valves_detector_temperature_pages(self):
        r = Rig()
        r.tap('s_valves')
        r.tap('v_pos')                                                        # INJECT: the loaded sample goes onto the column
        self.assertEqual(r.val('valve.position'), 'inject')
        self.assertTrue(r.val('valve.injected'))
        self.assertAlmostEqual(r.call(r.E, 'ledger.read')['sample_remaining_m3'], rt.SAMPLE_EXAMPLE['volume_m3'] - rt.METHOD_EXAMPLE['injection_m3'])
        r.step(30)
        self.assertEqual(r.text('v_time'), '0.50 min')
        r.tap('v_pos')
        self.assertEqual(r.val('valve.position'), 'load')
        r.go_home()
        r.tap('s_detector')
        r.choose('d_rise', '0.5')
        r.enter('d_tcomp', '2.1')
        self.assertEqual((r.val('detector.rise_time_s'), r.val('detector.temp_comp_pct_per_k')), (0.5, 2.1))
        r.enter('d_cell_sp', '40')
        self.assertAlmostEqual(r.val('temperature.cd_cell_target_k'), 313.15)
        r.enter('d_cell_sp', '31')                                            # below ambient + 7 °C
        self.assertEqual(r.dialog_lines(), ['Temperature setpoint out of range.'])
        r.ov('OK')
        r.go_home()
        r.tap('s_temperature')
        r.enter('t_column_sp', '35')
        self.assertAlmostEqual(r.val('column.target_k'), 308.15)
        self.assertEqual(r.text('t_column_icon'), '!')                        # heating towards the new setpoint
        r.step(30)
        self.assertEqual(r.text('t_column_icon'), 'OK')
        r.tap('t_compartment_on')
        self.assertFalse(r.val('temperature.compartment_on'))
        r.step(60)
        self.assertAlmostEqual(r.val('temperature.compartment_k'), rt.PLANT_EXAMPLE['ambient_k'], 6)

    def test_plot_page_and_screen_saver(self):
        r = Rig()
        r.tap('h_flow')
        r.call(r.A, 'acquisition.start', expect='accepted')                                     # data acquisition resets the plot
        r.step(1, 30)
        r.tap('h_plot')
        self.assertEqual(r.page(), ('app', 'plot'))
        r.tap('pl_sel_pressure')
        r.choose('pl_time', '5 min')
        r.choose('pl_range', '100 µS')
        s = r.val('app.settings')
        self.assertEqual((s['plot_pressure'], s['plot_time_min'], s['plot_range_us']), (True, 5, 100.0))
        p = r.widgets()['pl_plot']
        self.assertEqual(p['x_range'], [0.0, 5.0])
        self.assertEqual(p['y_range']['left'], [0.0, 100.0])
        self.assertEqual([x['label'] for x in p['series']], ['CD', 'PRESSURE'])
        self.assertEqual(len(p['series'][0]['points']), 30)
        self.assertEqual(r.text('pl_elapsed'), 'ELAPSED  0.50 min')
        r.step(1, 299)                                                        # 5 min without a touch: screen saver (plot + pressure)
        self.assertEqual(r.frame()['screen'], 'app')
        r.step(1)
        self.assertEqual(r.frame()['screen'], 'saver')
        self.assertIn('psi', r.text('ss_p'))
        r.touch(600, 400)                                                     # a touch returns to the last page
        self.assertEqual(r.page(), ('app', 'plot'))

    def test_analog_ttl_relay_pages(self):
        r = Rig()
        r.tap('t_acc')
        r.ov('ANALOG')
        r.tap('a_mode_full')
        self.assertEqual(r.val('analog.output_v'), 1.0)
        r.tap('a_mode_zero')
        self.assertEqual(r.val('analog.output_v'), 0.0)
        r.tap('a_mode_normal')
        r.enter('a_offset', '10')
        r.choose('a_range', '1')
        r.tap('a_pol')
        s = r.val('app.settings')
        self.assertEqual((s['analog_offset_pct'], s['analog_range_us'], s['analog_polarity']), (10.0, 1.0, 'INVERTED'))
        self.assertAlmostEqual(r.val('analog.output_v'), 0.1)                 # zero signal: the offset only
        r.tap('t_acc')
        r.ov('TTL/RELAY')
        r.tap('tr_ttl1')
        r.tap('tr_rel2')
        self.assertEqual(r.val('io.outputs'), {'ttl_v': [0, 5], 'relay': ['OPEN', 'CLOSE']})
        r.tap('tr_link1')                                                     # relay 1 follows the pump flow
        self.assertEqual(r.val('io.outputs')['relay'][0], 'OPEN')
        r.call(r.A, 'pump.set', enabled=True)
        self.assertEqual(r.val('io.outputs')['relay'][0], 'CLOSE')
        self.assertTrue(r.is_on('tr_rel1'))
        r.call(r.E, 'io.ttl_input', input=2, level_v=0)                      # input status read-out
        self.assertEqual(r.text('tr_in2'), 'TTL 2 INPUT STATUS  0 V')

    def test_consumables_rescan_approve(self):
        r = Rig()
        r.tap('t_cons')
        self.assertEqual(r.page(), ('app', 'consumables'))
        self.assertEqual(r.text('c_rescan'), 'Rescan')
        lst = r.widgets()['c_list']
        self.assertEqual([c[2] for c in lst['cells']], [c['name'] for c in rt.CONSUMABLES])
        x, y, w, h = lst['rect']
        r.touch(x + 20, y + 4 * 60 + 30)                                      # untick the separator column (RFID)
        self.assertEqual(r.text('c_rescan'), 'Approve')                       # Rescan becomes Approve
        r.touch(x + 20, y + 30)                                               # a wired device stays ticked
        self.assertTrue(r.val('consumables')[0]['checked'])
        r.tap('c_rescan')
        sep = r.val('consumables')[4]
        self.assertFalse(sep['enabled'])
        self.assertEqual(r.text('c_rescan'), 'Rescan')
        r.call(r.A, 'pump.set', enabled=True)
        r.step(60)
        c = {x['id']: x for x in r.val('consumables')}
        self.assertAlmostEqual(c['guard']['eluent_ml'], 1.0)                   # usage counted only for the enabled device
        self.assertEqual(c['separator']['eluent_ml'], 0.0)

    def test_information_language_and_service_menu(self):
        r = Rig()
        r.tap('t_svc')
        r.ov('INFORMATION')
        self.assertEqual(r.page(), ('app', 'information'))
        self.assertIn(INST, r.text('i_2'))
        r.tap('t_lang')
        r.ov('English')
        self.assertEqual(r.val('app.settings')['language'], 'EN')
        r.tap('t_back')
        self.assertEqual(r.page(), ('app', 'home'))


class Calibration(unittest.TestCase):
    def calibrate(self, r):
        r.tap('t_svc')
        r.ov('CALIBRATION')
        r.call(r.A, 'cd_cell.connect', source='gas')                          # He / N2 through the cell
        r.step(1)
        r.tap('k_offset')
        self.assertEqual(r.val('calibration.cd')['state'], 'offset')
        self.assertEqual(r.widgets()['k_status']['fraction'], 0.5)
        r.call(r.A, 'cd_cell.connect', source='kcl_1mm')                      # 1.0 mM KCl
        r.step(1)
        r.tap('k_slope')
        self.assertEqual(r.val('calibration.cd')['state'], 'calibrated')
        self.assertEqual(r.text('k_status'), 'CALIBRATION COMPLETE')
        r.step(1, 20)

    def test_calibration_removes_an_injected_error(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', offset_us=3.0, gain=0.04)
        r.call(r.A, 'cd_cell.connect', source='kcl_1mm')
        r.step(1, 20)
        self.assertAlmostEqual(r.val('detector.total_us'), 147.0 * 1.04 + 3.0 / (1 + 0.017 * 10), 3)   # the error shows before calibration
        r.call(r.A, 'cd_cell.connect', source='column')
        self.calibrate(r)
        self.assertAlmostEqual(r.val('detector.total_us'), 147.0, 6)          # KCl reads 147.00 µS after calibration
        r.call(r.A, 'cd_cell.connect', source='gas')
        r.step(1, 20)
        self.assertAlmostEqual(r.val('detector.total_us'), 0.0, 6)

    def test_ideal_sensor_unchanged_and_panel_equals_direct(self):
        r = Rig()
        self.calibrate(r)
        cal = r.val('calibration.cd')
        self.assertEqual((cal['offset_us'], cal['slope']), (0.0, 1.0))        # an ideal cell keeps the identity
        d = Rig()
        d.call(d.A, 'cd_cell.connect', source='gas')
        d.step(1)
        d.call(d.A, 'sensor.calibrate_cd_offset')
        d.call(d.A, 'cd_cell.connect', source='kcl_1mm')
        d.step(1)
        d.call(d.A, 'sensor.calibrate_cd_slope')
        self.assertEqual(d.val('calibration.cd'), cal)

    def test_refused_calibration_and_reset(self):
        r = Rig()
        r.tap('t_svc')
        r.ov('CALIBRATION')
        r.call(r.A, 'cd_cell.connect', source='kcl_1mm')
        r.step(1)
        r.tap('k_offset')                                                     # 147 µS is no zero point
        self.assertEqual(r.dialog_lines(), ['Insufficient CD cell data for this request'])
        self.assertEqual(r.val('calibration.cd')['state'], 'factory')
        r.ov('OK')
        r.tap('k_slope')                                                      # slope before offset
        self.assertEqual(r.val('calibration.cd')['state'], 'factory')
        r.ov('OK')
        r.call(r.E, 'sensor.configure_error', offset_us=0.0, gain=0.3)
        r.call(r.A, 'cd_cell.connect', source='gas')
        r.call(r.A, 'sensor.calibrate_cd_offset')
        r.call(r.A, 'cd_cell.connect', source='kcl_1mm')
        r.call(r.A, 'sensor.calibrate_cd_slope', expect='rejected')            # 191 µS: outside 147 ± 20 %
        r.call(r.A, 'sensor.reset_calibration')
        self.assertEqual(r.val('calibration.cd'), {'offset_us': 0.0, 'slope': 1.0, 'state': 'factory'})


class Equivalence(unittest.TestCase):
    def state(self, r):
        v = r.h.read('evaluator')
        for k in ('valve.elapsed_s',):
            v.pop(k)
        return v

    def test_panel_equals_direct_commands(self):
        p, d = Rig(), Rig()
        p.enter('h_rate_ml', '0.5')
        p.tap('h_flow')
        p.enter('h_egc_set', '5')
        p.tap('h_egc_on')
        p.tap('h_sup_on')
        p.enter('h_sup_set', '20')
        p.enter('h_cd_cell_sp', '38')
        p.tap('h_compartment_on')
        p.tap('h_autozero')
        p.tap('h_ps_pressure')
        p.choose('h_ptime', '10 min')
        d.call(d.A, 'method.update', flow_m3_s=0.5 * rt.ML_MIN)
        d.call(d.A, 'pump.set', enabled=True)
        d.call(d.A, 'method.update', eluent_mol_m3=5.0)
        d.call(d.A, 'electrolytics.set', device='egc', enabled=True)
        d.call(d.A, 'electrolytics.set', device='suppressor', enabled=True)
        d.call(d.A, 'method.update', suppression_current_a=0.02)
        d.call(d.A, 'temperature.set', zone='cd_cell', setpoint_k=311.15)
        d.call(d.A, 'temperature.set', zone='compartment', enabled=False)
        d.call(d.A, 'detector.autozero')
        d.call(d.A, 'plot.configure', pressure=True)
        d.call(d.A, 'plot.configure', time_min=10)
        self.assertEqual(self.state(p), self.state(d))
        p.step(1, 20)
        d.step(1, 20)
        self.assertEqual(self.state(p), self.state(d))


class Rules(unittest.TestCase):
    def test_sequence_lock_and_interrupt(self):
        r = Rig()
        r.tap('h_flow')
        r.call(r.A, 'acquisition.start', expect='accepted')
        r.step(1, 5)
        self.assertEqual(r.text('t_lock'), 'LOCKED')
        self.assertTrue(r.frame()['displays']['led_running']['on'])
        before = r.val('pump.target_m3_s')
        self.assertTrue(r.tap('h_rate_ml'))                                   # a setting while the sequence runs: the warning, no keypad
        self.assertEqual(r.overlay()['dialog'], 'interrupt')
        r.ov('WAIT')
        self.assertIsNone(r.overlay())
        self.assertEqual(r.val('acquisition.phase'), 'running')
        r.tap('h_ps_pressure')                                                # plot settings stay usable
        self.assertTrue(r.val('app.settings')['plot_pressure'])
        r.tap('h_egc_on')
        r.ov('STOP SEQUENCE')
        self.assertEqual(r.val('acquisition.phase'), 'cancelled')
        self.assertEqual(r.val('alerts')[-1]['text'], 'Chromeleon run stopped from tablet')
        self.assertEqual(r.val('pump.target_m3_s'), before)
        self.assertEqual(r.text('t_lock'), 'OPEN')

    def test_another_agent_cannot_stop_the_sequence(self):
        """Final sync 2026-10-19: the sequence belongs to the session that started it; another agent's STOP SEQUENCE in the app, acquisition.stop and
        pump off are refused (the app's message, state unchanged); the rear power switch and the doors are physical: never refused."""
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        b = r.h.session('agent_b')

        def tap_b(wid):
            x, y, w, h = r.widgets()[wid]['rect']
            return r.call(b, 'panel.touch', display_id=TAB, x=x + w // 2, y=y + h // 2)['accepted']
        r.tap('h_flow')
        op = r.call(r.A, 'acquisition.start', expect='accepted')['operation_id']
        r.step(1, 5)
        before = r.h.read('evaluator')
        for cap, args in (('acquisition.stop', {}), ('sequence.interrupt', {}), ('pump.set', dict(enabled=False))):
            self.assertEqual(r.call(b, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertTrue(tap_b('h_egc_on'))                                    # the interrupt warning is only a screen
        x, y, w, h = next(bt for bt in r.overlay()['buttons'] if bt['label'] == 'STOP SEQUENCE')['rect']
        self.assertFalse(r.call(b, 'panel.touch', display_id=TAB, x=x + w // 2, y=y + h // 2)['accepted'])
        self.assertEqual(r.dialog_lines(), ['This sequence was started by another user.'])
        self.assertEqual(r.h.read('evaluator')['acquisition.phase'], 'running')
        self.assertEqual(r.A.poll(op)['status'], 'running')
        r.call(b, 'door.set', angle_rad=1.0)                                  # the column door opens by hand: the documented warning
        self.assertEqual(r.val('alerts')[-1]['text'], 'Column door opened')
        self.assertEqual(r.val('acquisition.phase'), 'running')
        r.call(b, 'door.set', angle_rad=0.0)
        r.call(b, 'power.set', enabled=False)                                 # the rear power switch: a power loss
        self.assertEqual(r.A.poll(op)['status'], 'cancelled')

    def test_documented_error_messages(self):
        """Table 14 messages injected by the evaluator: Abort stops the sequence (pump messages stop the flow) and the Ready Check refuses a new
        start until the cause is cleared; Warning only logs the message."""
        r = Rig()
        r.tap('h_flow')
        op = r.call(r.A, 'acquisition.start', expect='accepted')['operation_id']
        r.step(1, 3)
        self.assertEqual(r.call(r.A, 'fault.inject', expect='rejected', code='leak_sensor_wet')['error']['code'], 'ACCESS_DENIED')
        r.call(r.E, 'fault.inject', code='leak_sensor_wet')
        self.assertEqual(r.val('acquisition.phase'), 'running')
        r.call(r.E, 'fault.inject', code='leak_detected_flow_stopped')
        self.assertEqual(r.A.poll(op)['status'], 'failed')
        self.assertFalse(r.val('pump.enabled'))
        self.assertEqual([a['text'] for a in r.val('alerts')[-2:]], ['Leak sensor wet', 'Leak detected. Flow stopped.'])
        self.assertTrue(r.frame()['displays']['led_alert']['on'])
        self.assertEqual(r.call(r.A, 'pump.set', expect='rejected', enabled=True)['error']['code'], 'NOT_READY')
        r.call(r.E, 'fault.clear', code='leak_detected_flow_stopped')
        r.call(r.A, 'pump.set', enabled=True)
        self.assertTrue(r.val('pump.enabled'))

    def test_refusals_show_the_app_message(self):
        r = Rig()
        before = copy.deepcopy(r.h.read('evaluator'))
        r.tap('h_egc_on')                                                     # flow off
        self.assertEqual(r.dialog_lines(), ['The EGC, CR-TC and suppressor are always off when the pump flow is off.'])
        self.assertEqual(r.frame()['ui']['last_gesture']['rejection'], 'FLOW_OFF')
        r.ov('OK')
        r.enter('h_rate_ml', '5')                                             # RFIC eluent generator: 0.1-3 mL/min
        self.assertEqual(r.dialog_lines(), ['EG invalid flow rate'])
        r.ov('OK')
        after = r.h.read('evaluator')
        self.assertEqual(before, after)                                       # refusals change nothing
        n = Rig(method=False, sample=False)
        n.tap('h_flow')
        self.assertEqual(n.dialog_lines()[0][:10], 'Not ready:')

    def test_power_off_and_gates(self):
        r = Rig()
        r.tap('s_pump')
        r.switch('off')
        self.assertEqual(r.frame()['screen'], 'off')
        self.assertFalse(r.touch(100, 100))
        self.assertIsNone(r.val('detector.total_us'))
        r.switch('on')
        self.assertEqual(r.frame()['screen'], 'startup')
        r.step(11)
        self.assertEqual(r.page(), ('app', 'home'))                           # a new power-up starts on Home

    def test_rejected_gestures(self):
        r = Rig()
        for args in (dict(display_id='nowhere', x=1, y=1), dict(display_id=TAB, x=5000, y=1)):
            res = r.call(r.A, 'panel.touch', expect='rejected', **args)
            self.assertEqual(res['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call(r.A, 'panel.frame', expect='rejected')['error']['code'], 'ACCESS_DENIED')   # evaluator only
        self.assertFalse(r.touch(173, 790))                                   # no widget there


class SyncAuditFixes(unittest.TestCase):
    """1.1.1 (sync audit 2026-10-19): with the EGC off an RFIC system pumps deionized water only, so nothing elutes; the sequence run switches the
    electrolytic devices on as its instrument method does."""

    def test_no_elution_without_eluent_generation(self):
        r = Rig()
        r.tap('h_flow')                                                       # pump flow, EGC off
        r.tap('s_valves')
        r.tap('v_pos')                                                        # manual injection onto the column
        self.assertTrue(r.val('valve.injected'))
        r.step(1, 60)
        led = r.call(r.E, 'ledger.read')
        self.assertEqual(led['eluted_mol'], 0.0)                              # the ions stay bound: no hydroxide eluent
        self.assertAlmostEqual(led['column_mol'], led['injected_mol'], 20)
        self.assertAlmostEqual(r.val('detector.total_us'), 0.0, 9)
        r.go_home()
        r.tap('h_egc_on')                                                     # EGC (and CR-TC) on: KOH eluent, the bands move
        r.tap('h_sup_on')
        self.assertTrue(r.val('electrolytics.egc_on'))
        r.step(1, 60)
        led = r.call(r.E, 'ledger.read')
        self.assertGreater(led['eluted_mol'], 0.9 * led['injected_mol'])

    def test_sequence_run_switches_the_electrolytics_on(self):
        r = Rig()
        r.tap('h_flow')
        self.assertFalse(r.val('electrolytics.egc_on'))
        r.call(r.A, 'acquisition.start', expect='accepted')
        self.assertTrue(r.val('electrolytics.egc_on') and r.val('electrolytics.crtc_on') and r.val('electrolytics.suppressor_on'))
        r.step(1, 61)
        self.assertEqual(r.val('acquisition.phase'), 'succeeded')
        self.assertGreater(max(p['conductivity_s_m'] for p in r.call(r.A, 'process.measure')['points']), 0)
        self.assertFalse(r.val('electrolytics.egc_on'))                       # the run ends with the pump off: always off without flow


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        r = Rig()
        r.tap('h_flow')
        r.tap('h_egc_on')
        r.tap('h_sup_on')
        r.call(r.A, 'acquisition.start', expect='accepted')
        r.step(1, 40)
        a = r.h.panel.png_bytes(TAB)[0]
        b = r.h.panel.png_bytes(TAB)[0]
        self.assertEqual(a, b)
        c = copy.deepcopy(r.h)                                                # a copied runtime renders the same screen
        self.assertEqual(c.panel.png_bytes(TAB)[0], a)
        OUT.mkdir(parents=True, exist_ok=True)
        renders = {'home': r.h.panel.save_png(TAB, OUT / 'home.png')}
        r.tap('s_pump')
        renders['menu'] = r.h.panel.save_png(TAB, OUT / 'menu.png')
        r.tap('p_flow')                                                       # locked: the interrupt warning
        renders['error'] = r.h.panel.save_png(TAB, OUT / 'error.png')
        r.ov('WAIT')
        r.tap('t_home')
        r.tap('s_plot')
        renders['plot'] = r.h.panel.save_png(TAB, OUT / 'plot.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')

    def test_capture_through_the_session(self):
        r = Rig()
        cap = r.call(r.A, 'panel.capture', display_id=TAB)
        self.assertEqual((cap['image']['width'], cap['image']['height']), (1280, 800))
        self.assertTrue(Path(cap['image']['path']).is_file())
        led = r.call(r.A, 'panel.capture', display_id='led_power')
        self.assertEqual(led['image']['width'], 24)

    def test_spec_and_contract(self):
        spec = json.loads((rt.ASSET_DIR / 'panel_ui.json').read_text())
        self.assertEqual(pu.check_spec(spec, set(rt.CAPS)), [])
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual(ext['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + rt.APP_CAPS:
            self.assertIn(k, c['capabilities'])
        for k in pu.spec_capabilities(spec):
            self.assertIn(k, c['capabilities'])
        self.assertIn('priming_valve', c['components'])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])            # the shared engine is not pinned
        m = rt.export_thermo_dionexintegrion_contract_metadata()
        for k in rt.BASE_CAPS:                                                # existing commands keep their semantics
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        missing = sorted(set(SURVEY_TASKS) - DONE_TASKS)
        self.assertEqual(missing, [], 'run the whole module')


if __name__ == '__main__':
    unittest.main()
