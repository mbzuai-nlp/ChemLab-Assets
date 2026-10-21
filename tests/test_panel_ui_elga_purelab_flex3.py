"""front-panel tests for CHEM-190 elga_purelab_flex3 (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_vacuum.py):
python3 -m pytest tests/test_panel_ui_elga_purelab_flex3.py -q

Every gesture goes through the session API (panel.press / panel.switch); the screen is checked in the structured frame (panel.frame, evaluator)
and in the rendered pixels.  Themes: the seven key procedures of the menu survey; the other handset functions (ECO mode and wake-up, Main Menu
settings, automatic / timed refill and intermittent recirculation, reminders, sanitization, calibration against an evaluator volume error,
alarm points, profile re-recording); panel = direct commands; refusals; time-outs; render determinism and the evidence PNGs; contract entries."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
import basic40_runtime_vacuum as rt  # noqa: E402
from asset_tool import validate_value  # noqa: E402

INST = 'flex'
OUT = ROOT / 'outputs/panel_ui/elga_purelab_flex3'
CONTRACT = rt.PURELAB_ASSET_DIR / 'asset.contract.json'
D = 'handset'
SURVEY_TASKS = ['first start-up', 'manual dispense', 'continuous dispense', 'auto-volume dispense', 'switch to profile dispense and record a profile',
                'export data to usb', 'start a reservoir refill']
DONE_TASKS = set()
NOON = 12 * 3600.0


class Rig:
    def __init__(self, on=True, commissioned=True, at=NOON, buffer_l=5.0, recirc=True):
        self.h = rt.PurelabHost()
        self.h.create_instance(INST)
        self.h.register_principal('agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.n = 0
        self.h.configure_supply(INST, feed_volume_m3=0.2, purified_volume_m3=buffer_l * 1e-3, buffer_capacity_m3=0.03, qualified_feed=True, evidence='test',
                                ro_product_rate_m3_s=rt.PURELAB_MAX_RO_M3_S if recirc else None, ro_recovery_fraction=0.5 if recirc else None,
                                recirculation_flow_m3_s=1e-4 if recirc else None, flush_flow_m3_s=5e-5, drain_connected=True)
        self.h.connect_receiver(INST, receiver_id='bottle', volume_m3=0.0, capacity_m3=0.02, evidence='test bottle')
        self.h.install_consumables(INST, evidence='test pack')
        if at:
            self.h.step(at)                                                        # the device clock starts at 00:00 (ECO window)
        if on:
            self.switch('on')
            if commissioned:
                self.press('key_accept')                                           # English
                self.press('key_accept')                                           # fitted

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        assert r['status'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if expect == 'succeeded' else r

    def val(self, sid):
        return self.E.read(instance_id=INST, state_ids=[sid])['observations'][sid]['value']

    def step(self, dt):
        t = 0.0
        while t < dt - 1e-9:
            d = min(3600.0, dt - t)
            self.h.step(d)
            t += d

    def press(self, key, duration_s=0.1, held=()):
        return self.call(self.A, 'panel.press', input_id=key, duration_s=duration_s, **({'held': list(held)} if held else {}))['accepted']

    def switch(self, pos):
        return self.call(self.A, 'panel.switch', input_id='power_supply', position=pos)['accepted']

    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def screen(self):
        return self.frame()['screen']

    def lines(self):
        return self.frame()['displays'][D]['lines']

    def menu_to(self, label):
        """Main Menu: hold Accept 2 s, then Down until the line is selected."""
        if self.screen() != 'menu':
            assert self.press('key_accept', 2.0)
        for _ in range(20):
            if self.frame()['ui']['menu']['label'] == label:
                return
            self.press('key_down')
        raise AssertionError(label)


class KeyProcedures(unittest.TestCase):
    def test_first_start_up(self):
        r = Rig(commissioned=False)
        self.assertEqual(r.screen(), 'language')
        self.assertEqual(r.lines()[0], 'Set Language')
        r.press('key_down')
        r.press('key_accept')                                                      # Deutsch
        self.assertEqual(r.screen(), 'fit')
        self.assertIn('Purification Pack', r.lines())
        self.assertFalse(r.val('unit.state')['commissioned'])
        r.press('key_accept')
        self.assertEqual(r.screen(), 'home')
        self.assertTrue(r.val('unit.state')['commissioned'])
        self.assertEqual(r.val('settings.values')['language'], 'Deutsch')
        DONE_TASKS.add('first start-up')

    def test_manual_and_continuous_dispense(self):
        r = Rig()
        r.press('key_dispense', 3.0)                                               # hold 3 s at 2 L/min = 0.1 L
        self.assertTrue(r.val('water.dispensing'))
        r.step(5)
        self.assertAlmostEqual(r.val('water.receiver_volume_m3'), 1e-4, places=9)
        DONE_TASKS.add('manual dispense')
        r.press('key_dispense', 0.5, held=['key_accept'])
        self.assertTrue(r.val('water.dispensing'))
        r.step(30)
        self.assertIn('Dispensed 1.00 L', r.lines()[3])
        r.press('key_dispense')
        self.assertFalse(r.val('water.dispensing'))
        self.assertEqual(r.val('water.dispense_status'), 'stopped')
        DONE_TASKS.add('continuous dispense')

    def test_auto_volume_dispense(self):
        r = Rig()
        r.press('key_autovol')
        self.assertEqual(r.screen(), 'autovol')
        self.assertEqual(r.lines()[1], '0.50 L')
        r.press('key_up')
        r.press('key_up', 1.0)                                                     # held: 1 + 5 repeats
        self.assertEqual(r.lines()[1], '0.85 L')
        r.press('key_down')
        self.assertAlmostEqual(r.val('dispense.state')['auto_volume_m3'], 0.8e-3)
        r.press('key_dispense')
        r.step(60)
        self.assertAlmostEqual(r.val('water.receiver_volume_m3'), 0.8e-3, places=9)
        self.assertEqual(r.val('water.dispense_status'), 'completed')
        r.press('key_autovol')
        self.assertEqual(r.screen(), 'home')
        DONE_TASKS.add('auto-volume dispense')

    def test_profile_dispense_record_use_and_rerecord(self):
        r = Rig()
        r.menu_to('Set Volumetric Dispense mode')
        r.press('key_accept')
        r.press('key_up')                                                          # Profile (Up / Down step the value being edited)
        r.press('key_accept')
        self.assertEqual(r.val('settings.values')['dispense_mode'], 'Profile Dispense')
        r.menu_to('Exit')
        r.press('key_accept')
        r.press('key_autovol')
        self.assertEqual(r.screen(), 'profiles')
        self.assertIn('>Volume 1  unused', r.lines())
        r.press('key_accept')                                                      # unused: recording starts
        self.assertEqual(r.screen(), 'recording')
        r.press('key_dispense', 6.0)                                               # 0.2 L
        r.step(10)
        r.press('key_dispense', 3.0)                                               # + 0.1 L
        r.step(10)
        r.press('key_accept')
        self.assertEqual(r.screen(), 'profiles')
        self.assertAlmostEqual(r.val('dispense.state')['profiles'][0], 3e-4)
        self.assertIn('>Volume 1  0.30 L', r.lines())
        r0 = r.val('water.receiver_volume_m3')
        r.press('key_accept')                                                      # recorded: the Dispense screen
        self.assertEqual(r.screen(), 'profile_dispense')
        r.press('key_dispense')
        r.step(30)
        self.assertAlmostEqual(r.val('water.receiver_volume_m3') - r0, 3e-4, places=9)
        r.press('key_accept')
        r.press('key_accept', 5.0)                                                 # hold 5 s: re-record Volume 1
        self.assertEqual(r.screen(), 'recording')
        r.press('key_dispense', 1.5)
        r.step(5)
        r.press('key_accept')
        self.assertAlmostEqual(r.val('dispense.state')['profiles'][0], 5e-5)
        DONE_TASKS.add('switch to profile dispense and record a profile')

    def test_data_capture_needs_a_usb_stick(self):
        r = Rig()
        r.menu_to('Data capture')
        self.assertFalse(r.press('key_accept'))
        f = r.frame()
        self.assertEqual(f['ui']['message']['text'], 'Insert USB (FAT32)')
        self.assertTrue(f['ui']['unsupported'])
        DONE_TASKS.add('export data to usb')

    def test_initiate_a_reservoir_refill(self):
        r = Rig(buffer_l=10.0)
        b0 = r.val('water.buffer_volume_m3')
        r.press('key_autovol', 2.0)
        self.assertEqual(r.val('water.mode'), 'recirculate')
        self.assertIn('Reservoir refill', r.val('unit.state')['infobar'])
        r.step(600)
        self.assertGreater(r.val('water.buffer_volume_m3'), b0)
        r.step(5 * 3600)
        self.assertAlmostEqual(r.val('water.buffer_volume_m3'), 0.03)           # full: the unit stops refilling
        self.assertIsNone(r.val('unit.state')['auto_mode'] if r.val('unit.state')['auto_mode'] == 'refill' else None)
        DONE_TASKS.add('start a reservoir refill')


class HandsetFunctions(unittest.TestCase):
    def test_eco_mode_blank_display_and_wake(self):
        r = Rig(at=20 * 3600.0)                                                    # 20:00: inside the default ECO window 18:00-09:00
        self.assertTrue(r.val('unit.state')['eco'])
        r.step(301)
        self.assertEqual(r.screen(), 'eco_blank')
        self.assertTrue(r.press('key_up'))                                         # any key wakes the unit and does nothing else
        self.assertEqual(r.screen(), 'home')
        r.menu_to('ECO start')
        r.press('key_accept')
        for _ in range(36):                                                        # 18:00 -> 00:00 (wraps at the end of the range)
            r.press('key_down')
        r.press('key_accept')
        r.press('key_down')                                                        # ECO end
        r.press('key_accept')
        for _ in range(18):
            r.press('key_down')
        r.press('key_accept')
        st = r.val('settings.values')
        self.assertEqual((st['eco_start_s'], st['eco_end_s']), (0.0, 0.0))         # 00:00 and 00:00: ECO disabled
        self.assertFalse(r.val('unit.state')['eco'])

    def test_automatic_refill_and_intermittent_recirculation(self):
        r = Rig(buffer_l=1.0)
        r.call(r.A, 'settings.configure', settings={'eco_start_s': 0.0, 'eco_end_s': 0.0})   # ECO off: intermittent recirculation
        r.step(1)
        self.assertEqual(r.val('unit.state')['auto_mode'], 'refill')             # 1 L < 6 L (no ECO at noon)
        r.step(8 * 3600)
        self.assertGreaterEqual(r.val('water.buffer_volume_m3'), 0.03 - 1e-9)
        r.step(3600)
        modes = set()
        for _ in range(24):
            r.step(600)
            modes.add(r.val('unit.state')['auto_mode'])
        self.assertIn('intermittent', modes)
        self.assertIn(None, modes)

    def test_timed_auto_refill_and_refill_set_point(self):
        r = Rig(buffer_l=10.0)
        r.menu_to('Auto Refill')
        r.press('key_accept')
        for _ in range(27):                                                        # Off, 00:00 ... 13:00
            r.press('key_up')
        r.press('key_accept')
        self.assertEqual(r.val('settings.values')['auto_refill_s'], 13 * 3600.0)
        r.menu_to('Refill set point')
        r.press('key_accept')
        for _ in range(4):
            r.press('key_down')                                                    # 6 -> 4 L
        r.press('key_accept')
        self.assertAlmostEqual(r.val('settings.values')['refill_setpoint_m3'], 0.004)
        r.step(1800)
        self.assertNotEqual(r.val('unit.state')['auto_mode'], 'refill')
        r.step(1800)                                                               # 13:00
        self.assertEqual(r.val('unit.state')['auto_mode'], 'refill')

    def test_alarm_points_and_reminders(self):
        r = Rig()
        r.h.set_readings(INST, resistivity_ohm_m=150000.0, temperature_k=310.15, source_kind='synthetic', evidence='test')
        r.step(1)
        self.assertIn('High Temperature Water Alarm', r.val('unit.state')['infobar'])
        text = r.val('unit.state')['infobar'] + '   '
        shown = r.lines()[5]
        self.assertEqual(len(shown), 21)                                           # the Infobar scrolls
        self.assertIn(shown, text + text)
        r.step(1)
        self.assertNotEqual(r.lines()[5], shown)
        self.assertIn('15.0 MOhm.cm', r.lines()[1])
        r.menu_to('Temp alarm point')
        r.press('key_accept')
        for _ in range(5):
            r.press('key_up')                                                      # 35 -> 40 C
        r.press('key_accept')
        r.menu_to('Purity alarm point')
        r.press('key_accept')
        for _ in range(60):
            r.press('key_up')                                                      # 10 -> 16 MOhm.cm
        r.press('key_accept')
        r.step(1)
        u = r.val('unit.state')
        self.assertFalse(u['alarm_temperature'])
        self.assertTrue(u['alarm_purity'])
        r.step(370 * 86400)
        rem = r.val('reminders.state')
        self.assertTrue(rem['pack_due'] and rem['uv_due'] and rem['sanitization_due'])
        r.menu_to('Reset UV reminder')
        r.press('key_accept')
        self.assertFalse(r.val('reminders.state')['uv_due'])
        r.menu_to('Reset Pack reminder')
        r.press('key_accept')                                                      # no confirmed pack replacement: refused
        self.assertEqual(r.frame()['ui']['message']['text'], 'Not possible now')
        r.call(r.A, 'water.set_mode', mode='idle')                                # stop the unit's own recirculation for the host replacement
        r.h.confirm_consumables_replacement(INST, evidence='new pack')
        r.press('key_accept')
        self.assertFalse(r.val('reminders.state')['pack_due'])

    def test_sanitization(self):
        r = Rig()
        r.menu_to('Sanitization')
        r.press('key_accept')
        self.assertEqual(r.screen(), 'sanitize')
        self.assertEqual(r.lines()[1], 'Fit Sanitization Pack')
        r.press('key_accept')
        self.assertEqual(r.val('water.mode'), 'flush')
        r.step(rt.PURELAB_SANITIZATION_S)
        self.assertEqual(r.lines()[1], 'Rinse complete')
        r.press('key_accept')
        r.press('key_accept')
        self.assertEqual(r.lines()[1], 'Complete')
        self.assertIsNone(r.val('sanitization.state')['step'])
        r.press('key_accept')
        self.assertEqual(r.screen(), 'home')

    def test_calibration_corrects_auto_volume(self):
        r = Rig()
        r.call(r.E, 'dispense.configure_error', gain=-0.04)                       # the unit dispenses 4 % too little
        r.menu_to('Calibration')
        r.press('key_accept')
        r.press('key_dispense')
        r.step(60)
        self.assertAlmostEqual(r.val('water.receiver_volume_m3'), 0.96e-3, places=9)
        r.press('key_accept')
        self.assertEqual(r.screen(), 'cal_enter')
        for _ in range(4):
            r.press('key_down', 1.0)                                               # 1000 - 4 x 6 ... then fine
        r.press('key_up', 0.1)
        while int(r.frame()['ui']['editor']['buffer']['measured']) > 960:
            r.press('key_down')
        r.press('key_accept')
        self.assertAlmostEqual(r.val('dispense.state')['calibration_factor'], 1 / 0.96, places=5)
        self.assertEqual(r.screen(), 'menu')
        r.menu_to('Exit')
        r.press('key_accept')
        r0 = r.val('water.receiver_volume_m3')
        r.press('key_autovol')
        r.press('key_dispense')                                                    # 0.5 L requested
        r.step(60)
        self.assertAlmostEqual(r.val('water.receiver_volume_m3') - r0, 0.5e-3, places=8)


class RefusalsAndTimeouts(unittest.TestCase):
    def test_power_off_and_menu_needs_auto_volume_off(self):
        r = Rig(on=False)
        self.assertEqual(r.screen(), 'off')
        self.assertFalse(r.press('key_dispense'))
        r.switch('on')
        r.press('key_accept')
        r.press('key_accept')
        r.press('key_autovol')
        self.assertFalse(r.press('key_accept', 2.0))                               # Auto Volume on: no Main Menu
        self.assertEqual(r.screen(), 'autovol')
        r.press('key_autovol')
        self.assertTrue(r.press('key_accept', 2.0))
        self.assertEqual(r.screen(), 'menu')

    def test_dispense_refused_without_receiver_space(self):
        r = Rig()
        r.h._instances[INST].state['water.receiver_volume_m3'] = 0.02
        self.assertFalse(r.press('key_dispense', 1.0))
        self.assertEqual(r.frame()['ui']['message']['text'], 'Not possible now')
        self.assertFalse(r.val('water.dispensing'))

    def test_render_determinism_and_evidence(self):
        a, b = Rig(), Rig()
        self.assertEqual(a.h.panel(INST).png_bytes(D)[0], b.h.panel(INST).png_bytes(D)[0])
        OUT.mkdir(parents=True, exist_ok=True)
        r = Rig()
        r.h.set_readings(INST, resistivity_ohm_m=182000.0, temperature_k=298.15, source_kind='synthetic', evidence='test')
        r.step(1)
        renders = {'home': r.h.panel(INST).save_png(D, OUT / 'home.png')}
        r.menu_to('Refill set point')
        renders['menu'] = r.h.panel(INST).save_png(D, OUT / 'menu.png')
        r.menu_to('Exit')
        r.press('key_accept')
        r.h._instances[INST].state['water.receiver_volume_m3'] = 0.02
        r.press('key_dispense')
        renders['error'] = r.h.panel(INST).save_png(D, OUT / 'error.png')
        for v in renders.values():
            v['path'] = str(Path(v['path']).relative_to(ROOT))
        (OUT / 'renders.json').write_text(json.dumps(renders, indent=1) + '\n')


class PanelEqualsDirectAndContract(unittest.TestCase):
    def test_panel_equals_direct(self):
        a, b = Rig(), Rig(commissioned=False)
        b.call(b.A, 'unit.commission', language='English')
        a.press('key_autovol')
        a.press('key_up')
        a.press('key_dispense')
        b.call(b.A, 'dispense.set_auto_volume', volume_m3=0.55e-3)
        b.call(b.A, 'dispense.auto', profile=None)
        for sid in ('water.target_volume_m3', 'water.flow_rate_m3_s', 'water.dispensing', 'dispense.state', 'unit.state'):
            self.assertEqual(a.val(sid), b.val(sid), sid)

    def test_metadata_and_contract(self):
        meta = rt.export_purelab_contract_metadata()['asset_types'][rt.PURELAB_ASSET_TYPE_ID]
        self.assertEqual(set(meta['device_capabilities']), set(rt.PURELAB_DEVICE_ARGS))
        self.assertEqual(set(meta['panel_capabilities']), {'panel.press', 'panel.switch', 'panel.capture', 'panel.frame'})
        for name, cap in {**meta['device_capabilities'], **meta['panel_capabilities']}.items():
            self.assertEqual(validate_value(cap['example_arguments'][0], cap['input_schema']), [], name)
        c = json.loads(CONTRACT.read_text())
        if 'chemistry_lab.panel_ui' in c['extensions']:
            for name in list(meta['device_capabilities']) + list(meta['panel_capabilities']):
                self.assertIn(name, c['capabilities'], name)
            self.assertEqual(c['asset_version'], rt.PURELAB_ASSET_VERSION)
        r = Rig()
        caps = r.A.describe(instance_id=INST)['capabilities']
        self.assertIn('panel.press', caps)
        self.assertNotIn('dispense.configure_error', caps)
        self.assertEqual(r.call(r.A, 'dispense.configure_error', expect='rejected', gain=0.0)['error']['code'], 'ACCESS_DENIED')


class SyncAudit20261019(unittest.TestCase):
    """Fixes of the 2026-10-19 front-panel sync audit: one metering model for the handset and the CHEM-190 dispense (rule 1), the language
    policy (rule 3) and panel gestures acting as the calling session (rule 2)."""

    def dispense(self, r, direct, litres=0.5):
        r0 = r.val('water.receiver_volume_m3')
        if direct:
            r.call(r.A, 'water.set_volume', volume_m3=litres * 1e-3)
            r.call(r.A, 'water.set_flow_rate', flow_m3_s=rt.PURELAB_MAX_DISPENSE_M3_S)
            r.call(r.A, 'water.start')
        else:
            r.call(r.A, 'dispense.set_auto_volume', volume_m3=litres * 1e-3)
            r.call(r.A, 'dispense.auto', profile=None)
        r.step(120)
        self.assertEqual(r.val('water.dispense_status'), 'completed')
        return r.val('water.receiver_volume_m3') - r0

    def test_direct_dispense_uses_the_device_metering(self):
        r = Rig()
        r.call(r.E, 'dispense.configure_error', gain=0.1)
        for direct in (False, True):
            self.assertAlmostEqual(self.dispense(r, direct), 0.55e-3, places=9)       # the water that flows carries the error on both paths
            self.assertAlmostEqual(r.val('water.indicated_dispensed_volume_m3'), 0.5e-3, places=12)   # the device indicates its metered volume
            self.assertAlmostEqual(r.val('water.target_volume_m3'), 0.5e-3, places=12)
        # a volumetric calibration on the device (dispense nominal 1 L, enter the measured 1.1 L) corrects both paths
        r.call(r.A, 'calibration.control', action='start')
        r.step(60)
        r.call(r.A, 'calibration.control', action='enter', measured_m3=1.1e-3)
        for direct in (False, True):
            self.assertAlmostEqual(self.dispense(r, direct), 0.5e-3, places=8)

    def test_defaults_keep_the_direct_dispense_exact(self):
        r = Rig()
        self.assertEqual(self.dispense(r, True), 0.5e-3)
        self.assertEqual(r.val('water.indicated_dispensed_volume_m3'), r.val('water.dispensed_volume_m3'))

    def test_language_is_stored_and_shown_but_texts_stay_english(self):
        r = Rig(commissioned=False)
        r.press('key_down')
        r.press('key_accept')                                                    # Deutsch
        r.press('key_accept')
        self.assertEqual(r.val('settings.values')['language'], 'Deutsch')
        r.menu_to('Language')
        self.assertIn('Deutsch', ' '.join(r.lines()))                           # shown in the Main Menu
        r.menu_to('Exit')
        r.press('key_accept')
        self.assertEqual(r.screen(), 'home')
        spec = json.loads((rt.PURELAB_ASSET_DIR / 'panel_ui.json').read_text())
        self.assertTrue(any('English texts' in a for a in spec['assumptions']))

    def test_panel_gesture_acts_as_the_calling_session(self):
        r = Rig()
        seen = []
        orig = rt.PurelabSession._apply

        def spy(sess, inst, cap, args, rid):
            if not cap.startswith('panel.'):
                seen.append((sess._PurelabSession__principal_id, cap))
            return orig(sess, inst, cap, args, rid)
        with mock.patch.object(rt.PurelabSession, '_apply', spy):
            r.press('key_dispense', 1.0)
        self.assertEqual(seen, [('agent', 'dispense.manual')])
        self.assertIsNone(r.h._panel_caller)


class Sweep20261019(unittest.TestCase):
    """2026-10-19 consistency sweep: a dispense / sanitization / calibration started by one agent cannot be stopped, powered off or changed by
    another agent (panel and direct path, ACCESS_DENIED, state unchanged, 'Run of another user' on the handset); the dispense arm and the
    evaluator are not restricted; the owner stops its own dispense."""

    def test_another_agents_dispense_cannot_be_stopped_or_changed(self):
        r = Rig()
        r.h.register_principal('agent_b')
        B = r.h.session('agent_b')
        r.press('key_dispense', 0.5, held=['key_accept'])                         # agent A: continuous dispense
        r.step(10)
        self.assertEqual(r.h._instances[INST].g03['run_owners'], {'dispense': 'agent'})
        before = copy.deepcopy(r.h._instances[INST].state)
        self.assertFalse(r.call(B, 'panel.press', input_id='key_dispense')['accepted'])   # B's Dispense key would stop it: refused
        self.assertEqual(r.frame()['ui']['message']['text'], 'Run of another user')
        self.assertIn('Run of another user', ' '.join(r.lines()))
        for cap, args in (('water.stop', {}), ('water.set_flow_rate', dict(flow_rate_m3_s=1e-5))):
            self.assertEqual(r.call(B, cap, expect='rejected', **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(r.h._instances[INST].state, before)                       # state unchanged
        self.assertEqual(r.call(B, 'arm.set_height', expect='rejected', height_m=0.0)['error']['code'], 'BUSY')   # the arm (by hand) is not
        # refused for ownership; the unit's own rule (no arm move while dispensing) answers
        self.assertTrue(r.press('key_dispense'))                                   # A stops its own dispense
        self.assertFalse(r.val('water.dispensing'))
        self.assertNotIn('run_owners', r.h._instances[INST].g03)
        self.assertTrue(r.call(B, 'panel.press', input_id='key_dispense', duration_s=0.5, held=['key_accept'])['accepted'])   # B's own dispense
        self.assertEqual(r.h._instances[INST].g03['run_owners'], {'dispense': 'agent_b'})
        r.call(r.E, 'water.stop')                                                  # the evaluator is not restricted
        self.assertFalse(r.val('water.dispensing'))
        self.assertTrue(r.press('key_dispense', 0.5, held=['key_accept']))         # A's dispense again ...
        self.assertTrue(r.call(B, 'panel.switch', input_id='power_supply', position='off')['accepted'])   # ... B pulls the 24 V supply:
        self.assertFalse(r.val('power.enabled'))                                   # physical (lead clarification 10-19), the dispense ends
        self.assertFalse(r.val('water.dispensing'))


if __name__ == '__main__':
    unittest.main()
