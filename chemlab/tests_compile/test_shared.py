"""The shared verb layer: power / open / close / method / start / stop / result / wait on a generated-style fragment
("layer": "shared"), the three job kinds, the one-path rule, --save-to, and precedence of a class fragment over the shared one."""
import json
import os
import shutil
import unittest

from helpers import ROOT, native, run, run_json, tmpdir
from chemlab.compile.fragments import adapters_for, load_fragments
from chemlab.glab import GenericLab, single

SHARED = {
    'class': 'shared', 'layer': 'shared', 'adapter': 'chemlab.compile.shared:SharedAdapter',
    'members': {
        'retsch_as200': {
            'verbs': {'power': {'cap': 'power.set', 'arg': 'enabled'},
                      'open': {'parts': {'cover': {'cap': 'cover.set', 'arg': 'lift_m', 'open': 0.14, 'close': 0.0}}},
                      'method': {'cap': 'method.configure', 'keys': {'time': {'arg': 'duration_s', 'kind': 'duration'},
                                                                    'lift': {'arg': 'total_lift_m', 'kind': 'length'}}},
                      'start': {'cap': 'acquisition.start', 'job': 'operation'},
                      'stop': {'cap': 'acquisition.stop'},
                      'result': {'cap': 'sieving.measure'}},
            'path_exceptions': ['sieving.measure'],
            'slots': {'power': 'power.enabled'}, 'status_slots': ['cycle.phase', 'cycle.remaining_s', 'power.enabled']},
        'mt_xpr204e': {
            'verbs': {'power': {'cap': 'power.set', 'arg': 'enabled'},
                      'open': {'parts': {'left_door': {'cap': 'door.operate', 'arg': 'action', 'open': 'open', 'close': 'close',
                                                       'args': {'side': 'left'}}}}},
            'slots': {'power': 'power.enabled'}},
    }}


