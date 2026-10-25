"""The default lab (configs/all203.json): every instrument runs its main workflow as the agent, with no task-world set-up.

For each instrument (default_world_smoke.json) a short workflow of chemlab commands runs in a one-instrument lab built from
the default config, then a check command:
  * gold:   every step succeeds, the check succeeds and (when the spec has "expect") its output matches the pattern;
  * no-act: in a fresh default lab, the check command alone must NOT give that result (it fails, or the pattern is missing,
            or for specs without a pattern the output differs) - doing nothing does not produce the result.  Specs with
            "noact_exempt" (the reason) skip this: the two external-calibration balances, whose loads need a host or the robot.

Steps: "VERB ..." is `chemlab run INST VERB ...`; "#wait N" is `chemlab wait N` (lab time); "!CMD" is a generic command
(`chemlab CMD`, e.g. `call INST cap ...`, `read INST slot`).  Also checks the default lab's inventory and load time.

    cd chemlab/tests_generic && <matterix python> -m unittest test_default_world      (about 4 minutes)
"""
import json
import os
import re
import shlex
import shutil
import time
import unittest

from helpers import ROOT, tmpdir
from chemlab.glab import GenericLab, single
from chemlab.labbackend import load_config, LabBackend

SPEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'default_world_smoke.json')


def argv_of(slug, c):
    c = c.replace('INST', slug)
    if c.startswith('#wait '):
        return ['wait', c.split()[1]]
    if c.startswith('!'):
        return shlex.split(c[1:])
    return ['run', slug] + shlex.split(c)


def _norm(text):
    """Output without times and request ids, to compare a no-act output with gold."""
    text = re.sub(r'request: r-\d+', '', text)
    return re.sub(r't = [0-9.]+ s|lab time [0-9.]+ s|at t = [0-9.]+ s|state revision \d+', '', text)


def run_spec(cfg, slug, sp, noact=False):
    """Run one spec; returns {'ok', 'rows', 'check': {'exit', 'out', 'match'}}."""
    t = tmpdir()
    rows, chk = [], None
    try:
        lab = GenericLab(os.path.join(t, 'lab'), single([slug], cfg))
        try:
            for c in ([] if noact else sp['steps']):
                r = lab.core.handle(['chemlab'] + argv_of(slug, c), 'agent')
                rows.append({'cmd': c, 'exit': r.exit_code, 'err': (r.err or '')[:300]})
                if r.exit_code != 0:
                    break
            if all(r['exit'] == 0 for r in rows):
                r = lab.core.handle(['chemlab'] + argv_of(slug, sp['check']), 'agent')
                out = (r.out or '') + (r.err or '')
                chk = {'exit': r.exit_code, 'out': out[:4000],
                       'match': bool(re.search(sp['expect'], out, re.S)) if sp.get('expect') else None}
        finally:
            lab.stop()
    finally:
        shutil.rmtree(t, ignore_errors=True)
    ok = chk is not None and chk['exit'] == 0 and chk['match'] is not False
    return {'ok': ok, 'rows': rows, 'check': chk}


def noact_differs(gold, noact):
    """True when doing nothing does not give gold's result."""
    c = noact['check']
    if c is None or c['exit'] != 0 or c['match'] is False:
        return True
    if c['match'] is None:
        return _norm(c['out']) != _norm(gold['check']['out'])
    return False


