"""tests for CHEM-200 inert_puresolvmd (Inert PureSolv MD 7 solvent purification system): python3 -m unittest tests/test_asset_inert_puresolvmd.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; gas manifold (50 psi supply, 70 psi relief) and vacuum manifold (6 Torr ultimate, 20 L/min); three evacuate /
refill cycles before dispensing (UCD) against dispensing into an air-filled or undried flask; laminar dispensing flow with the measured
viscosity (DMF slower than THF), the flask never filled above its brim, the 2/3 warning; the dispensing line hold-up blown into the flask on
refill and sucked into the pump when turned back through evacuate (UCD warning); column loading, breakthrough after the calibrated 800 L of
HPLC grade solvent, replacement; Q5 removes oxygen, alumina does not (Texas A&M); sparging (UCD 45 min for 15 L) and refilling with
air-saturated, stabilised or wet solvent; faults (pump, supply, clogged filter, joint leak, check valve, regulator creep up to the 60 psi keg
relief); per-channel solvent, water and flask-gas ledgers closing exactly."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import inert_puresolvmd as rt  # noqa: E402

INST = 'inert_puresolvmd_01'
PSI, INHG = rt.PSI, rt.INHG
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.InertPuresolvmdRuntime(instance)
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
        assert expect in [e['code'] for e in rt.export_inert_puresolvmd_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def truth(self, ch):
        return self.read('truth.channels')['channels'][ch - 1]

    def prime(self):
        self.call(self.A, 'gas.isolation', open=True)
        self.call(self.A, 'pump.set', on=True)
        self.step(30.0)

    def turn(self, ch, pos, direction='shortest'):
        return self.call(self.A, 'valve.turn', channel=ch, position=pos, direction=direction)

    def purge(self, ch, cycles=3):
        for _ in range(cycles):
            self.turn(ch, 'evacuate')
            self.step(30.0)
            self.turn(ch, 'refill', 'counterclockwise')                       # evacuate -> closed -> refill
            self.step(10.0)

    def dispense(self, ch, seconds, opening=1.0, evacuate_first=True):
        if evacuate_first:
            self.turn(ch, 'evacuate', 'clockwise')                            # refill -> dispense -> evacuate: flask still empty, line empty
            self.step(30.0)
        self.call(self.A, 'needle.set', channel=ch, opening=opening)
        self.turn(ch, 'dispense', 'counterclockwise')
        self.step(seconds)

    def finish(self, ch):
        self.turn(ch, 'refill', 'counterclockwise')                           # back-fill: the line hold-up is blown into the flask
        self.step(5.0)
        self.turn(ch, 'closed', 'counterclockwise')
        out = self.call(self.A, 'flask.detach', channel=ch)['flask']
        assert 'water_ppm' not in out and 'oxygen_ppm' not in out                 # not measured by the system: evaluator truth only
        return self.call(self.E, 'truth.inspect')['removed_flasks'][-1]

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
        out[k] = codes
    name = {'_cmd_' + c.replace('.', '_'): c for c in rt.CAPS}
    return {name[k]: v for k, v in out.items() if k in name}


def _balance(test, L, rig):
    for k, lk in enumerate(L['channels']):
        if rig.h.ch[k]['solvent'] is None:
            continue
        S = (lk['initial_solvent_kg'] + lk['added_solvent_kg'] - lk['removed_solvent_kg'] - lk['solvent_to_pump_kg'] - lk['sparge_solvent_kg'] - lk['drip_solvent_kg'])
        test.assertAlmostEqual(lk['solvent_in_system_kg'], S, delta=1e-9 * max(lk['initial_solvent_kg'], 1.0), msg=f'solvent ch{k + 1}')
        W = (lk['initial_water_kg'] + lk['added_water_kg'] + lk['water_from_air_kg'] + lk['wall_water_kg'] - lk['removed_water_kg'] - lk['water_to_pump_kg']
             - lk['water_vented_kg'] - lk['drip_water_kg'] - lk['column_water_removed_kg'])
        test.assertAlmostEqual(lk['water_in_system_kg'], W, delta=1e-12 + 1e-9 * lk['initial_water_kg'], msg=f'water ch{k + 1}')
        G = lk['air_in_mol'] + lk['flask_refill_mol'] - lk['flask_evac_mol'] - lk['flask_relief_mol'] - lk['flask_removed_mol'] - lk['gas_dissolved_mol']
        test.assertAlmostEqual(lk['flask_gas_mol'], G, delta=1e-12 + 1e-9 * (lk['air_in_mol'] + lk['flask_refill_mol']), msg=f'gas ch{k + 1}')


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_consistent(self):
        meta = rt.export_inert_puresolvmd_contract_metadata()
        slots = set(meta['state_slots'])
        self.assertEqual(slots, set(rt.SLOTS))
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            self.assertTrue({'INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'} <= {e['code'] for e in c['errors']}, k)
            for p in c['preconditions']:
                for s in p['state_ids']:
                    self.assertIn(s, slots, k)
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for name, p in meta['mechanisms']['solvent_purification_process']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_inert_puresolvmd_contract_metadata()
        for cap, codes in _raised_codes().items():
            declared = {e['code'] for e in meta['capabilities'][cap]['errors']}
            self.assertTrue(codes <= declared, (cap, sorted(codes - declared)))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            g.call(g.A, k, expect='UNSUPPORTED')

    def test_agent_truth_isolation(self):
        g = Rig()
        g.prime()
        self.assertFalse(any(k.startswith('truth.') for k in g.read(who=g.A)))
        for cap, a in (('truth.inspect', {}), ('ledger.read', {}), ('fault.inject', dict(fault='pump_failure', active=True)), ('sim.step', dict(dt_s=1.0)),
                       ('plant.configure', rt.PLANT_EXAMPLE)):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=a, request_id='x' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        g.call(g.E, 'fault.inject', fault='filter_clogged', active=True, channel=2)
        self.assertFalse(any(e['kind'] in ('fault', 'truth') for e in g.call(g.A, 'events.read', since_s=0.0)['events']))
        g.call(g.E, 'truth.inspect')

    def test_idempotent_request_ids(self):
        g = Rig()
        a = g.A.invoke(instance_id=INST, capability_id='pump.set', arguments=dict(on=True), request_id='same')
        b = g.A.invoke(instance_id=INST, capability_id='pump.set', arguments=dict(on=True), request_id='same')
        self.assertEqual(a, b)
        c = g.A.invoke(instance_id=INST, capability_id='pump.set', arguments=dict(on=False), request_id='same')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='i1'), Rig(instance='i2')
        a.call(a.A, 'pump.set', on=True)
        self.assertFalse(b.read('pump.on'))

    def test_step_partition_independence(self):
        runs = []
        for parts in ([400.0], [0.3, 7.7, 92.0, 300.0], [1.0] * 40 + [360.0]):
            g = Rig(seed=7)
            g.prime()
            g.call(g.A, 'flask.attach', channel=1, volume_m3=0.001, oven_dried=False)
            g.call(g.A, 'line.set', channel=3, keg_outlet=False)
            g.call(g.A, 'keg.sparge', channel=3, duration_s=200.0)
            g.call(g.E, 'fault.inject', fault='regulator_creep', active=True, channel=2)
            g.call(g.A, 'needle.set', channel=1, opening=0.3)
            g.turn(1, 'dispense')
            for p in parts:
                g.step(p)
            v = g.read()
            v.pop('clock.time_s')
            runs.append(json.dumps(v, sort_keys=True, default=str))
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[0], runs[2])

    def test_rejections_change_nothing(self):
        r = rt.InertPuresolvmdRuntime('fresh')
        r.register_principal('e', 'evaluator')
        r.register_principal('a', 'agent')
        self.assertEqual(r.session('a').invoke(instance_id='fresh', capability_id='pump.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g = Rig(solvents=['tetrahydrofuran', 'toluene', None, None, None, None, None])
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, solvents=['tetrahydrofuran', 'toluene', None, None, None, None, None], noise_seed=2))
        g.call(g.E, 'plant.configure', expect='SOLVENTS_FIXED', **rt.PLANT_EXAMPLE)
        g.call(g.A, 'valve.turn', expect='EMPTY_CHANNEL', channel=3, position='evacuate')
        g.call(g.A, 'flask.detach', expect='NO_FLASK', channel=1)
        g.call(g.A, 'flask.attach', channel=1, volume_m3=0.0005)
        g.call(g.A, 'flask.attach', expect='FLASK_ATTACHED', channel=1, volume_m3=0.0005)
        g.turn(1, 'refill')
        g.call(g.A, 'flask.detach', expect='VALVE_NOT_CLOSED', channel=1)
        g.call(g.A, 'keg.refill', expect='CABINET_CLOSED', channel=1, volume_m3=0.001)
        g.call(g.A, 'cabinet.door', side='left', angle_rad=1.5)
        g.call(g.A, 'keg.refill', expect='KEG_CONNECTED', channel=1, volume_m3=0.001)
        g.call(g.A, 'line.set', channel=1, keg_gas=False, keg_outlet=False)
        g.call(g.A, 'keg.refill', expect='KEG_OVERFILL', channel=1, volume_m3=0.010)
        g.call(g.A, 'keg.sparge', expect='NO_GAS', channel=1, duration_s=60.0)
        g.call(g.A, 'line.set', channel=1, keg_outlet=True)
        g.call(g.A, 'gas.isolation', open=True)
        g.call(g.A, 'keg.sparge', expect='KEG_CONNECTED', channel=1, duration_s=60.0)
        g.call(g.A, 'column.replace', expect='COLUMN_OPEN', channel=1)
        g.call(g.A, 'needle.set', expect='INVALID_ARGUMENT', channel=1, opening=1.5)
        g.call(g.A, 'valve.turn', expect='INVALID_ARGUMENT', channel=8, position='closed')
        g.call(g.E, 'fault.inject', expect='CHANNEL_REQUIRED', fault='filter_clogged', active=True)
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='alien', active=True)


class Physics(unittest.TestCase):
    def test_manifolds_and_gauges(self):
        g = Rig()
        self.assertEqual(g.read('gauge.pressure_pa'), 0.0)
        g.prime()
        self.assertAlmostEqual(g.read('gauge.pressure_pa'), 50.0 * PSI, delta=0.6 * PSI)                # building supply about 50 psi (UCD)
        self.assertAlmostEqual(g.read('gauge.pressure_angle_rad'), -rt.GAUGE_SWEEP * g.read('gauge.pressure_pa') / (100.0 * PSI), places=9)
        self.assertLess(g.read('gauge.vacuum_pa'), -29.0 * INHG)                                          # 6 Torr ultimate
        self.assertAlmostEqual(g.read('truth.manifold')['vacuum_pa'], rt.DOC['pump_ultimate_pa'], delta=1.0)
        g.call(g.A, 'pump.toggle')
        self.assertFalse(g.read('pump.on'))
        g.call(g.A, 'gas.isolation', open=False)
        self.assertEqual(g.read('gas.isolation_angle_rad'), 0.0)
        h = Rig(supply_gauge_pa=90.0 * PSI)
        h.prime()
        self.assertAlmostEqual(h.read('gauge.pressure_pa'), 70.0 * PSI, delta=0.6 * PSI)                # main relief 70 psi
        self.assertIn('MAIN_RELIEF', [e['code'] for e in h.call(h.A, 'events.read', since_s=0.0)['events']])

    def test_purged_flask_gives_dry_oxygen_free_solvent(self):
        g = Rig()
        g.prime()
        g.call(g.A, 'flask.attach', channel=3, volume_m3=0.0005, oven_dried=True)
        g.purge(3)
        air = g.truth(3)['flask']['gas_mol']
        self.assertLess(air['air'] + air['o2'], 1e-6)                                                      # three cycles (UCD)
        self.assertGreater(g.read('gauge.station3_pa'), 1.0 * PSI)                                        # refilled to the low-pressure regulator
        g.dispense(3, 45.0)
        self.assertGreater(g.read('flask.ch3')['liquid_m3'], 1e-4)
        r = g.finish(3)
        self.assertEqual(r['solvent'], 'toluene')
        self.assertAlmostEqual(r['water_ppm'], rt.ENG['water_floor_ppm'], delta=0.5)
        self.assertLess(r['oxygen_ppm'], 1.0)                                                             # Q5 column removes oxygen
        self.assertTrue(r['under_inert'])
        _balance(self, g.ledger(), g)

    def test_unpurged_or_undried_flask_contaminates(self):
        res = {}
        for name, purge, dried in (('purged', True, True), ('air', False, True), ('wet', True, False)):
            g = Rig()
            g.prime()
            g.call(g.A, 'flask.attach', channel=1, volume_m3=0.001, oven_dried=dried)
            if purge:
                g.purge(1)
                g.dispense(1, 30.0)
            else:
                g.call(g.A, 'needle.set', channel=1, opening=1.0)
                g.turn(1, 'dispense')                                                                       # straight into the air-filled flask
                g.step(30.0)
            res[name] = g.finish(1)
            _balance(self, g.ledger(), g)
        self.assertGreater(res['air']['water_ppm'], res['purged']['water_ppm'] + 5.0)
        self.assertGreater(res['air']['oxygen_ppm'], 5.0 * res['purged']['oxygen_ppm'])
        self.assertFalse(res['air']['under_inert'])
        self.assertGreater(res['wet']['water_ppm'], res['purged']['water_ppm'] + 20.0)                    # wall water is not removed by vacuum

    def test_flow_viscosity_brim_and_two_thirds_warning(self):
        g = Rig()
        g.prime()
        q = {}
        for ch in (1, 7):                                                                                   # THF 0.5 mPa s, DMF about 0.9 mPa s
            g.call(g.A, 'flask.attach', channel=ch, volume_m3=0.002)
            g.purge(ch)
            g.dispense(ch, 20.0, opening=0.5)
            q[ch] = g.read(f'flask.ch{ch}')['liquid_m3']
        mu = {ch: rt.SOLV[g.h.ch[ch - 1]['solvent']]['viscosity']['value'] for ch in (1, 7)}
        self.assertGreater(q[1], q[7])
        self.assertAlmostEqual(q[1] / q[7], mu[7] / mu[1] * 0.9, delta=0.35 * mu[7] / mu[1])               # laminar: Q ~ 1/mu (head and density differ)
        self.assertAlmostEqual(g.read('needle.ch1_angle_rad'), 0.5 * rt.NEEDLE_TURNS * 2 * math.pi, places=9)
        g.call(g.A, 'flask.attach', channel=2, volume_m3=0.00025)
        g.purge(2)
        g.dispense(2, 300.0)
        f = g.read('flask.ch2')
        self.assertLessEqual(f['liquid_m3'], f['volume_m3'] + 1e-12)                                       # a closed flask fills at most to its brim
        codes = [e['code'] for e in g.call(g.A, 'events.read', since_s=0.0)['events']]
        self.assertIn('FLASK_OVER_TWO_THIRDS', codes)
        self.assertIn('FLASK_PRESSURISED', codes)
        _balance(self, g.ledger(), g)

    def test_line_holdup_pump_and_drip(self):
        g = Rig()
        g.prime()
        g.call(g.A, 'flask.attach', channel=1, volume_m3=0.001)
        g.purge(1)
        g.dispense(1, 20.0)
        self.assertGreater(g.truth(1)['line_m3'], 0.0)
        g.turn(1, 'closed', 'clockwise')                                                                    # dispense -> evacuate -> closed: UCD warning
        self.assertEqual(g.truth(1)['line_m3'], 0.0)
        self.assertGreater(g.ledger()['channels'][0]['solvent_to_pump_kg'], 0.0)
        self.assertIn('SOLVENT_TO_VACUUM', [e['code'] for e in g.call(g.A, 'events.read', since_s=0.0)['events']])
        g.turn(1, 'dispense', 'counterclockwise')
        g.step(10.0)
        g.turn(1, 'closed', 'counterclockwise')                                                             # through refill: line blown into the flask
        self.assertEqual(g.truth(1)['line_m3'], 0.0)
        g.call(g.A, 'flask.detach', channel=1)
        _balance(self, g.ledger(), g)

    def test_column_breakthrough_and_replacement(self):
        g = Rig()
        g.prime()
        cap = g.truth(1)['column_water_capacity_kg']
        self.assertAlmostEqual(cap[0], rt.ENG['column_water_capacity_kg'], places=12)
        for i in range(2):                                                                                  # near the end of the 800 L (white box)
            g.h.ch[0]['col']['load'][i] = 0.99 * cap[i]
        g.h.led['ch'][0]['initial_water_kg'] += 0.99 * (cap[0] + cap[1])
        g.call(g.A, 'flask.attach', channel=1, volume_m3=0.002)
        g.purge(1)
        g.dispense(1, 60.0)
        r = g.finish(1)
        self.assertGreater(r['water_ppm'], 2.0 * rt.ENG['water_floor_ppm'])                                # broken through
        self.assertIn('COLUMN_BREAKTHROUGH', [e['kind'] == 'truth' and e['code'] for e in g.call(g.E, 'events.read', since_s=0.0)['events']])
        _balance(self, g.ledger(), g)
        g.call(g.A, 'line.set', channel=1, column_inlet=False, column_outlet=False)
        g.call(g.A, 'column.replace', channel=1)
        self.assertEqual(g.truth(1)['column_water_load_kg'], [0.0, 0.0])
        self.assertEqual(g.read('lines.valves')['1']['column_inlet'], False)
        g.call(g.A, 'line.set', channel=1, column_inlet=True, column_outlet=True)
        g.call(g.A, 'flask.attach', channel=1, volume_m3=0.002)
        g.purge(1)
        g.dispense(1, 60.0)
        self.assertAlmostEqual(g.finish(1)['water_ppm'], rt.ENG['water_floor_ppm'], delta=0.5)
        _balance(self, g.ledger(), g)

    def test_alumina_keeps_oxygen_sparging_and_refill(self):
        g = Rig()
        g.prime()
        o2_keg = g.truth(1)['keg_o2_ppm']
        g.call(g.A, 'flask.attach', channel=1, volume_m3=0.001)
        g.purge(1)
        g.dispense(1, 30.0)
        f, rho = g.truth(1)['flask'], rt.SOLV['tetrahydrofuran']['density']['value']
        o2_total = f['o2_ppm'] * 1e-6 * rho * f['liquid_m3'] + f['gas_mol']['o2'] * 0.0319988                 # dissolved plus partitioned into the inert headspace
        self.assertAlmostEqual(o2_total, o2_keg * 1e-6 * rho * f['liquid_m3'], delta=0.1 * o2_keg * 1e-6 * rho * f['liquid_m3'])   # alumina keeps O2 (Texas A&M)
        self.assertEqual(g.truth(1)['q5_o2_load_kg'], 0.0)
        r = g.finish(1)
        self.assertLess(r['oxygen_ppm'], o2_keg)
        g.call(g.A, 'cabinet.door', side='right', angle_rad=1.5)
        g.call(g.A, 'line.set', channel=1, keg_gas=False, keg_outlet=False)
        g.call(g.A, 'keg.refill', channel=1, volume_m3=0.004, water_ppm=300.0, stabiliser_ppm=250.0, grounded=False)
        t = g.truth(1)
        self.assertAlmostEqual(t['keg_o2_ppm'], rt.ENG['o2_air_saturated_ppm'], delta=1e-6)
        self.assertGreater(t['keg_water_ppm'], 150.0)
        codes = [e['code'] for e in g.call(g.A, 'events.read', since_s=0.0)['events']]
        self.assertIn('NOT_GROUNDED', codes)
        self.assertIn('STABILISER_IN_FEED', codes)
        v0 = t['keg_m3']
        g.call(g.A, 'keg.sparge', channel=1, duration_s=rt.DOC['ucd_sparge_s'])
        g.step(rt.DOC['ucd_sparge_s'] + 10.0)
        t = g.truth(1)
        self.assertLess(t['keg_o2_ppm'], 0.06 * rt.ENG['o2_air_saturated_ppm'])                           # 45 min (UCD): about 3 time constants
        self.assertLess(t['keg_m3'], v0)                                                                    # solvent vapour carried away
        self.assertFalse(t['sparging'])
        g.call(g.A, 'line.set', channel=1, keg_gas=True, keg_outlet=True)
        g.call(g.A, 'flask.attach', channel=1, volume_m3=0.002)
        g.purge(1)
        g.dispense(1, 60.0)
        g.finish(1)
        self.assertGreater(g.truth(1)['stabiliser_on_column_kg'], 0.0)
        self.assertEqual(g.read('reservoirs.recorded')['1']['added_m3'], 0.004)
        _balance(self, g.ledger(), g)

    def test_faults(self):
        g = Rig()
        g.call(g.E, 'fault.inject', fault='pump_failure', active=True)
        g.prime()
        self.assertEqual(g.read('gauge.vacuum_pa'), 0.0)
        g.call(g.E, 'fault.inject', fault='pump_failure', active=False)
        g.step(30.0)
        g.call(g.E, 'fault.inject', fault='check_valve_leak', active=True, channel=1)
        g.call(g.A, 'flask.attach', channel=1, volume_m3=0.0005)
        g.turn(1, 'evacuate')
        g.step(60.0)
        p_leak = g.truth(1)['flask']['pressure_pa']
        g.call(g.E, 'fault.inject', fault='check_valve_leak', active=False, channel=1)
        g.step(60.0)
        self.assertGreater(p_leak, 2.0 * g.truth(1)['flask']['pressure_pa'])                             # poorer vacuum in the flask
        g.turn(1, 'closed', 'counterclockwise')
        g.call(g.E, 'fault.inject', fault='flask_joint_leak', active=True, channel=1)
        n0 = g.truth(1)['flask']['gas_mol']['air']
        g.step(600.0)
        self.assertGreater(g.truth(1)['flask']['gas_mol']['air'], n0)
        g.call(g.E, 'fault.inject', fault='flask_joint_leak', active=False, channel=1)
        g.call(g.E, 'fault.inject', fault='gas_supply_empty', active=True)
        p_im = g.read('truth.manifold')['inert_pa']
        g.turn(1, 'refill', 'clockwise')
        g.step(30.0)
        self.assertLess(g.read('truth.manifold')['inert_pa'], p_im - 1000.0)                               # only the gas left in the manifold
        self.assertLess(g.read('gauge.pressure_pa'), 50.0 * PSI)
        g.call(g.E, 'fault.inject', fault='gas_supply_empty', active=False)
        g.step(1.0)
        q = {}
        for clog in (False, True):
            h = Rig()
            h.prime()
            if clog:
                h.call(h.E, 'fault.inject', fault='filter_clogged', active=True, channel=2)
            h.call(h.A, 'flask.attach', channel=2, volume_m3=0.002)
            h.purge(2)
            h.dispense(2, 20.0, opening=0.3)
            q[clog] = h.read('flask.ch2')['liquid_m3']
        self.assertLess(q[True], 0.8 * q[False])
        k = Rig(supply_gauge_pa=65.0 * PSI)
        k.prime()
        k.call(k.E, 'fault.inject', fault='regulator_creep', active=True, channel=4)
        k.step(86400.0)
        self.assertAlmostEqual(k.truth(4)['keg_head_pa'] - 101325.0, rt.DOC['ucd_gauges_pa']['keg'] + 86400.0 * rt.ENG['regulator_creep_pa_s'], delta=1.0)
        k.step(4 * 86400.0)
        self.assertAlmostEqual(k.truth(4)['keg_head_pa'] - 101325.0, rt.DOC['tamu_keg_relief_pa'], delta=1.0)   # 60 psi keg relief
        self.assertIn('KEG_RELIEF', [e['code'] for e in k.call(k.A, 'events.read', since_s=0.0)['events']])
        k.call(k.A, 'regulator.set', channel=4, keg_gauge_pa=10.0 * PSI, flask_gauge_pa=2.0 * PSI)
        self.assertEqual(k.read('regulators.settings')['4']['keg_gauge_pa'], 10.0 * PSI)
        _balance(self, g.ledger(), g)

    def test_cabinet_doors_and_quiet_stepping(self):
        g = Rig()
        g.call(g.A, 'cabinet.door', side='left', angle_rad=1.2)
        self.assertEqual(g.read('cabinet.left_angle_rad'), 1.2)
        g.call(g.A, 'cabinet.door', side='left', angle_rad=0.0)
        g.prime()
        n = g.h.tick
        g.step(30 * 86400.0)                                                                                # nothing evolves: the grid is skipped exactly
        self.assertEqual(g.h.tick - n, int(round(30 * 86400.0 / rt.DT)))
        _balance(self, g.ledger(), g)


    def test_review_r1_fixes(self):
        g = Rig()
        g.prime()
        g.call(g.A, 'flask.attach', channel=1, volume_m3=0.005)
        g.purge(1)
        g.call(g.A, 'line.set', channel=1, keg_gas=False)                                                  # keg cut off: headspace expands
        g.call(g.A, 'gas.isolation', open=False)
        g.call(g.E, 'fault.inject', fault='gas_supply_empty', active=True)
        p0 = g.truth(1)['keg_head_pa']
        g.dispense(1, 300.0)
        self.assertLess(g.truth(1)['keg_head_pa'], p0 - 0.2 * (p0 - 101325.0))
        g.turn(1, 'refill', 'counterclockwise')
        g.step(30.0)
        self.assertLess(g.read('gauge.pressure_pa'), 50.0 * PSI - 1.0)                                       # isolated manifold drawn down
        _balance(self, g.ledger(), g)
        h = Rig()
        h.prime()
        h.call(h.A, 'flask.attach', channel=1, volume_m3=5e-5)
        h.purge(1)
        h.dispense(1, 600.0)
        h.turn(1, 'refill', 'counterclockwise')
        f = h.read('flask.ch1')
        self.assertLessEqual(f['liquid_m3'], f['volume_m3'])                                               # the line hold-up does not overfill
        _balance(self, h.ledger(), h)
        b = Rig(ambient_k=308.15, pressure_pa=80000.0, solvents=['diethyl_ether', None, None, None, None, None, None])
        b.prime()
        b.call(b.A, 'line.set', channel=1, keg_outlet=False)
        v0 = b.truth(1)['keg_m3']
        b.call(b.A, 'keg.sparge', channel=1, duration_s=60.0)
        b.step(60.0)
        self.assertGreater(b.truth(1)['keg_m3'], v0 - 1e-4)                                               # bounded vapour carry-over
        self.assertIn('SPARGE_NEAR_BOILING', [e['code'] for e in b.call(b.A, 'events.read', since_s=0.0)['events']])
        o = Rig()
        o.prime()
        o.turn(2, 'evacuate')                                                                               # open joint: air into the vacuum
        o.step(10.0)
        self.assertGreater(o.read('truth.manifold')['vacuum_pa'], 10.0 * rt.DOC['pump_ultimate_pa'])
        o.turn(2, 'closed')
        o.call(o.A, 'needle.set', channel=2, opening=1.0)
        o.turn(2, 'dispense')                                                                               # no flask: solvent onto the drip tray
        o.step(10.0)
        o.turn(2, 'closed', 'counterclockwise')
        self.assertGreater(o.ledger()['channels'][1]['drip_solvent_kg'], 0.0)
        o.call(o.A, 'pump.set', on=False)
        o.call(o.A, 'flask.attach', channel=3, volume_m3=0.001)
        o.turn(3, 'evacuate')
        o.step(60.0)
        o.turn(3, 'closed', 'counterclockwise')
        o.turn(4, 'evacuate')                                                                               # vent the manifold through an open joint
        o.step(20.0)
        o.turn(4, 'closed')
        n_air = o.truth(3)['flask']['gas_mol']['air']
        o.turn(3, 'evacuate')                                                                               # back-flow from the manifold
        o.step(5.0)
        self.assertGreater(o.truth(3)['flask']['gas_mol']['air'], n_air)
        _balance(self, o.ledger(), o)


    def test_review_r2_isolated_manifold_conserves_gas(self):
        def inventory(g):
            RT = rt.R_GAS * g.h._T()
            return (g.h.p_im * rt.ENG['gas_manifold_volume_m3'] / RT + sum(c['keg']['n_gas'] for c in g.h.ch if c['solvent'])
                    + sum(lk['flask_refill_mol'] for lk in g.h.led['ch']))
        g = Rig(supply_gauge_pa=20.0 * PSI)
        g.prime()
        g.call(g.A, 'flask.attach', channel=1, volume_m3=0.005)
        g.call(g.A, 'gas.isolation', open=False)
        for ch in range(2, 8):
            g.call(g.A, 'line.set', channel=ch, keg_gas=False)
        n0 = inventory(g)
        g.call(g.A, 'regulator.set', channel=1, keg_gauge_pa=25.0 * PSI)                                  # set above what the manifold holds
        g.step(1.0)
        self.assertGreaterEqual(g.truth(1)['keg_head_pa'], 101325.0)
        g.turn(1, 'evacuate')
        g.step(30.0)
        g.call(g.A, 'needle.set', channel=1, opening=1.0)
        g.turn(1, 'dispense', 'counterclockwise')                                                         # headspace grows: gas from the manifold
        g.step(120.0)
        g.turn(1, 'refill', 'clockwise')
        g.step(30.0)
        self.assertAlmostEqual(inventory(g), n0, delta=1e-9 * n0)
        p_low = g.read('truth.manifold')['inert_pa']
        g.call(g.A, 'gas.isolation', open=True)                                                            # the supply refills the manifold
        g.turn(1, 'closed', 'counterclockwise')
        g.call(g.A, 'pump.set', on=False)
        g.step(600.0)
        self.assertGreater(g.read('truth.manifold')['inert_pa'], p_low)
        self.assertAlmostEqual(g.read('truth.manifold')['inert_pa'], 101325.0 + 20.0 * PSI, delta=1.0)
        _balance(self, g.ledger(), g)


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        missing = set(rt.CAPS) - set(rt.UNSUPPORTED) - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()