class Shared(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tmpdir()
        d = os.path.join(cls.tmp, 'fragments')
        os.makedirs(d)
        shutil.copy(os.path.join(ROOT, 'configs', 'adapters', 'balance.json'), d)
        with open(os.path.join(d, 'shared.json'), 'w') as f:
            json.dump(SHARED, f)
        cfg = single(['retsch_as200', 'mt_xpr204e'])
        cfg['class_adapters'] = d
        cls.dir = d
        cls.lab = GenericLab(cls.tmp + '/lab', cfg).start()

    @classmethod
    def tearDownClass(cls):
        cls.lab.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_precedence_class_over_shared(self):
        ad = self.lab.core.adapters['mt_xpr204e']
        self.assertEqual([p.CLASS for p in ad.parts], ['balance', 'shared'])
        self.assertEqual(ad.owner('power').CLASS, 'balance')                   # the class verb wins
        self.assertEqual(ad.owner('open').CLASS, 'shared')                     # the shared layer answers what the class lacks
        n = len(self.lab.core.log.entries)
        r = run(self.lab, 'run mt_xpr204e open left_door')
        self.assertEqual(r.exit_code, 0, r.err)
        self.assertEqual([(c['capability'], c['arguments']) for c in native(self.lab, n)], [('door.operate', {'side': 'left', 'action': 'open'})])
        run(self.lab, 'run mt_xpr204e close')
        self.assertEqual(type(self.lab.core.adapters['retsch_as200']).__name__, 'SharedAdapter')

    def test_one_path_rule(self):
        frags = load_fragments(self.dir)
        lab_inst = self.lab.backend.instruments['retsch_as200']
        ad = adapters_for(single(['retsch_as200']), [f for f in frags if f['class'] == 'shared'])['retsch_as200']
        self.assertEqual(ad.check_mapping(lab_inst), [])
        del ad.map['path_exceptions']                                          # result through a second native path: caught
        probs = ad.check_mapping(lab_inst)
        self.assertTrue(any('sieving.measure' in p and 'path' in p for p in probs), probs)
        ad.map['verbs']['result'] = {'reads': ['cycle.phase', 'power.enabled']}
        probs = ad.check_mapping(lab_inst)
        self.assertTrue(any('power.enabled' in p and 'does not change' in p for p in probs), probs)

    def test_method_with_class_keys_units_and_native_names(self):
        n = len(self.lab.core.log.entries)
        r = run(self.lab, 'run retsch_as200 method time=2min lift=1.2mm interval_enabled=false interval_drive_s=3')
        self.assertEqual(r.exit_code, 0, r.err)
        calls = [(c['capability'], c['arguments']) for c in native(self.lab, n)]
        self.assertEqual(calls, [('method.configure', {'duration_s': 120.0, 'total_lift_m': 0.0012, 'interval_enabled': False,
                                                       'interval_drive_s': 3.0})])
        self.assertEqual(run(self.lab, 'run retsch_as200 method speed=9').exit_code, 2)
        self.assertEqual(run(self.lab, 'run retsch_as200 method time=2parsecs').exit_code, 2)

    def test_operation_job_state_job_and_save_to(self):
        code, js = run_json(self.lab, 'run retsch_as200 start')
        self.assertEqual(code, 0, js)
        self.assertEqual(js['kind'], 'operation')
        job = js['job']
        code, st = run_json(self.lab, 'status %s' % job)
        self.assertEqual(st['done'], False)
        code, w = run_json(self.lab, 'run retsch_as200 wait --max 30min')
        self.assertEqual((code, w['status'], w['done']), (0, 'succeeded', True))
        r = run(self.lab, 'run retsch_as200 result --save-to out/sieve.json')
        self.assertEqual(r.exit_code, 0, r.err)
        rec = json.loads((self.lab.work / 'out' / 'sieve.json').read_text())
        self.assertEqual(rec['verb'], 'result')
        self.assertEqual(run(self.lab, 'run retsch_as200 result --save-to ../x.json').exit_code, 2)
        # the same start as a run-type job read from cycle.phase
        ad = self.lab.core.adapters['retsch_as200']
        ad.map['verbs']['start'] = {'cap': 'acquisition.start', 'job': 'state',
                                    'state': {'slot': 'cycle.phase', 'done': ['succeeded'], 'failed': ['cancelled']}}
        try:
            run(self.lab, 'call retsch_as200 cycle.rearm')
            code, js = run_json(self.lab, 'run retsch_as200 start')
            self.assertEqual((code, js['kind']), (0, 'state'), js)
            code, c = run_json(self.lab, 'cancel %s' % js['job'])
            self.assertEqual((code, c['status']), (0, 'cancelled'), c)
            self.assertIn('acquisition.stop', c['what'])
        finally:
            ad.map['verbs']['start'] = SHARED['members']['retsch_as200']['verbs']['start']

    def test_open_close_refusal_explained_and_describe(self):
        r = run(self.lab, 'run retsch_as200 open cover')                       # the sieve tower is clamped: refused, explained
        self.assertEqual(r.exit_code, 3, r.out)
        self.assertIn('mount.clamped=true', r.err)
        self.assertIn('instrument said: cover.set -> rejected PRECONDITION_FAILED', r.err)
        for cmd in ('run retsch_as200 status', 'describe retsch_as200', 'help retsch_as200 method'):
            r = run(self.lab, cmd)
            self.assertEqual(r.exit_code, 0, cmd + r.err)
        self.assertEqual(run(self.lab, 'run retsch_as200 open hatch').exit_code, 2)
        self.assertEqual(run(self.lab, 'run retsch_as200 estop').exit_code, 6)  # not mapped: not on this instrument


if __name__ == '__main__':
    unittest.main()


from chemlab.compile.base import ClassAdapter as _CA          # noqa: E402
from chemlab.compile.verbs import Verb as _Verb               # noqa: E402


class ShutterOnlyAdapter(_CA):
    """A test class whose verb `open` exists in its table; the member mapping marks it unsupported (only a shutter opens)."""
    CLASS = 'probeopen'
    TITLE = 'test class'

    def build_verbs(self):
        return {'open': _Verb('open', 'open the shutter', lambda ctx, p: {'text': 'never\n'})}


class UnsupportedBeatsShared(unittest.TestCase):
    """A class's explicit "unsupported" verb is not answered by the shared layer on that member (unless "shared_fallthrough")."""

    def lab(self, fallthrough):
        tmp = tmpdir()
        self.addCleanup(shutil.rmtree, tmp, True)
        d = os.path.join(tmp, 'fragments')
        os.makedirs(d)
        with open(os.path.join(d, 'shared.json'), 'w') as f:
            json.dump(SHARED, f)
        m = {'unsupported': {'open': 'only the shutter opens on this instrument (test)'},
             'alternatives': {'open': 'use the shutter verb (test)'}}
        if fallthrough:
            m['shared_fallthrough'] = ['open']
        with open(os.path.join(d, 'probeopen.json'), 'w') as f:
            json.dump({'class': 'probeopen', 'adapter': 'test_shared:ShutterOnlyAdapter', 'members': {'retsch_as200': m}}, f)
        cfg = single(['retsch_as200'])
        cfg['class_adapters'] = d
        return GenericLab(tmp + '/lab', cfg)

    def test_class_unsupported_wins(self):
        lab = self.lab(False)
        n = len(lab.core.log.entries)
        r = run(lab, 'run retsch_as200 open cover')
        self.assertEqual(r.exit_code, 6, r.out + r.err)
        self.assertIn('only the shutter opens', r.err)
        self.assertIn('use the shutter verb', r.err)
        self.assertEqual(native(lab, n), [])
        self.assertEqual(run(lab, 'run retsch_as200 close cover').exit_code, 3)   # close is not a probeopen verb: shared answers it
        out = run(lab, 'run retsch_as200').out
        self.assertIn('not on this instrument: open (only the shutter opens', out)
        shared_part = out[out.find('shared:'):]
        self.assertNotIn('  open ', shared_part)
        self.assertNotIn('open', lab.core.adapters['retsch_as200'].verbs())

    def test_fallthrough_to_shared(self):
        lab = self.lab(True)
        n = len(lab.core.log.entries)
        r = run(lab, 'run retsch_as200 open cover')
        self.assertEqual([c['capability'] for c in native(lab, n)], ['cover.set'], r.out + r.err)
