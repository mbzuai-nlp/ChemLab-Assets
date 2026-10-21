"""Front-panel tests for CHEM-141 lakeshore_measurereadym91 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on
scripts/asset_runtime/lakeshore_measurereadym91.py): python3 -m pytest tests/test_panel_ui_lakeshore_measurereadym91.py -q

Every gesture goes through the session API (panel.press / panel.touch, the runtime's normal invoke path); widgets are found by id in the structured
frame (panel.frame, evaluator) and tapped at their centre.  Measurements are started over SCPI (acquisition.start: the front panel only shows
results, manual p.34).  Themes: the key procedures of the menu survey (sleep / wake, view results, SCPI over TCP and a static IP address, factory
reset, firmware update, the DC Hall 'waiting' display); the System settings tabs (About, Display and sound, Privacy, Date and time, Legal); panel =
direct capability (same runtime state); refusals (invalid IP address or date, SLEEP, SYST:KLOC lock) with the state unchanged; time-outs (auto
dimming, lock message); render determinism and the evidence PNGs; contract entries."""
import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import lakeshore_measurereadym91 as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'm91_01'
D = 'screen'
OUT = ROOT / 'outputs/panel_ui/lakeshore_measurereadym91'
SURVEY_TASKS = ['sleep and wake', 'view results', 'scpi over tcp and static ip', 'factory reset', 'firmware update', 'dc hall waiting']
DONE_TASKS = set()


class Rig:
    """A runtime with an agent and an evaluator session; the plant, the field, the thermal boundary and the sample by the evaluator."""

    def __init__(self, on=True, sample=True):
        self.h = rt.LakeShoreMeasurereadyM91Runtime(INST)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        for cap, args in (('plant.configure', rt.PLANT_EXAMPLE), ('geometry.configure', rt.GEOMETRY_EXAMPLE), ('thermal.configure', rt.THERMAL_EXAMPLE),
                          ('field.configure', rt.FIELD_EXAMPLE)):
            self.call(self.E, cap, **args)
        if sample:
            self.call(self.E, 'sample.load', **rt.SAMPLE_EXAMPLE)
        if on:
            assert self.press('power_button')

    def prepare(self):
        """Clamp, temperature, field and method over SCPI, then the 1800 s warm-up."""
        for cap, args in (('sample.set_clamp', {'closed': True}), ('thermal.set_temperature', {'temperature_k': rt.PLANT_EXAMPLE['ambient_k']}),
                          ('field.set_target', {'field_t': 0.5})):
            self.call(self.E, cap, **args)                                        # host: clamp, external temperature and field controllers
        self.call(self.A, 'method.configure', **rt.METHOD_EXAMPLE)
        self.step(rt.WARMUP_S)

    # ---- sessions
    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def setting(self, key):
        return self.val('system.settings')[key]

    def step(self, dt):
        self.h.step(dt)

    # ---- gestures and screen
    def press(self, key, hold=0.1):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=hold)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][D]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=D, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid, dy=0.5):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + int(h * dy))

    def go(self, *path):
        for wid in path:
            assert self.tap(wid), wid

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        for b in self.overlay()['buttons']:
            if b['label'] == label and b['id'] != 'field':                       # not the entry line shown on top of a keypad
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in self.overlay()["buttons"]]}')

    def field(self, form_widget, label):
        """Tap the row of a form field by its label."""
        w = self.widgets()[form_widget]
        k = [r.split('  ')[0] for r in w['rows']].index(label)
        return self.touch(w['rect'][0] + 40, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)

    def type(self, text):
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov
        self.ov('C')
        for ch in text:
            self.ov(ch)
        assert self.overlay()['field'] == text, (self.overlay()['field'], text)
        return self.ov('OK')


def measure(g, wait=True):
    g.call(g.A, 'acquisition.start')
    if wait:
        g.step(g.h.expected_duration + 0.01)


AUTO = dict(max_current_a=0.1, max_voltage_v=10.0, points=11, minimum_r_squared=0.9999)


def run(g, cap, wait=True, **args):
    """A measurement type started over SCPI (MeasureLINK) by the agent, run to its end; then 1 s idle for the detector to settle."""
    out = g.call(g.A, cap, **args)
    if wait:
        g.step(out['expected_duration_s'] + 1e-6)
        g.step(1.0)
    return g.call(g.A, 'measurement.result', type=rt.MEAS_STARTS[cap])


