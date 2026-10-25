"""The gate: units, ranges, preconditions, error format, exit codes, dry-run, job ids, lock, request ids, log, hidden
capabilities, read-back, agent/evaluator split."""
import json
import os
import tempfile
import unittest

from helpers import PLATE_PREP, make_core, run, run_json, snapshot
from chemlab.core import Ctx
from chemlab.errors import UndefinedExecutionError


def vol(backend, cid):
    return next(c['volume_m3'] for c in backend.evaluator_state()['truth.containers'] if c['container_id'] == cid) * 1e9


class Base(unittest.TestCase):
    def setUp(self):
        self.backend, self.core = make_core()

    def ok(self, cmd, caller='agent'):
        r = run(self.core, cmd, caller)
        self.assertEqual(r.exit_code, 0, '%s -> %s%s' % (cmd, r.out, r.err))
        return r

    def err(self, cmd, code, exit_code, caller='agent'):
        n_calls = len(self.backend_calls())
        before = snapshot(self.backend)
        c, d = run_json(self.core, cmd, caller)
        self.assertEqual(c, exit_code, '%s -> %s' % (cmd, d))
        self.assertFalse(d['ok'])
        self.assertEqual(d['error']['code'], code, d)
        if d['error']['instrument_changed'] == 'no':
            self.assertEqual(snapshot(self.backend), before, 'instrument changed although the error says no: %s' % cmd)
        return d['error'], len(self.backend_calls()) - n_calls

    def backend_calls(self):
        return [c for e in self.core.log.entries for c in e['backend_calls']]


class UnitsAndRanges(Base):
    def test_volume_out_of_range_is_validation_and_sends_nothing(self):
        self.ok('run flex pick-up-tip')
        e, calls = self.err('run flex aspirate --volume 1500uL --from reservoir:A1', 'VOLUME_OUT_OF_RANGE', 2)
        self.assertEqual(calls, 0)
        self.assertEqual(e['sila_class'], 'ValidationError')
        e, calls = self.err('run flex aspirate --volume 2uL --from reservoir:A1', 'VOLUME_OUT_OF_RANGE', 2)
        self.assertEqual(calls, 0)
        self.err('run flex aspirate --volume 1.5mL --from reservoir:A1', 'VOLUME_OUT_OF_RANGE', 2)

    def test_units_are_converted(self):
        self.ok('run flex pick-up-tip --rack tiprack200')
        self.ok('run flex aspirate --volume 0.05mL --from reservoir:A1')
        self.ok('run flex dispense --volume 50000nL --to plate:A1')
        self.assertAlmostEqual(vol(self.backend, 'plate_A1'), 50.0, places=6)
        calls = [c for c in self.backend_calls() if c['capability'] == 'sample.aspirate']
        self.assertEqual(calls[0]['arguments']['volume_m3'], 5e-08)
        self.err('run flex aspirate --volume 5mg --from reservoir:A1', 'UNIT_NOT_ALLOWED', 2)

    def test_named_locations(self):
        self.err('run flex move-to plate:Z9', 'UNKNOWN_WELL', 2)
        e, _ = self.err('run flex move-to plat:A1', 'UNKNOWN_LABWARE', 2)
        self.assertIn('plate', e['fix'])
        self.err('run flex move-to plate', 'WELL_MISSING', 2)
        self.err('run flex aspirate --volume 10 --from tiprack1000:A1', 'WRONG_LOCATION_KIND', 2)
        self.ok('run flex move-to plate:B3')
        st = run_json(self.core, 'status flex')[1]
        self.assertEqual(st['position'], 'plate:B3')
        # the gate sent joint targets from the registry, never coordinates typed by the agent
        move = [c for c in self.backend_calls() if c['capability'] == 'motion.move'][-1]['arguments']
        well = self.core.inst.labware['plate']['wells']['B3']['position_m']
        self.assertEqual(move, well)

    def test_ranges_and_lists(self):
        self.ok('run flex transfer --volume 10 --from reservoir:A1 --to plate:A1-A3,plate:B1')
        for w in ('A1', 'A2', 'A3', 'B1'):
            self.assertAlmostEqual(vol(self.backend, 'plate_' + w), 10.0, places=6)
        self.err('run flex transfer --volume 10 --from reservoir:A1,reservoir:A3 --to plate:A1-A3', 'PAIRING', 2)


