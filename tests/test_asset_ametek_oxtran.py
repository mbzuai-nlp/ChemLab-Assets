"""CHEM-147 AMETEK MOCON OX-TRAN 2/22 L runtime tests: python3 -m unittest tests/test_asset_ametek_oxtran.py

Themes (pipeline.md): setpoints only write targets; read/describe/poll are pure; rejected preconditions leave the state
unchanged; two instances are isolated; agent/evaluator truth isolation and result redaction; O2 mass and heat conservation;
real return values validate against the contract schemas; async completion / cancellation residue.
Physics: Faraday coulometry (1 cc/day = 0.000199 A), Fickian time lag and steady state against the analytical solution,
Arrhenius and humidity transfer, barometric and permeant normalisation, ReZero baseline, individual zero, masks,
over-range protection, drawer/clamp interlocks, thermoelectric heat-up against the manufacturer order of magnitude.
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
from scripts.asset_runtime.ametek_oxtran import *  # noqa: E402,F401,F403
from scripts.asset_runtime import ametek_oxtran as rt  # noqa: E402

PROCESS_EVIDENCE = {}
CHECKS = []
_spec = importlib.util.spec_from_file_location('oxtran_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
asset_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(asset_tool)
_ispec = importlib.util.spec_from_file_location('oxtran_interface', ROOT / 'outputs/expansion_batch04/t1_thermal/ametek_oxtran/interface_definition.py')
interface = importlib.util.module_from_spec(_ispec)
_ispec.loader.exec_module(interface)
PET = 'poly_ethylene_terephthalate'
T23 = 296.15


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


def configured(plant=None, gas=None, inst=None, cfg=None, cells=('A',)):
    h = AmetekOxtranRuntime()
    call(h, 'plant.configure', **dict(PLANT_EXAMPLE, **(plant or {})))
    call(h, 'gas.configure', **dict(GAS_EXAMPLE, **(gas or {})))
    call(h, 'instrument.configure', **dict(INSTRUMENT_EXAMPLE, **(inst or {})))
    for c in cells:
        call(h, 'test.configure', **dict(TEST_CFG_EXAMPLE, cell=c, **(cfg or {})))
    call(h, 'power.set', enabled=True)
    return h


def loaded(template=PET, thickness=100e-6, cell='A', h=None, mount=None, clamp=True, settle=3600.0, **kw):
    h = h or configured(**kw)
    call(h, 'drawer.move', cell=cell, open=True)
    h.step(3.0)
    call(h, 'sample.mount', **dict(MOUNT_EXAMPLE, cell=cell, template_id=template, thickness_m=thickness, **(mount or {})))
    call(h, 'drawer.move', cell=cell, open=False)
    h.step(3.0)
    if clamp:
        call(h, 'cell.load_unload', cell=cell)
        h.step(settle)
    return h


def run_test(h, cell='A', limit_h=200.0, dt=1800.0):
    call(h, 'test.start', cell=cell)
    t = 0.0
    while h.cells[cell]['test']['phase'] == 'running' and t < limit_h * 3600:
        call(h, 'sim.step', dt_s=dt)
        t += dt
    return call(h, 'test.result', cell=cell)


def steady_otr(template, thickness, T=T23 - PLANT_EXAMPLE['temperature_sensor_bias_k'], rh=0.0, o2=0.999, baro=101325.0, E=45000.0, beta=0.0):
    rec = library()[template]
    f = math.exp(-E / GAS_CONSTANT * (1 / T - 1 / rec['reference_temperature_K']) + beta * rh)
    pw = rh * saturation_pressure_pa(T)
    return rec['oxygen_permeability_mol_m_per_m2_s_Pa'] * f * o2 * (baro - pw) / thickness


class ConstantsAndLibrary(unittest.TestCase):
    def test01_identity_and_documented_constants(self):
        self.assertEqual(ASSET_TYPE_ID, 'instrument.ametek_oxtran')
        self.assertEqual(CATALOG_ID, 'CHEM-147')
        self.assertAlmostEqual(AMP_PER_CC_DAY, 0.000199, delta=0.0000005)             # manual appendix D
        self.assertEqual(TEMPERATURE_RANGE_K, (283.15, 313.15))
        self.assertAlmostEqual(TEST_AREA_M2, 50e-4)
        self.assertAlmostEqual(MAX_FILM_THICKNESS_M, 3.048e-3)
        self.assertEqual(OTR_RANGE_CC_M2_DAY, (0.005, 200.0))
        self.assertAlmostEqual(SENSOR_RANGE_MOL_S * CC_DAY_PER_MOL_S, 1.0)             # 200 cc/(m2 day) x 50 cm2 = 1 cc/day

    def test02_interface_geometry_agrees_with_runtime(self):
        self.assertAlmostEqual(interface.DRAWER_TRAVEL_M, DRAWER_TRAVEL_M)
        self.assertAlmostEqual(interface.CLAMP_TRAVEL_M, CLAMP_TRAVEL_M)
        self.assertAlmostEqual(interface.POWER_SWITCH_ON_RAD, POWER_SWITCH_ON_RAD)
        self.assertAlmostEqual(interface.TEST_AREA_M2, TEST_AREA_M2)
        for part, sid, capid, *_ in interface.MOTION:
            self.assertIn(sid, SLOTS)
            self.assertIn(capid, CAPS)
        for r in interface.READOUTS:
            self.assertIn(r['source']['bridge'][0], SLOTS)
            self.assertEqual(SLOTS[r['source']['bridge'][0]]['visibility'], 'agent')
        for lamp in interface.LAMPS:
            self.assertIn(lamp['source']['lamp']['bridge'], SLOTS)
        for key, (capid, args) in interface.KEY_COMMANDS.items():
            validate(CAPS[capid]['input_schema'], args)
        self.assertAlmostEqual(interface.OTR_DISPLAY_SCALE, CC_DAY_PER_MOL_S)

    def test03_template_library_real_and_complete(self):
        lib = json.loads(LIBRARY_PATH.read_text())
        self.assertGreaterEqual(lib['coverage']['template_count'], 50)
        self.assertGreaterEqual(lib['coverage']['with_measured_diffusivity'], 50)
        self.assertEqual(lib['coverage']['synthetic_records'], 0)
        for t in lib['templates']:
            self.assertEqual(t['evidence'], 'measured_literature')
            self.assertTrue(t['source']['sha256'] and t['measurements'])
            self.assertGreater(t['oxygen_permeability_mol_m_per_m2_s_Pa'], 0)
        pet = library()[PET]
        self.assertAlmostEqual(pet['oxygen_permeability_barrer'], 0.11, places=6)
        report = json.loads((ROOT / 'outputs/expansion_batch04/t1_thermal/ametek_oxtran/independent_permeation_source_validation.json').read_text())
        self.assertEqual(report['status'], 'passed')

    def test04_film_step_is_exactly_conservative_and_converges_to_linear_profile(self):
        c = [5.0] * 20
        dx = 1e-6
        total0 = sum(c) * dx
        jt_sum = jb_sum = 0.0
        for _ in range(4000):
            c, jt, jb = rt.film_step(c, dx, 1e-12, 10.0, 0.0, 0.5)
            jt_sum += jt * 0.5
            jb_sum += jb * 0.5
        self.assertAlmostEqual(sum(c) * dx - total0, jt_sum - jb_sum, delta=1e-12 * total0)
        L = 20 * dx
        self.assertAlmostEqual(jb, 1e-12 * 10.0 / L, delta=1e-9 * 1e-12 * 10.0 / L)

    def test05_is_stable_requires_step_and_window_drift(self):
        self.assertTrue(rt.is_stable([1.0, 1.0, 1.001, 1.0], 3, 1.0, 0.0))
        self.assertFalse(rt.is_stable([1.0, 1.006, 1.012, 1.018], 3, 1.0, 0.0))     # every step < 1 % but drift 1.8 %
        self.assertFalse(rt.is_stable([1.0, 1.0], 3, 1.0, 0.0))


class RuntimeContract(unittest.TestCase):
    def test10_setpoints_only_write_targets(self):
        h = configured()
        T0 = h.T
        call(h, 'thermal.set_temperature', target_k=313.15)
        call(h, 'humidity.set', rh_fraction=0.5)
        self.assertEqual(h.T, T0)
        self.assertEqual(h.rh_model, 0.0)
        self.assertEqual(h.read('agent')['thermal.target_k'], 313.15)
        call(h, 'sim.step', dt_s=600.0)
        self.assertGreater(h.T, T0 + 3.0)
        self.assertGreater(h.rh_model, 0.3)

    def test11_read_describe_poll_do_not_advance_time(self):
        h = loaded()
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r = s.invoke(instance_id='oxtran_01', capability_id='test.start', arguments={'cell': 'A'}, request_id='x1')
        t0, rev = h.time_ns, h.revision
        before = copy.deepcopy(h.read('debug'))
        for _ in range(3):
            s.read(instance_id='oxtran_01')
            s.describe(instance_id='oxtran_01')
            s.poll(r['operation_id'])
            call(h, 'test.result', cell='A')
            call(h, 'analysis.otr', **CAPS['analysis.otr']['example_arguments'])
        self.assertEqual((h.time_ns, h.revision), (t0, rev))
        self.assertEqual(before, h.read('debug'))

    def test12_rejections_leave_state_unchanged(self):
        h = configured()
        cases = [('test.start', dict(cell='A'), 'CELL_NOT_CLAMPED'), ('sample.mount', dict(MOUNT_EXAMPLE), 'DRAWER_NOT_OPEN'),
                 ('sample.unmount', dict(cell='A'), 'NO_FILM'), ('humidity.set', dict(rh_fraction=0.02), 'RH_OUTSIDE_CONTROLLED_RANGE'),
                 ('test.stop', dict(cell='A'), 'NOT_RUNNING'),
                 ('instrument.configure', dict(INSTRUMENT_EXAMPLE, barometer_mode='manual', manual_barometer_pa=None), 'MANUAL_BAROMETER_REQUIRED'),
                 ('plant.configure', dict(PLANT_EXAMPLE), 'PLANT_ALREADY_CONFIGURED'), ('package.test', {}, 'UNSUPPORTED')]
        for k, a, code in cases:
            before = copy.deepcopy(h.read('debug'))
            with self.assertRaises(ActionRejected) as e:
                h.invoke(k, **a)
            self.assertEqual(str(e.exception), code, k)
            self.assertEqual(before, h.read('debug'), k)
        with self.assertRaises(ActionRejected):
            h.invoke('thermal.set_temperature', target_k=320.0)
        with self.assertRaises(ActionRejected):
            h.invoke('test.configure', **dict(TEST_CFG_EXAMPLE, cycles=True))

    def test13_two_instances_isolated(self):
        a = loaded()
        b = configured()
        call(a, 'thermal.set_temperature', target_k=303.15)
        a.step(3600.0)
        self.assertAlmostEqual(b.T, PLANT_EXAMPLE['ambient_k'])
        self.assertIsNone(b.cells['A']['film'])
        self.assertIsNotNone(a.cells['A']['film'])

    def test14_agent_truth_isolation_and_redaction(self):
        h = loaded()
        h.register_principal('agent', 'agent'); h.register_principal('eval', 'evaluator')
        ag, ev = h.session('agent'), h.session('eval')
        obs = ag.read(instance_id='oxtran_01')['observations']
        self.assertTrue(all(SLOTS[k]['visibility'] == 'agent' for k in obs))
        self.assertFalse(any(k.startswith('truth.') for k in obs))
        with self.assertRaises(ActionRejected):
            ag.read(instance_id='oxtran_01', state_ids=['truth.cell_a.flux_mol_s'])
        for k in ('ledger.read', 'provenance.read', 'sample.mount', 'drawer.move', 'sim.step', 'plant.configure'):
            self.assertEqual(ag.invoke(instance_id='oxtran_01', capability_id=k, arguments=CAPS[k]['example_arguments'], request_id='r' + k)['error']['code'], 'ACCESS_DENIED')
        r = ag.invoke(instance_id='oxtran_01', capability_id='test.start', arguments={'cell': 'A'}, request_id='s1')
        h.step(8 * 3600.0)
        res = ag.invoke(instance_id='oxtran_01', capability_id='test.result', arguments={'cell': 'A'}, request_id='q1')['result']
        self.assertNotIn(PET, json.dumps(res))
        self.assertIn('evaluator provenance.read', res['template_source'])
        prov = ev.invoke(instance_id='oxtran_01', capability_id='provenance.read', arguments={}, request_id='p1')['result']
        self.assertIn(PET, prov['cell_a'])
        self.assertEqual(prov['operations'][0]['operation_id'], r['operation_id'])
        desc = ag.describe(instance_id='oxtran_01')
        self.assertFalse(any(v['visibility'] != 'agent' for v in desc['capabilities'].values()))

    def test15_foreign_agent_guard_and_shared_panel_tests(self):
        h = loaded()
        for p in ('a1', 'a2'):
            h.register_principal(p, 'agent')
        h.register_principal('panel', 'evaluator')
        s1, s2, panel = h.session('a1'), h.session('a2'), h.session('panel')
        s1.invoke(instance_id='oxtran_01', capability_id='test.start', arguments={'cell': 'A'}, request_id='x')
        h.step(3600.0)
        for k, a in [('test.stop', {'cell': 'A'}), ('test.result', {'cell': 'A'}), ('cell.load_unload', {'cell': 'A'}), ('thermal.set_temperature', {'target_k': 300.0}),
                     ('humidity.set', {'rh_fraction': 0.0}), ('power.set', {'enabled': False}), ('instrument.configure', dict(INSTRUMENT_EXAMPLE))]:
            self.assertEqual(s2.invoke(instance_id='oxtran_01', capability_id=k, arguments=a, request_id='y' + k)['error']['code'], 'ACCESS_DENIED', k)
        self.assertEqual(s1.invoke(instance_id='oxtran_01', capability_id='test.stop', arguments={'cell': 'A'}, request_id='z')['status'], 'succeeded')
        # a panel/host-started test is visible to every operator
        call(h, 'test.start', cell='A')
        h._owners[h.cells['A']['test']['op']] = 'panel'
        self.assertEqual(s2.invoke(instance_id='oxtran_01', capability_id='test.result', arguments={'cell': 'A'}, request_id='w')['status'], 'succeeded')

    def test16_request_dedupe_conflict_and_write_via(self):
        h = configured()
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r1 = s.invoke(instance_id='oxtran_01', capability_id='thermal.set_temperature', arguments={'target_k': 300.0}, request_id='same')
        r2 = s.invoke(instance_id='oxtran_01', capability_id='thermal.set_temperature', arguments={'target_k': 300.0}, request_id='same')
        self.assertEqual(r1, r2)
        self.assertEqual(s.invoke(instance_id='oxtran_01', capability_id='thermal.set_temperature', arguments={'target_k': 301.0}, request_id='same')['error']['code'], 'REQUEST_CONFLICT')
        w = s.write(instance_id='oxtran_01', state_id='humidity.target_fraction', value=0.5, request_id='w1')
        self.assertEqual(w['status'], 'succeeded')
        self.assertEqual(h.rh_target, 0.5)
        with self.assertRaises(ActionRejected):
            s.write(instance_id='oxtran_01', state_id='cell_a.phase', value='idle', request_id='w2')

    def test17_poll_and_cancel_keep_partial_readings(self):
        h = loaded()
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        op = s.invoke(instance_id='oxtran_01', capability_id='test.start', arguments={'cell': 'A'}, request_id='s')['operation_id']
        h.step(4 * 3600.0)
        self.assertEqual(s.poll(op)['status'], 'running')
        n = len(h.cells['A']['test']['readings'])
        self.assertGreater(n, 0)
        c = s.cancel(op, 'c1')
        self.assertEqual(c['status'], 'succeeded')
        self.assertEqual(s.cancel(op, 'c1'), c)
        res = s.poll(op)
        self.assertEqual(res['status'], 'cancelled')
        self.assertEqual(len(res['result']['readings']), n)
        self.assertEqual(res['result']['validity'], 'cancelled')
        h.step(3600.0)
        self.assertEqual(h.cells['A']['p_up'] < 1.0, True)          # test gas switched back to carrier flushing
        with self.assertRaises(ActionRejected):
            s.cancel(op, 'c2')

    def test18_unsupported_options_are_explicit(self):
        h = configured()
        for k in UNSUPPORTED:
            with self.assertRaises(ActionRejected) as e:
                h.invoke(k)
            self.assertEqual(str(e.exception), 'UNSUPPORTED')


class Physics(unittest.TestCase):
    def test20_heatup_matches_manufacturer_order_of_magnitude(self):
        h = configured()
        call(h, 'thermal.set_temperature', target_k=313.15)
        t = 0.0
        while h.T + PLANT_EXAMPLE['temperature_sensor_bias_k'] < T23 + 0.99 * (313.15 - T23):
            h.step(30.0); t += 30.0
        self.assertLess(t, 6 * 3600.0)                     # not an order of magnitude slower than ~60 min (2/28 manual)
        self.assertGreater(t, 20 * 60.0)
        h.step(3 * 3600.0)
        bias = PLANT_EXAMPLE['temperature_sensor_bias_k']
        self.assertLess(abs(h.T + bias - 313.15), 0.02)                # controlled on the (biased) sensor
        self.assertLess(abs(h.read('agent')['thermal.indicated_k'] - 313.15), 0.02)
        call(h, 'thermal.set_temperature', target_k=283.15)  # thermoelectric cooling below ambient
        h.step(4 * 3600.0)
        self.assertLess(abs(h.T + bias - 283.15), 0.05)
        L = h.ledger()
        self.assertLess(abs(L['energy_residual_j']), 1e-6 * max(1.0, abs(L['tec_heat_j'])))

    def test21_pet_time_lag_and_steady_state_vs_analytical(self):
        h = loaded()
        res = run_test(h)['result'] if False else run_test(h)
        exams = [r['otr_mol_m2_s'] for r in res['readings'] if r['kind'] == 'exam']
        target = steady_otr(PET, 100e-6)
        self.assertEqual(res['validity'], 'valid')
        self.assertLess(exams[0], 0.7 * target)            # the first exam still sits on the time-lag rise
        self.assertLess(abs(res['final_otr_mol_m2_s'] - target) / target, 0.01)
        self.assertTrue(res['converged'] and res['within_repeatability_spec'])
        self.assertAlmostEqual(res['permeance_mol_m2_s_pa'], res['final_otr_mol_m2_s'] / STD_PRESSURE_PA)
        self.assertAlmostEqual(res['permeability_mol_m_per_m2_s_pa'], res['permeance_mol_m2_s_pa'] * 100e-6)
        PROCESS_EVIDENCE['representative_pet_100um'] = dict(final_cc_m2_day=res['final_otr_cc_m2_day'], expected_cc_m2_day=target * CC_DAY_PER_MOL_S,
                                                           exams=len(exams), individual_zero=res['individual_zero_mol_m2_s'])

    def test22_quantisation_resolution_and_sensor_counts(self):
        h = loaded()
        res = run_test(h)
        for r in res['readings']:
            q = r['otr_cc_pkg_day'] / (OTR_RESOLUTION_CC_M2_DAY * TEST_AREA_M2)
            self.assertAlmostEqual(q, round(q), places=6)
            self.assertIsInstance(r['sensor_counts'], int)
        self.assertEqual(h.read('agent')['sensor.raw_counts'], int(round(h._measured_current() / COUNT_A)))   # measured (noisy) current

    def test23_faraday_sensor_current_matches_input(self):
        h = loaded()
        call(h, 'test.start', cell='A')
        h.step(6 * 3600.0)
        while h.route != 'A':
            h.step(60.0)
        h.step(29 * 60.0)
        I_ss = 4 * FARADAY * PLANT_EXAMPLE['sensor_efficiency'] * h.sensor_in + PLANT_EXAMPLE['sensor_background_a']
        self.assertLess(abs(h.sensor_I - I_ss) / I_ss, 0.01)

    def test24_rezero_baseline_is_subtracted(self):
        nz = dict(individual_zero='none')
        base = run_test(loaded(gas=dict(carrier_o2_impurity_fraction=2e-6), cfg=nz))
        dirty = run_test(loaded(gas=dict(carrier_o2_impurity_fraction=5e-4), cfg=nz))      # large carrier residual: cancels via ReZero
        self.assertGreater(dirty['rezero_readings'][0]['sensor_counts'], 20 * base['rezero_readings'][0]['sensor_counts'])
        self.assertLess(abs(dirty['final_otr_mol_m2_s'] - base['final_otr_mol_m2_s']) / base['final_otr_mol_m2_s'], 0.02)
        noz = run_test(loaded(gas=dict(carrier_o2_impurity_fraction=5e-4), inst=dict(rezero_enabled=False), cfg=nz))
        self.assertGreater(noz['final_otr_mol_m2_s'], dirty['final_otr_mol_m2_s'] + 5 * REPEATABILITY_CC_M2_DAY / CC_DAY_PER_MOL_S)

    def test25_no_catalyst_removal_without_hydrogen(self):
        h1 = loaded(gas=dict(carrier_h2_fraction=0.02))
        h2 = loaded(gas=dict(carrier_h2_fraction=0.0))
        self.assertAlmostEqual(h1._carrier_residual_fraction(), 2e-6 * (1 - PLANT_EXAMPLE['catalyst_removal_efficiency']))
        self.assertAlmostEqual(h2._carrier_residual_fraction(), 2e-6)

    def test26_barometric_and_permeant_normalisation(self):
        ref = run_test(loaded())
        alt = run_test(loaded(plant=dict(barometric_pa=85000.0)))
        self.assertLess(abs(alt['final_otr_mol_m2_s'] - ref['final_otr_mol_m2_s']) / ref['final_otr_mol_m2_s'], 0.015)   # normalised to 760 mmHg
        air = run_test(loaded(gas=dict(test_o2_fraction=0.2095), inst=dict(permeant_concentration_percent=20.95)))
        self.assertLess(abs(air['final_otr_mol_m2_s'] - ref['final_otr_mol_m2_s']) / ref['final_otr_mol_m2_s'], 0.03)
        wrong = run_test(loaded(gas=dict(test_o2_fraction=0.2095)))                    # air supplied but 100 % declared
        self.assertLess(wrong['final_otr_mol_m2_s'], 0.25 * ref['final_otr_mol_m2_s'])

    def test27_temperature_arrhenius(self):
        cold = run_test(loaded())
        h = configured()
        call(h, 'thermal.set_temperature', target_k=308.15)
        h.step(3 * 3600.0)
        hot = run_test(loaded(h=h))
        ratio = hot['final_otr_mol_m2_s'] / cold['final_otr_mol_m2_s']
        expected = steady_otr(PET, 100e-6, T=308.15 - PLANT_EXAMPLE['temperature_sensor_bias_k']) / steady_otr(PET, 100e-6)
        self.assertLess(abs(ratio - expected) / expected, 0.02)
        self.assertGreater(ratio, 1.06 ** 12)             # manufacturer: 6-15 % per degC for many barriers

    def test28_humidity_dilution_and_water_sensitive_coefficient(self):
        h = configured()
        call(h, 'humidity.set', rh_fraction=0.9)
        h.step(3 * 3600.0)
        wet = run_test(loaded(h=h))
        dry = run_test(loaded())
        pw = h._rh_true() * saturation_pressure_pa(h.T)
        self.assertLess(abs(wet['final_otr_mol_m2_s'] / dry['final_otr_mol_m2_s'] - (101325 - pw) / 101325), 0.02)
        h2 = configured()
        call(h2, 'humidity.set', rh_fraction=0.5)
        call(h2, 'drawer.move', cell='A', open=True); h2.step(3.0)
        with self.assertRaises(ActionRejected) as e:
            h2.invoke('sample.mount', **dict(MOUNT_EXAMPLE, template_id='polyacrylonitrile'))
        self.assertEqual(str(e.exception), 'HUMIDITY_COEFFICIENT_REQUIRED')
        call(h2, 'sample.mount', **dict(MOUNT_EXAMPLE, template_id='polyacrylonitrile', thickness_m=25e-6, humidity_coefficient=2.0))
        with self.assertRaises(ActionRejected) as e2:
            h2.invoke('sample.mount', **dict(MOUNT_EXAMPLE, cell='B', template_id='nylon_6'))
        self.assertIn(str(e2.exception), ('DIFFUSIVITY_REQUIRED', 'DRAWER_NOT_OPEN'))
        f = h2.cells['A']['film']
        P1, D1, S1 = h2._transport(f, T23, 0.5)
        P0, D0, S0 = h2._transport(f, T23, 0.0)
        self.assertAlmostEqual(P1 / P0, math.exp(1.0)); self.assertAlmostEqual(S1, S0)
        with self.assertRaises(ActionRejected):
            h2.invoke('sample.mount', **dict(MOUNT_EXAMPLE, cell='B', humidity_coefficient=1.0))

    def test29_mask_area_scales_package_but_not_area_result(self):
        full = run_test(loaded())
        masked = run_test(loaded(mount=dict(exposed_area_m2=5e-4), cfg=dict(declared_area_m2=5e-4)))
        self.assertLess(abs(masked['final_otr_mol_m2_s'] - full['final_otr_mol_m2_s']) / full['final_otr_mol_m2_s'], 0.03)
        self.assertLess(abs(masked['final_otr_cc_pkg_day'] / full['final_otr_cc_pkg_day'] - 0.1), 0.005)
        pkg = run_test(loaded(cfg=dict(report_basis='package')))
        self.assertAlmostEqual(pkg['final_otr_cc_pkg_day'], pkg['final_otr_mol_m2_s'] * TEST_AREA_M2 * CC_DAY_PER_MOL_S)

    def test30_over_range_protection(self):
        h = loaded(thickness=25e-6)                                    # PET 25 um ~ 257 cc/(m2 day) at 23 degC > 200
        res = run_test(h)
        self.assertEqual(res['validity'], 'sensor_over_range')
        self.assertEqual(res['phase'], 'failed')
        self.assertGreaterEqual(h.ledger()['over_range_events'], 1)
        h.step(3600.0)
        self.assertFalse(h.over_range)
        empty = loaded(template=PET, mount=None, clamp=False)
        call(empty, 'drawer.move', cell='A', open=True); empty.step(3.0)
        call(empty, 'sample.unmount', cell='A')
        call(empty, 'drawer.move', cell='A', open=False); empty.step(3.0)
        call(empty, 'cell.load_unload', cell='A'); empty.step(600.0)
        self.assertEqual(run_test(empty)['validity'], 'sensor_over_range')     # an empty cartridge floods the sensor

    def test31_individual_zero_removes_ambient_ingress_not_edge_leak(self):
        leaky = run_test(loaded(plant=dict(ambient_ingress_mol_s_pa=4e-15, truseal_flush_efficiency=0.0)))
        clean = run_test(loaded())
        self.assertLess(abs(leaky['final_otr_mol_m2_s'] - clean['final_otr_mol_m2_s']) / clean['final_otr_mol_m2_s'], 0.02)
        self.assertGreater(leaky['individual_zero_mol_m2_s'], 0.5 * 4e-15 * 0.2095 * 1e5 / TEST_AREA_M2)
        edge = run_test(loaded(plant=dict(edge_leak_mol_s_pa=1e-17)))
        bias = 1e-17 * 0.999 * 101325 / TEST_AREA_M2
        self.assertGreater(edge['final_otr_mol_m2_s'] - clean['final_otr_mol_m2_s'], 0.8 * bias)

    def test32_two_cells_share_one_sensor_with_rezero(self):
        h = configured(cells=('A', 'B'))
        loaded(h=h, cell='A')
        loaded(h=h, cell='B', template='poly_vinyl_chloride', thickness=50e-6)
        call(h, 'test.start', cell='A'); call(h, 'test.start', cell='B')
        seen = set()
        for _ in range(600):
            h.step(600.0)
            seen.add(h.route)
            if all(h.cells[c]['test']['phase'] != 'running' for c in 'AB'):
                break
        self.assertTrue({'A', 'B', 'Z'} <= seen)
        a, b = call(h, 'test.result', cell='A'), call(h, 'test.result', cell='B')
        self.assertEqual(a['validity'], 'valid'); self.assertEqual(b['validity'], 'valid')
        self.assertLess(abs(b['final_otr_mol_m2_s'] - steady_otr('poly_vinyl_chloride', 50e-6)) / steady_otr('poly_vinyl_chloride', 50e-6), 0.02)

    def test33_standard_mode_cycles_and_start_zero(self):
        h = loaded(cfg=dict(test_mode='standard', cycles=4, individual_zero='start', conditioning_hours=0.0))
        res = run_test(h)
        kinds = [r['kind'] for r in res['readings']]
        self.assertEqual(kinds.count('exam'), 4)
        self.assertEqual(kinds[0], 'individual_zero')
        self.assertEqual(res['phase'], 'succeeded')

    def test34_conditioning_keeps_sensor_off_the_cell(self):
        h = loaded(cfg=dict(conditioning_hours=5.0))
        call(h, 'test.start', cell='A')
        routes = set()
        for _ in range(28):
            h.step(600.0)
            if h.cells['A']['phase'] == 'conditioning':
                routes.add(h.route)
        self.assertNotIn('A', routes)
        self.assertGreater(h.cells['A']['p_up'], 9e4)       # test gas already on during conditioning

    def test35_drawer_clamp_interlocks_and_scene_states(self):
        h = configured()
        call(h, 'drawer.move', cell='A', open=True)
        h.step(DRAWER_TRAVEL_M / PLANT_EXAMPLE['drawer_speed_m_s'] / 2)
        self.assertTrue(0 < h.read('evaluator')['drawer_a.position_m'] < DRAWER_TRAVEL_M)
        with self.assertRaises(ActionRejected):
            h.invoke('cell.load_unload', cell='A')
        h.step(5.0)
        self.assertEqual(h.read('evaluator')['drawer_a.position_m'], DRAWER_TRAVEL_M)
        call(h, 'sample.mount', **MOUNT_EXAMPLE)
        call(h, 'drawer.move', cell='A', open=False); h.step(5.0)
        call(h, 'cell.load_unload', cell='A')
        h.step(PLANT_EXAMPLE['clamp_actuation_s'] / 2)
        self.assertTrue(0 < h.read('evaluator')['clamp_a.position_m'] < CLAMP_TRAVEL_M)
        h.step(5.0)
        st = h.read('evaluator')
        self.assertEqual(st['clamp_a.position_m'], CLAMP_TRAVEL_M)
        self.assertTrue(st['cell_a.clamped'] and st['cell_a.indicator_on'])
        with self.assertRaises(ActionRejected):
            h.invoke('drawer.move', cell='A', open=True)
        call(h, 'test.start', cell='A')
        with self.assertRaises(ActionRejected) as e:
            h.invoke('cell.load_unload', cell='A')
        self.assertEqual(str(e.exception), 'TEST_ACTIVE')
        call(h, 'test.stop', cell='A')
        call(h, 'cell.load_unload', cell='A'); h.step(5.0)
        self.assertFalse(h.read('agent')['cell_a.clamped'])
        self.assertGreater(h.ledger()['released_to_air_mol'], 0.0)

    def test36_power_off_cancels_and_keeps_clamp(self):
        h = loaded()
        call(h, 'test.start', cell='A')
        h.step(3 * 3600.0)
        call(h, 'power.set', enabled=False)
        st = h.read('evaluator')
        self.assertEqual(st['cell_a.phase'], 'cancelled')
        self.assertTrue(st['cell_a.clamped'])
        self.assertIsNone(st['thermal.indicated_k'])
        self.assertEqual(st['power.angle_rad'], 0.0)
        self.assertEqual(call(h, 'test.result', cell='A')['validity'], 'cancelled')
        h.step(3600.0)
        self.assertEqual(h.sensor_I, 0.0)

    def test37_conservation_over_a_complete_workflow(self):
        h = configured(cells=('A', 'B'))
        loaded(h=h, cell='A'); loaded(h=h, cell='B', template='poly_vinyl_chloride', thickness=50e-6)
        call(h, 'test.start', cell='A'); call(h, 'test.start', cell='B')
        h.step(4 * 3600.0)
        self.assertTrue(h._test_active('B'))
        call(h, 'test.stop', cell='B')
        call(h, 'cell.load_unload', cell='B'); h.step(10.0)
        call(h, 'drawer.move', cell='B', open=True); h.step(5.0)
        call(h, 'sample.unmount', cell='B')
        h.step(10 * 3600.0)
        L = call(h, 'ledger.read')
        prov = call(h, 'provenance.read')
        self.assertIsNone(prov['cell_b'])
        self.assertIn('poly_ethylene_terephthalate', prov['cell_a'])
        self.assertEqual(len(prov['operations']), 2)
        scale = max(L['film_mounted_mol'], L['carrier_in_mol'])
        self.assertLess(abs(L['film_residual_mol']), 1e-9 * scale)
        self.assertLess(abs(L['carrier_residual_balance_mol']), 1e-9 * scale)
        self.assertLess(abs(L['sensor_balance_residual_mol']), 1e-12 * max(L['sensor_input_mol'], 1e-30) + 1e-24)
        self.assertLess(abs(L['energy_residual_j']), 1e-6 * max(1.0, abs(L['tec_heat_j'])))
        self.assertGreater(L['carrier_gas_used_mol'], 0); self.assertGreater(L['test_gas_used_mol'], 0)

    def test38_step_splitting_is_consistent(self):
        a, b = loaded(), loaded()
        call(a, 'test.start', cell='A'); call(b, 'test.start', cell='A')
        a.step(6 * 3600.0)
        for _ in range(360):
            b.step(60.0)
        self.assertEqual(a.time_ns, b.time_ns)
        self.assertLess(abs(a.read('evaluator')['truth.cell_a.flux_mol_s'] - b.read('evaluator')['truth.cell_a.flux_mol_s']),
                        0.01 * a.read('evaluator')['truth.cell_a.flux_mol_s'])
        self.assertEqual(len(a.cells['A']['test']['readings']), len(b.cells['A']['test']['readings']))

    def test39_no_user_gain_on_the_2_22_and_analysis(self):
        h = loaded()
        with self.assertRaises(ActionRejected) as e:                     # 2/22: out-of-spec certified film -> service, no user adjustment
            h.invoke('sensor.adjust')
        self.assertEqual(str(e.exception), 'UNSUPPORTED')
        self.assertNotIn('sensor.adjust_gain', SLOTS)
        out = call(h, 'analysis.otr', readings_mol_m2_s=[1e-8, 1.01e-8, 1e-8], individual_zero_mol_m2_s=1e-10, window=3, driving_partial_pressure_pa=1e5,
                   thickness_m=1e-4, convergence_percent=2.0)
        self.assertAlmostEqual(out['steady_state_otr_mol_m2_s'], 1.0033333333333333e-8 - 1e-10)
        self.assertTrue(out['converged'])
        self.assertAlmostEqual(out['permeability_barrer'], out['permeability_mol_m_per_m2_s_pa'] / rt.BARRER_SI)

    def test40_metadata_exports_every_capability_and_slot(self):
        meta = export_ametek_oxtran_contract_metadata()
        self.assertEqual(set(meta['capabilities']), set(CAPS))
        self.assertEqual(set(meta['state_slots']), set(SLOTS))
        for k, c in meta['capabilities'].items():
            self.assertEqual(asset_tool.lint_schema(c['input_schema']) if hasattr(asset_tool, 'lint_schema') else [], [], k)
            validate(c['input_schema'], c['example_arguments'])
        for k, s in meta['state_slots'].items():
            if s['origin'] == 'process_truth':
                self.assertIn(s['visibility'], ('evaluator', 'debug'))
        self.assertEqual(set(WRITE_VIA), {'thermal.target_k', 'humidity.target_fraction'})


    def test41_high_permeance_film_is_stable_and_matches_coupled_steady_state(self):
        # most permeable template with a measured D, thin film, tiny carrier chamber: film and chamber strongly coupled
        lib = library()
        top = max((k for k in lib if lib[k]['oxygen_diffusivity_m2_s']), key=lambda k: lib[k]['oxygen_permeability_mol_m_per_m2_s_Pa'])
        V = 1e-7
        h = loaded(template=top, thickness=25e-6, settle=5.0, plant=dict(carrier_chamber_volume_m3=V), cfg=dict(conditioning_hours=5.0, individual_zero='none'))
        call(h, 'test.start', cell='A')
        for _ in range(24):
            call(h, 'sim.step', dt_s=300.0)
            ev = h.read('evaluator')
            self.assertTrue(all(math.isfinite(v) for v in ev.values() if isinstance(v, float) and not isinstance(v, bool)))
            self.assertGreaterEqual(ev['truth.cell_a.carrier_side_o2_mol'], 0.0)
            self.assertEqual(h.cells['A']['phase'], 'conditioning')          # sensor bypassed: no over-range trip while conditioning
        film = h.cells['A']['film']
        P, D, S = h._transport(film, h.T, h._rh_true())
        p = h.plant
        p_up = h.cells['A']['p_up']
        n_c = rt.molar_flow(CARRIER_FLOW_SCCM)
        kh = n_c * GAS_CONSTANT * h.T / h._baro() / V
        g = TEST_AREA_M2 * P / film['L']
        q_in = p['edge_leak_mol_s_pa'] * p_up + p['ambient_ingress_mol_s_pa'] * (1 - p['truseal_flush_efficiency']) * h._air_o2_pa() + h._carrier_residual_fraction() * n_c
        n_ss = (g * p_up + q_in) / (kh + g * GAS_CONSTANT * h.T / V)
        self.assertLess(abs(h.cells['A']['n_h'] / n_ss - 1), 1e-6)
        self.assertGreater(n_ss * GAS_CONSTANT * h.T / V, 0.5 * p_up)                 # carrier side far from the zero-back-pressure limit
        L = h.ledger()
        scale = max(L['film_mounted_mol'], L['carrier_in_mol'])
        self.assertLess(abs(L['film_residual_mol']), 1e-9 * scale)
        self.assertLess(abs(L['carrier_residual_balance_mol']), 1e-9 * scale)

    def test42_no_carrier_supply_means_no_sweep_and_no_sensor_flow(self):
        h = loaded(gas=dict(carrier_supply_gauge_pa=0.0), settle=1800.0)
        n_air = AIR_O2_FRACTION * (101325.0 - 0.45 * saturation_pressure_pa(296.15)) * PLANT_EXAMPLE['carrier_chamber_volume_m3'] / (GAS_CONSTANT * 296.15)
        self.assertGreaterEqual(h.read('evaluator')['truth.cell_a.carrier_side_o2_mol'], n_air * (1 - 1e-9))   # trapped air is not flushed
        L = h.ledger()
        self.assertEqual(L['carrier_gas_used_mol'], 0.0)
        self.assertLess(abs(L['to_sensor_mol'] + L['to_vent_mol']), 1e-18)        # no sweep (rounding-level only)
        self.assertEqual(L['rezero_stream_mol'] + L['sensor_input_mol'], 0.0)
        self.assertFalse(h.read('agent')['gas.supply_ok'])
        with self.assertRaises(ActionRejected) as e:
            h.invoke('test.start', cell='A')
        self.assertEqual(str(e.exception), 'GAS_SUPPLY_PRESSURE_OUT_OF_RANGE')

    def test43_rezero_stream_is_over_range_protected(self):
        # no H2 in the carrier: 1000 ppm O2 x 10 sccm ~ 14 cc/day on the ReZero stream, far above the 1.25 cc/day protection limit
        h = loaded(gas=dict(carrier_h2_fraction=0.0, carrier_o2_impurity_fraction=1e-3), cfg=dict(individual_zero='none', conditioning_hours=0.0))
        call(h, 'test.start', cell='A')
        self.assertEqual(h.seq['kind'], 'Z')
        stream = 1e-3 * rt.molar_flow(CARRIER_FLOW_SCCM)
        worst = 0.0
        while h._test_active('A') and h.time_s < 40 * 3600:
            h.step(MAX_SUBSTEP_S)
            worst = max(worst, h.sensor_I)
        self.assertLess(worst, 0.5 * 4 * FARADAY * stream)                          # protected within one sub-step, far below the stream's current
        self.assertGreaterEqual(h.ledger()['over_range_events'], 1)
        self.assertEqual(h.zero_list, [])                                          # no ReZero value from a protected slot
        self.assertEqual(call(h, 'test.result', cell='A')['validity'], 'sensor_over_range')

    def test44_partial_slots_record_nothing(self):
        # conditioning ends 20 s before the end of the first cell-A slot: that slot must not produce an exam
        h = loaded(cfg=dict(conditioning_hours=(3600.0 - 20.0) / 3600.0, individual_zero='none', test_mode='standard', cycles=1))
        res = run_test(h, dt=60.0)
        times = [r['time_s'] for r in res['readings'] if r['kind'] == 'exam']
        self.assertEqual(times, [9000.0])            # Z 0-1800, A 1800-3600 (partial), idle B, Z, A 7200-9000
        self.assertEqual(res['validity'], 'valid')
        self.assertLess(abs(res['final_otr_mol_m2_s'] / steady_otr(PET, 100e-6) - 1), 0.25)
        # a test started in the middle of the other cell's idle slot waits for a complete slot
        h = configured(cells=('A', 'B'))
        loaded(h=h, cell='A'); loaded(h=h, cell='B', template='poly_vinyl_chloride', thickness=50e-6)
        call(h, 'test.start', cell='A')
        while not (h.seq['kind'] == 'B' and h.time_ns - h.seq['start_ns'] > 60e9):
            h.step(30.0)
        call(h, 'test.start', cell='B')
        start_b = h.time_s
        h.step(3 * 3600.0)
        exams_b = [r for r in h.cells['B']['test']['readings'] if r['kind'] == 'exam']
        self.assertTrue(exams_b)
        self.assertGreaterEqual(exams_b[0]['time_s'], TEST_CFG_EXAMPLE['exam_minutes'] * 60.0)
        self.assertGreater(exams_b[0]['time_s'] + start_b, start_b + 1800.0)

    def test45_power_loss_during_release_unseals_the_cell(self):
        h = loaded(settle=600.0)
        call(h, 'cell.load_unload', cell='A')                       # release stroke starts
        h.step(PLANT_EXAMPLE['clamp_actuation_s'] / 2)
        call(h, 'power.set', enabled=False)
        st = h.read('evaluator')
        self.assertFalse(st['cell_a.clamped'])
        self.assertTrue(0 < st['clamp_a.position_m'] < CLAMP_TRAVEL_M)
        self.assertEqual(st['truth.cell_a.carrier_side_o2_mol'], 0.0)
        with self.assertRaises(ActionRejected) as e:
            h.invoke('drawer.move', cell='A', open=True)             # the platen is still part-way down
        self.assertEqual(str(e.exception), 'CARTRIDGE_CLAMPED')
        call(h, 'power.set', enabled=True)
        call(h, 'cell.load_unload', cell='A'); h.step(5.0)          # completes the clamp stroke and reseals with trapped air
        self.assertTrue(h.read('agent')['cell_a.clamped'])
        call(h, 'cell.load_unload', cell='A'); h.step(5.0)
        call(h, 'drawer.move', cell='A', open=True); h.step(3.0)
        self.assertEqual(h.read('evaluator')['drawer_a.position_m'], DRAWER_TRAVEL_M)
        L = h.ledger()
        scale = max(L['film_mounted_mol'], L['carrier_in_mol'])
        self.assertLess(abs(L['carrier_residual_balance_mol']), 1e-9 * scale)
        self.assertLess(abs(L['film_residual_mol']), 1e-9 * scale)

    def test46_analysis_inputs_are_bounded(self):
        h = configured()
        for bad in ([1e308, 1e308], [2e-3]):
            with self.assertRaises(ActionRejected):
                h.invoke('analysis.otr', readings_mol_m2_s=bad, individual_zero_mol_m2_s=None, window=2, driving_partial_pressure_pa=1e5, thickness_m=None,
                         convergence_percent=1.0)
        out = call(h, 'analysis.otr', readings_mol_m2_s=[1e-3, -1e-3] * (MAX_READINGS // 2), individual_zero_mol_m2_s=-1e-3, window=100,
                   driving_partial_pressure_pa=1.0, thickness_m=0.01, convergence_percent=1.0)
        self.assertTrue(all(math.isfinite(v) for v in out.values() if isinstance(v, float)))

    def test47_describe_returns_private_copies(self):
        h = configured()
        h.register_principal('ag', 'agent')
        d = h.session('ag').describe(instance_id='oxtran_01')
        n = len(rt.LIMITATIONS)
        d['capabilities']['test.result']['limitations'].append('mutated by a caller')
        d['state_slots']['clock.time_s']['meaning_zh'] = 'mutated'
        self.assertEqual(len(rt.LIMITATIONS), n)
        meta = export_ametek_oxtran_contract_metadata()
        self.assertNotIn('mutated by a caller', meta['mechanisms']['oxygen_permeation']['limitations'])
        self.assertNotIn('mutated by a caller', meta['capabilities']['power.set']['limitations'])
        self.assertNotEqual(meta['state_slots']['clock.time_s']['meaning_zh'], 'mutated')

    def test48_rezero_time_base_and_new_gas_invalidates_the_baseline(self):
        h = loaded(cfg=dict(individual_zero='none', conditioning_hours=0.0, test_mode='standard', cycles=2))
        res = run_test(h, dt=600.0)
        self.assertEqual([z['time_s'] for z in res['rezero_readings']][:2], [1800.0, 7200.0])   # same time base as the exams
        self.assertEqual([r['time_s'] for r in res['readings']], [3600.0, 9000.0])
        self.assertIsNotNone(h.zero_current)
        call(h, 'gas.configure', **dict(GAS_EXAMPLE, carrier_o2_impurity_fraction=5e-4))
        self.assertIsNone(h.zero_current)
        call(h, 'test.start', cell='A')
        self.assertEqual(h.seq['kind'], 'Z')                        # a new supply is re-zeroed before the next exam
        call(h, 'power.set', enabled=False)
        self.assertIsNone(h.zero_current)

    def test49_panel_counts_carry_the_measurement_noise(self):
        h = loaded()
        call(h, 'test.start', cell='A')
        diffs = []
        for _ in range(400):
            h.step(30.0)
            a = h.read('agent')
            self.assertEqual(a, h.read('agent'))                                   # reads stay pure
            sigma = math.hypot(PLANT_EXAMPLE['sensor_noise_a'], PLANT_EXAMPLE['sensor_relative_noise'] * max(h.sensor_I_o2, 0.0))
            diffs.append((a['sensor.raw_counts'] * COUNT_A - h.read('evaluator')['truth.sensor_current_a']) / sigma)
        rms = math.sqrt(sum(d * d for d in diffs) / len(diffs))
        self.assertTrue(0.6 < rms < 1.6, rms)                                     # carries the declared noise, not the noise-free truth
        # an exam reading is the panel measurement at the slot end
        t = h.cells['A']['test']
        n = len(t['readings'])
        while len(t['readings']) == n:
            before = h._measured_current()
            h.step(1.0)
        self.assertEqual(t['readings'][-1]['sensor_counts'], int(round(h._measured_current() / COUNT_A)))

    def test50_over_range_is_detected_on_the_measured_current_and_visible(self):
        target = 1.6 / CC_DAY_PER_MOL_S                                         # 1.6 cc/day on the sensor (> 1.25 cc/day)
        L = steady_otr(PET, 1.0) * TEST_AREA_M2 / target
        h = loaded(thickness=L, settle=3 * 3600.0, cfg=dict(individual_zero='none', conditioning_hours=0.0),
                   plant=dict(sensor_noise_a=0.0, sensor_relative_noise=0.0))
        call(h, 'test.start', cell='A')
        seen = None
        for _ in range(800):
            h.step(10.0)
            a = h.read('agent')
            if a['sensor.over_range']:
                seen = (a['sensor.route'], a['cell_a.phase'])
                break
        self.assertEqual(seen, ('bypass', 'failed'))
        self.assertGreater(h.sensor_I, 0.5 * OVER_RANGE_CURRENT_A)                 # the flag is raised by the current, not by the inlet flow
        h.step(3600.0)
        self.assertFalse(h.read('agent')['sensor.over_range'])
        self.assertEqual(call(h, 'test.result', cell='A')['terminal_reason'], 'SENSOR_OVER_RANGE')

    def test51_continuous_mode_ends_by_operator_stop(self):
        for zero in ('end', 'none'):
            h = loaded(cfg=dict(test_mode='continuous', individual_zero=zero, conditioning_hours=0.0))
            call(h, 'test.start', cell='A')
            while h.cells['A']['exam_count'] < 6:
                call(h, 'sim.step', dt_s=1800.0)
            out = call(h, 'test.stop', cell='A')
            if zero == 'end':
                self.assertEqual(out['phase'], 'running')
                self.assertEqual(h.cells['A']['phase'], 'zero_end')
                while h._test_active('A'):
                    call(h, 'sim.step', dt_s=1800.0)
            res = call(h, 'test.result', cell='A')
            self.assertEqual(res['phase'], 'succeeded')
            self.assertEqual(res['terminal_reason'], 'TEST_COMPLETE' if zero == 'end' else 'OPERATOR_STOP')
            self.assertEqual(res['validity'], 'valid')
            self.assertEqual(res['individual_zero_mol_m2_s'] is not None, zero == 'end')
            self.assertEqual(h.read('agent')['cell_a.final_otr_mol_m2_s'], res['final_otr_mol_m2_s'])
        # standard mode stopped early is still a cancellation
        h = loaded(cfg=dict(test_mode='standard', cycles=10, individual_zero='none', conditioning_hours=0.0))
        call(h, 'test.start', cell='A'); h.step(3 * 3600.0)
        self.assertEqual(call(h, 'test.stop', cell='A')['phase'], 'cancelled')

    def test52_humidity_change_during_a_test_is_flagged(self):
        h = loaded(cfg=dict(test_mode='standard', cycles=4, individual_zero='none', conditioning_hours=0.0))
        call(h, 'test.start', cell='A')
        h.step(2 * 3600.0)
        call(h, 'humidity.set', rh_fraction=0.9)
        res = run_test(h) if False else None
        while h._test_active('A'):
            call(h, 'sim.step', dt_s=1800.0)
        res = call(h, 'test.result', cell='A')
        self.assertEqual(res['validity'], 'humidity_out_of_tolerance')
        self.assertEqual(res['conditions']['target_rh'], 0.0)

    def test53_humidity_coefficient_is_a_mount_prior_without_agent_oracle(self):
        h = configured()
        call(h, 'drawer.move', cell='A', open=True); h.step(3.0)
        with self.assertRaises(ActionRejected) as e:                                   # even for a dry test
            h.invoke('sample.mount', **dict(MOUNT_EXAMPLE, template_id='polyacrylonitrile'))
        self.assertEqual(str(e.exception), 'HUMIDITY_COEFFICIENT_REQUIRED')
        call(h, 'sample.mount', **dict(MOUNT_EXAMPLE, template_id='polyacrylonitrile', humidity_coefficient=0.0))
        h.register_principal('ag', 'agent')
        r = h.session('ag').invoke(instance_id='oxtran_01', capability_id='humidity.set', arguments={'rh_fraction': 0.5}, request_id='rh')
        self.assertEqual(r['status'], 'succeeded')                                     # no template-class dependent rejection

    def test54_provenance_is_bounded(self):
        h = loaded()
        for _ in range(MAX_PROVENANCE + 5):
            h.invoke('test.start', cell='A')
            h.invoke('test.stop', cell='A')
        prov = call(h, 'provenance.read')
        self.assertEqual(len(prov['operations']), MAX_PROVENANCE)
        self.assertEqual(prov['operations'][-1]['operation_id'], h.cells['A']['test']['op'])

    def test55_ledgers_are_real_conservation_checks(self):
        h = configured(cells=('A', 'B'), cfg=dict(conditioning_hours=0.5))
        loaded(h=h, cell='A'); loaded(h=h, cell='B', template='poly_vinyl_chloride', thickness=50e-6)
        call(h, 'test.start', cell='A'); call(h, 'test.start', cell='B')
        h.step(4 * 3600.0)
        call(h, 'humidity.set', rh_fraction=0.4); call(h, 'thermal.set_temperature', target_k=303.15)
        h.step(3 * 3600.0)
        call(h, 'test.stop', cell='B'); call(h, 'cell.load_unload', cell='B'); h.step(5.0)
        call(h, 'drawer.move', cell='B', open=True); h.step(3.0)
        call(h, 'sample.unmount', cell='B')
        call(h, 'sample.mount', **dict(MOUNT_EXAMPLE, cell='B', template_id='polystyrene', thickness_m=500e-6))
        h.step(1200.0)
        call(h, 'power.set', enabled=False); h.step(1800.0); call(h, 'power.set', enabled=True)
        call(h, 'drawer.move', cell='B', open=False); h.step(3.0)
        call(h, 'cell.load_unload', cell='B')
        h.step(20 * 3600.0)
        L = call(h, 'ledger.read')
        scale = max(L['film_mounted_mol'], L['carrier_in_mol'])
        self.assertLess(abs(L['film_residual_mol']), 1e-12 * scale)
        self.assertLess(abs(L['carrier_residual_balance_mol']), 1e-12 * scale)
        self.assertLess(abs(L['sensor_balance_residual_mol']), 1e-13 * L['sensor_input_mol'])
        self.assertLess(abs(L['energy_residual_j']), 1e-9 * abs(L['tec_heat_j']))
        self.assertGreater(L['sensor_discarded_mol'], 0.0)
        # the residuals are not identities: an O2 amount that appears without a booked flux shows up
        h.cells['A']['n_h'] += 1e-9
        self.assertAlmostEqual(h.ledger()['carrier_residual_balance_mol'] / -1e-9, 1.0, places=3)
        h.cells['A']['conc'][0] += 1.0
        f = h.cells['A']['film']
        self.assertAlmostEqual(h.ledger()['film_residual_mol'] / -(f['dx'] * f['area']), 1.0, places=3)

    def test56_rezero_over_range_fails_every_active_test_once(self):
        h = configured(cells=('A', 'B'), gas=dict(carrier_h2_fraction=0.0, carrier_o2_impurity_fraction=1e-3), cfg=dict(individual_zero='none', conditioning_hours=0.0))
        loaded(h=h, cell='A'); loaded(h=h, cell='B', template='poly_vinyl_chloride', thickness=50e-6)
        call(h, 'test.start', cell='A'); call(h, 'test.start', cell='B')
        self.assertEqual(h.seq['kind'], 'Z')
        for _ in range(360):
            h.step(10.0)
        for cell in 'AB':
            res = call(h, 'test.result', cell=cell)
            self.assertEqual((res['phase'], res['validity'], res['terminal_reason']), ('failed', 'sensor_over_range', 'SENSOR_OVER_RANGE'))
            self.assertEqual(res['exam_count'], 0)
        self.assertEqual(h.ledger()['over_range_events'], 1)                  # the tripped stream is not re-entered
        self.assertEqual(h.zero_list, [])
        self.assertIsNone(h.seq)

    def test57_cancel_aborts_even_a_continuous_test(self):
        h = loaded(cfg=dict(test_mode='continuous', individual_zero='end', conditioning_hours=0.0))
        h.register_principal('ag', 'agent')
        s = h.session('ag')
        op = s.invoke(instance_id='oxtran_01', capability_id='test.start', arguments={'cell': 'A'}, request_id='s')['operation_id']
        h.step(6 * 3600.0)
        c = s.cancel(op, 'c1')
        self.assertEqual((c['status'], c['result']['phase']), ('succeeded', 'cancelled'))
        self.assertEqual(s.cancel(op, 'c1'), c)
        self.assertEqual(s.poll(op)['status'], 'cancelled')
        self.assertEqual(s.poll(op)['result']['validity'], 'cancelled')
        with self.assertRaises(ActionRejected):
            s.cancel(op, 'c2')
        call(h, 'cell.load_unload', cell='A')                                # no longer TEST_ACTIVE

    def test58_no_exam_without_a_baseline(self):
        pvc = 'poly_vinyl_chloride'
        kw = dict(template=pvc, thickness=50e-6, cfg=dict(test_mode='standard', cycles=1, individual_zero='none', conditioning_hours=0.0), inst=dict(rezero_frequency=3))
        h = loaded(**kw)
        call(h, 'test.start', cell='A')
        h.step(3600.0 - 100.0)                                                 # Z 0-1800, A 1800-3600: 100 s before the first exam
        call(h, 'test.stop', cell='A')
        call(h, 'gas.configure', **dict(GAS_EXAMPLE, carrier_o2_impurity_fraction=5e-4))   # new cylinder: baseline invalid
        call(h, 'test.start', cell='A')
        while h._test_active('A'):
            h.step(600.0)
        res = call(h, 'test.result', cell='A')
        self.assertTrue(res['rezero_readings'])
        self.assertLess(res['rezero_readings'][0]['time_s'], res['readings'][0]['time_s'])
        self.assertEqual(res['validity'], 'valid')
        self.assertLess(abs(res['final_otr_mol_m2_s'] / steady_otr(pvc, 50e-6) - 1), 0.03)

    def test59_capability_metadata_is_specific(self):
        meta = export_ametek_oxtran_contract_metadata()
        slots = meta['state_slots']
        pre = {json.dumps(c['preconditions'], ensure_ascii=False, sort_keys=True) for c in meta['capabilities'].values() if c['preconditions']}
        self.assertGreater(len(pre), 8)                                         # not one template for every command
        for k, c in meta['capabilities'].items():
            for e in c['errors']:
                self.assertNotEqual(e['meaning_zh'], e['code'], k)
                self.assertNotIn(e['code'], ('TEST_COMPLETE', 'OPERATOR_STOP'), k)   # success reasons are not errors
            for sid in c['observes'] + [x for p in c['preconditions'] for x in p['state_ids']]:
                self.assertIn(sid, slots, k)
            if c['visibility'] == 'agent':
                self.assertTrue(all(slots[sid]['visibility'] == 'agent' for sid in c['observes']), k)
            if c['availability'] == 'implemented' and c['kind'] not in ('analysis', 'query'):
                self.assertTrue(c['observes'] or k == 'test.configure', k)
        params = meta['mechanisms']['oxygen_permeation']['parameters']
        humanized = [k for k, v in params.items() if v['meaning_zh'].split('；')[0].replace(' ', '_').lower() == k.lower()]
        self.assertEqual(humanized, [])
        self.assertIsNotNone(slots['truth.cell_a.test_side_o2_pa']['reference_conditions'])
        for k in ('humidity_coefficient', 'permeability_override_mol_m_per_m2_s_pa', 'diffusivity_override_m2_s', 'declared_thickness_m', 'manual_barometer_pa'):
            self.assertIn(k, params)                                                # nullable host priors are exported too
        caps = meta['capabilities']
        self.assertNotIn('ACCESS_DENIED', [e['code'] for e in caps['test.start']['errors']])   # agent-visible and not foreign-guarded
        self.assertIn('ACCESS_DENIED', [e['code'] for e in caps['test.stop']['errors']])
        self.assertIn('INVALID_ARGUMENT', [e['code'] for e in caps['sensor.adjust']['errors']])
        # the preconditions that reject test.start / cell.load_unload are visible to an agent
        h = loaded(cells=('A',), clamp=False)
        a = h.read('agent')
        self.assertTrue(a['cell_a.method_configured'] and not a['cell_b.method_configured'])
        call(h, 'cell.load_unload', cell='A')
        self.assertTrue(h.read('agent')['cell_a.clamp_moving'])
        h.step(5.0)
        self.assertFalse(h.read('agent')['cell_a.clamp_moving'])

if __name__ == '__main__':
    unittest.main()
