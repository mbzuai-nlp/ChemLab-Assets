"""CHEM-148 NETZSCH MMC 315 Nexus ARC module runtime tests: python3 -m unittest tests/test_asset_netzsch_mmc315.py

Themes (pipeline.md): setpoints only write targets; read/describe/poll are pure; rejected preconditions leave the state
unchanged; two instances are isolated; agent/evaluator truth isolation and result redaction; energy and mole conservation;
real return values validate against the contract schemas; async completion / cancellation.
Physics: the NETZSCH HWS example (20 % DTBP in toluene, Ti sphere): onset, dT, phi, dT_ad, heat and pressure step; HWS
detection against the analytic threshold temperature; adiabatic tracking limited to the documented 50 K/min; Iso-Fixed /
Iso-Track / ramp exotherm detection; end criteria (self-heat rate net of the programme) and the 150 bar limit; drift of an
inert sample below the threshold; Townsend-Tou evaluation with quality flags; vapour pressure continuous across Tc;
1 s instrument cycle (host-step independence, stable PI); over-range guards; data reduction; request binding.
No native scene, robot or hardware claims.
"""
import copy
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.asset_runtime.netzsch_mmc315 import *  # noqa: E402,F401,F403
from scripts.asset_runtime import netzsch_mmc315 as rt  # noqa: E402

