"""manual-remote tests for CHEM-182 vici_euta (panel_ui.json + scripts/asset_runtime/_panel_ui.py on scripts/basic40_runtime_automation.py):
python3 -m pytest tests/test_panel_ui_vici_euta_2sd6mwe.py -q

Every key goes through the runtime API (panel.press on UP, DOWN, HOME/SETUP); the host confirms each arrival as before.  Checks look at the
runtime state and at the 4-line display (frame lines, highlighted line, rendered pixels).  Themes: the six key procedures of the menu survey
(step, home, configure N positions, clockwise rotation, leave setup / factory mode, baud rate), the other setup functions (two position with
and without stops, learning the stops, offset limits, device ID, legacy mode, information screens), panel = direct capability, refusals
(moving, unknown position, unsupported interfaces), render determinism and the PNGs, contract entries."""
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

INST = 'vici_panel_01'
OUT = ROOT / 'outputs/panel_ui/vici_euta_2sd6mwe'
ASSET = rt.VICI_EUTA_ASSET_DIR
SPEC = rt._vr_panel()['spec']
SURVEY_TASKS = ['Move the selector one position', 'Return to position 1', 'Configure for a multiposition valve with N positions', 'Set clockwise rotation',
                'Leave factory or setup mode entered by mistake', 'Set the RS-232 baud rate']
DONE_TASKS = set()


class Rig:
    def __init__(self, home=True):
        self.calls = []
        self.v = rt.ViciEutaValveRuntime(INST, scene_adapter=lambda i, p: self.calls.append(p))
        self.n = 0
        self.t = 0.0
        if home:
            self.key('home')
            self.arrive()

    def call(self, cap, expect=('succeeded',), **args):
        self.n += 1
        r = self.v.invoke(capability_id=cap, arguments=args, request_id=f'p{self.n}')
        assert r['status'] in expect, (cap, args, r['status'], r['error'])
        return r['result'] if r['status'] == 'succeeded' else r

    def key(self, k, d=0.1, held=None):
        args = dict(input_id=k, duration_s=d)
        if held:
            args['held'] = held
        return self.call('panel.press', **args)['accepted']

    def arrive(self):
        """The host confirms the pending move."""
        target = self.state('valve.target_position')
        self.t += 1.0
        self.n += 1
        r = self.v.host_arrive(position=target, world_time_s=self.t, request_id=f'a{self.n}')
        assert r['status'] == 'succeeded', r

    def frame(self):
        return self.v.panel().frame()

    def lines(self):
        return [x.rstrip() for x in self.frame()['displays']['remote_display']['lines']]

    def highlighted(self):
        d = self.frame()['displays']['remote_display']
        return [x.strip() for x, inv in zip(d['lines'], d['inverse']) if inv]

    def screen(self):
        return self.frame()['screen']

    def state(self, sid):
        return self.v.read(state_ids=[sid])['observations'][sid]['value']

    def setup(self):
        self.key('home', 5.0)

    def to_main(self):
        """UP until the Main Menu shows (UP is back on the first line of a list)."""
        for _ in range(60):
            if self.lines()[0] == 'Main Menu':
                return
            self.key('up')
        raise AssertionError(self.lines())

    def escape(self):
        for _ in range(60):
            if self.screen() == 'normal':
                return
            self.key('up')
        raise AssertionError(self.lines())

    def go(self, label):
        """Move the highlight down to the line with this label (from the top of the list) and enter it."""
        for _ in range(40):
            if self.highlighted() and self.highlighted()[0].startswith(label):
                return self.key('home')
            self.key('down')
        raise AssertionError((label, self.lines()))


