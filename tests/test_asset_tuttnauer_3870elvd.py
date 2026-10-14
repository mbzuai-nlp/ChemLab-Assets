"""tests for CHEM-192 tuttnauer_3870elvd (Tuttnauer 3870ELV-D vertical laboratory autoclave): python3 -m unittest tests/test_asset_tuttnauer_3870elvd.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; IAPWS-IF97 saturation pressure (Table 35 verification values) and the latent heat; liquid cycle with two 700 ml
bottles (heating time against the same-platform printout, hold at the set point, loads lag the chamber: bottle F0 below the chamber F0, biological
indicator log reduction); 134 C solids cycle (pressure at the hold); liquid + cooling cycle (compressed air prevents boiling, ends at 90 C);
liquids under fast exhaust boil over, sealed bottles burst, non-autoclavable plastics deform; lid interlock; residual air (stuck air valve)
stops the heating at the pressure limit (Low Heat); no water supply; heater fault; door gasket leak; power loss; 200-record ring; program
editing limits (999 min extended time, 105-138 C); panel keys; water and load mass ledgers."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import tuttnauer_3870elvd as rt  # noqa: E402

INST = 'tuttnauer_3870elvd_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
BI = 'geobacillus_stearothermophilus'


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, ambient=295.15, patm=101325.0, water=True, air=True, cooling=True, instance=INST):
        self.h = rt.TuttnauerElvdRuntime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A = self.h.session('agent')
        self.E = self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', ambient_k=ambient, atmospheric_pressure_pa=patm, water_supply=water, compressed_air=air, cooling_water=cooling, noise_seed=seed)

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
        assert expect in [e['code'] for e in rt.export_tuttnauer_3870elvd_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def on(self):
        self.call(self.A, 'power.set', on=True)

    def open_lid(self):
        self.call(self.A, 'lid.set', open=True)
        self.step(3.0)

    def close_lid(self):
        self.call(self.A, 'lid.set', open=False)
        self.step(3.0)

    def bottles(self, n=2, mass=0.7, closure='loose', liquid='water', material='borosilicate_glass', bi=True):
        for i in range(n):
            args = dict(load_id=f'b{i + 1}', kind='liquid', mass_kg=mass, liquid=liquid, container_volume_m3=1e-3, material=material, closure=closure)
            if bi:
                args.update(organism=BI, log10_n0=6.0)
            self.call(self.A, 'load.add', **args)

    def run(self, program, limit=4 * 3600.0, every=10.0):
        self.call(self.A, 'program.select', program=program)
        self.call(self.A, 'cycle.start')
        t0, marks = self.h.t, {}
        while self.h.t - t0 < limit:
            self.step(every)
            st = self.read('cycle.status')['stage']
            marks.setdefault(st, self.h.t - t0)
            if st in ('complete', 'failed'):
                break
        return marks

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


def _water_balance(test, led):
    out = led['steam_vented_kg'] + led['flash_vented_kg'] + led['water_drained_kg'] + led['leak_kg'] + led['water_in_chamber_kg']
    test.assertAlmostEqual(led['water_supplied_kg'], out, delta=1e-9 + 1e-9 * led['water_supplied_kg'], msg=led)
    loads = led['load_removed_kg'] + led['load_mass_in_chamber_kg'] + led['load_boilover_kg'] + led['load_burst_kg']
    test.assertAlmostEqual(led['load_added_kg'], loads, delta=1e-9, msg=led)


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_and_mirrors_consistent(self):
        meta = rt.export_tuttnauer_3870elvd_contract_metadata()
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
            self.assertIn(c['kind'], ('query', 'device_command', 'robot_skill', 'simulation', 'analysis', 'configuration'))
            self.assertIn(c['model_effects']['mode'], ('none', 'direct', 'coupled', 'host_required'))
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for name, p in meta['mechanisms']['steam_sterilization_process']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_tuttnauer_3870elvd_contract_metadata()
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
            g.A.read(instance_id=INST, state_ids=['truth.loads'])
        for cap, args in (('truth.inspect', {}), ('sim.step', dict(dt_s=1.0)), ('ledger.read', {}), ('fault.inject', dict(fault='heater_failure', active=True))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x-' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED', cap)
        g.call(g.E, 'fault.inject', fault='door_gasket_leak', active=True)
        self.assertFalse(any(e['kind'] in ('fault', 'truth') for e in g.call(g.A, 'events.read', since_s=0.0)['events']))

    def test_idempotent_request_ids(self):
        g = Rig()
        g.on()
        args = dict(program='solids_134')
        r1 = g.A.invoke(instance_id=INST, capability_id='program.select', arguments=args, request_id='same')
        snap = g.h.read('evaluator')
        r2 = g.A.invoke(instance_id=INST, capability_id='program.select', arguments=args, request_id='same')
        self.assertEqual(r1, r2)
        self.assertEqual(json.dumps(snap, sort_keys=True, default=str), json.dumps(g.h.read('evaluator'), sort_keys=True, default=str))
        r3 = g.A.invoke(instance_id=INST, capability_id='program.select', arguments=dict(program='solids_121'), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='t_a'), Rig(instance='t_b', ambient=298.15)
        a.call(a.A, 'power.set', on=True)
        self.assertFalse(b.read('power.on'))
        r = a.A.invoke(instance_id='t_b', capability_id='power.set', arguments=dict(on=True), request_id='z')
        self.assertEqual(r['error']['code'], 'UNKNOWN_INSTANCE')

    def test_step_partition_independence(self):
        def run(steps):
            g = Rig(seed=5)
            g.on()
            g.open_lid()
            g.bottles(1)
            g.close_lid()
            g.call(g.A, 'program.select', program='liquids_121')
            g.call(g.A, 'cycle.start')
            for s in steps:
                g.step(s)
            r = g.read()
            return json.dumps({k: r[k] for k in r if k.startswith(('truth.', 'chamber.', 'cycle.'))}, sort_keys=True), json.dumps(g.ledger(), sort_keys=True)
        a = run([1500.0])
        b = run([0.05] * 400 + [0.37, 13.11, 1466.52])
        c = run([30.0] * 50)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_rejections_change_nothing(self):
        g = Rig()
        r = rt.TuttnauerElvdRuntime('fresh')
        r.register_principal('e', 'evaluator')
        self.assertEqual(r.session('e').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', ambient_k=295.15, atmospheric_pressure_pa=101325.0, water_supply=True, compressed_air=True,
               cooling_water=True, noise_seed=2)
        g.call(g.A, 'cycle.start', expect='POWER_OFF')
        g.call(g.A, 'records.read', expect='POWER_OFF')
        g.call(g.A, 'load.add', expect='LID_CLOSED', load_id='x', kind='solid', mass_kg=1.0, material='stainless_316')
        g.on()
        g.open_lid()
        g.call(g.A, 'cycle.start', expect='LID_OPEN')
        g.call(g.A, 'load.add', expect='MISSING_SETTING', load_id='x', kind='liquid', mass_kg=0.5, material='pp')
        g.call(g.A, 'load.add', expect='UNKNOWN_LIQUID', load_id='x', kind='liquid', mass_kg=0.5, material='pp', liquid='unobtainium', container_volume_m3=1e-3)
        g.call(g.A, 'load.add', expect='MISSING_SETTING', load_id='x', kind='solid', mass_kg=0.5, material='pp', organism=BI)
        g.call(g.A, 'load.add', load_id='w1', kind='liquid', mass_kg=9.0, material='borosilicate_glass', liquid='water', container_volume_m3=0.01)
        g.call(g.A, 'load.add', load_id='w2', kind='liquid', mass_kg=9.0, material='borosilicate_glass', liquid='water', container_volume_m3=0.009)
        g.call(g.A, 'load.add', expect='OVERLOAD', load_id='x', kind='liquid', mass_kg=2.0, material='pp', liquid='water', container_volume_m3=0.002)
        g.call(g.A, 'load.remove', load_id='w1')
        g.call(g.A, 'load.remove', load_id='w2')
        g.call(g.A, 'load.add', load_id='x', kind='solid', mass_kg=1.0, material='stainless_316')
        g.call(g.A, 'load.add', expect='LOAD_EXISTS', load_id='x', kind='solid', mass_kg=1.0, material='stainless_316')
        g.call(g.A, 'load.remove', expect='UNKNOWN_LOAD', load_id='y')
        g.call(g.A, 'cycle.stop', expect='NO_CYCLE')
        g.call(g.A, 'program.configure', expect='TIME_OUT_OF_RANGE', program='custom_1', time_s=120 * 60.0)
        g.call(g.A, 'program.configure', expect='INVALID_ARGUMENT', program='custom_1', temperature_k=420.0)
        g.call(g.A, 'program.configure', expect='INVALID_ARGUMENT', program='liquids_121', time_s=600.0)
        g.close_lid()
        g.call(g.A, 'cycle.start')
        g.step(60.0)
        g.call(g.A, 'lid.set', expect='LID_LOCKED', open=True)
        g.call(g.A, 'program.select', expect='CYCLE_RUNNING', program='solids_121')
        g.call(g.A, 'cycle.start', expect='CYCLE_RUNNING')
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='alien', active=True)
        h = Rig(water=False, air=False, cooling=False)
        h.on()
        h.call(h.A, 'cycle.start', expect='NO_WATER_SUPPLY')
        h.call(h.A, 'program.configure', expect='NO_COOLING_UTILITIES', program='custom_2', exhaust='cooling')


class Steam(unittest.TestCase):
    def test_iapws_saturation_and_latent_heat(self):
        for v in rt._lib('steam')['saturation']['verification']:
            self.assertAlmostEqual(rt.psat_water(v['T_K']) / v['ps_Pa'], 1.0, delta=1e-8)
        self.assertAlmostEqual(rt.tsat_water(101325.0), 373.124, delta=0.01)
        self.assertAlmostEqual(rt.psat_water(394.25) / 1e3, 205.0, delta=1.0)                  # 121.1 C (the manual's 204-205 kPa)
        for T, h_ref in ((373.15, 2256.5e3), (394.25, 2199.0e3), (407.15, 2162.0e3)):          # steam-table latent heats
            self.assertLess(abs(rt.hfg_water(T) / h_ref - 1.0), 0.035, T)
        CHECKS.append(dict(id='iapws_if97_table35_and_latent_heat', status='passed', layer='process'))


class Cycles(unittest.TestCase):
    def test_liquid_cycle_two_700ml_bottles(self):
        g = Rig()
        g.on()
        g.open_lid()
        g.bottles(2)
        g.close_lid()
        marks = g.run('liquids_121')
        st = g.read('cycle.status')
        self.assertEqual(st['result'], 'CYCLE ENDED')
        self.assertTrue(8 * 60 < marks['sterilization'] < 25 * 60, marks)                  # same-platform printout: sterilization from 14:51
        rec = g.call(g.A, 'records.read', last=1)['records'][0]
        self.assertGreaterEqual(rec['hold_min_k'], 394.15 - 0.5)
        self.assertLess(rec['hold_max_k'], 394.15 + rt.DOC['high_temperature_k'])
        self.assertGreaterEqual(rec['hold_s'], 1200.0)
        tl = g.read('truth.loads')
        self.assertLess(tl['b1']['F0_s'], rec['chamber_F0_s'])                             # the load centre lags the chamber
        self.assertGreater(6.0 - tl['b1']['log10_survivors'], 4.0)
        self.assertLess(tl['b1']['lost_kg'], 0.005)                                         # slow exhaust: (almost) no boil-over
        _water_balance(self, g.ledger())
        CHECKS.append(dict(id='liquid_cycle_two_bottles', status='passed', layer='process', marks_s=marks, bottle_F0_s=tl['b1']['F0_s'], chamber_F0_s=rec['chamber_F0_s']))

    def test_solids_134_hold_pressure(self):
        g = Rig()
        g.on()
        g.open_lid()
        g.call(g.A, 'load.add', load_id='tray', kind='solid', mass_kg=3.0, material='stainless_316', organism=BI, log10_n0=6.0)
        g.close_lid()
        g.call(g.A, 'program.select', program='solids_134')
        g.call(g.A, 'cycle.start')
        for _ in range(400):
            g.step(10.0)
            if g.read('cycle.status')['stage'] == 'sterilization':
                break
        g.step(60.0)
        P = g.read('truth.chamber')['pressure_pa']
        self.assertAlmostEqual(P / rt.psat_water(407.15 + 0.3), 1.0, delta=0.03)
        for _ in range(400):
            g.step(10.0)
            if g.read('cycle.status')['stage'] in ('complete', 'failed'):
                break
        self.assertEqual(g.read('cycle.status')['result'], 'CYCLE ENDED')
        self.assertLess(g.read('truth.loads')['tray']['log10_survivors'], 0.0)
        _water_balance(self, g.ledger())

    def test_liquid_cooling_cycle(self):
        g = Rig()
        g.on()
        g.open_lid()
        g.bottles(2)
        g.close_lid()
        marks = g.run('liquids_121_cooling')
        self.assertEqual(g.read('cycle.status')['result'], 'CYCLE ENDED')
        cool = marks['exhaust'] - marks['cooling']
        self.assertTrue(15 * 60 < cool < 45 * 60, marks)                                   # same-platform: average cooling stage 30 min
        tl = g.read('truth.loads')
        self.assertLessEqual(tl['b1']['temperature_k'], 363.15 + 0.5)
        self.assertEqual(tl['b1']['lost_kg'], 0.0)                                           # compressed air keeps the liquid from boiling
        _water_balance(self, g.ledger())

    def test_fast_exhaust_boils_liquids_and_sealed_bottles_burst(self):
        g = Rig()
        g.on()
        g.open_lid()
        g.bottles(1, bi=False)
        g.call(g.A, 'load.add', load_id='sealed', kind='liquid', mass_kg=0.5, liquid='water', container_volume_m3=1e-3, material='borosilicate_glass', closure='sealed')
        g.call(g.A, 'load.add', load_id='dish', kind='solid', mass_kg=0.2, material='ps')
        g.close_lid()
        g.run('solids_121')
        tl = g.read('truth.loads')
        self.assertTrue(tl['b1']['boilover'] and tl['b1']['lost_kg'] > 0.01, tl['b1'])
        self.assertTrue(tl['sealed']['burst'], tl['sealed'])
        self.assertTrue(tl['dish']['deformed'])
        g.open_lid()
        out = g.call(g.A, 'load.remove', load_id='b1')
        self.assertTrue(out['state']['liquid_lost'])
        _water_balance(self, g.ledger())
        CHECKS.append(dict(id='boilover_burst_deformation', status='passed', layer='process'))

    def test_residual_air_stops_heating_low_heat(self):
        g = Rig()
        g.on()
        g.call(g.E, 'fault.inject', fault='air_valve_stuck_closed', active=True)
        g.call(g.A, 'program.select', program='solids_121')
        g.call(g.A, 'cycle.start')
        g.step(30 * 60.0)
        tc = g.read('truth.chamber')
        self.assertGreater(tc['air_mol'], 2.0)
        self.assertLess(tc['water_temperature_k'], 394.15 - 3.0)
        self.assertLessEqual(tc['pressure_pa'], rt.psat_water(394.15) + rt.P_MARGIN + 3e3)
        g.step(40 * 60.0)
        self.assertIn('LOW_HEAT', g.read('alarms.active'))
        self.assertEqual(g.read('cycle.status')['result'], 'CYCLE FAIL')

    def test_lid_interlock_and_end_temperature(self):
        g = Rig()
        g.on()
        g.open_lid()
        g.bottles(1)
        g.close_lid()
        g.call(g.A, 'program.select', program='liquids_121')
        g.call(g.A, 'cycle.start')
        g.step(25 * 60.0)
        self.assertTrue(g.read('lid.locked'))
        g.call(g.A, 'lid.set', expect='LID_LOCKED', open=True)
        self.assertGreater(g.read('gauge.needle_rad'), 0.5)
        self.assertAlmostEqual(g.read('display.temperature_c'), g.read('chamber.temperature_k') - 273.15, delta=0.06)

    def test_faults_supply_and_power(self):
        g = Rig(water=False)
        g.on()
        g.call(g.A, 'cycle.start', expect='NO_WATER_SUPPLY')
        h = Rig()
        h.on()
        h.call(h.E, 'fault.inject', fault='water_supply_off', active=True)
        h.call(h.A, 'cycle.start')
        h.step(30.0)
        self.assertEqual(h.read('cycle.status')['result'], 'CYCLE FAIL')
        self.assertIn('NO_RES_SRC', h.read('alarms.active'))
        k = Rig()
        k.on()
        k.call(k.E, 'fault.inject', fault='heater_failure', active=True)
        k.call(k.A, 'program.select', program='solids_134')
        k.call(k.A, 'cycle.start')
        k.step(3700.0)
        self.assertEqual(k.read('cycle.status')['result'], 'CYCLE FAIL')
        self.assertIn('LOW_HEAT', k.read('alarms.active'))
        m = Rig()
        m.on()
        m.call(m.E, 'fault.inject', fault='door_gasket_leak', active=True)
        m.call(m.A, 'program.select', program='solids_121')
        m.call(m.A, 'cycle.start')
        m.step(40 * 60.0)
        self.assertGreater(m.ledger()['leak_kg'], 0.0)
        _water_balance(self, m.ledger())
        n = Rig()
        n.on()
        n.call(n.A, 'cycle.start')
        n.step(300.0)
        n.call(n.A, 'power.set', on=False)
        self.assertIsNone(n.read('chamber.temperature_k'))
        n.on()
        self.assertEqual(n.call(n.A, 'records.read', last=1)['records'][0]['result'], 'CYCLE FAIL')
        n.call(n.E, 'fault.inject', fault='pressure_sensor_fault', active=True)
        self.assertIsNone(n.read('chamber.pressure_pa'))
        n.call(n.E, 'fault.inject', fault='temperature_sensor_drift', active=True)
        n.call(n.E, 'truth.inspect')

    def test_record_ring_keeps_200_cycles(self):
        g = Rig()
        g.on()
        for i in range(203):
            g.call(g.A, 'cycle.start')
            g.call(g.A, 'cycle.stop')
            g.step(120.0)
        recs = g.call(g.A, 'records.read')
        self.assertEqual(recs['stored'], 200)
        self.assertEqual(recs['records'][0]['cycle_number'], 4)
        self.assertEqual(g.read('records.stored'), 200)

    def test_program_editing_panel_keys_and_toggle(self):
        g = Rig()
        g.on()
        out = g.call(g.A, 'program.configure', program='extended_time', time_s=999 * 60.0, temperature_k=378.15)
        self.assertEqual(out['parameters']['time_s'], 999 * 60.0)
        g.call(g.A, 'program.configure', program='custom_1', temperature_k=411.15, exhaust='slow_waste', end_temperature_k=353.15)
        first = g.read('program.current')['program']
        nxt = g.call(g.A, 'program.next')['program']
        self.assertNotEqual(first, nxt)
        self.assertTrue(g.call(g.A, 'cycle.toggle')['running'])
        g.step(30.0)
        self.assertFalse(g.call(g.A, 'cycle.toggle')['running'])
        self.assertFalse(g.call(g.A, 'power.press_switch')['on'])
        self.assertTrue(g.call(g.A, 'power.press_switch')['on'])

    def test_waste_bag_lags_and_non_autoclavable_note(self):
        g = Rig()
        g.on()
        g.open_lid()
        notes = g.call(g.A, 'load.add', load_id='bag', kind='waste', mass_kg=2.0, material='pp', organism=BI, log10_n0=6.0)['notes']
        self.assertEqual(notes, [])
        notes = g.call(g.A, 'load.add', load_id='tube', kind='solid', mass_kg=0.1, material='ldpe')['notes']
        self.assertTrue(any('ldpe' in n for n in notes))
        notes = g.call(g.A, 'load.add', load_id='eth', kind='liquid', mass_kg=0.2, liquid='ethanol', container_volume_m3=5e-4, material='borosilicate_glass',
                       closure='sealed')['notes']
        self.assertEqual(len(notes), 2)                                                      # sealed closure and a boiling point below 100 C
        g.call(g.A, 'load.remove', load_id='eth')
        g.close_lid()
        g.run('waste_121')
        tl = g.read('truth.loads')
        self.assertLess(tl['bag']['F0_s'], g.call(g.A, 'records.read', last=1)['records'][0]['chamber_F0_s'] * 0.8)
        self.assertTrue(tl['tube']['deformed'])
        _water_balance(self, g.ledger())


class ReviewRegressions(unittest.TestCase):
    def test_open_chamber_stays_atmospheric_when_loaded(self):          # round-1 R1-02 / C1-06
        g = Rig()
        g.on()
        g.open_lid()
        g.call(g.A, 'load.add', load_id='w', kind='liquid', mass_kg=8.0, liquid='water', container_volume_m3=0.01, material='borosilicate_glass', closure='loose')
        g.step(5.0)
        self.assertAlmostEqual(g.read('truth.chamber')['pressure_pa'], 101325.0, delta=1.0)
        self.assertFalse(g.read('lid.locked'))
        g.close_lid()
        g.step(600.0)
        g.call(g.A, 'lid.set', open=True)                                  # idle, cold: the lid opens again

    def test_liquid_hold_counts_on_the_reference_bottle(self):          # round-1 R1-01
        g = Rig()
        g.on()
        g.open_lid()
        g.bottles(2)
        g.close_lid()
        g.run('liquids_121')
        rec = g.call(g.A, 'records.read', last=1)['records'][-1]
        self.assertEqual(rec['result'], 'CYCLE ENDED')
        L = g.read('truth.loads')
        # the reference bottle was at >= 120.6 C for the whole 20 min hold: its F0 is close to the hold time
        self.assertGreater(min(x['F0_s'] for x in L.values()), 0.8 * 1200.0, L)

    def test_stop_during_exhaust_keeps_venting(self):                   # round-1 R1-03
        g = Rig()
        g.on()
        g.call(g.A, 'program.select', program='solids_121')
        g.call(g.A, 'cycle.start')
        while g.read('cycle.status')['stage'] != 'exhaust':
            g.step(10.0)
        g.step(2.0)
        g.call(g.A, 'cycle.stop')
        g.step(300.0)
        self.assertLess(g.read('truth.chamber')['pressure_pa'], 101325.0 + 3e3)
        st = g.read('cycle.status')
        self.assertIn(st['stage'], ('drain', 'failed'))

    def test_hot_restart_flashes_stored_heat(self):                     # round-1 R1-08
        g = Rig()
        g.on()
        g.call(g.A, 'program.select', program='solids_121')
        g.call(g.A, 'cycle.start')
        while g.read('cycle.status')['stage'] != 'sterilization':
            g.step(10.0)
        g.call(g.A, 'power.set', on=False)                                # power fail: CYCLE FAIL, chamber stays hot and pressurised
        g.step(60.0)
        g.call(g.A, 'power.set', on=True)
        before = g.ledger()['flash_vented_kg']
        T0 = g.read('truth.chamber')['water_temperature_k']
        self.assertGreater(T0, 374.0)
        g.call(g.A, 'cycle.start')
        g.step(30.0)
        self.assertGreater(g.ledger()['flash_vented_kg'] - before, 1e-3)
        _water_balance(self, g.ledger())

    def test_drift_shifts_the_true_hold_and_faults_fail(self):         # round-1 R1-05 / R1-07
        a, b = Rig(), Rig()
        b.call(b.E, 'fault.inject', fault='temperature_sensor_drift', active=True)
        for g in (a, b):
            g.on()
            g.call(g.A, 'program.select', program='solids_121')
            g.call(g.A, 'cycle.start')
            while g.read('cycle.status')['stage'] != 'sterilization':
                g.step(10.0)
            g.step(300.0)
        self.assertLess(b.read('truth.chamber')['water_temperature_k'], a.read('truth.chamber')['water_temperature_k'] - 1.0)
        c = Rig()
        c.on()
        c.call(c.E, 'fault.inject', fault='pressure_sensor_fault', active=True)
        c.call(c.A, 'program.select', program='solids_121')
        c.run('solids_121')
        self.assertEqual(c.call(c.A, 'records.read', last=1)['records'][-1]['failure'], 'PRESS_SENSOR')

    def test_container_fill_and_separate_limits(self):                  # round-1 R1-06 / R1-09, C1-07
        g = Rig()
        g.on()
        g.open_lid()
        g.call(g.A, 'load.add', expect='CONTAINER_OVERFILLED', load_id='x', kind='liquid', mass_kg=5.0, liquid='water', container_volume_m3=1e-6, material='pp')
        r = g.call(g.A, 'load.add', load_id='e', kind='liquid', mass_kg=0.5, liquid='glycerol', container_volume_m3=1e-3, material='borosilicate_glass')
        self.assertTrue(any('inconsistent' in n for n in r['notes']), r)
        g.call(g.A, 'load.add', load_id='s', kind='solid', mass_kg=19.0, material='stainless_316')
        g.call(g.A, 'load.add', load_id='l', kind='liquid', mass_kg=9.0, liquid='water', container_volume_m3=0.01, material='borosilicate_glass')   # liquids and instruments counted separately
        small, big = rt.TuttnauerElvdRuntime('a'), None
        L1 = dict(kind='waste', mass_kg=0.1, cp=3000.0)
        L2 = dict(kind='waste', mass_kg=5.0, cp=3000.0)
        self.assertLess(small._load_ua(L1, True) / (0.1 * 3000.0), 0.7 * small._load_ua(L2, True) / (5.0 * 3000.0) * 10)
        del big

    def test_records_hold_sensor_values_and_pressure_alarms(self):       # round-2 R2-03 / R2-04 / R2-05
        g = Rig()
        g.call(g.E, 'fault.inject', fault='temperature_sensor_drift', active=True)
        g.on()
        g.run('solids_121')
        rec = g.call(g.A, 'records.read', last=1)['records'][-1]
        self.assertGreater(rec['hold_min_k'], 394.15 - 0.6)                  # the record shows what sensor 1 read (drifted), not the truth
        self.assertTrue(all(abs(x['T1_k'] - round(x['T1_k'], 2)) < 1e-9 for x in rec['log']))
        h = Rig()
        h.on()
        h.call(h.E, 'fault.inject', fault='heater_relay_stuck', active=True)
        h.run('solids_121')
        self.assertEqual(h.call(h.A, 'records.read', last=1)['records'][-1]['failure'], 'HIGH_PRESSURE')
        lp = Rig()
        lp.on()
        lp.call(lp.A, 'program.select', program='solids_121')
        lp.call(lp.A, 'cycle.start')
        while lp.read('cycle.status')['stage'] != 'sterilization':
            lp.step(10.0)
        lp.call(lp.E, 'fault.inject', fault='pressure_sensor_drift', active=True)
        lp.step(30.0)
        self.assertIn('LOW_PRESSURE', lp.read('alarms.active'))
        k = Rig()
        k.on()
        k.open_lid()
        k.call(k.A, 'load.add', load_id='w', kind='liquid', mass_kg=8.0, liquid='water', container_volume_m3=0.01, material='borosilicate_glass')
        self.assertAlmostEqual(k.read('truth.chamber')['pressure_pa'], 101325.0, delta=1.0)    # no step needed
        self.assertFalse(k.read('lid.locked'))


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()
