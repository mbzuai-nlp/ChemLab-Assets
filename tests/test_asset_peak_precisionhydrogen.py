"""tests for CHEM-196 peak_precisionhydrogen (PEAK Scientific Precision Hydrogen Trace 500): python3 -m unittest tests/test_asset_peak_precisionhydrogen.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; Faraday production at the set pressure up to 500 sccm; power-up diagnostics and leak test (about 6 min), internal
leak -> leak test failure / standby leak check; demand above capacity or external leak -> LOW_PRESSURE shutdown; overpressure switch;
depressurise and restart; water: loading pump, bottle running dry -> WATER_LOW, refill, de-ionizer breakthrough -> WATER_QUALITY; dryer dew point
after the 24 h run-in; O2 purity and membrane crossover; hydrogen and water ledgers closing exactly."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import peak_precisionhydrogen as rt  # noqa: E402

INST = 'peak_precisionhydrogen_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
GC = dict(demand_sccm=150.0, min_pressure_pa=2.5e5)


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.PeakPrecisionhydrogenRuntime(instance)
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
        assert expect in [e['code'] for e in rt.export_peak_precisionhydrogen_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def ready(self):
        self.call(self.A, 'power.set', on=True)
        self.step(rt.DOC['leak_test_s'] + 10.0)

    def until(self, pred, limit, every=0.5):
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
    test.assertAlmostEqual(L['h2_initial_mol'] + L['h2_made_mol'],
                           L['h2_stored_mol'] + L['h2_delivered_mol'] + L['h2_external_leak_mol'] + L['h2_internal_leak_mol'] + L['h2_vented_mol'],
                           delta=1e-9 * (L['h2_initial_mol'] + L['h2_made_mol']))
    test.assertAlmostEqual(L['water_initial_kg'] + L['water_added_kg'], L['water_in_tank_kg'] + L['water_in_bottle_kg'] + L['water_electrolysed_kg'] + L['water_lost_kg'],
                           delta=1e-9 * (L['water_initial_kg'] + L['water_added_kg']))


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_consistent(self):
        meta = rt.export_peak_precisionhydrogen_contract_metadata()
        slots = set(meta['state_slots'])
        self.assertEqual(slots, set(rt.SLOTS))
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            self.assertTrue({'INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'} <= {e['code'] for e in c['errors']}, k)
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for name, p in meta['mechanisms']['pem_hydrogen_generation']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_peak_precisionhydrogen_contract_metadata()
        for cap, codes in _raised_codes().items():
            declared = {e['code'] for e in meta['capabilities'][cap]['errors']}
            self.assertTrue(codes <= declared, (cap, sorted(codes - declared)))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            g.call(g.A, k, expect='UNSUPPORTED')

    def test_agent_truth_isolation(self):
        g = Rig()
        g.ready()
        self.assertFalse(any(k.startswith('truth.') for k in g.read(who=g.A)))
        r = g.A.invoke(instance_id=INST, capability_id='truth.inspect', arguments={}, request_id='x')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = g.A.invoke(instance_id=INST, capability_id='consumer.set', arguments=GC, request_id='y')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        g.call(g.E, 'fault.inject', fault='membrane_crossover', active=True)
        self.assertFalse(any(e['kind'] in ('fault', 'truth') for e in g.call(g.A, 'events.read', since_s=0.0)['events']))

    def test_idempotent_request_ids(self):
        g = Rig()
        a = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=True), request_id='same')
        b = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=True), request_id='same')
        self.assertEqual(a, b)
        c = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=False), request_id='same')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='i1'), Rig(instance='i2')
        a.ready()
        self.assertFalse(b.read('power.on'))

    def test_step_partition_independence(self):
        runs = []
        for parts in ([1200.0], [0.3, 7.7, 400.0, 792.0], [0.5] * 40 + [1180.0]):
            g = Rig(seed=7)
            g.call(g.E, 'consumer.set', **GC)
            g.call(g.A, 'power.set', on=True)
            for p in parts:
                g.step(p)
            v = g.read()
            v.pop('clock.time_s')
            runs.append(json.dumps(v, sort_keys=True, default=str))
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[0], runs[2])

    def test_rejections_change_nothing(self):
        g = Rig()
        r = rt.PeakPrecisionhydrogenRuntime('fresh')
        r.register_principal('e', 'evaluator')
        self.assertEqual(r.session('e').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=2))
        g.call(g.A, 'cycle.start', expect='POWER_OFF')
        g.call(g.A, 'settings.configure', expect='POWER_OFF', pressure_set_pa=4e5)
        g.call(g.A, 'water.refill', expect='BOTTLE_OVERFLOW', volume_m3=0.002)
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'cycle.start', expect='DIAGNOSTICS_RUNNING')
        g.call(g.A, 'output.depressurise', expect='DIAGNOSTICS_RUNNING')
        g.call(g.A, 'alarm.mute', expect='NO_ALARM')
        g.call(g.A, 'deioniser.replace', expect='POWER_ON')
        g.call(g.A, 'settings.configure', expect='INVALID_ARGUMENT', pressure_set_pa=8e5)
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='alien', active=True)


class Physics(unittest.TestCase):
    def test_diagnostics_start_and_faraday(self):
        g = Rig()
        g.call(g.A, 'power.set', on=True)
        g.step(1.0)
        self.assertEqual(g.read('state.current'), 'diagnostics')
        self.assertIsNotNone(g.read('state.remaining_s'))
        t = g.until(lambda: g.read('state.current') == 'standby', 900.0, 1.0)
        self.assertAlmostEqual(t, 360.0, delta=15.0)
        self.assertTrue(g.read('indicator.ok'))
        g.call(g.E, 'consumer.set', **GC)
        g.call(g.A, 'cycle.toggle')
        g.step(900.0)
        tg = g.read('truth.gas')
        self.assertAlmostEqual(tg['delivered_sccm'], 150.0, delta=0.5)
        self.assertAlmostEqual(g.read('flow.percent'), 30.0, delta=0.5)
        cell = g.read('truth.cell')
        self.assertAlmostEqual(cell['current_a'], 2 * rt.F_CONST * (1 + rt.ENG['psa_purge_fraction']) * tg['product_mol_s'], delta=1e-9)
        self.assertAlmostEqual(g.read('pressure.delivery_pa'), g.read('pressure.set_pa'), delta=0.3e5)
        g.call(g.A, 'settings.configure', units='bar')
        self.assertAlmostEqual(g.read('display.pressure'), round(g.read('pressure.delivery_pa') / 1e5, 1), delta=0.11)
        g.call(g.A, 'cycle.stop')
        self.assertEqual(g.read('state.current'), 'standby')
        _balance(self, g.ledger())

    def test_internal_leak_fails_leak_test_and_standby_check(self):
        g = Rig()
        g.call(g.E, 'fault.inject', fault='internal_leak', active=True)
        g.ready()
        self.assertEqual(g.read('state.current'), 'fault')
        self.assertIn('LEAK_TEST_FAILED', g.read('alarms.active'))
        self.assertTrue(g.read('indicator.fault'))
        self.assertTrue(g.read('alarm.audible'))
        g.call(g.A, 'alarm.mute')
        self.assertFalse(g.read('alarm.audible'))
        g.call(g.A, 'cycle.start', expect='FAULT_ACTIVE')
        h = Rig()
        h.ready()
        h.step(300.0)
        h.call(h.E, 'fault.inject', fault='internal_leak', active=True)
        h.step(300.0)
        self.assertIn('INTERNAL_LEAK', h.read('alarms.active'))
        _balance(self, h.ledger())

    def test_low_pressure_shutdown_and_overpressure(self):
        g = Rig()
        g.ready()
        g.call(g.E, 'consumer.set', demand_sccm=800.0, min_pressure_pa=2e5)
        g.call(g.A, 'cycle.start')
        g.step(1500.0)
        self.assertIn('LOW_PRESSURE', g.read('alarms.active'))
        self.assertEqual(g.read('truth.gas')['product_mol_s'], 0.0)
        g.call(g.A, 'output.depressurise')
        g.step(60.0)
        self.assertTrue(g.read('indicator.depressurised'))
        self.assertLess(g.read('pressure.delivery_pa'), 0.2e5)
        g.call(g.E, 'consumer.set', demand_sccm=100.0, min_pressure_pa=2e5, external_leak_sccm_at_5bar=0.0)
        g.call(g.A, 'cycle.start')
        g.step(900.0)
        self.assertEqual(g.read('state.current'), 'running')
        o = Rig()
        o.ready()
        o.call(o.A, 'settings.configure', pressure_set_pa=6.89e5)
        o.call(o.E, 'fault.inject', fault='pressure_sensor_drift', active=True)
        o.step(900.0)
        self.assertIn('OVERPRESSURE', o.read('alarms.active'))
        _balance(self, g.ledger())

    def test_water_loading_refill_and_deioniser(self):
        g = Rig(bottle_water_m3=0.0002)
        g.ready()
        g.step(600.0)                                                             # stand-by reaches the set pressure first
        g.call(g.E, 'consumer.set', demand_sccm=450.0, min_pressure_pa=2e5)
        g.call(g.A, 'cycle.start')
        g.step(3 * 86400.0)
        self.assertIn('WATER_LOW', g.read('alarms.active'))
        self.assertTrue(g.read('indicator.water'))
        self.assertEqual(g.read('truth.gas')['product_mol_s'], 0.0)
        g.call(g.A, 'water.refill', volume_m3=0.003, conductivity_s_m=5e-5)
        g.step(600.0)
        self.assertNotIn('WATER_LOW', g.read('alarms.active'))
        self.assertGreater(g.read('truth.gas')['product_mol_s'], 0.0)
        _balance(self, g.ledger())
        d = Rig(feed_conductivity_s_m=5e-4, bottle_volume_m3=0.02, bottle_water_m3=0.02)
        d.ready()
        d.step(600.0)
        d.call(d.E, 'consumer.set', demand_sccm=450.0, min_pressure_pa=2e5)
        d.call(d.A, 'cycle.start')
        for _ in range(12):
            d.step(5 * 86400.0)
            if 'WATER_QUALITY' in d.read('alarms.active'):
                break
            d.call(d.A, 'water.refill', volume_m3=0.02 - d.read('truth.water')['bottle_m3'])
        self.assertIn('WATER_QUALITY', d.read('alarms.active'))
        d.call(d.A, 'power.toggle')
        d.call(d.A, 'deioniser.replace')
        d.call(d.A, 'power.set', on=True)
        d.step(rt.DOC['leak_test_s'] + 60.0)
        self.assertNotIn('WATER_QUALITY', d.read('alarms.active'))

    def test_dryer_run_in_and_purity(self):
        g = Rig()
        g.ready()
        d0 = g.read('truth.gas')['dew_point_k']
        g.step(24 * 3600.0)
        tg = g.read('truth.gas')
        self.assertLess(tg['dew_point_k'], 273.15 - 72.0)
        self.assertGreater(d0, tg['dew_point_k'] + 40.0)
        self.assertGreaterEqual(tg['purity'], rt.DOC['purity'])
        g.call(g.E, 'fault.inject', fault='membrane_crossover', active=True)
        self.assertLess(g.call(g.E, 'truth.inspect')['gas']['purity'], rt.DOC['purity'])


class ReviewR1(unittest.TestCase):
    """Round-1 review fixes: no LOW_PRESSURE while still pressurising, leak-test timing after a long water check, tank conductivity with its own
    de-ioniser loop, PI pressure control, CELL_FAULT latches, mute only for sounding alarms, bottle re-declaration."""
    def test_no_low_pressure_while_still_rising(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'output.depressurise')
        g.step(60.0)
        g.call(g.A, 'settings.configure', pressure_set_pa=6.89e5)
        g.call(g.E, 'consumer.set', demand_sccm=400.0, min_pressure_pa=1e5)
        g.call(g.A, 'cycle.start')
        g.step(1500.0)
        self.assertNotIn('LOW_PRESSURE', g.read('alarms.active'))
        self.assertEqual(g.read('state.current'), 'running')

    def test_long_water_check_then_leak_test_passes(self):
        g = Rig(bottle_water_m3=0.0)
        g.h.tank = 0.2 * rt.ENG['tank_volume_m3']
        g.call(g.A, 'power.set', on=True)
        g.step(1000.0)
        n_low = sum(e['code'] == 'WATER_LOW' for e in g.call(g.A, 'events.read', since_s=0.0)['events'])
        self.assertEqual(n_low, 1)
        g.call(g.A, 'water.refill', volume_m3=0.003)
        g.step(rt.DOC['leak_test_s'] + 120.0)
        self.assertEqual(g.read('state.current'), 'standby', g.read('alarms.active'))

    def test_refill_does_not_change_the_cell_water_at_once(self):
        g = Rig(feed_conductivity_s_m=5e-4)
        g.ready()
        k0 = g.read('truth.water')['cell_water_conductivity_s_m']
        g.call(g.A, 'water.refill', volume_m3=0.0005, conductivity_s_m=5e-6)
        self.assertEqual(g.read('truth.water')['cell_water_conductivity_s_m'], k0)

    def test_pi_control_holds_the_set_pressure_under_load(self):
        g = Rig()
        g.ready()
        g.step(600.0)
        g.call(g.E, 'consumer.set', demand_sccm=490.0, min_pressure_pa=2e5)
        g.call(g.A, 'cycle.start')
        g.step(1800.0)
        self.assertAlmostEqual(g.read('pressure.delivery_pa'), g.read('pressure.set_pa'), delta=0.03e5)

    def test_cell_fault_latches_and_mute_needs_a_sounding_alarm(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'alarm.mute', expect='NO_ALARM')
        g.call(g.E, 'fault.inject', fault='cell_failure', active=True)
        g.step(1.0)
        self.assertEqual(g.read('state.current'), 'fault')
        g.call(g.A, 'cycle.start', expect='FAULT_ACTIVE')
        g.call(g.A, 'alarm.mute')
        g.call(g.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, bottle_volume_m3=0.001), expect='BOTTLE_OVERFLOW')


class ReviewR2(unittest.TestCase):
    """Round-2 minors fixed directly: anti-windup (no overshoot above 6.9 bar), LOW_PRESSURE only when the pressure cannot recover, cell failure at
    power-up reported as CELL_FAULT, silent notices do not cancel the mute."""
    def test_no_overshoot_from_integral_windup(self):
        g = Rig()
        g.ready()
        g.call(g.A, 'output.depressurise')
        g.step(60.0)
        g.call(g.A, 'settings.configure', pressure_set_pa=6.89e5)
        g.call(g.E, 'consumer.set', demand_sccm=0.0, min_pressure_pa=0.0)
        g.call(g.A, 'cycle.start')
        peak = 0.0
        for _ in range(60):
            g.step(60.0)
            peak = max(peak, g.read('truth.gas')['internal_pressure_abs_pa'] - rt.PLANT_EXAMPLE['pressure_pa'])
        self.assertLess(peak, rt.DOC['max_pressure_pa'] + 3e3, peak)

    def test_497_sccm_held_when_pressurised_but_not_from_empty(self):
        g = Rig()
        g.ready()
        g.step(600.0)                                                            # stand-by at the set pressure
        g.call(g.E, 'consumer.set', demand_sccm=497.0, min_pressure_pa=1e5)
        g.call(g.A, 'cycle.start')
        g.step(2 * 3600.0)
        self.assertNotIn('LOW_PRESSURE', g.read('alarms.active'))
        h = Rig()
        h.ready()
        h.call(h.A, 'output.depressurise')
        h.step(60.0)
        h.call(h.E, 'consumer.set', demand_sccm=497.0, min_pressure_pa=1e5)
        h.call(h.A, 'cycle.start')
        h.step(3600.0)
        self.assertIn('LOW_PRESSURE', h.read('alarms.active'))                  # 3 sccm net: hours to reach the set pressure

    def test_cell_failure_at_power_up_and_silent_notice_keeps_mute(self):
        g = Rig()
        g.call(g.E, 'fault.inject', fault='cell_failure', active=True)
        g.call(g.A, 'power.set', on=True)
        g.step(rt.DOC['leak_test_s'] + 30.0)
        self.assertIn('CELL_FAULT', g.read('alarms.active'))
        self.assertNotIn('LEAK_TEST_FAILED', g.read('alarms.active'))
        g.call(g.A, 'alarm.mute')
        g.h.di_age = rt.DOC['deioniser_interval_s'] + 1.0                       # a silent notice appears
        g.step(1.0)
        self.assertFalse(g.read('alarm.audible'))


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()
