"""The generic layer is unchanged for every instrument that has a class adapter: the generic sweeps (chemlab/sweep.py) on just
those instruments, with the adapters loaded (the default config merges configs/adapters/*.json):

* equivalence: every agent capability with the contract's example arguments, `chemlab call` in the sandbox vs the runtime
  called directly on a fresh copy -> same status and error code, same arguments sent;
* ready: describe / read (text and --json) in the sandbox, every example call in order vs a twin lab, and the visibility scan
  of all output (now including the class sections of describe and the friendly read).

Which instruments: the members of the class fragments (the generated shared layer, "layer": "shared", is checked on all 203 by
test_shared_all203).  Set CHEMLAB_PARITY_CLASSES=chromatography[,spectroscopy] to check only those classes' members (what a
class package runs).  Each run writes to its own directory build/compile_parity/<pid>-<time>/ (generic_results/ is not touched),
so runs at the same time never collide.
"""
import os
import time
import unittest
from pathlib import Path

from helpers import ROOT
from chemlab import sweep
from chemlab.compile.fragments import load_fragments


def members():
    only = [c.strip() for c in os.environ.get('CHEMLAB_PARITY_CLASSES', '').split(',') if c.strip()]
    out = []
    for f in load_fragments():
        if f.get('layer') == 'shared' or (only and f['class'] not in only):
            continue
        for slug in f['members']:
            if slug not in out:
                out.append(slug)
    return out


class Parity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = (sweep.RESULTS, sweep.RUNS)
        d = Path(ROOT) / 'build' / 'compile_parity' / ('%d-%s' % (os.getpid(), time.strftime('%H%M%S')))
        sweep.RESULTS, sweep.RUNS = d, d / 'runs'
        cls.ids = members()

    @classmethod
    def tearDownClass(cls):
        sweep.RESULTS, sweep.RUNS = cls.saved

    def test_1_equivalence(self):
        res = sweep.equivalence(only=self.ids, log=lambda *a: None)
        rows = [r for r in res['rows'] if 'cli' in r]
        self.assertEqual({r['instrument'] for r in rows}, set(self.ids))
        bad = [r for r in rows if not (r['same'] and r['args_sent_equal'])]
        self.assertEqual(bad, [])

    def test_2_ready_and_visibility(self):
        res = sweep.ready(only=self.ids, log=lambda *a: None)
        self.assertEqual([r['instrument'] for r in res['describe_read'] if not r['ok']], [])
        self.assertEqual([r for r in res['rows'] if not r['same']], [])
        self.assertEqual(res['visibility'], [])


if __name__ == '__main__':
    unittest.main()