class Preconditions(Base):
    def test_power_off(self):
        backend, core = make_core(power=False)
        c, d = run_json(core, 'run flex pick-up-tip')
        self.assertEqual((c, d['error']['code'], d['error']['instrument_changed']), (3, 'POWER_OFF', 'no'))
        self.assertIn('power on', d['error']['fix'])

    def test_no_tip_and_tip_already_attached(self):
        e, calls = self.err('run flex aspirate --volume 50 --from reservoir:A1', 'NO_TIP', 3)
        self.assertEqual(calls, 0)
        self.assertEqual(e['sila_class'], 'DefinedExecutionError')
        self.ok('run flex pick-up-tip')
        self.err('run flex pick-up-tip', 'TIP_ALREADY_ATTACHED', 3)
        self.err('run flex transfer --volume 10 --from reservoir:A1 --to plate:A1', 'TIP_ALREADY_ATTACHED', 3)

    def test_tip_capacity_and_contents(self):
        self.ok('run flex pick-up-tip --rack tiprack200')
        e, calls = self.err('run flex aspirate --volume 350 --from reservoir:A1', 'TIP_CAPACITY', 3)
        self.assertEqual(calls, 0)
        self.assertIn('200 µL', e['message'])
        self.ok('run flex aspirate --volume 150 --from reservoir:A1')
        e, _ = self.err('run flex aspirate --volume 60 --from reservoir:A1', 'TIP_CAPACITY', 3)
        self.assertIn('50 µL', e['fix'])
        self.err('run flex dispense --volume 160 --to plate:A1', 'TIP_HOLDS_LESS', 3)

    def test_transfer_splits_over_the_tip_size(self):
        c, d = run_json(self.core, 'run flex transfer --volume 350 --from reservoir:A1 --to tuberack:A2 --rack tiprack200')
        self.assertEqual(c, 0, d)
        self.assertEqual(d['transfers'][0]['trips'], 2)
        asp = [x['arguments']['volume_m3'] for x in self.backend_calls() if x['capability'] == 'sample.aspirate']
        self.assertEqual(asp, [1.75e-07, 1.75e-07])
        self.assertAlmostEqual(vol(self.backend, 'tuberack_A2'), 350.0, places=6)

    def test_insufficient_liquid_is_inferred_from_the_device_refusal(self):
        self.ok('run flex pick-up-tip')
        e, _ = self.err('run flex aspirate --volume 600 --from reservoir:A3', 'INSUFFICIENT_LIQUID', 3)
        self.assertEqual(e['instrument_changed'], 'yes')                     # the gantry moved to the source first
        self.assertEqual(e['backend']['code'], 'PRECONDITION_FAILED')
        self.assertAlmostEqual(vol(self.backend, 'reservoir_A3'), 400.0, places=6)

    def test_destination_full(self):
        self.ok('run flex pick-up-tip')
        self.ok('run flex aspirate --volume 50 --from reservoir:A1')
        e, _ = self.err('run flex dispense --volume 50 --to plate:H12', 'DEST_FULL', 3)   # 350 of 360 uL
        self.assertIn('360 µL', e['message'])

    def test_tips_run_out_and_specific_tip(self):
        backend, core = make_core(labware_override={'tiprack200': {'tips': ['A1']}})
        self.assertEqual(run(core, 'run flex pick-up-tip --tip tiprack200:B1').exit_code, 3)
        self.assertEqual(run(core, 'run flex pick-up-tip --rack tiprack200').exit_code, 0)
        self.assertEqual(run(core, 'run flex drop-tip').exit_code, 0)
        c, d = run_json(core, 'run flex pick-up-tip --rack tiprack200')
        self.assertEqual(d['error']['code'], 'RACK_EMPTY')
        self.assertIn('tiprack1000', d['error']['fix'])

    def test_return_tip_makes_it_available_again(self):
        self.ok('run flex pick-up-tip')
        self.ok('run flex return-tip')
        c, d = run_json(self.core, 'run flex pick-up-tip')
        self.assertEqual(d['tip'], 'tiprack1000:A1')
        self.err('run flex drop-tip --to tiprack1000:B1', 'WELL_OCCUPIED', 3)

    def test_no_trash_configured(self):
        self.ok('run flex pick-up-tip')
        self.ok('run flex deck-config A1 none')
        self.err('run flex drop-tip', 'NO_TRASH', 3)
        self.err('run flex deck-config A1 staging_area', 'FIXTURE_NOT_ALLOWED', 2)
        self.ok('run flex deck-config A1 trash_bin')
        self.ok('run flex drop-tip')

    def test_lost_tip_is_reported_not_hidden(self):
        backend, core = make_core(faults={'tip_off_after_dispenses': 2})
        c, d = run_json(core, 'run flex transfer --volume 20 --from reservoir:A1 --to plate:A1-A4')
        self.assertEqual((c, d['error']['code'], d['error']['instrument_changed']), (3, 'NO_TIP', 'yes'))
        self.assertIn('fallen off', d['error']['message'])
        self.assertEqual(sum(1 for s in d['error']['done'] if s.startswith('dispense')), 2)
        st = run(core, 'status flex').out
        self.assertIn('fallen off', st)

    def test_unsupported_and_unknown_verbs(self):
        e, _ = self.err('run flex mix', 'NOT_SUPPORTED', 2)
        self.assertIn('sample.mix', e['message'])
        self.err('run flex detect-liquid', 'NOT_SUPPORTED', 2)
        e, _ = self.err('run flex aspriate --volume 5 --from plate:A1', 'UNKNOWN_VERB', 2)
        self.assertIn('aspirate', e['fix'])
        self.err('run flex aspirate --volume 5 --from plate:A1 --speed 3', 'UNKNOWN_OPTION', 2)


