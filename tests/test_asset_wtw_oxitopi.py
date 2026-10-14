"""tests for CHEM-171 wtw_oxitopi (WTW OxiTop-i IS 6 respirometric BOD system): python3 -m unittest tests/test_asset_wtw_oxitopi.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation and foreign-agent ownership; idempotent
request ids; two instances isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its
schema and every success changes only declared slots; WTW BOD equation and the manual's factor table; Benson-Krause oxygen solubility; oxygen
conservation in the closed bottle; glucose / glutamic acid check (WTW 260 +- 30 mg/l stirred, DIN 210 +- 40 mg/l with the literature rates, blank
<= 2 mg/l); missing NaOH, missing stirring, untight head, NaOH in the sample; nitrification and ATU; AutoTemp (cold sample, test interval by
duration, 3 h latest start); daily storage, end of measurement, green LED for 24 h; units and digits; range exceeded; OxiTop PM tablet against the lot
value; OxiTop PT against the altitude table; battery drain, low-battery red LED, battery replacement; LED flash pattern; display switch-off; key
menu; incubator door and failure; sample preparation (dilution, seeding, pH adjustment, buffer chemistry); seeded-dilution evaluation; library
substances (degradable vs refractory) and evaluator-defined substances; inhibitors and chlorine."""
import copy
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import wtw_oxitopi as rt  # noqa: E402

INST = 'oxitop_i_is6_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
DAY = 86400.0
DROPS = {22.7: 1, 43.5: 1, 97.0: 2, 164.0: 3, 250.0: 5, 365.0: 7, 432.0: 9}


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return x != y


