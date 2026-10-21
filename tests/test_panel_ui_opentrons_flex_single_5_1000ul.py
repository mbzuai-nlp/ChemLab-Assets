"""touchscreen tests for CHEM-175 opentrons_flex (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_automation.py):
python3 -m pytest tests/test_panel_ui_opentrons_flex_single_5_1000ul.py -q

Every gesture goes through the session API (panel.touch on the touchscreen, panel.switch on the rear power switch).  The host stands in for the
Opentrons App (stored protocols), the operator (tip pickup / drop confirmations, refilling a well) and the clock.  Checks look at the runtime state
(robot software, joint / volume ledger) and at the screen (frame widgets, pop-ups, keyboards; status light).  Themes: the six key procedures of
the menu survey (run a stored protocol, Labware Position Check, quick transfer, error recovery, deck fixture, turn off), protocol management,
setup checks (parameters, CSV, deck conflicts, instruments), run controls, instrument management, settings (recovery mode, sleep, brightness,
language, device reset, file manager), status light, panel = direct capability, refusals, render determinism and the PNGs, contract."""
import hashlib
import importlib.util
import re
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import basic40_runtime_automation as rt  # noqa: E402
from asset_runtime import _panel_ui as pu  # noqa: E402

INST = 'flex_panel_01'
OUT = ROOT / 'outputs/panel_ui/opentrons_flex_single_5_1000ul'
ASSET = rt.OPENTRONS_FLEX_ASSET_DIR
SPEC = rt._fts_panel()['spec']
SURVEY_TASKS = ['Run a stored protocol', 'Run Labware Position Check', 'Create and run a quick transfer', 'Recover from a run error', 'Configure a deck fixture',
                'Turn off the robot']
DONE_TASKS = set()


def P(x, y, z):
    return {'x_m': x, 'y_m': y, 'z_m': z}


PROTOCOL = {'protocol_id': 'p1', 'name': 'Serial transfer', 'author': 'Lab', 'description': 'one transfer with a runtime volume',
            'labware': [{'slot': 'C2', 'name': 'source plate'}, {'slot': 'D2', 'name': 'destination plate'}],
            'liquids': [{'name': 'buffer', 'container_ids': ['src_1'], 'volume_m3': 2e-6}],
            'parameters': [{'name': 'volume_ul', 'display_name': 'Volume (uL)', 'type': 'float', 'default': 100, 'min': 5, 'max': 1000}],
            'tips': ['tip_1', 'tip_2'],
            'commands': [{'cmd': 'pick_up_tip', 'tip_id': 'tip_1'}, {'cmd': 'aspirate', 'container_id': 'src_1', 'volume_m3': {'param': 'volume_ul', 'scale': 1e-9}},
                         {'cmd': 'dispense', 'container_id': 'dst_1', 'volume_m3': {'param': 'volume_ul', 'scale': 1e-9}}, {'cmd': 'drop_tip', 'slot_id': 'A1'}]}