class ErrorFormatAndExitCodes(Base):
    def test_text_format(self):
        r = run(self.core, 'run flex aspirate --volume 50 --from reservoir:A1')
        self.assertEqual(r.exit_code, 3)
        lines = r.err.strip().splitlines()
        self.assertRegex(lines[0], r'^error\[defined/NO_TIP\]: ')
        self.assertTrue(lines[1].startswith('fix: '))
        self.assertEqual(lines[2], 'instrument changed: no')
        self.assertRegex(lines[3], r'^request: r-\d{4}$')
        self.assertEqual(r.out, '')

    def test_json_format(self):
        c, d = run_json(self.core, 'run flex aspirate --volume 50 --from reservoir:A1')
        self.assertEqual(set(d['error']) >= {'class', 'sila_class', 'code', 'message', 'fix', 'instrument_changed', 'done'}, True)
        self.assertEqual(d['error']['class'], 'defined')

    def test_partial_progress_is_reported(self):
        self.ok('run flex pick-up-tip')
        r = run(self.core, 'run flex aspirate --volume 600 --from reservoir:A3')
        self.assertIn('instrument changed: yes', r.err)

    def test_undefined_error_exit_4(self):
        backend, core = make_core(faults={'raise_on': 'robot.set_lights'})
        c, d = run_json(core, 'run flex lights on')
        self.assertEqual((c, d['error']['class'], d['error']['sila_class'], d['error']['instrument_changed']), (4, 'undefined', 'UndefinedExecutionError', 'unknown'))

    def test_readback_mismatch_is_undefined(self):
        original = self.backend.invoke

        def lying(principal, cid, args, rid):
            if cid == 'robot.set_lights':                                   # claims success without doing it
                r = original(principal, 'state.read', {}, rid)
                r['result'] = {}
                return r
            return original(principal, cid, args, rid)
        self.backend.invoke = lying
        c, d = run_json(self.core, 'run flex lights on')
        self.assertEqual((c, d['error']['code']), (4, 'READBACK_MISMATCH'))

    def test_gate_never_sends_an_invalid_call(self):
        ctx = Ctx(self.core, self.core.inst, 'agent', 'r-test', {})
        with self.assertRaises(UndefinedExecutionError) as cm:
            ctx.call('motion.move', {'x_m': 1.0, 'y_m': 0.0, 'z_m': 0.0}, 'bad move')
        self.assertEqual(cm.exception.code, 'GATE_BUILT_INVALID_CALL')
        with self.assertRaises(UndefinedExecutionError):
            ctx.call('velocity.set', {}, 'hidden capability')
        self.assertEqual(ctx.calls, [])

    def test_usage_errors(self):
        self.assertEqual(run(self.core, 'frobnicate').exit_code, 2)
        self.assertEqual(run(self.core, 'run gc aspirate').exit_code, 2)
        self.assertEqual(run(self.core, 'run flex aspirate --volume 5').exit_code, 2)
        self.assertEqual(run(self.core, '--help').exit_code, 0)
        self.assertIn('exit codes', run(self.core, '--help').out)