def lines(g, wid):
    """All key / value lines of a result list: the visible rows, then drag the list up until its last row is shown."""
    out = {}
    while g.widgets()[wid]['scroll'] > 0:                                         # back to the top first (the scroll position is kept)
        x, y, ww, hh = g.widgets()[wid]['rect']
        g.touch(x + ww // 2, y + 5, to_x=x + ww // 2, to_y=y + hh - 5)
    while True:
        w = g.widgets()[wid]
        out.update({k: v for k, v in w['cells']})
        if w['scroll'] + len(w['cells']) >= w['count']:
            return out
        x, y, ww, hh = w['rect']
        g.touch(x + ww // 2, y + hh - 5, to_x=x + ww // 2, to_y=y + 5)


def full_session(g):
    """Optimizing contact check and linked resistivity at zero field, then linked FastHall and four wire at 0.5 T."""
    g.call(g.E, 'field.set_target', field_t=0.0)
    g.step(5.0)
    run(g, 'contact_check.auto', **AUTO)
    run(g, 'resistivity.start_link', voltage_range_v=0.1, thickness_m=0.0005, samples=4, minimum_snr=0.0)
    g.call(g.E, 'field.set_target', field_t=0.5)
    g.step(5.0)
    run(g, 'fasthall.start_link', field_t=0.5, voltage_range_v=0.01, samples=4, minimum_snr=0.0)
    run(g, 'four_wire.start', **rt.FW_EXAMPLE)


class SurveyProcedures(unittest.TestCase):
    def test_sleep_and_wake(self):
        g = Rig(on=False)
        f = g.frame()
        self.assertEqual(f['screen'], 'sleep')
        self.assertFalse(g.touch(400, 240))                                       # SLEEP: the screen is dark, touches are ignored
        self.assertTrue(g.press('power_button'))                                  # short press: ON
        self.assertTrue(g.val('power.enabled'))
        self.assertEqual(g.page(), ('main', 'home'))
        self.assertEqual(g.frame()['displays']['power_led']['on'], True)
        g.step(1800.0)
        self.assertTrue(g.val('warmup.ready'))
        self.assertTrue(g.press('power_button'))                                  # short press again: SLEEP (warm-up and excitation stop)
        self.assertFalse(g.val('power.enabled'))
        self.assertEqual(g.page()[0], 'sleep')
        led = []
        for _ in range(4):
            led.append(g.frame()['displays']['power_led']['on'])
            g.step(0.5)
        self.assertEqual(sorted(set(led)), [False, True])                         # slow pulse in SLEEP
        g.press('power_button')
        self.assertFalse(g.val('warmup.ready'))                                   # the warm-up restarts
        DONE_TASKS.add('sleep and wake')

    def test_view_results(self):
        g = Rig()
        g.prepare()
        self.assertIn('Not run', g.text('card_dchall'))
        self.assertIn('Not run', g.text('card_optimization'))
        g.tap('card_dchall')                                                      # the first touch only wakes the dimmed display (no touch for 30 min)
        self.assertEqual(g.page(), ('main', 'home'))
        g.go('card_contact')
        self.assertEqual(g.page()[1], 'r_cc')
        self.assertEqual(g.disp()['title'], 'Van Der Pauw contact check results')
        self.assertEqual(g.widgets()['cc_lines']['cells'][0][0], 'No results.')
        g.go('r_cc_back')
        measure(g, wait=False)
        g.step(g.h.expected_duration / 2)
        self.assertRegex(g.text('card_dchall'), r'Running  \d+ %')
        g.step(g.h.expected_duration)
        self.assertIn('Complete  9 readings', g.text('card_dchall'))
        g.go('card_dchall')
        self.assertEqual(g.page()[1], 'results')
        rows = g.widgets()['rs_list']['cells']
        self.assertEqual(len(rows), 9)
        self.assertEqual([r[0] for r in rows[:3]], ['vdp_a'] * 3)
        self.assertEqual([r[1] for r in rows[:3]], ['zero', 'positive', 'negative'])
        res = g.call(g.A, 'acquisition.result')
        self.assertEqual(rows[1][2], f"{res['rows'][1]['current_a']:.4e}")
        self.assertEqual(rows[1][3], f"{res['rows'][1]['voltage_v']:.4e}")
        self.assertIn('Current', g.text('rs_setup'))
        g.go('results_back')
        self.assertEqual(g.page()[1], 'home')
        DONE_TASKS.add('view results')

    def test_scpi_over_tcp_and_static_ip(self):
        g = Rig()
        g.go('settings_menu', 'set_connectivity')
        self.assertTrue(g.widgets()['cn_scpi']['value'])
        g.go('cn_scpi')
        self.assertFalse(g.setting('scpi_tcp'))
        g.go('cn_scpi')                                                           # enable SCPI over TCP (port 7777)
        self.assertTrue(g.setting('scpi_tcp'))
        g.go('cn_serial')
        self.assertFalse(g.setting('virtual_serial'))
        g.go('cn_ethernet')
        self.assertEqual(g.disp()['title'], 'Ethernet settings')
        self.assertEqual([r.split('  ')[0] for r in g.widgets()['f_eth_w']['rows']], ['IP Configuration'])
        g.field('f_eth_w', 'IP Configuration')
        g.ov('Static')
        self.assertEqual(g.setting('ip_mode'), 'static')
        self.assertEqual([r.split('  ')[0] for r in g.widgets()['f_eth_w']['rows']], ['IP Configuration', 'IP address', 'Gateway', 'Subnet mask', 'DNS'])
        for label, value in (('IP address', '192.168.1.50'), ('Gateway', '192.168.1.1'), ('Subnet mask', '255.255.255.0'), ('DNS', '8.8.8.8')):
            g.field('f_eth_w', label)
            g.type(value)
        s = g.val('system.settings')
        self.assertEqual((s['ip_address'], s['gateway'], s['subnet'], s['dns']), ('192.168.1.50', '192.168.1.1', '255.255.255.0', '8.8.8.8'))
        self.assertEqual(g.text('et_v2'), '192.168.1.50')
        self.assertEqual(g.text('et_v1'), 'Disconnected')                         # no network in the simulation
        g.field('f_eth_w', 'IP Configuration')
        g.ov('DHCP')
        self.assertEqual(g.setting('ip_mode'), 'dhcp')
        self.assertEqual(g.text('et_v2'), 'error')                                 # no DHCP lease
        self.assertEqual(g.setting('ip_address'), '192.168.1.50')                  # the static address is kept
        DONE_TASKS.add('scpi over tcp and static ip')

    def test_factory_reset(self):
        g = Rig()
        g.go('settings_menu', 'set_display', 'ds_b25', 'ds_v100', 'display_back', 'set_privacy', 'pv_toggle', 'privacy_back')
        self.assertEqual((g.setting('brightness'), g.setting('volume'), g.setting('usage_statistics')), (25, 100, True))
        g.go('set_reset', 'rs_reset')
        self.assertEqual(g.overlay()['title'], 'Reset')
        g.ov('Cancel')
        self.assertEqual(g.setting('brightness'), 25)
        g.go('rs_reset')
        g.ov('Reset')                                                             # confirm
        self.assertEqual(g.val('system.settings'), rt.SETTINGS_DEFAULT)
        self.assertIsNone(g.overlay())
        g.go('reset_back', 'set_about')
        self.assertIn('M91', g.text('ab_model'))                                   # calibration data are kept
        self.assertIn('23.0', g.text('ab_temp'))
        DONE_TASKS.add('factory reset')

    def test_firmware_update(self):
        g = Rig()
        g.go('settings_menu', 'set_update')
        before = g.h.read('evaluator')
        self.assertFalse(g.tap('up_usb'))                                          # unsupported: no USB-C drive, the device message
        ov = g.overlay()
        self.assertEqual(ov['title'], 'Update')
        self.assertIn('No update package found', ' '.join(ov['lines']))
        self.assertIsNotNone(g.frame()['ui']['unsupported'])
        g.ov('OK')
        self.assertEqual(g.h.read('evaluator'), before)                           # nothing installed
        g.go('up_notify')
        self.assertFalse(g.setting('notify_updates'))
        self.assertIn('No updates available', g.text('up_state'))
        DONE_TASKS.add('firmware update')

    def test_dc_hall_waiting(self):
        g = Rig()
        g.prepare()
        g.press('power_button')
        g.press('power_button')                                                   # SLEEP / ON restarts the warm-up
        g.step(rt.WARMUP_S)
        measure(g)
        self.assertIn('Complete', g.text('card_dchall'))
        g.call(g.E, 'field.set_target', field_t=-0.5)                             # the host reverses the external field for the second half
        g.step(0.05)
        self.assertEqual(g.text('card_dchall'), 'DC Hall\nWaiting (field changing)')
        g.step(30.0)
        self.assertIn('Complete', g.text('card_dchall'))
        measure(g)
        self.assertIn('Complete  9 readings', g.text('card_dchall'))
        DONE_TASKS.add('dc hall waiting')


class SettingsTabs(unittest.TestCase):
    def test_about_and_legal(self):
        g = Rig()
        g.go('settings_menu', 'set_about')
        for wid, value in (('ab_model', 'M91'), ('ab_product', 'FastHall'), ('ab_firmware', rt.ABOUT['firmware']), ('ab_serial', rt.ABOUT['serial'])):
            self.assertIn(value, g.text(wid))
        g.go('about_back', 'set_legal')
        self.assertIn('Communication certifications', g.text('lg_1'))

    def test_display_and_sound(self):
        g = Rig()
        g.go('settings_menu', 'set_display')
        self.assertTrue(g.widgets()['ds_b75']['selected'])                         # Suggested (default)
        for wid, level in (('ds_b100', 100), ('ds_b0', 0), ('ds_b50', 50)):
            g.go(wid)
            self.assertEqual(g.setting('brightness'), level)
            self.assertEqual(g.disp()['brightness'], level)
        g.go('ds_vOff' if 'ds_vOff' in g.widgets() else 'ds_v0')
        self.assertEqual(g.setting('volume'), 0)
        self.assertTrue(g.widgets()['ds_v0']['selected'])

    def test_date_time_and_zone(self):
        g = Rig()
        g.go('settings_menu', 'set_datetime')
        self.assertIn('2026-01-01 00:00', g.text('dt_now'))
        for label, value in (('Year', '2027'), ('Month', '6'), ('Day', '15'), ('Hour', '9'), ('Minute', '45')):
            g.field('f_clock_w', label)
            g.type(value)
        self.assertEqual(g.val('system.datetime'), '2026-01-01 00:00')            # entered, not yet set
        g.go('dt_set')
        self.assertEqual(g.val('system.datetime'), '2027-06-15 09:45')
        g.step(120.0)
        self.assertEqual(g.val('system.datetime'), '2027-06-15 09:47')
        g.field('f_tz_w', 'Time zone')
        g.ov('UTC+02:00')
        self.assertEqual(g.setting('timezone_min'), 120)
        self.assertEqual(g.val('system.datetime'), '2027-06-15 11:47')
        g.go('dt_auto')
        self.assertTrue(g.setting('auto_datetime'))
        g.go('datetime_back', 'settings_back')
        self.assertEqual(g.text('bar_time'), '11:47')


class MeasurementCards(unittest.TestCase):
    """The home cards and result screens of the measurement types started over SCPI (contact check / optimization, resistivity, FastHall, four
    wire): running status, high-level result, Setup / Warnings / Results equal to the runtime's measurement.result."""

    def test_contact_check_and_optimization(self):
        g = Rig()
        g.prepare()
        g.tap('card_dchall')                                                      # wake the display
        res = run(g, 'contact_check.manual', **rt.CC_MANUAL)
        self.assertEqual(g.text('card_optimization'), 'Optimization\nParameters input manually')   # figure 3-8
        self.assertEqual(g.text('card_contact'), 'Contact Check\nPass\nComplete')
        g.go('card_contact')
        for i, pair in enumerate(res['summary']['pairs']):
            self.assertEqual(g.text(f'cc_tile{i}'), f"Pass\n{pair['pair'][0]}-{pair['pair'][1]}\nR²\n{pair['r_squared']:.6f}")
        info = lines(g, 'cc_lines')
        self.assertEqual((info['Excitation start value'], info['Excitation end value'], info['Number of samples']), ('10.0000 µA', '-10.0000 µA', '11'))
        self.assertEqual(info['Pair 1-2 slope'], rt.si(res['summary']['pairs'][0]['slope_ohm'], 'Ω'))
        g.go('r_cc_back')
        g.call(g.A, 'contact_check.auto', **AUTO)
        g.step(0.3)
        self.assertRegex(g.text('card_contact'), r'Contact Check\nRunning  \d+ %')     # the progress wheel
        g.step(2.0)
        self.assertEqual(g.text('card_optimization'), 'Optimization\nExcitation: 1.0000 mA\nType: Standard (Current)')   # figure 3-7
        g.go('card_optimization')
        self.assertEqual(g.page()[1], 'r_cc')
        self.assertEqual(lines(g, 'cc_lines')['Optimized'], 'Yes')
        g.go('r_cc_back')
        res = run(g, 'contact_check.manual', **dict(rt.CC_MANUAL, excitation_start_a=0.1, excitation_end_a=-0.1, excitation_range_a=0.1, voltage_range_v=10.0, compliance_v=1.0))
        self.assertEqual(g.text('card_contact'), 'Contact Check\nFail\nComplete')
        g.go('card_contact')
        self.assertTrue(g.text('cc_tile0').endswith('Compliance'))
        self.assertEqual(lines(g, 'cc_lines')['Warnings'], 'Contact pair 1-2 in compliance')
        g.call(g.A, 'measurement.reset', type='contact_check')
        g.go('r_cc_back')
        self.assertEqual(g.text('card_contact'), 'Contact Check\nNot run')

    def test_resistivity_fasthall_and_four_wire(self):
        g = Rig()
        g.prepare()
        g.tap('card_dchall')
        full_session(g)
        res = g.call(g.A, 'measurement.result', type='resistivity')['summary']
        fh = g.call(g.A, 'measurement.result', type='fasthall')['summary']
        fw = g.call(g.A, 'measurement.result', type='four_wire')['summary']
        self.assertEqual(g.text('card_resistivity'), 'Resistivity\nρ average\n' + rt.si(res['resistivity_ohm_m'], 'Ω·m'))
        self.assertEqual(g.text('card_fasthall'), f"FastHall\nMobility average\n{fh['mobility_m2_v_s'] * 1e4:.1f} cm²/(V·s)")
        self.assertEqual(g.text('card_fourwire'), 'Four Wire\nResistance\n' + rt.si(fw['resistance_ohm'], 'Ω'))
        g.go('card_resistivity')
        self.assertRegex(g.disp()['title'], r'^Resistivity results \(2026-01-01 at \d\d:\d\d, \d+ ms\)$')
        info = lines(g, 'res_lines')
        self.assertEqual((info['ρ average'], info['Geometry A F value'], info['Linked'], info['Warnings']),
                         (rt.si(res['resistivity_ohm_m'], 'Ω·m'), f"{res['geometry_a']['f_value']:.6f}", 'contact check', '(none)'))
        g.go('r_res_back', 'card_fasthall')
        info = lines(g, 'fh_lines')
        self.assertEqual((info['Field'], info['Carrier type'], info['Hall coefficient']), ('500.000 mT', 'p-type (4 p / 0 n)', rt.sci(fh['hall_coefficient_m3_c'], 'm³/C')))
        self.assertEqual(info['Linked'], 'contact check + resistivity')
        g.go('r_fh_back', 'card_fourwire')
        info = lines(g, 'fw_lines')
        self.assertEqual((info['Excitation contact pair'], info['Sense contact pair'], info['Resistance']), ('1-2', '4-3', rt.si(fw['resistance_ohm'], 'Ω')))
        g.go('r_fw_back')
        run(g, 'four_wire.start', **dict(rt.FW_EXAMPLE, contacts=[1, 2, 1, 2], excitation_a=0.1, excitation_range_a=0.1, voltage_range_v=10.0, compliance_v=1.0))
        self.assertEqual(g.text('card_fourwire'), 'Four Wire\nFailed: SOURCE_COMPLIANCE')   # 0.1 A through ~21 ohm needs more than 1 V
        g.go('card_fourwire')
        self.assertEqual(lines(g, 'fw_lines')['Status'], 'Failed: SOURCE_COMPLIANCE')


class RefusalsTimeoutsAndEquivalence(unittest.TestCase):
    def test_invalid_ip_and_date_are_refused(self):
        g = Rig()
        g.go('settings_menu', 'set_connectivity', 'cn_ethernet')
        g.field('f_eth_w', 'IP Configuration')
        g.ov('Static')
        before = g.val('system.settings')
        g.field('f_eth_w', 'IP address')
        g.type('192.168.1.500')
        ov = g.overlay()
        self.assertEqual(ov['lines'], ['Invalid IP address.'])
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'INVALID_IP')
        self.assertEqual(g.val('system.settings'), before)
        g.ov('OK')
        g.go('ethernet_back', 'connectivity_back', 'set_datetime')
        g.field('f_clock_w', 'Month')
        g.type('2')
        g.field('f_clock_w', 'Day')
        g.type('30')
        t0 = g.val('system.datetime')
        self.assertFalse(g.tap('dt_set'))
        self.assertEqual(g.overlay()['lines'], ['Invalid date.'])
        self.assertEqual(g.val('system.datetime'), t0)
        g.ov('OK')
        g.field('f_clock_w', 'Month')
        g.type('13')                                                              # out of range: the keypad refuses it
        self.assertEqual(g.overlay()['kind'], 'keyboard')
        self.assertIsNotNone(g.overlay().get('error'))

    def test_sleep_and_remote_lock_refuse_touches(self):
        g = Rig()
        g.call(g.A, 'system.lock', locked=True)                                   # SYST:KLOC 1
        before = g.h.read('evaluator')
        self.assertFalse(g.tap('settings_menu'))
        self.assertEqual(g.frame()['ui']['message']['text'], 'Front panel is locked (remote SYST:KLOC)')
        self.assertEqual(g.page(), ('main', 'home'))
        self.assertTrue(g.widgets()['bar_lock'])
        self.assertEqual(g.h.read('evaluator'), before)
        g.step(3.5)
        self.assertIsNone(g.frame()['ui']['message'])                             # the message times out
        self.assertTrue(g.press('power_button'))                                  # the power button stays active
        self.assertFalse(g.val('power.enabled'))
        self.assertFalse(g.touch(400, 100))
        g.press('power_button')
        g.call(g.A, 'system.lock', locked=False)
        self.assertTrue(g.tap('settings_menu'))
        self.assertEqual(g.page()[1], 'settings')

    def test_auto_dimming(self):
        g = Rig()
        g.go('settings_menu', 'set_display')
        g.step(599.0)
        self.assertEqual(g.disp()['brightness'], 75)
        g.step(2.0)                                                               # no touch for 10 min: dimmed, back on the home screen
        self.assertEqual(g.disp()['brightness'], 18.75)
        self.assertEqual(g.page()[1], 'home')
        g.tap('settings_menu')                                                    # the first touch only wakes the display
        self.assertEqual(g.disp()['brightness'], 75)
        self.assertEqual(g.page()[1], 'home')
        g.tap('settings_menu')
        self.assertEqual(g.page()[1], 'settings')

    def test_panel_equals_direct(self):
        def script(direct):
            g = Rig()
            if direct:
                g.call(g.A, 'system.configure', section='connectivity', values={'scpi_tcp': False, 'virtual_serial': True})
                g.call(g.A, 'system.configure', section='ethernet', values={'ip_mode': 'static', 'ip_address': '10.1.2.3', 'gateway': '192.168.0.1',
                                                                           'subnet': '255.255.255.0', 'dns': '192.168.0.1'})
                g.call(g.A, 'system.configure', section='display', values={'brightness': 50, 'volume': 25})
                g.call(g.A, 'system.configure', section='privacy', values={'usage_statistics': True})
                g.call(g.A, 'system.set_clock', year=2026, month=11, day=10, hour=8, minute=30)
                g.call(g.A, 'system.configure', section='datetime', values={'auto_datetime': False, 'timezone_min': -300})
                g.call(g.A, 'power.set', enabled=False)
            else:
                g.go('settings_menu', 'set_connectivity', 'cn_scpi', 'cn_ethernet')
                g.field('f_eth_w', 'IP Configuration')
                g.ov('Static')
                g.field('f_eth_w', 'IP address')
                g.type('10.1.2.3')
                g.go('ethernet_back', 'connectivity_back', 'set_display', 'ds_b50', 'ds_v25', 'display_back', 'set_privacy', 'pv_toggle', 'privacy_back',
                     'set_datetime')
                for label, value in (('Month', '11'), ('Day', '10'), ('Hour', '8'), ('Minute', '30')):
                    g.field('f_clock_w', label)
                    g.type(value)
                g.go('dt_set')
                g.field('f_tz_w', 'Time zone')
                g.ov('UTC-05:00')
                g.press('power_button')
            g.step(5.0)
            return g.h.read('evaluator')
        a, b = script(False), script(True)
        for k in a:
            self.assertEqual(json.dumps(a[k], sort_keys=True), json.dumps(b[k], sort_keys=True), k)


class CallerRulesAndRemoteInterface(unittest.TestCase):
    """1.2.1: a touch or key press made through a session acts as that session's caller, so the power button keeps the ownership rule of the direct
    power.set (no switch-off during another agent's acquisition or measurement-type run; the device shows its "Not permitted." dialog); the M91 front
    panel starts no runs (results only), so there is no panel-started run to own.  The session's direct commands stand for SCPI over the USB virtual
    serial port: with Virtual serial port off they get REMOTE_DISABLED, while the touchscreen still works."""
    def two_agents(self):
        g = Rig()
        g.prepare()
        g.h.register_principal('agent2', 'agent')
        return g, g.h.session('agent2')

    def refused_power(self, g, b):
        before = g.h.read('evaluator')
        r = b.invoke(instance_id=INST, capability_id='panel.press', arguments=dict(input_id='power_button', duration_s=0.1), request_id='b-press')
        self.assertFalse(r['result']['accepted'])
        self.assertEqual(g.frame()['ui']['last_gesture']['rejection'], 'ACCESS_DENIED')
        self.assertEqual((g.overlay()['dialog'], g.overlay()['lines']), ('err_access_denied', ['Not permitted.']))
        r = b.invoke(instance_id=INST, capability_id='power.set', arguments=dict(enabled=False), request_id='b-power')
        self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'))
        after = g.h.read('evaluator')
        for k in ('power.enabled', 'acquisition.phase', 'measurement.type', 'measurement.status'):
            self.assertEqual(before[k], after[k], k)
        g.ov('OK')

    def test_power_button_keeps_the_acquisition_owner_rule(self):
        g, b = self.two_agents()
        op = g.call(g.A, 'acquisition.start')['operation_id']
        self.refused_power(g, b)
        r = b.invoke(instance_id=INST, capability_id='acquisition.stop', arguments={}, request_id='b-stop')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.A.poll(op)['status'], 'running')
        self.assertTrue(g.press('power_button'))                                  # the owner may switch to SLEEP: the run ends
        self.assertEqual((g.val('power.enabled'), g.A.poll(op)['status']), (False, 'cancelled'))

    def test_power_button_and_reset_keep_the_measurement_owner_rule(self):
        g, b = self.two_agents()
        op = g.call(g.A, 'four_wire.start', **rt.FW_EXAMPLE)['operation_id']
        g.step(0.05)
        self.refused_power(g, b)
        r = b.invoke(instance_id=INST, capability_id='measurement.reset', arguments=dict(type='four_wire'), request_id='b-reset')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.A.poll(op)['status'], 'running')
        self.assertEqual(g.A.cancel(op, 'a-cancel')['status'], 'succeeded')
        self.assertEqual(g.A.poll(op)['status'], 'cancelled')
        run(g, 'four_wire.start', **rt.FW_EXAMPLE)
        r = b.invoke(instance_id=INST, capability_id='measurement.reset', arguments=dict(type='four_wire'), request_id='b-reset2')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')                    # nor delete another agent's result
        self.assertEqual(g.val('measurement.status')['four_wire'], 'succeeded')
        g.call(g.E, 'measurement.reset', type='four_wire')                      # the evaluator may
        self.assertEqual(g.val('measurement.status')['four_wire'], 'not_run')

    def test_virtual_serial_port_gates_the_remote_commands(self):
        g = Rig()
        g.go('settings_menu', 'set_connectivity', 'cn_scpi')                     # SCPI over TCP off: USB still answers
        self.assertFalse(g.setting('scpi_tcp'))
        g.call(g.A, 'method.configure', **rt.METHOD_EXAMPLE)
        g.go('cn_serial')                                                         # Virtual serial port off: no remote path left
        self.assertFalse(g.setting('virtual_serial'))
        before = g.h.read('evaluator')
        for cap, args in (('method.configure', rt.METHOD_EXAMPLE), ('acquisition.start', {}),
                          ('system.configure', dict(section='connectivity', values=dict(scpi_tcp=True, virtual_serial=True)))):
            r = g.call(g.A, cap, expect='rejected', **args)
            self.assertEqual(r['error']['code'], 'REMOTE_DISABLED', cap)
        self.assertEqual(g.h.read('evaluator'), before)
        self.assertEqual(g.call(g.A, 'measurement.result', type='four_wire')['type'], 'four_wire')   # reads stay allowed (sweep 1.3.0)
        with self.assertRaisesRegex(rt.ActionRejected, 'UNKNOWN_OPERATION|ACCESS_DENIED'):
            g.A.poll('m91_01:acquisition:1')                                     # poll is a read: no REMOTE_DISABLED
        with self.assertRaisesRegex(rt.ActionRejected, 'REMOTE_DISABLED'):
            g.A.cancel('m91_01:acquisition:1', 'c-off')                          # cancel is a command
        self.assertTrue(g.press('power_button'))                                  # the front panel is local: the button and the touchscreen work
        self.assertTrue(g.press('power_button'))
        g.step(1.0)
        g.go('settings_menu', 'set_connectivity', 'cn_serial')
        self.assertTrue(g.setting('virtual_serial'))
        g.call(g.A, 'method.configure', **rt.METHOD_EXAMPLE)
        g.call(g.E, 'system.configure', section='connectivity', values=dict(scpi_tcp=True, virtual_serial=False))
        g.call(g.E, 'method.configure', **rt.METHOD_EXAMPLE)                     # evaluator / host entries are not SCPI