class KeyProcedures(unittest.TestCase):
    def test_move_the_selector_one_position(self):
        r = Rig()
        self.assertEqual(r.lines()[:2], ['Position', '1'])
        self.assertTrue(r.key('up'))
        self.assertEqual((r.state('valve.target_position'), r.lines()[2]), (2, 'Moving to 2'))
        self.assertFalse(r.key('up'))                                                     # a move is pending: ignored
        r.arrive()
        r.key('down')
        r.arrive()
        r.key('down')                                                                     # 1 -> 6 (wraps)
        r.arrive()
        self.assertEqual((r.state('valve.position'), r.state('route.connected_port_id'), r.lines()[1]), (6, 'port_6', '6'))
        r.key('up')
        r.arrive()
        self.assertEqual(r.state('valve.position'), 1)                                   # 6 -> 1
        DONE_TASKS.add('Move the selector one position')

    def test_return_to_position_1(self):
        r = Rig()
        r.key('up'), r.arrive(), r.key('up'), r.arrive()
        self.assertTrue(r.key('home'))
        r.arrive()
        self.assertEqual(r.state('valve.position'), 1)
        self.assertFalse(r.key('home'))                                                   # already in position 1: nothing happens
        DONE_TASKS.add('Return to position 1')

    def test_configure_n_positions(self):
        r = Rig()
        r.setup()
        self.assertEqual((r.screen(), r.lines()[0], r.highlighted()), ('setup', 'Main Menu', ['1. Valve Setup']))
        r.go('1. Valve Setup')
        self.assertEqual(r.lines()[0], 'Valve Menu')
        r.go('2. Multiposition')
        self.assertEqual(r.lines()[0], 'Multiposition Menu')
        r.go('1. Set Positions')
        self.assertEqual(r.lines()[:2], ['Set positions:', '6'])
        for _ in range(2):
            r.key('up')
        r.key('home')
        self.assertEqual((r.state('actuator.positions'), r.state('actuator.mode')), (10, 'multiposition'))
        DONE_TASKS.add('Configure for a multiposition valve with N positions')

    def test_set_clockwise_rotation(self):
        r = Rig()
        r.setup()
        r.go('1. Valve Setup'), r.go('2. Multiposition'), r.go('2. Move Direction')
        self.assertEqual(r.lines()[0], 'Move Direction')
        r.go('2. Clockwise')
        self.assertEqual((r.state('actuator.direction'), r.lines()[0]), ('clockwise', 'Multiposition Menu'))
        DONE_TASKS.add('Set clockwise rotation')

    def test_leave_factory_or_setup_mode(self):
        r = Rig()
        r.setup()
        self.assertTrue(r.key('up'))                                                      # UP on the first line: back to normal operation
        self.assertEqual(r.screen(), 'normal')
        self.assertTrue(r.key('up', held=['down']))                                       # both arrows: the factory mode
        self.assertEqual((r.screen(), r.lines()[0]), ('factory', 'Factory Mode'))
        self.assertFalse(r.key('down'))
        r.key('up')
        self.assertEqual(r.screen(), 'normal')
        self.assertEqual(r.state('valve.position'), 1)                                   # nothing moved
        DONE_TASKS.add('Leave factory or setup mode entered by mistake')

    def test_set_the_rs232_baud_rate(self):
        r = Rig()
        r.setup()
        r.go('2. Interface Setup')
        self.assertEqual(r.lines()[0], 'Interface Menu')
        r.go('1. RS232')
        self.assertEqual(r.lines()[0], 'RS232/485 Menu')
        r.go('1. Set Baudrate')
        self.assertEqual(r.lines()[0], 'Baud Rate')
        self.assertTrue(any('2. 9600' in x and '<' in x for x in r.lines()))              # the current rate marked
        r.go('3. 19200')
        self.assertEqual(r.state('interface.baud_rate'), 19200)
        r.go('1. Set Baudrate')
        r.go('6. 115200')                                                                 # scrolled across the extra screen
        self.assertEqual(r.state('interface.baud_rate'), 115200)
        DONE_TASKS.add('Set the RS-232 baud rate')