class DryRun(Base):
    def test_dry_run_changes_nothing(self):
        before = snapshot(self.backend)
        c, d = run_json(self.core, 'run flex transfer --volume 150 --from reservoir:A1 --to plate:A1 --dry-run')
        self.assertEqual(c, 0)
        self.assertTrue(d['dry_run'])
        self.assertEqual([x['capability'] for x in d['backend_calls']],
                         ['motion.move', 'tip.attach', 'motion.move', 'sample.aspirate', 'motion.move', 'sample.dispense', 'motion.move', 'tip.release'])
        self.assertEqual(snapshot(self.backend), before)
        self.assertEqual(self.backend_calls(), [])
        r = run(self.core, 'run flex transfer --volume 150 --from reservoir:A1 --to plate:A1 --dry-run')
        self.assertIn('dry-run (nothing was sent', r.out)
        self.assertIn('checked only when it runs', r.out)

    def test_dry_run_still_checks(self):
        e, calls = self.err('run flex aspirate --volume 50 --from reservoir:A1 --dry-run', 'NO_TIP', 3)
        self.assertEqual(calls, 0)
        # the shadow follows the plan: a later step sees the tip a former step picked up
        c, d = run_json(self.core, 'run flex transfer --volume 50 --from reservoir:A1 --to plate:A1-A3 --new-tip always --dry-run')
        self.assertEqual(c, 0)
        self.assertEqual(sum(1 for x in d['backend_calls'] if x['capability'] == 'tip.attach'), 3)

    def test_destructive_needs_yes(self):
        before = snapshot(self.backend)
        r = self.ok('run flex shutdown')
        self.assertIn('add --yes', r.out)
        self.assertEqual(snapshot(self.backend), before)
        r = self.ok('run flex device-reset run_history,offsets')
        self.assertEqual(snapshot(self.backend), before)
        self.ok('run flex shutdown --yes')
        self.assertFalse(self.backend.evaluator_state()['power.enabled'])


