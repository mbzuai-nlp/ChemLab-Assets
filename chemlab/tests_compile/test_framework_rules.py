"""Framework rules of the compile layer: verb ownership on multi-class members, job states, generic usage errors from class
handlers, mapping check of slot references, requirement tests, INST in notes, non-zero formatting, units, the shared method's
full argument set, the 8697 event clock, lowering helpers and lenient adapter loading."""
import copy
import json
import os
import shutil
import unittest

from helpers import native, run, run_json, tmpdir
from chemlab.compile import fragments, jobs, units
from chemlab.compile.base import ClassAdapter
from chemlab.compile.fragments import adapters_for, load_fragments
from chemlab.compile.verbs import Verb
from chemlab.generic import usage
from chemlab.glab import GenericLab, single

SHARED = {
    'class': 'shared', 'layer': 'shared', 'adapter': 'chemlab.compile.shared:SharedAdapter',
    'members': {'retsch_as200': {
        'verbs': {'power': {'cap': 'power.set', 'arg': 'enabled'},
                  'open': {'parts': {'cover': {'cap': 'cover.set', 'arg': 'lift_m', 'open': 0.14, 'close': 0.0}}},
                  'method': {'cap': 'method.configure', 'keys': {'time': {'arg': 'duration_s', 'kind': 'duration'},
                                                                'lift': {'arg': 'total_lift_m', 'kind': 'length'}}},
                  'start': {'cap': 'acquisition.start', 'job': 'operation'},
                  'stop': {'cap': 'acquisition.stop'},
                  'result': {'cap': 'sieving.measure'}},
        'path_exceptions': ['sieving.measure'],
        'slots': {'power': 'power.enabled'}, 'status_slots': ['cycle.phase', 'power.enabled']}}}


class NoOpenAdapter(ClassAdapter):
    """Test class A: has `open` and `status`; the member marks `open` unsupported."""
    CLASS = 'probea'
    TITLE = 'test class A'

    def build_verbs(self):
        return {'open': Verb('open', 'open (class A)', lambda ctx, p: {'text': 'A opened\n'}),
                'bad': Verb('bad', 'raises a generic usage error', self.h_bad)}

    def h_bad(self, ctx, p):
        raise usage('BAD_VALUE', 'a generic usage error from a class handler (test).', 'nothing to fix (test)')


class OpenAdapter(ClassAdapter):
    """Test class B: supports `open` (no native call)."""
    CLASS = 'probeb'
    TITLE = 'test class B'

    def build_verbs(self):
        return {'open': Verb('open', 'open (class B)', lambda ctx, p: {'text': 'B opened\n'})}


class RunClassAdapter(ClassAdapter):
    """Test class C: replaces the shared `start` by its own verb (class HIDES)."""
    CLASS = 'probec'
    TITLE = 'test class C'
    HIDES = {'start': 'use `chemlab run INST go` (test)'}

    def build_verbs(self):
        return {'go': Verb('go', 'start a run (class C)', lambda ctx, p: {'text': 'C started\n'})}


class BrokenAdapter(ClassAdapter):
    CLASS = 'probebroken'

    def __init__(self, *a, **k):
        raise AttributeError('broken on purpose (test)')


def _lab(frags):
    tmp = tmpdir()
    d = os.path.join(tmp, 'fragments')
    os.makedirs(d)
    for f in frags:
        with open(os.path.join(d, f['class'] + '.json'), 'w') as fh:
            json.dump(f, fh)
    cfg = single(['retsch_as200'])
    cfg['class_adapters'] = d
    return tmp, GenericLab(tmp + '/lab', cfg)


def frag(cls, adapter, member, role='primary'):
    m = dict(member, role=role)
    names = {'NoOpenAdapter': ['open', 'bad'], 'OpenAdapter': ['open'], 'RunClassAdapter': ['go']}.get(adapter, [])
    m.setdefault('verbs', {n: {} for n in names})                         # the test classes' verbs need no mapping data
    return {'class': cls, 'adapter': 'test_framework_rules:' + adapter, 'members': {'retsch_as200': m}}


