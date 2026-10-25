"""The generic commands on a small lab: parsing, exit codes, visibility, replay, operations, time, panel, adapter hook.

Run with the matterix python (the runtimes need numpy):  cd chemlab && <matterix python> -m unittest discover -s tests_generic
"""
import json
import os
import re
import shutil
import unittest

from helpers import host_path_re, run, run_json, tmpdir
from chemlab.adapters import InstrumentAdapter
from chemlab.contract_text import type_short
from chemlab.generic import EXIT_REFUSED, EXIT_UNEXPECTED, EXIT_UNSUPPORTED, EXIT_USAGE, CmdError, coerce, json_safe
from chemlab.glab import GenericLab, single
from chemlab.labbackend import load_config
from chemlab.recipes import ADVANCE_MODES, all_recipes

IDS = ['ika_plate_rctdigital', 'retsch_as200', 'vaisala_dmt152', 'mt_xpr204e', 'alicat_mc_1slpm_rev6']


class Pure(unittest.TestCase):
    def test_recipes_and_default_config(self):
        rec = all_recipes()
        self.assertEqual(len(rec), 203)
        self.assertTrue(all(r.get('advance') in ADVANCE_MODES for r in rec.values()))
        cfg = load_config()
        self.assertEqual(len(cfg['instruments']), 203)
        self.assertEqual(len({e['id'] for e in cfg['instruments']}), 203)
        self.assertEqual(sum(1 for e in cfg['instruments'] if 'panel' in e), 106)

    def test_coerce_by_schema(self):
        self.assertEqual(coerce('373.15', {'type': 'number'}, 'x'), 373.15)
        self.assertIs(type(coerce('5', {'type': 'integer'}, 'x')), int)
        self.assertIs(coerce('true', {'type': 'boolean'}, 'x'), True)
        self.assertEqual(coerce('true', {'type': 'string'}, 'x'), 'true')          # strings stay text
        self.assertEqual(coerce('5e-9', {'type': 'number'}, 'x'), 5e-9)              # no unit conversion: SI as given
        self.assertIsNone(coerce('null', {'anyOf': [{'type': 'number'}, {'type': 'null'}]}, 'x'))
        self.assertEqual(coerce('{"a": [1, 2]}', {'type': 'object'}, 'x'), {'a': [1, 2]})
        self.assertEqual(coerce('abc', {'type': ['string', 'null']}, 'x'), 'abc')
        self.assertEqual(coerce('900', {'type': 'number', 'maximum': 10}, 'x'), 900)  # no range check: the runtime checks
        with self.assertRaises(CmdError) as c:
            coerce('maybe', {'type': 'boolean'}, 'on')
        self.assertEqual(c.exception.exit_code, EXIT_USAGE)

    def test_type_short(self):
        self.assertEqual(type_short({'type': 'number', 'x-unit': 'K', 'minimum': 273.15, 'maximum': 583.15}), 'NUM(K, 273.15..583.15)')
        self.assertEqual(type_short({'type': 'string', 'enum': ['a', 'b']}), '{a|b}')
        self.assertEqual(type_short({'anyOf': [{'type': 'number', 'x-unit': 'K'}, {'type': 'null'}]}), 'NUM(K)|null')

    def test_json_safe(self):
        self.assertEqual(json_safe([float('nan'), float('inf'), 1.5]), ['NaN', 'Infinity', 1.5])

    def test_option_names_never_collide_with_contract_arguments(self):
        import glob
        import os
        from helpers import ROOT
        names = set()
        for f in glob.glob(os.path.join(ROOT, '..', 'assets', '**', 'asset.contract.json'), recursive=True):
            with open(f, encoding='utf-8') as fh:
                caps = json.load(fh)['capabilities']
            for c in caps.values():
                names |= set((c.get('input_schema') or {}).get('properties') or {})
        self.assertFalse(names & {'args', 'json', 'help', 'request-id', 'request_id', 'save-to'})