class JobsAndBusy(Base):
    def setUp(self):
        self.backend, self.core = make_core(protocols=[PLATE_PREP])

    def test_job_lifecycle(self):
        self.err('run flex run-protocol plate_prep --param volume_ul=500', 'PARAMETER_OUT_OF_RANGE', 2)
        self.err('run flex run-protocol nope', 'UNKNOWN_PROTOCOL', 2)
        c, d = run_json(self.core, 'run flex run-protocol plate_prep --param volume_ul=80')
        self.assertEqual((c, d['job'], d['status']), (0, 'job-1', 'running'))
        self.assertGreater(d['eta_s'], 60)
        self.err('run flex aspirate --volume 10 --from reservoir:A1', 'BUSY_OWN_JOB', 3)
        self.err('run flex power off', 'BUSY_OWN_JOB', 3)
        self.backend.advance(3)
        c, s = run_json(self.core, 'status job-1')
        self.assertEqual(s['status'], 'running')
        self.ok('run flex pause job-1')
        self.err('run flex pause job-1', 'JOB_NOT_RUNNING', 3)
        self.ok('run flex resume job-1')
        self.backend.advance(200)
        c, s = run_json(self.core, 'status job-1')
        self.assertEqual(s['status'], 'completed')
        self.err('cancel job-1', 'JOB_FINISHED', 3)
        self.assertAlmostEqual(vol(self.backend, 'plate_C3'), 80.0, places=6)

    def test_cancel_and_ownership(self):
        self.ok('run flex run-protocol plate_prep')
        self.err('status job-1', 'NOT_YOUR_JOB', 2, caller='other')
        self.err('cancel job-1', 'LOCKED', 3, caller='other')              # the owner holds the instrument while its job runs
        self.err('cancel job-9', 'UNKNOWN_JOB', 2)
        self.ok('cancel job-1')
        self.assertEqual(run_json(self.core, 'status job-1')[1]['status'], 'canceled')

    def test_recovery(self):
        self.ok('run flex run-protocol plate_prep')
        self.backend.rt.host_inject_error(self.backend.iid, kind='general')
        self.backend.advance(3)
        c, s = run_json(self.core, 'status job-1')
        self.assertEqual(s['status'], 'awaiting_recovery')
        self.assertIn('retry', s['recovery_options'])
        self.err('run flex recover job-1 --action retry_new_tips', 'ACTION_NOT_ALLOWED', 3)
        self.ok('run flex recover job-1 --action retry')
        self.backend.advance(200)
        self.assertEqual(run_json(self.core, 'status job-1')[1]['status'], 'completed')

    def test_other_users_run_makes_the_flex_busy(self):
        self.backend.start_run_as('colleague', 'plate_prep')
        self.backend.advance(2)
        e, calls = self.err('run flex transfer --volume 10 --from reservoir:A1 --to plate:H1', 'BUSY', 3)
        self.assertEqual(calls, 0)
        self.assertIn('left', e['message'])
        self.assertIn('power', e['fix'])                                     # do not power off
        self.err('run flex power off', 'BUSY', 3)                            # the gate refuses to abort their run
        self.err('run flex run-protocol plate_prep', 'BUSY', 3)
        self.assertTrue(run_json(self.core, 'status flex')[1]['busy'])
        self.backend.advance(200)
        self.ok('run flex transfer --volume 10 --from reservoir:A1 --to plate:H1')

    def test_failed_start_rolls_the_setup_back(self):
        backend, core = make_core(protocols=[PLATE_PREP], faults={'raise_on': 'run.play'})
        c, d = run_json(core, 'run flex run-protocol plate_prep')
        self.assertEqual((c, d['error']['class']), (4, 'undefined'))
        self.assertIn('rolled back', d['error']['instrument_changed'])
        self.assertIsNone(backend.evaluator_state()['run.state'])
        self.assertEqual([x['capability'] for x in core.log.entries[-1]['backend_calls']][-1], 'run.stop')

    def test_tip_attached_blocks_a_run(self):
        self.ok('run flex pick-up-tip')
        self.err('run flex run-protocol plate_prep', 'TIP_ATTACHED', 3)

    def test_uncalibrated_pipette_blocks_a_run(self):
        self.ok('run flex device-reset calibration --yes')
        self.err('run flex run-protocol plate_prep', 'RUN_NOT_READY', 3)
        self.ok('run flex calibrate-pipette')
        self.ok('run flex run-protocol plate_prep')