class Ownership(unittest.TestCase):
    """A class's "unsupported" blocks only the shared layer; another class that supports the verb still answers it."""

    def lab(self, frags):
        tmp, lab = _lab(frags)
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(lab.stop)
        return lab

    def test_two_class_member_other_class_wins(self):
        lab = self.lab([SHARED, frag('probea', 'NoOpenAdapter', {'unsupported': {'open': 'class A has no opening (test)'}}),
                        frag('probeb', 'OpenAdapter', {}, role='secondary')])
        ad = lab.core.adapters['retsch_as200']
        self.assertEqual(ad.owner('open').CLASS, 'probeb')
        r = run(lab, 'run retsch_as200 open')
        self.assertEqual((r.exit_code, r.out), (0, 'B opened\n'), r.err)
        out = run(lab, 'run retsch_as200').out
        self.assertNotIn('class A has no opening', out)                  # A does not claim a verb B answers
        self.assertIn('open (class B)', out)

    def test_class_unsupported_still_beats_shared(self):
        lab = self.lab([SHARED, frag('probea', 'NoOpenAdapter', {'unsupported': {'open': 'class A has no opening (test)'}})])
        n = len(lab.core.log.entries)
        r = run(lab, 'run retsch_as200 open cover')
        self.assertEqual(r.exit_code, 6, r.out + r.err)
        self.assertIn('class A has no opening', r.err)
        self.assertEqual(native(lab, n), [])

    def test_real_member_agitation_plus_thermal(self):
        """ika_ks4000: agitation (primary) marks set-temp unsupported; thermal (secondary) supports it -> thermal answers, with or
        without "shared_fallthrough" in the agitation fragment."""
        frs = {f['class']: f for f in load_fragments()}
        if 'agitation' not in frs or 'ika_ks4000' not in frs['agitation']['members']:
            self.skipTest('no agitation fragment')
        agit = copy.deepcopy(frs['agitation'])
        agit['members']['ika_ks4000'].pop('shared_fallthrough', None)
        agit['members']['ika_ks4000'].setdefault('unsupported', {})['set-temp'] = 'thermal verbs handle it (test)'
        ad = adapters_for(single(['ika_ks4000']), [agit, frs['thermal'], frs['shared']], strict=True)['ika_ks4000']
        self.assertEqual(ad.owner('set-temp').CLASS, 'thermal')
        self.assertIn('set-temp', ad.verbs())

    def test_hidden_shared_verb(self):
        lab = self.lab([SHARED, frag('probec', 'RunClassAdapter', {})])
        n = len(lab.core.log.entries)
        r = run(lab, 'run retsch_as200 start')
        self.assertEqual(r.exit_code, 6, r.out + r.err)
        self.assertIn('use `chemlab run retsch_as200 go`', r.err)
        self.assertEqual(native(lab, n), [])
        self.assertNotIn('start', lab.core.adapters['retsch_as200'].verbs())
        h = run(lab, 'help retsch_as200 start')
        self.assertEqual(h.exit_code, 0, h.err)
        self.assertIn('replaced here', h.out)
        self.assertIn('start (replaced here: use `chemlab run retsch_as200 go`', run(lab, 'run retsch_as200').out)
        self.assertEqual(run(lab, 'run retsch_as200 go').out, 'C started\n')

    def test_member_hides_and_fallthrough(self):
        lab = self.lab([SHARED, frag('probeb', 'OpenAdapter', {'hides': {'stop': 'stop with the class verb (test)'}})])
        self.assertEqual(run(lab, 'run retsch_as200 stop').exit_code, 6)
        lab = self.lab([SHARED, frag('probec', 'RunClassAdapter', {'shared_fallthrough': ['start']})])
        self.assertEqual(lab.core.adapters['retsch_as200'].owner('start').CLASS, 'shared')

    def test_generic_usage_error_from_a_handler_is_usage(self):
        lab = self.lab([SHARED, frag('probea', 'NoOpenAdapter', {})])
        r = run(lab, 'run retsch_as200 bad')
        self.assertEqual(r.exit_code, 2, r.out + r.err)
        self.assertIn('BAD_VALUE', r.err)
        r = run(lab, 'run retsch_as200 method duration_s=[1')
        self.assertEqual(r.exit_code, 2, r.out + r.err)

    def test_lenient_loading(self):
        n = len(fragments.LOAD_ERRORS)
        lab = self.lab([SHARED, frag('probebroken', 'BrokenAdapter', {})])
        self.assertEqual(lab.core.adapters['retsch_as200'].CLASS, 'shared')    # the lab loads without the broken class
        self.assertTrue(any(e[0] == 'probebroken' for e in fragments.LOAD_ERRORS[n:]))
        with self.assertRaises(AttributeError):
            adapters_for(single(['retsch_as200']), [SHARED, frag('probebroken', 'BrokenAdapter', {})], strict=True)


