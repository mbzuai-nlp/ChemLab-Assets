"""front-panel tests for CHEM-176 azenta_heat_sealer (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_automation.py):
python3 -m pytest tests/test_panel_ui_azenta_automated_roll_heat_sealer_a4s.py -q

Every gesture goes through the session API (panel.touch / panel.switch).  Checks look at the runtime state and at the screen: the structured frame
(panel.frame, widgets and pop-ups with their rects) and the rendered pixels (determinism, the PNGs of outputs/panel_ui/azenta_automated_roll_heat_
sealer_a4s/).  Themes: the eight key procedures of the menu survey (time, temperature, seal a plate, create / select a protocol, unload / load the
roll, power saving) and the other screen functions (security levels and passwords, Administrator page, delete, close door with a cut seal,
warnings); panel = direct capability; the device functions behind the screen (seal cycle with press and carrier, seal count, power saving cooling
and wake, red lock); refusals; time-outs (software version 3 s, loading complete 3 s); render determinism; contract entries."""
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_automation as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'ahs_panel_01'
C0 = 273.15
OUT = ROOT / 'outputs/panel_ui/azenta_automated_roll_heat_sealer_a4s'
ASSET = rt.AZENTA_HEAT_SEALER_ASSET_DIR
SPEC = rt._ahs_panel()['spec']
SURVEY_TASKS = ['Set sealing time', 'Set sealing temperature', 'Seal a plate', 'Create a protocol', 'Select a stored protocol', 'Unload the seal roll',
                'Load a seal roll', 'Change power-saving settings']
DONE_TASKS = set()