class LockRequestIdsLog(Base):
    def test_lease_lock(self):
        backend, core = make_core(lease_s=60)
        self.assertEqual(run(core, 'run flex lights on', 'alice').exit_code, 0)
        c, d = run_json(core, 'run flex lights off', 'bob')
        self.assertEqual((c, d['error']['code']), (3, 'LOCKED'))
        self.assertIn('alice', d['error']['message'])
        self.assertEqual(run(core, 'status flex', 'bob').exit_code, 0)       # reading is always allowed
        core.lock.last -= 61                                                 # alice idle past the lease
        self.assertEqual(run(core, 'run flex lights off', 'bob').exit_code, 0)

    def test_request_id_replay(self):
        r1 = run(self.core, 'run flex pick-up-tip --request-id my-1')
        n = len(self.backend_calls())
        r2 = run(self.core, 'run flex pick-up-tip --request-id my-1')
        self.assertEqual(r2.exit_code, 0)
        self.assertEqual(r1.out, r2.out)
        self.assertIn('replayed', r2.err)
        self.assertEqual(len(self.backend_calls()), n)                       # not executed twice
        self.err('run flex drop-tip --request-id my-1', 'REQUEST_ID_REUSED', 2)
        self.err('run flex drop-tip --request-id "bad id!"', 'BAD_REQUEST_ID', 2)
        r = run(self.core, 'status flex --request-id my-1', 'other')                 # ids are per caller: no replay, no conflict
        self.assertEqual(r.exit_code, 0)
        self.assertNotIn('replayed', r.err)

    def test_backend_request_ids_derive_from_the_request(self):
        self.ok('run flex transfer --volume 10 --from reservoir:A1 --to plate:A1 --request-id job-x')
        ids = [c['request_id'] for c in self.backend_calls()]
        self.assertEqual(ids[0], 'job-x.1')
        self.assertEqual(len(set(ids)), len(ids))

    def test_append_only_log(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(__file__))) as d:
            path = os.path.join(d, 'log.jsonl')
            backend, core = make_core(log_path=path)
            run(core, 'run flex pick-up-tip')
            run(core, 'run flex aspirate --volume 5000 --from reservoir:A1')
            size = os.path.getsize(path)
            run(core, 'status flex', 'other')
            with open(path) as f:
                head = f.read(size)
                lines = [json.loads(x) for x in (head + f.read()).splitlines()]
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0]['backend_calls'][-1]['capability'], 'tip.attach')
            self.assertEqual(lines[1]['outcome'], 'validation/VOLUME_OUT_OF_RANGE')
            self.assertIn('stderr', lines[1])                                # full reply text kept for failure analysis
            with open(path) as f:
                self.assertTrue(f.read().startswith(head))                   # earlier entries untouched
            mine = run_json(core, 'log')[1]['entries']
            self.assertEqual(len(mine), 2)                                   # only my own requests (the log call is logged after)
            self.assertNotIn('stdout', mine[0])


class AgentEvaluatorSplit(Base):
    def test_core_uses_only_the_callers_agent_principal(self):
        seen = []
        for name in ('read', 'invoke', 'write', 'describe'):
            orig = getattr(self.backend, name)

            def spy(principal, *a, _orig=orig, **k):
                seen.append(principal)
                return _orig(principal, *a, **k)
            setattr(self.backend, name, spy)
        self.ok('run flex transfer --volume 10 --from reservoir:A1 --to plate:A1')
        self.ok('status flex')
        self.ok('run flex labware')
        self.assertEqual(set(seen), {'agent'})
        self.assertEqual(self.backend._visibility['agent'], 'agent')

    def test_agent_view_has_no_truth(self):
        ctx = Ctx(self.core, self.core.inst, 'agent', 'r-x', {})
        self.assertFalse([k for k in ctx.view if k.startswith('truth.')])
        out = run(self.core, 'status flex --json').out + run(self.core, 'run flex labware --json').out
        self.assertNotIn('volume_m3', out)
        self.assertNotIn('truth', out)

    def test_reserved_identities_cannot_call(self):
        for who in ('evaluator', 'scene'):
            with self.assertRaises(ValueError):
                self.core.handle(['chemlab', 'status'], who)


if __name__ == '__main__':
    unittest.main()