PROCESS_EVIDENCE = {}
CHECKS = []
_spec = importlib.util.spec_from_file_location('mmc315_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
asset_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(asset_tool)
_ispec = importlib.util.spec_from_file_location('mmc315_interface', ROOT / 'outputs/expansion_batch04/t1_thermal/netzsch_mmc315/interface_definition.py')
interface = importlib.util.module_from_spec(_ispec)
_ispec.loader.exec_module(interface)
DTBP = 'di_tbutyl_peroxide'
CALIBRATED = dict(sample_tc_offset_k=0.0, jacket_tc_offset_k=0.0, tc_noise_k=0.001)   # light containers (small C_s) need a quiet zero-mismatch pair
LIB = json.loads(LIBRARY_PATH.read_text())


def call(h, k, **a):
    """Invoke through the runtime, validate the returned value and check that only declared slots changed immediately."""
    before = h.read('debug')
    r = h.invoke(k, **a)
    validate(CAPS[k]['output_schema'], r)
    problems = asset_tool.validate_value(r, CAPS[k]['output_schema'])
    if problems:
        raise AssertionError(f'{k}: {problems}')
    after = h.read('debug')
    delta = {x for x in after if before[x] != after[x]}
    if k != 'sim.step' and not delta <= set(CAPS[k]['effects']):
        raise AssertionError(f'{k}: undeclared state changes {sorted(delta - set(CAPS[k]["effects"]))}')
    PROCESS_EVIDENCE.setdefault(k, []).append(dict(time_s=h.time_s, result_keys=sorted(r), actual_call=True))
    return r


def configured(plant=None, method=None, power=True):
    h = NetzschMmc315Runtime()
    call(h, 'plant.configure', **dict(PLANT_EXAMPLE, **(plant or {})))
    call(h, 'method.configure', **dict(METHOD_EXAMPLE, **(method or {})))
    if power:
        call(h, 'power.set', enabled=True)
    return h


def loaded(load=None, h=None, **kw):
    h = h or configured(**kw)
    call(h, 'lid.set', open=True); h.step(3.0)
    call(h, 'sample.load', **dict(LOAD_EXAMPLE, **(load or {})))
    call(h, 'lid.set', open=False); h.step(3.0)
    return h


def run(h, limit_h=60.0, dt=60.0):
    call(h, 'test.start')
    t = 0.0
    while h._test_active() and t < limit_h * 3600:
        call(h, 'sim.step', dt_s=dt)
        t += dt
    return call(h, 'test.result')


def threshold_temperature(tpl, dT_obs, thr_k_min):
    """Temperature at which the zero-conversion adiabatic self-heat rate equals the threshold (first-order template kinetics)."""
    k = thr_k_min / 60.0 / dT_obs
    return tpl['kinetics']['Ea_J_mol'] / (GAS_CONSTANT * math.log(tpl['kinetics']['A_per_s'] / k))


class ConstantsAndLibrary(unittest.TestCase):
    def test01_identity_and_documented_constants(self):
        self.assertEqual(ASSET_TYPE_ID, 'instrument.netzsch_mmc315')
        self.assertEqual(CATALOG_ID, 'CHEM-148')
        self.assertAlmostEqual(TRACKING_RATE_K_S * 60, 50.0)
        self.assertAlmostEqual(MAX_TEMPERATURE_K, 773.15)
        self.assertAlmostEqual(MAX_PRESSURE_PA, 150e5)
        self.assertEqual(CONTAINER_VOLUME_RANGE_M3, (0.5e-6, 8.5e-6))
        self.assertAlmostEqual(TEMPERATURE_READABILITY_K, 0.01)
        self.assertAlmostEqual(PRESSURE_READABILITY_PA, 1000.0)

    def test02_interface_geometry_agrees_with_runtime(self):
        self.assertAlmostEqual(interface.TRAY_TRAVEL_M, TRAY_TRAVEL_M)
        self.assertAlmostEqual(interface.LID_OPEN_RAD, LID_OPEN_RAD)
        self.assertAlmostEqual(interface.POWER_SWITCH_ON_RAD, POWER_SWITCH_ON_RAD)
        self.assertAlmostEqual(interface.CONTAINER_VOLUME_M3, CONTAINER_VOLUME_RANGE_M3[1])
        self.assertAlmostEqual(interface.CONTAINER_MASS_KG, LOAD_EXAMPLE['container_mass_kg'])
        for part, sid, capid, *_ in interface.MOTION:
            self.assertIn(sid, SLOTS); self.assertIn(capid, CAPS)
        for r in interface.READOUTS:
            self.assertIn(r['source']['bridge'][0], SLOTS)
            self.assertEqual(SLOTS[r['source']['bridge'][0]]['visibility'], 'agent')
        for lamp in interface.LAMPS:
            self.assertIn(lamp['source']['lamp']['bridge'], SLOTS)

    def test03_template_library_real_and_complete(self):
        self.assertGreaterEqual(LIB['coverage']['distinct_substances'], 50)
        self.assertEqual(LIB['coverage']['synthetic_records'], 0)
        for t in LIB['templates']:
            self.assertEqual(t['evidence'], 'measured_manufacturer')
            self.assertTrue(t['measurements'] and all(m['source']['sha256'] for m in t['measurements']))
            self.assertGreater(t['kinetics']['A_per_s'], 0); self.assertGreater(t['kinetics']['Ea_J_mol'], 5e4)
            self.assertTrue(t['heat_of_decomposition_J_per_g'] is None or t['cas'] == '110-05-4')
        dtbp = templates()[DTBP]
        self.assertAlmostEqual(dtbp['arc_reference']['thermal_inertia_phi'], 1.48)
        report = json.loads((ROOT / 'outputs/expansion_batch04/t1_thermal/netzsch_mmc315/template_validation.json').read_text())
        self.assertEqual(report['status'], 'passed')

    def test04_vapour_pressure_follows_nist_toluene(self):
        self.assertAlmostEqual(solvent_vapour_pressure_pa('toluene', 383.78) / 1e5, 1.013, delta=0.02)      # normal boiling point 383.8 K
        p200 = solvent_vapour_pressure_pa('toluene', 473.15) / 1e5
        self.assertAlmostEqual(p200, 10 ** (4.54436 - 1738.123 / (473.15 + 0.394)), places=6)
        gap = [solvent_vapour_pressure_pa('toluene', T) for T in (384.66, 400.0, 420.0)]
        self.assertTrue(gap[0] < gap[1] < gap[2])                                                          # monotone across the bridged gap
        Tc = LIB['solvents']['toluene']['critical_temperature_K']
        lo, hi = solvent_vapour_pressure_pa('toluene', Tc - 1e-6), solvent_vapour_pressure_pa('toluene', Tc + 1e-6)
        self.assertLess(abs(hi / lo - 1), 1e-6)                                                             # continued smoothly above Tc
        self.assertLess(solvent_vapour_pressure_pa('toluene', 600.0), solvent_vapour_pressure_pa('toluene', 620.0))
        self.assertEqual(solvent_vapour_pressure_pa('none', 450.0), 0.0)

    def test05_exact_thermal_step_conserves_energy(self):
        Cj, Cs, G, Gsj, P, Q, Ta = 1200.0, 17.8, 0.15, 0.06, 300.0, 0.5, 296.15
        Tj, Ts, iu, ix = thermal_step(Cj, Cs, G, Gsj, P, Q, Ta, 320.0, 300.0, 7.3)
        self.assertAlmostEqual(P * 7.3 + Q * 7.3 - G * iu, Cj * (Tj - 320.0) + Cs * (Ts - 300.0), delta=1e-8)
        self.assertAlmostEqual(Q * 7.3 + Gsj * ix, Cs * (Ts - 300.0), delta=1e-8)
        # millisecond sub-steps keep full relative accuracy of the integrals (trapezoid is O(dt^3) exact here)
        dt = 1e-3
        Tj, Ts, iu, ix = thermal_step(Cj, Cs, G, Gsj, P, Q, Ta, 520.0, 519.0, dt)
        self.assertLess(abs(iu / ((520.0 - Ta + Tj - Ta) / 2 * dt) - 1), 1e-12)
        self.assertLess(abs(ix / ((520.0 - 519.0 + Tj - Ts) / 2 * dt) - 1), 1e-9)

    def test06_nth_order_update_is_exact_and_continuous_in_n(self):
        y, k, dt = 0.7, 3e-3, 40.0
        self.assertAlmostEqual(nth_order_remaining(y, k, dt, 1.0), y * math.exp(-k * dt), places=15)
        for n in (1.0 + 2e-12, 1.0 - 2e-12, 1.0 + 1e-7):
            self.assertLess(abs(nth_order_remaining(y, k, dt, n) / (y * math.exp(-k * dt)) - 1), 1e-6, n)
        self.assertAlmostEqual(nth_order_remaining(y, k, dt, 2.0), 1.0 / (1.0 / y + k * dt), places=14)
        self.assertEqual(nth_order_remaining(0.01, 1.0, 10.0, 0.5), 0.0)                                   # n < 1: complete within dt
        self.assertAlmostEqual(nth_order_remaining(0.5, 0.01, 1.0, 0.5), (math.sqrt(0.5) - 0.005) ** 2, places=14)


class RuntimeContract(unittest.TestCase):
    def test10_configuration_only_writes_targets(self):
        h = configured()
        T0 = h.T_s
        call(h, 'method.configure', **dict(METHOD_EXAMPLE, start_temperature_k=373.15))
        self.assertEqual(h.T_s, T0)
        self.assertFalse(h.read('agent')['test.running'])

    def test11_read_describe_poll_do_not_advance_time(self):
        h = loaded()
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r = s.invoke(instance_id='mmc315_01', capability_id='test.start', arguments={}, request_id='x1')
        h.step(1800.0)
        t0, rev = h.time_ns, h.revision
        before = copy.deepcopy(h.read('debug'))
        for _ in range(3):
            s.read(instance_id='mmc315_01'); s.describe(instance_id='mmc315_01'); s.poll(r['operation_id'])
            call(h, 'test.result'); call(h, 'analysis.kinetics', **CAPS['analysis.kinetics']['example_arguments'])
        self.assertEqual((h.time_ns, h.revision), (t0, rev))
        self.assertEqual(before, h.read('debug'))

    def test12_rejections_leave_state_unchanged(self):
        h = configured()
        cases = [('test.start', {}, 'NO_SAMPLE'), ('sample.load', dict(LOAD_EXAMPLE), 'LID_NOT_OPEN'), ('sample.unload', {}, 'NO_SAMPLE'),
                 ('test.stop', {}, 'NOT_RUNNING'), ('plant.configure', dict(PLANT_EXAMPLE), 'PLANT_ALREADY_CONFIGURED'), ('module.scanning', {}, 'UNSUPPORTED'),
                 ('method.configure', dict(METHOD_EXAMPLE, end_temperature_k=313.15), 'INVALID_METHOD'),
                 ('method.configure', dict(METHOD_EXAMPLE, mode='iso_fixed'), 'INVALID_METHOD'),
                 ('method.configure', dict(METHOD_EXAMPLE, cool_to_k=PLANT_EXAMPLE['ambient_k'] + 2.0), 'COOL_TARGET_BELOW_AMBIENT'),
                 ('method.configure', dict(METHOD_EXAMPLE, search_min=2.0), 'INVALID_ARGUMENT'),
                 ('method.configure', dict(METHOD_EXAMPLE, wait_min=1.0), 'INVALID_ARGUMENT')]
        for k, a, code in cases:
            before = copy.deepcopy(h.read('debug'))
            with self.assertRaises(ActionRejected) as e:
                h.invoke(k, **a)
            self.assertEqual(str(e.exception), code, k)
            self.assertEqual(before, h.read('debug'), k)
        call(h, 'lid.set', open=True); h.step(3.0)
        for load, code in ((dict(template_id='nope'), 'UNKNOWN_TEMPLATE'), (dict(sample_cp_j_kg_k=None), 'SAMPLE_HEAT_CAPACITY_REQUIRED'),
                           (dict(template_id='dicumyl_peroxide'), 'HEAT_OF_DECOMPOSITION_REQUIRED'), (dict(gas_yield_mol_per_mol=None), 'GAS_YIELD_REQUIRED'),
                           (dict(sample_mass_kg=8e-3), 'CONTAINER_OVERFILLED'), (dict(accept_priors=False), 'ENGINEERING_ACCEPTANCE_REQUIRED'),
                           (dict(reactant_mass_fraction=1.0, solvent='none', heat_of_decomposition_j_kg=1e7, sample_cp_j_kg_k=500.0), 'ADIABATIC_RISE_OUT_OF_MODEL_RANGE')):
            with self.assertRaises(ActionRejected) as e:
                h.invoke('sample.load', **dict(LOAD_EXAMPLE, **load))
            self.assertEqual(str(e.exception), code)
        with self.assertRaises(ActionRejected) as e:
            h.invoke('plant.configure', **PLANT_EXAMPLE)
        bad = NetzschMmc315Runtime()
        for plant, code in ((dict(jacket_heater_max_w=400.0), 'TRACKING_RATE_NOT_REACHABLE'), (dict(jacket_heat_capacity_j_k=100.0), 'CONTROLLER_UNSTABLE'),
                            (dict(controller_gain_w_k=5000.0), 'CONTROLLER_UNSTABLE'), (dict(sample_tc_offset_k=0.06, jacket_tc_offset_k=-0.06), 'THERMOCOUPLE_MISMATCH')):
            with self.assertRaises(ActionRejected) as e:
                bad.invoke('plant.configure', **dict(PLANT_EXAMPLE, **plant))
            self.assertEqual(str(e.exception), code)
        # a method configured before the plant is re-checked at start
        late = NetzschMmc315Runtime()
        late.invoke('method.configure', **dict(METHOD_EXAMPLE, cool_to_k=298.15))
        call(late, 'plant.configure', **PLANT_EXAMPLE)
        call(late, 'power.set', enabled=True)
        loaded(h=late)
        with self.assertRaises(ActionRejected) as e:
            late.invoke('test.start')
        self.assertEqual(str(e.exception), 'COOL_TARGET_BELOW_AMBIENT')

    def test13_two_instances_isolated(self):
        a, b = loaded(), configured()
        call(a, 'test.start'); a.step(7200.0)
        self.assertAlmostEqual(b.T_s, PLANT_EXAMPLE['ambient_k'])
        self.assertIsNone(b.sample)
        self.assertGreater(a.T_s, PLANT_EXAMPLE['ambient_k'] + 10)

    def test14_agent_truth_isolation_and_redaction(self):
        h = loaded()
        h.register_principal('agent', 'agent'); h.register_principal('eval', 'evaluator')
        ag, ev = h.session('agent'), h.session('eval')
        obs = ag.read(instance_id='mmc315_01')['observations']
        self.assertTrue(all(SLOTS[k]['visibility'] == 'agent' for k in obs))
        self.assertFalse(any(k.startswith('truth.') or k.endswith('_m') or k.endswith('_rad') for k in obs))
        with self.assertRaises(ActionRejected):
            ag.read(instance_id='mmc315_01', state_ids=['truth.conversion'])
        for k in ('ledger.read', 'provenance.read', 'sample.load', 'tray.move', 'sim.step', 'plant.configure'):
            self.assertEqual(ag.invoke(instance_id='mmc315_01', capability_id=k, arguments=CAPS[k]['example_arguments'], request_id='r' + k)['error']['code'], 'ACCESS_DENIED')
        r = ag.invoke(instance_id='mmc315_01', capability_id='test.start', arguments={}, request_id='s1')
        h.step(20 * 3600.0)
        res = ag.invoke(instance_id='mmc315_01', capability_id='test.result', arguments={}, request_id='q1')['result']
        self.assertNotIn(DTBP, json.dumps(res))
        self.assertIn('evaluator provenance.read', res['template_source'])
        prov = ev.invoke(instance_id='mmc315_01', capability_id='provenance.read', arguments={}, request_id='p1')['result']
        self.assertIn(DTBP, prov['loaded_template'])
        self.assertEqual(prov['operations'][0]['operation_id'], r['operation_id'])
        d = ag.describe(instance_id='mmc315_01')
        self.assertFalse(any(v['visibility'] != 'agent' for v in d['capabilities'].values()))
        n = len(rt.LIMITATIONS)
        d['capabilities']['test.result']['limitations'].append('mutated')
        self.assertEqual(len(rt.LIMITATIONS), n)

    def test15_foreign_agent_guard(self):
        h = loaded()
        for p in ('a1', 'a2'):
            h.register_principal(p, 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        s1.invoke(instance_id='mmc315_01', capability_id='test.start', arguments={}, request_id='x')
        h.step(600.0)
        for k, a in [('test.stop', {}), ('test.result', {}), ('method.configure', dict(METHOD_EXAMPLE)), ('power.set', {'enabled': False})]:
            self.assertEqual(s2.invoke(instance_id='mmc315_01', capability_id=k, arguments=a, request_id='y' + k)['error']['code'], 'ACCESS_DENIED', k)
        own = lambda s_: s_.read(instance_id='mmc315_01', state_ids=['test.started_by_other_agent'])['observations']['test.started_by_other_agent']['value']  # noqa: E731
        self.assertEqual((own(s1), own(s2)), (False, True))
        self.assertEqual(s1.invoke(instance_id='mmc315_01', capability_id='test.stop', arguments={}, request_id='z')['status'], 'succeeded')

    def test16_request_dedupe_poll_cancel(self):
        h = loaded()
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r1 = s.invoke(instance_id='mmc315_01', capability_id='method.configure', arguments=dict(METHOD_EXAMPLE), request_id='same')
        self.assertEqual(r1, s.invoke(instance_id='mmc315_01', capability_id='method.configure', arguments=dict(METHOD_EXAMPLE), request_id='same'))
        self.assertEqual(s.invoke(instance_id='mmc315_01', capability_id='method.configure', arguments=dict(METHOD_EXAMPLE, step_k=5.0), request_id='same')['error']['code'],
                         'REQUEST_CONFLICT')
        op = s.invoke(instance_id='mmc315_01', capability_id='test.start', arguments={}, request_id='s')['operation_id']
        h.step(3 * 3600.0)
        self.assertEqual(s.poll(op)['status'], 'running')
        n = len(h.test['points'])
        c = s.cancel(op, 'c1')
        self.assertEqual((c['status'], c['result']['phase']), ('succeeded', 'cancelled'))
        self.assertEqual(s.cancel(op, 'c1'), c)
        res = s.poll(op)
        self.assertEqual(res['status'], 'cancelled')
        self.assertEqual(len(res['result']['points']), n)
        with self.assertRaises(ActionRejected) as e:
            s.cancel(op, 'c2')
        self.assertEqual(str(e.exception), 'NOT_RUNNING')
        with self.assertRaises(ActionRejected) as e:
            s.cancel(op, 'c2')                                                                            # replay of the recorded rejection
        self.assertEqual(str(e.exception), 'NOT_RUNNING')
        self.assertEqual(s.invoke(instance_id='mmc315_01', capability_id='test.result', arguments={}, request_id='c2')['error']['code'], 'REQUEST_CONFLICT')
        # a rejected request binds its request_id as well
        rej = s.invoke(instance_id='mmc315_01', capability_id='test.stop', arguments={}, request_id='rej')
        self.assertEqual(rej['error']['code'], 'NOT_RUNNING')
        self.assertEqual(s.invoke(instance_id='mmc315_01', capability_id='test.stop', arguments={}, request_id='rej'), rej)
        self.assertEqual(s.invoke(instance_id='mmc315_01', capability_id='method.configure', arguments=dict(METHOD_EXAMPLE), request_id='rej')['error']['code'],
                         'REQUEST_CONFLICT')
        with self.assertRaises(ActionRejected) as e:
            s.poll('mmc315_01:test:99')
        self.assertEqual(str(e.exception), 'UNKNOWN_OPERATION')
        with self.assertRaises(ActionRejected):
            s.write(instance_id='mmc315_01', state_id='test.step_target_k', value=400.0, request_id='w')

    def test17_unsupported_options_are_explicit(self):
        h = configured()
        for k in UNSUPPORTED:
            with self.assertRaises(ActionRejected) as e:
                h.invoke(k)
            self.assertEqual(str(e.exception), 'UNSUPPORTED')


class Physics(unittest.TestCase):
    def test20_dtbp_hws_reproduces_the_netzsch_example(self):
        h = loaded(method=dict(end_temperature_k=503.15))
        res = run(h)
        ref = templates()[DTBP]['arc_reference']
        self.assertEqual((res['phase'], res['validity']), ('succeeded', 'valid'))
        e = res['exotherms'][0]
        self.assertLess(abs(e['onset_temperature_k'] - 273.15 - ref['detection_temperature_C']), 3.0)
        self.assertLess(abs(e['delta_t_observed_k'] / ref['delta_T_observed_K'] - 1), 0.03)
        self.assertAlmostEqual(e['phi'], ref['thermal_inertia_phi'], delta=0.005)
        self.assertLess(abs(e['delta_t_adiabatic_k'] / ref['delta_T_adiabatic_K'] - 1), 0.03)
        self.assertLess(abs(e['specific_heat_of_reaction_j_kg_sample'] / 1e3 / ref['specific_heat_of_reaction_J_per_g_sample'] - 1), 0.03)
        self.assertLess(abs((e['pressure_after_pa'] - e['pressure_before_pa']) / 1e5 - ref['pressure_step_bar']), 3.0)
        tk = templates()[DTBP]['kinetics']
        self.assertLess(abs(e['kinetics']['activation_energy_j_mol'] / tk['Ea_J_mol'] - 1), 0.03)             # Townsend-Tou recovers the template
        self.assertLess(abs(e['kinetics']['log10_a_per_s'] - math.log10(tk['A_per_s'])), 0.4)
        self.assertEqual((e['conditions'], e['kinetics']['quality']), ('adiabatic', 'ok'))
        self.assertFalse(e['tracking_rate_exceeded'])
        for x in (e['onset_temperature_k'], e['end_temperature_k'], e['max_temperature_k'], e['temperature_at_max_rate_k'], res['max_temperature_k']):
            self.assertLess(abs(x / TEMPERATURE_READABILITY_K - round(x / TEMPERATURE_READABILITY_K)), 1e-6)        # published values are readings
        for x in (e['pressure_before_pa'], e['pressure_after_pa'], res['max_pressure_pa']):
            self.assertLess(abs(x / PRESSURE_READABILITY_PA - round(x / PRESSURE_READABILITY_PA)), 1e-6)
        self.assertLessEqual(e['temperature_at_max_rate_k'], e['max_temperature_k'])
        self.assertEqual((res['exotherm_count'], res['exotherms_dropped']), (len(res['exotherms']), 0))
        PROCESS_EVIDENCE['dtbp_netzsch_example'] = dict(onset_C=e['onset_temperature_k'] - 273.15, dT=e['delta_t_observed_k'], dT_ad=e['delta_t_adiabatic_k'],
                                                        pressure_step_bar=(e['pressure_after_pa'] - e['pressure_before_pa']) / 1e5,
                                                        Ea_kJ_mol=e['kinetics']['activation_energy_j_mol'] / 1e3, lgA=e['kinetics']['log10_a_per_s'])

    def test21_detection_temperature_follows_the_threshold(self):
        # dicumyl peroxide 30 % in toluene with an explicit host heat prior: detection within one HWS step above the analytic threshold temperature
        tpl = templates()['dicumyl_peroxide']
        load = dict(template_id='dicumyl_peroxide', sample_mass_kg=4.0e-3, reactant_mass_fraction=0.30, heat_of_decomposition_j_kg=1.2e6, gas_yield_mol_per_mol=0.5)
        h = loaded(load=load, method=dict(end_temperature_k=493.15, step_k=5.0))
        res = run(h)
        s = h.sample
        dT_obs = s['m_r'] * s['dH'] / s['C_s']
        T_thr = threshold_temperature(tpl, dT_obs, METHOD_EXAMPLE['threshold_k_min'])
        on = res['exotherms'][0]['onset_temperature_k']
        self.assertTrue(T_thr - 2.0 <= on <= T_thr + 5.0 + 2.0, (on, T_thr))
        self.assertLess(abs(res['exotherms'][0]['delta_t_observed_k'] / dT_obs - 1), 0.05)
        # a higher threshold detects later
        h2 = loaded(load=load, method=dict(end_temperature_k=493.15, step_k=5.0, threshold_k_min=0.2))
        self.assertGreater(run(h2)['exotherms'][0]['onset_temperature_k'], on + 5.0)

    def test22_inert_sample_drift_stays_below_threshold(self):
        h = loaded(load=dict(reactant_mass_fraction=0.0, gas_yield_mol_per_mol=None), method=dict(end_temperature_k=423.15))
        res = run(h)
        self.assertEqual(res['validity'], 'no_exotherm_detected')
        pts = res['points']
        late = []                                                            # search points after the settling third of each search segment
        for i, p in enumerate(pts):
            if p['stage'] == 'search' and p['self_heat_rate_k_min'] is not None:
                j = i
                while j > 0 and pts[j - 1]['stage'] == 'search':
                    j -= 1
                if p['time_s'] - pts[j]['time_s'] >= METHOD_EXAMPLE['search_min'] * 60.0 / 3.0 + 120.0:
                    late.append(p['self_heat_rate_k_min'])
        thr = METHOD_EXAMPLE['threshold_k_min']
        self.assertTrue(late and max(abs(r) for r in late) < thr, late[:20])                             # displayed 2 min rate: noisy but below
        drift = PLANT_EXAMPLE['sample_jacket_conductance_w_k'] * (PLANT_EXAMPLE['sample_tc_offset_k'] - PLANT_EXAMPLE['jacket_tc_offset_k']) / h.sample['C_s'] * 60.0
        self.assertLess(abs(sum(late) / len(late) - drift), 0.25 * thr)                                  # mean = offset-mismatch drift
        self.assertAlmostEqual(h.sample['alpha'], 0.0)

    def test23_tracking_rate_limit_is_flagged(self):
        # 100 % DTBP (no solvent) in a light container: self-heating far above the documented 50 K/min tracking rate
        load = dict(sample_mass_kg=1.0e-3, reactant_mass_fraction=1.0, solvent='none', container_mass_kg=4.0e-3, gas_yield_mol_per_mol=0.3)
        h = loaded(load=load, plant=CALIBRATED, method=dict(end_temperature_k=673.15, max_rate_k_min=50.0, max_pressure_pa=MAX_PRESSURE_PA))
        res = run(h)
        e = res['exotherms'][0]
        self.assertTrue(e['tracking_rate_exceeded'] or res['terminal_reason'] in ('END_RATE_REACHED', 'PRESSURE_LIMIT_EXCEEDED', 'END_PRESSURE_REACHED'))
        self.assertIn(res['validity'], ('end_rate_reached', 'end_pressure_reached', 'pressure_limit_exceeded', 'valid'))

    def test24_end_pressure_and_hard_limit(self):
        load = dict(sample_mass_kg=5.0e-3, reactant_mass_fraction=0.30, gas_yield_mol_per_mol=2.0)
        res = run(loaded(load=load, method=dict(end_temperature_k=503.15, max_pressure_pa=20e5)))
        self.assertEqual((res['phase'], res['validity'], res['terminal_reason']), ('succeeded', 'end_pressure_reached', 'END_PRESSURE_REACHED'))
        self.assertLess(res['max_pressure_pa'], 25e5)
        for e in res['exotherms']:                                                                        # truncated by the end pressure
            if not e['complete']:
                self.assertEqual(e['kinetics']['quality'], 'incomplete_exotherm')
        h = loaded(load=dict(load, gas_yield_mol_per_mol=10.0), method=dict(end_temperature_k=503.15, max_pressure_pa=MAX_PRESSURE_PA))
        res = run(h)
        self.assertIn(res['terminal_reason'], ('PRESSURE_LIMIT_EXCEEDED', 'END_PRESSURE_REACHED'))

    def test25_iso_modes_and_ramp(self):
        iso = dict(mode='iso_fixed', iso_temperature_k=373.15, iso_hours=6.0, end_temperature_k=493.15)
        res = run(loaded(method=iso))
        self.assertEqual(res['validity'], 'no_exotherm_detected')
        self.assertTrue(any(abs(p['sample_k'] - 373.15) < 0.6 for p in res['points'] if p['stage'] == 'iso'))
        track = dict(iso, mode='iso_track', iso_temperature_k=398.15, iso_hours=30.0)
        res = run(loaded(method=track))
        self.assertEqual(res['validity'], 'valid')
        self.assertEqual(res['exotherms'][0]['conditions'], 'adiabatic')
        self.assertGreater(res['exotherms'][0]['delta_t_observed_k'], 60.0)
        ramp = dict(mode='ramp', heat_rate_k_min=1.0, end_temperature_k=433.15)
        res = run(loaded(load=dict(reactant_mass_fraction=0.0, gas_yield_mol_per_mol=None), method=ramp))
        self.assertEqual(res['validity'], 'no_exotherm_detected')
        temps = [p['jacket_k'] for p in res['points'] if p['stage'] == 'ramp']
        slope_ = (temps[-1] - temps[10]) / ((len(temps) - 11) * METHOD_EXAMPLE['data_interval_s'] / 60.0)
        self.assertLess(abs(slope_ - 1.0), 0.1)

    def test25b_iso_fixed_and_ramp_detect_self_heating(self):
        # Iso-Fixed at 125 degC: DTBP self-heats above the 0.02 K/min threshold; the jacket holds, the event is non-adiabatic
        res = run(loaded(method=dict(mode='iso_fixed', iso_temperature_k=398.15, iso_hours=30.0, end_temperature_k=493.15)))
        self.assertEqual(res['validity'], 'valid')
        e = res['exotherms'][0]
        self.assertEqual((e['conditions'], e['delta_t_adiabatic_k'], e['heat_released_j'], e['kinetics']['quality']), ('isothermal_jacket', None, None, 'not_adiabatic'))
        self.assertGreater(e['max_excess_over_jacket_k'], 0.3)
        # a 2 K/min ramp: the runaway overtakes the jacket, the instrument changes to adiabatic mode and resumes the ramp afterwards
        h = loaded(method=dict(mode='ramp', heat_rate_k_min=2.0, end_temperature_k=563.15))
        res = run(h)
        e = res['exotherms'][0]
        self.assertEqual(e['conditions'], 'adiabatic')
        self.assertGreater(e['delta_t_observed_k'], 40.0)
        self.assertGreater(h.sample['alpha'], 0.99)
        stages = [p['stage'] for p in res['points']]
        first = stages.index('exotherm')
        self.assertIn('ramp', stages[stages.index('ramp', first) if 'ramp' in stages[first:] else len(stages):])   # ramp resumes after the event
        self.assertEqual(res['terminal_reason'], 'RAMP_COMPLETE')

    def test26_conservation_and_unloading(self):
        h = loaded()
        res = run(h, limit_h=40.0)
        L = call(h, 'ledger.read')
        prov = call(h, 'provenance.read')
        self.assertIn(DTBP, prov['loaded_template'])
        self.assertEqual(len(prov['operations']), 1)
        self.assertLess(abs(L['energy_residual_j']), 1e-9 * max(1.0, L['heater_energy_j'] + L['reaction_heat_j']))
        self.assertLess(abs(L['reactant_residual_mol']), 1e-12 * L['reactant_loaded_mol'])
        self.assertLess(abs(L['gas_residual_mol']), 1e-12 * max(L['gas_generated_mol'], 1e-30))
        self.assertAlmostEqual(L['reaction_heat_j'], h.sample['n_r0'] * h.sample['alpha'] * h.sample['M_r'] * h.sample['dH'], delta=1e-6)
        call(h, 'lid.set', open=True); h.step(3.0)
        out = call(h, 'sample.unload')
        self.assertAlmostEqual(out['vented_gas_mol'], L['gas_generated_mol'], delta=1e-12)
        L2 = h.ledger()
        self.assertLess(abs(L2['energy_residual_j']), 1e-9 * max(1.0, L2['heater_energy_j']))
        self.assertEqual(L2['gas_in_container_mol'], 0.0)
        self.assertEqual(res['phase'], 'succeeded')

    def test27_interlocks_and_hot_unload(self):
        h = loaded()
        call(h, 'test.start')
        for k, a, code in (('tray.move', dict(extend=True), 'TEST_ACTIVE'), ('lid.set', dict(open=True), 'TEST_ACTIVE'), ('sample.unload', {}, 'TEST_ACTIVE')):
            with self.assertRaises(ActionRejected) as e:
                h.invoke(k, **a)
            self.assertEqual(str(e.exception), code)
        h.step(3 * 3600.0)
        call(h, 'test.stop')
        call(h, 'lid.set', open=True); h.step(3.0)
        self.assertGreater(h.T_s, UNLOAD_MAX_K)
        with self.assertRaises(ActionRejected) as e:
            h.invoke('sample.unload')
        self.assertEqual(str(e.exception), 'SAMPLE_TOO_HOT')
        while h.T_s > UNLOAD_MAX_K:
            h.step(600.0)
        call(h, 'sample.unload')
        h2 = loaded()
        call(h2, 'tray.move', extend=True); h2.step(0.5)
        with self.assertRaises(ActionRejected) as e:
            h2.invoke('test.start')
        self.assertEqual(str(e.exception), 'TRAY_NOT_DOCKED')
        h2.step(5.0); call(h2, 'tray.move', extend=False); h2.step(5.0)
        self.assertTrue(h2.read('agent')['tray.docked'])

    def test28_power_off_cancels_and_step_splitting(self):
        h = loaded()
        call(h, 'test.start'); h.step(3600.0)
        call(h, 'power.set', enabled=False)
        self.assertEqual(call(h, 'test.result')['validity'], 'cancelled')
        self.assertIsNone(h.read('agent')['sample.indicated_k'])
        a, b = loaded(), loaded()
        call(a, 'test.start'); call(b, 'test.start')
        a.step(6 * 3600.0)
        for _ in range(360):
            b.step(60.0)
        self.assertEqual(a.time_ns, b.time_ns)
        self.assertEqual((a.T_s, a.T_j, a.test['stage'], len(a.test['points'])), (b.T_s, b.T_j, b.test['stage'], len(b.test['points'])))   # same 1 s cycle grid
        c = loaded()
        call(c, 'test.start')
        for _ in range(int(3600 / 0.7)):
            c.step(0.7)                                                                                   # host steps off the cycle grid
        a2 = loaded(); call(a2, 'test.start'); a2.step(c.time_s - a2.time_s)
        self.assertLess(abs(a2.T_s - c.T_s), 1e-3)

    def test29_analysis_kinetics_pure_function(self):
        tk = templates()[DTBP]['kinetics']
        T0, Tf = 383.0, 473.0
        temps, rates = [], []
        for i in range(1, 60):
            T = T0 + (Tf - T0) * i / 60
            k = tk['A_per_s'] * math.exp(-tk['Ea_J_mol'] / (GAS_CONSTANT * T))
            temps.append(T); rates.append(k * (Tf - T) * 60.0)
        out = call(configured(), 'analysis.kinetics', temperatures_k=temps, self_heat_rates_k_min=rates, onset_temperature_k=T0, final_temperature_k=Tf, phi=1.48, n=1.0)
        self.assertAlmostEqual(out['activation_energy_j_mol'], tk['Ea_J_mol'], delta=1.0)
        self.assertAlmostEqual(out['log10_a_per_s'], math.log10(tk['A_per_s']), delta=1e-4)
        self.assertAlmostEqual(out['delta_t_adiabatic_k'], 1.48 * 90.0)
        with self.assertRaises(ActionRejected):
            configured().invoke('analysis.kinetics', temperatures_k=temps, self_heat_rates_k_min=rates[:-1], onset_temperature_k=T0, final_temperature_k=Tf, phi=1.48, n=1.0)

    def test30_panel_readings_carry_measurement_noise(self):
        h = loaded()
        call(h, 'test.start')
        diffs = []
        for _ in range(200):
            h.step(15.0)
            a = h.read('agent')
            self.assertEqual(a, h.read('agent'))
            diffs.append(a['sample.indicated_k'] - h.T_s)
        mean = sum(diffs) / len(diffs)
        sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / len(diffs))
        self.assertGreater(sd, 0.002)                                                             # noisy, not the quantised truth
        self.assertLess(abs(mean - PLANT_EXAMPLE['sample_tc_offset_k']), 0.005)
        self.assertLess(max(abs(d) for d in diffs), 0.05)

    def test31_metadata_exports_every_capability_and_slot(self):
        meta = export_netzsch_mmc315_contract_metadata()
        self.assertEqual(set(meta['capabilities']), set(CAPS))
        self.assertEqual(set(meta['state_slots']), set(SLOTS))
        slots = meta['state_slots']
        pre = {json.dumps(c['preconditions'], ensure_ascii=False, sort_keys=True) for c in meta['capabilities'].values() if c['preconditions']}
        self.assertGreater(len(pre), 7)
        for k, c in meta['capabilities'].items():
            validate(c['input_schema'], c['example_arguments'])
            for e in c['errors']:
                self.assertNotEqual(e['meaning_zh'], e['code'], k)
            for sid in c['observes'] + [x for p in c['preconditions'] for x in p['state_ids']]:
                self.assertIn(sid, slots, k)
            if c['visibility'] == 'agent':
                self.assertTrue(all(slots[sid]['visibility'] == 'agent' for sid in c['observes']), k)
            self.assertTrue(c['source_ids'], k)
        for k, s in slots.items():
            if s['origin'] == 'process_truth':
                self.assertIn(s['visibility'], ('evaluator', 'debug'))
        params = meta['mechanisms']['arc_calorimetry']['parameters']
        for k in ('iso_temperature_k', 'sample_cp_j_kg_k', 'heat_of_decomposition_j_kg', 'gas_yield_mol_per_mol'):
            self.assertIn(k, params)
        for k in ('sample_cp_j_kg_k', 'container_cp_j_kg_k', 'gas_yield_mol_per_mol'):
            self.assertIn('反推', params[k]['meaning_zh'], k)
        self.assertIn('TEST_ACTIVE', [e['code'] for e in meta['capabilities']['sample.unload']['errors']])
        self.assertIn('plant.configured', meta['capabilities']['sample.load']['preconditions'][0]['state_ids'])
        for k in ('test.stop', 'power.set'):
            self.assertIn('test.exotherm_count', CAPS[k]['effects'])

    def test32_end_rate_ignores_programmed_heating(self):
        inert = dict(reactant_mass_fraction=0.0, gas_yield_mol_per_mol=None)
        res = run(loaded(load=inert, method=dict(end_temperature_k=373.15, heat_rate_k_min=2.0, max_rate_k_min=1.0)))
        self.assertNotEqual(res['terminal_reason'], 'END_RATE_REACHED')
        res = run(loaded(load=inert, method=dict(mode='ramp', heat_rate_k_min=5.0, max_rate_k_min=5.0, end_temperature_k=423.15)))
        self.assertIn(res['terminal_reason'], ('END_TEMPERATURE_REACHED', 'RAMP_COMPLETE'))
        self.assertEqual(res['validity'], 'no_exotherm_detected')

    def test33_shortest_search_window_still_detects(self):
        res = run(loaded(method=dict(search_min=5.0, wait_min=15.0, end_temperature_k=503.15)))
        ref = templates()[DTBP]['arc_reference']
        e = res['exotherms'][0]
        self.assertLess(abs(e['onset_temperature_k'] - 273.15 - ref['detection_temperature_C']), 3.0)
        self.assertGreater(e['delta_t_observed_k'], 80.0)

    def test34_controller_stable_and_host_step_independent(self):
        plant = dict(jacket_heat_capacity_j_k=400.0, controller_gain_w_k=200.0)                          # loop gain exactly at the 0.5 bound
        inert = dict(reactant_mass_fraction=0.0, gas_yield_mol_per_mol=None)
        runs = []
        for dt in (60.0, 1.0):
            h = loaded(load=inert, plant=plant, method=dict(end_temperature_k=373.15))
            runs.append((run(h, dt=dt), h))
        for res, h in runs:
            self.assertEqual(res['validity'], 'no_exotherm_detected')
        self.assertEqual(runs[0][0], runs[1][0])                                                          # identical record: 1 s cycle, not host steps

    def test35_pressure_continuous_across_tc_and_readings_saturate(self):
        h = loaded(load=dict(reactant_mass_fraction=0.0, gas_yield_mol_per_mol=None))
        Tc = LIB['solvents']['toluene']['critical_temperature_K']
        lo, hi = h._pressure_true(Tc - 1e-4), h._pressure_true(Tc + 1e-4)
        self.assertLess(abs(hi / lo - 1), 1e-5)
        self.assertEqual(h._ind_p(400e5), 150e5)
        self.assertEqual(h._ind_T(2000.0), TC_RANGE_MAX_K)

    def test36_overtemperature_cut_off(self):
        load = dict(sample_mass_kg=3.0e-3, reactant_mass_fraction=1.0, solvent='none', liquid_density_kg_m3=796.0, sample_cp_j_kg_k=2000.0,
                    heat_of_decomposition_j_kg=2.5e6, gas_yield_mol_per_mol=0.1)
        res = run(loaded(load=load, plant=CALIBRATED, method=dict(end_temperature_k=673.15, max_rate_k_min=50.0, max_pressure_pa=MAX_PRESSURE_PA)))
        self.assertIn(res['terminal_reason'], ('TEMPERATURE_LIMIT_EXCEEDED', 'END_RATE_REACHED', 'END_TEMPERATURE_REACHED'))
        h = loaded(load=load, plant=CALIBRATED, method=dict(end_temperature_k=673.15, max_rate_k_min=50.0, max_pressure_pa=MAX_PRESSURE_PA))
        call(h, 'test.start'); h.step(2.0)
        h.T_s = OVERTEMPERATURE_K + 1.0                                                                   # white-box: force the cut-off path
        h.step(2.0)
        r = call(h, 'test.result')
        self.assertEqual((r['phase'], r['validity'], r['terminal_reason']), ('failed', 'temperature_limit_exceeded', 'TEMPERATURE_LIMIT_EXCEEDED'))

    def test37_exotherm_overflow_is_counted(self):
        h = loaded()
        call(h, 'test.start'); h.step(5.0)
        for _ in range(MAX_EXOTHERMS + 3):                                                                # white-box: open/close more events than recorded
            h._open_exotherm(h._meas_Ts(), h._meas_p(), 'adiabatic'); h._close_exotherm(complete=True)
        h.test['stage'] = 'heat'
        r = call(h, 'test.result')
        self.assertEqual((len(r['exotherms']), r['exotherm_count'], r['exotherms_dropped']), (MAX_EXOTHERMS, MAX_EXOTHERMS + 3, 3))
        self.assertEqual(h.read('agent')['test.exotherm_count'], MAX_EXOTHERMS + 3)

    def test38_long_hws_reduces_data_instead_of_failing(self):
        inert = dict(reactant_mass_fraction=0.0, gas_yield_mol_per_mol=None, solvent='none')             # non-volatile inert: no solvent pressure at 500 degC
        res = run(loaded(load=inert, method=dict(start_temperature_k=323.15, end_temperature_k=773.15, data_interval_s=5.0)), limit_h=80.0)
        self.assertEqual((res['phase'], res['validity']), ('succeeded', 'no_exotherm_detected'))
        self.assertLessEqual(len(res['points']), MAX_POINTS)
        self.assertGreater(res['data_interval_s'], 5.0)

    def test39_tracking_follows_at_most_50_k_per_min(self):
        load = dict(sample_mass_kg=1.0e-3, reactant_mass_fraction=1.0, solvent='none', container_mass_kg=4.0e-3, gas_yield_mol_per_mol=0.3)
        h = loaded(load=load, plant=dict(CALIBRATED, jacket_heater_max_w=5000.0), method=dict(end_temperature_k=673.15, max_rate_k_min=50.0, max_pressure_pa=MAX_PRESSURE_PA))
        call(h, 'test.start')
        hist, fastest = [], 0.0
        while h._test_active() and h.time_s < 40 * 3600:
            h.step(5.0)
            hist.append((h.time_s, h.T_j))
            if len(hist) > 6:
                t0, T0 = hist[-7]
                fastest = max(fastest, (h.T_j - T0) / (h.time_s - t0) * 60.0)
        self.assertLess(fastest, 50.0 * 1.05)
        self.assertGreater(fastest, 20.0)

    def test40_iso_track_detection_not_early(self):
        tpl = templates()[DTBP]
        quiet = dict(tc_noise_k=0.0, pressure_noise_pa=0.0, sample_tc_offset_k=0.0, jacket_tc_offset_k=0.0)
        # quiet plant: conversion during the slow approach (~8 %) and one search window put detection ~1 K above the zero-conversion threshold;
        # default plant: the thermocouple offset mismatch heats the sample by ~0.003 K/min and detects ~1 K earlier
        for plant, lo, hi in ((quiet, 0.1, 1.5), (None, 1.5, 3.0)):
            h = loaded(plant=plant, method=dict(mode='iso_track', iso_temperature_k=373.15, iso_hours=40.0, end_temperature_k=493.15))
            res = run(h)
            s = h.sample
            T_thr = threshold_temperature(tpl, s['m_r'] * s['dH'] / s['C_s'], METHOD_EXAMPLE['threshold_k_min'])
            on = res['exotherms'][0]['onset_temperature_k']
            self.assertTrue(T_thr - lo <= on <= T_thr + hi, (plant, on, T_thr))

    def test41_jacket_mode_programme_and_sealed_air(self):
        h = loaded()
        p0 = h.read('agent')['pressure.indicated_pa']
        self.assertLess(abs(p0 - PLANT_EXAMPLE['ambient_pressure_pa'] - PLANT_EXAMPLE['pressure_offset_pa']), 3 * PLANT_EXAMPLE['pressure_noise_pa'] + 1000.0)
        call(h, 'test.start')
        seen = {}
        while h._test_active() and h.time_s < 12 * 3600:
            h.step(30.0)
            a = h.read('agent')
            seen.setdefault(a['test.stage'], set()).add((a['jacket.mode'], a['test.step_target_k'] is None))
        self.assertEqual(seen['search'], {('adiabatic_tracking', False)})
        self.assertEqual(seen['wait'], {('isothermal_hold', False)})
        self.assertTrue(seen['exotherm'] <= {('adiabatic_tracking', True)})

    def test43_iso_time_enforced_during_events(self):
        h = loaded(method=dict(mode='iso_fixed', iso_temperature_k=398.15, iso_hours=2.0, end_temperature_k=493.15))
        call(h, 'test.start')
        start = h.time_s
        while h._test_active() and h.time_s - start < 40 * 3600:
            h.step(60.0)
            if h.test['stage'] == 'cool':
                break
        self.assertEqual(h.test['end_reason'], 'ISO_TIME_ELAPSED')
        self.assertLess(h.time_s - start, 2.0 * 3600 + 120.0)
        r = call(h, 'test.result')
        self.assertTrue(r['exotherms'] and r['exotherms'][0]['conditions'] == 'isothermal_jacket')

    def test44_iso_approach_detects_runaways(self):
        # at 150 degC DTBP runs away while the jacket is still approaching / settling at the iso temperature
        res = run(loaded(method=dict(mode='iso_track', iso_temperature_k=423.15, iso_hours=6.0, end_temperature_k=563.15)))
        e = res['exotherms'][0]
        self.assertEqual(e['conditions'], 'adiabatic')
        self.assertGreater(e['delta_t_observed_k'], 40.0)
        h = loaded(method=dict(mode='iso_fixed', iso_temperature_k=423.15, iso_hours=6.0, end_temperature_k=563.15))
        res = run(h)
        e = res['exotherms'][0]
        self.assertEqual(e['conditions'], 'isothermal_jacket')
        self.assertGreater(e['max_excess_over_jacket_k'], 5.0)                                         # the peak, not only the tail
        self.assertGreater(e['delta_t_observed_k'], 0.5)

    def test45_hws_heat_step_runaway_is_recorded(self):
        load = dict(sample_mass_kg=1.0e-3, reactant_mass_fraction=1.0, solvent='none', container_mass_kg=4.0e-3, gas_yield_mol_per_mol=0.3)
        res = run(loaded(load=load, plant=CALIBRATED, method=dict(start_temperature_k=433.15, end_temperature_k=673.15, max_rate_k_min=50.0, max_pressure_pa=MAX_PRESSURE_PA)))
        self.assertTrue(res['exotherms'], res['terminal_reason'])
        self.assertEqual(res['exotherms'][0]['conditions'], 'adiabatic')
        self.assertLess(res['exotherms'][0]['onset_temperature_k'], 433.15)                            # detected during the first heat step

    def test46_lab_ambient_and_owner_flag_per_role(self):
        h = loaded()
        h.register_principal('a1', 'agent'); h.register_principal('ev', 'evaluator')
        a1, ev = h.session('a1'), h.session('ev')
        val = lambda s_, k: s_.read(instance_id='mmc315_01', state_ids=[k])['observations'][k]['value']  # noqa: E731
        self.assertEqual(val(a1, 'lab.ambient_k'), PLANT_EXAMPLE['ambient_k'])
        self.assertIsNone(NetzschMmc315Runtime().read('agent')['lab.ambient_k'])
        a1.invoke(instance_id='mmc315_01', capability_id='test.start', arguments={}, request_id='go')
        h.step(60.0)
        self.assertEqual((val(a1, 'test.started_by_other_agent'), val(ev, 'test.started_by_other_agent')), (False, False))
        self.assertEqual(ev.invoke(instance_id='mmc315_01', capability_id='test.stop', arguments={}, request_id='st')['status'], 'succeeded')

    def test47_noise_bound_and_no_false_events(self):
        with self.assertRaises(ActionRejected) as e:
            NetzschMmc315Runtime().invoke('plant.configure', **dict(PLANT_EXAMPLE, tc_noise_k=0.03))
        self.assertEqual(str(e.exception), 'INVALID_ARGUMENT')
        inert = dict(reactant_mass_fraction=0.0, gas_yield_mol_per_mol=None)
        for seed in (1, 2, 3):
            plant = dict(tc_noise_k=TC_NOISE_MAX_K, noise_seed=seed, sample_tc_offset_k=0.01, jacket_tc_offset_k=-0.01)     # noise and pair at their bounds
            for method in (dict(end_temperature_k=383.15), dict(mode='iso_fixed', iso_temperature_k=398.15, iso_hours=3.0, end_temperature_k=493.15)):
                h = loaded(load=inert, plant=plant, method=method)
                lim = h.read('agent')['test.detection_limit_k_min']
                call(h, 'method.configure', **dict(METHOD_EXAMPLE, **method, threshold_k_min=1.05 * lim))                  # the noisiest accepted plant
                res = run(h, limit_h=20.0)
                self.assertEqual((res['phase'], res['validity']), ('succeeded', 'no_exotherm_detected'), (seed, method))

    def test48_detection_limit_uses_plant_sample_and_search(self):
        strong = dict(sample_jacket_conductance_w_k=2.0, jacket_heater_max_w=5000.0)
        for pair in (dict(sample_tc_offset_k=0.01, jacket_tc_offset_k=-0.01), dict(sample_tc_offset_k=0.0, jacket_tc_offset_k=0.0)):
            h = loaded(plant=dict(strong, **pair))                                                        # G_sj/C_s amplifies drift and injected noise
            self.assertGreater(h.read('agent')['test.detection_limit_k_min'], METHOD_EXAMPLE['threshold_k_min'])
            with self.assertRaises(ActionRejected) as e:
                h.invoke('test.start')
            self.assertEqual(str(e.exception), 'THRESHOLD_BELOW_DETECTION_LIMIT')
        h = loaded()
        lim = h.read('agent')['test.detection_limit_k_min']
        self.assertAlmostEqual(lim, detection_limit_k_min(PLANT_EXAMPLE, h.sample['C_s'], METHOD_EXAMPLE['search_min']), places=12)
        self.assertLess(lim, METHOD_EXAMPLE['threshold_k_min'])                                          # the NETZSCH example is usable
        for f, ok in ((0.999, False), (1.001, True)):                                                   # the slot is exactly the refusal boundary
            call(h, 'method.configure', **dict(METHOD_EXAMPLE, threshold_k_min=f * lim))
            if ok:
                call(h, 'test.start'); self.assertTrue(h._test_active())
            else:
                with self.assertRaises(ActionRejected) as e:
                    h.invoke('test.start')
                self.assertEqual(str(e.exception), 'THRESHOLD_BELOW_DETECTION_LIMIT')

    def test51_slope_standard_error_matches_tracking_data(self):
        # stationary adiabatic storage of an inert sample: the spread of 180 s regression slopes follows the analytic standard error
        import statistics
        plant = dict(tc_noise_k=0.003, sample_jacket_conductance_w_k=2.0, jacket_heater_max_w=5000.0, sample_tc_offset_k=0.0, jacket_tc_offset_k=0.0)
        h = loaded(load=dict(reactant_mass_fraction=0.0, gas_yield_mol_per_mol=None), plant=plant,
                   method=dict(mode='iso_track', iso_temperature_k=348.15, iso_hours=30.0, end_temperature_k=493.15, threshold_k_min=1.0, search_min=240.0))
        call(h, 'test.start'); h.step(2 * 3600)
        t0, buf = h.time_s, []
        for _ in range(4 * 60):
            h.step(60.0)
            buf.extend(q for q in h.rate_buf if q[0] > (buf[-1][0] if buf else t0))
        sl = [slope(buf[i:i + 180]) for i in range(0, len(buf) - 180, 180)]
        sd = statistics.pstdev(sl)
        analytic = slope_standard_error(0.003, 2.0, h.sample['C_s'], 180.0) / INJECTION_FACTOR * 1.0
        pure = math.sqrt(24 * 0.003 ** 2 * TC_NOISE_TAU_S / 180.0 ** 3 + 0.01 ** 2 / 180.0 ** 3 + (2.0 / h.sample['C_s'] * 2 * 0.003 * math.sqrt(TC_NOISE_TAU_S / 180.0)) ** 2)
        self.assertTrue(0.7 * pure < sd < 1.3 * pure, (sd, pure, analytic))
        self.assertGreater(slope_standard_error(0.003, 2.0, h.sample['C_s'], 180.0), sd)                # the limit uses a larger (safe) value

    def test50_iso_fixed_event_ends_with_a_pair_at_the_bound(self):
        plant = dict(tc_noise_k=TC_NOISE_MAX_K, sample_tc_offset_k=0.01, jacket_tc_offset_k=-0.01)
        # at 150 degC DTBP is consumed within a few hours (at 125 degC the tail lasts > 20 h, so an event there rightly stays open)
        h = loaded(plant=plant, method=dict(mode='iso_fixed', iso_temperature_k=423.15, iso_hours=12.0, end_temperature_k=563.15, threshold_k_min=0.1))
        res = run(h)
        e = res['exotherms'][0]
        self.assertTrue(e['complete'])                                                                   # ends on its own, well before the 12 h
        self.assertGreater(h.sample['alpha'], 0.999)
        self.assertEqual(res['terminal_reason'], 'ISO_TIME_ELAPSED')

    def test49_iso_track_runaway_past_the_ageing_time_completes(self):
        h = loaded(method=dict(mode='iso_track', iso_temperature_k=398.15, iso_hours=1.0, end_temperature_k=493.15))
        res = run(h)
        e = res['exotherms'][0]
        self.assertTrue(e['complete'])
        self.assertEqual((e['conditions'], e['kinetics']['quality']), ('adiabatic', 'ok'))
        self.assertGreater(e['delta_t_observed_k'], 60.0)
        self.assertEqual(res['terminal_reason'], 'ISO_TIME_ELAPSED')

    def test42_weak_exotherm_fit_is_flagged(self):
        load = dict(reactant_mass_fraction=0.004)
        res = run(loaded(load=load, method=dict(end_temperature_k=503.15)))
        qualities = {e['kinetics']['quality'] for e in res['exotherms']}
        self.assertTrue(not res['exotherms'] or qualities - {'ok'} or all(40e3 <= e['kinetics']['activation_energy_j_mol'] <= 400e3 for e in res['exotherms']))


if __name__ == '__main__':
    unittest.main()