class SetupFunctions(unittest.TestCase):
    def test_two_position_without_stops_and_ab_keys(self):
        r = Rig()
        r.setup()
        r.go('1. Valve Setup'), r.go('1. Two position')
        self.assertEqual(r.lines()[0], 'With Stops?')
        r.go('2. No')
        self.assertEqual(r.lines()[0], 'Set Ports:')
        r.key('home')
        self.assertEqual((r.state('actuator.mode'), r.state('actuator.positions')), ('two_position_no_stops', 6))
        r.escape()                                                                        # UP back up to normal operation
        self.assertEqual((r.screen(), r.lines()[1], r.lines()[3]), ('normal', 'A', 'Two position'))
        self.assertTrue(r.key('up'))                                                      # to B
        r.arrive()
        self.assertEqual(r.lines()[1], 'B')
        self.assertFalse(r.key('up'))                                                     # already in B: nothing happens
        r.key('home')                                                                     # HOME: to A
        r.arrive()
        self.assertEqual(r.state('valve.position'), 1)

    def test_with_stops_learns_the_stops(self):
        r = Rig()
        r.key('up'), r.arrive()
        r.setup()
        r.go('1. Valve Setup'), r.go('1. Two position'), r.go('1. Yes')
        self.assertEqual(r.lines()[:3], ['Press the Home', 'Button When the', 'valve is ready'])
        r.key('home')
        self.assertEqual((r.state('actuator.mode'), r.state('actuator.stops_learned'), r.state('valve.target_position')), ('two_position_stops', True, 1))
        r.arrive()
        self.assertEqual(r.state('valve.position'), 1)                                   # position A after learning

    def test_offset_limit_device_id_legacy_and_information(self):
        r = Rig()
        r.call('actuator.configure', key='positions', value=40)
        r.setup()
        r.go('1. Valve Setup'), r.go('2. Multiposition'), r.go('3. Set Offset')
        for _ in range(70):
            r.key('up')
        r.key('home')
        self.assertEqual(r.state('actuator.offset'), 56)                                 # 1 to 96 - 40
        r.to_main()
        r.go('2. Interface Setup'), r.go('3. RS485'), r.go('2. Set ID')
        r.go('Z')
        self.assertEqual(r.state('interface.device_id'), 'Z')
        r.go('3. Set Legacy Mode'), r.go('2. On')
        self.assertTrue(r.state('interface.legacy_mode'))
        r.to_main()
        r.go('2. Interface Setup')
        self.assertFalse(r.go('2. USB'))
        self.assertIn('USB', r.frame()['ui']['unsupported'])
        r.to_main()
        r.go('3. Information'), r.go('2. Valve')
        self.assertEqual(r.lines(), ['Valve Setting', 'Mode: 3', 'Ports: 40', 'Press Mode Button'])
        r.key('home')
        self.assertEqual(r.highlighted(), ['2. Valve'])
        r.key('up')
        r.key('home')
        self.assertEqual(r.lines()[0], 'Display Firmware')


class MoveDirection(unittest.TestCase):
    def test_direction_sets_the_path_and_the_move_time(self):
        r = Rig()                                                                         # at position 1, Shortest
        r.call('valve.select_position', position=5)
        self.assertEqual((r.state('valve.move_path'), r.state('valve.move_direction'), r.state('valve.move_time_s')), ([6, 5], 'negative', None))
        r.arrive()
        r.v.host_configure_motion(seconds_per_position=0.1, request_id='motion')
        r.call('actuator.configure', key='direction', value='counterclockwise')         # positive only: 5 -> 6 -> 1 -> 2 -> 3 -> 4
        r.call('valve.select_position', position=4)
        self.assertEqual((r.state('valve.move_path'), r.state('valve.move_direction')), ([6, 1, 2, 3, 4], 'positive'))
        self.assertAlmostEqual(r.state('valve.move_time_s'), 0.5)
        r.arrive()
        r.call('actuator.configure', key='direction', value='clockwise')                 # through the menu: UP takes the long way (4 -> 5 negative)
        r.key('up')
        self.assertEqual((r.state('valve.target_position'), r.state('valve.move_path')), (5, [3, 2, 1, 6, 5]))
        self.assertAlmostEqual(r.state('valve.move_time_s'), 0.5)
        r.arrive()
        self.assertEqual(r.state('route.connected_port_id'), 'port_5')
        r.call('actuator.configure', key='mode', value='two_position_no_stops')
        r.call('valve.select_position', position=1)
        self.assertEqual((r.state('valve.move_path'), r.state('valve.move_direction')), ([1], None))
        fresh = Rig(home=False)
        fresh.key('home')                                                                 # start position unknown: no path
        self.assertEqual((fresh.state('valve.move_path'), fresh.state('valve.move_time_s')), (None, None))
        bad = r.v.host_configure_motion(seconds_per_position=0, request_id='bad')
        self.assertEqual(bad['error']['code'], 'INVALID_ARGUMENT')
        self.assertNotIn('host.configure_motion', r.v.describe()['capabilities'])
        self.assertEqual(r.call('host.configure_motion', expect=('rejected',), seconds_per_position=0.1)['error']['code'], 'UNKNOWN_CAPABILITY')