class SmallLab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tmpdir()
        cls.lab = GenericLab(cls.tmp + '/lab', single(IDS), callers=('agent', 'other')).start()

    @classmethod
    def tearDownClass(cls):
        cls.lab.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def calls_since(self, n):
        return [c for e in self.lab.core.log.entries[n:] for c in e['backend_calls'] if c['op'] in ('invoke', 'write', 'cancel')]

    def test_list_and_describe(self):
        code, js = run_json(self.lab, 'list')
        self.assertEqual(code, 0)
        self.assertEqual(sorted(r['id'] for r in js['instruments']), sorted(IDS))
        code, js = run_json(self.lab, 'describe ika_plate_rctdigital')
        caps = {c['id'] for c in js['capabilities']}
        self.assertIn('heating.set_temperature', caps)
        self.assertNotIn('truth.inspect', caps)                                # evaluator-only
        self.assertNotIn('ph.measure', caps)                                   # unsupported
        self.assertNotIn('panel.frame', caps)                                  # evaluator-only panel view
        slots = {s['id'] for s in js['state_slots']}
        self.assertNotIn('truth.process', slots)
        arg = next(c for c in js['capabilities'] if c['id'] == 'heating.set_temperature')['arguments'][0]
        self.assertEqual((arg['name'], arg['unit'], arg['minimum'], arg['maximum']), ('temperature_k', 'K', 273.15, 583.15))
        r = run(self.lab, 'describe ika_plate_rctdigital')
        self.assertIn('heating.set_temperature', r.out)
        self.assertNotIn('truth', r.out)

    def test_help_has_example_and_errors(self):
        r = run(self.lab, 'help ika_plate_rctdigital heating.set_temperature')
        self.assertEqual(r.exit_code, 0)
        self.assertIn('chemlab call ika_plate_rctdigital heating.set_temperature --temperature_k 373.15', r.out)
        self.assertIn('POWER_OFF', r.out)
        self.assertIn('(zh)', r.out)

    def test_hidden_is_the_same_as_nonexistent(self):
        a = run(self.lab, 'call ika_plate_rctdigital truth.inspect')
        b = run(self.lab, 'call ika_plate_rctdigital no.such_capability')
        self.assertEqual((a.exit_code, b.exit_code), (EXIT_USAGE, EXIT_USAGE))
        self.assertEqual(a.err.replace('truth.inspect', 'X').split('request:')[0], b.err.replace('no.such_capability', 'X').split('request:')[0])
        r = run(self.lab, 'read ika_plate_rctdigital truth.process')
        self.assertEqual(r.exit_code, EXIT_USAGE)
        self.assertIn('UNKNOWN_SLOT', r.err)
        r = run(self.lab, 'help mt_xpr204e panel.frame')
        self.assertEqual(r.exit_code, EXIT_USAGE)

    def test_unsupported_is_exit_6(self):
        n = len(self.lab.core.log.entries)
        r = run(self.lab, 'call ika_plate_rctdigital ph.measure')
        self.assertEqual(r.exit_code, EXIT_UNSUPPORTED, r.err)
        self.assertIn('unsupported', r.err)
        self.assertEqual(len(self.calls_since(n)), 1)                         # the runtime answered it itself

    def test_refused_prints_code_contract_and_side_effects(self):
        r = run(self.lab, 'call ika_plate_rctdigital heating.set_temperature --temperature_k 900')
        self.assertEqual(r.exit_code, EXIT_REFUSED)
        self.assertIn('error[refused/INVALID_ARGUMENT]', r.err)
        self.assertIn('contract: 参数不符合输入模式或取值范围', r.err)
        self.assertIn('side effects: none', r.err)
        code, js = run_json(self.lab, 'call ika_plate_rctdigital heating.set_temperature --temperature_k 900')
        self.assertEqual(js['error']['runtime']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(js['error']['side_effects'], 'none')

    def test_bad_usage_sends_nothing(self):
        n = len(self.lab.core.log.entries)
        for cmd in ('call ika_plate_rctdigital power.set --on maybe', 'call ika_plate_rctdigital', 'read nosuch', 'frobnicate',
                    'call ika_plate_rctdigital power.set --args [1]', 'wait 0', 'wait -5', 'screen ika_plate_rctdigital -o ../escape.png',
                    'screen ika_plate_rctdigital -o /etc/x.png', 'panel ika_plate_rctdigital dance', 'write ika_plate_rctdigital power.on true'):
            r = run(self.lab, cmd)
            self.assertEqual(r.exit_code, EXIT_USAGE, cmd + '\n' + r.err)
            self.assertIn('nothing was sent', r.err, cmd)
        self.assertEqual(self.calls_since(n), [])

    def test_request_id_replay(self):
        n = len(self.lab.core.log.entries)
        a = run(self.lab, 'call vaisala_dmt152 power.set --on true --voltage_v 24 --request-id p1')
        b = run(self.lab, 'call vaisala_dmt152 power.set --on true --voltage_v 24 --request-id p1')
        self.assertEqual(a.exit_code, 0, a.err)
        self.assertEqual(a.out, b.out)
        self.assertIn('replayed', b.err)
        self.assertEqual(len(self.calls_since(n)), 1)
        c = run(self.lab, 'call vaisala_dmt152 power.set --on false --request-id p1')
        self.assertEqual(c.exit_code, EXIT_USAGE)
        self.assertIn('REQUEST_ID_REUSED', c.err)
        d = run(self.lab, 'time --request-id r-0001')
        self.assertIn('BAD_REQUEST_ID', d.err)

    def test_runtime_exception_is_exit_4(self):
        host = self.lab.backend.instruments['vaisala_dmt152'].host
        orig = host.invoke

        def boom(*a, **k):
            raise RuntimeError('secret internals %s/x' % os.path.expanduser('~'))
        host.invoke = boom
        try:
            r = run(self.lab, 'call vaisala_dmt152 errors.read')
        finally:
            host.invoke = orig
        self.assertEqual(r.exit_code, EXIT_UNEXPECTED)
        self.assertIn('RuntimeError', r.err)
        self.assertNotIn('secret internals', r.err)

    def test_operation_status_wait_and_ownership(self):
        code, js = run_json(self.lab, 'call retsch_as200 acquisition.start')
        self.assertEqual(code, 0, js)
        op = js['operation']
        code, st = run_json(self.lab, 'status %s' % op)
        self.assertEqual((code, st['status'], st['done']), (0, 'running', False))
        r = run(self.lab, 'status %s' % op, caller='other')                    # another caller cannot see it
        self.assertEqual(r.exit_code, EXIT_USAGE)
        t0 = self.lab.backend.now()
        code, w = run_json(self.lab, 'wait %s' % op)
        self.assertEqual((code, w['status'], w['done']), (0, 'succeeded', True))
        self.assertGreater(self.lab.backend.now(), t0)
        code, mine = run_json(self.lab, 'status')
        self.assertIn(op, [o['handle'] for o in mine['operations']])

    def test_wait_moves_every_clock(self):
        t0 = self.lab.backend.now()
        r = run(self.lab, 'wait 2.5')
        self.assertEqual(r.exit_code, 0, r.err)
        self.assertAlmostEqual(self.lab.backend.now(), t0 + 2.5)
        for iid in IDS:
            self.assertAlmostEqual(self.lab.backend.runtime_clock(iid), t0 + 2.5, places=6)
        self.assertEqual(run(self.lab, 'wait 1e9').exit_code, EXIT_USAGE)

    def test_write_and_contract_slot_ids(self):
        r = run(self.lab, 'write mt_xpr204e balance.filter_level High')
        self.assertEqual(r.exit_code, 0, r.err)
        code, js = run_json(self.lab, 'read mt_xpr204e balance.filter_level')
        self.assertEqual(js['observations']['balance.filter_level']['value'], 'High')
        # runtime and contract slot ids are identical (alicat 1.4.1 and later): no case aliases
        code, js = run_json(self.lab, 'read alicat_mc_1slpm_rev6 measurement.pressure_pa')
        self.assertEqual(code, 0, js)
        self.assertEqual(list(js['observations']), ['measurement.pressure_pa'])
        self.assertEqual(run(self.lab, 'read alicat_mc_1slpm_rev6 measurement.pressure_Pa').exit_code, EXIT_USAGE)
        code, js = run_json(self.lab, 'read alicat_mc_1slpm_rev6')
        self.assertIn('gas.reference_temperature_k', js['observations'])
        self.assertFalse([k for k in js['observations'] if k != k.lower()])

    def test_screen_and_panel(self):
        r = run(self.lab, 'screen mt_xpr204e -o shots/a.png')
        self.assertEqual(r.exit_code, 0, r.err)
        f = self.lab.work / 'shots' / 'a.png'
        self.assertTrue(f.read_bytes().startswith(b'\x89PNG'))
        resp = self.lab.backend.invoke('agent', 'mt_xpr204e', 'panel.capture', {'display_id': 'terminal'}, 'cap-test')
        self.assertEqual(f.read_bytes(), self.lab.backend.capture_file(resp).read_bytes())
        r = run(self.lab, 'panel ika_plate_rctdigital press menu_key')
        self.assertEqual(r.exit_code, 0, r.err)
        self.assertIn('accepted', r.out)
        r = run(self.lab, 'panel ika_plate_rctdigital turn knob_temperature 3')
        self.assertEqual(r.exit_code, 0, r.err)
        r = run(self.lab, 'panel vaisala_dmt152 press x')
        self.assertEqual(r.exit_code, EXIT_USAGE)

    def test_log_is_per_caller_and_json_everywhere(self):
        run(self.lab, 'time', caller='other')
        code, js = run_json(self.lab, 'log --last 1000')
        self.assertTrue(all('other' not in json.dumps(e) for e in js['entries'] if 'caller' in e))
        code, other = run_json(self.lab, 'log --last 1000', caller='other')
        mine = {e['request_id'] for e in js['entries']}
        self.assertFalse(mine & {e['request_id'] for e in other['entries']})
        for cmd in ('list', 'time', 'status', 'describe retsch_as200', 'help retsch_as200 acquisition.start', 'read retsch_as200',
                    'help mt_xpr204e panel', 'call vaisala_dmt152 errors.read', 'wait 1', 'call x y', 'log'):
            r = run(self.lab, cmd + ' --json')
            json.loads(r.out)

    def test_no_host_paths_in_outputs(self):
        for e in self.lab.core.log.entries:
            self.assertNotRegex(e['stdout'] + e['stderr'], host_path_re())


class Probe(InstrumentAdapter):
    """A step-2 style adapter for the hook test: one class verb and an intercepted describe."""

    def verbs(self):
        return {'hello': 'say hello through the hook'}

    def intercept(self, core, req):
        if req.command == 'describe' and '--json' not in req.args:
            return None
        return None

    def run(self, core, req, verb, args):
        r = core.backend.read(req.caller, req.instrument, None)
        return {'text': 'hello from %s at t=%.1f\n' % (req.instrument, r['clock']['time_s']), 'json': {'verb': verb}}


class AdapterHook(unittest.TestCase):
    def test_hook_empty_by_default_and_pluggable(self):
        tmp = tmpdir()
        try:
            cfg = single(['vaisala_dmt152'])
            cfg['class_adapters'] = False                     # no class fragments: the hook is empty (compile layer off)
            lab = GenericLab(tmp + '/a', cfg)
            r = run(lab, 'run vaisala_dmt152 hello')
            self.assertEqual(r.exit_code, EXIT_USAGE)
            self.assertIn('no class-level verbs yet', r.err)
            lab2 = GenericLab(tmp + '/b', single(['vaisala_dmt152']), adapters={'vaisala_dmt152': Probe('vaisala_dmt152', {})})
            r = run(lab2, 'run vaisala_dmt152 hello')
            self.assertEqual(r.exit_code, 0, r.err)
            self.assertIn('hello from vaisala_dmt152', r.out)
            self.assertIn('class-level verbs', run(lab2, 'describe vaisala_dmt152').out)
            self.assertEqual(run(lab2, 'read vaisala_dmt152').exit_code, 0)                    # generic commands unchanged
            self.assertEqual(run(lab2, 'call vaisala_dmt152 power.set --on true').exit_code, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
