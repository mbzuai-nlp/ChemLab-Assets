"""Visibility of everything the compile layer prints: for every member of the class fragments (CHEMLAB_PARITY_CLASSES=a,b
limits it to those classes, as in test_generic_parity), describe / read / the class overview / status / every verb's help (text
and --json), plus every request and reply of the acceptance runs (compile_results/<class>/*/gold|noact/core_log.jsonl) - no
evaluator-only or debug id, no host path.  The shared layer on all 203 is scanned by test_shared_all203."""
import glob
import json
import os
import shutil
import unittest

from helpers import ROOT, host_paths, run, tmpdir
from chemlab.compile.fragments import load_fragments
from chemlab.glab import GenericLab, single
from chemlab.sweep import _hidden_hits

HOST_PATH = host_paths()


class Visibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tmpdir()
        from test_generic_parity import members                     # class-fragment members (CHEMLAB_PARITY_CLASSES filters)
        cls.ids = sorted(members())
        cls.lab = GenericLab(cls.tmp + '/lab', single(cls.ids)).start()

    @classmethod
    def tearDownClass(cls):
        cls.lab.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def scan(self, iid, text, what):
        hits = _hidden_hits(self.lab.backend.instruments[iid].view, text, self.lab.backend.global_view)
        self.assertEqual(hits, [], what)
        for p in HOST_PATH:
            self.assertNotIn(p, text, what)

    def test_adapter_views_of_every_member(self):
        for iid in self.ids:
            ad = self.lab.core.adapters[iid]
            cmds = ['describe %s', 'describe %s --json', 'read %s', 'read %s --json', 'run %s', 'run %s --json', 'run %s status', 'run %s status --json']
            cmds += ['help %%s %s' % v for v in ad.verbs()] + ['run %%s %s --help' % v for v in list(ad.verbs())[:3]]
            for c in cmds:
                r = run(self.lab, c % iid)
                self.assertIn(r.exit_code, (0, 2, 3, 6), (c % iid, r.err))
                self.scan(iid, r.out + r.err, c % iid)

    def test_acceptance_logs(self):
        only = [c.strip() for c in os.environ.get('CHEMLAB_PARITY_CLASSES', '').split(',') if c.strip()]
        logs = [f for f in glob.glob(os.path.join(ROOT, 'compile_results', '*', '*', '*', 'core_log.jsonl'))
                if not only or f.split(os.sep)[-4] in only]
        n = 0
        for f in logs:
            with open(f, encoding='utf-8') as fh:
                lines = fh.readlines()
            for line in lines:
                e = json.loads(line)
                iid = {'flex': 'opentrons_flex_single_5_1000ul'}.get(e.get('instrument'), e.get('instrument'))   # task lab names
                if iid in self.lab.backend.instruments:
                    self.scan(iid, e.get('stdout', '') + e.get('stderr', ''), '%s %s' % (f, e['argv']))
                    n += 1
        self.assertGreater(n, 0) if logs else None


if __name__ == '__main__':
    unittest.main()