class DefaultWorld(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()
        with open(SPEC, encoding='utf-8') as f:
            cls.spec = {k: v for k, v in json.load(f).items() if not k.startswith('_')}

    def test_0_every_instrument_has_a_workflow(self):
        ids = [e['id'] for e in self.cfg['instruments']]
        self.assertEqual(len(ids), 203)
        self.assertEqual(sorted(set(ids) - set(self.spec)), [], 'instruments without a default-lab workflow')
        self.assertEqual(sorted(set(self.spec) - set(ids)), [])

    def test_1_load_time_and_ready_time(self):
        t = tmpdir()
        try:
            t0 = time.time()
            b = LabBackend(self.cfg, run_dir=os.path.join(t, 'b'))
            load_s = time.time() - t0
            self.assertEqual(b.t_setup, 1800.0)
            bad = {i: [r for r in inst.setup_report if r.get('status') not in ('succeeded', 'accepted', 'ok')]
                   for i, inst in b.instruments.items()}
            self.assertEqual({k: v for k, v in bad.items() if v}, {}, 'set-up steps of the default lab failed')
            self.assertLess(load_s, 60.0)
        finally:
            shutil.rmtree(t, ignore_errors=True)

    def test_2_gold_and_noact(self):
        fails = {}
        for slug, sp in sorted(self.spec.items()):
            gold = run_spec(self.cfg, slug, sp)
            if not gold['ok']:
                last = gold['rows'][-1] if gold['rows'] and gold['rows'][-1]['exit'] else {'cmd': sp['check'], 'err': gold['check']}
                fails[slug] = 'gold failed at %r: %s' % (last['cmd'], str(last['err'])[:200])
                continue
            if sp.get('noact_exempt'):                                  # stated in the spec (a load needs a host or the robot)
                continue
            noact = run_spec(self.cfg, slug, sp, noact=True)
            if not noact_differs(gold, noact):
                fails[slug] = 'doing nothing gives the same result'
        self.assertEqual(fails, {})

    def test_3_inventory(self):
        from chemlab import inventory as INV
        inv = INV.resolve(self.cfg)
        self.assertEqual(INV.check(inv), [])
        # the bench shelf's T1 rule (the robot hand overhangs both T1 neighbours): a 1 L bottle next to a handled bottle is reported
        moved = json.loads(json.dumps(inv))
        for r in moved['containers']:
            if r['location']['place'] == 'bench_shelf' and r['location']['position'] == 'T1-01':
                r['location']['position'] = 'T2-09'
            if r['id'] == 'water_ultrapure_1l':
                r['location']['position'] = 'T1-01'
        self.assertTrue(any('T1 neighbours' in x for x in INV.check(moved)))
        places = {p['id']: p for p in inv.get('places') or []}
        self.assertTrue(places)
        recs = inv.get('containers') or []
        self.assertGreater(len(recs), 80)
        self.assertGreater(len(inv.get('instrument_contents') or []), 100)
        # the content rules: substance ids in substances.json, physical amounts only, solutions with their solutes
        bad = json.loads(json.dumps(inv))
        for r in bad['containers']:
            if r['id'] == 'buffer_ph9_18':
                r['contents'] = [{'substance': 'sodium_tetraborate_decahydrate', 'amount': {'value': 0.5, 'unit': 'L'}}]
            if r['id'] == 'osmolality_cal_400':
                r['contents'][0]['amount'] = {'value': 12, 'unit': 'vial'}
            if r['id'] == 'hexane_2_5l':
                r['contents'][0]['substance'] = 'not_a_shared_id'
        msgs = INV.check(bad)
        self.assertTrue(any('booked as a volume' in m for m in msgs))
        self.assertTrue(any('not a physical amount' in m for m in msgs))
        self.assertTrue(any('not in substances.json' in m for m in msgs))
        # the reach rule: no "in reach" claim at a side-access-only place (the desiccator)
        reach = json.loads(json.dumps(inv))
        for r in reach['containers']:
            if r['id'] == 'kbr_ir_grade':
                r['source'] += ' (in reach)'
        self.assertTrue(any('side-access-only' in m for m in INV.check(reach)))
        # the vial rack's row B stays empty (the robot's fingers would hit a vial there)
        rowb = json.loads(json.dumps(inv))
        for r in rowb['containers']:
            if r['id'] == 'quantos_vial_1':
                r['location']['position'] = 'B01'
        self.assertTrue(any('cannot use' in m for m in INV.check(rowb)))
        used = {}
        for r in recs:
            for k in ('id', 'type', 'contents', 'location', 'storage_class'):
                self.assertIn(k, r, r.get('id'))
            loc = r['location']
            self.assertIn(loc['place'], places, r['id'])
            if places[loc['place']].get('slots'):
                key = (loc['place'], loc.get('position'))
                self.assertNotIn(key, used, 'two containers in one position: %s, %s' % (used.get(key), r['id']))
                used[key] = r['id']
        self.assertEqual(len({r['id'] for r in recs}), len(recs))
        t = tmpdir()
        try:
            lab = GenericLab(os.path.join(t, 'lab'), single(['rigaku_miniflex600_external_cooling'], self.cfg))
            try:
                r = lab.core.handle(['chemlab', 'list', '--inventory'], 'agent')
                self.assertEqual(r.exit_code, 0, r.err)
                self.assertIn('cylinder_rack', r.out)
                r = lab.core.handle(['chemlab', 'list', '--inventory', 'rigaku_miniflex600_external_cooling'], 'agent')
                self.assertEqual(r.exit_code, 0, r.err)
                self.assertIn('silicon', r.out.lower())
            finally:
                lab.stop()
        finally:
            shutil.rmtree(t, ignore_errors=True)

    def test_4_quantos_powder_task_variant(self):
        """The task-world variant quantos_powder (configs/all203_quantos_powder.json + inventory_task_quantos_powder.json, made by
        make_config from inventory.VARIANTS): the Quantos starts empty, the heads stand in their containers on the bench shelf and
        the empty vials in the bench vial rack (top-down places); a host / the robot inserts the KBr head and a vial (the runtime's
        head / container registration) and the agent doses 50 mg; the head loses what the vial gains."""
        from chemlab import inventory as INV
        from chemlab.labbackend import CONFIG_DIR
        vcfg = load_config(str(CONFIG_DIR / 'all203_quantos_powder.json'))
        inv = INV.resolve(vcfg)
        self.assertEqual(inv.get('variant'), 'quantos_powder')
        self.assertEqual(INV.check(inv), [])
        places = {p['id']: p for p in inv['places']}
        recs = {r['id']: r for r in inv['containers']}
        for rid in ('qh_kbr_1', 'qh_caco3_1', 'quantos_vial_1', 'quantos_vial_pan'):
            self.assertEqual(places[recs[rid]['location']['place']].get('access'), 'top_down', rid)
        for rid in ('qh_kbr_1', 'qh_caco3_1'):
            self.assertTrue(recs[rid].get('stored_in'), rid)
        for rid in ('quantos_vial_1', 'quantos_vial_2', 'quantos_vial_3', 'quantos_vial_pan'):
            self.assertEqual(recs[rid].get('cap'), 'off', rid)            # prepared open for dosing
        default = {r['id']: r for r in INV.resolve(self.cfg)['containers']}
        self.assertTrue(all(default[rid].get('cap', 'on') == 'on' for rid in ('quantos_vial_1', 'quantos_vial_pan')))
        bad = json.loads(json.dumps(inv))
        for r in bad['containers']:
            if r['id'] == 'quantos_vial_1':
                r['cap'] = 'half'
            if r['id'] == 'qh_kbr_1':
                r['cap'] = 'off'                                         # a dosing head type has no closure
        msgs = INV.check(bad)
        self.assertTrue(any('is not "on" or "off"' in m for m in msgs) and any('has no closure' in m for m in msgs))
        head, vial = recs['qh_kbr_1'], recs['quantos_vial_1']
        fill_kg = head['contents'][0]['amount']['value'] / 1000.0
        q = 'mt_quantos_q2_xpe206dr'
        from chemlab.compile.tasks.dosing_world import TRAVEL_M as TRAVEL      # the contract's front-door travel (0.5495 m)
        t = tmpdir()
        try:
            lab = GenericLab(os.path.join(t, 'lab'), single([q], vcfg))
            try:
                b, inst = lab.backend, lab.backend.instruments[q]
                self.assertIn('none inserted', lab.core.handle(['chemlab', 'run', q, 'head'], 'agent').out)
                self.assertEqual(b.invoke('agent', q, 'door.set_opening', {'opening_m': TRAVEL}, 't4-open').get('status'), 'succeeded')
                inst.host.flush()
                inst.host.host.host_load_head(iid=q, head_id=head['head_id'], source_mass_kg=fill_kg, free_flowing=True,
                                              evidence='test: the robot inserted head %s' % head['id'])
                inst.host.host.host_load_container(iid=q, container_id=vial['id'], position_id='balance_pan', content_mass_kg=0.0,
                                                   evidence='test: the robot placed vial %s' % vial['id'])
                self.assertEqual(b.invoke('agent', q, 'door.set_opening', {'opening_m': 0.0}, 't4-close').get('status'), 'succeeded')
                for c in ('tare', 'dose 50mg --tolerance 1%'):
                    r = lab.core.handle(['chemlab', 'run', q] + shlex.split(c), 'agent')
                    self.assertEqual(r.exit_code, 0, r.err)
                lab.core.handle(['chemlab', 'wait', '200'], 'agent')
                out = lab.core.handle(['chemlab', 'run', q, 'result'], 'agent').out
                self.assertIn('succeeded', out)
                truth = {k: (v.get('value') if isinstance(v, dict) else v) for k, v in b.evaluator_read(q)['observations'].items()}
                dosed = truth['truth.container_mass_kg']
                self.assertAlmostEqual(dosed, 5e-05, delta=5e-07)
                self.assertAlmostEqual(truth['truth.source_mass_kg'], fill_kg - dosed, delta=1e-12)
            finally:
                lab.stop()
        finally:
            shutil.rmtree(t, ignore_errors=True)

if __name__ == '__main__':
    unittest.main()