class Equivalence(unittest.TestCase):
    def test_panel_equals_direct_commands(self):
        a, b = Rig(), Rig()
        a.setup(), a.go('1. Valve Setup'), a.go('2. Multiposition'), a.go('2. Move Direction'), a.go('2. Clockwise')
        b.call('actuator.configure', key='direction', value='clockwise')
        a.escape()
        a.key('up')
        b.call('valve.select_position', position=2)
        sa, sb = a.v.debug_snapshot(), b.v.debug_snapshot()
        self.assertEqual((sa['state'], sa['world_time_s']), (sb['state'], sb['world_time_s']))


class Rules(unittest.TestCase):
    def test_refusals_and_unknown_position(self):
        r = Rig(home=False)
        self.assertEqual(r.lines()[1], '--')
        self.assertFalse(r.key('up'))                                                     # position unknown: only HOME
        self.assertTrue(r.key('home'))
        self.assertFalse(r.key('home'))
        r.arrive()
        r.key('up')
        self.assertEqual(r.call('actuator.configure', expect=('rejected',), key='positions', value=8)['error']['code'], 'BUSY')
        r.arrive()
        self.assertEqual(r.call('actuator.configure', expect=('rejected',), key='positions', value=7)['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(r.call('actuator.learn_stops', expect=('rejected',))['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(r.call('panel.frame', expect=('rejected',))['error']['code'], 'ACCESS_DENIED')
        caps = r.v.describe()['capabilities']
        self.assertEqual({k for k in caps if k.startswith('panel.')}, {'panel.press', 'panel.capture'})
        r.call('actuator.configure', key='positions', value=8)
        r.key('down')                                                                     # 2 -> 1
        r.arrive()
        r.key('down')                                                                     # 1 -> 8: beyond the installed 6-position valve
        self.assertEqual((r.lines()[3], r.frame()['ui']['message']['text']), ('Not allowed', 'Not allowed'))
        self.assertEqual((r.state('valve.position'), r.state('valve.target_position')), (1, 1))
        r.call('actuator.configure', key='positions', value=4)
        for _ in range(3):
            r.key('down'), r.arrive()                                                     # 1 -> 4 -> 3 -> 2: wraps at the set positions
        self.assertEqual(r.state('valve.position'), 2)
        self.assertEqual(r.call('valve.select_position', expect=('rejected',), position=5)['error']['code'], 'PRECONDITION_FAILED')
        r.call('actuator.configure', key='mode', value='two_position_no_stops')
        self.assertEqual(r.call('valve.select_position', expect=('rejected',), position=3)['error']['code'], 'PRECONDITION_FAILED')
        self.assertEqual(r.call('valve.select_position', position=1)['valve.target_position'], 1)


class OffsetNumbering(unittest.TestCase):
    """Setup-menu offset SO (manual p.16, p.22): the actuator answers serial position numbers SO .. SO+NP-1 and counts SO as the valve's
    position 1; the remote keys keep stepping the valve's own positions."""
    def test_panel_offset_renumbers_direct_selection(self):
        r = Rig()
        self.assertEqual(r.state('valve.position_number'), 1)                            # factory SO 1: the number is the position
        r.setup()
        r.go('1. Valve Setup'), r.go('2. Multiposition'), r.go('3. Set Offset')
        for _ in range(9):
            r.key('up')
        r.key('home')
        r.escape()
        self.assertEqual((r.state('actuator.offset'), r.state('valve.position'), r.state('valve.position_number')), (10, 1, 10))
        before = r.v.debug_snapshot()
        for n, code in ((3, 'PRECONDITION_FAILED'), (9, 'PRECONDITION_FAILED'), (16, 'INVALID_ARGUMENT')):   # below SO; beyond the 6-position valve
            self.assertEqual(r.call('valve.select_position', expect=('rejected',), position=n)['error']['code'], code, n)
        self.assertEqual(before, r.v.debug_snapshot())
        self.assertEqual(r.call('valve.select_position', position=12)['valve.target_position'], 3)
        r.arrive()
        self.assertEqual((r.state('valve.position'), r.state('route.connected_port_id'), r.state('valve.position_number'), r.lines()[1]),
                         (3, 'port_3', 12, '3'))
        r.call('valve.select_position', position=15)                                     # SO + NP - 1: the valve's position 6
        r.arrive()
        self.assertEqual((r.state('valve.position'), r.state('valve.position_number')), (6, 15))
        self.assertTrue(r.key('home'))                                                    # the remote's HOME: the valve's position 1
        r.arrive()
        self.assertEqual((r.state('valve.position'), r.state('valve.position_number')), (1, 10))
        r.key('up')
        r.arrive()
        self.assertEqual((r.state('valve.position'), r.state('valve.position_number')), (2, 11))
        r.call('actuator.configure', key='mode', value='two_position_no_stops')        # SO is a multiposition setting: A / B stay 1 / 2
        self.assertEqual(r.call('valve.select_position', position=1)['valve.target_position'], 1)
        r.arrive()
        self.assertEqual(r.state('valve.position_number'), 1)

    def test_replay_keeps_the_position_it_meant(self):
        r = Rig()
        r.call('actuator.configure', key='offset', value=5)
        first = r.v.invoke(capability_id='valve.select_position', arguments={'position': 7}, request_id='sel')
        self.assertEqual(first['result']['valve.target_position'], 3)
        r.arrive()
        r.call('actuator.configure', key='offset', value=1)
        self.assertEqual(r.v.invoke(capability_id='valve.select_position', arguments={'position': 7}, request_id='sel'), first)
        self.assertEqual(r.v.invoke(capability_id='valve.select_position', arguments={'position': 3}, request_id='sel')['error']['code'], 'REQUEST_CONFLICT')


class RenderAndContract(unittest.TestCase):
    def test_render_determinism_and_pngs(self):
        a, b = Rig(), Rig()
        self.assertTrue((a.v.panel().render('remote_display') == b.v.panel().render('remote_display')).all())
        OUT.mkdir(parents=True, exist_ok=True)
        shots = {'home': a.v.panel().save_png('remote_display', OUT / 'home.png')}
        a.setup(), a.go('1. Valve Setup'), a.go('2. Multiposition')
        shots['menu'] = a.v.panel().save_png('remote_display', OUT / 'menu.png')
        c = Rig()
        c.call('actuator.configure', key='positions', value=8)
        c.key('down')                                                                     # 1 -> 8: beyond the installed 6-position valve
        shots['error'] = c.v.panel().save_png('remote_display', OUT / 'error.png')
        for name, s in shots.items():
            self.assertEqual(hashlib.sha256((OUT / f'{name}.png').read_bytes()).hexdigest(), s['sha256'])
        (OUT / 'renders.json').write_text(json.dumps(shots, indent=2, sort_keys=True).replace(str(ROOT) + '/', '') + '\n')

    def test_capture_through_the_runtime(self):
        r = Rig()
        r.v.panel_capture_dir = str(OUT / 'captures')
        res = r.call('panel.capture', display_id='remote_display')
        self.assertEqual(res['image']['sha256'], hashlib.sha256(Path(res['image']['path']).read_bytes()).hexdigest())

    def test_spec_and_contract(self):
        meta = rt.export_vici_euta_contract_metadata(panel=True)
        self.assertEqual(pu.check_spec(SPEC, set(meta['capabilities'])), [])
        c = json.loads((ASSET / 'asset.contract.json').read_text())
        self.assertEqual(c['asset_version'], meta['asset_version'])
        for k, v in {**meta['capabilities'], **meta['panel_capabilities']}.items():
            for key in ('input_schema', 'output_schema', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        for sid, slot in meta['state_slots'].items():
            self.assertEqual(c['state_slots'][sid]['value_schema'], slot['value_schema'], sid)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['ui_class'], 'text_menu')
        self.assertEqual(c['artifacts']['panel_ui']['sha256'], hashlib.sha256((ASSET / 'panel_ui.json').read_bytes()).hexdigest())
        for aid in ('panel_engine', 'panel_engine_tests'):
            self.assertIsNone(c['artifacts'][aid]['sha256'], aid)
        spec = importlib.util.spec_from_file_location('vici_panel_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.assertEqual(tool.validate_contract(c, ROOT, check_files=False), [])


class ZCoverage(unittest.TestCase):
    def test_all_survey_procedures_done(self):
        if len(DONE_TASKS) > 1:
            self.assertEqual(set(SURVEY_TASKS), DONE_TASKS)


if __name__ == '__main__':
    unittest.main()
