"""tests for CHEM-183 bronkhorst_cemw (Bronkhorst CEM W-202A-NN0-K controlled evaporator and mixer): python3 -m unittest tests/test_asset_bronkhorst_cemw.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; NIST Antoine data reproduce the normal boiling points and the Clausius-Clapeyron enthalpy of vaporization agrees with
the NIST tables; Bronkhorst normal litres (0 C, 1 atm); complete evaporation with species mass balance; the documented W-202A capacity (water
about 120 g/h at 10 ln/min, 100 W) and the incomplete evaporation above it; temperature set point too low and downstream condensation below the
dew point; process pressure raises the dew point; FLUIDAT-like calculation agrees with the process; heater without cooling and the 200 C safety
switch; stopping the heater before the liquid floods the exchanger; bleeding the mixing valve; liquid without carrier gas pushed into the gas line
and purged; power off closes the valves; faults (heater, PT100, clogging, pressure fluctuation); supply pressure too low; documented capacity
window alarms; liquid mixtures."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import bronkhorst_cemw as rt  # noqa: E402

INST = 'bronkhorst_cemw_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
LN = rt.LN_MIN
GH = 1.0 / 3.6e6                         # 1 g/h in kg/s


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, ambient=295.15, pressure=101325.0, line=295.15, instance=INST):
        self.h = rt.BronkhorstCemwRuntime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A = self.h.session('agent')
        self.E = self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', ambient_k=ambient, process_pressure_pa=pressure, line_temperature_k=line, noise_seed=seed)

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
        assert expect in [e['code'] for e in rt.export_bronkhorst_cemw_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def connect(self, liquid=None, gas='nitrogen', p_liq=3.0e5, p_gas=4.0e5, bleed=True):
        P = self.h.plant['process_pressure_pa']
        self.call(self.A, 'supply.connect_liquid', composition=liquid or {'water': 1.0}, pressure_pa=P + p_liq)
        self.call(self.A, 'supply.connect_gas', gas=gas, pressure_pa=P + p_gas)
        self.call(self.A, 'power.set', on=True)
        self.call(self.A, 'controllers.configure', gas_full_scale_m3_s=rt.GAS_FS_MAX, liquid_full_scale_kg_s=rt.LIQ_FS_MAX)
        if bleed:
            self.call(self.A, 'bleed.set', open=True)
            self.step(rt.ENG['bleed_clear_s'] + 3.0)
            self.call(self.A, 'bleed.set', open=False)
            self.step(3.0)

    def start(self, g_h=100.0, ln=10.0, T=423.15, warm=500.0, run=900.0):
        self.call(self.A, 'heater.set_temperature', setpoint_k=T)
        self.call(self.A, 'gas.set_flow', setpoint_m3_s=ln * LN)
        self.step(warm)
        self.call(self.A, 'liquid.set_flow', setpoint_kg_s=g_h * GH)
        self.step(run)
        return self.read()

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


def _balance(test, led, tol=1e-9):
    for s, d in led.items():
        fed = d.get('fed_kg', 0.0)
        acc = sum(d.get(k, 0.0) for k in ('bled_kg', 'into_gas_line_kg', 'purged_from_gas_line_kg', 'evaporated_kg', 'pooled_kg', 'carried_over_kg'))
        test.assertAlmostEqual(fed, acc, delta=tol + 1e-9 * fed, msg=(s, d))
        ev = d.get('evaporated_kg', 0.0)
        test.assertAlmostEqual(ev, d.get('delivered_kg', 0.0) + d.get('condensed_in_line_kg', 0.0) + d['outlet_holdup_kg'], delta=tol + 1e-9 * ev, msg=(s, d))
        test.assertGreaterEqual(d['outlet_holdup_kg'], -1e-12)


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_and_mirrors_consistent(self):
        meta = rt.export_bronkhorst_cemw_contract_metadata()
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
        for name, p in meta['mechanisms']['controlled_evaporation_mixing']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_bronkhorst_cemw_contract_metadata()
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
            g.A.read(instance_id=INST, state_ids=['truth.stream'])
        for cap, args in (('truth.inspect', {}), ('sim.step', dict(dt_s=1.0)), ('ledger.read', {}), ('fault.inject', dict(fault='clogging', active=True)),
                          ('plant.configure', dict(ambient_k=295.15, process_pressure_pa=101325.0, line_temperature_k=295.15, noise_seed=1))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x-' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED', cap)
        g.call(g.E, 'fault.inject', fault='heater_failure', active=True)
        self.assertFalse(any(e['kind'] == 'fault' for e in g.call(g.A, 'events.read', since_s=0.0)['events']))
        self.assertTrue(any(e['kind'] == 'fault' for e in g.call(g.E, 'events.read', since_s=0.0)['events']))

    def test_idempotent_request_ids(self):
        g = Rig()
        g.connect(bleed=False)
        args = dict(setpoint_m3_s=5 * LN)
        r1 = g.A.invoke(instance_id=INST, capability_id='gas.set_flow', arguments=args, request_id='same')
        snap = g.h.read('evaluator')
        r2 = g.A.invoke(instance_id=INST, capability_id='gas.set_flow', arguments=args, request_id='same')
        self.assertEqual(r1, r2)
        self.assertEqual(json.dumps(snap, sort_keys=True, default=str), json.dumps(g.h.read('evaluator'), sort_keys=True, default=str))
        r3 = g.A.invoke(instance_id=INST, capability_id='gas.set_flow', arguments=dict(setpoint_m3_s=6 * LN), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='c_a'), Rig(instance='c_b', ambient=298.15)
        a.call(a.A, 'power.set', on=True)
        self.assertFalse(b.read('power.on'))
        r = a.A.invoke(instance_id='c_b', capability_id='power.set', arguments=dict(on=True), request_id='z')
        self.assertEqual(r['error']['code'], 'UNKNOWN_INSTANCE')

    def test_step_partition_independence(self):
        def run(steps):
            g = Rig(seed=5)
            g.connect()
            g.call(g.A, 'heater.set_temperature', setpoint_k=413.15)
            g.call(g.A, 'gas.set_flow', setpoint_m3_s=5 * LN)
            g.call(g.A, 'liquid.set_flow', setpoint_kg_s=60 * GH)
            for s in steps:
                g.step(s)
            r = g.read()
            return json.dumps({k: r[k] for k in r if k.startswith(('truth.', 'measured.', 'heater.'))}, sort_keys=True), json.dumps(g.ledger(), sort_keys=True)
        a = run([300.0])
        b = run([0.05] * 400 + [0.37, 13.11, 266.52])
        c = run([10.0] * 30)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_rejections_change_nothing(self):
        g = Rig()
        r = rt.BronkhorstCemwRuntime('fresh')
        r.register_principal('e', 'evaluator')
        self.assertEqual(r.session('e').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', ambient_k=295.15, process_pressure_pa=101325.0, line_temperature_k=295.15, noise_seed=2)
        g.call(g.A, 'gas.set_flow', expect='POWER_OFF', setpoint_m3_s=LN)
        g.call(g.A, 'heater.set_temperature', expect='POWER_OFF', setpoint_k=373.15)
        g.call(g.A, 'supply.connect_liquid', expect='UNKNOWN_LIQUID', composition={'unobtainium': 1.0}, pressure_pa=4e5)
        g.call(g.A, 'supply.connect_liquid', expect='INVALID_COMPOSITION', composition={'water': 0.5, 'ethanol': 0.4}, pressure_pa=4e5)
        g.call(g.A, 'supply.connect_liquid', expect='PRESSURE_ABOVE_RATING', composition={'water': 1.0}, pressure_pa=1.2e7)
        g.call(g.A, 'supply.connect_gas', expect='PRESSURE_ABOVE_RATING', gas='nitrogen', pressure_pa=1.5e7)
        g.call(g.A, 'mixture.calculate', expect='NO_LIQUID_SUPPLY')
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'gas.set_flow', expect='NO_GAS_SUPPLY', setpoint_m3_s=LN)
        g.call(g.A, 'liquid.set_flow', expect='NO_LIQUID_SUPPLY', setpoint_kg_s=10 * GH)
        g.call(g.A, 'supply.connect_liquid', composition={'water': 1.0}, pressure_pa=4e5)
        g.call(g.A, 'mixture.calculate', expect='NO_GAS_SUPPLY')
        g.call(g.A, 'supply.connect_gas', gas='nitrogen', pressure_pa=5e5)
        g.call(g.A, 'controllers.configure', gas_full_scale_m3_s=5 * LN, liquid_full_scale_kg_s=60 * GH)
        g.call(g.A, 'gas.set_flow', expect='SETPOINT_ABOVE_FULL_SCALE', setpoint_m3_s=6 * LN)
        g.call(g.A, 'liquid.set_flow', expect='SETPOINT_ABOVE_FULL_SCALE', setpoint_kg_s=70 * GH)
        g.call(g.A, 'gas.set_flow', setpoint_m3_s=2 * LN)
        g.call(g.A, 'controllers.configure', expect='FLOW_ACTIVE', gas_full_scale_m3_s=rt.GAS_FS_MAX, liquid_full_scale_kg_s=rt.LIQ_FS_MAX)
        g.call(g.A, 'supply.connect_gas', expect='GAS_FLOWING', gas='argon', pressure_pa=5e5)
        g.call(g.A, 'liquid.set_flow', setpoint_kg_s=20 * GH)
        g.call(g.A, 'supply.connect_liquid', expect='LIQUID_FLOWING', composition={'ethanol': 1.0}, pressure_pa=4e5)
        g.call(g.A, 'bleed.set', expect='LIQUID_SETPOINT_NOT_ZERO', open=True)
        g.call(g.A, 'liquid.set_flow', setpoint_kg_s=0.0)
        g.call(g.A, 'bleed.set', open=True)
        g.call(g.A, 'liquid.set_flow', expect='BLEED_OPEN', setpoint_kg_s=20 * GH)
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='alien', active=True)
        g.call(g.A, 'heater.set_temperature', expect='INVALID_ARGUMENT', setpoint_k=500.0)


class Library(unittest.TestCase):
    def test_antoine_reproduces_normal_boiling_points(self):
        ok, n = 0, 0
        for s, e in rt.liquids().items():
            tb = e.get('boiling_point_K')
            if not tb:
                continue
            n += 1
            p = rt.psat(s, tb)
            if abs(p / 101325.0 - 1.0) < 0.05:
                ok += 1
            if s == 'water':
                self.assertAlmostEqual(rt.dew_point({'water': 1.0}, 101325.0), 373.15, delta=0.3)
        self.assertGreaterEqual(n, 50)
        self.assertGreaterEqual(ok / n, 0.9, (ok, n))
        CHECKS.append(dict(id='antoine_normal_boiling_points', status='passed', layer='process', within_5pct=ok, compounds=n))

    def test_clausius_clapeyron_dhvap_against_nist_tables(self):
        errs = []
        for s, e in rt.liquids().items():
            for row in e['dhvap_table']:
                if 250.0 < row['T_K'] < 0.85 * 650:
                    errs.append(abs(rt.dhvap(s, row['T_K']) / row['value_J_mol'] - 1.0))
        errs.sort()
        med = errs[len(errs) // 2]
        self.assertGreater(len(errs), 300)
        self.assertLess(med, 0.06, med)
        CHECKS.append(dict(id='dhvap_vs_nist_tables', status='passed', layer='process', rows=len(errs), median_rel_error=med))

    def test_gas_properties_and_normal_litre(self):
        self.assertAlmostEqual(rt.cp_gas_pure('nitrogen', 298.15), 29.12, delta=0.05)
        self.assertAlmostEqual(rt.cp_gas_pure('argon', 400.0), 20.786, delta=0.01)
        self.assertAlmostEqual(rt.cp_gas_pure('air', 298.15), 29.1, delta=0.2)
        self.assertAlmostEqual(LN * rt.P_N / (rt.R_GAS * rt.T_N), 7.436e-4, delta=2e-7)        # 1 ln/min of an ideal gas in mol/s
        CHECKS.append(dict(id='gas_cp_and_normal_litre', status='passed', layer='process'))


class Physics(unittest.TestCase):
    def test_complete_evaporation_and_species_balance(self):
        g = Rig(line=433.15)
        g.connect()
        r = g.start(g_h=100.0, ln=10.0, T=423.15)
        st = r['truth.stream']
        self.assertAlmostEqual(st['evaporated_fraction'], 1.0, places=6)
        self.assertAlmostEqual(st['line_saturation_fraction'], 1.0, places=6)
        self.assertNotIn('INCOMPLETE_EVAPORATION', st['conditions'])
        n_w = st['vapour_mol_s']['water'] * rt.molar_mass('water')
        self.assertAlmostEqual(n_w / (100.0 * GH), 1.0, delta=0.01)
        self.assertAlmostEqual(r['measured.liquid_kg_s'] / (100 * GH), 1.0, delta=0.02)
        led = g.ledger()
        _balance(self, led)
        self.assertGreater(led['water']['bled_kg'], 0.0)
        y = st['mole_fraction_at_exchanger']['water']
        n_g = 10 * LN * rt.P_N / (rt.R_GAS * rt.T_N)
        self.assertAlmostEqual(y, (100 * GH / rt.molar_mass('water')) / ((100 * GH / rt.molar_mass('water')) + n_g), delta=1e-6)
        calc = g.call(g.A, 'mixture.calculate')
        self.assertAlmostEqual(calc['vapour_mole_fraction']['water'], y, delta=1e-6)
        self.assertAlmostEqual(calc['dew_point_k'], st['dew_point_k'], delta=0.05)
        g.call(g.A, 'provenance.read')
        CHECKS.append(dict(id='complete_evaporation_species_balance', status='passed', layer='process', dew_point_k=st['dew_point_k']))

    def test_documented_capacity_120_g_h_water(self):
        res = {}
        for g_h in (110.0, 140.0):
            g = Rig(line=473.15)
            g.connect()
            r = g.start(g_h=g_h, ln=10.0, T=473.15, warm=600.0, run=1500.0)
            res[g_h] = (r['truth.stream']['evaporated_fraction'], sum(r['truth.lines']['pool_kg'].values()), r['truth.stream']['conditions'] + r['alarms.active'], g.ledger())
        self.assertGreater(res[110.0][0], 0.999)
        self.assertLess(res[140.0][0], 0.97)
        self.assertIn('INCOMPLETE_EVAPORATION', res[140.0][2])
        self.assertIn('WINDOW_LIQUID_ABOVE_WINDOW', res[140.0][2])
        self.assertGreater(res[140.0][3]['water'].get('carried_over_kg', 0.0) + res[140.0][1], 1e-4)
        _balance(self, res[140.0][3])
        CHECKS.append(dict(id='capacity_water_120_g_h', status='passed', layer='process', evaporated_110=res[110.0][0], evaporated_140=res[140.0][0]))

    def test_low_setpoint_and_downstream_condensation(self):
        g = Rig(line=295.15)
        g.connect()
        r = g.start(g_h=100.0, ln=2.0, T=333.15, warm=600.0, run=600.0)
        self.assertLess(r['truth.stream']['evaporated_fraction'], 0.9)
        self.assertIn('INCOMPLETE_EVAPORATION', r['truth.stream']['conditions'])
        self.assertIn('CONDENSATION_DOWNSTREAM', r['truth.stream']['conditions'])
        self.assertFalse({'INCOMPLETE_EVAPORATION', 'CONDENSATION_DOWNSTREAM'} & set(r['alarms.active']))      # no sensor sees them: evaluator truth only
        led = g.ledger()
        self.assertGreater(led['water']['condensed_in_line_kg'], 0.0)
        _balance(self, led)
        dp = r['truth.stream']['dew_point_line_k']
        self.assertGreater(dp, 295.15)
        CHECKS.append(dict(id='low_setpoint_incomplete_and_line_condensation', status='passed', layer='process'))

    def test_process_pressure_raises_dew_point(self):
        g = Rig()
        g.connect(bleed=False)
        a = g.call(g.A, 'mixture.calculate', liquid_kg_s=60 * GH, gas_m3_s=5 * LN, pressure_pa=1.0e5)
        b = g.call(g.A, 'mixture.calculate', liquid_kg_s=60 * GH, gas_m3_s=5 * LN, pressure_pa=5.0e5)
        self.assertGreater(b['dew_point_k'], a['dew_point_k'] + 30.0)
        self.assertAlmostEqual(a['vapour_mole_fraction']['water'], b['vapour_mole_fraction']['water'], places=9)
        c = g.call(g.A, 'mixture.calculate', liquid_kg_s=60 * GH, gas_m3_s=0.1 * LN)
        self.assertFalse(c['within_documented_window'])
        CHECKS.append(dict(id='pressure_raises_dew_point', status='passed', layer='process', dew_1bar=a['dew_point_k'], dew_5bar=b['dew_point_k']))

    def test_heater_no_cooling_and_safety_switch(self):
        g = Rig()
        g.connect(bleed=False)
        g.call(g.A, 'heater.set_temperature', setpoint_k=433.15)
        g.step(900.0)
        T1 = g.read('measured.temperature_k')
        self.assertAlmostEqual(T1, 433.15, delta=1.0)
        g.call(g.A, 'heater.set_temperature', setpoint_k=353.15)
        g.step(60.0)
        self.assertEqual(g.read('heater.output_fraction'), 0.0)
        self.assertLess(g.read('measured.temperature_k'), T1)
        self.assertGreater(g.read('measured.temperature_k'), 400.0)               # no cooling: only the slow loss to ambient
        g.h.T_hx = 474.0                                                         # white-box: overshoot past the 200 C switch
        g.h.T_meas = 474.0                                                       # (PT100 settled on the overshoot)
        g.call(g.A, 'heater.set_temperature', setpoint_k=473.15)
        g.step(1.0)
        self.assertIn('OVER_TEMPERATURE', g.read('alarms.active'))
        self.assertIn('SETPOINT_AT_SAFETY_LIMIT', g.read('alarms.active'))
        self.assertTrue(g.read('truth.thermal')['safety_switch_tripped'])
        self.assertEqual(g.read('heater.output_fraction'), 0.0)
        CHECKS.append(dict(id='heater_no_cooling_safety_switch', status='passed', layer='process'))

    def test_heater_off_before_liquid_floods(self):
        g = Rig(line=433.15)
        g.connect()
        g.start(g_h=100.0, ln=5.0, T=423.15, warm=500.0, run=300.0)
        g.call(g.A, 'heater.set_temperature', setpoint_k=273.15)
        g.step(1800.0)
        r = g.read()
        self.assertIn('INCOMPLETE_EVAPORATION', r['truth.stream']['conditions'])
        self.assertIn('LIQUID_ACCUMULATING', r['truth.stream']['conditions'])
        led = g.ledger()
        self.assertGreater(led['water'].get('carried_over_kg', 0.0), 0.0)
        _balance(self, led)
        CHECKS.append(dict(id='heater_off_before_liquid_floods', status='passed', layer='process'))

    def test_bleed_procedure(self):
        g = Rig()
        g.connect(bleed=False)
        self.assertIn('BLEED_RECOMMENDED', g.read('alarms.active'))
        self.assertTrue(g.read('truth.lines')['gas_in_liquid_line'])
        g.call(g.A, 'bleed.set', open=True)
        self.assertNotIn('BLEED_RECOMMENDED', g.read('alarms.active'))
        g.step(1.0)
        self.assertGreater(g.read('bleed.screw_angle_rad'), 0.0)
        g.step(rt.ENG['bleed_clear_s'] + 3.0)
        self.assertAlmostEqual(g.read('bleed.screw_angle_rad'), rt.BLEED_TURNS_RAD, places=6)
        self.assertFalse(g.read('truth.lines')['gas_in_liquid_line'])
        self.assertGreater(g.ledger()['water']['bled_kg'], 0.0)
        g.call(g.A, 'bleed.set', open=False)
        g.step(3.0)
        self.assertEqual(g.read('bleed.screw_angle_rad'), 0.0)
        CHECKS.append(dict(id='bleed_procedure', status='passed', layer='process'))

    def test_unbled_line_makes_liquid_flow_unstable(self):
        vals = {}
        for bleed in (False, True):
            g = Rig(seed=9)
            g.connect(bleed=bleed)
            g.start(g_h=60.0, ln=5.0, T=413.15, warm=300.0, run=60.0)
            xs = []
            for _ in range(60):
                g.step(1.0)
                xs.append(g.read('truth.lines')['liquid_flow_kg_s'])
            m = sum(xs) / len(xs)
            vals[bleed] = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) / m
        self.assertGreater(vals[False], 3 * vals[True] + 1e-4)
        CHECKS.append(dict(id='unbled_line_unstable', status='passed', layer='process', rel_sd=vals))

    def test_liquid_without_gas_pushed_into_gas_line_and_purged(self):
        g = Rig()
        g.connect(p_liq=5.0e5, p_gas=3.0e5)
        g.call(g.A, 'heater.set_temperature', setpoint_k=413.15)
        g.call(g.A, 'liquid.set_flow', setpoint_kg_s=30 * GH)
        g.step(60.0)
        al = g.read('alarms.active')
        self.assertIn('LIQUID_WITHOUT_GAS', al)
        self.assertIn('LIQUID_PRESSURE_ABOVE_GAS', al)
        self.assertGreater(g.read('truth.lines')['liquid_in_gas_line_kg'], 0.0)
        self.assertGreater(g.ledger()['water']['into_gas_line_kg'], 0.0)
        g.call(g.A, 'liquid.set_flow', setpoint_kg_s=0.0)
        g.call(g.A, 'gas.set_flow', setpoint_m3_s=rt.GAS_FS_MAX)
        g.step(rt.PURGE_CLEAR_S + 30.0)
        self.assertEqual(g.read('truth.lines')['liquid_in_gas_line_kg'], 0.0)
        self.assertGreater(g.ledger()['water']['purged_from_gas_line_kg'], 0.0)
        _balance(self, g.ledger())
        CHECKS.append(dict(id='liquid_into_gas_line_and_purge', status='passed', layer='process'))

    def test_power_off_closes_valves(self):
        g = Rig()
        g.connect()
        g.start(g_h=60.0, ln=5.0, T=413.15, warm=300.0, run=120.0)
        g.call(g.A, 'power.set', on=False)
        self.assertIsNone(g.read('measured.temperature_k'))
        g.step(30.0)
        tl = g.read('truth.lines')
        self.assertLess(tl['gas_flow_m3_s'], 1e-9)
        self.assertLess(tl['liquid_flow_kg_s'], 1e-9)
        self.assertEqual(g.read('truth.thermal')['heater_power_w'], 0.0)
        CHECKS.append(dict(id='power_off_closes_valves', status='passed', layer='process'))

    def test_faults_and_supply_pressure(self):
        g = Rig()
        g.connect(p_gas=0.05e5)
        g.call(g.A, 'heater.set_temperature', setpoint_k=413.15)
        g.call(g.A, 'gas.set_flow', setpoint_m3_s=5 * LN)
        g.step(30.0)
        self.assertIn('GAS_FLOW_DEVIATION', g.read('alarms.active'))
        self.assertLess(g.read('truth.lines')['gas_flow_m3_s'], 0.5 * 5 * LN)
        g.call(g.E, 'fault.inject', fault='heater_failure', active=True)
        g.step(20.0)
        self.assertEqual(g.read('truth.thermal')['heater_power_w'], 0.0)
        g.call(g.E, 'fault.inject', fault='heater_failure', active=False)
        g.call(g.E, 'fault.inject', fault='pt100_open', active=True)
        self.assertIsNone(g.read('measured.temperature_k'))
        g.step(5.0)
        self.assertIn('SENSOR_FAILURE', g.read('alarms.active'))
        self.assertEqual(g.read('heater.output_fraction'), 0.0)
        g.call(g.A, 'power.set', on=False)
        self.assertEqual(g.read('heater.output_fraction'), 0.0)
        g.call(g.A, 'power.set', on=True)
        g.call(g.E, 'fault.inject', fault='pt100_open', active=False)
        g.call(g.E, 'fault.inject', fault='clogging', active=True)
        g.step(300.0)
        self.assertLess(g.read('truth.lines')['clog_factor'], 0.95)
        g.call(g.E, 'fault.inject', fault='clogging', active=False)
        g.call(g.E, 'fault.inject', fault='pressure_fluctuation', active=True)
        g.call(g.E, 'fault.inject', fault='gas_inclusion', active=True)
        self.assertEqual(g.call(g.E, 'truth.inspect')['faults'], ['gas_inclusion', 'pressure_fluctuation'])
        CHECKS.append(dict(id='faults_and_supply_pressure', status='passed', layer='process'))

    def test_window_alarms_and_mixture(self):
        g = Rig(line=453.15)
        g.connect(liquid={'water': 0.5, 'ethanol': 0.5})
        g.call(g.A, 'heater.set_temperature', setpoint_k=433.15)
        g.call(g.A, 'gas.set_flow', setpoint_m3_s=0.1 * LN)
        g.call(g.A, 'liquid.set_flow', setpoint_kg_s=40 * GH)
        self.assertIn('WINDOW_GAS_BELOW_WINDOW', g.read('alarms.active'))
        g.call(g.A, 'gas.set_flow', setpoint_m3_s=5 * LN)
        g.step(900.0)
        st = g.read('truth.stream')
        y = st['mole_fraction_at_exchanger']
        self.assertEqual(set(y), {'water', 'ethanol'})
        n_w, n_e = 0.5 / rt.molar_mass('water'), 0.5 / rt.molar_mass('ethanol')
        self.assertAlmostEqual(y['water'] / y['ethanol'], n_w / n_e, delta=1e-3)
        d_mix = st['dew_point_k']
        self.assertGreater(d_mix, rt.dew_point({'ethanol': y['ethanol'] + y['water']}, 101325.0) - 30)
        _balance(self, g.ledger())
        CHECKS.append(dict(id='window_alarms_and_mixture', status='passed', layer='process', dew_mix_k=d_mix))


class ReviewRegressions(unittest.TestCase):
    def test_outlet_inventory_never_negative_after_shutdown(self):
        g = Rig(line=473.15)
        g.connect()
        g.start(g_h=110.0, ln=10.0, T=453.15, warm=300.0, run=300.0)
        g.call(g.A, 'liquid.set_flow', setpoint_kg_s=0.0)
        g.call(g.A, 'gas.set_flow', setpoint_m3_s=0.0)
        for _ in range(12):
            g.step(50.0)
            led = g.ledger()
            for s, d in led.items():
                self.assertGreaterEqual(d['outlet_holdup_kg'], 0.0, (s, d))
                self.assertLessEqual(d.get('delivered_kg', 0.0) + d.get('condensed_in_line_kg', 0.0), d.get('evaporated_kg', 0.0) + 1e-15, (s, d))
        _balance(self, g.ledger())

    def test_bleed_at_sub_atmospheric_process_books_no_negative_mass(self):
        g = Rig(pressure=1.0e4)
        g.call(g.A, 'supply.connect_liquid', composition={'water': 1.0}, pressure_pa=5.0e4)
        g.call(g.A, 'supply.connect_gas', gas='nitrogen', pressure_pa=2.0e5)
        g.call(g.A, 'bleed.set', open=True)
        g.step(10.0)
        led = g.ledger()
        self.assertTrue(all(v >= 0.0 for v in led.get('water', {'fed_kg': 0.0}).values()), led)

    def test_every_library_liquid_survives_calculation_and_reads(self):
        g = Rig(line=473.15)
        g.connect(bleed=False)
        for sp in rt.liquids():
            r = g.call(g.A, 'mixture.calculate', composition={sp: 1.0}, liquid_kg_s=50 * GH, gas_m3_s=5 * LN)
            self.assertTrue(r['dew_point_k'] is None or 150.0 < r['dew_point_k'] < 700.0, (sp, r))
        for sp in ('methyl_formate', 'methyl_benzoate', 'methyl_methacrylate'):
            h = Rig(seed=7)
            h.call(h.A, 'supply.connect_liquid', composition={sp: 1.0}, pressure_pa=4e5)
            h.call(h.A, 'supply.connect_gas', gas='nitrogen', pressure_pa=5e5)
            h.call(h.A, 'power.set', on=True)
            h.call(h.A, 'heater.set_temperature', setpoint_k=413.15)
            h.call(h.A, 'gas.set_flow', setpoint_m3_s=5 * LN)
            h.call(h.A, 'liquid.set_flow', setpoint_kg_s=30 * GH)
            h.step(60.0)
            h.call(h.E, 'truth.inspect')

    def test_downstream_condensation_fractionates_a_mixture(self):
        g = Rig(line=298.15)
        g.connect(liquid={'water': 0.5, 'ethanol': 0.5})
        g.start(g_h=60.0, ln=5.0, T=423.15, warm=500.0, run=600.0)
        st = g.read('truth.stream')
        n_in = st['vapour_mol_s']
        y = st['mole_fraction_delivered']
        self.assertGreater(y['ethanol'] / y['water'], 1.2 * n_in['ethanol'] / n_in['water'])     # the less volatile water condenses preferentially
        P = 101325.0
        K = {s: rt.psat(s, 298.15) / P for s in ('water', 'ethanol')}
        self.assertAlmostEqual(y['water'] / K['water'] + y['ethanol'] / K['ethanol'], 1.0, delta=1e-6)    # the delivered vapour is saturated at the line temperature

    def test_window_uses_the_calculation_pressure_and_needs_no_plant(self):
        h = rt.BronkhorstCemwRuntime('calc_only')
        h.register_principal('a', 'agent')
        a = h.session('a')
        r = a.invoke(instance_id='calc_only', capability_id='mixture.calculate', arguments=dict(composition={'water': 1.0}, gas='nitrogen', liquid_kg_s=60 * GH, gas_m3_s=5 * LN,
                                                                                                 pressure_pa=5e5), request_id='c1')
        self.assertEqual(r['status'], 'succeeded')
        self.assertFalse(r['result']['within_documented_window'])
        self.assertTrue(any('PRESSURE_NOT_CHARACTERISED' in n for n in r['result']['notes']))

    def test_evaporated_fraction_is_relative_to_the_feed(self):
        g = Rig(line=473.15)
        g.connect()
        r = g.start(g_h=130.0, ln=10.0, T=473.15, warm=600.0, run=1500.0)
        st = r['truth.stream']
        self.assertAlmostEqual(st['evaporated_fraction'], min(1.0, st['evaporation_kg_s'] / st['liquid_feed_kg_s']), places=9)
        self.assertGreater(st['evaporated_fraction'], 0.8)

    def test_gas_line_purge_books_each_species_it_received(self):          # round-2 R2-01
        g = Rig(seed=11)
        g.call(g.A, 'supply.connect_gas', gas='nitrogen', pressure_pa=2e5)
        g.call(g.A, 'supply.connect_liquid', composition={'water': 1.0}, pressure_pa=3e5)
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'liquid.set_flow', setpoint_kg_s=60 * GH)
        g.step(60.0)
        g.call(g.A, 'liquid.set_flow', setpoint_kg_s=0.0)
        g.step(5.0)
        g.call(g.A, 'supply.connect_liquid', composition={'ethanol': 1.0}, pressure_pa=3e5)
        g.call(g.A, 'bleed.set', open=True)
        g.step(rt.ENG['bleed_clear_s'] + 5.0)
        g.call(g.A, 'bleed.set', open=False)
        g.step(3.0)
        g.call(g.A, 'liquid.set_flow', setpoint_kg_s=60 * GH)
        g.step(30.0)
        g.call(g.A, 'liquid.set_flow', setpoint_kg_s=0.0)
        g.step(5.0)
        before = g.ledger()
        into = {s: before[s].get('into_gas_line_kg', 0.0) for s in ('water', 'ethanol')}
        self.assertTrue(all(v > 0.0 for v in into.values()), into)
        g.call(g.A, 'gas.set_flow', setpoint_m3_s=10 * LN)
        g.step(400.0)
        led = g.ledger()
        for s in ('water', 'ethanol'):
            self.assertAlmostEqual(led[s].get('into_gas_line_kg', 0.0), 0.0, delta=1e-12, msg=(s, led[s]))
            self.assertAlmostEqual(led[s]['purged_from_gas_line_kg'], into[s], delta=1e-12, msg=(s, led[s]))
        _balance(self, led)

    def test_narrow_degenerate_fit_continues_with_watson_enthalpy(self):    # round-2 R2-02
        sp = 'methyl_formate'
        lo, hi = rt.liquids()[sp]['antoine_fit']['t_range_K']
        self.assertLess(hi - lo, 25.0)
        for te in (lo, hi):
            self.assertAlmostEqual(rt.psat(sp, te - 1e-6) / rt.psat(sp, te + 1e-6), 1.0, delta=1e-5)
        v = [rt.psat(sp, 150.0 + 0.5 * i) for i in range(1101)]               # round-3 R3-01: monotonic up to 700 K (capped at Tc)
        self.assertTrue(all(b_ > a_ for a_, b_ in zip(v, v[1:])))
        for T in (250.0, 340.0, 400.0, 460.0):                              # local Clausius-Clapeyron slope = Watson enthalpy of vaporization
            d = 1e-3
            slope = -(math.log(rt.psat(sp, T + d)) - math.log(rt.psat(sp, T - d))) / (1.0 / (T + d) - 1.0 / (T - d)) * rt.R_GAS
            self.assertAlmostEqual(slope / rt.dhvap(sp, T), 1.0, delta=0.15, msg=T)
        g = Rig(seed=12)
        g.call(g.A, 'supply.connect_liquid', composition={sp: 1.0}, pressure_pa=4e5)
        prov = g.call(g.A, 'provenance.read')
        self.assertEqual(prov['liquids'][sp]['vapour_pressure_quality'], 'inconsistent')

    def test_watson_anchor_follows_the_non_derived_nist_rows(self):        # round-2 R2-03
        L = rt.liquids()
        for sp in ('glycerol', 'propanoic_acid'):
            rows = [r for r in L[sp]['dhvap_table'] if r.get('method') != 'A' and r['T_K'] < L[sp]['critical_temperature_K'] - 5.0]
            errs = sorted(abs(rt.dhvap(sp, r['T_K']) / r['value_J_mol'] - 1.0) for r in rows)
            self.assertLess(errs[len(errs) // 2], 0.06, (sp, errs))
        gc = [r for r in L['glycerol']['dhvap_table'] if r.get('method') == 'GC']
        self.assertTrue(gc and all(abs(rt.dhvap('glycerol', r['T_K']) / r['value_J_mol'] - 1.0) < 0.05 for r in gc))


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()