class SharedMethod(unittest.TestCase):
    """The shared `method` sends the full argument set; values not given come from the instrument, the last method chemlab
    sent, or the contract example, and the reply says which."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.lab = _lab([SHARED])
        run(cls.lab, 'run retsch_as200 power on')

    @classmethod
    def tearDownClass(cls):
        cls.lab.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_1_partial_method_is_completed_from_the_example(self):
        n = len(self.lab.core.log.entries)
        r = run(self.lab, 'run retsch_as200 method time=2min')
        self.assertEqual(r.exit_code, 0, r.err)
        self.assertIn("from the contract's example method", r.out)
        self.assertIn('total_lift_m=1.2 mm', r.out)
        calls = [(c['capability'], c['arguments']) for c in native(self.lab, n) if c['op'] == 'invoke']
        self.assertEqual(calls, [('method.configure', {'duration_s': 120.0, 'total_lift_m': 0.0012, 'interval_enabled': True,
                                                       'interval_drive_s': 3.0})])
        code, js = run_json(self.lab, 'run retsch_as200 method time=2min')
        self.assertEqual(code, 0, js)
        self.assertEqual(js['sources'], {'duration_s': 'given', 'total_lift_m': 'last', 'interval_enabled': 'last',
                                         'interval_drive_s': 'last'})

    def test_2_last_sent_beats_the_example(self):
        self.assertEqual(run(self.lab, 'run retsch_as200 method lift=2mm interval_drive_s=5').exit_code, 0)
        n = len(self.lab.core.log.entries)
        code, js = run_json(self.lab, 'run retsch_as200 method time=3min')
        self.assertEqual(code, 0, js)
        args = [c['arguments'] for c in native(self.lab, n) if c['op'] == 'invoke'][0]
        self.assertEqual(args, {'duration_s': 180.0, 'total_lift_m': 0.002, 'interval_enabled': True, 'interval_drive_s': 5.0})
        self.assertEqual(js['sources']['total_lift_m'], 'last')

    def test_3_out_of_range_is_a_usage_error_and_dry_run_shows_the_full_call(self):
        n = len(self.lab.core.log.entries)
        r = run(self.lab, 'run retsch_as200 method time=200h')
        self.assertEqual(r.exit_code, 2, r.out + r.err)
        self.assertIn('BAD_SETTING', r.err)
        r = run(self.lab, 'run retsch_as200 method time=2min --dry-run')
        self.assertEqual(r.exit_code, 0, r.err)
        self.assertIn('interval_drive_s', r.out)
        self.assertEqual([c for c in native(self.lab, n) if c['op'] == 'invoke'], [])

    def test_4_no_source_is_a_usage_error(self):
        tmp, lab = _lab([SHARED])
        try:
            inst = lab.backend.instruments['retsch_as200']
            inst.contract = copy.deepcopy(inst.contract)
            inst.contract['capabilities']['method.configure']['example_arguments'] = {}
            run(lab, 'run retsch_as200 power on')
            r = run(lab, 'run retsch_as200 method time=2min')
            self.assertEqual(r.exit_code, 2, r.out + r.err)
            self.assertIn('MISSING_SETTING', r.err)
            self.assertIn('total_lift_m', r.err)
        finally:
            lab.stop()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_5_reported_values_are_kept(self):
        frs = {f['class']: f for f in load_fragments()}
        if 'keyence_vkx3000' not in frs['shared']['members']:
            self.skipTest('keyence_vkx3000 not in the shared fragment')
        tmp = tmpdir()
        cfg = single(['keyence_vkx3000'])
        cfg['class_adapters'] = ['shared']
        lab = GenericLab(tmp + '/lab', cfg)
        try:
            run(lab, 'run keyence_vkx3000 power on')
            code, js = run_json(lab, 'run keyence_vkx3000 method gain=2')
            self.assertEqual(code, 0, js)
            self.assertEqual(js['sources']['mode'], 'instrument:measurement.settings#mode')
            self.assertIn('kept from what the instrument reports', run(lab, 'run keyence_vkx3000 method gain=3').out)
        finally:
            lab.stop()
            shutil.rmtree(tmp, ignore_errors=True)


class Small(unittest.TestCase):
    def test_job_states(self):
        self.assertIn('injecting', jobs.RUNNING)
        self.assertIn('some_member_phase', jobs.RUNNING)               # unknown non-terminal: running
        for st in ('completed', 'succeeded', 'failed', 'cancelled', 'idle', None, ''):
            self.assertNotIn(st, jobs.RUNNING)
        self.assertEqual(jobs.op_status({'status': 'succeeded', 'result': {'phase': 'injecting'}}), 'injecting')
        self.assertEqual(jobs.op_status({'status': 'succeeded', 'result': {'status': 'measured'}}), 'succeeded')
        self.assertIn('running', tuple(jobs.RUNNING))

    def test_requirement_ops(self):
        ok = ClassAdapter.requirement_ok
        self.assertTrue(ok('standard:zero_air', {'in': ['standard:zero_air']}))
        self.assertFalse(ok('sample', {'in': ['standard:zero_air']}))
        self.assertTrue(ok('standard:srm_2631a', {'prefix': ['standard:srm']}))
        self.assertTrue(ok('standard:srm_2631a', {'prefix': 'standard:'}))
        self.assertFalse(ok(None, {'prefix': ['standard:srm']}))
        self.assertTrue(ok(['low_flow'], {'contains_not': 'over_range'}))
        self.assertFalse(ok(['over_range'], {'contains_not': 'over_range'}))
        self.assertTrue(ok(None, {'contains_not': 'over_range'}))
        self.assertTrue(ok(5, {'min': 5, 'max': 6}))
        self.assertFalse(ok(None, {'max': 6}))
        self.assertTrue(ok(True, {'value': True}))
        self.assertFalse(ok(1, {'not': [1, 2]}))
        self.assertTrue(ok(0, {'falsy': True}))
        self.assertTrue(ok(None, {'null': True}))
        self.assertTrue(ok('x', {}))

    def test_mapping_slot_references(self):
        tmp, lab = _lab([SHARED])
        try:
            ad = lab.core.adapters['retsch_as200']
            ad.bind(lab.core)
            inst = lab.backend.instruments['retsch_as200']
            base = copy.deepcopy(ad.map)
            ad.map['verbs']['result'] = {'cap': 'sieving.measure', 'done': {'slot': 'cycle.phase#state'}, 'rack': {'slot': 'A1'},
                                         'pos': {'slot': 3}}
            self.assertEqual([p for p in ad.check_mapping(inst) if 'slot' in p], [])
            ad.map['verbs']['result'] = {'cap': 'sieving.measure', 'done': {'slot': 'nope.phase#state'}}
            self.assertTrue(any('nope.phase' in p for p in ad.check_mapping(inst)))
            ad.map = base
        finally:
            lab.stop()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_inst_in_notes(self):
        tmp, lab = _lab([SHARED])
        try:
            ad = lab.core.adapters['retsch_as200']
            ad.map.setdefault('notes', {})['method'] = ['see `chemlab describe INST` (test)']
            self.assertIn('see `chemlab describe retsch_as200` (test)', run(lab, 'help retsch_as200 method').out)
        finally:
            lab.stop()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_nonzero_formatting(self):
        self.assertEqual(units.fmt(1.0, 'pressure'), '0.01 mbar')
        self.assertEqual(units.fmt(5.0, 'pressure'), '0.05 mbar')                 # sub-mbar with 3 significant digits
        self.assertEqual(units.fmt(12.34, 'pressure'), '0.123 mbar')
        self.assertEqual(units.fmt(20000.0, 'pressure'), '200 mbar')
        self.assertEqual(units.fmt(0.0, 'pressure'), '0 mbar')
        self.assertEqual(units.fmt_s(0.02), '20 ms')
        self.assertEqual(units.fmt_s(2e-6), '2 µs')
        self.assertEqual(units.fmt_s(0), '0 s')
        self.assertEqual(units.fmt_s(30), '30 s')
        self.assertEqual(units.fmt(2e-6, 'fraction'), '2 ppm')
        self.assertEqual(units.fmt(5e-12, 'force'), '5 pN')

    def test_units_more(self):
        self.assertAlmostEqual(units.parse('100sccm', 'flow'), 100e-6 / 60)
        self.assertAlmostEqual(units.parse('1.5slpm', 'flow'), 1.5e-3 / 60)
        self.assertAlmostEqual(units.parse('2slm', 'flow'), 2e-3 / 60)
        self.assertAlmostEqual(units.parse('3sccs', 'flow'), 3e-6)
        self.assertAlmostEqual(units.parse('2m3/h', 'flow'), 2 / 3600)
        self.assertAlmostEqual(units.parse('30L/h', 'flow'), 30e-3 / 3600)
        self.assertAlmostEqual(units.parse_for('1L/min', {'type': 'number', 'x-unit': 'L/h'}), 60.0)
        self.assertAlmostEqual(units.parse_for('1L/min', {'type': 'number', 'x-unit': 'm3/h'}), 0.06)
        self.assertAlmostEqual(units.parse_for('500sccm', {'type': 'number', 'x-unit': 'SLPM'}), 0.5)
        self.assertAlmostEqual(units.parse('5ppm', 'fraction'), 5e-6)
        self.assertAlmostEqual(units.parse('7ppb', 'fraction'), 7e-9)
        self.assertAlmostEqual(units.parse_for('1%', {'type': 'number', 'x-unit': 'ppm'}), 10000.0)
        self.assertAlmostEqual(units.parse('5pN', 'force'), 5e-12)
        self.assertAlmostEqual(units.parse('5nN', 'force'), 5e-9)
        self.assertAlmostEqual(units.parse('5uN', 'force'), 5e-6)
        self.assertAlmostEqual(units.parse('5µN', 'force'), 5e-6)
        self.assertAlmostEqual(units.parse('2us', 'duration'), 2e-6)
        self.assertAlmostEqual(units.parse('2μs', 'duration'), 2e-6)
        self.assertAlmostEqual(units.from_declared(1.0, 'eV'), 1.602176634e-19)
        self.assertAlmostEqual(units.parse_for('8keV', {'type': 'number', 'x-unit': 'eV'}), 8000.0)
        self.assertEqual(units.sig(0.02 * 3.141592653589793 / 180), 0.000349065850398866)
        self.assertEqual(units.sig(353.15000000000003), 353.15)


if __name__ == '__main__':
    unittest.main()


class HS8697Clock(unittest.TestCase):
    """The Agilent 8697 runtime moves its events only in host_update().  Its host recipe now has the advance mode
    clock_host_update (hosts.RuntimeHost.advance calls host_update() after every clock step); a recipe without an advance mode
    gets the lab's clock hook instead.  Either way a started headspace sequence finishes in the default lab (world: vials and GC as
    the lab operator sets them up, no scene)."""

    def test_sequence_finishes_without_the_scene(self):
        from chemlab.compile.tasks.sample_intro_world import config
        from chemlab.labbackend import load_config
        cfg = config()
        cfg['instruments'] = [e for e in cfg['instruments'] if e['id'] == 'agilent_hs8697']
        for e in cfg['instruments']:
            e.pop('scene', None)
        self.assertIn('agilent_hs8697', [e['id'] for e in load_config()['instruments']])
        tmp = tmpdir()
        lab = GenericLab(tmp + '/lab', cfg)
        try:
            inst = lab.backend.instruments['agilent_hs8697']
            self.assertTrue(inst.recipe.get('advance') == 'clock_host_update' or inst.clock_hook == 'host_update',
                            (inst.recipe.get('advance'), inst.clock_hook))
            for c in ('run agilent_hs8697 load v1 --vial 1', 'run agilent_hs8697 set-temp 70C --zone oven',
                      'run agilent_hs8697 set-temp 80C --zone loop', 'run agilent_hs8697 set-temp 90C --zone transfer',
                      'run agilent_hs8697 method equilibration=10min'):
                r = run(lab, c)
                self.assertEqual(r.exit_code, 0, c + '\n' + r.out + r.err)
            code, js = run_json(lab, 'run agilent_hs8697 start --vials 1')
            self.assertEqual(code, 0, js)
            code, w = run_json(lab, 'wait %s --max 2h' % js['job'])
            self.assertEqual(code, 0, w)
            self.assertTrue(w['done'], w)
            self.assertEqual(w['status'], 'succeeded', w)
        finally:
            lab.stop()
            shutil.rmtree(tmp, ignore_errors=True)


class LoweringHelpers(unittest.TestCase):
    """('#host', fn) steps on lab and twin; Measured / Obs matchers in expected arguments; callable expectations."""

    def test_matchers(self):
        from chemlab.compile.lowering import Measured, Obs, calls_match
        got = [('analysis.fit', {'points': [{'x': 1.0, 'y': 0.52}, {'x': 2.0, 'y': 1.01}], 'gain': 3.2, 'label': 'a'})]
        self.assertTrue(calls_match(got, [('analysis.fit', {'points': Obs(2, {}, ['x', 'y']), 'gain': Measured(), 'label': 'a'})]))
        self.assertTrue(calls_match(got, [('analysis.fit', {'points': Obs(2, [{'x': 1.0}, {'x': 2.0}], ['y']), 'gain': Measured(),
                                                            'label': 'a'})]))
        self.assertFalse(calls_match(got, [('analysis.fit', {'points': Obs(3, {}, ['x']), 'gain': Measured(), 'label': 'a'})]))
        self.assertFalse(calls_match(got, [('analysis.fit', {'points': Obs(2, {}, ['z']), 'gain': Measured(), 'label': 'a'})]))
        self.assertFalse(calls_match([('a', {'v': None})], [('a', {'v': Measured()})]))
        self.assertTrue(calls_match(got, [('analysis.fit', lambda a: len(a['points']) == 2)]))
        self.assertTrue(calls_match(got, lambda g: g[0][0] == 'analysis.fit'))

    def test_host_step_runs_on_lab_and_twin(self):
        from chemlab.compile.lowering import check_member
        seen = []

        def swap(backend):
            seen.append(backend)
            backend.advance(2.0)
        tmp = tmpdir()
        try:
            out = check_member('retsch_as200', {'steps': [('status', []), ('#host swap the sample', swap), ('status', [])]},
                               log=lambda *a: None, run_root=tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(len(seen), 2)
        self.assertIsNot(seen[0], seen[1])
        self.assertEqual([r['outcome'] for r in out['steps']][1], 'host')
        self.assertTrue(out['ok'], out)


class MoreUnits(unittest.TestCase):
    """Amount of substance, X-ray energies, mass concentration display."""

    def test_amount_energy_concentration(self):
        self.assertAlmostEqual(units.parse('2umol', 'amount'), 2e-6)
        self.assertAlmostEqual(units.parse('5pmol', 'amount'), 5e-12)
        self.assertAlmostEqual(units.parse_for('3µmol', {'type': 'number', 'x-unit': 'mol'}), 3e-6)
        self.assertEqual(units.fmt(2e-3, 'amount'), '2 mmol')
        self.assertEqual(units.fmt(5e-9, 'amount'), '5 nmol')
        self.assertEqual(units.fmt(8 * 1.602176634e-16, 'energy'), '8 keV')
        self.assertEqual(units.fmt(998.2, 'density'), '0.9982 g/cm³')
        self.assertEqual(units.fmt(0.005, 'density'), '5 mg/L')
        self.assertEqual(units.fmt(2e-6, 'density'), '2 µg/L')
        self.assertAlmostEqual(units.parse_for('5mg/L', {'type': 'number', 'x-unit': 'kg/m^3'}), 0.005)
        self.assertAlmostEqual(units.parse_for('5mg/L', {'type': 'number', 'x-unit': 'ug/L'}), 5000.0)

    def test_covers(self):
        tmp, lab = _lab([SHARED, frag('probeb', 'OpenAdapter', {'covers': {'start': 'open'}})])
        try:
            r = run(lab, 'run retsch_as200 start')
            self.assertEqual(r.exit_code, 6, r.out + r.err)
            self.assertIn('use `chemlab run retsch_as200 open`', r.err)
        finally:
            lab.stop()
            shutil.rmtree(tmp, ignore_errors=True)
        # the covering verb unsupported on this member: the shared verb stays
        tmp, lab = _lab([SHARED, frag('probeb', 'OpenAdapter', {'covers': {'start': 'open'},
                                                                'unsupported': {'open': 'no opening here (test)'}})])
        try:
            self.assertEqual(lab.core.adapters['retsch_as200'].owner('start').CLASS, 'shared')
        finally:
            lab.stop()
            shutil.rmtree(tmp, ignore_errors=True)


class ConcentrationUnits(unittest.TestCase):
    def test_units(self):
        self.assertAlmostEqual(units.parse('5uM', 'concentration'), 5e-3)
        self.assertAlmostEqual(units.parse('5nM', 'concentration'), 5e-6)
        self.assertAlmostEqual(units.parse('5pM', 'concentration'), 5e-9)
        self.assertAlmostEqual(units.parse_for('5pM', {'type': 'number', 'x-unit': 'nM'}), 0.005)
        self.assertAlmostEqual(units.parse('3µcal/s', 'power'), 3 * 4.184e-6)
        self.assertAlmostEqual(units.parse('2uW', 'power'), 2e-6)
        self.assertAlmostEqual(units.parse_for('300rpm', {'type': 'number', 'x-unit': '1/min'}), 300.0)
        self.assertAlmostEqual(units.parse_for('10sccm', {'type': 'number', 'x-unit': 'cm^3/min'}), 10.0)
        self.assertEqual(units.fmt(2e-6, 'power'), '2 µW')

    def test_temperature_difference_is_not_celsius(self):
        self.assertIsNone(units.friendly(50, 'K', 'safety.tdiff_max_k'))
        self.assertIsNone(units.friendly(5, 'K', 'jacket.delta_k'))
        self.assertIsNone(units.friendly(5, 'K', 'x.limit_k', '夹套与反应物温差上限'))
        self.assertEqual(units.friendly(323.15, 'K', 'thermal.temperature_k'), '50.0 °C')

    def test_atomic_write_and_retry(self):
        import threading
        from chemlab.compile.fragments import write_json_atomic
        tmp = tmpdir()
        try:
            p = os.path.join(tmp, 'probe.json')
            write_json_atomic(p, {'class': 'probe', 'members': {}})
            self.assertEqual(sorted(os.listdir(tmp)), ['probe.json'])
            with open(p, 'w') as f:
                f.write('{"class": "pro')                            # half written
            t = threading.Timer(0.15, write_json_atomic, (p, {'class': 'probe', 'members': {'x': {}}}))
            t.start()
            frs = load_fragments(tmp)
            t.join()
            self.assertEqual(frs[0]['members'], {'x': {}})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class MoreKinds(unittest.TestCase):
    def test_kinds(self):
        self.assertAlmostEqual(units.parse('10mT/s', 'field_rate'), 0.01)
        self.assertAlmostEqual(units.parse('6mN/min', 'force_rate'), 1e-4)
        self.assertAlmostEqual(units.parse('5kohm', 'resistance'), 5000.0)
        self.assertAlmostEqual(units.parse('2megohm/sq', 'sheet_resistance'), 2e6)
        self.assertAlmostEqual(units.parse('3.47emu', 'moment'), 3.47e-3)
        self.assertAlmostEqual(units.parse('8.7kJ/K', 'heat_capacity'), 8700.0)
        self.assertAlmostEqual(units.parse('2.5', 'number'), 2.5)
        self.assertEqual(units.unit_kind('ohm'), 'resistance')
        self.assertEqual(units.unit_kind('A*m^2'), 'moment')
        self.assertEqual(units.fmt(1500.0, 'resistance'), '1.5 kΩ')

    def test_operation_job_poll_interval(self):
        class Ad:
            def __init__(self, params):
                self.params = params

            def param(self, name, default=None):
                return self.params.get(name, default)
        tmp, lab = _lab([SHARED])
        try:
            from chemlab.generic import Req
            req = Req('run', 'retsch_as200', [], 'agent', 'r-x', {})
            self.assertEqual(jobs.OperationJob(lab.core, Ad({}), req, 'd', 'acquisition.start', 'op-1').poll_s(), 1.0)
            self.assertEqual(jobs.OperationJob(lab.core, Ad({'job_poll_s': 30}), req, 'd', 'acquisition.start', 'op-1').poll_s(), 30.0)
            self.assertEqual(jobs.OperationJob(lab.core, Ad({'job_poll_s': 30}), req, 'd', 'acquisition.start', 'op-1', poll_s=5).poll_s(), 5.0)
        finally:
            lab.stop()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_wait_until(self):
        self.assertTrue(callable(ClassAdapter.wait_until))
        t = ClassAdapter.moving_done(['a.moving', 'b.moving'])

        class C:
            def __init__(self, v):
                self.view = v

            def v(self, s):
                return self.view.get(s)
        self.assertTrue(t(C({'a.moving': False, 'b.moving': None})))
        self.assertFalse(t(C({'a.moving': True})))