class InstrumentFaults(unittest.TestCase):
    """Sweep 2026-10-19 (1.3.0): the error messages of manual table 6-1 / 6.3.2 by evaluator fault.inject, shown on the home screen until OK."""
    def test_internal_communication_disables_the_output(self):
        g = Rig()
        g.prepare()
        op = g.call(g.A, 'four_wire.start', **rt.FW_EXAMPLE)['operation_id']
        g.step(0.05)
        g.call(g.E, 'fault.inject', code='internal_communication')
        self.assertEqual(g.A.poll(op)['status'], 'failed')                        # the output is disabled: the measurement fails
        self.assertEqual(g.call(g.A, 'measurement.result', type='four_wire')['error_code'], 'INTERNAL_COMMUNICATION')
        self.assertEqual(g.page(), ('main', 'home'))
        self.assertEqual(g.text('bar_fault'), 'Internal communication interrupted')
        for cap, args in (('four_wire.start', rt.FW_EXAMPLE), ('acquisition.start', {})):
            self.assertEqual(g.call(g.A, cap, expect='rejected', **args)['error']['code'], 'INTERNAL_COMMUNICATION', cap)
        g.tap('bar_name')                                                          # the first touch wakes the dimmed display (after the warm-up)
        g.tap('fault_ok')                                                          # OK closes the message, the cause stays
        self.assertNotIn('bar_fault', g.widgets())
        self.assertEqual(g.call(g.A, 'acquisition.start', expect='rejected')['error']['code'], 'INTERNAL_COMMUNICATION')
        g.press('power_button')
        g.press('power_button')                                                    # still there after SLEEP / ON: reported again
        self.assertEqual(g.text('bar_fault'), 'Internal communication interrupted')
        g.call(g.E, 'fault.clear', code='internal_communication')                 # repaired by service
        g.tap('fault_ok')
        g.step(rt.WARMUP_S)
        g.call(g.A, 'acquisition.start')
        self.assertEqual(g.val('acquisition.phase'), 'running')

    def test_invalid_calibration_is_reported_and_operation_continues(self):
        for state in ('default', 'uninitialized', 'corrupt', 'not_passed'):
            g = Rig()
            g.call(g.E, 'fault.inject', code='calibration_' + state)
            self.assertEqual(g.val('calibration.state'), state)
            text = rt.FAULTS['calibration_' + state]
            self.assertEqual(g.text('bar_fault'), text)
            g.tap('fault_ok')
            self.assertIsNone(g.val('fault.message'))
            g.prepare()
            g.call(g.A, 'acquisition.start')                                      # the default constants apply: operation is allowed
            self.assertEqual(g.val('acquisition.phase'), 'running')
            g.call(g.A, 'acquisition.stop')
            g.press('power_button')
            g.press('power_button')                                                # reported again at the next power-up
            self.assertEqual(g.text('bar_fault'), text)
            g.call(g.E, 'fault.clear', code='calibration_' + state)
            self.assertEqual(g.val('calibration.state'), 'valid')
            g.tap('fault_ok')
            g.press('power_button')
            g.press('power_button')
            self.assertNotIn('bar_fault', g.widgets())

    def test_fault_injection_is_evaluator_only(self):
        g = Rig()
        self.assertEqual(g.call(g.A, 'fault.inject', expect='rejected', code='calibration_corrupt')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(g.call(g.A, 'fault.clear', expect='rejected', code='calibration_corrupt')['error']['code'], 'ACCESS_DENIED')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.prepare()
            full_session(g)
            measure(g)
            return g
        a, b = scenario(), scenario()
        for did in rt.PANEL_SPEC['displays']:
            ia, ib = a.h.panel.render(did), b.h.panel.render(did)
            self.assertEqual(ia.dtype.name, 'uint8')
            self.assertTrue((ia == ib).all(), did)
        self.assertEqual(a.h.panel.png_bytes(D), b.h.panel.png_bytes(D))
        OUT.mkdir(parents=True, exist_ok=True)
        a.tap('card_dchall')                                                      # wake the dimmed display
        shots = {'home': a.h.panel.save_png(D, OUT / 'home.png')}
        a.go('card_dchall')
        shots['results'] = a.h.panel.save_png(D, OUT / 'results.png')
        a.go('results_back')
        for card, name in (('card_contact', 'r_cc'), ('card_resistivity', 'r_res'), ('card_fasthall', 'r_fh'), ('card_fourwire', 'r_fw')):
            a.go(card)
            shots[name] = a.h.panel.save_png(D, OUT / f'{name}.png')
            a.go(name + '_back')
        a.go('settings_menu')
        shots['menu'] = a.h.panel.save_png(D, OUT / 'menu.png')
        a.go('set_connectivity')
        shots['connectivity'] = a.h.panel.save_png(D, OUT / 'connectivity.png')
        a.go('cn_ethernet')
        a.field('f_eth_w', 'IP Configuration')
        a.ov('Static')
        shots['ethernet'] = a.h.panel.save_png(D, OUT / 'ethernet.png')
        a.field('f_eth_w', 'Gateway')
        a.type('10.0.0.256')
        shots['error'] = a.h.panel.save_png(D, OUT / 'error.png')
        a.ov('OK')
        a.go('ethernet_back', 'connectivity_back', 'set_display')
        shots['display'] = a.h.panel.save_png(D, OUT / 'display.png')
        a.go('display_back', 'set_reset', 'rs_reset')
        shots['reset'] = a.h.panel.save_png(D, OUT / 'reset.png')
        for v in shots.values():
            self.assertEqual(v['sha256'], hashlib.sha256(Path(v['path']).read_bytes()).hexdigest())
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig(on=False)
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id=D)
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        self.assertEqual((r['image']['width'], r['image']['height']), (800, 480))
        g.press('power_button')
        r2 = g.call(g.A, 'panel.capture', display_id=D)
        self.assertNotEqual(r['image']['sha256'], r2['image']['sha256'])

    def test_copy_keeps_the_panel_bound(self):
        g = Rig()
        c = copy.deepcopy(g.h)
        self.assertIs(c.panel.host, c)
        x, y, w, h = {e['id']: e for e in c.panel.frame()['displays'][D]['widgets']}['settings_menu']['rect']
        c.panel.press('power_button')
        self.assertFalse(c.power)
        self.assertTrue(g.h.power)

    def test_spec_and_contract(self):
        self.assertEqual(pu.check_spec(rt.PANEL_SPEC, set(rt.CAPS)), [])
        self.assertEqual(set(rt.PANEL_CAPS), {'panel.press', 'panel.touch', 'panel.capture', 'panel.frame'})
        c = json.loads((rt.ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        m = rt.export_lakeshore_measurereadym91_contract_metadata()
        for k in list(rt.PANEL_CAPS) + sorted(rt.PANEL_DEVICE) + sorted(rt.MEAS_STARTS) + ['measurement.result', 'measurement.reset']:
            for key in ('input_schema', 'output_schema', 'kind', 'visibility', 'component_ids'):
                self.assertEqual(c['capabilities'][k][key], m['capabilities'][k][key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class'], ext['spec_version']), ('panel_ui', 'touchscreen_gui', pu.SPEC_VERSION))
        for aid, path in (('panel_ui', rt.ASSET / 'panel_ui.json'), ('runtime', ROOT / 'scripts/asset_runtime/lakeshore_measurereadym91.py')):
            self.assertEqual(c['artifacts'][aid]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), aid)
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])
        spec = importlib.util.spec_from_file_location('m91_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