class Rig:
    def __init__(self, seed=1, incubator=293.15, room=295.15, pressure=101325.0, instance=INST):
        self.h = rt.WtwOxitopiRuntime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A = self.h.session('agent')
        self.E = self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', incubator_k=incubator, room_k=room, ambient_pressure_pa=pressure, noise_seed=seed)

    def call(self, sess, cap, expect='succeeded', **args):
        """Instrumented call: outputs validated against the declared schema; a success changes only declared immediate slots and never the clock;
        a rejection leaves every slot unchanged and returns a declared error code."""
        self.n += 1
        before, t0 = self.h.read('evaluator'), self.h.t
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'r{self.n}')
        after = self.h.read('evaluator')
        changed = {k for k in after if _differ(after[k], before[k])} - {'clock.time_s'}
        if expect == 'succeeded':
            assert r['status'] == 'succeeded', (cap, r['error'])
            rt.validate(rt.CAPS[cap]['output_schema'], r['result'])
            if cap != 'sim.step':
                extra = changed - set(rt.EFFECTS.get(cap, []))
                assert not extra, (cap, sorted(extra))
                assert self.h.t == t0, cap
            SEEN_CAPS.add(cap)
            PROCESS_EVIDENCE.setdefault(cap, []).append(dict(arguments=json.dumps(args, sort_keys=True, default=str)[:200], time_s=t0, status=r['status']))
            return r['result']
        assert r['status'] in ('rejected', 'unsupported'), (cap, r['status'], r.get('result'))
        assert r['error']['code'] == expect, (cap, r['error']['code'], expect)
        assert not changed, (cap, 'rejected call changed', sorted(changed))
        assert expect in [e['code'] for e in rt.export_wtw_oxitopi_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def kits(self, *names, volume=3e-3):
        for k in names:
            self.call(self.E, 'kit.provide', kit=k, volume_m3=volume)

    def free(self, pos, head=None):
        """Take the stored head off bottle `pos` (and `head` off whatever bottle it sits on) before working on the bottle."""
        on = self.h.bottles[pos]['head']
        if on is not None:
            self.call(self.A, 'head.unscrew', head=on)
        if head is not None and self.h.heads[head]['position'] is not None:
            self.call(self.A, 'head.unscrew', head=head)

    def load(self, pos, head, sample, ml, pellets=2, atu=True, bar=True, days=5, unit='mg/l', start=True, tight=True):
        A = self.A
        self.free(pos, head)
        self.call(A, 'bottle.fill', position=pos, sample_id=sample, volume_m3=ml * 1e-6)
        if bar:
            self.call(A, 'bottle.add_stir_bar', position=pos)
        if atu:
            self.call(A, 'bottle.add_reagent', position=pos, reagent='nth_600', drops=DROPS[ml])
        self.call(A, 'bottle.insert_sleeve', position=pos, naoh_pellets=pellets)
        self.call(A, 'head.screw_on', head=head, position=pos, tight=tight)
        self.call(A, 'head.configure', head=head, duration_days=days, unit=unit, sample_volume_m3=ml * 1e-6)
        if start:
            self.call(A, 'head.start', head=head)

    def gga(self, seed_fraction=0.02):
        """Respirometric check (BOD primer p.32-33, Standard Methods 5210 D): R10 diluted 1:100 in seeded dilution water (20 ml settled wastewater
        per litre), 164 ml; blank = seeded dilution water, 432 ml."""
        self.kits('gga_stock_r10', 'nth_600', 'nhp_600', 'dilution_water', 'settled_sewage_seed')
        self.call(self.A, 'sample.prepare', sample_id='std', volume_m3=1e-3, sources=[dict(source_id='gga_stock_r10', volume_m3=1e-5)],
                  seed_volume_fraction=seed_fraction, diluent='dilution_water', aerate=True, temperature_k=293.15)
        self.call(self.A, 'sample.prepare', sample_id='blank', volume_m3=1e-3, sources=[], seed_volume_fraction=seed_fraction, diluent='dilution_water',
                  aerate=True, temperature_k=293.15)

    def stored(self, head, unit_value=True):
        v = self.read('heads.stored_values')[head]
        return [x['value'] for x in v] if unit_value else v

    def truth(self, pos):
        return self.read('truth.bottles')[pos]


def _raised_codes():
    """Error codes each command can raise: its own ActionRejected calls plus those of the helpers it calls (static scan of the module source)."""
    src = Path(rt.__file__).read_text()
    funcs = {}
    for m in re.finditer(r'\n    def (\w+)\(self[^\n]*\n(.*?)(?=\n    def |\n# ----|\nclass )', src, re.S):
        funcs[m.group(1)] = m.group(2)
    own = {k: set(re.findall(r"ActionRejected\('([A-Z_]+)'\)", v)) for k, v in funcs.items()}
    calls = {k: set(re.findall(r'self\.(_\w+)\(', v)) & set(funcs) for k, v in funcs.items()}
    out = {}
    for k in funcs:
        if not k.startswith('_cmd_'):
            continue
        seen, todo, codes = set(), [k], set()
        while todo:
            f = todo.pop()
            if f in seen:
                continue
            seen.add(f)
            codes |= own.get(f, set())
            todo += [g for g in calls.get(f, ()) if g not in ('_cmd_sim_step',)]
        out[k[5:].replace('_', '.', 1)] = codes
    return out


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_and_mirrors_consistent(self):
        meta = rt.export_wtw_oxitopi_contract_metadata()
        slots = set(meta['state_slots'])
        self.assertEqual(slots, set(rt.SLOTS))
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            for x in c['observes']:
                self.assertIn(x, slots, k)
            for pre in c['preconditions']:
                self.assertTrue(set(pre['state_ids']) <= slots, k)
            self.assertTrue({'INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'} <= {e['code'] for e in c['errors']}, k)
            self.assertTrue(c['function_zh'])
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for k in rt.SCENE_MIRRORED:
            self.assertEqual(meta['state_slots'][k]['visibility'], 'agent', k)
        self.assertEqual(len([k for k in slots if k.startswith('led.')]), 18)
        mech = meta['mechanisms']['respirometric_bod_process']
        for name, p in mech['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_wtw_oxitopi_contract_metadata()
        for cap, codes in _raised_codes().items():
            if cap not in meta['capabilities']:
                cap = next(k for k in meta['capabilities'] if k.replace('.', '_') == cap.replace('.', '_'))
            declared = {e['code'] for e in meta['capabilities'][cap]['errors']}
            self.assertTrue(codes <= declared, (cap, sorted(codes - declared)))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            r = g.A.invoke(instance_id=INST, capability_id=k, arguments={}, request_id='u-' + k)
            self.assertEqual(r['status'], 'unsupported')
            self.assertEqual(r['error']['code'], 'UNSUPPORTED')

    def test_agent_truth_isolation(self):
        g = Rig()
        d = g.A.describe(instance_id=INST)
        self.assertFalse(any(k.startswith('truth.') for k in d['state_slots']))
        self.assertFalse(any(c['visibility'] != 'agent' for c in d['capabilities'].values()))
        for c in d['capabilities'].values():
            self.assertFalse(any(e['state_id'].startswith('truth.') for e in c['state_effects']))
        with self.assertRaises(rt.ActionRejected):
            g.A.read(instance_id=INST, state_ids=['truth.bottles'])
        r = g.A.invoke(instance_id=INST, capability_id='bottle.inspect', arguments=dict(position='P1'), request_id='x1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = g.A.invoke(instance_id=INST, capability_id='sim.step', arguments=dict(dt_s=10.0), request_id='x2')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.assertIn('truth.bottles', g.E.describe(instance_id=INST)['state_slots'])
        g.call(g.E, 'fault.inject', fault='incubator_failure', active=True)
        ev = g.call(g.A, 'events.read', since_s=0.0)['events']
        self.assertFalse(any(e['kind'] == 'fault' for e in ev))

    def test_foreign_agent_ownership(self):
        g = Rig()
        g.h.register_principal('agent2', 'agent')
        B = g.h.session('agent2')
        g.gga()
        g.load('P1', 'h1', 'std', 164.0)
        for cap, args in (('bottle.empty', dict(position='P1')), ('head.stop', dict(head='h1')), ('head.read', dict(head='h1')),
                          ('head.unscrew', dict(head='h1')), ('bottle.add_stir_bar', dict(position='P1'))):
            r = B.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='b-' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED', cap)
        r = B.invoke(instance_id=INST, capability_id='bottle.fill', arguments=dict(position='P2', sample_id='std', volume_m3=1.64e-4), request_id='b-fill')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('std', B.read(instance_id=INST, state_ids=['samples.available'])['observations']['samples.available']['value'])
        # agent2 works with its own objects; agent 1 releases the head after stopping and unscrewing
        B.invoke(instance_id=INST, capability_id='head.configure', arguments=dict(head='h4', duration_days=3), request_id='b-own')
        g.call(g.A, 'head.stop', head='h1')
        g.call(g.A, 'head.unscrew', head='h1')
        r = B.invoke(instance_id=INST, capability_id='head.read', arguments=dict(head='h1'), request_id='b-free')
        self.assertEqual(r['status'], 'succeeded')
        g.call(g.A, 'bottle.empty', position='P1')
        r = B.invoke(instance_id=INST, capability_id='bottle.add_stir_bar', arguments=dict(position='P1'), request_id='b-free2')
        self.assertEqual(r['status'], 'succeeded')

    def test_idempotent_request_ids(self):
        g = Rig()
        g.kits('distilled_water')
        g.free('P1')
        args = dict(position='P1', sample_id='distilled_water', volume_m3=1.64e-4)
        r1 = g.A.invoke(instance_id=INST, capability_id='bottle.fill', arguments=args, request_id='same')
        snap = g.h.read('evaluator')
        r2 = g.A.invoke(instance_id=INST, capability_id='bottle.fill', arguments=args, request_id='same')
        self.assertEqual(r1, r2)
        self.assertEqual(snap, g.h.read('evaluator'))
        r3 = g.A.invoke(instance_id=INST, capability_id='bottle.fill', arguments=dict(args, position='P2'), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')
        r5 = g.A.invoke(instance_id=INST, capability_id='bottle.fill', arguments=dict(args, position='P2'), request_id='p2')
        self.assertEqual(r5['error']['code'], 'HEAD_ON_BOTTLE')               # stored heads sit on the bottles
        r4 = g.A.invoke(instance_id=INST, capability_id='bottle.fill', arguments=args, request_id='other')
        self.assertEqual(r4['error']['code'], 'BOTTLE_NOT_EMPTY')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='oxitop_a'), Rig(instance='oxitop_b', incubator=298.15)
        a.call(a.A, 'platform.set_power', on=True)
        self.assertFalse(b.read('platform.powered'))
        with self.assertRaises(rt.ActionRejected):
            a.A.read(instance_id='oxitop_b')
        r = a.A.invoke(instance_id='oxitop_b', capability_id='platform.set_power', arguments=dict(on=True), request_id='z')
        self.assertEqual(r['error']['code'], 'UNKNOWN_INSTANCE')
        a.step(3600.0)
        b.step(3600.0)
        self.assertNotAlmostEqual(a.read('truth.incubator_air_k'), b.read('truth.incubator_air_k'), places=1)

    def test_step_partition_independence(self):
        def run(steps):
            g = Rig(seed=5)
            g.gga()
            g.load('P1', 'h1', 'std', 164.0)
            g.call(g.A, 'platform.set_power', on=True)
            for s in steps:
                g.step(s)
            return g.read('heads.stored_values'), g.read('truth.bottles')['P1'], g.read('display.h1_value')
        total = 2.3 * DAY
        a = run([total])
        b = run([7.0, 53.0, 1000.0, 3599.5, 0.5] + [total - 4660.0])
        c = run([total / 37.0] * 37)
        self.assertEqual(a, b)
        self.assertEqual(a[0], c[0])
        self.assertAlmostEqual(a[1]['pressure_pa'], c[1]['pressure_pa'], places=6)

    def test_rejections_change_nothing(self):
        g = Rig()
        r = rt.WtwOxitopiRuntime('fresh')
        r.register_principal('e', 'evaluator')
        out = r.session('e').invoke(instance_id='fresh', capability_id='platform.set_power', arguments=dict(on=True), request_id='p')
        self.assertEqual(out['error']['code'], 'PLANT_REQUIRED')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', incubator_k=293.15, room_k=295.15, ambient_pressure_pa=101325.0, noise_seed=2)
        g.kits('distilled_water', 'nhp_600')
        g.call(g.A, 'bottle.fill', expect='HEAD_ON_BOTTLE', position='P1', sample_id='distilled_water', volume_m3=1.64e-4)
        g.free('P1')
        g.call(g.A, 'bottle.fill', position='P1', sample_id='distilled_water', volume_m3=1.64e-4)
        g.call(g.A, 'bottle.fill', expect='BOTTLE_NOT_EMPTY', position='P1', sample_id='distilled_water', volume_m3=1.64e-4)
        g.call(g.A, 'bottle.add_stir_bar', position='P1')
        g.call(g.A, 'bottle.add_stir_bar', expect='ALREADY_PRESENT', position='P1')
        g.call(g.A, 'bottle.add_reagent', expect='UNKNOWN_SAMPLE', position='P1', reagent='nth_600', drops=3)
        g.call(g.A, 'bottle.insert_sleeve', position='P1', naoh_pellets=2)
        g.call(g.A, 'bottle.add_reagent', expect='SLEEVE_IN_NECK', position='P1', reagent='nth_600', drops=3)
        g.call(g.A, 'head.screw_on', head='h1', position='P1')
        g.call(g.A, 'bottle.fill', expect='HEAD_ON_BOTTLE', position='P1', sample_id='distilled_water', volume_m3=1.64e-4)
        g.call(g.A, 'head.screw_on', expect='HEAD_ON_BOTTLE', head='h2', position='P1')
        g.free('P2')
        g.call(g.A, 'head.screw_on', expect='POSITION_OCCUPIED', head='h2', position='P1')
        g.call(g.A, 'head.screw_on', expect='HEAD_ON_BOTTLE', head='h1', position='P2')
        g.call(g.A, 'head.pt_test', expect='HEAD_ON_BOTTLE', head='h1')
        g.call(g.A, 'head.start', head='h1')
        g.call(g.A, 'head.configure', expect='MEASUREMENT_RUNNING', head='h1', duration_days=3)
        g.call(g.A, 'head.start', expect='MEASUREMENT_RUNNING', head='h1')
        g.call(g.A, 'head.unscrew', expect='HEAD_NOT_ON_BOTTLE', head='h2')
        g.call(g.A, 'head.screw_on', head='h2', position='P2')
        g.call(g.A, 'bottle.empty', expect='HEAD_ON_BOTTLE', position='P1')
        g.call(g.A, 'bottle.empty', expect='HEAD_ON_BOTTLE', position='P3')
        g.free('P3')
        g.call(g.A, 'bottle.empty', expect='BOTTLE_EMPTY', position='P3')
        g.call(g.A, 'sample.prepare', expect='INSUFFICIENT_SAMPLE', sample_id='big', volume_m3=0.004,
               sources=[dict(source_id='distilled_water', volume_m3=0.004)])
        g.call(g.A, 'sample.prepare', expect='VOLUME_OUT_OF_RANGE', sample_id='v', volume_m3=1e-3, sources=[dict(source_id='distilled_water', volume_m3=5e-4)])
        g.call(g.A, 'sample.prepare', expect='NO_SEED', sample_id='v', volume_m3=1e-3, sources=[dict(source_id='distilled_water', volume_m3=9e-4)],
               seed_volume_fraction=0.02, diluent='distilled_water')
        g.call(g.A, 'sample.prepare', expect='UNKNOWN_SAMPLE', sample_id='v', volume_m3=1e-3, sources=[dict(source_id='nope', volume_m3=1e-4)],
               diluent='distilled_water')
        g.call(g.A, 'head.stop', expect='HEAD_OFF', head='h5')
        g.call(g.E, 'stock.provide', expect='UNKNOWN_SUBSTANCE', stock_id='q', volume_m3=1e-3, composition=dict(substances=[dict(substance_id='unobtainium',
                                                                                                                                   concentration_kg_m3=0.1)], ph=7.0))
        g.call(g.E, 'kit.provide', expect='INVALID_ARGUMENT', kit='oxitop_pm')
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='head_untight', active=True)
        g.call(g.A, 'eval.bod', expect='INVALID_ARGUMENT', sample_value_kg_m3=0.1, blank_value_kg_m3=0.0, sample_volume_m3=2e-3, total_volume_m3=1e-3)


class Physics(unittest.TestCase):
    def test_bod_equation_matches_manual_factor_table(self):
        f432 = rt.bod_factor(432.0)
        for v, fac in rt.DOC['factors'].items():
            self.assertAlmostEqual(rt.bod_factor(float(v)) / f432, fac, delta=0.012 * fac)
        self.assertAlmostEqual(rt.DIGIT_HPA, 3.5616, places=3)
        # manual volume table: BOD measuring range = 40 digits x factor; the digit display ends at 50 digits (primer: > 50 digit exceeds the range)
        for v, rng in rt.DOC['ranges_mg_l'].items():
            self.assertEqual(rng, 40 * rt.DOC['factors'][v])
            self.assertAlmostEqual(40 * rt.DIGIT_HPA * rt.bod_factor(float(v)), rng, delta=0.012 * rng)

    def test_benson_krause_reference_values(self):
        # USGS DOTABLES / Benson-Krause at 760 mmHg: 9.09 (20 C), 11.29 (10 C), 7.56 (30 C) mg/l
        self.assertAlmostEqual(rt.do_saturation_1atm(293.15), 9.09, delta=0.01)
        self.assertAlmostEqual(rt.do_saturation_1atm(283.15), 11.29, delta=0.01)
        self.assertAlmostEqual(rt.do_saturation_1atm(303.15), 7.56, delta=0.01)
        self.assertAlmostEqual(rt.p_vapour(293.15), 2339.0, delta=4.0)

    def test_buffer_chemistry_round_trip(self):
        for ph, dic, pt in ((7.2, 0.5, 3.75), (6.0, 2.0, 0.0), (8.4, 4.0, 0.1), (4.0, 0.0, 0.0), (11.0, 0.1, 0.0)):
            alk = rt.alk_of(ph, dic, pt)
            self.assertAlmostEqual(rt.ph_of(alk, dic, pt), ph, places=6)
        self.assertAlmostEqual(rt.dic_of(7.38, 2.0), 2.2, places=3)

    def test_oxygen_conservation_closed_bottle(self):
        g = Rig(seed=3)
        g.gga()
        g.load('P1', 'h1', 'std', 164.0)
        g.call(g.A, 'platform.set_power', on=True)
        b = g.h.bottles['P1']
        L, G = b['liquid'], b['gas']
        o2_0 = G['o2'] * rt.M_O2 * 1000.0 + L['DO'] * L['V'] * 1000.0          # mg
        g.step(3 * DAY)
        o2_1 = G['o2'] * rt.M_O2 * 1000.0 + L['DO'] * L['V'] * 1000.0 + L['o2_used'] * L['V'] * 1000.0
        self.assertAlmostEqual(o2_0, o2_1, delta=1e-6 * o2_0)

    def test_gga_check_in_wtw_range_and_blank_below_2(self):
        g = Rig(seed=11)
        g.gga()
        g.load('P1', 'h1', 'std', 164.0)
        g.load('P2', 'h2', 'blank', 432.0)
        g.call(g.A, 'platform.set_power', on=True)
        g.step(5 * DAY + 4 * 3600)
        A, B = g.stored('h1')[-1], g.stored('h2')[-1]
        self.assertEqual(len(g.stored('h1')), 5)
        self.assertLessEqual(B, 2.0)
        r = g.call(g.A, 'eval.bod', sample_value_kg_m3=A / 1000, blank_value_kg_m3=B / 1000, sample_volume_m3=0.99e-3, total_volume_m3=1e-3)
        self.assertTrue(0.230 <= A / 1000 - 0.99 * B / 1000 <= 0.290, (A, B))
        self.assertAlmostEqual(r['bod_kg_m3'], (A / 1000 - B / 1000 * 0.01) / 0.99, places=9)
        true = g.truth('P1')['o2_consumed_kg_m3'] * 1000
        self.assertLess(abs(A - true) / true, 0.04)               # head vs true consumption (AutoTemp phase, sensor errors)

    def test_literature_rates_reproduce_din_dilution_value(self):
        old = rt.STIRRED_RATE_FACTOR
        rt.STIRRED_RATE_FACTOR = 1.0
        try:
            g = Rig(seed=11)
            g.gga()
            g.load('P1', 'h1', 'std', 164.0)
            g.call(g.A, 'platform.set_power', on=True)
            g.step(5 * DAY + 4 * 3600)
            self.assertTrue(170.0 <= g.stored('h1')[-1] <= 250.0, g.stored('h1'))
        finally:
            rt.STIRRED_RATE_FACTOR = old

    def test_missing_naoh_and_missing_stirring_read_low(self):
        def run(pellets=2, power=True):
            g = Rig(seed=4)
            g.gga()
            g.load('P1', 'h1', 'std', 164.0, pellets=pellets)
            if power:
                g.call(g.A, 'platform.set_power', on=True)
            g.step(5 * DAY + 4 * 3600)
            return g.stored('h1')[-1], g.truth('P1')
        ref, t_ref = run()
        no_naoh, t_no = run(pellets=0)
        still, t_still = run(power=False)
        self.assertLess(no_naoh, 0.6 * ref)
        self.assertGreater(t_no['p_co2_pa'], 1000.0)
        self.assertLess(t_ref['p_co2_pa'], 50.0)
        self.assertLess(still, 0.6 * ref)
        self.assertLess(t_still['do_kg_m3'], 0.5 * t_ref['do_kg_m3'])
        self.assertGreater(t_ref['remaining_bod_kg_m3'], -1e-12)

    def test_nitrification_and_atu(self):
        def run(atu):
            g = Rig(seed=8)
            g.kits('domestic_effluent', 'nth_600', 'nhp_600')
            g.call(g.A, 'sample.prepare', sample_id='eff', volume_m3=1e-3, sources=[dict(source_id='domestic_effluent', volume_m3=1e-3)], aerate=True,
                   temperature_k=293.15)
            g.load('P1', 'h1', 'eff', 432.0, atu=atu)
            g.call(g.A, 'platform.set_power', on=True)
            g.step(5 * DAY + 4 * 3600)
            return g.stored('h1')[-1], g.truth('P1')
        with_atu, t1 = run(True)
        without, t2 = run(False)
        self.assertLess(t1['nitrified_n_kg_m3'], 2e-5)
        self.assertGreater(t2['nitrified_n_kg_m3'], 1e-3)
        self.assertGreater(without - with_atu, 0.7 * 4.57 * t2['nitrified_n_kg_m3'] * 1000)

    def test_autotemp_cold_sample_and_latest_start(self):
        starts = {}
        for T in (293.15, 288.15, 285.15):
            g = Rig(seed=2)
            g.gga()
            g.call(g.A, 'sample.prepare', sample_id='cold', volume_m3=5e-4, sources=[dict(source_id='std', volume_m3=5e-4)], temperature_k=T)
            g.load('P1', 'h1', 'cold', 164.0)
            g.call(g.A, 'platform.set_power', on=True)
            g.step(4 * 3600)
            ev = [e for e in g.call(g.A, 'events.read', since_s=0.0)['events'] if e['code'] == 'MEASUREMENT_START']
            starts[T] = ev[0]['time_s']
            if T < 288.15:
                self.assertIn('FILLING_TEMPERATURE_P1', g.read('warnings.active'))
        self.assertEqual(starts[293.15], 3600.0 + 1800.0)
        self.assertGreater(starts[288.15], starts[293.15])
        self.assertLessEqual(max(starts.values()), 3 * 3600.0 + 60.0)

    def test_autotemp_interval_scales_with_duration(self):
        g = Rig(seed=2)
        g.gga()
        g.load('P1', 'h1', 'std', 164.0, days=1)
        g.call(g.A, 'platform.set_power', on=True)
        g.step(2 * 3600)
        ev = [e for e in g.call(g.A, 'events.read', since_s=0.0)['events'] if e['code'] == 'MEASUREMENT_START']
        self.assertEqual(ev[0]['time_s'], 3600.0 + 360.0)

    def test_daily_storage_finish_green_led_and_display(self):
        g = Rig(seed=6)
        g.gga()
        g.load('P1', 'h1', 'std', 164.0, days=2)
        g.call(g.A, 'platform.set_power', on=True)
        g.step(3600 + 1800 + DAY + 60)
        self.assertEqual(len(g.stored('h1')), 1)
        self.assertEqual(g.read('heads.status')['h1']['state'], 'measuring')
        g.step(DAY)
        st = g.read('heads.status')['h1']
        self.assertEqual(st['state'], 'finished')
        self.assertEqual(st['led'], 'green')
        r = g.call(g.A, 'head.read', head='h1')
        self.assertEqual(r['label'], 'BOD2')
        self.assertAlmostEqual(r['value'], g.stored('h1')[-1])
        g.step(DAY + 120)
        self.assertEqual(g.read('heads.status')['h1']['led'], 'off')
        self.assertEqual(len(g.stored('h1')), 2)

    def test_units_and_digits_consistent(self):
        g = Rig(seed=6)
        g.gga()
        g.load('P1', 'h1', 'std', 164.0, days=2)
        g.call(g.A, 'platform.set_power', on=True)
        g.step(2 * DAY + 3 * 3600)
        mg = g.stored('h1')[-1]
        raw = g.stored('h1', unit_value=False)[-1]
        vals = {}
        for u in ('digit', 'dhPa', 'hPa', 'mg/l'):
            g.call(g.A, 'head.configure', head='h1', unit=u)
            vals[u] = g.stored('h1')[-1]
        self.assertEqual(vals['mg/l'], mg)
        self.assertAlmostEqual(vals['dhPa'], round(raw['delta_p_pa'] / 100.0), delta=1.0)
        self.assertAlmostEqual(vals['digit'], round(vals['dhPa'] / rt.DIGIT_HPA), delta=1.0)
        self.assertAlmostEqual(mg, vals['digit'] * 10.0, delta=12.0)        # factor 10 at 164 ml (manual table)
        self.assertTrue(900 < vals['hPa'] < 1013)

    def test_range_exceeded_still_displayed(self):
        g = Rig(seed=9)
        g.gga()
        g.load('P1', 'h1', 'std', 432.0)                       # range 40 mg/l at 432 ml, GGA ~260 mg/l
        g.call(g.A, 'platform.set_power', on=True)
        g.step(3 * DAY)
        st = g.stored('h1', unit_value=False)
        self.assertTrue(any(x['over_range'] for x in st))
        self.assertTrue(all(x['value'] is not None for x in st))
        self.assertIn('RANGE_EXCEEDED_h1', g.read('warnings.active'))
        tb = g.truth('P1')
        self.assertLess(tb['do_kg_m3'], 0.002)                  # oxygen nearly exhausted in the small head space

    def test_pm_tablet_matches_lot_value(self):
        """BOD primer p.34-35: heads started and thermostatted 4-4.5 h off the bottle, then tablet, rubber sleeve without NaOH, head screwed on."""
        g = Rig(seed=12)
        g.kits('distilled_water')
        g.call(g.E, 'kit.provide', kit='oxitop_pm', lot_test_value_kg_m3=0.21)
        g.free('P1')
        g.call(g.A, 'bottle.fill', position='P1', sample_id='distilled_water', volume_m3=1.64e-4)
        g.call(g.A, 'bottle.add_stir_bar', position='P1')
        g.call(g.A, 'platform.set_power', on=True)
        g.call(g.A, 'head.configure', head='h1', duration_days=5, unit='mg/l', sample_volume_m3=1.64e-4)
        g.call(g.A, 'head.start', head='h1')
        g.step(4.25 * 3600)
        self.assertEqual(g.read('heads.status')['h1']['state'], 'measuring')
        g.call(g.A, 'bottle.add_reagent', position='P1', reagent='oxitop_pm')
        g.call(g.A, 'bottle.insert_sleeve', position='P1', naoh_pellets=0)
        g.call(g.A, 'head.screw_on', head='h1', position='P1')
        g.step(5 * DAY)
        v = g.stored('h1')[-1]
        tol = 210.0 * 0.005 + 1.0 * rt.bod_factor(164.0) + 0.03 * 210.0
        self.assertLess(abs(v - 210.0), tol, g.stored('h1'))

    def test_pt_test_altitude_table(self):
        for p, ctrl in ((101325.0, 39), (74700.0, 29), (89800.0, 35)):
            g = Rig(seed=3, pressure=p)
            g.call(g.A, 'head.pt_test', expect='HEAD_ON_BOTTLE', head='h2')
            g.free('P2')
            r = g.call(g.A, 'head.pt_test', head='h2')
            self.assertEqual(r['control_digits'], ctrl)
            self.assertTrue(r['passed'], r)
            self.assertLessEqual(abs(r['measured_digits'] - ctrl), 3.0)

    def test_untight_head_and_pellets_in_sample(self):
        def run(fault=None, tight=True):
            g = Rig(seed=10)
            g.gga()
            if fault:
                g.call(g.E, 'fault.inject', fault=fault, active=True, target='P1')
            g.load('P1', 'h1', 'std', 164.0, tight=tight)
            g.call(g.A, 'platform.set_power', on=True)
            g.step(5 * DAY + 4 * 3600)
            return g.stored('h1')[-1], g.truth('P1'), g
        ref, _, _ = run()
        leak, _, _ = run(fault='head_untight')
        loose, _, _ = run(tight=False)
        naoh, t, g = run(fault='pellets_in_sample')
        self.assertLess(leak, 0.85 * ref)
        self.assertLess(loose, 0.85 * ref)
        self.assertLess(naoh, 0.1 * ref)
        self.assertTrue(g.call(g.E, 'bottle.inspect', position='P1')['state']['naoh_in_sample'])
        self.assertGreater(g.call(g.E, 'bottle.inspect', position='P1')['state']['ph'], 11.0)

    def test_door_and_incubator_failure(self):
        g = Rig(seed=10, room=298.15)
        g.gga()
        g.load('P1', 'h1', 'std', 164.0)
        g.call(g.A, 'platform.set_power', on=True)
        g.step(DAY)
        p0 = g.truth('P1')['pressure_pa']
        g.call(g.A, 'incubator.set_door', open=True)
        g.step(600)
        self.assertGreater(g.read('truth.incubator_air_k'), 296.0)
        self.assertGreater(g.truth('P1')['pressure_pa'], p0 - 50.0)          # warming opposes the BOD pressure drop
        g.call(g.A, 'incubator.set_door', open=False)
        g.step(4 * 3600)
        self.assertLess(abs(g.read('truth.incubator_air_k') - 293.15), 0.5)
        g.call(g.E, 'fault.inject', fault='incubator_failure', active=True)
        g.step(6 * 3600)
        self.assertAlmostEqual(g.read('truth.incubator_air_k'), 298.15, delta=0.05)
        self.assertGreater(g.truth('P1')['temperature_k'], 297.0)

    def test_battery_low_red_led_dead_and_replace(self):
        g = Rig(seed=10)
        g.gga()
        g.load('P1', 'h1', 'std', 164.0, days=7)
        g.call(g.A, 'platform.set_power', on=True)
        g.step(1.2 * DAY)
        g.call(g.E, 'fault.inject', fault='battery_drain', active=True, target='h1')
        n = len(g.stored('h1'))
        low_seen = False
        for _ in range(48 * 12):
            g.step(300)
            st = g.read('heads.status')['h1']
            if st['battery_low'] and st['state'] == 'measuring':
                low_seen = True
                self.assertEqual(st['led'], 'red')
            if st['state'] == 'dead':
                break
        self.assertTrue(low_seen)
        self.assertEqual(g.read('heads.status')['h1']['state'], 'dead')
        self.assertGreaterEqual(len(g.stored('h1')), n)
        g.call(g.A, 'head.press_key', expect='BATTERY_EMPTY', head='h1', key='M')
        g.call(g.A, 'head.replace_battery', head='h1')
        self.assertEqual(g.read('heads.status')['h1']['state'], 'off')
        self.assertFalse(g.read('heads.status')['h1']['battery_low'])
        self.assertGreaterEqual(len(g.stored('h1')), n)

    def test_led_flash_pattern_and_signal_led_off(self):
        g = Rig(seed=10)
        g.gga()
        g.load('P1', 'h1', 'std', 164.0)
        lit = []
        for _ in range(20):
            g.step(0.5)
            lit.append(g.read('led.h1_blue'))
        self.assertEqual(sum(lit), 2)                            # 0.5 s on every 5 s
        self.assertFalse(any(g.read('led.h1_red') for _ in range(1)))
        g.call(g.A, 'head.set_system', head='h1', signal_led=False)
        for _ in range(12):
            g.step(0.5)
            self.assertFalse(g.read('led.h1_blue'))

    def test_display_switch_off_and_wake(self):
        g = Rig(seed=10)
        g.call(g.A, 'head.set_system', head='h3', switch_off_s=60.0)
        self.assertEqual(g.read('heads.status')['h3']['state'], 'idle')
        g.step(61)
        self.assertEqual(g.read('heads.status')['h3']['state'], 'off')
        r = g.call(g.A, 'head.read', head='h3')
        self.assertTrue(r['display_on'])
        self.assertEqual(r['lines'][0], 'ID: 13')

    def test_key_menu_navigation(self):
        g = Rig(seed=10)
        g.gga()
        g.free('P1')
        g.call(g.A, 'bottle.fill', position='P1', sample_id='std', volume_m3=1.64e-4)
        g.call(g.A, 'bottle.insert_sleeve', position='P1', naoh_pellets=2)
        g.call(g.A, 'head.screw_on', head='h1', position='P1')
        r = g.call(g.A, 'head.press_key', head='h1', key='M')               # any key: display on
        self.assertEqual(r['screen'], 'id')
        r = g.call(g.A, 'head.press_key', head='h1', key='ENTER')           # measurement settings
        self.assertEqual(r['screen'], 'menu_meas')
        g.call(g.A, 'head.press_key', head='h1', key='NAV')                 # duration 5 -> 6
        g.call(g.A, 'head.press_key', head='h1', key='ENTER')               # next item: unit
        g.call(g.A, 'head.press_key', head='h1', key='ENTER')               # next item: sample volume
        for _ in range(4):                                                  # 432 -> 22.7 -> 43.5 -> 97 -> 164
            g.call(g.A, 'head.press_key', head='h1', key='NAV')
        st = g.read('heads.status')['h1']
        self.assertEqual(st['duration_days'], 6)
        self.assertAlmostEqual(st['sample_volume_m3'], 1.64e-4)
        g.call(g.A, 'head.press_key', head='h1', key='M')                   # back to the info page
        r = g.call(g.A, 'head.press_key', head='h1', key='M')               # start
        self.assertEqual(g.read('heads.status')['h1']['state'], 'adaption')
        self.assertIn('AutoTemp', r['lines'])
        r = g.call(g.A, 'head.press_key', head='h1', key='M', long=True)
        self.assertEqual(r['screen'], 'confirm_off')
        g.call(g.A, 'head.press_key', head='h1', key='ENTER')               # no
        self.assertEqual(g.read('heads.status')['h1']['state'], 'adaption')
        g.call(g.A, 'head.press_key', head='h1', key='M', long=True)
        g.call(g.A, 'head.press_key', head='h1', key='M')                   # yes
        self.assertEqual(g.read('heads.status')['h1']['state'], 'off')

    def test_stop_keeps_values_restart_clears_and_unscrew_vents(self):
        g = Rig(seed=10)
        g.gga()
        g.load('P1', 'h1', 'std', 164.0)
        g.call(g.A, 'platform.set_power', on=True)
        g.step(1.2 * DAY)
        g.call(g.A, 'head.unscrew', head='h1')
        self.assertAlmostEqual(g.truth('P1')['pressure_pa'], 101325.0, delta=1.0)
        g.step(600)
        r = g.call(g.A, 'head.read', head='h1')
        self.assertLess(abs(r['value'] or 0.0), 3.0 * rt.bod_factor(164.0))    # vented: no pressure difference left
        g.call(g.A, 'head.stop', head='h1')
        self.assertEqual(len(g.stored('h1')), 1)
        g.call(g.A, 'head.start', head='h1')
        self.assertEqual(len(g.stored('h1')), 0)

    def test_volume_setting_mismatch_warning(self):
        g = Rig(seed=10)
        g.gga()
        g.free('P1')
        g.call(g.A, 'bottle.fill', position='P1', sample_id='std', volume_m3=1.64e-4)
        g.call(g.A, 'bottle.insert_sleeve', position='P1', naoh_pellets=2)
        g.call(g.A, 'head.screw_on', head='h1', position='P1')
        g.call(g.A, 'head.start', head='h1')                                # default setting 432 ml
        self.assertIn('VOLUME_SETTING_h1', g.read('warnings.active'))


class ReviewRound1(unittest.TestCase):
    def test_head_read_and_stored_slot_agree_over_range(self):        # R1-07 / C1-10
        g = Rig(seed=9)
        g.gga()
        g.load('P1', 'h1', 'std', 432.0)
        g.call(g.A, 'platform.set_power', on=True)
        g.step(3 * DAY)
        slot = g.read('heads.stored_values')['h1']
        r = g.call(g.A, 'head.read', head='h1')['stored']
        self.assertTrue(any(x['over_range'] for x in slot))
        self.assertEqual([x['value'] for x in slot], [x['value'] for x in r])
        self.assertTrue(all(x['value'] is not None for x in r))

    def test_empty_battery_while_off_is_dead(self):                  # R1-04
        g = Rig(seed=9)
        g.call(g.E, 'fault.inject', fault='battery_drain', active=True, target='h3')
        g.step(4 * DAY)
        self.assertEqual(g.read('heads.status')['h3']['state'], 'dead')
        g.call(g.A, 'head.start', expect='BATTERY_EMPTY', head='h3')
        g.call(g.A, 'head.press_key', expect='BATTERY_EMPTY', head='h3', key='M')
        g.call(g.A, 'head.replace_battery', head='h3')
        g.call(g.E, 'fault.inject', fault='battery_drain', active=False, target='h3')
        g.call(g.A, 'head.start', head='h3')

    def test_filling_temperature_hidden_from_agent(self):           # R1-08
        g = Rig(seed=9)
        g.call(g.E, 'stock.provide', stock_id='cold', volume_m3=1e-3, composition=dict(substances=[], ph=7.0, temperature_k=284.65))
        g.free('P1')
        g.call(g.A, 'bottle.fill', position='P1', sample_id='cold', volume_m3=4.32e-4)
        agent_msgs = ' '.join(e['message_zh'] for e in g.call(g.A, 'events.read', since_s=0.0)['events'])
        self.assertIn('FILLING_TEMPERATURE_P1', g.read('warnings.active'))
        self.assertNotIn('11.5', agent_msgs)
        ev = g.E.invoke(instance_id=INST, capability_id='events.read', arguments=dict(since_s=0.0), request_id='ev-truth')['result']['events']
        self.assertTrue(any('11.5' in e['message_zh'] for e in ev))

    def test_pt_control_values_full_primer_table(self):              # R1-06
        table = rt.DOC['pt_test']
        self.assertEqual(len(table['control_digits']), 29)
        for alt in ('-200', '-100', '300', '700', '1100', '2300'):
            g = Rig(seed=3, pressure=table['mean_pressure_hpa'][alt] * 100.0)
            g.free('P2')
            r = g.call(g.A, 'head.pt_test', head='h2')
            self.assertEqual(r['control_digits'], table['control_digits'][alt], alt)
            self.assertTrue(r['passed'], (alt, r))

    def test_display_slot_text_matches_over_range_rule(self):      # C2-01 (round 2)
        self.assertIn('超过50 digit仍显示数值', rt.SLOTS['display.h1_value']['meaning'])
        g = Rig(seed=9)
        g.gga()
        g.load('P1', 'h1', 'std', 432.0)
        g.call(g.A, 'platform.set_power', on=True)
        g.step(3 * DAY)
        r = g.call(g.A, 'head.read', head='h1')
        self.assertTrue(r['stored'][-1]['over_range'])
        self.assertIsNotNone(g.read('display.h1_value'))

    def test_pm_tablet_first_day_course(self):                       # R2-01 (round 2): BOD primer p.34 figure
        g = Rig(seed=12)
        g.kits('distilled_water')
        g.call(g.E, 'kit.provide', kit='oxitop_pm', lot_test_value_kg_m3=0.30)
        g.free('P1')
        g.call(g.A, 'bottle.fill', position='P1', sample_id='distilled_water', volume_m3=1.64e-4)
        g.call(g.A, 'bottle.add_stir_bar', position='P1')
        g.call(g.A, 'platform.set_power', on=True)
        g.call(g.A, 'head.configure', head='h1', duration_days=5, unit='mg/l', sample_volume_m3=1.64e-4)
        g.call(g.A, 'head.start', head='h1')
        g.step(4.25 * 3600)
        g.call(g.A, 'bottle.add_reagent', position='P1', reagent='oxitop_pm')
        g.call(g.A, 'bottle.insert_sleeve', position='P1', naoh_pellets=0)
        g.call(g.A, 'head.screw_on', head='h1', position='P1')
        g.step(7 * 3600)
        v7 = g.call(g.A, 'head.read', head='h1')['value']
        g.step(12 * 3600)
        v19 = g.call(g.A, 'head.read', head='h1')['value']
        self.assertTrue(150.0 <= v7 <= 240.0, v7)
        self.assertGreaterEqual(v19, 280.0)

    def test_key_components_wired_to_press_key(self):              # C1-08
        comps = rt.CAPS['head.press_key']['component_ids']
        self.assertTrue(all(f'head_{i}_key_{k}' in comps for i in range(1, 7) for k in ('m', 'enter', 'nav')))


class Samples(unittest.TestCase):
    def test_sample_prepare_dilution_seed_ph_and_buffer(self):
        g = Rig(seed=10)
        g.kits('domestic_influent', 'dilution_water', 'settled_sewage_seed', 'distilled_water')
        r = g.call(g.A, 'sample.prepare', sample_id='d10', volume_m3=1e-3, sources=[dict(source_id='domestic_influent', volume_m3=1e-4)],
                   diluent='dilution_water', seed_volume_fraction=0.02, atu_kg_m3=0.005)
        self.assertAlmostEqual(r['dilution_factor'], 10.0)
        s = g.h.samples['d10']
        self.assertAlmostEqual(s['subs']['ww_readily'], 95.0 * 0.1 + rt.sample_lib()['samples']['settled_sewage_seed']['components']['readily_mg_l'] * 0.02, places=9)
        self.assertAlmostEqual(s['atu'], 5.0, places=9)
        self.assertAlmostEqual(s['pt'], 3.75 * 0.88, places=9)
        self.assertTrue(6.6 < s['ph'] < 7.6)
        g.call(g.A, 'sample.prepare', sample_id='adj', volume_m3=5e-4, sources=[dict(source_id='domestic_influent', volume_m3=5e-4)], ph_adjust_to=6.8)
        self.assertAlmostEqual(g.h.samples['adj']['ph'], 6.8)
        self.assertAlmostEqual(rt.ph_of(g.h.samples['adj']['alk'], g.h.samples['adj']['dic'], g.h.samples['adj']['pt']), 6.8, places=6)
        g.call(g.A, 'sample.prepare', sample_id='aer', volume_m3=5e-4, sources=[dict(source_id='domestic_influent', volume_m3=5e-4)], aerate=True)
        self.assertGreater(g.h.samples['aer']['ph'], 8.0)                          # CO2 stripped by aeration
        g.call(g.A, 'sample.prepare', sample_id='dw', volume_m3=1e-3, sources=[dict(source_id='domestic_influent', volume_m3=1e-4)], diluent='distilled_water')
        self.assertTrue(any(i['name'].startswith('osmotic') for i in g.h.samples['dw']['inhibitors']))
        self.assertAlmostEqual(g.h.samples['domestic_influent']['V'], 3e-3 - 1e-4 - 5e-4 - 5e-4 - 1e-4, places=12)

    def test_library_substances_degradable_vs_refractory(self):
        res = {}
        for sub, c in (('phenol', 0.08), ('pentachlorophenol', 0.08), ('sodium_acetate', 0.15)):
            g = Rig(seed=10)
            g.kits('dilution_water', 'settled_sewage_seed', 'nth_600', 'nhp_600')
            g.call(g.E, 'stock.provide', stock_id='s', volume_m3=1e-3, composition=dict(substances=[dict(substance_id=sub, concentration_kg_m3=c)], ph=7.0,
                                                                                         alkalinity_mol_m3=1.0))
            g.call(g.A, 'sample.prepare', sample_id='m', volume_m3=1e-3, sources=[dict(source_id='s', volume_m3=9.8e-4)], seed_volume_fraction=0.02,
                   diluent='dilution_water', aerate=True)
            g.load('P1', 'h1', 'm', 164.0)
            g.call(g.A, 'platform.set_power', on=True)
            g.step(5 * DAY + 4 * 3600)
            thod = rt.substances()[sub]['thod_nh4_mg_mg'] * c * 1000 * 0.98
            res[sub] = g.stored('h1')[-1] / thod
        self.assertGreater(res['phenol'], 0.5)
        self.assertGreater(res['sodium_acetate'], 0.5)
        self.assertLess(res['pentachlorophenol'], 0.1)

    def test_evaluator_substance_inhibitor_and_chlorine(self):
        def run(**comp):
            g = Rig(seed=10)
            g.kits('nth_600', 'nhp_600')
            g.call(g.E, 'substance.define', substance_id='cmpd_x', name='compound X', thod_kg_kg=1.5, f_ult=0.8, k20_per_s=3e-6)
            dechlor = comp.pop('dechlorinate', False)
            g.call(g.E, 'stock.provide', stock_id='w', volume_m3=1e-3, composition=dict(dict(substances=[dict(substance_id='cmpd_x', concentration_kg_m3=0.1)], ph=7.2,
                                                                                            seed=dict(type='activated_sludge', volume_fraction=0.02)), **comp))
            g.call(g.A, 'sample.prepare', sample_id='m', volume_m3=1e-3, sources=[dict(source_id='w', volume_m3=1e-3)], aerate=True, dechlorinate=dechlor)
            g.load('P1', 'h1', 'm', 164.0)
            g.call(g.A, 'platform.set_power', on=True)
            g.step(5 * DAY + 4 * 3600)
            return g.stored('h1')[-1]
        base = run()
        self.assertTrue(0.5 * 150 * 0.8 < base < 1.05 * 150 * 0.8 + 5.0, base)
        tox = run(inhibitors=[dict(name='cu', concentration_kg_m3=0.002, ic50_kg_m3=0.001, hill=2.0)])
        self.assertLess(tox, 0.5 * base)
        chl = run(free_chlorine_kg_m3=0.002)
        self.assertLess(chl, 0.2 * base)
        rescued = run(free_chlorine_kg_m3=0.002, dechlorinate=True)
        self.assertAlmostEqual(rescued, base, delta=0.05 * base)

    def test_events_provenance_ledger_trace(self):
        g = Rig(seed=10)
        g.gga()
        g.load('P1', 'h1', 'std', 164.0)
        g.call(g.A, 'platform.set_power', on=True)
        g.step(2 * DAY)
        codes = [e['code'] for e in g.call(g.A, 'events.read', since_s=0.0)['events']]
        for c in ('START', 'AUTOTEMP_ZERO', 'MEASUREMENT_START'):
            self.assertIn(c, codes)
        prov = g.call(g.A, 'provenance.read')
        self.assertGreaterEqual(len(prov['substances']), 80)
        led = g.call(g.E, 'ledger.read')['ledger']
        tb = g.truth('P1')
        self.assertAlmostEqual(led['o2_consumed_mg'], tb['o2_consumed_kg_m3'] * 1000 * tb['liquid_m3'] * 1000, delta=1e-6)
        self.assertLessEqual(led['co2_absorbed_mol'], led['co2_produced_mol'] + led['co2_initial_mol'] + 1e-12)
        self.assertGreater(led['co2_initial_mol'], 0.0)
        tr = g.call(g.E, 'data.trace', head='h1', max_points=50)
        self.assertEqual(len(tr['time_s']), 50)
        dps = [x for x in tr['delta_p_pa'] if x is not None]
        self.assertGreater(dps[-1], dps[0])
        g.call(g.A, 'head.stop', head='h1')
        g.call(g.A, 'head.unscrew', head='h1')
        out = g.call(g.A, 'bottle.empty', position='P1')
        self.assertAlmostEqual(out['removed_m3'], 1.64e-4)
        self.assertAlmostEqual(g.call(g.E, 'ledger.read')['ledger']['waste_m3'], 1.64e-4)


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()