class Rig:
    def __init__(self, on=True, src=2e-6, offsets=True):
        self.h = rt.OpentronsFlexRuntime()
        self.h.create_instance(INST)
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('eval', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('eval')
        self.n = 0
        for i in range(6):
            self.h.host_register_tip(INST, f'tip_{i + 1}', slot_id='B2', capacity_m3=1e-6, position_m=P(0.009 * i, 0.05, 0.1), evidence='synthetic tip in the rack')
        self.h.host_register_container(INST, 'src_1', slot_id='C2', capacity_m3=5e-6, volume_m3=src, position_m=P(0.0, -0.05, 0.05), evidence='synthetic well')
        for i in range(2):
            self.h.host_register_container(INST, f'dst_{i + 1}', slot_id='D2', capacity_m3=5e-6, volume_m3=0.0, position_m=P(0.009 * i, -0.15, 0.05), evidence='synthetic well')
        self.h.host_register_waste(INST, slot_id='A1', position_m=P(-0.2, 0.15, 0.15), evidence='synthetic trash bin drop position')
        if on:
            self.switch('on')
            self.wait(3.0)
        self.h.host_store_protocol(INST, PROTOCOL)
        if offsets:
            for lw in ('source plate', 'destination plate'):
                self.h._instances[INST]['ts']['offsets'][lw] = P(0.0, 0.0, 0.0)

    # ---- session calls
    def call(self, cap, sess=None, expect=('succeeded', 'accepted'), **args):
        self.n += 1
        t0 = self.h.time_s
        r = (sess or self.A).invoke(instance_id=INST, capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0
        return r['result'] if r['status'] in ('succeeded', 'accepted') else r

    def switch(self, pos):
        return self.call('panel.switch', input_id='power_switch', position=pos)['accepted']

    def touch(self, x, y, d=0.1, to=None):
        extra = dict(to_x=to[0], to_y=to[1]) if to else {}
        return self.call('panel.touch', display_id='touchscreen', x=x, y=y, duration_s=d, **extra)['accepted']

    def frame(self):
        return self.call('panel.frame', sess=self.E)['frame']

    def disp(self):
        return self.frame()['displays']['touchscreen']

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid].get('text')

    def tap(self, wid, d=0.1):
        x, y, w, h = self.widgets()[wid]['rect']
        return self.touch(x + w // 2, y + h // 2, d)

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label, d=0.1):
        ov = self.overlay()
        assert ov is not None, 'no pop-up'
        for b in ov['buttons']:
            if b['label'] == label or re.sub(r'^(\[.\] | ?\d+ | - )', '', b['label']) == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2, d)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def type(self, text, ok=True):
        for ch in text:
            if ch == ' ':
                self.ov('Space')
                continue
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
        if ok:
            self.ov('OK')

    def row(self, wid, label, d=0.1):
        for _ in range(12):
            w = self.widgets()[wid]
            for i, cells in enumerate(w['cells']):
                if cells[0] == label:
                    x, y, ww, _ = w['rect']
                    return self.touch(x + ww // 2, y + i * w['row_h'] + w['row_h'] // 2, d)
            x, y, ww, hh = w['rect']
            self.touch(x + ww // 2, y + hh - 5, to=(x + ww // 2, y + 5))
        raise AssertionError(f'{label!r} not in {[c[0] for c in w["cells"]]}')

    def choose(self, option):
        self.ov(option)
        if self.overlay() is not None and self.overlay().get('kind') == 'select' and 'OK' in [b['label'] for b in self.overlay()['buttons']]:
            self.ov('OK')

    def screen(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def wait(self, dt):
        self.h.host_set_clock(self.h.time_s + dt)

    def serve(self, seconds, confirm=True):
        """Advance the clock second by second, confirming each tip event the run waits for (the operator / host)."""
        for _ in range(int(seconds)):
            ev = self.h.host_pending_tip_event(INST)
            if ev is not None and confirm:
                self.h.host_confirm_tip_event(INST, event=ev['event'], tip_id=ev['tip_id'], slot_id=ev['slot_id'], position_m=ev['position_m'],
                                              evidence='synthetic host constraint for the tip')
            self.wait(1.0)

    def state(self, *ids):
        o = self.E.read(instance_id=INST, state_ids=list(ids))['observations']
        return o[ids[0]]['value'] if len(ids) == 1 else {k: v['value'] for k, v in o.items()}

    def run(self, path=None):
        r = self.state('run.state')
        return r if path is None else r[path]

    def volume(self, cid):
        return next(c['volume_m3'] for c in self.state('truth.containers') if c['container_id'] == cid)

    def open_setup(self, name='Serial transfer'):
        self.tap('nav_protocols')
        self.row('plist', name)
        self.tap('det_start')


class KeyProcedures(unittest.TestCase):
    def test_run_a_stored_protocol(self):
        r = Rig()
        r.tap('nav_protocols')
        r.row('plist', 'Serial transfer')
        self.assertEqual((r.screen(), r.text('det_name')), (('ui', 'details'), 'Serial transfer'))
        r.tap('det_start')
        self.assertEqual((r.screen()[1], r.run('status')), ('setup', 'setup'))
        self.assertFalse(r.run('ready'))                                                  # parameters not confirmed yet
        r.tap('su_params')
        r.row('prm_list', 'Volume (uL)')
        r.type('150')
        r.tap('prm_confirm')
        self.assertEqual((r.run('params')['volume_ul'], r.run('params_confirmed'), r.run('ready')), (150.0, True, True))
        r.tap('su_play')
        self.assertEqual((r.screen()[0], r.state('robot.status_light')), ('running', {'color': 'green', 'pattern': 'solid'}))
        r.serve(8)
        self.assertEqual(r.screen()[0], 'run_end')
        self.assertEqual(r.text('end_msg'), 'Run completed')
        self.assertAlmostEqual(r.volume('dst_1'), 1.5e-7)
        self.assertEqual(r.state('robot.status_light'), {'color': 'green', 'pattern': 'pulsing'})
        r.tap('end_msg')
        self.assertEqual(r.screen()[1], 'summary')
        r.tap('sm_dash')
        self.assertEqual((r.screen(), r.run()), (('ui', 'dashboard'), None))
        self.assertEqual(r.text('rc0').split('\n')[0], 'Serial transfer')                 # the recent-runs carousel
        self.assertEqual(r.state('run.history')[-1]['status'], 'succeeded')
        DONE_TASKS.add('Run a stored protocol')

    def test_run_labware_position_check(self):
        r = Rig(offsets=False)
        r.open_setup()
        self.assertEqual(r.text('su_offsets'), 'Labware Offsets   Offsets missing')
        r.tap('su_offsets')
        r.tap('of_apply')
        self.assertEqual(r.overlay()['title'], 'Labware Offsets')                          # nothing stored yet
        r.ov('OK')
        r.tap('of_lpc')
        self.assertEqual(r.screen()[1], 'lpc')
        self.assertAlmostEqual(r.state('motion.z_m'), 0.06)                                 # 10 mm above the source plate
        r.tap('j_0_1'), r.tap('ax_z'), r.tap('jog_m'), r.tap('jog_m')                     # Z down 2 x 0.1 mm
        r.tap('j_1'), r.tap('ax_x'), r.tap('jog_p')                                       # X right 1 mm
        r.tap('lpc_close')
        self.assertAlmostEqual(r.state('labware.offsets')['source plate']['z_m'], -0.0002)        # the jog from the check position (fix 2026-10-19)
        self.assertAlmostEqual(r.state('labware.offsets')['source plate']['x_m'], 0.001)
        self.assertIn('destination plate', r.text('lpc_title'))
        r.tap('lpc_close')
        self.assertEqual((r.screen()[1], r.run('offsets_ok')), ('setup_offsets', True))
        DONE_TASKS.add('Run Labware Position Check')

    def test_create_and_run_a_quick_transfer(self):
        r = Rig()
        r.tap('nav_protocols')
        r.tap('qt_new')
        r.row('qt_pipette_rows', 'Liquid class')
        r.choose('Viscous')
        r.tap('qt_pipette_next')
        r.row('qt_source_rows', 'Source wells')
        r.ov('src_1'), r.ov('OK')
        r.tap('qt_source_next')
        r.row('qt_dest_rows', 'Destination wells')
        r.ov('dst_1'), r.ov('dst_2'), r.ov('OK')
        r.tap('qt_dest_next')
        r.row('qt_volume_rows', 'Volume')
        r.type('50')
        r.row('qt_volume_rows', 'Name')
        r.type('Fill plate')
        r.tap('qt_volume_next')
        r.row('qt_advanced_rows', 'Change tip')
        r.choose('Before every aspirate')
        r.tap('qt_advanced_next')
        self.assertEqual(r.overlay()['title'], 'Create Transfer')
        r.ov('Run')
        qt = next(p for p in r.state('protocols.list') if p['kind'] == 'quick_transfer')
        self.assertEqual((qt['name'], r.screen()[1], r.run('ready')), ('Fill plate', 'setup', True))
        r.tap('su_play')
        r.serve(20)
        self.assertEqual(r.text('end_msg'), 'Run completed')
        self.assertAlmostEqual(r.volume('dst_1'), 5e-8)
        self.assertAlmostEqual(r.volume('dst_2'), 5e-8)
        events = [e['event'] for e in r.state('tip.events')]
        self.assertEqual(events, ['attach', 'release', 'attach', 'release'])                 # a new tip before every aspirate
        DONE_TASKS.add('Create and run a quick transfer')

    def test_recover_from_a_run_error(self):
        r = Rig(src=5e-8)                                                                    # 50 uL in the well, 100 uL wanted
        r.open_setup()
        r.call('run.confirm_parameters')
        r.tap('su_play')
        r.serve(3)
        self.assertEqual((r.screen()[0], r.run('error')['type']), ('recovery', 'no_liquid_detected'))
        self.assertEqual(r.state('robot.status_light'), {'color': 'yellow', 'pattern': 'pulsing'})
        r.tap('er_launch')
        self.assertEqual(r.text('er_opt0'), 'Manually fill well and retry with same tips')
        r.h.host_set_container_volume(INST, 'src_1', volume_m3=2e-6, evidence='synthetic operator fills the well')
        r.tap('er_opt0')
        self.assertEqual(r.screen()[0], 'running')
        r.serve(8)
        self.assertEqual(r.text('end_msg'), 'Run completed')
        self.assertEqual(r.state('run.history')[-1]['errors'][0]['action'], 'retry_same_tips')
        DONE_TASKS.add('Recover from a run error')

    def test_configure_a_deck_fixture(self):
        r = Rig()
        r.tap('nav_menu')
        r.ov('Deck configuration')
        self.assertEqual(r.screen()[1], 'deck_config')
        r.tap('dc_D3')
        labels = [b['label'] for b in r.overlay()['buttons']]
        self.assertIn('Waste chute', labels)
        r.ov('Waste chute')
        r.ov('Confirm')
        self.assertEqual(r.state('deck.fixtures')['D3'], 'waste_chute')
        self.assertIn('Waste chute', r.text('dc_D3'))
        r.tap('dc_B1')
        self.assertNotIn('Waste chute', [b['label'] for b in r.overlay()['buttons']])         # only D3 takes the chute
        r.ov('Cancel')
        DONE_TASKS.add('Configure a deck fixture')

    def test_turn_off_the_robot(self):
        r = Rig()
        r.tap('nav_menu')
        r.ov('Turn off robot')
        r.ov('Turn off')
        self.assertEqual((r.state('power.enabled'), r.screen()[0], r.state('robot.status_light')), (False, 'off', {'color': 'off', 'pattern': 'off'}))
        r.switch('off')
        r.call('motion.move', expect=('rejected',), x_m=0.0, y_m=0.0, z_m=0.0)
        r.switch('on')                                                                       # back on: the gantry homes (Home gantry on restart)
        self.assertEqual((r.state('power.enabled'), r.state('motion.x_m'), r.screen()), (True, 0.24, ('ui', 'dashboard')))
        DONE_TASKS.add('Turn off the robot')


class ProtocolsAndSetup(unittest.TestCase):
    def test_pin_sort_delete_and_limit(self):
        r = Rig()
        for i in range(21):
            r.h.host_store_protocol(INST, dict(PROTOCOL, protocol_id=f'q{i}', name=f'Protocol {i:02d}'))
        self.assertEqual(len(r.state('protocols.list')), 20)                                   # older protocols deleted beyond 20
        self.assertNotIn('p1', [p['protocol_id'] for p in r.state('protocols.list')])
        r.tap('nav_protocols')
        r.tap('s_name')
        self.assertEqual(r.widgets()['plist']['cells'][0][0], 'Protocol 01')
        r.tap('s_name')
        self.assertEqual(r.widgets()['plist']['cells'][0][0], 'Protocol 20')
        r.row('plist', 'Protocol 20', d=1.2)                                                   # long press: the card menu
        r.ov('Pin / Unpin protocol')
        self.assertEqual(r.text('pc0').split('\n')[0], 'Protocol 20')
        r.tap('pc0', d=1.2)
        r.ov('Delete protocol')
        r.ov('Delete')
        self.assertNotIn('q20', [p['protocol_id'] for p in r.state('protocols.list')])

    def test_deck_conflict_update_deck_and_instruments(self):
        r = Rig()
        r.h.host_store_protocol(INST, dict(PROTOCOL, protocol_id='p2', name='Chute protocol', fixtures={'D3': 'waste_chute'}, parameters=[]))
        r.open_setup('Chute protocol')
        self.assertEqual(r.text('su_deck'), 'Deck hardware   Deck conflict')
        r.tap('su_deck')
        self.assertEqual(r.widgets()['dk_list']['cells'][0], ['D3', 'waste_chute', 'Location conflict'])
        r.tap('dk_update')
        self.assertEqual(r.state('deck.fixtures')['D3'], 'waste_chute')
        r.tap('dk_back')
        r.call('instrument.detach', mount='left')
        self.assertEqual(r.text('su_instr'), 'Instruments   Attach or calibrate')
        r.tap('su_instr')
        r.tap('si_attach')
        r.ov('1-Channel Pipette'), r.ov('Done')
        self.assertEqual(r.overlay()['title'], 'Calibrate pipette')
        r.ov('Start'), r.ov('OK')
        self.assertTrue(r.state('instruments.mounts')['left']['calibrated'])
        r.tap('si_back')
        self.assertTrue(r.run('ready'))

    def test_csv_parameter_and_locked_values(self):
        r = Rig()
        r.h.host_set_usb_drive(INST, ['plate_map.csv'])
        r.h.host_store_protocol(INST, dict(PROTOCOL, protocol_id='p3', name='CSV protocol',
                                           parameters=[{'name': 'map', 'display_name': 'Plate map', 'type': 'csv', 'default': None},
                                                       {'name': 'mix', 'display_name': 'Mix', 'type': 'bool', 'default': False},
                                                       {'name': 'speed', 'display_name': 'Speed', 'type': 'str', 'default': 'slow',
                                                        'choices': [{'value': 'slow', 'text': 'Slow'}, {'value': 'fast', 'text': 'Fast'}]}],
                                           commands=[{'cmd': 'comment', 'text': 'nothing'}]))
        r.open_setup('CSV protocol')
        r.tap('su_params')
        r.row('prm_list', 'Plate map')
        r.choose('plate_map.csv')
        r.row('prm_list', 'Mix')
        r.row('prm_list', 'Speed')
        r.choose('Fast')
        self.assertEqual(r.run('params'), {'map': 'plate_map.csv', 'mix': True, 'speed': 'fast'})
        r.tap('prm_confirm')
        r.tap('su_params')
        r.row('prm_list', 'Mix')
        self.assertEqual(r.overlay()['title'], 'Parameters')                                  # read-only after Confirm values
        self.assertTrue(r.run('params')['mix'])


class RunControls(unittest.TestCase):
    def test_pause_resume_step_list_stop(self):
        r = Rig()
        r.open_setup()
        r.call('run.confirm_parameters')
        r.tap('su_play')
        r.serve(2)
        r.tap('rn_play')                                                                      # pause
        self.assertEqual((r.run('status'), r.state('robot.status_light')['color']), ('paused', 'blue'))
        idx = r.run('index')
        r.serve(3)
        self.assertEqual(r.run('index'), idx)                                                 # paused: no command runs
        r.touch(900, 300, to=(200, 300))                                                     # swipe right to left: the step list
        self.assertEqual(r.screen()[1], 'run_list')
        self.assertEqual(r.widgets()['rl_steps']['cells'][0][0], r.run('current_step'))       # the current step at the top
        r.tap('rl_play')
        r.serve(1)
        r.tap('rl_stop')
        r.ov('Cancel run')
        self.assertEqual((r.screen()[0], r.text('end_msg')), ('run_end', 'Run canceled'))

    def test_recovery_off_fails_and_injected_errors(self):
        r = Rig()
        r.call('robot.configure', key='recovery_mode', value=False)
        r.h.host_inject_error(INST, kind='general')
        r.open_setup()
        r.call('run.confirm_parameters')
        r.tap('su_play')
        r.serve(2)
        self.assertEqual(r.text('end_msg'), 'Run failed')
        r.tap('end_msg')
        r.tap('sm_error')
        self.assertEqual(r.overlay()['lines'][0], 'General error')
        r.ov('OK')
        r.tap('sm_again')
        self.assertEqual((r.screen()[1], r.run('status')), ('setup', 'setup'))
        r.call('robot.configure', key='recovery_mode', value=True)
        r.call('run.confirm_parameters')
        r.tap('su_play')
        r.serve(2)
        r.h.host_inject_error(INST, kind='overpressure')
        r.serve(2)
        self.assertEqual(r.run('recovery_options'), ['skip_same_tips', 'skip_new_tips', 'cancel'] if r.run('error')['type'] == 'overpressure_dispense'
                         else ['retry_new_tips', 'cancel'])
        r.tap('er_launch')
        r.tap('er_opt0')
        r.serve(12)
        self.assertEqual(r.text('end_msg'), 'Run completed')


class InstrumentsSettings(unittest.TestCase):
    def test_instrument_detach_recalibrate_and_unsupported(self):
        r = Rig()
        r.tap('nav_instruments')
        r.tap('in_left')
        self.assertEqual(r.overlay()['title'], 'Instrument')
        r.ov('Recalibrate'), r.ov('Start'), r.ov('OK')
        self.assertEqual(r.state('instruments.mounts')['left']['last_calibrated_s'], r.h.time_s)
        self.assertEqual(r.state('robot.status_light'), {'color': 'green', 'pattern': 'blink_twice'})   # action complete
        r.tap('in_left')
        r.ov('Detach'), r.ov('Done')
        self.assertIsNone(r.state('instruments.mounts')['left'])
        r.tap('in_right')
        self.assertFalse(r.ov('96-Channel Pipette'))
        self.assertIn('96-channel', r.frame()['ui']['unsupported'])

    def test_settings_rows(self):
        r = Rig()
        r.tap('nav_settings')
        r.row('set_rows', 'Robot name')
        r.type('lab-flex')
        self.assertEqual(r.text('nav_name'), 'lab-flex')
        r.row('set_rows', 'Status LEDs')
        r.choose('Off')
        self.assertEqual(r.state('robot.status_light'), {'color': 'off', 'pattern': 'off'})
        r.row('set_rows', 'Touchscreen brightness')
        r.ov('+'), r.ov('+'), r.ov('+'), r.ov('Done')
        self.assertEqual((r.state('robot.settings')['brightness_level'], r.disp()['brightness']), (6, 100))
        r.row('set_rows', 'Language')
        r.choose('中文')
        self.assertEqual(r.text('nav_protocols'), '全部协议')
        r.call('robot.configure', key='language', value='English')
        r.row('set_rows', 'Network settings')
        self.assertIn('network', r.frame()['ui']['unsupported'])
        r.row('set_rows', 'Touchscreen sleep')
        r.choose('3 minutes')
        r.wait(181)
        self.assertEqual(r.screen()[0], 'sleep')
        self.assertTrue(r.touch(500, 300))                                                    # one tap wakes the screen
        self.assertEqual(r.screen()[0], 'ui')

    def test_device_reset_files_and_usb(self):
        r = Rig()
        r.open_setup()
        r.call('run.confirm_parameters')
        r.tap('su_play')
        r.serve(8)
        r.tap('end_msg'), r.tap('sm_dash')
        r.tap('nav_settings')
        r.row('set_rows', 'File manager')
        r.row('file_list', 'run record Serial transfer')
        r.ov('Download')
        self.assertEqual(r.overlay()['title'], 'File manager')                                 # no USB drive
        r.ov('OK')
        r.h.host_set_usb_drive(INST, [])
        r.row('file_list', 'run record Serial transfer')
        r.ov('Download')
        self.assertEqual(r.state('usb.drive')['files'], ['run_1'])
        r.tap('file_back')
        r.row('set_rows', 'Device reset')
        r.row('rst_rows', 'Clear')
        r.ov('Run history'), r.ov('Labware offsets'), r.ov('OK')
        r.tap('rst_go')
        self.assertEqual((r.state('run.history'), r.state('labware.offsets')), ([], {}))
        self.assertEqual(len(r.state('protocols.list')), 1)


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        a, b = Rig(), Rig()
        a.open_setup()
        a.tap('su_params'), a.row('prm_list', 'Volume (uL)'), a.type('150'), a.tap('prm_confirm'), a.tap('su_play')
        b.call('run.setup', protocol_id='p1')
        b.call('run.set_parameter', name='volume_ul', value=150.0)
        b.call('run.confirm_parameters')
        b.call('run.play')
        sa, sb = a.h.debug_snapshot()['instances'][INST], b.h.debug_snapshot()['instances'][INST]
        self.assertEqual(json.dumps(sa['state'], sort_keys=True), json.dumps(sb['state'], sort_keys=True))
        a.serve(8), b.serve(8)
        sa, sb = a.h.debug_snapshot()['instances'][INST], b.h.debug_snapshot()['instances'][INST]
        self.assertEqual(json.dumps(sa['state'], sort_keys=True), json.dumps(sb['state'], sort_keys=True))


class Rules(unittest.TestCase):
    def test_refusals_and_access(self):
        r = Rig()
        caps = r.A.describe(instance_id=INST)['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.touch', 'panel.switch', 'panel.capture'})
        self.assertEqual(r.call('panel.frame', expect=('rejected',))['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('truth.', json.dumps(r.A.describe(instance_id=INST)))
        r.open_setup()
        self.assertFalse(r.tap('su_play'))                                                    # play inactive: parameters not confirmed
        r.call('run.confirm_parameters')
        r.tap('su_play')
        self.assertEqual(r.call('robot.home', expect=('rejected',))['error']['code'], 'BUSY')
        self.assertEqual(r.call('deck.configure', expect=('rejected',), slot='B1', fixture='waste_chute')['error']['code'], 'BUSY')
        r.call('run.stop')
        self.assertEqual(r.call('deck.configure', expect=('rejected',), slot='B1', fixture='waste_chute')['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(r.call('quick_transfer.create', expect=('rejected',), spec={'name': 'x', 'source_wells': ['dst_1'], 'destination_wells': ['dst_2'],
                                                                                   'volume_m3': 1e-7})['error']['code'], 'INVALID_ARGUMENT')


class Callers(unittest.TestCase):
    """Lead rule 2026-10-19: a run belongs to the caller that set it up (touchscreen gestures act as the gesturing session); another caller can
    neither stop, pause, restart / shut down (touchscreen) nor move the robot under it, on the touchscreen or the direct path; the refusal shows
    on the screen; the evaluator is unrestricted.  The rear power switch is a hard switch: never refused, the run stops as at a power loss."""
    def test_run_belongs_to_the_caller_that_set_it_up(self):
        r = Rig()
        r.h.register_principal('agent_b', visibility='agent')
        B = r.h.session('agent_b')
        r.open_setup()                                                                    # A sets the run up on the touchscreen
        self.assertEqual(r.h._instances[INST]['ts']['run']['owner'], 'agent')
        self.assertEqual(r.call('run.confirm_parameters', sess=B, expect=('rejected',))['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(r.call('run.setup', sess=B, expect=('rejected',), protocol_id=PROTOCOL['protocol_id'])['error']['code'], 'ACCESS_DENIED')
        r.call('run.confirm_parameters')
        r.tap('su_play')
        r.serve(2)
        snap = lambda: json.dumps([r.h._instances[INST]['ts']['run'], r.state('motion.x_m', 'power.enabled')], sort_keys=True, default=str)   # noqa: E731
        before = snap()
        for cap, args in (('run.pause', {}), ('run.stop', {}), ('robot.restart', {}), ('robot.shutdown', {}),
                          ('motion.move', dict(x_m=0.0, y_m=0.0, z_m=0.2))):
            self.assertEqual(r.call(cap, sess=B, expect=('rejected',), **args)['error']['code'], 'ACCESS_DENIED', cap)
        self.assertEqual(snap(), before)
        x, y, w, h = r.widgets()['rn_stop']['rect']                                       # B on the touchscreen: Stop, Cancel run
        r.call('panel.touch', sess=B, display_id='touchscreen', x=x + w // 2, y=y + h // 2)
        ov = r.overlay()
        b = next(b for b in ov['buttons'] if b['label'] == 'Cancel run')
        self.assertFalse(r.call('panel.touch', sess=B, display_id='touchscreen', x=b['rect'][0] + 5, y=b['rect'][1] + 5)['accepted'])
        self.assertEqual((snap(), r.overlay()['title'], r.overlay()['lines']), (before, 'Robot busy', ["Another user's run is in progress."]))
        r.ov('OK')
        r.tap('rn_play')                                                                  # A pauses and resumes its own run
        self.assertEqual(r.run('status'), 'paused')
        r.call('run.play')
        r.call('run.pause', sess=r.E)                                                     # the evaluator is unrestricted
        self.assertEqual(r.run('status'), 'paused')
        r.call('run.play')
        self.assertTrue(r.call('panel.switch', sess=B, input_id='power_switch', position='off')['accepted'])   # hard switch: physical
        self.assertEqual((r.state('power.enabled'), r.state('run.history')[-1]['status']), (False, 'stopped'))


class OffsetsCameraRestart(unittest.TestCase):
    """Sync audit 2026-10-19: labware offsets act on the run motion; Home gantry on restart; the built-in camera's image files."""
    def setup_run(self, r, jog_x=0.0):
        r.call('run.setup', protocol_id='p1')
        r.call('run.confirm_parameters')
        r.call('run.lpc', action='start')                                                   # above the source plate
        if jog_x:
            pos = {k: r.state('motion.' + k) for k in ('x_m', 'y_m', 'z_m')}
            r.call('motion.move', **dict(pos, x_m=pos['x_m'] + jog_x))
        r.call('run.lpc', action='save')
        r.call('run.lpc', action='save')                                                    # destination plate, no jog

    def test_zero_jog_saves_zero_and_the_run_lands_on_the_labware(self):
        r = Rig(offsets=False)
        self.setup_run(r)
        self.assertEqual(r.state('labware.offsets'), {'source plate': P(0.0, 0.0, 0.0), 'destination plate': P(0.0, 0.0, 0.0)})
        self.assertTrue(r.run('offsets_applied'))
        r.call('run.play')
        r.serve(8)
        self.assertEqual(r.state('run.history')[-1]['status'], 'succeeded')
        self.assertAlmostEqual(r.volume('dst_1'), 1e-7)

    def test_a_saved_offset_moves_the_run_beside_the_labware(self):
        r = Rig(offsets=False)
        self.setup_run(r, jog_x=0.001)
        self.assertAlmostEqual(r.state('labware.offsets')['source plate']['x_m'], 0.001)
        r.call('run.play')
        r.serve(4)
        self.assertEqual((r.run('status'), r.run('error')['type']), ('awaiting_recovery', 'general'))
        self.assertAlmostEqual(r.state('motion.x_m'), 0.001)                                 # src_1 at x 0 plus the offset: the aspirate is refused
        self.assertAlmostEqual(r.volume('src_1'), 2e-6)

    def test_home_gantry_on_restart(self):
        r = Rig()
        home = rt.FLEX_HOME_POSITION
        r.call('motion.move', **P(0.0, 0.0, 0.1))
        r.call('robot.restart')
        self.assertEqual({k: r.state('motion.' + k) for k in home}, home)                     # the setting is on (factory)
        r.wait(6.0)
        r.call('robot.configure', key='home_on_restart', value=False)
        r.call('motion.move', **P(0.0, 0.0, 0.1))
        r.call('robot.restart')
        self.assertEqual(r.state('motion.z_m'), 0.1)
        r.wait(6.0)
        r.switch('off'), r.switch('on')
        self.assertEqual(r.state('motion.z_m'), 0.1)                                          # off: the power switch does not home either
        r.wait(3.0)
        r.call('robot.configure', key='home_on_restart', value=True)
        r.switch('off'), r.switch('on')
        self.assertEqual({k: r.state('motion.' + k) for k in home}, home)

    def test_camera_images_and_setup_row(self):
        r = Rig()
        r.h.host_inject_error(INST, kind='general')
        r.open_setup()
        r.tap('su_camera')
        self.assertEqual((r.screen()[1], r.text('scam_error')), ('setup_camera', 'On-error image capture: Off'))
        r.call('robot.configure', key='camera_on_error', value=True)
        self.assertEqual(r.text('scam_error'), 'On-error image capture: On')
        r.tap('scam_back')
        r.call('run.confirm_parameters')
        r.call('run.play')
        r.serve(2)
        imgs = [f for f in r.state('files.records') if f['kind'] == 'image']
        self.assertEqual([(f['trigger'], f['error']) for f in imgs], [('error', 'general')])
        q = Rig()
        steps = dict(PROTOCOL, protocol_id='p2', name='Photo', parameters=[], commands=[{'cmd': 'capture_image'}, {'cmd': 'comment', 'text': 'x'}, {'cmd': 'capture_image'}])
        q.h.host_store_protocol(INST, steps)
        q.call('robot.configure', key='camera_photos', value=True)
        q.call('run.setup', protocol_id='p2')
        q.call('run.play')
        q.serve(4)
        self.assertEqual([f['trigger'] for f in q.state('files.records') if f['kind'] == 'image'], ['step', 'step'])


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        self.assertTrue((a.h.panel(INST).render('touchscreen') == b.h.panel(INST).render('touchscreen')).all())
        OUT.mkdir(parents=True, exist_ok=True)
        a.tap('nav_protocols')
        shots = {'home': a.h.panel(INST).save_png('touchscreen', OUT / 'home.png')}
        a.row('plist', 'Serial transfer'), a.tap('det_start')
        shots['menu'] = a.h.panel(INST).save_png('touchscreen', OUT / 'menu.png')
        a.call('run.confirm_parameters'), a.tap('su_play'), a.serve(2)
        shots['running'] = a.h.panel(INST).save_png('touchscreen', OUT / 'running.png')
        c = Rig(src=5e-8)
        c.open_setup(), c.call('run.confirm_parameters'), c.tap('su_play'), c.serve(3)
        shots['error'] = c.h.panel(INST).save_png('touchscreen', OUT / 'error.png')
        shots['status_light'] = c.h.panel(INST).save_png('status_light', OUT / 'status_light.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_session(self):
        r = Rig()
        r.h.panel_capture_dir = str(OUT / 'captures')
        res = r.call('panel.capture', display_id='touchscreen')
        self.assertEqual(res['image']['sha256'], hashlib.sha256(Path(res['image']['path']).read_bytes()).hexdigest())

    def test_spec_and_contract(self):
        meta = rt.export_opentrons_flex_contract_metadata(panel=True)
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities'])), [])
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for sid, slot in meta['state_slots'].items():
            self.assertEqual(c['state_slots'][sid]['value_schema'], slot['value_schema'], sid)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['ui_class'], 'touch_gui')
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('flex_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 1:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
