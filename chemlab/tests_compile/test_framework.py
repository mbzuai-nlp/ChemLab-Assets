"""The compile-layer framework on a small lab: fragments, mapping checks, dry-run, partial-progress reports, jobs, composite
adapters, error format, help and describe, the generic commands unchanged, and visibility of every adapter output.

Run with the matterix python:  cd chemlab/tests_compile && <python> -m unittest test_framework
"""
import json
import os
import shutil
import unittest

from helpers import ROOT, host_path_re, native, run, run_json, tmpdir
from chemlab.compile.base import ClassAdapter
from chemlab.compile.fragments import CompositeAdapter, adapters_for, load_fragments
from chemlab.compile.jobs import ConditionJob
from chemlab.compile.tasks.balance_tasks import place
from chemlab.compile.verbs import Verb
from chemlab.glab import GenericLab, single
from chemlab.labbackend import load_config
from chemlab.sweep import _hidden_hits

IDS = ['mt_xpr204e', 'ohaus_px224e', 'ohaus_spx2201']


class ProbeAdapter(ClassAdapter):
    """A second class on the PX224E for the tests: a composite verb whose second call is refused, a job, and a verb name that the
    balance class also has."""
    CLASS = 'probe'
    TITLE = 'test class'
    ALWAYS = ('status', 'twostep', 'waitstable', 'tare')

    def build_verbs(self):
        return {'twostep': Verb('twostep', 'set the unit to mg, then zero (refused with a load)', self.h_two),
                'waitstable': Verb('waitstable', 'a job that ends when the reading is stable', self.h_job),
                'tare': Verb('tare', 'never used: the balance class owns tare', self.h_two)}

    def h_two(self, ctx, p):
        ctx.call('weighing.set_unit', {'unit': 'mg'}, 'set the display unit to mg')
        ctx.call('weighing.zero', {}, 'zero')
        return {'text': 'done\n'}

    def h_job(self, ctx, p):
        def test(c):
            return bool(c.v('weighing.stable')), 'stable' if c.v('weighing.stable') else 'settling'
        j = ConditionJob(ctx.core, self, ctx.req, 'wait until stable', test, max_s=120, poll_s=1.0)
        return {'text': 'started %s\n' % j.handle, 'json': {'job': j.handle}}


def probe_fragments(tmp):
    d = os.path.join(tmp, 'fragments')
    os.makedirs(d)
    shutil.copy(os.path.join(ROOT, 'configs', 'adapters', 'balance.json'), d)
    with open(os.path.join(d, 'probe.json'), 'w') as f:
        json.dump({'class': 'probe', 'adapter': 'test_framework:ProbeAdapter',
                   'members': {'ohaus_px224e': {'role': 'secondary', 'verbs': {}}}}, f)
    return d


