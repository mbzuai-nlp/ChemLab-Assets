"""The shared layer on all 203 instruments.

Fast parts (always): the shared fragment plus the thin fragment cover all 203; every generated mapping fits its contract; the
generator reproduces configs/adapters/shared.json exactly (nobody hand-edits it); the power and open/close values agree with the
contract (power = the write_via of the power slot; open/close = the argument's range ends or true/false).

Slow parts (CHEMLAB_ALL203=1): the shared lowering table on all 198 members (native calls + twin state + one path), and the
visibility scan of every adapter output (describe / read / run / status / help of every verb) in one lab of all 203.
The generic sweeps on all 203 with every adapter loaded are `python -m chemlab.compile.parity_all` (about an hour).
"""
import json
import os
import shutil
import unittest

from helpers import host_path_re, run, tmpdir
from chemlab.compile import shared_gen
from chemlab.compile.fragments import FRAGMENT_DIR, adapters_for, load_fragments
from chemlab.glab import GenericLab, single
from chemlab.labbackend import LabBackend, load_config
from chemlab.sweep import _hidden_hits

SLOW = os.environ.get('CHEMLAB_ALL203') == '1'


class SharedLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frags = {f['class']: f for f in load_fragments()}
        cls.shared = cls.frags['shared']
        cfg = load_config()
        cfg['class_adapters'] = False
        cls.tmp = tmpdir()
        cls.backend = LabBackend(cfg, run_dir=cls.tmp + '/b', setup=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_1_all_203_covered(self):
        slugs = {e['asset_type_id'].split('.', 1)[1] for e in load_config()['instruments']}
        self.assertEqual(len(slugs), 203)
        covered = set(self.shared['members']) | set(self.frags['thin']['members'])
        self.assertEqual(covered, slugs)
        self.assertFalse(set(self.shared['members']) & set(self.frags['thin']['members']))
        self.assertEqual(self.shared.get('layer'), 'shared')

    def test_2_every_mapping_fits_its_contract(self):
        bad = {}
        for slug in self.shared['members']:
            ad = adapters_for(single([slug]), [self.shared])[slug]
            p = ad.check_mapping(self.backend.instruments[slug])
            if p:
                bad[slug] = p
        self.assertEqual(bad, {})

    def test_3_generator_reproduces_the_fragment(self):
        members, bad, thin = shared_gen.build(log=lambda *a: None)
        self.assertEqual(bad, {})
        on_disk = json.loads((FRAGMENT_DIR / 'shared.json').read_text())
        self.assertEqual(json.dumps(on_disk['members'], sort_keys=True), json.dumps(members, sort_keys=True),
                         'configs/adapters/shared.json differs from `python -m chemlab.compile.shared_gen --write`')

    def test_4_power_and_parts_agree_with_the_contract(self):
        for slug, m in self.shared['members'].items():
            c = self.backend.instruments[slug].contract
            p = m['verbs'].get('power')
            if p and not p.get('toggle'):
                sid = (m.get('slots') or {}).get('power')
                wv = (c['state_slots'].get(sid) or {}).get('write_via') or {}
                if wv.get('capability_id'):
                    self.assertEqual((p['cap'], p['arg']), (wv['capability_id'], wv['argument']), slug)
                self.assertEqual(sorted((c['capabilities'][p['cap']]['input_schema'].get('required') or [])),
                                 sorted(set([p['arg']]) | set((p.get('args') or {}))), slug)
            for name, part in ((m['verbs'].get('open') or {}).get('parts') or {}).items():
                if 'cap' not in part:
                    continue
                s = c['capabilities'][part['cap']]['input_schema']['properties'][part['arg']]
                if s.get('type') == 'boolean':
                    self.assertEqual({part['open'], part['close']}, {True, False}, (slug, name))
                elif 'minimum' in s:
                    self.assertEqual({part['open'], part['close']}, {s['minimum'], s['maximum']}, (slug, name))
                    self.assertEqual(part['open'], s['maximum'] if 'closed' not in part['arg'] else s['minimum'], (slug, name))

    @unittest.skipUnless(SLOW, 'set CHEMLAB_ALL203=1 (about 10 minutes)')
    def test_5_lowering_all_members(self):
        from chemlab.compile.lowering import run_class
        out = run_class('shared', log=lambda *a: None)
        bad = [(m['instrument'], s['command'], s['native_calls'], s['expected'], s['diff']) for m in out['members']
               for s in m['steps'] if not (s['calls_ok'] and s['state_equal'])]
        self.assertEqual(bad, [])
        self.assertEqual([m['instrument'] for m in out['members'] if m['path_problems'] or not m['start_equal']], [])
        self.assertEqual(len(out['members']), len(self.shared['members']))

    @unittest.skipUnless(SLOW, 'set CHEMLAB_ALL203=1 (a few minutes)')
    def test_6_visibility_all_203(self):
        tmp = tmpdir()
        lab = GenericLab(tmp + '/lab', load_config())
        try:
            for iid, ad in lab.core.adapters.items():
                cmds = ['describe %s', 'describe %s --json', 'read %s', 'run %s', 'run %s --json', 'run %s status', 'run %s status --json']
                cmds += ['help %%s %s' % v for v in ad.verbs()]
                for c in cmds:
                    r = run(lab, c % iid)
                    self.assertIn(r.exit_code, (0, 2, 3, 6), (c % iid, r.out, r.err))
                    hits = _hidden_hits(lab.backend.instruments[iid].view, r.out + r.err, lab.backend.global_view)
                    self.assertEqual(hits, [], c % iid)
                    self.assertNotRegex(r.out + r.err, host_path_re(), c % iid)
            self.assertEqual(len(lab.core.adapters), 203)
        finally:
            lab.stop()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
