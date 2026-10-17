"""tests for CHEM-095 wyatt_eclipseaf4 (Wyatt Eclipse AF4 + analytical channel): python3 -m unittest tests/test_asset_wyatt_eclipseaf4.py

Themes: contract consistency (slots, effects, errors raised vs declared); set-points only write targets; read / describe pure; rejected calls leave the
state unchanged; idempotent request ids; two instances isolated; agent / evaluator truth isolation (truth slots, evaluator capabilities, blind
samples); mass conservation (loop and channel ledgers); every output validated against its schema and every success changes only declared slots;
step-partition independence; AF4 physics: elution in order of size, retention time vs the Wahlund-Giddings void time and R(lambda) for a constant
cross flow, stronger cross flow retains longer, membrane cut-off losses, void peak with too short a focus, overpressure protection at 30 bar,
thickness calibration with a standard recovers the true thickness and the R_h of other analytes; template-backed results carry provenance."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import wyatt_eclipseaf4 as rt  # noqa: E402
import asset_tool  # noqa: E402

INST = 'wyatt_eclipseaf4_01'
SEEN_CAPS = set()
ML_MIN = 1e-6 / 60.0


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


def method(vc=2.0, vdet=1.0, focus_s=120.0, inject_s=120.0, elution_s=1500.0, end_vc=None, profile='constant', flush_s=300.0):
    steps = [dict(mode='elution', duration_s=30.0, cross_flow_start_m3_s=vc * ML_MIN),
             dict(mode='focus', duration_s=60.0, cross_flow_start_m3_s=vc * ML_MIN),
             dict(mode='focus_inject', duration_s=inject_s, cross_flow_start_m3_s=vc * ML_MIN),
             dict(mode='focus', duration_s=focus_s, cross_flow_start_m3_s=vc * ML_MIN),
             dict(mode='elution', duration_s=elution_s, cross_flow_start_m3_s=vc * ML_MIN, cross_flow_end_m3_s=(vc if end_vc is None else end_vc) * ML_MIN,
                  profile=profile)]
    if flush_s:
        steps.append(dict(mode='elution', duration_s=flush_s, cross_flow_start_m3_s=0.0))
    return dict(detector_flow_m3_s=vdet * ML_MIN, focus_flow_m3_s=vc * ML_MIN, inject_flow_m3_s=0.2 * ML_MIN, steps=steps)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.WyattEclipseaf4Runtime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A = self.h.session('agent')
        self.E = self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        before, t0 = self.h.read('evaluator'), self.h.t
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'r{self.n}')
        after = self.h.read('evaluator')
        changed = {k for k in after if _differ(after[k], before[k])} - {'clock.time_s'}
        if expect == 'succeeded':
            assert r['status'] == 'succeeded', (cap, r['error'])
            rt.validate(rt.CAPS[cap]['output_schema'], r['result'])
            errs = asset_tool.validate_value(r['result'], rt.export_wyatt_eclipseaf4_contract_metadata()['capabilities'][cap]['output_schema'])
            assert not errs, (cap, errs[:3])
            allowed = set(rt.EFFECTS.get(cap, [])) | (set(rt.ON_STEP['sim.step']) if cap == 'sim.step' else set())
            extra = changed - allowed
            assert not extra, (cap, sorted(extra))
            if cap != 'sim.step':
                assert self.h.t == t0, cap
            SEEN_CAPS.add(cap)
            return r['result']
        assert r['status'] in ('rejected', 'unsupported'), (cap, r['status'], r.get('result'))
        assert r['error']['code'] == expect, (cap, r['error']['code'], expect)
        assert not changed, (cap, 'rejected call changed', sorted(changed))
        assert expect in [e['code'] for e in rt.export_wyatt_eclipseaf4_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def ready(self, m=None):
        self.call(self.A, 'power.set', on=True)
        self.call(self.A, 'method.set', **(m or method()))

    def load(self, comps, vol=20e-9, sid='s1'):
        return self.call(self.A, 'sample.load', sample_id=sid, volume_m3=vol, components=[dict(template_id=t, concentration_kg_m3=c) for t, c in comps])

    def run(self, total=None):
        r = self.call(self.A, 'run.start')
        self.step(total or r['expected_duration_s'] + 60.0)
        return r

    def peaks(self, **kw):
        return self.call(self.A, 'peaks.analyze', **kw)['peaks']

    def ledger(self):
        return self.call(self.E, 'ledger.read')['ledger']

    def events(self, who=None):
        return [e['code'] for e in self.call(who or self.E, 'events.read', since_s=0.0)['events']]


def _balance(test, L):
    scale = max(L['injected_kg'], L['loaded_kg'], 1e-12)
    test.assertLess(abs(L['injected_balance_kg']), 1e-9 * scale, L)
    test.assertLess(abs(L['loop_balance_kg']), 1e-9 * scale, L)


def _raised_codes():
    src = Path(rt.__file__).read_text()
    funcs = {}
    for m in re.finditer(r'\n    def (\w+)\(self[^\n]*\n(.*?)(?=\n    def |\n# ----|\nclass |\ndef )', src, re.S):
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
            todo += [g for g in calls.get(f, ()) if g not in ('_cmd_sim_step', 'step', '_tick')]
        out[k] = codes
    name = {'_cmd_' + c.replace('.', '_'): c for c in rt.CAPS}
    return {name[k]: v for k, v in out.items() if k in name}


class Contract(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.wyatt_eclipseaf4')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-095')

    def test_metadata_consistent_and_schemas_lint(self):
        m = rt.export_wyatt_eclipseaf4_contract_metadata()
        for k, c in m['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], m['state_slots'], (k, e))
            for p in c['preconditions']:
                for s in p['state_ids']:
                    self.assertIn(s, m['state_slots'], (k, s))
            self.assertFalse(asset_tool.lint_schema(c['input_schema']), k)
            self.assertFalse(asset_tool.lint_schema(c['output_schema']), k)
        for s in rt.SCENE_MIRRORED:
            self.assertEqual(rt.SLOTS[s]['visibility'], 'agent')
        for k, s in m['state_slots'].items():
            self.assertFalse(asset_tool.lint_schema(s['value_schema']), k)

    def test_declared_errors_cover_raised_codes(self):
        m = rt.export_wyatt_eclipseaf4_contract_metadata()
        for cap, codes in _raised_codes().items():
            declared = {e['code'] for e in m['capabilities'][cap]['errors']}
            self.assertFalse(codes - declared, (cap, codes - declared))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            r = g.A.invoke(instance_id=INST, capability_id=k, arguments={}, request_id='u' + k)
            self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))

    def test_describe_and_read_are_pure(self):
        g = Rig()
        g.ready()
        t0, rev = g.h.t, g.h.revision
        d1 = g.A.describe(instance_id=INST)
        r1 = g.A.read(instance_id=INST)
        r2 = g.A.read(instance_id=INST)
        self.assertEqual(r1, r2)
        self.assertEqual(d1, g.A.describe(instance_id=INST))
        self.assertEqual((g.h.t, g.h.revision), (t0, rev))
        json.dumps(r1)
        json.dumps(g.E.read(instance_id=INST))

    def test_agent_truth_isolation(self):
        g = Rig()
        obs = g.A.read(instance_id=INST)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        for cap, args in (('truth.inspect', {}), ('ledger.read', {}), ('fault.inject', dict(fault='pump_failure', active=True)), ('sim.step', dict(dt_s=1.0)),
                          ('plant.configure', dict(rt.PLANT_EXAMPLE)), ('sample.provide', dict(provided_id='x', components=[dict(template_id='ovalbumin', concentration_kg_m3=1.0)]))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        d = g.A.describe(instance_id=INST)
        self.assertNotIn('truth.inspect', d['capabilities'])
        self.assertNotIn('truth.process', d['state_slots'])

    def test_blind_sample_hidden_from_agent(self):
        g = Rig()
        g.call(g.E, 'sample.provide', provided_id='unk1', components=[dict(template_id='aldolase', concentration_kg_m3=1.0)])
        g.ready()
        g.call(g.A, 'sample.load', sample_id='u', volume_m3=20e-9, provided_id='unk1')
        s = g.read('sample.loaded', who=g.A)
        self.assertTrue(s['blind'])
        self.assertNotIn('components', s)
        g.run()
        fr = g.call(g.A, 'fractogram.read')
        self.assertIsNone(fr['template_source']['records'])
        self.assertNotIn('SAMPLE_PROVIDED', g.events(g.A))
        self.assertIn('SAMPLE_PROVIDED', g.events())
        self.assertNotIn('aldolase', json.dumps(g.A.read(instance_id=INST)))
        self.assertIn('aldolase', json.dumps(g.E.read(instance_id=INST)))

    def test_idempotent_request_ids(self):
        g = Rig()
        r1 = g.A.invoke(instance_id=INST, capability_id='power.toggle', arguments={}, request_id='same')
        r2 = g.A.invoke(instance_id=INST, capability_id='power.toggle', arguments={}, request_id='same')
        self.assertEqual(r1, r2)
        self.assertTrue(g.read('power.on'))
        r3 = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=False), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='a'), Rig(instance='b')
        a.ready()
        a.load([('bovine_serum_albumin', 1.0)])
        a.call(a.A, 'run.start')
        a.step(600.0)
        self.assertEqual(b.read('run.state'), 'idle')
        self.assertFalse(b.read('power.on'))
        self.assertIsNone(b.read('sample.loaded'))
        r = b.A.invoke(instance_id='a', capability_id='power.set', arguments=dict(on=True), request_id='z')
        self.assertEqual(r['error']['code'], 'UNKNOWN_INSTANCE')


class Preconditions(unittest.TestCase):
    def test_rejections_leave_state_unchanged(self):
        g = Rig()
        g.call(g.A, 'method.set', expect='POWER_OFF', **method())
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'run.start', expect='NO_METHOD')
        g.call(g.A, 'method.set', **method())
        g.call(g.A, 'run.start', expect='NO_SAMPLE')
        g.call(g.A, 'membrane.install', expect='CHANNEL_CLOSED', membrane='PES_10kDa')
        g.call(g.A, 'sample.load', expect='SOLVENT_INCOMPATIBLE', sample_id='o', volume_m3=10e-9,
               components=[dict(template_id='nist_rm8027_si2', concentration_kg_m3=0.01)])
        bad = method()
        bad['steps'].append(dict(mode='focus_inject', duration_s=10.0, cross_flow_start_m3_s=1e-8))
        g.call(g.A, 'method.set', expect='INVALID_METHOD', **bad)
        bad = method()
        bad['steps'].append(dict(mode='focus', duration_s=10.0, cross_flow_start_m3_s=1e-8))
        g.call(g.A, 'method.set', expect='INVALID_METHOD', **bad)
        g.call(g.A, 'method.set', expect='FLOW_LIMIT', **method(vc=9.5, vdet=1.0))
        g.load([('ovalbumin', 1.0)])
        g.call(g.A, 'sample.load', expect='SAMPLE_PRESENT', sample_id='b', volume_m3=10e-9, components=[dict(template_id='ovalbumin', concentration_kg_m3=1.0)])
        g.call(g.A, 'channel.set_lift', lift_m=0.03)
        g.call(g.A, 'run.start', expect='CHANNEL_OPEN')
        g.call(g.A, 'membrane.install', expect='MEMBRANE_PRESENT', membrane='RC_30kDa')
        g.call(g.A, 'membrane.remove')
        g.call(g.A, 'channel.set_lift', lift_m=0.0)
        g.call(g.A, 'run.start', expect='CHANNEL_NOT_ASSEMBLED')
        g.call(g.A, 'channel.set_lift', lift_m=0.04)
        g.call(g.A, 'membrane.install', membrane='RC_30kDa')
        g.call(g.A, 'spacer.install', expect='SPACER_PRESENT', thickness_m=250e-6)
        g.call(g.A, 'spacer.remove')
        g.call(g.A, 'spacer.install', expect='INVALID_ARGUMENT', thickness_m=300e-6)
        g.call(g.A, 'spacer.install', thickness_m=250e-6)
        g.call(g.A, 'channel.set_lift', lift_m=0.0)
        g.call(g.A, 'run.start')
        g.call(g.A, 'channel.set_lift', expect='FLOW_ACTIVE', lift_m=0.02)
        g.call(g.A, 'method.set', expect='RUN_ACTIVE', **method())
        g.call(g.A, 'sample.unload', expect='RUN_ACTIVE')
        g.call(g.A, 'run.start', expect='RUN_ACTIVE')
        g.step(100.0)
        self.assertTrue(g.call(g.A, 'run.abort')['aborted'])
        g.call(g.A, 'channel.set_lift', expect='FLOW_ACTIVE', lift_m=0.02)      # still pressurised: the 2 s lag has not relieved it
        g.step(60.0)
        g.call(g.A, 'channel.set_lift', lift_m=0.02)
        self.assertIn('RUN_ABORTED', g.events())
        _balance(self, g.ledger())

    def test_organic_version_rules(self):
        g = Rig(version='organic', carrier='thf')
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'channel.set_lift', lift_m=0.04)
        g.call(g.A, 'membrane.remove')
        g.call(g.A, 'membrane.install', expect='MEMBRANE_INCOMPATIBLE', membrane='PES_10kDa')
        g.call(g.A, 'membrane.install', membrane='RC_10kDa')
        g.call(g.A, 'channel.set_lift', lift_m=0.0)
        g.call(g.A, 'sample.load', expect='SOLVENT_INCOMPATIBLE', sample_id='a', volume_m3=10e-9, components=[dict(template_id='ovalbumin', concentration_kg_m3=1.0)])
        g.call(g.A, 'sample.load', sample_id='si', volume_m3=20e-9, components=[dict(template_id='nist_srm706a_ps285k', concentration_kg_m3=1.0)])
        self.assertEqual(g.read('config.summary')['carrier'], 'thf')
        h = rt.WyattEclipseaf4Runtime('x')
        h.register_principal('e', 'evaluator')
        r = h.session('e').invoke(instance_id='x', capability_id='plant.configure', arguments=dict(rt.PLANT_EXAMPLE, carrier='toluene'), request_id='1')
        self.assertEqual(r['error']['code'], 'SOLVENT_INCOMPATIBLE')


class Physics(unittest.TestCase):
    def test_elution_order_and_conservation(self):
        g = Rig()
        g.ready(method(vc=2.0, elution_s=1500.0, end_vc=0.1, profile='linear'))
        g.load([('bovine_serum_albumin', 1.0), ('thyroglobulin', 1.0), ('nist_srm1963a_ps100', 0.5)])
        g.run()
        self.assertEqual(g.read('run.state'), 'completed')
        tr = g.call(g.E, 'truth.inspect')['process']
        pk = g.peaks(detector='dri')
        normal = [p for p in pk if p['regime'] == 'normal']
        self.assertGreaterEqual(len(normal), 3)
        rhs = [p['rh_from_retention_m'] for p in normal[:3]]
        self.assertEqual(rhs, sorted(rhs))                                   # smaller analytes first
        L = g.ledger()
        _balance(self, L)
        self.assertGreater(L['eluted_kg'], 0.97 * L['injected_kg'])
        self.assertIsNotNone(tr['thickness_true_m'])
        fr = g.call(g.A, 'fractogram.read')
        self.assertEqual(fr['template_source']['records'], ['bovine_serum_albumin', 'nist_srm1963a_ps100', 'thyroglobulin'])
        self.assertEqual(fr['template_source']['sha256'], rt.library()['sha256'])

    def test_retention_matches_theory_constant_cross_flow(self):
        g = Rig(detectors=['dri'])
        vc, vd = 1.0, 1.0
        g.ready(method(vc=vc, vdet=vd, focus_s=300.0, elution_s=3000.0, flush_s=0.0))
        g.load([('ovalbumin', 2.0)])
        g.run()
        pk = [p for p in g.peaks(detector='dri') if p['regime'] == 'normal']
        self.assertEqual(len(pk), 1)
        geo = rt.Geometry('long', g.h._w_true())
        t0 = geo.void_time(vd * ML_MIN, vc * ML_MIN)
        D = g.h._diff(rt.template('ovalbumin')['values']['hydrodynamic_radius_m'])
        lam = D * geo.AT / (vc * ML_MIN * geo.w)
        tr = t0 / rt.retention_ratio(lam)
        t_start = 30.0 + 60.0 + 120.0 + 300.0
        observed = pk[0]['apex_time_s'] - t_start - rt.ENG['transfer_volume_m3'] / (vd * ML_MIN)
        self.assertAlmostEqual(observed / tr, 1.0, delta=0.03)              # apex of a slightly polydisperse band vs the mean-zone retention

    def test_stronger_cross_flow_retains_longer(self):
        out = []
        for vc in (0.5, 1.5):
            g = Rig()
            g.ready(method(vc=vc, elution_s=2400.0, flush_s=0.0))
            g.load([('apoferritin', 1.0)])
            g.run()
            out.append([p['apex_time_s'] for p in g.peaks() if p['regime'] == 'normal'][0] - (30.0 + 60.0 + 120.0 + 120.0))
        self.assertGreater(out[1], 1.5 * out[0])                            # t_r ~ (V0 w / 6 D A_T) ln(1 + V_c / V_out) at small lambda

    def test_membrane_cutoff_losses(self):
        rec = {}
        for mem in ('RC_10kDa', 'PES_30kDa'):
            g = Rig()
            g.call(g.A, 'channel.set_lift', lift_m=0.04)
            g.call(g.A, 'membrane.remove')
            g.call(g.A, 'membrane.install', membrane=mem)
            g.call(g.A, 'channel.set_lift', lift_m=0.0)
            g.ready()
            g.load([('ribonuclease_a', 1.0), ('bovine_serum_albumin', 1.0)])
            g.run()
            L = g.ledger()
            _balance(self, L)
            z = {}
            for e in g.call(g.E, 'truth.inspect')['process']['zones']:
                z[e['species']] = z.get(e['species'], 0.0) + e['permeated_kg']
            rec[mem] = L['permeated_kg'] / L['injected_kg']
        self.assertLess(rec['RC_10kDa'], rec['PES_30kDa'])
        self.assertGreater(rec['PES_30kDa'], 0.3)                            # RNase A (13.7 kDa) passes a 30 kDa membrane
        self.assertLess(rt.sieving(3.55e-9, 'RC_10kDa'), 1e-3)

    def test_short_focus_gives_void_peak(self):
        g = Rig()
        g.ready(method(vc=3.0, focus_s=1.0, inject_s=6.0))
        g.load([('igg_human', 2.0)], vol=2e-9)
        g.run()
        self.assertIn('INCOMPLETE_RELAXATION', g.events())                  # hidden relaxation state: evaluator only (R1-04)
        self.assertNotIn('INCOMPLETE_RELAXATION', g.events(g.A))
        self.assertTrue([p for p in g.peaks() if p['regime'] == 'void'])

    def test_overpressure_protection(self):
        g = Rig()
        g.call(g.A, 'channel.set_lift', lift_m=0.04)
        g.call(g.A, 'membrane.remove')
        g.call(g.A, 'membrane.install', membrane='PES_1kDa')
        g.call(g.A, 'channel.set_lift', lift_m=0.0)
        g.ready(method(vc=6.0, vdet=1.0))
        g.load([('ovalbumin', 1.0)])
        g.call(g.A, 'run.start')
        g.step(60.0)
        self.assertEqual(g.read('run.state'), 'aborted')
        self.assertEqual(g.read('alarm.state'), 'overpressure')
        self.assertEqual(g.read('display.text'), 'OVERPRES')
        _balance(self, g.ledger())

    def test_outlet_blockage_fault(self):
        g = Rig()
        g.ready()
        g.load([('ovalbumin', 1.0)])
        g.call(g.E, 'fault.inject', fault='outlet_blockage', active=True)
        g.call(g.A, 'run.start')
        g.step(120.0)
        self.assertEqual(g.read('run.state'), 'aborted')
        self.assertNotIn('FAULT_OUTLET_BLOCKAGE', g.events(g.A))

    def test_calibration_recovers_thickness_and_sizes(self):
        g = Rig()
        g.ready(method(vc=2.0, elution_s=1800.0, end_vc=0.1, profile='linear'))
        g.load([('bovine_serum_albumin', 1.0), ('thyroglobulin', 1.0)])
        g.run()
        pk = [p for p in g.peaks() if p['regime'] == 'normal']
        bsa = pk[0]
        self.assertLess(bsa['rh_from_retention_m'], 0.85 * 3.55e-9)         # nominal 350 um spacer: biased small
        r = g.call(g.A, 'channel.calibrate_thickness', template_id='bovine_serum_albumin', retention_time_s=bsa['apex_time_s'])
        self.assertAlmostEqual(r['thickness_m'] / g.h._w_true(), 1.0, delta=0.01)
        pk2 = [p for p in g.peaks(dn_dc_m3_kg=0.185e-3) if p['regime'] == 'normal']
        self.assertAlmostEqual(pk2[0]['rh_from_retention_m'] / 3.55e-9, 1.0, delta=0.03)
        self.assertAlmostEqual(pk2[1]['rh_from_retention_m'] / 8.5e-9, 1.0, delta=0.05)
        self.assertAlmostEqual(pk2[1]['molar_mass_kg_mol'] / 606.444, 1.0, delta=0.08)
        g.call(g.A, 'channel.calibrate_thickness', expect='CALIBRATION_OUT_OF_RANGE', template_id='bovine_serum_albumin', retention_time_s=20000.0)

    def test_step_partition_independence(self):
        a, b = Rig(seed=5), Rig(seed=5)
        for g in (a, b):
            g.ready()
            g.load([('ovalbumin', 1.0)])
            g.call(g.A, 'run.start')
        a.step(1500.0)
        for _ in range(15):
            b.step(100.0)
        ra, rb = a.call(a.A, 'fractogram.read'), b.call(b.A, 'fractogram.read')
        self.assertEqual(ra['rows'], rb['rows'])
        self.assertEqual(a.read('detector.dri_riu'), b.read('detector.dri_riu'))

    def test_setpoints_do_not_move_physics(self):
        g = Rig()
        g.ready()
        self.assertEqual(g.read('flow.cross_set_m3_s'), 0.0)
        self.assertEqual(g.read('pressure.channel_pa'), 0.0)
        g.load([('ovalbumin', 1.0)])
        self.assertEqual(g.ledger()['injected_kg'], 0.0)                    # loading fills the loop only; injection happens in the run
        g.call(g.A, 'run.start')
        self.assertEqual(g.read('flow.cross_set_m3_s'), 0.0)
        g.step(1.0)
        self.assertGreater(g.read('flow.cross_set_m3_s'), 0.0)
        self.assertEqual(g.read('display.text'), 'Elution')
        g.step(60.0)
        self.assertEqual(g.read('display.text'), 'Focus')
        self.assertAlmostEqual(g.read('display.cross_flow_ml_min'), 2.0)

    def test_open_channel_discards_sample_and_power_cycle(self):
        g = Rig()
        g.ready()
        g.load([('ovalbumin', 1.0)])
        g.call(g.A, 'run.start')
        g.step(400.0)
        g.call(g.A, 'power.toggle')                                          # power off aborts
        self.assertEqual(g.read('run.state'), 'aborted')
        g.step(60.0)
        g.call(g.A, 'channel.set_lift', lift_m=0.04)
        self.assertTrue(g.read('channel.open'))
        L = g.ledger()
        _balance(self, L)
        self.assertGreater(L['flushed_kg'], 0.0)
        g.call(g.A, 'sample.unload')
        self.assertEqual(g.read('display.text'), '')
        g.call(g.A, 'power.toggle')
        self.assertEqual(g.read('display.text'), 'OPEN')
        _balance(self, g.ledger())

    def test_mains_outage_and_pump_failure(self):
        g = Rig()
        g.ready()
        g.load([('ovalbumin', 1.0)])
        g.call(g.A, 'run.start')
        g.step(100.0)
        g.call(g.E, 'fault.inject', fault='pump_failure', active=True)
        self.assertEqual(g.read('alarm.state'), 'pump_failure')
        g.call(g.A, 'run.start', expect='PUMP_FAILURE')
        g.call(g.E, 'fault.inject', fault='pump_failure', active=False)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.call(g.A, 'power.set', expect='NO_MAINS', on=True)
        self.assertEqual(g.read('display.text'), '')
        g.call(g.E, 'fault.inject', fault='detector_fault', active=True)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        g.call(g.A, 'power.set', on=True)
        g.step(5.0)
        self.assertIsNone(g.read('detector.dri_riu'))
        _balance(self, g.ledger())

    def test_template_library_coverage(self):
        lib = rt.library()['doc']
        self.assertGreaterEqual(len(lib['templates']), 50)
        for r in lib['templates']:
            self.assertIn(r['evidence'], ('measured_literature', 'measured_certificate', 'derived'))
            self.assertTrue(r['source'] and r['licence_flag'])
            self.assertGreater(r['values']['hydrodynamic_radius_m'], 0.0)

    def test_quiet_skip_exact(self):
        g = Rig()
        g.call(g.A, 'power.set', on=True)
        t = g.h.tick
        g.step(3600.0)
        self.assertEqual(g.h.tick - t, 36000)
        self.assertAlmostEqual(g.read('clock.time_s'), 3600.0)


class ReviewRegressions(unittest.TestCase):
    def test_carry_over_refocused_and_attributed(self):                     # R1-02, R1-01
        g = Rig()
        short = method(vc=2.0, elution_s=400.0, flush_s=0.0)
        g.ready(short)
        g.load([('bovine_serum_albumin', 1.0), ('thyroglobulin', 1.0)])
        g.run()
        left = g.ledger()['in_channel_kg']
        self.assertGreater(left, 1e-9)                                      # thyroglobulin still in the channel
        codes_a, codes_e = g.events(g.A), g.events()
        self.assertIn('MATERIAL_LEFT_IN_CHANNEL', codes_e)
        self.assertNotIn('MATERIAL_LEFT_IN_CHANNEL', codes_a)
        self.assertNotIn('µg', json.dumps(g.call(g.A, 'events.read', since_s=0.0), ensure_ascii=False))
        blank = method(vc=2.0, elution_s=1500.0)
        blank['steps'] = [st for st in blank['steps'] if st['mode'] != 'focus_inject']
        g.call(g.A, 'method.set', **blank)
        g.run()
        fr = g.call(g.A, 'fractogram.read')
        self.assertEqual(fr['template_source']['carry_over'], ['bovine_serum_albumin', 'thyroglobulin'])
        self.assertIn('thyroglobulin', fr['template_source']['records'])
        pk = [p for p in g.peaks() if p['regime'] == 'normal']
        self.assertTrue(pk)
        big = max(pk, key=lambda p: p['area'])
        self.assertAlmostEqual(big['rh_from_retention_m'] / (8.5e-9 * 0.735), 1.0, delta=0.12)   # nominal-thickness bias as for a fresh injection
        _balance(self, g.ledger())

    def test_focus_flow_limit_alarm_clear_detectors_off(self):           # R1-03, R1-08
        g = Rig()
        g.call(g.A, 'power.set', on=True)
        m = method(vc=1.0)
        m['focus_flow_m3_s'] = 9.5 * ML_MIN
        g.call(g.A, 'method.set', expect='FLOW_LIMIT', **m)
        g.call(g.A, 'method.set', **method())
        g.load([('ovalbumin', 1.0)])
        g.call(g.A, 'run.start')
        g.step(30.0)
        self.assertIsNotNone(g.read('detector.dri_riu'))
        g.call(g.E, 'fault.inject', fault='pump_failure', active=True)
        self.assertEqual(g.read('alarm.state'), 'pump_failure')
        g.call(g.E, 'fault.inject', fault='pump_failure', active=False)
        self.assertIsNone(g.read('alarm.state'))
        g.call(g.A, 'power.set', on=False)
        self.assertIsNone(g.read('detector.dri_riu'))

    def test_analysis_uses_run_conditions(self):                          # R1-07
        g = Rig()
        g.ready()
        g.load([('bovine_serum_albumin', 1.0)])
        g.run()
        before = [p['rh_from_retention_m'] for p in g.peaks() if p['regime'] == 'normal']
        g.step(60.0)
        g.call(g.A, 'channel.set_lift', lift_m=0.04)
        g.call(g.A, 'spacer.remove')
        g.call(g.A, 'spacer.install', thickness_m=490e-6)
        g.call(g.A, 'channel.set_lift', lift_m=0.0)
        after = [p['rh_from_retention_m'] for p in g.peaks() if p['regime'] == 'normal']
        self.assertEqual(before, after)
        g.call(g.A, 'channel.calibrate_thickness', expect='RUN_CONDITIONS_CHANGED', template_id='bovine_serum_albumin', retention_time_s=600.0)

    def test_blind_provenance_hides_evidence(self):                       # R1-05
        g = Rig()
        g.call(g.E, 'sample.provide', provided_id='b', components=[dict(template_id='nist_rm8012_au30', concentration_kg_m3=0.1)])
        g.ready()
        g.call(g.A, 'sample.load', sample_id='x', volume_m3=10e-9, provided_id='b')
        g.call(g.A, 'run.start')
        g.step(400.0)
        src = g.call(g.A, 'fractogram.read')['template_source']
        self.assertIsNone(src['records'])
        self.assertIsNone(src['evidence'])
        self.assertIsNone(src['carry_over'])


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        missing = set(rt.CAPS) - set(rt.UNSUPPORTED) - SEEN_CAPS
        if len(SEEN_CAPS) > 10:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()
