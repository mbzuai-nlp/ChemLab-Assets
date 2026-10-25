"""Opentrons Flex runtime fixes of 2026-10-22 (found by the chemlab pilot), on scripts/basic40_runtime_automation.py:
python3 -m pytest tests/test_flex_runtime_fixes.py -q

(a) a protocol volume given as {'param': ..., 'unit': 'uL'} is converted to m^3; bad references are refused when the protocol is stored,
    an undefined parameter when the run starts; (b) one long host clock jump runs every command due in it; (c) read() says why a slot is empty."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_panel_ui_opentrons_flex_single_5_1000ul import INST, PROTOCOL, Rig  # noqa: E402


def protocol(pid, commands, parameters=PROTOCOL['parameters']):
    return dict(PROTOCOL, protocol_id=pid, name='Fix ' + pid, parameters=parameters, commands=commands)


def start(r, pid):
    r.call('run.setup', protocol_id=pid)
    r.call('run.confirm_parameters')
    return r.call('run.play', expect=('succeeded', 'accepted', 'rejected'))


class ParameterUnits(unittest.TestCase):
    def test_volume_in_ul_is_converted(self):
        r = Rig()
        r.h.host_store_protocol(INST, protocol('ul', [
            {'cmd': 'pick_up_tip', 'tip_id': 'tip_1'}, {'cmd': 'aspirate', 'container_id': 'src_1', 'volume_m3': {'param': 'volume_ul', 'unit': 'uL'}},
            {'cmd': 'dispense', 'container_id': 'dst_1', 'volume_m3': {'param': 'volume_ul', 'unit': 'uL'}}, {'cmd': 'drop_tip', 'slot_id': 'A1'}]))
        start(r, 'ul')
        r.serve(8)
        self.assertEqual(r.state('run.history')[-1]['status'], 'succeeded')
        self.assertAlmostEqual(r.volume('dst_1'), 100e-9)                              # default 100 uL

    def test_bad_references_are_refused_when_stored(self):
        r = Rig()
        for ref, key in (({'param': 'volume_ul', 'unit': 'gal'}, 'volume_m3'), ({'param': 'volume_ul', 'unit': 'uL'}, 'container_id'),
                         ({'param': 'volume_ul', 'scale': '1e-9'}, 'volume_m3'), ({'param': 'volume_ul', 'unit': 'uL', 'scale': 1e-9}, 'volume_m3')):
            cmd = {'cmd': 'aspirate', 'container_id': 'src_1', 'volume_m3': 1e-7}
            cmd[key] = ref
            with self.assertRaises(ValueError, msg=ref):
                r.h.host_store_protocol(INST, protocol('bad', [cmd]))

    def test_undefined_parameter_refuses_the_start(self):
        r = Rig()
        r.h.host_store_protocol(INST, protocol('undef', [{'cmd': 'aspirate', 'container_id': 'src_1', 'volume_m3': {'param': 'volume_ul', 'scale': 1e-9}}],
                                               parameters=[]))
        out = start(r, 'undef')
        self.assertEqual((out['status'], out['error']['code']), ('rejected', 'INVALID_ARGUMENT'))
        self.assertEqual(r.run('status'), 'setup')


class ClockJump(unittest.TestCase):
    def test_one_long_jump_runs_every_due_command(self):
        r = Rig()
        r.h.host_store_protocol(INST, protocol('jump', [{'cmd': 'comment', 'text': 'start'}, {'cmd': 'delay', 's': 30.0}]
                                               + [{'cmd': 'comment', 'text': 'step %d' % i} for i in range(5)]))
        start(r, 'jump')
        r.wait(20.0)                                                                   # inside the 30 s delay
        self.assertEqual((r.run('status'), r.run('index')), ('running', 2))
        r.wait(100.0)
        rec = r.state('run.history')[-1]
        self.assertEqual((rec['status'], rec['steps_done'], rec['n_steps']), ('succeeded', 7, 7))

    def test_jump_matches_small_steps(self):
        a, b = Rig(), Rig()
        for r in (a, b):
            r.h.host_store_protocol(INST, protocol('jump', [{'cmd': 'delay', 's': 3.0}] + [{'cmd': 'comment', 'text': 'step %d' % i} for i in range(4)]))
            start(r, 'jump')
        a.wait(5.0)
        for _ in range(10):
            b.wait(0.5)
        self.assertEqual(a.run('index'), b.run('index'))


class EmptySlotReasons(unittest.TestCase):
    def test_reason_names_the_missing_thing(self):
        r = Rig()
        o = r.A.read(instance_id=INST, state_ids=['tip.attached_id', 'run.state', 'usb.drive'])['observations']
        self.assertEqual({k: (v['quality'], v['reason']) for k, v in o.items()},
                         {'tip.attached_id': ('unavailable', 'No tip is attached.'), 'run.state': ('unavailable', 'No protocol run is set up.'),
                          'usb.drive': ('unavailable', 'No USB drive is inserted.')})


if __name__ == '__main__':
    unittest.main()
