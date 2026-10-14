"""tests for CHEM-194 inficon_ul1000fab (INFICON UL1000 Fab helium leak detector): python3 -m unittest tests/test_asset_inficon_ul1000fab.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; run-up 3 min; pump-down and autoranging (15 / 2 / 0.4 mbar) faster for small volumes and slower with polymer outgassing;
large leak -> maximum evacuation time W76; response time of a sprayed leak grows with the part volume and the leak path; the detection limit is a
display floor, not the reading error; ZERO, I-ZERO (stable signal only) and auto zero; internal / external calibration, temperature drift and
W80 / W81; O-ring permeation slow signal; sniffer mode (air helium background, distance law, W62 / W63); molecular-flow leak-rate conversion;
helium and total-gas ledgers closing exactly; vent and part exchange."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import inficon_ul1000fab as rt  # noqa: E402

INST = 'inficon_ul1000fab_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
MB = rt.MBAR_L
CHAMBER = dict(part_id='chamber', volume_m3=0.01, surface_area_m2=0.3, material='stainless_unbaked',
               leaks=[dict(leak_id='weld', helium_leak_rate_pa_m3_s=1e-9 * MB, path_length_m=0.3, path_diameter_m=0.004)])


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.InficonUl1000fabRuntime(instance)
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
        assert expect in [e['code'] for e in rt.export_inficon_ul1000fab_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def ready(self):
        self.call(self.A, 'power.set', on=True)
        self.step(181.0)

    def until(self, pred, limit, every=1.0):
        t = 0.0
        while not pred() and t < limit:
            self.step(every)
            t += every
        return t

    def ledger(self):
        return self.call(self.E, 'ledger.read')['ledger']


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
        out[k[5:].replace('_', '.', 1)] = codes
    return out


def _balance(test, L):
    he_in = L['he_initial'] + L['he_added_with_part'] + L['he_leak_in'] + L['he_permeation_in'] + L['he_vent_in']
    he_out = L['he_pumped'] + L['he_vent_out'] + L['he_removed_with_part'] + L['he_in_volume']
    test.assertAlmostEqual(he_in, he_out, delta=1e-9 * he_in + 1e-18)
    g_in = L['gas_initial'] + L['gas_added_with_part'] + L['gas_leak_in'] + L['gas_outgassing'] + L['gas_vent_in']
    g_out = L['gas_pumped'] + L['gas_vent_out'] + L['gas_removed_with_part'] + L['gas_in_volume']
    test.assertAlmostEqual(g_in, g_out, delta=1e-9 * g_in)


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_and_mirrors_consistent(self):
        meta = rt.export_inficon_ul1000fab_contract_metadata()
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
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for name, p in meta['mechanisms']['helium_leak_detection']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_inficon_ul1000fab_contract_metadata()
        for cap, codes in _raised_codes().items():
            declared = {e['code'] for e in meta['capabilities'][cap]['errors']}
            self.assertTrue(codes <= declared, (cap, sorted(codes - declared)))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            r = g.A.invoke(instance_id=INST, capability_id=k, arguments={}, request_id='u-' + k)
            self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))

    def test_agent_truth_isolation(self):
        g = Rig()
        d = g.A.describe(instance_id=INST)
        self.assertFalse(any(k.startswith('truth.') for k in d['state_slots']))
        with self.assertRaises(rt.ActionRejected):
            g.A.read(instance_id=INST, state_ids=['truth.gas'])
        for cap, args in (('truth.inspect', {}), ('sim.step', dict(dt_s=1.0)), ('ledger.read', {}), ('fault.inject', dict(fault='turbo_failure', active=True))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x-' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED', cap)
        g.call(g.E, 'fault.inject', fault='helium_contamination', active=True)
        self.assertFalse(any(e['kind'] in ('fault', 'truth') for e in g.call(g.A, 'events.read', since_s=0.0)['events']))

    def test_idempotent_request_ids(self):
        g = Rig()
        g.ready()
        args = dict(trigger_1_pa_m3_s=1e-11)
        r1 = g.A.invoke(instance_id=INST, capability_id='settings.configure', arguments=args, request_id='same')
        snap = g.h.read('evaluator')
        r2 = g.A.invoke(instance_id=INST, capability_id='settings.configure', arguments=args, request_id='same')
        self.assertEqual(r1, r2)
        self.assertEqual(json.dumps(snap, sort_keys=True, default=str), json.dumps(g.h.read('evaluator'), sort_keys=True, default=str))
        r3 = g.A.invoke(instance_id=INST, capability_id='settings.configure', arguments=dict(trigger_1_pa_m3_s=1e-12), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='t_a'), Rig(instance='t_b', ambient_k=298.15)
        a.call(a.A, 'power.set', on=True)
        self.assertFalse(b.read('power.on'))
        r = a.A.invoke(instance_id='t_b', capability_id='power.set', arguments=dict(on=True), request_id='z')
        self.assertEqual(r['error']['code'], 'UNKNOWN_INSTANCE')

    def test_step_partition_independence(self):
        def run(steps):
            g = Rig(seed=5)
            g.call(g.A, 'power.set', on=True)
            g.call(g.A, 'part.connect', **CHAMBER)
            for s in steps:
                g.step(s)
            r = g.read()
            return json.dumps({k: r[k] for k in r if not k.startswith('clock')}, sort_keys=True), json.dumps(g.ledger(), sort_keys=True)
        a = run([400.0])
        b = run([0.05] * 200 + [0.37, 13.11, 376.52])
        c = run([20.0] * 20)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_rejections_change_nothing(self):
        g = Rig()
        r = rt.InficonUl1000fabRuntime('fresh')
        r.register_principal('e', 'evaluator')
        self.assertEqual(r.session('e').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=2))
        g.call(g.A, 'measure.start', expect='POWER_OFF')
        g.call(g.A, 'part.disconnect', expect='NO_PART')
        g.call(g.A, 'spray.set', expect='NO_PART', target='x', on=True)
        g.call(g.A, 'part.connect', expect='DUPLICATE_LEAK', **dict(CHAMBER, leaks=CHAMBER['leaks'] * 2))
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'measure.start', expect='RUNNING_UP')
        g.step(181.0)
        g.call(g.A, 'zero.set', expect='NOT_MEASURING', enabled=True)
        g.call(g.A, 'settings.configure', expect='TRIGGER_ORDER', trigger_1_pa_m3_s=1e-9, trigger_2_pa_m3_s=1e-10)
        g.call(g.A, 'calibration.start', expect='MISSING_SETTING', kind='external')
        g.call(g.A, 'sniffer.position', expect='NO_SNIFFER', target='x', distance_m=0.0)
        g.call(g.A, 'part.connect', **CHAMBER)
        g.call(g.A, 'part.connect', expect='INLET_OCCUPIED', **CHAMBER)
        g.call(g.A, 'measure.start')
        g.step(5.0)
        g.call(g.A, 'part.disconnect', expect='INLET_NOT_VENTED')
        g.call(g.A, 'settings.configure', expect='MEASURING', mode='sniff')
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='alien', active=True)
        g.call(g.E, 'fault.inject', fault='turbo_failure', active=True)
        g.call(g.A, 'zero.set', expect='DETECTOR_FAULT', enabled=True)


class Physics(unittest.TestCase):
    def test_run_up_pumpdown_and_autoranging(self):
        g = Rig()
        g.call(g.A, 'power.set', on=True)
        self.assertEqual(g.read('state.current'), 'run_up')
        g.step(179.0)
        self.assertEqual(g.read('state.current'), 'run_up')
        g.step(2.0)
        self.assertEqual(g.read('state.current'), 'standby')
        g.call(g.A, 'part.connect', **CHAMBER)
        g.call(g.A, 'measure.start')
        seen = []
        t = g.until(lambda: (seen.append(g.read('range.current')) or True) and g.read('range.current') == 'ULTRA', 600.0, 0.5)
        order = [x for i, x in enumerate(seen) if x and (i == 0 or seen[i - 1] != x)]
        self.assertEqual(order[:3], ['GROSS', 'FINE', 'ULTRA'])
        self.assertLess(t, 120.0)
        ta = g.read('truth.gas')
        self.assertLess(ta['total_pressure_pa'], 40.0)
        big = Rig()
        big.ready()
        big.call(big.A, 'part.connect', **dict(CHAMBER, volume_m3=0.2))
        big.call(big.A, 'measure.start')
        t_big = big.until(lambda: big.read('range.current') == 'ULTRA', 3000.0, 2.0)
        self.assertGreater(t_big, 3 * t)                                      # pump-down time grows with the volume
        poly = Rig()
        poly.ready()
        poly.call(poly.A, 'part.connect', **dict(CHAMBER, surface_area_m2=2.0, material='neoprene_unbaked'))
        poly.call(poly.A, 'measure.start')
        t_poly = poly.until(lambda: poly.read('range.current') == 'ULTRA', 1700.0, 5.0)
        self.assertGreater(t_poly, 2 * t)                                     # polymer outgassing delays ULTRA
        CHECKS.append(dict(id='pumpdown', status='passed', layer='process', t_ultra_10l_s=t, t_ultra_200l_s=t_big, t_ultra_neoprene_s=t_poly))

    def test_large_leak_hits_the_maximum_evacuation_time(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'settings.configure', max_evacuation_s=300.0)
        r = g.call(g.A, 'part.connect', **dict(CHAMBER, leaks=[dict(leak_id='gross', helium_leak_rate_pa_m3_s=20.0)]))
        self.assertTrue(r['notes'])
        g.call(g.A, 'measure.start')
        g.step(320.0)
        self.assertIn('W76', g.read('alarms.active'))
        self.assertEqual(g.read('state.current'), 'standby')

    def test_spray_response_time_grows_with_volume_and_path(self):
        def t63(volume, path_len, diam):
            g = Rig()
            g.ready()
            g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=volume, leaks=[dict(leak_id='weld', helium_leak_rate_pa_m3_s=1e-8 * MB, path_length_m=path_len,
                                                                                         path_diameter_m=diam)]))
            g.call(g.A, 'measure.start')
            g.until(lambda: g.read('range.current') == 'ULTRA', 3000.0, 2.0)
            g.step(120.0)
            g.call(g.A, 'zero.set', enabled=True)
            g.call(g.A, 'spray.set', target='weld', on=True)
            final = 1e-8 * MB * rt.ENG['spray_he_fraction']
            t = g.until(lambda: (g.read('leak.display_pa_m3_s') or 0) > 0.63 * final, 2000.0, 0.5)
            g.step(600.0)
            return t, g.read('leak.display_pa_m3_s')
        t_small, v_small = t63(0.002, 0.05, 0.01)
        t_big, _ = t63(0.05, 0.05, 0.01)
        t_path, _ = t63(0.002, 3.0, 0.002)
        self.assertGreater(t_big, 3 * t_small)
        self.assertGreater(t_path, t_small + 5.0)
        self.assertAlmostEqual(v_small / (1e-8 * MB * rt.ENG['spray_he_fraction']), 1.0, delta=0.2)
        CHECKS.append(dict(id='response_time', status='passed', layer='process', t63_2l_s=t_small, t63_50l_s=t_big, t63_3m_path_s=t_path))

    def test_detection_limit_is_a_floor_not_the_reading_error(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, leaks=[]))
        g.call(g.A, 'measure.start')
        g.until(lambda: g.read('range.current') == 'ULTRA', 600.0, 1.0)
        g.step(1200.0)
        g.call(g.A, 'zero.set', enabled=True)
        vals = []
        for _ in range(40):
            g.step(1.0)
            vals.append(g.read('leak.display_pa_m3_s'))
        self.assertTrue(all(v >= rt.DISPLAY_LIMIT['ULTRA'] for v in vals))
        self.assertLess(max(vals), 5 * rt.RANGES['ULTRA']['limit_pa_m3_s'])
        big = Rig()
        big.ready()
        big.call(big.A, 'part.connect', **dict(CHAMBER, leaks=[dict(leak_id='w', helium_leak_rate_pa_m3_s=1e-6 * MB)]))
        big.call(big.A, 'measure.start')
        big.until(lambda: big.read('range.current') == 'ULTRA', 600.0, 1.0)
        big.call(big.A, 'spray.set', target='w', on=True)
        big.step(120.0)
        r = []
        for _ in range(20):
            big.step(1.0)
            r.append(big.read('leak.display_pa_m3_s'))
        mean = sum(r) / len(r)
        rel = math.sqrt(sum((x - mean) ** 2 for x in r) / len(r)) / mean
        self.assertLess(rel, 0.12)                                            # a large reading has a relative error far above the detection limit
        self.assertEqual(big.read('leak.status'), 'reject')

    def test_zero_izero_and_auto_zero(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'settings.configure', zero_mode='i_zero')
        g.call(g.A, 'part.connect', **CHAMBER)
        g.call(g.A, 'measure.start')
        g.until(lambda: g.read('range.current') == 'GROSS', 600.0, 0.5)
        g.step(0.5)
        g.call(g.A, 'zero.set', expect='SIGNAL_NOT_STABLE', enabled=True)       # background still falling fast
        g.until(lambda: g.read('signal.stable'), 1800.0, 2.0)
        g.call(g.A, 'zero.toggle')
        self.assertTrue(g.read('zero.enabled'))
        g.call(g.A, 'zero.set', enabled=False)
        self.assertFalse(g.read('zero.enabled'))
        h = Rig()
        h.ready()
        h.call(h.E, 'fault.inject', fault='helium_contamination', active=True)
        h.step(30.0)
        h.call(h.E, 'fault.inject', fault='helium_contamination', active=False)
        h.call(h.A, 'measure.start')
        self.assertGreater(h.read('zero.offset_pa_m3_s'), 0.0)                 # background suppression: internal background measured in standby

    def test_calibration_drift_and_test_leak(self):
        g = Rig(ambient_k=303.15, temperature_at_calibration_k=293.15)
        g.ready()
        self.assertNotIn('W80', g.read('alarms.active'))                      # calibration request is OFF by default (manual)
        g.call(g.A, 'settings.configure', calibration_request=True)
        self.assertIn('W80', g.read('alarms.active'))
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.002, leaks=[dict(leak_id='ref', helium_leak_rate_pa_m3_s=1e-7 * MB, path_length_m=0.0)]))
        g.call(g.A, 'measure.start')
        g.until(lambda: g.read('range.current') == 'ULTRA', 600.0, 1.0)
        g.step(300.0)
        g.call(g.A, 'zero.set', enabled=True)
        g.call(g.A, 'spray.set', target='ref', on=True)
        g.step(60.0)
        before = g.read('leak.display_pa_m3_s')
        true = 1e-7 * MB * rt.ENG['spray_he_fraction']
        self.assertGreater(abs(before / true - 1), 0.07)                     # 10 K away from the last calibration: about 10 % off
        g.call(g.A, 'spray.set', target='ref', on=False)
        g.step(60.0)
        r = g.call(g.A, 'calibration.start', kind='external', test_leak_pa_m3_s=1e-7 * MB * rt.ENG['spray_he_fraction'])
        g.call(g.A, 'spray.set', target='ref', on=True)
        g.step(r['duration_s'] + 30.0)
        self.assertEqual(g.read('state.current'), 'measure')
        g.step(30.0)
        self.assertAlmostEqual(g.read('leak.display_pa_m3_s') / true, 1.0, delta=0.12)
        self.assertNotIn('W80', g.read('alarms.active'))                      # calibrated at the current temperature
        e = Rig()
        e.ready()
        e.call(e.E, 'fault.inject', fault='internal_test_leak_empty', active=True)
        e.call(e.A, 'calibration.start', kind='internal')
        e.step(60.0)
        self.assertIn('W81', e.read('alarms.active'))
        self.assertEqual(e.read('calibration.factor'), 1.0)
        ok = Rig()
        ok.ready()
        ok.call(ok.A, 'calibration.start', kind='internal')
        ok.step(60.0)
        self.assertEqual(ok.read('state.current'), 'standby')
        self.assertAlmostEqual(ok.read('calibration.factor'), 1.0, delta=0.1)

    def test_seal_permeation_slow_signal(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.002, leaks=[dict(leak_id='oring', helium_leak_rate_pa_m3_s=2e-9 * MB, kind='seal_permeation')]))
        g.call(g.A, 'measure.start')
        g.until(lambda: g.read('range.current') == 'ULTRA', 600.0, 1.0)
        g.step(300.0)
        g.call(g.A, 'zero.set', enabled=True)
        g.call(g.A, 'spray.set', target='oring', on=True)
        g.step(20.0)
        early = g.read('leak.display_pa_m3_s')
        g.step(400.0)
        late = g.read('leak.display_pa_m3_s')
        self.assertGreater(late, 3 * early)                                    # permeation is slow (minutes), unlike a channel leak

    def test_sniffer_mode(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'sniffer.connect', connected=True)
        g.call(g.A, 'measure.start', expect='SNIFFER_IN_VACUUM_MODE')
        g.call(g.A, 'settings.configure', mode='sniff')
        g.call(g.A, 'measure.start')
        g.step(10.0)
        bg = g.read('leak.display_pa_m3_s')
        self.assertGreater(bg, 0.5 * rt.ENG['helium_in_air_mol_fraction'] * rt.ENG['sniff_flow_pa_m3_s'])     # air helium background
        g.call(g.A, 'zero.set', enabled=True)
        g.call(g.A, 'measure.stop')
        g.call(g.A, 'sniffer.connect', connected=False)
        g.call(g.A, 'part.connect', **dict(CHAMBER, helium_fill_pa=2e5, leaks=[dict(leak_id='weld', helium_leak_rate_pa_m3_s=1e-5 * MB)]))
        g.call(g.A, 'part.disconnect')
        h = Rig()
        h.ready()
        h.call(h.A, 'settings.configure', mode='sniff')
        h.call(h.A, 'sniffer.connect', connected=True)
        r = h.call(h.A, 'part.connect', part_id='vessel', volume_m3=0.01, surface_area_m2=0.0, material='stainless_unbaked', helium_fill_pa=2e5,
                   leaks=[dict(leak_id='weld', helium_leak_rate_pa_m3_s=1e-5 * MB)])                  # sniffer mode: declared as the sniffed object
        self.assertTrue(any('吸枪检查对象' in n for n in r['notes']))
        self.assertEqual(h.read('part.connected'), 'sniffer')
        h.call(h.A, 'part.connect', expect='INLET_OCCUPIED', **CHAMBER)
        h.call(h.A, 'measure.start')
        h.step(10.0)
        h.call(h.A, 'zero.set', enabled=True)                                   # subtract the air helium background
        h.call(h.A, 'sniffer.position', target='weld', distance_m=0.0)
        h.step(5.0)
        near = h.read('leak.display_pa_m3_s')
        h.call(h.A, 'sniffer.position', target='weld', distance_m=0.02)
        h.step(5.0)
        far = h.read('leak.display_pa_m3_s')
        self.assertGreater(near, 10 * far)
        h.call(h.A, 'part.disconnect')                                            # sniffed object moved away: only the background remains
        h.step(5.0)
        self.assertLess(h.read('leak.display_pa_m3_s'), far + 1e-15)
        self.assertAlmostEqual(near / (2e-5 * MB), 1.0, delta=0.3)              # 2 bar helium fill doubles the standard leak rate
        b = Rig()
        b.ready()
        b.call(b.A, 'settings.configure', mode='sniff')
        b.call(b.A, 'sniffer.connect', connected=True)
        b.call(b.E, 'fault.inject', fault='sniffer_capillary_blocked', active=True)
        b.call(b.A, 'measure.start')
        b.step(8.0)
        self.assertIn('W62', b.read('alarms.active'))                          # inlet below 0.1 mbar after the warning delay
        c = Rig()
        c.ready()
        c.call(c.A, 'settings.configure', mode='sniff')
        c.call(c.A, 'sniffer.connect', connected=True)
        c.call(c.A, 'measure.start')
        c.step(8.0)
        self.assertTrue(10.0 <= c.read('truth.gas')['total_pressure_pa'] <= 100.0)   # healthy line: inside the 0.1-1 mbar window
        self.assertNotIn('W62', c.read('alarms.active'))

    def test_conversion_vent_ledgers_rotation_and_faults(self):
        g = Rig()
        g.ready()
        r = g.call(g.A, 'leakrate.convert', gas='nitrogen', helium_leak_rate_pa_m3_s=1e-8)
        self.assertAlmostEqual(r['factor'], math.sqrt(rt.M_HE / rt.gases()['nitrogen']['molar_mass_kg_mol']), places=12)
        self.assertLess(r['equivalent_leak_rate_pa_m3_s'], 1e-8)
        self.assertGreaterEqual(len(rt.gases()), 50)
        g.call(g.A, 'display.rotate', angle_rad=0.7)
        g.call(g.A, 'part.connect', **CHAMBER)
        g.call(g.A, 'measure.start')
        g.step(120.0)
        g.call(g.A, 'spray.set', target='weld', on=True)
        g.step(30.0)
        g.call(g.A, 'measure.stop', hold_s=3.0)
        self.assertEqual(g.read('state.current'), 'vent')
        g.step(30.0)
        self.assertEqual(g.read('state.current'), 'standby')
        self.assertAlmostEqual(g.read('pressure.inlet_pa'), 101325.0, delta=0.05 * 101325.0)
        g.call(g.A, 'part.disconnect')
        _balance(self, g.ledger())
        d = g.call(g.A, 'datalog.read', last=10)
        self.assertGreaterEqual(len(d['records']), 10)
        g.call(g.E, 'fault.inject', fault='filament_1_failure', active=True)
        g.step(1.0)
        self.assertIn('W45', g.read('alarms.active'))
        g.call(g.A, 'power.toggle')
        self.assertFalse(g.read('power.on'))
        f = Rig()
        f.ready()
        f.call(f.A, 'part.connect', **CHAMBER)
        f.call(f.A, 'measure.start')
        f.step(200.0)
        f.call(f.A, 'calibration.start', kind='internal')
        f.step(100.0)
        self.assertIn(f.read('state.current'), ('measure', 'calibration'))
        f.call(f.E, 'truth.inspect')
        _balance(self, f.ledger())


class ReviewR1(unittest.TestCase):
    """Round-1 runtime review regressions."""
    def test_zero_follows_a_falling_signal(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.002, leaks=[dict(leak_id='w', helium_leak_rate_pa_m3_s=5e-10 * MB)]))
        g.call(g.A, 'measure.start')
        g.until(lambda: g.read('range.current') == 'ULTRA', 600.0, 1.0)
        g.step(20.0)
        g.call(g.A, 'zero.set', enabled=True)                                 # ZERO while the background is still falling
        g.step(600.0)
        self.assertEqual(g.read('leak.status'), 'pass')                         # no false reject later
        g.call(g.A, 'spray.set', target='w', on=True)
        g.step(30.0)
        self.assertGreater(g.read('leak.display_pa_m3_s'), 0.5 * 5e-10 * MB * rt.ENG['spray_he_fraction'])   # the sprayed leak is not masked

    def test_start_during_vent_and_machine_factor(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'settings.configure', machine_factor=0.37)
        g.call(g.A, 'measure.start')
        g.step(5.0)
        g.call(g.A, 'measure.stop', hold_s=3.0)
        self.assertEqual(g.read('state.current'), 'vent')
        g.call(g.A, 'measure.start')
        g.step(10.0)
        self.assertEqual(g.read('state.current'), 'measure')

    def test_two_stage_w76_and_huge_leaks_stable(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.0, surface_area_m2=0.0, leaks=[dict(leak_id=f'l{i}', helium_leak_rate_pa_m3_s=100.0) for i in range(12)]))
        g.call(g.A, 'measure.start')
        g.step(31.0)
        self.assertIn('W76', g.read('alarms.active'))                           # p1 never below 100 mbar within 30 s
        tg = g.read('truth.gas')
        self.assertTrue(all(math.isfinite(v) for v in (tg['total_pressure_pa'], tg['helium_partial_pa'])))
        L = g.ledger()
        _balance(self, L)

    def test_internal_calibration_with_part_and_timing(self):
        g = Rig()
        g.ready()
        r = g.call(g.A, 'calibration.start', kind='internal')
        t = g.until(lambda: g.read('state.current') == 'standby', 120.0, 0.5)
        self.assertAlmostEqual(t, r['duration_s'], delta=6.0)
        self.assertAlmostEqual(r['duration_s'], 25.0, delta=0.5)                # about 25 s including the recovery after the test leak closes
        h = Rig()
        h.ready()
        h.call(h.A, 'part.connect', **dict(CHAMBER, volume_m3=0.005, leaks=[]))
        h.call(h.A, 'measure.start')
        h.until(lambda: h.read('range.current') == 'ULTRA', 600.0, 1.0)
        h.step(60.0)
        h.call(h.A, 'calibration.start', kind='internal')
        h.step(300.0)
        self.assertNotIn('W81', h.read('alarms.active'))
        self.assertAlmostEqual(h.read('calibration.factor'), 1.0, delta=0.15)

    def test_range_does_not_flap(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.002, surface_area_m2=1.0, material='neoprene_unbaked', leaks=[]))
        g.call(g.A, 'measure.start')
        g.step(600.0)
        n = sum(e['code'].startswith('RANGE_') for e in g.call(g.A, 'events.read', since_s=0.0)['events'])
        self.assertLess(n, 25)                                                 # anti-hunting: at most one upgrade per minute


class ReviewR2(unittest.TestCase):
    """Round-2 review: calibration from vented standby, manual 6.4.5 alarm delay, anti-hunting reset, truth air inflow, recovery after calibration."""
    def test_calibration_with_part_from_vented_standby(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.005, leaks=[]))
        r = g.call(g.A, 'calibration.start', kind='internal')
        rem = []
        t = g.until(lambda: rem.append(g.read('state.remaining_s')) or g.read('state.current') == 'standby', 600.0, 0.5)
        self.assertTrue(all(x is not None and x > 0 for x in rem[:-1]), rem[:5])
        self.assertLess(abs(t - r['duration_s']), 0.35 * t, (t, r['duration_s']))
        self.assertNotIn('W81', g.read('alarms.active'))
        self.assertAlmostEqual(g.read('calibration.factor'), 1.0, delta=0.03)
        ref = Rig()
        ref.ready()
        ref.call(ref.A, 'part.connect', **dict(CHAMBER, volume_m3=0.005, leaks=[]))
        ref.call(ref.A, 'measure.start')
        ref.step(g.h.t - ref.h.t)
        bg, bg_ref = g.read('truth.detector')['internal_background_pa_m3_s'], ref.read('truth.detector')['internal_background_pa_m3_s']
        self.assertLess(bg, 1.2 * bg_ref, (bg, bg_ref))                       # no air helium counted above 15 mbar: background as after a normal START
        g.call(g.A, 'measure.start')
        g.step(300.0)
        self.assertEqual(g.read('leak.status'), 'pass')

    def test_alarm_sounds_once_below_trigger_and_partition_independent(self):
        part = dict(CHAMBER, volume_m3=0.0005, surface_area_m2=0.05, leaks=[dict(leak_id='weld', helium_leak_rate_pa_m3_s=1e-7 * MB)])
        res = []
        for chunks in ([0.5] * 40, [20.0]):
            g = Rig()
            g.ready()
            g.call(g.A, 'part.connect', **part)
            g.call(g.A, 'measure.start')
            for c in chunks:
                g.step(c)
            self.assertEqual(g.read('leak.status'), 'pass')
            g.call(g.A, 'spray.set', target='weld', on=True)
            g.step(4.0)
            res.append((g.read('leak.status'), g.read('alarm.audible')))
        self.assertEqual(res[0], ('reject', True))                             # 24 s after START: below trigger 1 earlier, so no alarm delay
        self.assertEqual(res[0], res[1])

    def test_new_start_is_not_held_by_an_old_downgrade(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.0005, surface_area_m2=0.05, leaks=[]))
        g.h.downgrade_t = g.h.t                                                # a downgrade just happened in an earlier measurement
        g.call(g.A, 'measure.start')
        t = g.until(lambda: g.read('range.current') == 'ULTRA', 60.0, 0.5)
        self.assertLess(t, 20.0)

    def test_truth_air_inflow_matches_the_gas_balance(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.002, surface_area_m2=0.0,
                                         leaks=[dict(leak_id='crack', helium_leak_rate_pa_m3_s=1e-2, path_length_m=0.5, path_diameter_m=0.002)]))
        g.call(g.A, 'measure.start')
        g.step(60.0)
        q = g.read('truth.leaks')['crack']['air_inflow_pa_m3_s']
        L0 = g.ledger()['gas_leak_in']
        g.step(2.0)
        rate = (g.ledger()['gas_leak_in'] - L0) / 2.0
        self.assertAlmostEqual(q / rate, 1.0, delta=0.02)

    def test_no_false_reject_right_after_calibration(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.0005, surface_area_m2=0.05, leaks=[]))
        g.call(g.A, 'measure.start')
        g.until(lambda: g.read('range.current') == 'ULTRA', 120.0, 0.5)
        g.step(30.0)
        g.call(g.A, 'calibration.start', kind='internal')
        g.until(lambda: g.read('state.current') == 'measure', 120.0, 0.5)
        st = []
        for _ in range(20):
            st.append(g.read('leak.status'))
            g.step(0.5)
        self.assertNotIn('reject', st)


class ReviewR3(unittest.TestCase):
    """Round-3 minors fixed directly: duration incl. range dwells, drift-corrected baseline, warning cleared by a new calibration, abort on re-pressurising."""
    def test_duration_estimate_and_warning_cleared(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.005, leaks=[]))
        r = g.call(g.A, 'calibration.start', kind='internal')
        t = g.until(lambda: g.read('state.current') == 'standby', 600.0, 0.5)
        self.assertLess(abs(t - r['duration_s']), 6.0, (t, r['duration_s']))
        g.h.warnings_latch = 'W76'                                              # an earlier aborted calibration
        g.call(g.A, 'calibration.start', kind='internal')
        self.assertNotIn('W76', g.read('alarms.active'))

    def test_no_false_reject_with_falling_background(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'settings.configure', trigger_1_pa_m3_s=2.3e-10)
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.01, leaks=[]))
        g.call(g.A, 'measure.start')
        g.until(lambda: g.read('range.current') == 'GROSS', 60.0, 0.5)
        g.call(g.A, 'calibration.start', kind='internal')
        g.until(lambda: g.read('state.current') == 'measure', 300.0, 0.5)
        st = []
        for _ in range(10):
            st.append(g.read('leak.status'))
            g.step(0.5)
        self.assertNotIn('reject', st)
        self.assertAlmostEqual(g.read('calibration.factor'), 1.0, delta=0.02)

    def test_repressurised_inlet_aborts_running_calibration(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'part.connect', **dict(CHAMBER, volume_m3=0.002, leaks=[]))
        g.call(g.A, 'measure.start')
        g.until(lambda: g.read('range.current') == 'ULTRA', 120.0, 0.5)
        f0 = g.read('calibration.factor')
        g.call(g.A, 'calibration.start', kind='internal')
        g.until(lambda: g.h.cal is not None and g.h.cal['stage'] == 'open', 120.0, 0.5)
        g.call(g.E, 'fault.inject', fault='scroll_pump_failure', active=True)
        g.call(g.E, 'part.connect', **dict(CHAMBER, part_id='chamber2', volume_m3=0.002, leaks=[dict(leak_id='big', helium_leak_rate_pa_m3_s=0.3)]), expect='PART_CONNECTED') if False else None
        g.h.p1 = 5000.0                                                         # the inlet re-pressurises (evaluator-only situation)
        g.step(1.0)
        self.assertIsNone(g.h.cal)
        self.assertEqual(g.read('calibration.factor'), f0)
        self.assertIn('CAL_ABORTED', [e['code'] for e in g.call(g.E, 'events.read', since_s=0.0)['events']])


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()
