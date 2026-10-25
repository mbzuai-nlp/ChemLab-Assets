"""Acceptance: each task's gold solution, run through the chemlab client in the sandbox, passes its checker; NoAct fails."""
import os
import shutil
import tempfile
import unittest

from helpers import ROOT
from chemlab.flex_tasks import TASKS, run_task

RUNS = os.path.join(ROOT, 'build', 'test_runs')


class Acceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(RUNS, exist_ok=True)
        cls.tmp = tempfile.mkdtemp(dir=RUNS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


def _make(task):
    def test(self):
        gold = run_task(task, os.path.join(self.tmp, task['id'], 'gold'), speed=40.0)
        self.assertTrue(gold['passed'], [c for c in gold['checks'] if not c['ok']])
        self.assertFalse(gold['timed_out'])
        noact = run_task(task, os.path.join(self.tmp, task['id'], 'noact'), solution='', speed=40.0)
        self.assertFalse(noact['passed'], 'an empty solution must fail %s' % task['id'])
    return test


for _t in TASKS:
    setattr(Acceptance, 'test_' + _t['id'], _make(_t))

if __name__ == '__main__':
    unittest.main()