class Rig:
    def __init__(self, film=True, scene=True, rate=10.0):
        self.h = rt.AzentaHeatSealerRuntime()
        self.scene = []
        self.h.create_instance(INST, scene_adapter=(lambda i, t: self.scene.append(t) or True) if scene else None)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        if film:
            self.h.host_load_film(INST, roll_id='roll_1', width_m=.08, remaining_seals=50, film_roll_position_id='rear_support', compatibility_evidence='synthetic test roll')
        self.switch('on')
        self.h.host_set_temperature(INST, temperature_K=293.15, evidence='synthetic room temperature')
        self.h.host_configure_thermal_rate(INST, thermal_rate_K_s=rate, evidence='synthetic linear rate (test assumption)')

    def call(self, sess, cap, expect=('succeeded', 'accepted'), **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_switch', position=pos)['accepted']

    def touch(self, x, y, d=0.1, to=None):
        extra = dict(to_x=to[0], to_y=to[1]) if to else {}
        return self.call(self.A, 'panel.touch', display_id='screen', x=x, y=y, duration_s=d, **extra)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays']['screen']

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def tap(self, wid, d=0.1):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2, d)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label, d=0.1):
        ov = self.overlay()
        assert ov is not None, 'no pop-up'
        for b in ov['buttons']:
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2, d)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def type(self, text):
        """Type on the pop-up keyboard: the field's text is selected on opening, so the first key replaces it; pages switched as needed."""
        for ch in text:
            ov = self.overlay()
            labels = [b['label'] for b in ov['buttons']]
            if ch not in labels:
                mode = ov.get('mode')
                if ch.isalpha() and mode == 'sym':
                    self.ov('abc')
                elif not ch.isalpha() and mode in ('lower', 'upper'):
                    self.ov('12@')
                ov = self.overlay()
                if ch.isalpha() and (ch.isupper() != (ov.get('mode') == 'upper')):
                    self.ov('Shift')
            self.ov(ch)
        self.ov('OK')

    def row(self, wid, first):
        """Tap the row whose first cell is this text, dragging the list up until it shows."""
        for _ in range(10):
            w = self.widgets()[wid]
            for i, cells in enumerate(w['cells']):
                if cells[0] == first:
                    x, y, ww, _ = w['rect']
                    return self.touch(x + ww // 2, y + i * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            self.touch(x + ww // 2, y + hh - 5, to=(x + ww // 2, y + 5))
        raise AssertionError(f'{first!r} not in {[c[0] for c in w["cells"]]}')

    def lines(self):
        ov = self.overlay()
        return ov.get('lines') if ov else None

    def page(self):
        return self.frame()['ui']['page']

    def screen(self):
        return self.frame()['screen']

    def step(self, s):
        while s > 1e-12:
            dt = min(s, 3600.0)
            self.h.host_step(dt, world_time_s=self.h.time_s + dt)
            s -= dt

    def state(self, *ids):
        o = self.E.read(instance_id=INST, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def plate(self, height=.02):
        self.h.host_load_plate(INST, plate_id='pcr_plate', height_m=height, plate_adapter_id='pcr_adapter', compatibility_evidence='synthetic PCR plate in adapter')

    def set_temperature(self, celsius):
        """Temperature icon, arrows, Yes."""
        self.tap('temp')
        cur = self.frame()['ui']['forms']['f_temp']['temp']
        diff = celsius - cur
        while abs(diff) >= 5:                                                            # held: 5 C steps
            self.ov('Up' if diff > 0 else 'Down', d=0.5)
            diff = celsius - self.frame()['ui']['forms']['f_temp']['temp']
        while diff:
            self.ov('Up' if diff > 0 else 'Down')
            diff = celsius - self.frame()['ui']['forms']['f_temp']['temp']
        self.ov('Yes')


def runtime_state(h):
    s = h.debug_snapshot()['instances'][INST]
    return json.dumps(dict(state=s['state'], protocols=s['protocols'], idle=s['idle_s']), sort_keys=True, default=float)


# ---------------------------------------------------------------- the survey's key procedures
class KeyProcedures(unittest.TestCase):
    def test_set_sealing_time(self):
        g = Rig()
        g.tap('time')
        self.assertEqual(g.lines(), ['3.0 Sec.'])
        g.ov('Up')
        g.ov('Up', d=0.5)                                                                 # held: 0.5 s steps
        self.assertEqual(g.lines(), ['3.6 Sec.'])
        g.ov('Esc')                                                                       # Esc reverts
        self.assertIsNone(g.state('configuration.press_time_s'))
        g.tap('time')
        g.ov('Down')
        g.ov('Yes')
        self.assertEqual(g.state('configuration.press_time_s'), 2.9)
        self.assertEqual(g.widgets()['time']['text'], '2.9sec')
        DONE_TASKS.add('Set sealing time')

    def test_set_sealing_temperature(self):
        g = Rig()
        g.set_temperature(172)
        self.assertEqual(g.state('thermal.setpoint_k'), 445.15)
        self.assertEqual(g.widgets()['temp']['text'], '172°C')
        self.assertEqual(g.widgets()['status']['text'], 'Status:Heating')
        self.assertEqual(g.widgets()['status']['color'], 'red')
        g.step(20.0)
        self.assertEqual((g.widgets()['status']['text'], g.widgets()['status']['color']), ('Status:Ready', 'green'))
        g.tap('temp')
        for _ in range(30):                                                              # the window stops at 195 C
            g.ov('Up', d=0.5)
        self.assertEqual(g.lines(), ['195 °C'])
        g.ov('Esc')
        DONE_TASKS.add('Set sealing temperature')

    def test_seal_a_plate(self):
        g = Rig()
        g.plate()
        g.tap('time')
        g.ov('Yes')                                                                       # 3.0 s
        g.set_temperature(175)
        self.assertNotEqual(g.widgets()['seal'].get('style'), 'primary')                 # grey while heating
        self.assertFalse(g.tap('seal'))
        g.step(30.0)
        self.assertEqual(g.widgets()['seal'].get('style'), 'primary')                     # green when ready
        self.assertTrue(g.tap('seal'))
        self.assertEqual((g.state('sealing.status'), g.state('carrier.position')), ('running', 'in'))
        self.assertAlmostEqual(g.state('scene.press_displacement_m'), .057)
        self.assertEqual(g.widgets()['seal']['text'], 'Sealing')
        g.step(3.0)
        self.assertEqual(g.state('sealing.status', 'plate.simulated_sealed', 'carrier.position', 'scene.press_displacement_m', 'device.seal_count', 'film.remaining_seals'),
                         {'sealing.status': 'succeeded', 'plate.simulated_sealed': True, 'carrier.position': 'out', 'scene.press_displacement_m': 0.0,
                          'device.seal_count': 1, 'film.remaining_seals': 49})
        self.assertEqual([round(t['scene.press_displacement_m'], 9) for t in g.scene], [.057, 0.0])
        self.assertEqual(g.widgets()['count']['text'], 'Seal count:1')
        DONE_TASKS.add('Seal a plate')

    def test_create_a_protocol(self):
        g = Rig()
        g.tap('protocol')
        self.assertEqual(g.page(), 'protocols')
        self.assertEqual(g.widgets()['list']['cells'][0], ['4ti-0520', '175', '3.0', 'Peel Seal'])
        g.tap('new')
        g.row('f_proto_rows', 'Name')
        g.type('PCR96')
        g.row('f_proto_rows', 'Temperature (°C)')
        g.type('168')
        g.row('f_proto_rows', 'Time (s)')
        g.type('2.5')
        g.row('f_proto_rows', 'Information')
        g.type('Clear')
        g.tap('save')
        self.assertEqual(g.overlay()['title'], 'Security level')
        g.ov('Enter')                                                                     # no lock
        self.assertEqual(g.page(), 'protocols')
        p = g.state('protocol.list')[-1]
        self.assertEqual(p, {'name': 'PCR96', 'temperature_K': 441.15, 'press_time_s': 2.5, 'information': 'Clear', 'security': 'none'})
        g.tap('select')
        self.assertEqual((g.page(), g.state('protocol.current'), g.state('thermal.setpoint_k'), g.state('configuration.press_time_s')), ('main', 'PCR96', 441.15, 2.5))
        self.assertEqual(g.widgets()['current']['text'], 'Current Protocol:\nPCR96')
        DONE_TASKS.add('Create a protocol')

    def test_select_a_stored_protocol(self):
        g = Rig()
        g.tap('protocol')
        g.row('list', '4ti-0573')
        g.tap('select')
        self.assertEqual((g.state('protocol.current'), g.state('thermal.setpoint_k'), g.state('configuration.press_time_s')), ('4ti-0573', 443.15, 3.0))
        self.assertEqual((g.widgets()['temp']['text'], g.widgets()['time']['text']), ('170°C', '3.0sec'))
        DONE_TASKS.add('Select a stored protocol')

    def test_unload_the_seal_roll(self):
        g = Rig()
        g.tap('carrier')
        self.assertEqual(g.overlay()['lines'][0], 'To proceed, remove the plate')
        self.assertEqual([b['label'] for b in g.overlay()['buttons']], ['Close door', 'Chg roll', 'Esc'])
        g.ov('Chg roll')
        self.assertTrue(g.state('film.released'))
        self.assertEqual(g.widgets()['release_box']['text'], 'It is now safe to remove the roll.')
        g.h.host_remove_film(INST)                                                       # the operator rotates the roll out and takes it off
        self.assertEqual(g.widgets()['release_box']['text'], 'Please reboot the unit.')
        self.assertIsNone(g.state('film.roll_id'))
        DONE_TASKS.add('Unload the seal roll')

    def test_load_a_seal_roll(self):
        g = Rig(film=False)
        self.assertEqual((g.screen(), g.widgets()['l1_box']['text']), ('loading', 'No seal detected. Press OK to perform the Seal Loading Process.'))
        g.tap('l1_ok')
        g.tap('l2_ok')
        self.assertEqual(g.state('carrier.position'), 'loading')
        self.assertFalse(g.tap('l3_ok'))                                                 # the seal is not threaded yet
        g.h.host_load_film(INST, roll_id='roll_2', width_m=.08, remaining_seals=5000, film_roll_position_id='rear_support', compatibility_evidence='synthetic')
        self.assertTrue(g.tap('l3_ok'))
        self.assertEqual(g.widgets()['l4_box']['text'], 'Please confirm the seal loading tool has been removed from the unit!')
        g.tap('l4_ok')
        self.assertEqual(g.state('carrier.position'), 'out')
        g.tap('l5_ok')
        self.assertEqual((g.screen(), g.widgets()['done']['text']), ('loaded', 'Seal loading is now complete.'))
        g.step(3.0)
        self.assertEqual((g.screen(), g.page()), ('ui', 'main'))
        DONE_TASKS.add('Load a seal roll')

    def test_change_power_saving_settings(self):
        g = Rig()
        self.assertEqual(g.widgets()['psave']['text'], 'Power saving ON')
        g.tap('psave', d=5.0)                                                             # held over 5 s: the settings
        self.assertEqual(g.lines(), ['1.0 Hr.   100 °C'])
        for _ in range(3):
            g.ov('Hr. +')
        g.ov('°C -')
        g.ov('Yes')
        self.assertEqual(g.state('power_saving.idle_time_s', 'power_saving.hold_temperature_k'),
                         {'power_saving.idle_time_s': 9000.0, 'power_saving.hold_temperature_k': 323.15})
        g.tap('psave')                                                                    # a tap turns it off
        self.assertFalse(g.state('power_saving.enabled'))
        self.assertEqual(g.widgets()['psave']['text'], 'Power saving OFF')
        DONE_TASKS.add('Change power-saving settings')


# ---------------------------------------------------------------- other screen functions
class OtherScreenFunctions(unittest.TestCase):
    def protocol(self, g, name, level, pw):
        g.tap('protocol')
        g.tap('new')
        g.row('f_proto_rows', 'Name')
        g.type(name)
        g.tap('save')
        x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:level'][0]['rect']
        g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
        g.ov({'none': 'No password', 'low': 'Black lock', 'high': 'Red lock'}[level])
        if pw:
            x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:pw'][0]['rect']
            g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
            g.type(pw)
        g.ov('Enter')

    def test_security_levels_red_lock_and_delete(self):
        g = Rig()
        self.protocol(g, 'RUN1', 'high', '1234')
        self.assertEqual(g.state('protocol.list')[-1]['security'], 'high')
        g.row('list', 'RUN1')
        g.tap('select')
        self.assertEqual(g.overlay()['title'], 'Password?')
        x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:pw'][0]['rect']
        g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
        g.type('9999')
        g.ov('Enter')
        self.assertEqual(g.overlay()['lines'], ['Wrong password.'])
        g.ov('OK')
        self.assertIsNone(g.state('protocol.current'))
        g.tap('select')
        x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:pw'][0]['rect']
        g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
        g.type('1234')
        g.ov('Enter')
        self.assertEqual((g.page(), g.state('protocol.current')), ('main', 'RUN1'))
        before = g.state('thermal.setpoint_k')
        g.tap('temp')                                                                     # red lock: password before any change
        self.assertEqual(g.overlay()['title'], 'Password?')
        g.ov('Esc')
        g.tap('protocol')
        self.assertEqual((g.page(), g.overlay()['title']), ('main', 'Password?'))
        x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:pw'][0]['rect']
        g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
        g.type('1234')
        g.ov('Enter')
        self.assertTrue(g.state('protocol.unlocked'))
        g.set_temperature(180)
        self.assertNotEqual(g.state('thermal.setpoint_k'), before)
        g.tap('protocol')
        g.row('list', 'RUN1')
        g.tap('delete')
        g.ov('OK')
        x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:pw'][0]['rect']
        g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
        g.type('1234')
        g.ov('Enter')
        self.assertNotIn('RUN1', [p['name'] for p in g.state('protocol.list')])
        self.assertIsNone(g.state('protocol.current'))

    def test_black_lock_edit_needs_password_and_administrator(self):
        g = Rig()
        self.protocol(g, 'LOW1', 'low', '77')
        g.row('list', 'LOW1')
        g.tap('select')                                                                    # black lock: selecting is free
        self.assertEqual(g.state('protocol.current'), 'LOW1')
        g.tap('protocol')
        g.row('list', 'LOW1')
        g.tap('edit')
        x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:pw'][0]['rect']
        g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
        g.type('77')
        g.ov('Enter')
        self.assertEqual(g.page(), 'edit')
        g.row('f_proto_rows', 'Time (s)')
        g.type('4.2')
        g.tap('save')
        x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:level'][0]['rect']
        g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
        g.ov('Black lock')
        x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:pw'][0]['rect']
        g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
        g.type('77')
        g.ov('Enter')
        self.assertEqual([p['press_time_s'] for p in g.state('protocol.list') if p['name'] == 'LOW1'], [4.2])
        g.tap('admin')                                                                      # Administrator: default 8888
        x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:pw'][0]['rect']
        g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
        g.type('8888')
        g.ov('Enter')
        self.assertEqual(g.page(), 'admin')
        g.row('admin_list', 'LOW1')
        g.tap('a_pw')
        x = [o for o in g.overlay()['buttons'] if o['id'] == 'row:pw'][0]['rect']
        g.touch(x[0] + x[2] // 2, x[1] + x[3] // 2)
        g.type('55')
        g.ov('Enter')
        self.assertEqual(g.h.debug_snapshot()['instances'][INST]['protocols'][-1]['password'], '55')
        g.tap('a_delete')
        self.assertNotIn('LOW1', [p['name'] for p in g.state('protocol.list')])

    def test_save_warnings(self):
        g = Rig()
        g.tap('protocol')
        g.tap('new')
        g.tap('save')
        self.assertEqual(g.overlay()['lines'], ['No protocol name before', 'saving protocol.'])
        g.ov('OK')
        g.row('f_proto_rows', 'Name')
        g.type('4ti')
        g.row('f_proto_rows', 'Time (s)')
        g.type('12')
        g.tap('save')
        self.assertEqual(g.overlay()['lines'], ['The time value must be', 'between 0.1 and 10 seconds.'])
        g.ov('OK')
        g.row('f_proto_rows', 'Time (s)')
        g.type('2')
        g.row('f_proto_rows', 'Temperature (°C)')
        g.type('99')
        g.tap('save')
        self.assertEqual(g.overlay()['lines'], ['Temperature value must be', 'between 100 and 200°C.'])
        g.ov('OK')
        g.row('f_proto_rows', 'Temperature (°C)')
        g.type('170')
        g.row('f_proto_rows', 'Name')
        g.type('4ti-0520')
        g.tap('save')
        self.assertEqual(g.overlay()['lines'], ['This name already exists,', 'please change name and save.'])

    def test_close_door_cut_seal_and_warnings(self):
        g = Rig()
        g.tap('carrier')
        g.ov('Close door')
        self.assertEqual(g.state('carrier.position', 'carrier.cut_seal_present', 'film.remaining_seals'),
                         {'carrier.position': 'in', 'carrier.cut_seal_present': True, 'film.remaining_seals': 49})
        g.tap('carrier')
        self.assertEqual(g.overlay()['lines'], ['Check for and remove any', 'cut seal before continuing.'])
        g.ov('OK')
        self.assertEqual(g.state('carrier.position', 'carrier.cut_seal_present'), {'carrier.position': 'out', 'carrier.cut_seal_present': False})
        g.plate()
        g.tap('carrier')
        g.ov('Close door')                                                                  # with a plate on the carrier: the options stay
        self.assertEqual((g.state('carrier.position'), g.overlay()['buttons'][0]['label']), ('out', 'Close door'))
        g.ov('Esc')
        g.set_temperature(175)
        g.step(30)
        g.h.host_set_inspection_door(INST, closed=False)
        g.tap('seal')
        self.assertEqual(g.overlay()['lines'], ['Please close the inspection', 'door on the side of the unit.'])
        g.ov('OK')
        g.h.host_set_inspection_door(INST, closed=True)
        g.h.host_set_film_remaining(INST, remaining_seals=0)
        g.tap('seal')
        self.assertEqual(g.overlay()['buttons'][1]['label'], 'Continue')
        g.ov('OK')
        self.assertIn('Seal film has run out', g.widgets()['out_box']['text'])

    def test_seal_incomplete_and_safety_fault(self):
        g = Rig()
        g.set_temperature(175)
        g.step(30)
        g.tap('seal')
        self.assertEqual(g.overlay()['lines'][0], 'Seal incomplete. Refer to')
        g.ov('OK')
        g.plate()
        g.tap('time')
        g.ov('Yes')
        g.tap('seal')
        g.step(1.0)
        g.h.host_set_inspection_door(INST, closed=False)                                   # door opened during the run
        self.assertEqual(g.state('sealing.status'), 'failed')
        self.assertEqual(g.screen(), 'fault')
        self.assertFalse(g.touch(240, 160))
        g.h.host_set_inspection_door(INST, closed=True)
        g.switch('off')
        g.switch('on')
        self.assertEqual(g.screen(), 'ui')

    def test_power_saving_cools_and_a_touch_wakes(self):
        g = Rig()
        g.set_temperature(175)
        g.step(30)
        g.call(g.A, 'power_saving.configure', enabled=True, idle_time_s=1800.0, hold_temperature_K=None)
        g.step(1800.0)
        self.assertTrue(g.state('power_saving.active'))
        self.assertEqual(g.widgets()['status']['text'], 'Status:Power saving')
        g.step(60.0)
        self.assertLess(g.state('measurement.temperature_k'), 448.15)                       # heater off: cooling toward the room
        self.assertEqual(g.state('thermal.setpoint_k'), 448.15)
        self.assertTrue(g.tap('temp'))                                                        # the first touch only wakes it
        self.assertIsNone(g.overlay())
        self.assertFalse(g.state('power_saving.active'))
        g.step(60.0)
        self.assertEqual(g.state('measurement.temperature_k'), 448.15)

    def test_software_version_for_three_seconds(self):
        g = Rig()
        self.assertIn('version', g.widgets())
        g.step(3.0)
        self.assertNotIn('version', g.widgets())


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        a, b = Rig(), Rig()
        for g in (a, b):
            g.plate()
        a.tap('time')
        a.ov('Down')
        a.ov('Yes')
        a.set_temperature(170)
        a.tap('protocol')
        a.row('list', '4ti-0580')
        a.tap('select')
        a.tap('psave')
        b.call(b.A, 'sealing.set_time', press_time_s=2.9)
        b.call(b.A, 'thermal.set_temperature', temperature_K=443.15)
        b.call(b.A, 'protocol.select', name='4ti-0580', password='')
        b.call(b.A, 'power_saving.configure', enabled=False, idle_time_s=3600.0, hold_temperature_K=373.15)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        for g in (a, b):
            g.step(30.0)
        a.tap('seal')
        b.call(b.A, 'sealing.seal_plate')
        for g in (a, b):
            g.step(4.0)
        self.assertEqual(runtime_state(a.h), runtime_state(b.h))
        self.assertEqual(a.state('device.seal_count'), 1)


class Rules(unittest.TestCase):
    def test_power_off_and_refusals(self):
        g = Rig()
        g.switch('off')
        self.assertEqual(g.screen(), 'off')
        self.assertFalse(g.touch(240, 160))
        g.switch('on')
        r = g.call(g.A, 'sealing.seal_plate', expect=('rejected',))
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')
        r = g.call(g.A, 'protocol.select', expect=('rejected',), name='nope', password='')
        self.assertEqual(r['error']['code'], 'PRECONDITION_FAILED')
        r = g.call(g.A, 'power_saving.configure', expect=('rejected',), enabled=True, idle_time_s=2000.0, hold_temperature_K=None)
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        r = g.call(g.A, 'panel.frame', expect=('rejected',))
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        caps = g.A.describe(instance_id=INST)['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.touch', 'panel.switch', 'panel.capture'})
        self.assertIn('panel.frame', g.E.describe(instance_id=INST)['capabilities'])

    def test_no_time_out_in_pop_ups(self):
        g = Rig()
        g.tap('temp')
        g.step(600.0)
        self.assertIsNotNone(g.overlay())


def ready_to_seal(g):
    g.plate()
    g.tap('time')
    g.ov('Yes')
    g.set_temperature(175)
    g.step(30.0)


class Callers(unittest.TestCase):
    """A gesture acts as the session that makes it (sync audit 2026-10-19): a seal started with the Seal key belongs to that agent."""
    def test_panel_started_seal_belongs_to_the_gesturing_agent(self):
        g = Rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        ready_to_seal(g)
        self.assertTrue(g.tap('seal'))
        op = [k for k, v in g.h._operations.items() if v['status'] == 'running'][0]
        self.assertEqual(g.h._operations[op]['principal'], 'agent')
        self.assertEqual(g.A.poll(instance_id=INST, operation_id=op)['status'], 'running')
        before = runtime_state(g.h)
        self.assertEqual(B.poll(instance_id=INST, operation_id=op)['error']['code'], 'UNKNOWN_OPERATION')
        self.assertEqual(B.cancel(instance_id=INST, operation_id=op, request_id='b_cancel')['error']['code'], 'UNKNOWN_OPERATION')
        for wid in ('seal', 'carrier'):                                                  # B's Seal and Carrier keys during the run: ignored
            x, y, w, h = g.widgets()[wid]['rect']
            self.assertFalse(g.call(B, 'panel.touch', display_id='screen', x=x + w // 2, y=y + h // 2)['accepted'])
        self.assertEqual(runtime_state(g.h), before)
        self.assertEqual(g.A.cancel(instance_id=INST, operation_id=op, request_id='a_cancel')['status'], 'succeeded')
        self.assertEqual((g.state('sealing.status'), g.A.poll(instance_id=INST, operation_id=op)['status']), ('cancelled', 'cancelled'))

    def test_foreign_seal_changes_refused_rear_switch_is_physical(self):
        """Lead rule 2026-10-19: B cannot change A's running seal (temperature, time, protocol), on the panel or the direct path; the touch screen
        shows the refusal as a pop-up (text assumed); the owner gets the device's own BUSY.  The rear power switch is a hard mains switch: B may
        switch it off and the seal stops as at a power loss."""
        g = Rig()
        g.h.register_principal('agent_b', visibility='agent')
        B = g.h.session('agent_b')
        ready_to_seal(g)
        g.call(g.A, 'sealing.set_time', press_time_s=10.0)
        self.assertTrue(g.tap('seal'))
        op = [k for k, v in g.h._operations.items() if v['status'] == 'running'][0]
        g.step(1.0)
        before = runtime_state(g.h)
        for cap, args in (('thermal.set_temperature', dict(temperature_K=430.0)), ('sealing.set_time', dict(press_time_s=2.0)),
                          ('protocol.select', dict(name='4ti-0520', password=''))):
            self.assertEqual(g.call(B, cap, expect=('rejected',), **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(runtime_state(g.h), before)
        x, y, w, h = g.widgets()['temp']['rect']                                         # B on the touch screen: temperature window, Yes
        g.call(B, 'panel.touch', display_id='screen', x=x + w // 2, y=y + h // 2)
        b = next(b for b in g.overlay()['buttons'] if b['label'] == 'Yes')
        self.assertFalse(g.call(B, 'panel.touch', display_id='screen', x=b['rect'][0] + 5, y=b['rect'][1] + 5)['accepted'])
        self.assertEqual(runtime_state(g.h), before)
        self.assertEqual((g.overlay()['title'], g.lines()), ('Warning', ["Another user's seal is", 'in progress.']))
        g.ov('OK')
        self.assertEqual(g.call(g.A, 'thermal.set_temperature', expect=('rejected',), temperature_K=430.0)['error']['code'], 'BUSY')
        self.assertTrue(g.call(B, 'panel.switch', input_id='power_switch', position='off')['accepted'])   # physical: never refused
        self.assertEqual((g.state('power.enabled'), g.A.poll(instance_id=INST, operation_id=op)['status']), (False, 'cancelled'))

    def test_panel_request_ids_stay_out_of_the_callers_namespace(self):
        g = Rig()                                                                         # the switch-on gesture ran power.set as 'agent'
        mine = dict(instance_id=INST, capability_id='thermal.set_temperature', arguments={'temperature_K': 400.0}, request_id='__panel_2')
        r = g.A.invoke(**mine)
        self.assertEqual(r['status'], 'succeeded')
        g.set_temperature(175)                                                            # the panel's own ids skip the caller's '__panel_2'
        self.assertAlmostEqual(g.state('thermal.setpoint_k'), 175 + C0)
        self.assertEqual(g.A.invoke(**mine), r)                                           # the caller's replay is still its own


class Faults(unittest.TestCase):
    """Error 1-12 of manual p.64, injected by the evaluator (fault.inject / fault.clear)."""
    def test_injected_error_stops_the_seal_and_shows_the_error_screen(self):
        g = Rig()
        ready_to_seal(g)
        g.tap('seal')
        g.step(1.0)
        self.assertEqual(g.call(g.A, 'fault.inject', expect=('rejected',), code=3)['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('fault.inject', g.A.describe(instance_id=INST)['capabilities'])
        self.assertIn('fault.inject', g.E.describe(instance_id=INST)['capabilities'])
        self.assertEqual(g.call(g.E, 'fault.inject', code=2), {'device.error_code': 2, 'sealing.status': 'failed'})
        self.assertEqual((g.screen(), g.state('carrier.position')), ('error', 'in'))                 # stopped where it was
        w = g.widgets()
        self.assertEqual((w['error_code']['text'], w['error_text']['text'], w['error_action']['text']),
                         ('Error 2', "You touch Seal but heater can't move up", 'Contact your Azenta Life Sciences service representative.'))
        g.step(5.0)
        self.assertEqual((g.state('carrier.position'), g.state('device.seal_count')), ('in', 0))
        self.assertFalse(g.touch(400, 100))                                                         # the error screen takes no touches
        for cap, args in (('carrier.open', {}), ('sealing.seal_plate', {}), ('film.release', {})):
            self.assertEqual(g.call(g.A, cap, expect=('rejected',), **args)['error']['code'], 'PRECONDITION_FAILED', cap)
        self.assertEqual(g.call(g.E, 'fault.inject', expect=('rejected',), code=5)['error']['code'], 'PRECONDITION_FAILED')
        g.switch('off')
        g.switch('on')
        self.assertEqual((g.screen(), g.state('device.error_code')), ('error', 2))                 # a hardware fault stays through a reboot
        g.call(g.E, 'fault.clear')
        self.assertEqual((g.screen(), g.state('device.error_code')), ('ui', None))
        g.call(g.A, 'film.release')                                                                 # motions work again
        self.assertTrue(g.state('film.released'))

    def test_every_documented_code_and_error_6_action(self):
        g = Rig()
        for code, text in rt.AZENTA_HEAT_SEALER_ERRORS.items():
            g.call(g.E, 'fault.inject', code=code)
            w = g.widgets()
            self.assertEqual((w['error_code']['text'], w['error_text']['text']), (f'Error {code}', text))
            if code == 6:
                self.assertEqual(w['error_action']['text'], 'Contact your service representative.')
            g.call(g.E, 'fault.clear')
        self.assertEqual(g.call(g.E, 'fault.inject', expect=('rejected',), code=8)['error']['code'], 'INVALID_ARGUMENT')   # no Error 8 in the manual


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        def scenario():
            g = Rig()
            g.set_temperature(175)
            g.step(30.0)
            return g
        a, b = scenario(), scenario()
        pa, pb = a.h.panel(INST), b.h.panel(INST)
        self.assertTrue((pa.render('screen') == pb.render('screen')).all())
        self.assertEqual(pa.render('screen').shape, (320, 480, 3))
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': pa.save_png('screen', OUT / 'home.png')}
        a.tap('temp')
        shots['adjust'] = pa.save_png('screen', OUT / 'adjust.png')
        a.ov('Esc')
        a.tap('protocol')
        shots['menu'] = pa.save_png('screen', OUT / 'menu.png')
        a.tap('return')
        a.tap('carrier')
        shots['error'] = pa.save_png('screen', OUT / 'error.png')
        a.ov('Esc')
        a.tap('psave', d=5.0)
        shots['power_saving'] = pa.save_png('screen', OUT / 'power_saving.png')
        f = Rig()
        f.call(f.E, 'fault.inject', code=3)
        shots['fault'] = f.h.panel(INST).save_png('screen', OUT / 'fault.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        g = Rig()
        g.h.panel_capture_dir = str(OUT / 'captures')
        r = g.call(g.A, 'panel.capture', display_id='screen')
        self.assertEqual(r['image']['sha256'], hashlib.sha256(Path(r['image']['path']).read_bytes()).hexdigest())
        self.assertEqual((r['image']['width'], r['image']['height']), (480, 320))

    def test_spec_and_contract(self):
        meta = rt.export_azenta_heat_sealer_contract_metadata()
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities'])), [])
        self.assertEqual(set(meta['panel_capabilities']), {'panel.touch', 'panel.switch', 'panel.capture', 'panel.frame'})
        for sid, s in SPEC['screens'].items():
            self.assertTrue(s.get('evidence'), sid)
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
            for comp in c['capabilities'][k]['component_ids']:
                self.assertIn(k, c['components'][comp]['capability_ids'])
        for sid in meta['state_slots']:
            self.assertIn(sid.lower(), c['state_slots'])
        ext = c['extensions']['chemistry_lab.panel_ui']
        self.assertEqual((ext['artifact_id'], ext['ui_class']), ('panel_ui', 'touch_gui'))
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests', 'runtime'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('ahs_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 2:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
