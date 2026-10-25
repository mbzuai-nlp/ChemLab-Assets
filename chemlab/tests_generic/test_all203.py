"""The all-203 acceptance checks of the generic layer (slow: about 15 minutes with the matterix python).

Each part is in chemlab/sweep.py and writes generic_results/<part>.json.  Known, reported exceptions (runtime findings) are
listed here with the reason; anything else fails the test.

    cd chemlab/tests_generic && <matterix python> -m unittest test_all203
"""
import unittest

from helpers import ROOT  # noqa: F401  (puts chemlab on sys.path)
from chemlab import sweep

# capability -> reason, for calls whose chemlab result may differ from the direct runtime call (none known).
EQUIVALENCE_EXCEPTIONS = {}


class All203(unittest.TestCase):
    def test_1_equivalence_on_fresh_copies(self):
        res = sweep.equivalence(log=lambda *a: None)
        rows = [r for r in res['rows'] if 'cli' in r]
        self.assertGreaterEqual(len({r['instrument'] for r in rows}), 203)
        bad = [r for r in rows if not (r['same'] and r['args_sent_equal']) and (r['instrument'], r['capability']) not in EQUIVALENCE_EXCEPTIONS]
        self.assertEqual(bad, [], 'chemlab call differs from the runtime called directly')

    def test_2_ready_lab_describe_read_calls_visibility(self):
        res = sweep.ready(log=lambda *a: None)
        dr = res['describe_read']
        self.assertEqual(len(dr), 203)
        self.assertEqual([r['instrument'] for r in dr if not r['ok']], [])
        self.assertEqual([r for r in res['rows'] if not r['same']], [])
        self.assertEqual(res['visibility'], [], 'a hidden id reached agent output')

    def test_3_panels(self):
        res = sweep.panels(log=lambda *a: None)
        rows = res['rows']
        self.assertEqual(len(rows), 106)
        self.assertEqual([r['instrument'] for r in rows if not r.get('ok')], [])
        self.assertTrue(all(r['screen_equals_capture'] for r in rows))

    def test_4_clock(self):
        res = sweep.clock(log=lambda *a: None)
        rows = res['rows']
        self.assertEqual(len(rows), 2 * 203)
        self.assertEqual([r['instrument'] for r in rows if not r['clock_ok']], [])
        self.assertEqual([r['instrument'] for r in rows if r.get('twin_equal') is False], [])


if __name__ == '__main__':
    unittest.main()