class Framework(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tmpdir()
        cfg = single(IDS)
        cfg['class_adapters'] = probe_fragments(cls.tmp)
        cls.lab = GenericLab(cls.tmp + '/lab', cfg, callers=('agent', 'other')).start()
        place(cls.lab, 'ohaus_px224e', 0.030)
        cls.lab.backend.advance(30)

    @classmethod
    def tearDownClass(cls):
        cls.lab.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ fragments
    def test_fragments_and_switches(self):
        ad = self.lab.core.adapters
        self.assertEqual(type(ad['mt_xpr204e']).__name__, 'BalanceAdapter')
        self.assertIsInstance(ad['ohaus_px224e'], CompositeAdapter)
        self.assertEqual([p.CLASS for p in ad['ohaus_px224e'].parts], ['balance', 'probe'])       # primary first
        cfg = single(IDS)
        self.assertEqual(sorted(adapters_for(cfg)), sorted(IDS))
        cfg['class_adapters'] = False
        self.assertEqual(adapters_for(cfg), {})
        cfg = single(IDS)
        cfg['instruments'][0]['adapter'] = 'chemlab.adapters:InstrumentAdapter'                   # an entry's own adapter wins
        self.assertNotIn(cfg['instruments'][0]['id'], adapters_for(cfg))
        names = [f['class'] for f in load_fragments()]
        self.assertIn('balance', names)

    def test_every_member_mapping_fits_its_contract(self):
        frags = load_fragments()
        cfg = load_config()
        known = {e['asset_type_id'].split('.', 1)[1] for e in cfg['instruments']}
        for frag in frags:
            for slug in frag['members']:
                self.assertIn(slug, known, '%s: %s is not one of the 203' % (frag['class'], slug))
        for frag in frags:
            ids = [s for s in frag['members'] if s in IDS]
            for slug in ids:
                ad = adapters_for(single([slug]), [frag])[slug]
                self.assertEqual(ad.check_mapping(self.lab.backend.instruments[slug]), [], slug)

    # ------------------------------------------------------------------ dry-run, partial progress, errors
    def test_dry_run_sends_nothing(self):
        for iid in IDS:
            before = self.lab.backend.evaluator_read(iid)['observations']
            n = len(self.lab.core.log.entries)
            for cmd in ('tare', 'zero', 'unit kg', 'door close', 'power off', 'weigh --stable', 'power on'):
                r = run(self.lab, 'run %s %s --dry-run' % (iid, cmd))
                self.assertIn(r.exit_code, (0, 2, 3, 6), r.err)
                if r.exit_code == 0:
                    self.assertTrue(r.out.startswith('dry-run'), r.out)
            self.assertEqual(native(self.lab, n), [], iid)
            after = self.lab.backend.evaluator_read(iid)['observations']
            self.assertEqual(json.dumps(before, sort_keys=True, default=str), json.dumps(after, sort_keys=True, default=str), iid)
        code, js = run_json(self.lab, 'run ohaus_px224e unit kg --dry-run')
        self.assertEqual(code, 0)
        self.assertEqual(js['backend_calls'], [{'capability': 'weighing.set_unit', 'arguments': {'unit': 'kg'}}])
        self.assertTrue(js['dry_run'])

    def test_refused_composite_lists_the_steps_done(self):
        run(self.lab, 'run ohaus_px224e unit g')
        r = run(self.lab, 'run ohaus_px224e twostep')
        self.assertEqual(r.exit_code, 3, r.out + r.err)
        self.assertIn('instrument changed: yes - done before the error: set the display unit to mg', r.err)
        self.assertIn('instrument said: weighing.zero -> rejected ZERO_RANGE', r.err)
        code, js = run_json(self.lab, 'run ohaus_px224e twostep')
        self.assertEqual(js['error']['done'], ['set the display unit to mg'])
        self.assertEqual(js['error']['instrument_changed'], 'yes')
        self.assertEqual(js['error']['code'], 'ZERO_RANGE')
        self.assertEqual([c['capability'] for c in js['native_calls']], ['weighing.set_unit', 'weighing.zero'])
        run(self.lab, 'run ohaus_px224e unit g')

    def test_error_format_and_exit_codes(self):
        r = run(self.lab, 'run ohaus_spx2201 door close')
        self.assertEqual(r.exit_code, 6)
        for part in ('error[unsupported/NOT_SUPPORTED]', 'fix:', 'instrument changed: no', 'request:'):
            self.assertIn(part, r.err)
        r = run(self.lab, 'run mt_xpr204e tare --wait 5parsecs')
        self.assertEqual(r.exit_code, 2)
        self.assertIn('UNIT_NOT_ALLOWED', r.err)
        r = run(self.lab, 'run mt_xpr204e frobnicate')
        self.assertEqual(r.exit_code, 2)
        self.assertIn('UNKNOWN_VERB', r.err)
        code, js = run_json(self.lab, 'run mt_xpr204e door open --side top')
        self.assertEqual(code, 2)
        self.assertEqual(set(js['error']) >= {'class', 'sila_class', 'code', 'message', 'fix', 'instrument_changed', 'done'}, True)
        r = run(self.lab, 'run vaisala_dmt152 tare')
        self.assertEqual(r.exit_code, 2)

    # ------------------------------------------------------------------ jobs
    def test_jobs_status_wait_cancel_and_ownership(self):
        place(self.lab, 'ohaus_px224e', 0.031)
        self.lab.backend.advance(1)
        code, js = run_json(self.lab, 'run ohaus_px224e waitstable')
        self.assertEqual(code, 0, js)
        job = js['job']
        code, st = run_json(self.lab, 'status %s' % job)
        self.assertEqual((code, st['done']), (0, False))
        self.assertEqual(run(self.lab, 'status %s' % job, caller='other').exit_code, 2)
        self.assertEqual(run(self.lab, 'wait %s' % job, caller='other').exit_code, 2)
        code, w = run_json(self.lab, 'wait %s --max 60s' % job)
        self.assertEqual((code, w['status'], w['done']), (0, 'succeeded', True))
        self.assertGreater(w['waited_s'], 0)
        code, lst = run_json(self.lab, 'status')
        self.assertIn(job, [j['job'] for j in lst['jobs']])
        r = run(self.lab, 'cancel %s' % job)
        self.assertEqual(r.exit_code, 3)
        self.assertIn('JOB_FINISHED', r.err)
        self.assertEqual(run(self.lab, 'status job-999').exit_code, 2)
        place(self.lab, 'ohaus_px224e', 0.030)
        self.lab.backend.advance(1)
        code, js = run_json(self.lab, 'run ohaus_px224e waitstable')
        code, c = run_json(self.lab, 'cancel %s' % js['job'])
        self.assertEqual((code, c['status']), (0, 'cancelled'))
        self.lab.backend.advance(30)

    # ------------------------------------------------------------------ describe / help / composite
    def test_describe_help_and_composite(self):
        r = run(self.lab, 'describe ohaus_px224e')
        self.assertIn('class-level verbs (balance', r.out)
        self.assertIn('class-level verbs (probe', r.out)
        self.assertIn('tare', r.out)
        code, js = run_json(self.lab, 'describe mt_xpr204e')
        self.assertEqual(js['class']['name'], 'balance')
        self.assertIn('weigh', [v['verb'] for v in js['class']['verbs']])
        r = run(self.lab, 'run ohaus_spx2201')
        self.assertIn('not on this instrument: door', r.out)
        for cmd in ('help mt_xpr204e tare', 'run mt_xpr204e tare --help'):
            r = run(self.lab, cmd)
            self.assertEqual(r.exit_code, 0, r.err)
            self.assertIn('on mt_xpr204e this uses: balance.tare', r.out)
        n = len(self.lab.core.log.entries)
        r = run(self.lab, 'run ohaus_px224e tare --dry-run')                 # the balance class owns tare (primary)
        self.assertIn('weighing.tare', r.out)
        self.assertEqual(native(self.lab, n), [])
        r = run(self.lab, 'read ohaus_px224e')
        self.assertIn('balance view: net', r.out)
        code, js = run_json(self.lab, 'read mt_xpr204e')
        self.assertIn('class_view', js)

    # ------------------------------------------------------------------ the generic commands do not change
    def test_generic_commands_unchanged_with_adapters(self):
        tmp = tmpdir()
        try:
            cfg = single(['mt_xpr204e'])
            cfg['class_adapters'] = False
            plain = GenericLab(tmp + '/plain', cfg)
            withad = GenericLab(tmp + '/with', single(['mt_xpr204e']))
            self.assertIn('mt_xpr204e', withad.core.adapters)
            for cmd in ('describe mt_xpr204e', 'read mt_xpr204e', 'call mt_xpr204e balance.reading', 'call mt_xpr204e balance.tare',
                        'help mt_xpr204e balance.tare', 'call mt_xpr204e door.operate --side left --action open', 'read mt_xpr204e'):
                a, b = run_json(plain, cmd), run_json(withad, cmd)
                self.assertEqual(a[0], b[0], cmd)
                for k in ('capabilities', 'state_slots', 'observations', 'status', 'result', 'capability'):
                    if k in a[1]:
                        self.assertEqual(json.dumps(a[1][k], sort_keys=True, default=str), json.dumps(b[1][k], sort_keys=True, default=str), (cmd, k))
            self.assertEqual(plain.backend.evaluator_read('mt_xpr204e')['observations'].keys(),
                             withad.backend.evaluator_read('mt_xpr204e')['observations'].keys())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------------------ visibility
    def test_zz_no_hidden_ids_in_any_adapter_output(self):
        for iid in IDS:
            for cmd in ('describe %s', 'describe %s --json', 'read %s', 'read %s --json', 'run %s', 'run %s status --json',
                        'help %s tare', 'run %s weigh --json', 'run %s unit', 'run %s door open --dry-run --json', 'run %s frob'):
                r = run(self.lab, cmd % iid)
                hits = _hidden_hits(self.lab.backend.instruments[iid].view, r.out + r.err, self.lab.backend.global_view)
                self.assertEqual(hits, [], cmd % iid)
        for e in self.lab.core.log.entries:
            self.assertNotRegex(e['stdout'] + e['stderr'], host_path_re())


if __name__ == '__main__':
    unittest.main()
