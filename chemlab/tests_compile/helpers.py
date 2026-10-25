import json
import os
import shlex
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RUNS = os.path.join(ROOT, 'build', 'test_runs_compile')


def host_paths():
    """Paths of this machine that must never appear in agent-visible output: the project folder, the home directory and the
    folder holding the home directories (when it is not '/'), plus the temp dir prefix and the runtime source folder name."""
    from pathlib import Path
    from chemlab.hosts import CHEMSIM_ROOT
    roots = {str(CHEMSIM_ROOT), str(Path.home()), str(Path.home().parent)}
    return tuple(sorted(p for p in roots if p.strip('/'))) + ('/tmp/', 'Chemistry-Sim/scripts')


def host_path_re():
    import re
    return '|'.join(re.escape(p) for p in host_paths())


def tmpdir():
    os.makedirs(RUNS, exist_ok=True)
    return tempfile.mkdtemp(dir=RUNS)


def run(lab, cmd, caller='agent'):
    return lab.core.handle(['chemlab'] + shlex.split(cmd), caller)


def run_json(lab, cmd, caller='agent'):
    r = run(lab, cmd + ' --json', caller)
    return r.exit_code, json.loads(r.out)


def native(lab, since):
    """Native invoke / write / advance ops recorded since log entry ``since``."""
    return [c for e in lab.core.log.entries[since:] for c in e['backend_calls'] if c.get('op') in ('invoke', 'write', 'advance')]


def class_suite(cls, lowering=True, acceptance=True):
    """A TestCase class for one instrument class: every member's mapping fits its contract, the lowering table (native calls
    and twin equality) and the acceptance tasks (gold passes in the sandbox, NoAct fails).  A class test module is two lines:

        from helpers import class_suite
        BalanceClass = class_suite('balance')
    """
    import unittest

    class ClassSuite(unittest.TestCase):
        def test_1_mapping_fits_every_contract(self):
            from chemlab.compile.fragments import adapters_for, load_fragments
            from chemlab.glab import GenericLab, single
            frag = [f for f in load_fragments() if f['class'] == cls]
            self.assertEqual(len(frag), 1, 'one fragment configs/adapters/%s.json' % cls)
            members = list(frag[0]['members'])
            self.assertTrue(members)
            tmp = tmpdir()
            lab = GenericLab(tmp + '/lab', single(members), setup=False)
            for slug, ad in adapters_for(single(members), frag, strict=True).items():   # strict: a class module error fails here
                self.assertEqual(ad.check_mapping(lab.backend.instruments[slug]), [], slug)

        if lowering:
            def test_2_lowering(self):
                from chemlab.compile.lowering import run_class
                out = run_class(cls, log=lambda *a: None)
                bad = [(m['instrument'], s['command'], s['native_calls'], s['expected'], s['diff']) for m in out['members']
                       for s in m['steps'] if not (s['calls_ok'] and s['state_equal'])]
                self.assertEqual(bad, [])
                self.assertEqual([(m['instrument'], m['path_problems']) for m in out['members'] if m['path_problems']], [])
                self.assertTrue(all(m['start_equal'] for m in out['members']))
                self.assertTrue(out['passed'])

        if acceptance:
            def test_3_acceptance(self):
                from chemlab.compile.acceptance import suite
                s = suite(cls).main()
                self.assertEqual([r['task'] for r in s['tasks'] if not r['gold_passed']], [], 'gold must pass')
                self.assertEqual([r['task'] for r in s['tasks'] if not r['noact_failed']], [], 'NoAct must fail')
                self.assertGreaterEqual(len(s['tasks']), 5)                 # 8-10 for big classes, at least 5 (BRIEF_B)
                self.assertTrue(any('recovery' in r['kind'] for r in s['tasks']))

    ClassSuite.__name__ = ClassSuite.__qualname__ = '%sClass' % cls.capitalize()
    return ClassSuite
