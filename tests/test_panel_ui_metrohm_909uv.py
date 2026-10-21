"""Front-panel tests for CHEM-046 metrohm_909uv (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/asset_runtime/metrohm_909uv.py):
python3 -m pytest tests/test_panel_ui_metrohm_909uv.py -q

Gestures only through the session API (panel.press / panel.switch); the host (evaluator) declares the plant, loads the 12 tubes and moves the
UV shield and the temperature sensor.  Themes: the six key procedures of the menu survey (set the parameters, start, cancel, lamp info, new lamp
in Lamp setup, acknowledge an error); QUIT restoring a value, out-of-range values not saved, parameters changed during digestion, contrast,
Dev. busy cooling, Lamp exhausted with a short lifetime, counters refused during the lamp's service life, mains switch gate; panel = direct
command; rendering; contract entries."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import metrohm_909uv as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'uv909_01'
OUT = ROOT / 'outputs/panel_ui/metrohm_909uv'
CONTRACT = rt.ASSET_DIR / 'asset.contract.json'


class Rig:
    def __init__(self, on=True, shield=True):
        self.h = rt.Metrohm909uvRuntime()
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('judge', 'evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.host('plant.configure', **rt.PLANT_EXAMPLE)
        self.host('sample.configure', **rt.example_sample())
        if on:
            assert self.switch('on')
            self.h.step(1)
        if shield:
            self.call(self.A, 'shield.set_open', open=False)
            self.h.step(2)

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] in ((expect, 'accepted') if expect == 'succeeded' else (expect,)), (cap, args, r['status'], r['error'])
        return r['result'] if expect == 'succeeded' else r

    def host(self, cap, **args):
        return self.call(self.E, cap, **args)

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def lines(self):
        return [ln if isinstance(ln, str) else ln['text'] for ln in self.frame()['displays']['lcd']['lines']]

    def screen(self):
        return self.frame()['screen']

    def press(self, key, n=1, held=None):
        ok = True
        for _ in range(n):
            args = dict(input_id=key, duration_s=0.1)
            if held:
                args['held'] = held
            ok = self.call(self.A, 'panel.press', **args)['accepted'] and ok
        return ok

    def switch(self, pos, held=None):
        args = dict(input_id='mains', position=pos)
        if held:
            args['held'] = held
        return self.call(self.A, 'panel.switch', **args)['accepted']

    def dial(self, target, digits=3):
        """In the value: the digit cursor stands on the first digit; each digit is turned with up / down, right moves to the next one."""
        ed = self.frame()['ui']['editor']
        cur = str(int(round(ed['buffer'][ed['field']]))).zfill(digits)
        want = str(target).zfill(digits)
        for i, (a, b) in enumerate(zip(cur, want)):
            d = int(b) - int(a)
            self.press('up' if d > 0 else 'down', abs(d))
            if i < digits - 1:
                self.press('right')

    def set_parameters(self, temp_c, minutes):
        """SET, the line cursor on Sample Temp., [▶] into the value, digits, [OK] (next parameter), Rad. Time the same way, [OK] (leaves)."""
        assert self.press('set')
        assert self.press('right')
        self.dial(temp_c)
        ok1 = self.press('ok')
        assert self.press('right')
        self.dial(minutes)
        ok2 = self.press('ok')
        return ok1, ok2


class SurveyProcedures(unittest.TestCase):
    def test_set_sample_temperature_and_radiating_time(self):
        r = Rig()
        self.assertEqual(r.lines()[1:3], ['Curr.Temp.    25 °C', 'Set Temp.     90 °C'])
        self.assertEqual(r.set_parameters(85, 120), (True, True))
        self.assertEqual(r.screen(), 'main')
        self.assertEqual((r.val('thermal.target_k'), r.h.duration, r.val('method.configured')), (358.15, 7200.0, True))
        self.assertEqual(r.lines()[2], 'Set Temp.     85 °C')
        r.press('set')
        self.assertEqual(r.lines()[:3], ['Parameters', 'Sample Temp.  085 °C', 'Rad.Time     120 min'])
        self.assertEqual(r.frame()['displays']['lcd']['inverse'][:3], [False, True, False])
        r.press('right')
        r.press('right')
        r.press('up')                                                       # 085 -> 095 on the screen
        self.assertEqual(r.lines()[1], 'Sample Temp.  095 °C')
        r.press('quit')                                                     # [QUIT] restores the previous value
        self.assertEqual((r.lines()[1], r.val('thermal.target_k')), ('Sample Temp.  085 °C', 358.15))
        r.press('quit')                                                     # [QUIT] on the line leaves the dialog
        self.assertEqual(r.screen(), 'main')

    def test_start_a_digestion(self):
        r = Rig()
        r.set_parameters(90, 2)
        self.assertTrue(r.press('start'))
        self.assertEqual(r.val('cycle.phase'), 'running')
        self.assertEqual(r.val('lamp.ignitions'), 1)
        r.h.step(30)
        self.assertEqual(r.lines()[0], '       2 min')
        self.assertEqual(r.lines()[3], 'Heating')
        self.assertTrue(r.val('fan.running'))
        r.h.step(91)
        self.assertEqual(r.val('cycle.phase'), 'succeeded')
        self.assertEqual(r.lines()[0], '    ---- min')
        self.assertEqual(r.lines()[3], 'Lamp cooling')
        r.h.step(600)
        self.assertEqual((r.val('cycle.cooldown_s'), r.val('fan.running'), r.lines()[3]), (0.0, False, ''))

    def test_cancel_a_digestion(self):
        r = Rig()
        r.press('start')                                                    # the shown factory parameters 90 °C / 90 min
        self.assertEqual((r.val('method.configured'), r.h.duration), (True, 5400.0))
        r.h.step(60)
        self.assertTrue(r.press('start'))
        self.assertEqual((r.val('cycle.phase'), r.val('lamp.power_w')), ('cancelled', 0.0))
        self.assertEqual(r.lines()[3], 'Lamp cooling')
        self.assertFalse(r.press('start'))                                  # restart while cooling
        self.assertEqual(r.lines()[3], 'Dev. busy cooling')
        r.h.step(601)
        self.assertEqual(r.lines()[3], '')
        self.assertTrue(r.switch('off'))                                   # switched off after the cooling
        self.assertEqual(r.screen(), 'off')

    def test_check_lamp_hours_and_ignitions(self):
        r = Rig()
        r.set_parameters(90, 70)
        r.press('start')
        r.h.step(3600)
        r.h.step(100)
        r.press('ok')
        self.assertEqual(r.screen(), 'lamp_info')
        self.assertEqual(r.lines()[:2], ['Lamp info', ' >Counters'])
        r.press('ok')
        self.assertEqual(r.lines()[2:], ['  Hours          1 h', '  Ignitions        1'])
        r.press('quit')
        self.assertEqual(r.screen(), 'main')

    def test_configure_a_new_uv_lamp(self):
        r = Rig()
        r.h.lamp_usage, r.h.ignitions = 750 * 3600.0, 40                    # an exhausted lamp has been replaced by a new one
        r.call(r.A, 'shield.set_open', open=True)
        r.h.step(2)
        r.switch('off')
        self.assertTrue(r.switch('on', held=['set']))                      # hold [SET] while switching on
        self.assertEqual(r.screen(), 'lamp_setup')
        self.assertEqual(r.lines()[:3], ['Lamp setup', 'Replace after 0750 h', 'Reset Counters'])
        r.press('down')
        r.press('ok')
        self.assertEqual(r.screen(), 'reset_confirm')
        self.assertEqual(r.lines()[2], '  Are you sure?')
        self.assertTrue(r.press('ok'))
        self.assertEqual((r.val('lamp.usage_s'), r.val('lamp.ignitions'), r.screen()), (0.0, 0, 'lamp_setup'))
        r.press('up')
        r.press('right')
        r.dial(800, 4)
        self.assertTrue(r.press('ok'))
        self.assertEqual(r.val('lamp.replace_after_h'), 800)
        r.press('quit')
        self.assertEqual(r.screen(), 'main')
        r.switch('off')
        r.switch('on')
        self.assertEqual(r.screen(), 'main')                               # a normal switch-on shows the main dialog

    def test_acknowledge_an_error_message(self):
        r = Rig()
        r.press('start')
        r.h.step(20)
        r.host('sensor.set_connected', connected=False)
        self.assertEqual((r.val('cycle.phase'), r.lines()[3]), ('failed', 'No sensor connectd'))
        self.assertEqual(r.lines()[1], 'Curr.Temp.    -- °C')
        r.press('quit')                                                     # cause not removed / still cooling: stays
        self.assertEqual(r.val('cycle.phase'), 'failed')
        r.host('sensor.set_connected', connected=True)
        r.h.step(601)
        self.assertTrue(r.press('quit'))
        self.assertEqual((r.val('cycle.phase'), r.lines()[3]), ('idle', ''))


class MoreBehaviour(unittest.TestCase):
    def test_out_of_range_value_is_not_saved(self):
        r = Rig()
        r.press('set')
        r.press('right')
        r.dial(105)
        self.assertFalse(r.press('ok'))
        self.assertEqual(r.lines()[3], '80 - 100 °C')
        self.assertEqual(r.val('thermal.target_k'), 363.15)

    def test_parameters_changed_during_digestion_and_contrast(self):
        r = Rig()
        r.press('start')
        r.h.step(60)
        r.set_parameters(95, 30)
        self.assertEqual((r.val('cycle.phase'), r.val('thermal.target_k'), r.val('panel.remaining_min')), ('running', 368.15, 29))
        b0 = r.frame()['displays']['lcd'].get('brightness')
        r.press('up', 3)
        self.assertEqual(r.val('display.contrast'), 9)
        r.press('down', 20)
        self.assertEqual(r.val('display.contrast'), 1)
        self.assertNotEqual(r.frame()['displays']['lcd'].get('brightness'), b0)

    def test_lamp_exhausted_and_counters_in_service(self):
        r = Rig(shield=False)
        r.call(r.A, 'shield.set_open', open=True)
        r.h.step(2)
        r.switch('off')
        r.switch('on', held=['set'])
        r.press('right')
        r.dial(1, 4)
        r.press('ok')
        self.assertEqual(r.val('lamp.replace_after_h'), 1)
        r.press('quit')
        r.call(r.A, 'shield.set_open', open=False)
        r.h.step(2)
        r.set_parameters(90, 90)
        r.press('start')
        r.h.step(600)
        r.press('start')                                                    # stop after 10 min of radiating
        r.h.step(601)
        r.switch('off')
        r.switch('on', held=['set'])
        r.press('down')
        r.press('ok')
        self.assertFalse(r.press('ok'))
        self.assertEqual((r.screen(), r.lines()[3]), ('lamp_setup', 'Lamp in service'))
        self.assertGreater(r.val('lamp.usage_s'), 0)
        r.press('quit')
        r.press('start')
        r.h.step(3010)
        self.assertEqual((r.val('cycle.phase'), r.lines()[3]), ('failed', 'Lamp exhausted'))
        r.h.step(601)
        self.assertFalse(r.press('start'))
        self.assertEqual(r.lines()[3], 'Lamp exhausted')

    def test_mains_switch_gate_and_led(self):
        r = Rig(on=False)
        self.assertEqual((r.screen(), r.frame()['displays']['on_led']['on']), ('off', False))
        self.assertFalse(r.press('start'))
        self.assertFalse(r.val('cycle.phase') == 'running')
        r.switch('on')
        self.assertTrue(r.frame()['displays']['on_led']['on'])
        r.call(r.A, 'shield.set_open', open=False)
        r.h.step(2)
        r.press('start')
        self.assertTrue(r.switch('off'))                                   # the mains switch is a hard switch (1.2.1): a power loss
        self.assertEqual((r.val('power.enabled'), r.val('cycle.phase'), r.screen()), (False, 'cancelled', 'off'))
        self.assertFalse(r.frame()['displays']['on_led']['on'])


class PanelEqualsDirect(unittest.TestCase):
    def test_parameters_and_start_equal_commands(self):
        a, b = Rig(), Rig()
        a.set_parameters(85, 3)
        a.press('start')
        b.call(b.A, 'method.configure', temperature_K=358.15, duration_s=5400.0)
        b.call(b.A, 'method.configure', temperature_K=358.15, duration_s=180.0)
        b.call(b.A, 'digestion.toggle')
        for r in (a, b):
            r.h.step(200)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))

    def test_keys_equal_commands(self):
        a, b = Rig(on=False), Rig(on=False)
        a.switch('on')
        b.call(b.A, 'power.set', enabled=True)
        a.press('up')
        b.call(b.A, 'display.set_contrast', level=7)
        self.assertEqual(a.h.read('evaluator'), b.h.read('evaluator'))


class FaultInjection(unittest.TestCase):
    """1.2.0: the instrument defects of manual p.45-46 (evaluator fault.inject / fault.clear) drive the documented display and behaviour."""

    def test_thermo_switch_open_stops_the_run(self):
        r = Rig()
        r.set_parameters(90, 30)
        r.press('start')
        r.h.step(60)
        r.host('fault.inject', code='thermo_switch_open')
        self.assertEqual((r.val('cycle.phase'), r.val('lamp.power_w'), r.lines()[3]), ('failed', 0.0, 'Thermo switch open'))
        r.h.step(601)
        self.assertFalse(r.press('quit'))                                   # still open: cannot be acknowledged
        self.assertFalse(r.press('start'))
        self.assertEqual(r.lines()[3], 'Thermo switch open')
        self.assertEqual(r.val('cycle.phase'), 'failed')
        r.host('fault.clear', code='thermo_switch_open')                    # service repaired the instrument
        self.assertTrue(r.press('quit'))
        r.h.step(4)                                                         # the refusal message of START (3 s) has gone
        self.assertEqual((r.val('cycle.phase'), r.lines()[3]), ('idle', ''))
        self.assertTrue(r.press('start'))
        self.assertEqual(r.val('cycle.phase'), 'running')

    def test_adj_data_missing_after_switching_on(self):
        r = Rig()
        r.host('fault.inject', code='adj_data_missing')
        self.assertEqual(r.val('cycle.phase'), 'idle')                      # the checksum is checked when switching on
        r.switch('off')
        r.switch('on')
        self.assertEqual((r.val('cycle.phase'), r.lines()[3]), ('failed', 'Adj. data missing'))
        self.assertFalse(r.press('start'))
        self.assertFalse(r.press('quit'))
        r.switch('off')
        r.switch('on')
        self.assertEqual(r.lines()[3], 'Adj. data missing')                 # switching off and on does not help while the data is bad
        r.host('fault.clear', code='adj_data_missing')
        r.switch('off')
        r.switch('on')
        self.assertEqual((r.val('cycle.phase'), r.lines()[3]), ('idle', ''))
        self.assertEqual(r.val('fault.injected'), [])

    def test_check_sensor_lamp_after_no_temperature_rise(self):
        r = Rig()
        r.host('fault.inject', code='check_sensor_lamp')
        r.set_parameters(90, 30)
        self.assertTrue(r.press('start'))
        t0 = r.val('thermal.indicated_temperature_k')
        r.h.step(rt.CHECK_WINDOW_S - 2)
        self.assertEqual((r.val('cycle.phase'), r.val('lamp.power_w')), ('running', 0.0))
        self.assertLess(r.val('thermal.indicated_temperature_k') - t0, rt.CHECK_RISE_K)
        r.h.step(3)
        self.assertEqual((r.val('cycle.phase'), r.lines()[3]), ('failed', 'Check Sensor/Lamp'))
        self.assertGreater(r.val('cycle.cooldown_s'), 598.0)                # lamp off at the end of the 5 min window, fan cooling runs
        r.h.step(601)
        self.assertEqual(r.lines()[3], 'Check Sensor/Lamp')
        self.assertTrue(r.press('quit'))                                    # acknowledged; the lamp is still defective
        r.call(r.A, 'shield.set_open', open=True)
        r.h.step(2)
        r.switch('off')
        r.host('lamp.replace', replacement_confirmed=True)                  # remedy: replace the UV lamp
        self.assertEqual(r.val('fault.injected'), [])

    def test_working_lamp_passes_the_check(self):
        r = Rig()
        r.set_parameters(100, 30)
        r.press('start')
        r.h.step(1200)
        self.assertEqual(r.val('cycle.phase'), 'running')
        self.assertEqual(r.lines()[3], 'Temp. reached')


class CallerRules(unittest.TestCase):
    """Sync audit rule 2 (1.2.0): a run started with START (key or digestion.toggle) belongs to the calling session (poll / cancel with
    cycle.operation_id); another agent cannot poll, stop or change it (key or direct: state unchanged)."""

    def started(self, direct=False):
        r = Rig()
        r.h.register_principal('agent_b', 'agent')
        r.B = r.h.session('agent_b')
        r.set_parameters(90, 30)
        if direct:
            r.call(r.A, 'digestion.toggle')
        else:
            self.assertTrue(r.press('start'))
        op = r.val('cycle.operation_id')
        self.assertEqual(r.h._owners.get(op), 'agent')
        return r, op

    def test_owner_polls_and_cancels(self):
        for direct in (False, True):
            r, op = self.started(direct)
            self.assertEqual(r.A.poll(op)['status'], 'running')
            self.assertEqual(r.A.cancel(op, 'c1')['status'], 'cancelled')

    def test_other_agent_is_refused(self):
        r, op = self.started()
        r.h.step(30)
        before = r.h.read('evaluator')
        with self.assertRaises(rt.ActionRejected):
            r.B.poll(op)
        self.assertFalse(r.call(r.B, 'panel.press', input_id='start', duration_s=0.1)['accepted'])
        self.assertEqual(r.lines()[3], 'Run of other user')
        for cap, args in (('acquisition.stop', {}), ('digestion.toggle', {}), ('thermal.set_temperature', {'temperature_K': 353.15}),
                          ('method.configure', {'temperature_K': 353.15, 'duration_s': 600.0})):
            self.assertEqual(r.call(r.B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h.read('evaluator'), before)
        self.assertTrue(r.press('start'))                                   # the owner stops it with the key
        self.assertEqual(r.val('cycle.phase'), 'cancelled')


class Rendering(unittest.TestCase):
    def test_render_determinism_and_evidence(self):
        a, b = Rig(), Rig()
        for r in (a, b):
            r.set_parameters(90, 20)
            r.press('start')
            r.h.step(120)
        self.assertEqual(a.call(a.A, 'panel.capture', display_id='lcd')['image']['sha256'], b.call(b.A, 'panel.capture', display_id='lcd')['image']['sha256'])
        cap = a.call(a.A, 'panel.capture', display_id='on_led')
        self.assertEqual(validate_value(cap, rt.CAPS['panel.capture'][1]), [])
        a.h.panel.save_png('lcd', OUT / 'home.png')
        a.press('set')
        a.h.panel.save_png('lcd', OUT / 'menu.png')
        a.press('quit')
        a.press('start')
        a.press('start')
        a.h.panel.save_png('lcd', OUT / 'error.png')
        a.h.step(10)
        a.press('ok')
        a.press('ok')
        a.h.panel.save_png('lcd', OUT / 'lamp_info.png')
        a.press('quit')
        a.host('fault.inject', code='thermo_switch_open')                    # an injected instrument defect (manual p.46)
        a.h.panel.save_png('lcd', OUT / 'fault.png')
        (OUT / 'renders.json').write_text(json.dumps({k: str(OUT.relative_to(ROOT) / f'{k}.png') for k in ('home', 'menu', 'error', 'lamp_info', 'fault')},
                                                     indent=1) + '\n')


class ContractEntries(unittest.TestCase):
    def test_contract_has_panel_layer(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + ['display.set_contrast', 'lamp.set_lifetime', 'lamp.reset_counters', 'fault.inject', 'fault.clear']:
            self.assertEqual(c['capabilities'][k]['input_schema'], rt.CAPS[k][0])
        self.assertIsNone(c['artifacts']['panel_engine']['sha256'])

    def test_slot_values_match_schemas(self):
        r = Rig()
        r.press('start')
        r.h.step(30)
        for k, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, rt.SLOTS[k][0]), [], k)


if __name__ == '__main__':
    unittest.main()
