"""CHEM-149 METTLER TOLEDO RC1mx + HFCal (AP01-2 glass reactor) runtime tests: python3 -m unittest tests/test_asset_mt_rc1mxhfcal.py

Themes (pipeline.md): set points only write targets; read/describe/poll are pure; rejected preconditions leave the state
unchanged; two instances are isolated; agent/evaluator truth isolation; energy, mass and species conservation; real return
values validate against the contract schemas; async completion / cancellation residue.
Physics and instrument: the RC1e AP01-2 heating/cooling curves (empty reactor Tj mode, 1 L water Tr mode, emergency A) are
reproduced within tolerance and not an order of magnitude slower; |Tj - Tr| <= T diff max; the HFCal UA calibration, the
heat-capacity ramp and the heat-flow evaluation recover the true UA, heat capacity and the reaction heat of the acetic
anhydride hydrolysis (enthalpy derived from NIST enthalpies of formation); accumulation and MTSR; emergency programs A, B1 and
C with their triggers, actions, 0.1 K/s gradients, blocking and reset; stirrer torque limit and shaft mirror; discharge;
bubble-point and volume guards; quantised Pt100 readings; 2 s instrument cycle (host-step independence, PI stability bound);
power loss without cooling; request binding, ownership and unsupported capabilities.  No native scene, robot or hardware claims.
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
from scripts.asset_runtime.mt_rc1mxhfcal import *  # noqa: E402,F401,F403
from scripts.asset_runtime import mt_rc1mxhfcal as rt  # noqa: E402

PROCESS_EVIDENCE = {}
CHECKS = []
_spec = importlib.util.spec_from_file_location('rc1mx_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
asset_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(asset_tool)
_ispec = importlib.util.spec_from_file_location('rc1mx_interface', ROOT / 'outputs/expansion_batch04/t1_thermal/mt_rc1mxhfcal/interface_definition.py')
interface = importlib.util.module_from_spec(_ispec)
_ispec.loader.exec_module(interface)
OUT = ROOT / 'outputs/expansion_batch04/t1_thermal/mt_rc1mxhfcal'
QUIET = dict(tr_offset_k=0.0, tj_offset_k=0.0, tc_offset_k=0.0, sensor_noise_k=0.0)
FIT_PLANT = dict(PLANT_EXAMPLE, ambient_k=295.15, coolant_inlet_k=289.15, coolant_flow_m3_s=10e-3 / 60.0, **QUIET)
HYDROLYSIS = dict(reactions=[dict(reaction_id='acetic_anhydride_hydrolysis', log10_a=5.5, activation_energy_j_mol=46000.0,
                                  orders=[dict(substance_id='acetic_anhydride', order=1.0), dict(substance_id='water', order=0.0)])], accept_priors=True)


def state(h):
    s = h.read('evaluator')
    s.pop('clock.time_s')
    return s


def call(h, k, **a):
    """Invoke through the runtime, validate the returned value and check that only declared slots changed immediately."""
    before = h.read('evaluator')
    r = h.invoke(k, **a)
    validate(CAPS[k]['output_schema'], r)
    problems = asset_tool.validate_value(r, CAPS[k]['output_schema'])
    if problems:
        raise AssertionError(f'{k}: {problems}')
    after = h.read('evaluator')
    delta = {x for x in after if before[x] != after[x]}
    if k != 'sim.step' and not delta <= set(CAPS[k]['effects']):
        raise AssertionError(f'{k}: undeclared state changes {sorted(delta - set(CAPS[k]["effects"]))}')
    PROCESS_EVIDENCE.setdefault(k, []).append(dict(time_s=h.time_s, result_keys=sorted(r) if isinstance(r, dict) else None, actual_call=True))
    return r


def rejected(test, h, code, k, **a):
    before = copy.deepcopy(state(h))
    with test.assertRaises(ActionRejected) as e:
        h.invoke(k, **a)
    test.assertEqual(str(e.exception), code)
    test.assertEqual(state(h), before, f'{k} rejection changed the state')


def plant(**kw):
    h = MtRc1mxhfcalRuntime()
    call(h, 'plant.configure', **dict(PLANT_EXAMPLE, **kw))
    call(h, 'power.set', enabled=True)
    return h


def with_water(kg=0.8, rpm=250.0, T=313.15, **kw):
    h = plant(**kw)
    call(h, 'reactor.charge', substances=[dict(substance_id='water', mass_kg=kg)], temperature_k=T, viscosity_pa_s=0.00089, thermal_conductivity_w_m_k=0.6, accept_priors=True)
    call(h, 'stirrer.set', rpm=rpm)
    return h


def settle_tr(h, T=313.15, s=1800.0, controller='aqueous'):
    call(h, 'thermostat.set_tr', end_k=T, ramp_s=0.0, controller=controller)
    call(h, 'sim.step', dt_s=s)


def run_until(h, pred, limit_s, dt=2.0):
    t = 0.0
    while not pred() and t < limit_s:
        h.step(dt)
        t += dt
    return t


def digitised(plot, key):
    d = json.loads((OUT / 'source_cache/rc1e_curves_digitized.json').read_text())['plots'][plot]['series'][key]
    return sorted((a[0], a[1]) for a in d)


def first_time(series, level, after=0.0):
    return next(t for t, T in series if t >= after and T >= level)


def calibrated_hydrolysis(noise=True, seed=7):
    kw = dict(noise_seed=seed) if noise else dict(QUIET)
    h = with_water(kg=0.8, T=313.15, **kw)
    call(h, 'reaction.configure', **HYDROLYSIS)
    e = call(h, 'experiment.start', name='hydrolysis')['experiment_id']
    settle_tr(h)
    c1 = call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0)['operation_id']
    h.step(2200.0)
    t0, q0 = h.time_s, h.ledger()['reaction_heat_j']
    call(h, 'dosing.start', substance_id='acetic_anhydride', rate_kg_s=1.5e-4, total_mass_kg=0.10209, feed_temperature_k=None)
    h.step(3600.0)
    t1, q1 = h.time_s, h.ledger()['reaction_heat_j']
    c2 = call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0)['operation_id']
    h.step(2200.0)
    return h, e, c1, c2, t0, t1, q1 - q0


class Rc1mxRuntimeTests(unittest.TestCase):
    # ---------------------------------------------------------------- contract themes
    def test01_read_and_describe_are_pure(self):
        h = with_water()
        s0, rev = state(h), h.revision
        h.read('agent'); h.read('evaluator'); h.ledger()
        self.assertEqual((state(h), h.revision, h.time_s), (s0, rev, h.time_s))
        h.register_principal('a1', 'agent')
        d = h.session('a1').describe(instance_id=h.instance_id)
        self.assertEqual(d['asset_type_id'], ASSET_TYPE_ID)
        self.assertTrue(all(v['visibility'] == 'agent' for v in d['state_slots'].values()))
        self.assertEqual(state(h), s0)

    def test02_setpoint_only_writes_target(self):
        h = with_water(T=298.15)
        T0 = h.read('evaluator')['truth.reactor_k']
        call(h, 'thermostat.set_tr', end_k=333.15, ramp_s=600.0, controller='aqueous')
        s = h.read('evaluator')
        self.assertEqual(s['truth.reactor_k'], T0)
        self.assertEqual(s['thermostat.mode'], 'tr')
        call(h, 'sim.step', dt_s=600.0)
        self.assertGreater(h.read('evaluator')['truth.reactor_k'], T0 + 10.0)

    def test03_rejections_leave_state_unchanged(self):
        h = MtRc1mxhfcalRuntime()
        rejected(self, h, 'PLANT_REQUIRED', 'power.set', enabled=True)
        call(h, 'plant.configure', **PLANT_EXAMPLE)
        rejected(self, h, 'PLANT_ALREADY_CONFIGURED', 'plant.configure', **PLANT_EXAMPLE)
        rejected(self, h, 'POWER_OFF', 'thermostat.set_tj', end_k=320.0, ramp_s=0.0)
        call(h, 'power.set', enabled=True)
        rejected(self, h, 'OUTSIDE_SAFETY_LIMITS', 'thermostat.set_tj', end_k=440.0, ramp_s=0.0)
        rejected(self, h, 'VOLUME_BELOW_OPERATING_RANGE', 'thermostat.set_tr', end_k=320.0, ramp_s=0.0, controller='aqueous')
        rejected(self, h, 'UNKNOWN_SUBSTANCE', 'reactor.charge', substances=[dict(substance_id='unobtainium', mass_kg=0.1)], temperature_k=298.15,
                 viscosity_pa_s=0.001, thermal_conductivity_w_m_k=0.5, accept_priors=True)
        rejected(self, h, 'NOT_LIQUID', 'reactor.charge', substances=[dict(substance_id='water', mass_kg=0.1)], temperature_k=270.0,
                 viscosity_pa_s=0.001, thermal_conductivity_w_m_k=0.5, accept_priors=True)
        rejected(self, h, 'VOLUME_ABOVE_OPERATING_RANGE', 'reactor.charge', substances=[dict(substance_id='water', mass_kg=2.1)], temperature_k=298.15,
                 viscosity_pa_s=0.001, thermal_conductivity_w_m_k=0.5, accept_priors=True)
        rejected(self, h, 'OUTSIDE_STIRRER_RANGE', 'stirrer.set', rpm=20.0)
        rejected(self, h, 'OUTSIDE_STIRRER_RANGE', 'stirrer.set', rpm=900.0)
        rejected(self, h, 'TR_MODE_REQUIRED', 'calibration.run', pre_s=600.0, heat_s=600.0, post_s=600.0)
        rejected(self, h, 'RATE_ABOVE_DOSING_UNIT', 'dosing.start', substance_id='water', rate_kg_s=0.01, total_mass_kg=0.1, feed_temperature_k=None)
        rejected(self, h, 'INVALID_KINETICS', 'reaction.configure', reactions=[dict(reaction_id='acetic_anhydride_hydrolysis', log10_a=5.0, activation_energy_j_mol=4e4,
                                                                                    orders=[dict(substance_id='acetic_anhydride', order=1.0)])], accept_priors=True)
        rejected(self, h, 'INVALID_SAFETY_SETTINGS', 'safety.configure', **dict(SAFETY_EXAMPLE, tr_min_k=400.0))
        rejected(self, h, 'NO_EMERGENCY', 'emergency.reset')

    def test04_two_instances_are_isolated(self):
        a, b = with_water(), with_water(kg=1.2)
        call(a, 'thermostat.set_tr', end_k=333.15, ramp_s=0.0, controller='aqueous')
        a.step(900.0)
        self.assertNotAlmostEqual(a.read('evaluator')['truth.reactor_k'], b.read('evaluator')['truth.reactor_k'], places=2)
        self.assertEqual(b.read('evaluator')['thermostat.mode'], 'tj')
        self.assertEqual(b.time_s, 0.0)

    def test05_agent_sees_no_truth(self):
        h = with_water()
        h.register_principal('a1', 'agent'); h.register_principal('ev', 'evaluator')
        obs = h.session('a1').read(instance_id=h.instance_id)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.') or k in ('stirrer.angle_rad', 'drain.angle_rad')])
        self.assertTrue(all(k in h.session('ev').read(instance_id=h.instance_id)['observations'] for k in ('truth.ua_w_k', 'truth.composition_mol')))
        r = h.session('a1').invoke(instance_id=h.instance_id, capability_id='ledger.read', arguments={}, request_id='r1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        with self.assertRaises(ActionRejected):
            h.session('a1').read(instance_id=h.instance_id, state_ids=['truth.reactor_k'])

    def test06_energy_ledger_closes(self):
        h, *_ = calibrated_hydrolysis()
        call(h, 'reactor.discharge', open=True)
        h.step(600.0)
        led = call(h, 'ledger.read')
        scale = max(abs(led['heater_j']), abs(led['coolant_removed_j']), 1.0)
        self.assertLess(abs(led['energy_residual_j']), 1e-9 * scale)
        self.assertGreater(led['reaction_heat_j'], 5e4)
        self.assertGreater(led['discharged_kg'], 0.0)

    def test07_mass_and_species_conservation(self):
        h, *_ = calibrated_hydrolysis()
        led = h.ledger()
        self.assertLess(abs(led['mass_residual_kg']), 1e-12)
        subs = rt.library()['substances']
        n = h.read('evaluator')['truth.composition_mol']
        # carbon balance: acetic anhydride (C4) dosed -> acetic acid (C2); all anhydride hydrolysed
        self.assertAlmostEqual(n.get('acetic_acid', 0.0), 2 * 0.10209 / subs['acetic_anhydride']['M'], places=6)
        self.assertLess(n.get('acetic_anhydride', 0.0), 1e-9)
        for rid, r in rt.library()['reactions'].items():
            dm = sum(c * subs[s]['M'] for s, c in r['products'].items()) - sum(c * subs[s]['M'] for s, c in r['reactants'].items())
            self.assertLess(abs(dm), 2e-4, rid)                                 # stoichiometric mass balance of every library reaction
        prov = call(h, 'provenance.read')
        self.assertEqual((prov['library'], prov['reactions']), (str(LIBRARY_PATH.relative_to(ROOT)), ['acetic_anhydride_hydrolysis']))
        self.assertEqual(set(prov['substances']), set(n))

    # ---------------------------------------------------------------- thermostat against the manufacturer's curves
    def test08_empty_reactor_tj_mode_follows_rc1e_curve1(self):
        h = MtRc1mxhfcalRuntime()
        call(h, 'plant.configure', **FIT_PLANT); call(h, 'power.set', enabled=True)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tj_max_k=503.15, tr_max_k=473.15))
        call(h, 'thermostat.set_tj', end_k=298.15, ramp_s=0.0); h.step(1800.0)
        call(h, 'thermostat.set_tj', end_k=473.15, ramp_s=0.0)
        t = run_until(h, lambda: h.read('evaluator')['truth.jacket_k'] >= 453.15, 7200.0)
        man = digitised('curve1_empty_Tj_mode', 'Tj')
        t_man = first_time(man, 180.0, 120.0) - first_time(man, 26.0, 120.0)
        self.assertLess(abs(t / t_man - 1.0), 0.15, (t, t_man))
        h.step(900.0)
        call(h, 'thermostat.set_tj', end_k=298.15, ramp_s=0.0)
        tc = run_until(h, lambda: h.read('evaluator')['truth.jacket_k'] <= 333.15, 3600.0)
        dn = [x for x in man if x[0] > 1800.0]
        t_man_c = next(t_ for t_, T in dn if T <= 60.0) - next(t_ for t_, T in dn if T <= 199.0)
        self.assertLess(abs(tc / t_man_c - 1.0), 0.3, (tc, t_man_c))

    def test09_one_litre_water_tr_mode_follows_rc1e_curve2(self):
        h = MtRc1mxhfcalRuntime()
        call(h, 'plant.configure', **FIT_PLANT); call(h, 'power.set', enabled=True)
        call(h, 'reactor.charge', substances=[dict(substance_id='water', mass_kg=0.995)], temperature_k=298.15, viscosity_pa_s=0.00089, thermal_conductivity_w_m_k=0.6,
             accept_priors=True)
        call(h, 'stirrer.set', rpm=200.0)
        settle_tr(h, 298.15, 1200.0)
        call(h, 'thermostat.set_tr', end_k=368.15, ramp_s=0.0, controller='aqueous')
        t0 = h.time_s
        diffs = []
        t40 = t90 = None
        while h.time_s - t0 < 3600.0 and t90 is None:
            h.step(2.0)
            s = h.read('evaluator')
            diffs.append(s['truth.jacket_k'] - s['truth.reactor_k'])
            if t40 is None and s['truth.reactor_k'] >= 313.15:
                t40 = h.time_s
            if s['truth.reactor_k'] >= 363.15:
                t90 = h.time_s
        man = digitised('curve2_1L_water_Tr_mode', 'Tr')
        t_man = first_time(man, 90.0, 240.0) - first_time(man, 40.0, 240.0)
        self.assertLess(abs((t90 - t40) / t_man - 1.0), 0.15, ((t90 - t40), t_man))
        self.assertLessEqual(max(diffs), TDIFF_MAX_K + 0.5)                  # glass protection: Tj - Tr limited to 50 K
        self.assertGreater(max(diffs), TDIFF_MAX_K - 3.0)                   # and the heater is really at the limit

    def test10_emergency_a_cools_about_twice_as_fast(self):
        rates = {}
        for emerg in (False, True):
            h = MtRc1mxhfcalRuntime()
            call(h, 'plant.configure', **FIT_PLANT); call(h, 'power.set', enabled=True)
            call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tj_max_k=503.15, tr_max_k=473.15))
            call(h, 'thermostat.set_tj', end_k=473.15, ramp_s=0.0); h.step(3600.0)
            T0 = h.read('evaluator')['truth.jacket_k']
            if emerg:
                call(h, 'emergency.trigger')
            else:
                call(h, 'thermostat.set_tj', end_k=298.15, ramp_s=0.0)
            h.step(60.0)
            rates[emerg] = (T0 - h.read('evaluator')['truth.jacket_k']) / 60.0
        self.assertGreater(rates[True], 1.4 * rates[False], rates)          # safety-valve bypass: fitted to the RC1e curve-4 collapse
        self.assertGreater(rates[False], 0.5, rates)                        # K/s: RC1e curve 1 drops ~95 K in 2 min

    def test11_rates_not_an_order_of_magnitude_off(self):
        cal = json.loads((OUT / 'thermal_calibration.json').read_text())
        for plot, r in cal['rate_check'].items():
            self.assertTrue(0.5 < r['model_over_manufacturer'] < 2.0, (plot, r))
        for k, v in cal['fitted'].items():
            if k in PLANT_FIT:
                self.assertAlmostEqual(PLANT_FIT[k], v, delta=abs(v) * 1e-4, msg=k)

    def test12_tj_below_coolant_margin_warns_but_is_accepted(self):
        h = plant(**QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tj_min_k=253.15, tr_min_k=253.15))
        call(h, 'thermostat.set_tj', end_k=290.15, ramp_s=0.0)
        self.assertIn('TJ_BELOW_COOLANT_MARGIN', [e['code'] for e in h.events])
        self.assertEqual(h.read('evaluator')['thermostat.tj_setpoint_k'], 290.15)

    # ---------------------------------------------------------------- HFCal
    def test13_ua_calibration_recovers_true_ua(self):
        h = with_water(**QUIET)
        settle_tr(h)
        op = call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0)['operation_id']
        self.assertTrue(h.read('evaluator')['calibration.running'])
        h.step(1200.0)
        self.assertTrue(h.read('evaluator')['cal_heater.on'])
        h.step(1000.0)
        res = call(h, 'calibration.result', operation_id=op)
        ua = h.read('evaluator')['truth.ua_w_k']
        self.assertEqual(res['phase'], 'succeeded')
        self.assertLess(abs(res['ua_w_k'] / ua - 1.0), 0.02, (res['ua_w_k'], ua))
        self.assertEqual(h.read('evaluator')['calibration.ua_w_k'], res['ua_w_k'])
        self.assertIsNotNone(res['uncertainty_w_k'])

    def test14_heat_capacity_ramp_recovers_true_heat_capacity(self):
        h = with_water(kg=1.2, **QUIET)
        settle_tr(h)
        call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0); h.step(2200.0)
        op = call(h, 'calibration.cp', delta_k=5.0, rate_k_min=0.5, hold_s=600.0)['operation_id']
        h.step(600.0 + 600.0 + 700.0)
        res = call(h, 'calibration.result', operation_id=op)
        C = h.read('evaluator')['truth.heat_capacity_j_k']
        self.assertEqual(res['phase'], 'succeeded')
        self.assertLess(abs(res['heat_capacity_j_k'] / C - 1.0), 0.05, (res['heat_capacity_j_k'], C))

    def test15_evaluation_recovers_reaction_heat_and_enthalpy(self):
        for noise in (False, True):
            h, e, c1, c2, t0, t1, q_true = calibrated_hydrolysis(noise=noise)
            C = h.read('evaluator')['truth.heat_capacity_j_k']
            ev = call(h, 'calorimetry.evaluate', experiment_id=e, start_s=t0, end_s=t1, limiting_reagent_mol=1.0, heat_capacity_j_k=C)
            self.assertEqual((ev['ua_basis'], ev['interpolation'], ev['quality']), ('bracketing_calibrations', 'dosed_mass', ['ok']))
            self.assertLess(abs(ev['heat_released_j'] / q_true - 1.0), 0.02, (ev['heat_released_j'], q_true))
            dh = rt.library()['reactions']['acetic_anhydride_hydrolysis']['dh']
            self.assertLess(abs(ev['reaction_enthalpy_j_mol'] / dh - 1.0), 0.03)
            self.assertGreater(ev['final_thermal_conversion'], 0.99)

    def test16_accumulation_and_mtsr(self):
        h, e, c1, c2, t0, t1, q_true = calibrated_hydrolysis(noise=False)
        C = h.read('evaluator')['truth.heat_capacity_j_k']
        ev = h.invoke('calorimetry.evaluate', experiment_id=e, start_s=t0, end_s=t1, limiting_reagent_mol=None, heat_capacity_j_k=C)
        self.assertIsNone(ev['reaction_enthalpy_j_mol'])
        self.assertTrue(0.05 < ev['max_accumulation'] < 0.6, ev['max_accumulation'])
        self.assertGreater(ev['max_mtsr_k'], 313.15)
        self.assertAlmostEqual(ev['adiabatic_temperature_rise_k'], ev['heat_released_j'] / C, places=9)
        pts = ev['points']
        self.assertTrue(all(0.0 <= p['accumulation'] <= 1.0 for p in pts))
        self.assertTrue(all(p['mtsr_k'] >= p['tr_k'] - 1e-9 for p in pts))

    def test17_evaluation_requires_calibrations(self):
        h = with_water(**QUIET)
        e = call(h, 'experiment.start', name='x')['experiment_id']
        settle_tr(h, s=600.0)
        rejected(self, h, 'UA_CALIBRATION_REQUIRED', 'calorimetry.evaluate', experiment_id=e, start_s=0.0, end_s=600.0, limiting_reagent_mol=None, heat_capacity_j_k=None)
        call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0); h.step(2200.0)
        rejected(self, h, 'HEAT_CAPACITY_REQUIRED', 'calorimetry.evaluate', experiment_id=e, start_s=0.0, end_s=600.0, limiting_reagent_mol=None, heat_capacity_j_k=None)
        rejected(self, h, 'UNKNOWN_EXPERIMENT', 'calorimetry.evaluate', experiment_id='exp-99', start_s=0.0, end_s=600.0, limiting_reagent_mol=None, heat_capacity_j_k=None)
        rejected(self, h, 'WINDOW_TOO_SHORT', 'calorimetry.evaluate', experiment_id=e, start_s=100.0, end_s=110.0, limiting_reagent_mol=None, heat_capacity_j_k=3000.0)

    def test18_calibration_fails_when_conditions_change(self):
        h = with_water(**QUIET)
        settle_tr(h)
        op = call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0)['operation_id']
        h.step(700.0)
        self.assertTrue(h.read('evaluator')['cal_heater.on'])
        call(h, 'stirrer.set', rpm=300.0)
        res = h.invoke('calibration.result', operation_id=op)
        self.assertEqual((res['phase'], res['reason']), ('failed', 'CONDITIONS_CHANGED'))
        s = h.read('evaluator')
        self.assertFalse(s['cal_heater.on'] or s['calibration.running'])
        self.assertIsNone(s['calibration.ua_w_k'])

    def test19_dosing_completion_and_cancellation_residue(self):
        h = with_water(**QUIET)
        op = call(h, 'dosing.start', substance_id='ethanol', rate_kg_s=1e-3, total_mass_kg=0.05, feed_temperature_k=293.15)['operation_id']
        self.assertEqual(h.operations[op]['phase'], 'running')
        h.step(60.0)
        self.assertEqual(h.operations[op]['phase'], 'succeeded')
        self.assertAlmostEqual(h.ledger()['dosed_kg'], 0.05, places=12)
        op2 = call(h, 'dosing.start', substance_id='ethanol', rate_kg_s=1e-3, total_mass_kg=0.05, feed_temperature_k=None)['operation_id']
        h.step(20.0)
        res = call(h, 'dosing.stop')
        self.assertEqual((res['phase'], res['reason']), ('cancelled', 'USER_CANCEL'))
        self.assertAlmostEqual(res['dosed_kg'], 0.02, places=9)                 # partial dosing stays in the reactor
        self.assertAlmostEqual(h.ledger()['dosed_kg'], 0.07, places=9)
        self.assertFalse(h.read('evaluator')['dosing.active'])
        rejected(self, h, 'NOT_DOSING', 'dosing.stop')
        self.assertNotEqual(op, op2)

    # ---------------------------------------------------------------- safety system
    def test20_limit_violations_trigger_programs(self):
        h = with_water(T=298.15, **QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tr_max_k=313.15))
        call(h, 'thermostat.set_tj', end_k=333.15, ramp_s=0.0)
        run_until(h, lambda: h.emergency is not None, 7200.0)
        self.assertEqual((h.emergency['program'], h.emergency['cause']), ('A', 'TR_ABOVE_MAX'))
        h2 = with_water(T=298.15, **QUIET)
        call(h2, 'safety.configure', **dict(SAFETY_EXAMPLE, tr_min_k=293.15, tsafe_k=298.15, tj_min_k=253.15))
        call(h2, 'thermostat.set_tj', end_k=285.15, ramp_s=0.0)
        run_until(h2, lambda: h2.emergency is not None, 7200.0)
        self.assertEqual(h2.emergency['program'], 'C')
        self.assertIn('TR_BELOW_MIN', h2.emergency['cause'])

    def test21_emergency_a_actions(self):
        h = with_water(**QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, rsafe_rpm=100.0))
        settle_tr(h)
        call(h, 'calibration.run', pre_s=300.0, heat_s=300.0, post_s=300.0); h.step(400.0)
        self.assertTrue(h.read('evaluator')['cal_heater.on'])
        call(h, 'emergency.trigger')
        h.step(4.0)
        s = h.read('evaluator')
        self.assertEqual((s['emergency.program'], s['status.ok'], s['safety_relay.closed'], s['thermostat.mode'], s['cal_heater.on']), ('A', False, False, 'tj', False))
        self.assertEqual((s['truth.heater_power_w'], s['truth.valve_opening'], h.safety_valve), (0.0, 1.0, True))
        self.assertEqual(s['stirrer.setpoint_rpm'], 100.0)                                                 # Rsafe
        self.assertEqual(h.operations[h.calibrations[-1]['operation_id']]['phase'] if h.calibrations else 'failed', 'failed')

    def test22_emergency_blocks_changes_and_reset_needs_clear_cause(self):
        h = with_water(T=298.15, **QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tr_max_k=313.15))
        call(h, 'thermostat.set_tj', end_k=333.15, ramp_s=0.0)
        run_until(h, lambda: h.emergency is not None, 7200.0)
        rejected(self, h, 'EMERGENCY_ACTIVE', 'thermostat.set_tj', end_k=300.0, ramp_s=0.0)
        rejected(self, h, 'EMERGENCY_ACTIVE', 'stirrer.set', rpm=100.0)
        rejected(self, h, 'EMERGENCY_ACTIVE', 'dosing.start', substance_id='water', rate_kg_s=1e-4, total_mass_kg=0.01, feed_temperature_k=None)
        if h._active_causes():
            rejected(self, h, 'CAUSE_PERSISTS', 'emergency.reset')
        run_until(h, lambda: not h._active_causes(), 3600.0)
        call(h, 'emergency.reset')
        s = h.read('evaluator')
        self.assertEqual((s['emergency.active'], s['status.ok'], s['safety_relay.closed'], s['thermostat.mode']), (False, True, True, 'tj'))

    def test23_b1_and_c_move_to_tsafe_at_0p1_k_per_s(self):
        h = with_water(T=333.15, **QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tsafe_k=303.15, rmin_rpm=50.0))
        call(h, 'thermostat.set_tj', end_k=333.15, ramp_s=0.0); h.step(600.0)
        call(h, 'stirrer.set', rpm=0.0)
        run_until(h, lambda: h.emergency is not None, 400.0)
        self.assertEqual((h.emergency['program'], h.emergency['cause']), ('B1', 'STIRRER_OFF_3MIN'))
        sp0 = h.read('evaluator')['thermostat.tj_setpoint_k']
        h.step(100.0)
        sp1 = h.read('evaluator')['thermostat.tj_setpoint_k']
        self.assertAlmostEqual((sp0 - sp1) / 100.0, EMERGENCY_GRADIENT_K_S, delta=0.011)
        h.step(600.0)
        self.assertAlmostEqual(h.read('evaluator')['thermostat.tj_setpoint_k'], 303.15, places=6)

    def test24_tc_above_max_for_3_min_triggers_a(self):
        h = with_water(**QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tc_max_k=285.15))            # coolant 16 C: the cold oil is above 12 C
        h.step(10.0)
        self.assertIn('TC_ABOVE_MAX', [e['code'] for e in h.events])
        self.assertIsNone(h.emergency)
        h.step(TC_MAX_DELAY_S)
        self.assertEqual((h.emergency['program'], h.emergency['cause']), ('A', 'TC_ABOVE_MAX_3MIN'))

    def test25_more_than_one_cause_triggers_a(self):
        # RC1e 5.6: one C cause -> C; two causes in the same cycle -> A (cycle logic driven with crafted indicated values)
        for low_tj, want in ((False, 'C'), (True, 'A')):
            h = with_water(T=298.15, **QUIET)
            h.step(4.0)
            h.meas = dict(h.meas, tr=h.safety['tr_min_k'] - 1.0, tj=h.safety['tj_min_k'] - (1.0 if low_tj else -30.0))
            h._safety_cycle()
            self.assertEqual(h.emergency['program'], want, h.emergency)
            self.assertEqual('+' in h.emergency['cause'], low_tj)

    def test26_emergency_button_is_never_guarded(self):
        h = with_water(**QUIET)
        h.register_principal('a1', 'agent'); h.register_principal('a2', 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        r = s1.invoke(instance_id=h.instance_id, capability_id='experiment.start', arguments=dict(name='a1-run'), request_id='x1')
        self.assertEqual(r['status'], 'succeeded')
        r = s2.invoke(instance_id=h.instance_id, capability_id='thermostat.set_tj', arguments=dict(end_k=320.0, ramp_s=0.0), request_id='y1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = s2.invoke(instance_id=h.instance_id, capability_id='emergency.trigger', arguments={}, request_id='y2')
        self.assertEqual((r['status'], r['result']['program']), ('succeeded', 'A'))

    # ---------------------------------------------------------------- stirrer, reactor, readings
    def test27_glass_stirrer_torque_limit(self):
        h = plant(**QUIET)
        call(h, 'reactor.charge', substances=[dict(substance_id='glycerol', mass_kg=1.26)], temperature_k=298.15, viscosity_pa_s=1.4, thermal_conductivity_w_m_k=0.29,
             accept_priors=True)
        call(h, 'stirrer.set', rpm=600.0)
        h.step(20.0)
        s = h.read('evaluator')
        self.assertLess(s['stirrer.indicated_rpm'], 600.0)
        self.assertAlmostEqual(s['truth.stirrer_torque_nm'], GLASS_STIRRER_TORQUE_MAX_NM, delta=1e-3)
        h2 = with_water(rpm=600.0, **QUIET)
        h2.step(20.0)
        self.assertEqual(h2.read('evaluator')['stirrer.indicated_rpm'], 600.0)

    def test28_stirrer_angle_mirror_modulo_one_turn(self):
        h = with_water(rpm=240.0, **QUIET)
        h.step(10.0)
        a0 = h.read('evaluator')['stirrer.angle_rad']
        h.step(0.125)                                                          # 240 rpm = 4 rev/s: half a turn
        a1 = h.read('evaluator')['stirrer.angle_rad']
        self.assertAlmostEqual((a1 - a0) % (2 * math.pi), math.pi, places=6)
        self.assertTrue(0.0 <= a1 < STIRRER_JOINT_MAX_RAD)

    def test29_discharge_drains_and_switches_to_tj(self):
        h = with_water(kg=0.8, **QUIET)
        settle_tr(h, s=300.0)
        call(h, 'reactor.discharge', open=True)
        h.step(2.0)
        self.assertTrue(h.read('evaluator')['drain.open'])
        run_until(h, lambda: h.read('evaluator')['thermostat.mode'] == 'tj', 600.0)
        self.assertIn('TR_SENSOR_NOT_IMMERSED', [e['code'] for e in h.events])
        h.step(120.0)
        s = h.read('evaluator')
        self.assertLess(s['truth.volume_m3'], 1e-9)
        self.assertAlmostEqual(h.ledger()['discharged_kg'], 0.8, places=9)
        call(h, 'reactor.discharge', open=False); h.step(2.0)
        self.assertEqual(h.read('evaluator')['drain.angle_rad'], 0.0)

    def test30_bubble_point_guard(self):
        h = plant(**QUIET)
        call(h, 'reactor.charge', substances=[dict(substance_id='dichloromethane', mass_kg=0.9)], temperature_k=293.15, viscosity_pa_s=0.00041, thermal_conductivity_w_m_k=0.14,
             accept_priors=True)
        bp = h.read('evaluator')['truth.bubble_point_k']
        self.assertAlmostEqual(bp, rt.library()['substances']['dichloromethane']['tb'], places=3)
        rejected(self, h, 'ABOVE_BUBBLE_POINT', 'thermostat.set_tr', end_k=bp - 1.0, ramp_s=0.0, controller='organic')
        call(h, 'thermostat.set_tr', end_k=bp - 5.0, ramp_s=0.0, controller='organic')

    def test31_readings_are_quantised_with_offsets(self):
        h = with_water(tr_offset_k=0.2, tj_offset_k=-0.1, sensor_noise_k=0.0)
        h.step(600.0)
        s = h.read('evaluator')
        for k in ('reactor.indicated_k', 'jacket.indicated_k', 'cooling_oil.indicated_k'):
            self.assertAlmostEqual(s[k] / RESOLUTION_K, round(s[k] / RESOLUTION_K), places=6)
        h.step(0.5)                                                            # readings only change on the 2 s cycle
        self.assertEqual(h.read('evaluator')['reactor.indicated_k'], s['reactor.indicated_k'])
        self.assertAlmostEqual(s['reactor.indicated_k'] - s['truth.reactor_k'], 0.2, delta=0.02)

    def test32_host_step_independence(self):
        a, b = with_water(), with_water()
        for h, dt in ((a, 2.0), (b, 60.0)):
            call(h, 'thermostat.set_tr', end_k=333.15, ramp_s=300.0, controller='aqueous')
            for _ in range(int(1200 / dt)):
                h.step(dt)
        sa, sb = state(a), state(b)
        for k in ('truth.reactor_k', 'truth.jacket_k', 'truth.cooling_oil_k', 'reactor.indicated_k'):
            self.assertAlmostEqual(sa[k], sb[k], places=9, msg=k)

    def test33_pi_stability_bound(self):
        self.assertLessEqual(KP_TJ_W_K * CYCLE_S / PLANT_FIT['C_J'], 0.5)
        h = plant(**QUIET)
        call(h, 'thermostat.set_tj', end_k=323.15, ramp_s=0.0)
        h.step(1800.0)
        tj = []
        for _ in range(60):
            h.step(2.0); tj.append(h.read('evaluator')['truth.jacket_k'])
        self.assertLess(max(tj) - min(tj), 0.05)
        self.assertAlmostEqual(sum(tj) / len(tj), 323.15, delta=0.1)

    def test34_power_loss_means_no_cooling(self):
        h, *_ = calibrated_hydrolysis(noise=False)
        call(h, 'dosing.start', substance_id='acetic_anhydride', rate_kg_s=1e-3, total_mass_kg=0.05, feed_temperature_k=313.15)
        h.step(30.0)
        call(h, 'power.set', enabled=False)
        T0 = h.read('evaluator')['truth.reactor_k']
        h.step(120.0)
        s = h.read('evaluator')
        self.assertGreater(s['truth.reactor_k'], T0 + 0.5)                                                 # reaction continues without cooling
        self.assertEqual((s['truth.heater_power_w'], s['truth.valve_opening'], s['dosing.active'], s['reactor.indicated_k']), (0.0, 0.0, False, None))

    def test35_kirchhoff_consistent_adiabatic_reaction(self):
        h = MtRc1mxhfcalRuntime()
        call(h, 'plant.configure', **dict(PLANT_EXAMPLE, **QUIET))
        call(h, 'reactor.charge', substances=[dict(substance_id='water', mass_kg=0.5), dict(substance_id='acetic_anhydride', mass_kg=0.05)], temperature_k=298.15,
             viscosity_pa_s=0.001, thermal_conductivity_w_m_k=0.55, accept_priors=True)
        call(h, 'reaction.configure', **HYDROLYSIS)
        subs = rt.library()['substances']
        n0 = dict(h.n)
        E0 = rt.formation_enthalpy(h.n) + h._C_r() * (h.T[0] - T_REF)
        h._react(600.0)
        E1 = rt.formation_enthalpy(h.n) + h._C_r() * (h.T[0] - T_REF)
        self.assertAlmostEqual(E0, E1, delta=1e-6 * abs(E0))
        xi = n0['acetic_anhydride'] - h.n['acetic_anhydride']
        self.assertGreater(xi, 0.0)
        self.assertAlmostEqual(h.led['reaction_heat_j'].value, -rt.library()['reactions']['acetic_anhydride_hydrolysis']['dh'] * xi, delta=1e-6)
        self.assertIn('acetic_acid', h.n)
        self.assertGreater(subs['acetic_acid']['cp_mol'], 0.0)

    # ---------------------------------------------------------------- host protocol
    def test36_request_binding_and_unsupported(self):
        h = with_water()
        h.register_principal('a1', 'agent')
        s = h.session('a1')
        r1 = s.invoke(instance_id=h.instance_id, capability_id='stirrer.set', arguments=dict(rpm=300.0), request_id='q')
        r2 = s.invoke(instance_id=h.instance_id, capability_id='stirrer.set', arguments=dict(rpm=300.0), request_id='q')
        self.assertEqual(r1, r2)
        r3 = s.invoke(instance_id=h.instance_id, capability_id='stirrer.set', arguments=dict(rpm=400.0), request_id='q')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')
        bad = s.invoke(instance_id=h.instance_id, capability_id='stirrer.set', arguments=dict(rpm=5.0), request_id='b')
        self.assertEqual(s.invoke(instance_id=h.instance_id, capability_id='stirrer.set', arguments=dict(rpm=5.0), request_id='b'), bad)   # rejections bind too
        for k in UNSUPPORTED:
            r = s.invoke(instance_id=h.instance_id, capability_id=k, arguments={}, request_id='u-' + k)
            self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))

    def test37_ownership_of_experiments_and_operations(self):
        h = with_water(**QUIET)
        for p, v in (('a1', 'agent'), ('a2', 'agent'), ('ev', 'evaluator')):
            h.register_principal(p, v)
        s1, s2, ev = h.session('a1'), h.session('a2'), h.session('ev')
        e = s1.invoke(instance_id=h.instance_id, capability_id='experiment.start', arguments=dict(name='mine'), request_id='1')['result']['experiment_id']
        h.step(20.0)
        self.assertTrue(s2.read(instance_id=h.instance_id, state_ids=['experiment.started_by_other_agent'])['observations']['experiment.started_by_other_agent']['value'])
        self.assertFalse(s1.read(instance_id=h.instance_id, state_ids=['experiment.started_by_other_agent'])['observations']['experiment.started_by_other_agent']['value'])
        for k, a in (('experiment.data', dict(experiment_id=e)), ('experiment.end', dict(end_mode='keep', end_temperature_k=None)), ('stirrer.toggle', {}),
                     ('calibration.run', dict(pre_s=300.0, heat_s=300.0, post_s=300.0))):
            self.assertEqual(s2.invoke(instance_id=h.instance_id, capability_id=k, arguments=a, request_id='2' + k)['error']['code'], 'ACCESS_DENIED', k)
        self.assertEqual(ev.invoke(instance_id=h.instance_id, capability_id='experiment.data', arguments=dict(experiment_id=e), request_id='3')['status'], 'succeeded')
        op = s1.invoke(instance_id=h.instance_id, capability_id='dosing.start',
                       arguments=dict(substance_id='water', rate_kg_s=1e-4, total_mass_kg=0.01, feed_temperature_k=None), request_id='4')['operation_id']
        with self.assertRaises(ActionRejected):
            s2.poll(op)
        self.assertEqual(s1.poll(op)['status'], 'running')
        r = s1.cancel(op, 'c1')
        self.assertEqual(r['result']['phase'], 'cancelled')
        self.assertEqual(s1.cancel(op, 'c1'), r)                               # replay
        with self.assertRaises(ActionRejected) as ex:
            s1.cancel(op, 'c2')
        self.assertEqual(str(ex.exception), 'NOT_RUNNING')

    def test38_calibration_cancel_leaves_no_ua(self):
        h = with_water(**QUIET)
        h.register_principal('a1', 'agent')
        s = h.session('a1')
        settle_tr(h, s=600.0)
        op = s.invoke(instance_id=h.instance_id, capability_id='calibration.run', arguments=dict(pre_s=300.0, heat_s=300.0, post_s=300.0), request_id='k')['operation_id']
        h.step(400.0)
        self.assertTrue(h.read('evaluator')['cal_heater.on'])
        s.cancel(op, 'kc')
        st = h.read('evaluator')
        self.assertFalse(st['cal_heater.on'] or st['calibration.running'])
        self.assertIsNone(st['calibration.ua_w_k'])
        self.assertEqual(s.poll(op)['status'], 'cancelled')

    def test39_record_decimation_and_event_cap(self):
        h = plant(**QUIET)
        e = call(h, 'experiment.start', name='long')['experiment_id']
        h.step(MAX_POINTS * CYCLE_S + 200.0)
        d = call(h, 'experiment.data', experiment_id=e)
        self.assertLessEqual(len(d['points']), MAX_POINTS)
        self.assertEqual(d['interval_s'], 2 * CYCLE_S)
        for i in range(MAX_EVENTS + 5):
            h._event('task', 'X', str(i))
        ev = call(h, 'events.read', since_s=0.0)
        self.assertEqual((len(ev['events']), ev['dropped'] >= 5), (MAX_EVENTS, True))
        end = call(h, 'experiment.end', end_mode='cool_to', end_temperature_k=298.15)
        self.assertEqual(end['experiment_id'], e)
        self.assertEqual(h.read('evaluator')['thermostat.tj_setpoint_k'], 298.15)

    # ---------------------------------------------------------------- data, geometry and interface agreement
    def test40_reaction_mass_library_is_real_data(self):
        lib = json.loads(LIBRARY_PATH.read_text())
        good = [s for s in lib['substances'] if s['runtime_eligible'] and s['cp_liquid']['evidence'] == 'measured_literature' and s['density']['evidence'] == 'measured_literature']
        self.assertGreaterEqual(len(good), 50)
        self.assertEqual(lib['coverage']['synthetic_records'], 0)
        rx = lib['reactions'][0]
        self.assertEqual((rx['evidence'], rx['kinetics']), ('derived', None))
        tv = json.loads((OUT / 'template_validation.json').read_text())
        self.assertEqual(tv['status'], 'passed')

    def test41_fill_table_agrees_with_geometry_and_documented_volumes(self):
        v, hgt, a = rt.fill_table()
        self.assertLess(abs(v[-1] / NOMINAL_VOLUME_M3 - 1.0), 0.05)                          # 2.5 L nominal (RC1e 9.5)
        self.assertGreater(rt.liquid_height(OPERATING_VOLUME_M3[0]), interface.PROBE_TIP_Z - interface.REACTOR_BOTTOM_Z)   # probes immersed at 0.5 L
        self.assertLess(interface.REACTOR_BOTTOM_Z + rt.liquid_height(OPERATING_VOLUME_M3[1]), interface.JACKET_Z[1])  # 2 L still inside the jacket
        self.assertGreater(rt.wetted_area(2e-3), 1.5 * rt.wetted_area(1e-3))

    def test42_interface_keys_readouts_and_motion(self):
        for key, (capability, args) in interface.KEY_COMMANDS.items():
            self.assertIn(capability, CAPS)
            self.assertEqual(CAPS[capability]['visibility'], 'agent')
        for r in interface.READOUTS:
            self.assertIn(r['source']['bridge'][0], SLOTS)
        for l in interface.LAMPS:
            self.assertIn(l['source']['lamp']['bridge'], SLOTS)
        for part, sid, cap_, kind, unit, hi in interface.MOTION:
            self.assertIn(sid, SLOTS)
            self.assertIn(cap_, CAPS)
        self.assertEqual((interface.STIRRER_JOINT_MAX_RAD, interface.DRAIN_OPEN_RAD), (STIRRER_JOINT_MAX_RAD, DRAIN_OPEN_RAD))
        self.assertEqual((interface.AXIS_X, interface.AXIS_Y), (json.loads(MANIFEST_PATH.read_text())['reactor']['axis_xy_m'][0],
                                                                json.loads(MANIFEST_PATH.read_text())['reactor']['axis_xy_m'][1]))

    def test43_panel_keys_toggle(self):
        h = with_water(**QUIET)
        call(h, 'stirrer.toggle')
        self.assertEqual(h.read('evaluator')['stirrer.setpoint_rpm'], 0.0)
        call(h, 'stirrer.toggle')
        self.assertEqual(h.read('evaluator')['stirrer.setpoint_rpm'], 250.0)
        call(h, 'power.toggle')
        self.assertFalse(h.read('evaluator')['power.enabled'])
        call(h, 'power.toggle')
        self.assertEqual(h.read('evaluator')['thermostat.mode'], 'tj')
        h2 = plant()
        rejected(self, h2, 'STIRRER_SETPOINT_REQUIRED', 'stirrer.toggle')

    def test44_contract_metadata_consistent(self):
        m = export_mt_rc1mxhfcal_contract_metadata()
        self.assertEqual(set(m['capabilities']), set(CAPS))
        self.assertEqual(set(m['state_slots']), set(SLOTS))
        for k, c in m['capabilities'].items():
            self.assertTrue(set(e['code'] for e in c['errors']) <= set(ERROR_ZH), k)
            for p in c['preconditions']:
                self.assertTrue(set(p['state_ids']) <= set(SLOTS), (k, p['state_ids']))
            self.assertTrue(set(OBSERVES.get(k, [])) <= set(SLOTS), k)
        self.assertTrue(set(rt.ERRORS) <= set(ERROR_ZH))
        params = m['mechanisms']['rc1_heat_flow_calorimetry']['parameters']
        self.assertEqual(params['fit_u_w200']['evidence'], 'assumption')
        self.assertIn('RC1e', params['fit_u_w200']['uncertainty'])
        self.assertTrue(all(v['evidence'] in ('documented', 'measured', 'photo_estimate', 'assumption', 'unknown') for v in params.values()))

    def test45_safety_configure_checks_current_values_and_rmax(self):
        h = with_water(rpm=500.0, T=313.15, **QUIET)
        h.step(60.0)
        rejected(self, h, 'LIMIT_VIOLATED_NOW', 'safety.configure', **dict(SAFETY_EXAMPLE, tr_max_k=300.0, tsafe_k=298.15))
        rejected(self, h, 'STIRRER_ABOVE_RMAX', 'safety.configure', **dict(SAFETY_EXAMPLE, rmax_rpm=400.0))
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, rmax_rpm=600.0))
        rejected(self, h, 'OUTSIDE_STIRRER_RANGE', 'stirrer.set', rpm=700.0)

    def test46_pi_option_shows_integral_tail(self):
        errs = {}
        for ctl in ('aqueous', 'aqueous_pi'):
            h = with_water(**QUIET)
            settle_tr(h, controller=ctl)
            op = call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0)['operation_id']
            h.step(2200.0)
            r = h.invoke('calibration.result', operation_id=op)
            errs[ctl] = (r['phase'], r['reason'], None if r['ua_w_k'] is None else abs(r['ua_w_k'] / h.read('evaluator')['truth.ua_w_k'] - 1.0))
        self.assertEqual(errs['aqueous'][:2], ('succeeded', None))
        self.assertLess(errs['aqueous'][2], 0.01)
        # the documented P-only default gives the cleaner calibration: the PI tail either fails the steady-state check or biases UA more
        self.assertTrue(errs['aqueous_pi'][:2] == ('failed', 'CALIBRATION_NOT_STEADY') or errs['aqueous_pi'][2] > errs['aqueous'][2], errs)

    def test47_charge_mixing_energy(self):
        h = MtRc1mxhfcalRuntime()
        call(h, 'plant.configure', **dict(PLANT_EXAMPLE, **QUIET))
        call(h, 'reactor.charge', substances=[dict(substance_id='water', mass_kg=0.5)], temperature_k=353.15, viscosity_pa_s=0.001, thermal_conductivity_w_m_k=0.6,
             accept_priors=True)
        C1 = h._C_r()
        T1 = h.T[0]
        call(h, 'reactor.charge', substances=[dict(substance_id='water', mass_kg=0.5)], temperature_k=293.15, viscosity_pa_s=0.001, thermal_conductivity_w_m_k=0.6,
             accept_priors=True)
        C2 = h._C_r()
        half = C2 - C1
        self.assertAlmostEqual(h.T[0], (C1 * T1 + half * 293.15) / C2, places=9)

    # ---------------------------------------------------------------- independent review round 1 (2026-09-25): regression tests
    def test48_b1_and_c_start_from_the_current_temperature(self):
        h = with_water(kg=1.0, T=298.15, **QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, rmin_rpm=30.0))
        call(h, 'thermostat.set_tr', end_k=353.15, ramp_s=0.0, controller='aqueous')
        call(h, 'stirrer.set', rpm=0.0)                                     # Tr step heating, stirrer switched off by the operator
        run_until(h, lambda: h.read('evaluator')['emergency.program'] is not None, 400.0)
        s = h.read('evaluator')
        self.assertEqual((s['emergency.program'], s['emergency.cause']), ('B1', 'STIRRER_OFF_3MIN'))
        self.assertLessEqual(s['thermostat.tj_setpoint_k'], s['jacket.indicated_k'] + 0.011)   # ramp from the current Tj, not the cascade set point
        tj0 = s['truth.jacket_k']
        rise = 0.0
        for _ in range(100):
            h.step(2.0)
            rise = max(rise, h.read('evaluator')['truth.jacket_k'] - tj0)
        self.assertLess(rise, 0.5)
        # C in the Tj mode from the current (indicated) Tj: a cold charge pulls Tr below Tr min while the jacket set point is elsewhere
        h = with_water(kg=1.0, T=353.15, **QUIET)
        call(h, 'thermostat.set_tj', end_k=353.15, ramp_s=0.0)
        h.step(1800.0)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tr_min_k=340.15, tsafe_k=345.15, tr_max_k=393.15, tj_max_k=423.15))
        call(h, 'thermostat.set_tj', end_k=363.15, ramp_s=3600.0)
        h.step(20.0)
        call(h, 'reactor.charge', substances=[dict(substance_id='water', mass_kg=0.4)], temperature_k=275.15, viscosity_pa_s=0.00089,
             thermal_conductivity_w_m_k=0.6, accept_priors=True)
        run_until(h, lambda: h.read('evaluator')['emergency.program'] is not None, 20.0)
        s = h.read('evaluator')
        self.assertEqual((s['emergency.program'], s['emergency.cause']), ('C', 'TR_BELOW_MIN'))
        self.assertLess(abs(s['thermostat.tj_setpoint_k'] - s['jacket.indicated_k']), 0.3)

    def test49_stirrer_emergencies_can_be_reset(self):
        h = with_water(kg=1.0, T=298.15, **QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, rmin_rpm=30.0))
        call(h, 'stirrer.set', rpm=0.0)
        h.step(200.0)
        self.assertEqual(h.read('evaluator')['emergency.program'], 'B1')
        rejected(self, h, 'EMERGENCY_ACTIVE', 'stirrer.set', rpm=200.0)   # no changes while the program is active (RC1e 5.5)
        call(h, 'emergency.reset')                                          # delayed cause: its delay restarts
        s = h.read('evaluator')
        self.assertIsNone(s['emergency.program'])
        self.assertEqual(h._stir_off_since, h.time_s)
        call(h, 'stirrer.set', rpm=200.0)
        h.step(400.0)
        self.assertIsNone(h.read('evaluator')['emergency.program'])
        # Rsafe = 0: the program stops the stirrer, which is not a manual stop and does not block the reset
        h = with_water(kg=1.0, T=298.15, **QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, rmin_rpm=30.0, rsafe_rpm=0.0))
        h.step(10.0)
        call(h, 'emergency.trigger')
        h.step(240.0)
        self.assertEqual(h.read('evaluator')['emergency.cause'], 'EMERGENCY_BUTTON')
        call(h, 'emergency.reset')
        h.step(400.0)
        self.assertIsNone(h.read('evaluator')['emergency.program'])
        # an immediate cause still blocks the reset
        h = with_water(kg=1.0, T=298.15, **QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tr_max_k=393.15))
        h._enter_emergency('C', 'TEST')
        h.meas = dict(h.meas, tr=250.0)                                    # below Tr min in this cycle
        rejected(self, h, 'CAUSE_PERSISTS', 'emergency.reset')

    def test50_tdiff_released_while_tr_rises_during_cooling(self):
        h = with_water(kg=0.8, T=313.15, **QUIET)
        call(h, 'reactor.charge', substances=[dict(substance_id='acetic_anhydride', mass_kg=0.3)], temperature_k=313.15, viscosity_pa_s=0.00089,
             thermal_conductivity_w_m_k=0.6, accept_priors=True)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tdiff_max_k=10.0, tj_min_k=233.15, tr_max_k=393.15))
        call(h, 'thermostat.set_tr', end_k=313.15, ramp_s=0.0, controller='aqueous')
        h.step(1200.0)
        call(h, 'reaction.configure', **dict(HYDROLYSIS, reactions=[dict(HYDROLYSIS['reactions'][0], log10_a=5.0)]))
        lifted, prev = 0, h.read('evaluator')['reactor.indicated_k']
        for _ in range(900):
            h.step(2.0)
            s = h.read('evaluator')
            tr, tj = s['reactor.indicated_k'], s['thermostat.tj_setpoint_k']
            lifted += tr > prev and tj < tr - 10.0 - 1e-6
            prev = tr
        self.assertGreater(lifted, 0)                                       # RC1e 9.6: restriction not effective while Tr rises during cooling
        # heating keeps the restriction: Tj set never above Tr + T diff max
        h = with_water(kg=1.0, T=298.15, **QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tdiff_max_k=10.0))
        call(h, 'thermostat.set_tr', end_k=353.15, ramp_s=0.0, controller='aqueous')
        for _ in range(300):
            h.step(2.0)
            s = h.read('evaluator')
            self.assertLessEqual(s['thermostat.tj_setpoint_k'], s['reactor.indicated_k'] + 10.0 + 1e-9)

    def test51_bubble_point_guard_on_every_path(self):
        h = with_water(kg=1.0, T=298.15, **QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tj_max_k=503.15, tr_max_k=473.15, tc_max_k=323.15))
        rejected(self, h, 'ABOVE_BUBBLE_POINT', 'thermostat.set_tj', end_k=423.15, ramp_s=0.0)
        e = call(h, 'experiment.start', name='x')['experiment_id']
        rejected(self, h, 'ABOVE_BUBBLE_POINT', 'experiment.end', end_mode='cool_to', end_temperature_k=380.0)
        self.assertTrue(e)
        # volatile feed into a warm organic mass (ideal Raoult mixing): bubble point of the final composition below the Tr target
        h = plant(**QUIET)
        call(h, 'reactor.charge', substances=[dict(substance_id='toluene', mass_kg=0.8)], temperature_k=313.15, viscosity_pa_s=0.00056,
             thermal_conductivity_w_m_k=0.13, accept_priors=True)
        call(h, 'stirrer.set', rpm=250.0)
        call(h, 'thermostat.set_tr', end_k=333.15, ramp_s=0.0, controller='organic')
        h.step(1800.0)
        rejected(self, h, 'ABOVE_BUBBLE_POINT', 'dosing.start', substance_id='diethyl_ether', rate_kg_s=1e-4, total_mass_kg=0.5, feed_temperature_k=293.15)
        rejected(self, h, 'ABOVE_BUBBLE_POINT', 'reactor.charge', substances=[dict(substance_id='diethyl_ether', mass_kg=0.5)], temperature_k=293.15,
                 viscosity_pa_s=0.0005, thermal_conductivity_w_m_k=0.13, accept_priors=True)
        call(h, 'dosing.start', substance_id='ethanol', rate_kg_s=1e-4, total_mass_kg=0.01, feed_temperature_k=None)   # small, bubble point stays high
        # a runaway that reaches the bubble point is flagged (no boiling model)
        h = with_water(kg=0.8, T=333.15, **QUIET)
        call(h, 'reactor.charge', substances=[dict(substance_id='acetic_anhydride', mass_kg=0.5)], temperature_k=333.15, viscosity_pa_s=0.00089,
             thermal_conductivity_w_m_k=0.6, accept_priors=True)
        call(h, 'reaction.configure', **dict(HYDROLYSIS, reactions=[dict(HYDROLYSIS['reactions'][0], log10_a=7.0)]))
        call(h, 'power.set', enabled=False)                                 # no cooling: adiabatic runaway
        h.step(3600.0)
        codes = [ev['code'] for ev in h.events]
        self.assertIn('BOILING_NOT_MODELLED', codes)
        self.assertTrue(h._boil_log)

    def test52_evaluation_flags_incomplete_reaction(self):
        h = with_water(kg=0.8, T=313.15, noise_seed=7)
        call(h, 'reaction.configure', **dict(HYDROLYSIS, reactions=[dict(HYDROLYSIS['reactions'][0], log10_a=4.0)]))
        e = call(h, 'experiment.start', name='slow')['experiment_id']
        settle_tr(h)
        call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0)
        h.step(2200.0)
        t0 = h.time_s
        call(h, 'dosing.start', substance_id='acetic_anhydride', rate_kg_s=1.5e-4, total_mass_kg=0.10209, feed_temperature_k=None)
        h.step(1800.0)
        t1 = h.time_s
        ev = call(h, 'calorimetry.evaluate', experiment_id=e, start_s=t0, end_s=t1, limiting_reagent_mol=1.0, heat_capacity_j_k=3700.0)
        self.assertIn('reaction_incomplete_at_window_end', ev['quality'])
        ev2 = call(h, 'calorimetry.evaluate', experiment_id=e, start_s=t0 + 600.0, end_s=t1, limiting_reagent_mol=1.0, heat_capacity_j_k=3700.0)
        self.assertIn('reaction_running_at_window_start', ev2['quality'])

    def test53_cp_uses_the_measured_ramp_slope(self):
        h = with_water(kg=1.0, T=313.15, noise_seed=3)
        settle_tr(h, s=3600.0)
        call(h, 'calibration.run', pre_s=900.0, heat_s=900.0, post_s=900.0)
        h.step(2800.0)
        for dk, rate, hold in [(2.0, 2.0, 300.0), (5.0, 0.5, 600.0)]:
            hc = copy.deepcopy(h)
            op = call(hc, 'calibration.cp', delta_k=dk, rate_k_min=rate, hold_s=hold)['operation_id']
            hc.step(2 * hold + dk / rate * 60 + 60)
            r = hc.invoke('calibration.result', operation_id=op)
            true = hc.read('evaluator')['truth.heat_capacity_j_k']
            self.assertTrue(r['phase'] != 'succeeded' or abs(r['heat_capacity_j_k'] / true - 1) <= 0.10, (dk, rate, r, true))
            if r['phase'] == 'succeeded':
                self.assertGreater(r['measured_rate_k_s'], 0.0)

    def test54_short_calibrations_are_not_steady_or_honest(self):
        for subs, mu, k in [([('water', 1.0)], 0.00089, 0.6), ([('glycerol', 0.75)], 1.4, 0.285)]:
            h = plant(sensor_noise_k=0.003)
            call(h, 'reactor.charge', substances=[dict(substance_id=s, mass_kg=m) for s, m in subs], temperature_k=313.15, viscosity_pa_s=mu,
                 thermal_conductivity_w_m_k=k, accept_priors=True)
            call(h, 'stirrer.set', rpm=250.0)
            settle_tr(h, s=3600.0)
            op = call(h, 'calibration.run', pre_s=300.0, heat_s=300.0, post_s=300.0)['operation_id']
            h.step(960.0)
            r = h.invoke('calibration.result', operation_id=op)
            if r['phase'] == 'succeeded':
                err = r['ua_w_k'] / h.read('evaluator')['truth.ua_w_k'] - 1
                self.assertLessEqual(abs(err), 3 * r['uncertainty_w_k'] / r['ua_w_k'] + 0.01, r)
            else:
                self.assertEqual(r['reason'], 'CALIBRATION_NOT_STEADY')
        self.assertEqual(r['reason'], 'CALIBRATION_NOT_STEADY')              # 0.6 L glycerol: 300 s stages are far from steady

    def test55_tc_above_40c_triggers_program_c(self):
        h = plant(coolant_inlet_k=298.15, **QUIET)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tj_max_k=503.15, tr_max_k=473.15, tc_max_k=323.15))
        call(h, 'reactor.charge', substances=[dict(substance_id='glycerol', mass_kg=1.0)], temperature_k=298.15, viscosity_pa_s=0.9,
             thermal_conductivity_w_m_k=0.285, accept_priors=True)
        call(h, 'stirrer.set', rpm=200.0)
        call(h, 'thermostat.set_tj', end_k=473.15, ramp_s=0.0)
        h.step(2400.0)
        call(h, 'thermostat.set_tj', end_k=298.15, ramp_s=0.0)
        for _ in range(900):
            h.step(2.0)
            s = h.read('evaluator')
            if s['cooling_oil.indicated_k'] > 313.15:
                self.assertIsNotNone(s['emergency.program'])                # RC1e 5.6: Tc outside (Tjoil min ... +40 C) -> C
        self.assertIn('TC_OUTSIDE_LIMITS', [ev['message_zh'].split('：')[-1] for ev in h.events if ev['kind'] == 'emergency'])

    def test56_power_up_to_tsafe_with_last_stirrer_setting(self):
        h = with_water(kg=1.0, T=330.15, noise_seed=3)
        call(h, 'stirrer.set', rpm=200.0)
        call(h, 'thermostat.set_tj', end_k=340.0, ramp_s=0.0)
        h.step(600.0)
        call(h, 'power.set', enabled=False)
        h.step(600.0)
        call(h, 'power.set', enabled=True)
        s = h.read('evaluator')
        self.assertEqual(s['thermostat.tj_setpoint_k'], SAFETY_EXAMPLE['tsafe_k'])     # RC1e 5.9, and no leak of truth.jacket_k
        self.assertNotEqual(s['thermostat.tj_setpoint_k'], s['truth.jacket_k'])
        self.assertEqual(s['stirrer.setpoint_rpm'], 200.0)

    def test57_dosing_records_ownership_and_cp_cancel(self):
        h = with_water(kg=0.8, T=313.15, noise_seed=5)
        h.register_principal('a', 'agent'); h.register_principal('b', 'agent')
        A, B = h.session('a'), h.session('b')
        r = A.invoke(instance_id=h.instance_id, capability_id='dosing.start', arguments=dict(substance_id='ethanol', rate_kg_s=1.234e-4, total_mass_kg=0.05,
                                                                                               feed_temperature_k=None), request_id='d1')
        op = r['operation_id']
        h.step(37.3)
        polled = A.poll(op)['result']['dosed_kg']
        self.assertGreater(polled, 0.0)
        self.assertAlmostEqual(polled / 1e-4, round(polled / 1e-4), places=6)   # 0.1 g balance reading
        self.assertEqual(polled, h.read('agent')['dosing.indicated_mass_kg'])
        denied = B.invoke(instance_id=h.instance_id, capability_id='dosing.stop', arguments={}, request_id='s1')
        self.assertEqual(denied['error']['code'], 'ACCESS_DENIED')
        stop = A.invoke(instance_id=h.instance_id, capability_id='dosing.stop', arguments={}, request_id='s2')['result']
        self.assertAlmostEqual(stop['dosed_kg'] / 1e-4, round(stop['dosed_kg'] / 1e-4), places=6)
        self.assertTrue(all(len(ev['message_zh'].split('：')[-1].rstrip('g').split('.')[-1]) <= 1 for ev in h.events if ev['code'].startswith('DOSING_C')))
        # cancelling a heat-capacity ramp holds the Tr set point where it is
        settle_tr(h, s=1800.0)
        call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0)
        h.step(2200.0)
        cp = A.invoke(instance_id=h.instance_id, capability_id='calibration.cp', arguments=dict(delta_k=5.0, rate_k_min=0.5, hold_s=300.0), request_id='c1')['operation_id']
        h.step(300.0 + 240.0)
        A.cancel(cp, 'x1')
        held = h.read('agent')['thermostat.tr_setpoint_k']
        h.step(600.0)
        self.assertAlmostEqual(h.read('agent')['thermostat.tr_setpoint_k'], held, places=9)
        self.assertLess(held, 313.15 + 5.0 - 1.0)

    def test58_small_review_items(self):
        # stirrer angle independent of the host step partition during spin-up (exact for constant acceleration)
        angles = []
        for dt in (2.0, 0.5, 0.01):
            h = with_water(kg=1.0, T=298.15, rpm=0.0, **QUIET)
            h.step(2.0)
            call(h, 'stirrer.set', rpm=250.0)
            n = int(round(6.0 / dt))
            for _ in range(n):
                h.step(dt)
            angles.append(h.stir_angle)
        self.assertLess(max(abs(math.remainder(a - angles[0], 2 * math.pi)) for a in angles), 1e-6)
        # a full drain leaves nothing on record
        h = with_water(kg=0.8, T=313.15, **QUIET)
        call(h, 'reactor.discharge', open=True)
        h.step(3600.0)
        self.assertEqual(h.read('agent')['reactor.charged_mass_kg'], 0.0)
        # library() hands out copies; integral floats are integers (JSON Schema)
        self.assertIsNot(rt.library(), rt.library())
        h = MtRc1mxhfcalRuntime()
        call(h, 'plant.configure', **dict(PLANT_EXAMPLE, noise_seed=7.0))
        self.assertEqual(h.plant['noise_seed'], 7)
        # library densities: the PubChem refractive index of anisole and the USP bound of glycerol are not used
        subs = rt.library()['substances']
        self.assertAlmostEqual(subs['anisole']['rho'], 995.6, places=1)
        self.assertAlmostEqual(subs['glycerol']['rho'], 1261.3, places=1)

    def test59_review_round2_items(self):
        # F17: reading noise at the schema maximum never releases T diff max while Tr is falling
        h = with_water(kg=1.0, T=333.15, sensor_noise_k=0.02, noise_seed=4)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tdiff_max_k=1.0, tr_max_k=393.15, tj_max_k=423.15))
        settle_tr(h, T=333.15)
        call(h, 'thermostat.set_tr', end_k=303.15, ramp_s=10800.0, controller='aqueous')
        prev = h.read('evaluator')['truth.reactor_k']
        for _ in range(2700):
            h.step(2.0)
            s = h.read('evaluator')
            if s['truth.reactor_k'] < prev:
                self.assertGreaterEqual(s['thermostat.tj_setpoint_k'], s['reactor.indicated_k'] - 1.0 - 1e-6)
            prev = s['truth.reactor_k']
        # F18: a Tr-mode cool-down towards Tj min stays 1 K inside the limit and does not trip program C
        h = with_water(kg=1.0, T=333.15, **QUIET)
        settle_tr(h, T=333.15)
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tj_min_k=298.15, tr_min_k=283.15, tsafe_k=303.15, tr_max_k=393.15, tj_max_k=423.15))
        call(h, 'thermostat.set_tr', end_k=305.15, ramp_s=0.0, controller='aqueous')
        for _ in range(3600):
            h.step(2.0)
            self.assertGreaterEqual(h.read('evaluator')['thermostat.tj_setpoint_k'], 298.15 + 1.0 - 1e-9)
        self.assertIsNone(h.read('evaluator')['emergency.program'])
        # I08: Tsafe must stay below the bubble point (power-up and B1/C drive the mass towards it)
        h = plant(**QUIET)
        call(h, 'reactor.charge', substances=[dict(substance_id='diethyl_ether', mass_kg=0.5)], temperature_k=293.15, viscosity_pa_s=0.0002,
             thermal_conductivity_w_m_k=0.13, accept_priors=True)
        rejected(self, h, 'ABOVE_BUBBLE_POINT', 'safety.configure', **dict(SAFETY_EXAMPLE, tsafe_k=312.15))
        call(h, 'safety.configure', **dict(SAFETY_EXAMPLE, tsafe_k=298.15))
        # I09: a baseline-only window gets no reaction edge flags
        h = with_water(kg=0.8, T=313.15, sensor_noise_k=0.02, noise_seed=5)
        e = call(h, 'experiment.start', name='baseline')['experiment_id']
        settle_tr(h)
        call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0)
        h.step(2200.0)
        t0 = h.time_s
        h.step(1800.0)
        ev = call(h, 'calorimetry.evaluate', experiment_id=e, start_s=t0, end_s=h.time_s, limiting_reagent_mol=None, heat_capacity_j_k=3700.0)
        self.assertFalse({'reaction_incomplete_at_window_end', 'reaction_running_at_window_start'} & set(ev['quality']), ev['quality'])

    def test60_review_round3_items(self):
        # F19: a small, slow reaction at the schema-maximum noise is still flagged as incomplete at the window end
        h = with_water(kg=0.8, T=313.15, sensor_noise_k=0.02, noise_seed=2)
        call(h, 'reaction.configure', **dict(HYDROLYSIS, reactions=[dict(HYDROLYSIS['reactions'][0], log10_a=4.0)]))
        e = call(h, 'experiment.start', name='small')['experiment_id']
        settle_tr(h)
        call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0)
        h.step(2200.0)
        t0 = h.time_s
        call(h, 'dosing.start', substance_id='acetic_anhydride', rate_kg_s=1.5e-4, total_mass_kg=0.3 * 0.10209, feed_temperature_k=None)
        h.step(1800.0)
        ev = call(h, 'calorimetry.evaluate', experiment_id=e, start_s=t0, end_s=h.time_s, limiting_reagent_mol=0.3, heat_capacity_j_k=3700.0)
        self.assertIn('reaction_incomplete_at_window_end', ev['quality'])
        # I11: a Tj window narrower than the two 1 K cascade margins + 1 K is rejected
        rejected(self, h, 'INVALID_SAFETY_SETTINGS', 'safety.configure', **dict(SAFETY_EXAMPLE, tj_min_k=300.0, tj_max_k=301.0))
        # I10: at default noise a slow runaway releases T diff max (threshold 0.06 K/min floor)
        self.assertLess(TDIFF_RELEASE_K_S, 2e-3)

    def test61_edge_flags_on_decimated_records(self):
        """F20: the evaluation statistics are defined in seconds, so a record thinned to 4 s spacing keeps both edge flags."""
        saved = rt.MAX_POINTS
        rt.MAX_POINTS = 1500                                                   # decimation after 50 min instead of 11 h
        try:
            h = with_water(kg=0.8, T=313.15, noise_seed=3)
            e = call(h, 'experiment.start', name='long')['experiment_id']
            settle_tr(h, s=3000.0)
            call(h, 'calibration.run', pre_s=600.0, heat_s=900.0, post_s=600.0)
            h.step(2200.0)
            self.assertEqual(h.experiment['interval'], 4.0)
            call(h, 'reaction.configure', **HYDROLYSIS)
            t0 = h.time_s
            call(h, 'dosing.start', substance_id='acetic_anhydride', rate_kg_s=1.5e-4, total_mass_kg=0.10209, feed_temperature_k=None)
            h.step(3600.0)
            late = call(h, 'calorimetry.evaluate', experiment_id=e, start_s=t0 + 300.0, end_s=h.time_s, limiting_reagent_mol=1.0, heat_capacity_j_k=3700.0)
            self.assertIn('reaction_running_at_window_start', late['quality'])
            short = call(h, 'calorimetry.evaluate', experiment_id=e, start_s=t0, end_s=t0 + 600.0, limiting_reagent_mol=1.0, heat_capacity_j_k=3700.0)
            self.assertIn('reaction_incomplete_at_window_end', short['quality'])
            full = call(h, 'calorimetry.evaluate', experiment_id=e, start_s=t0, end_s=h.time_s, limiting_reagent_mol=1.0, heat_capacity_j_k=3700.0)
            self.assertFalse({'reaction_running_at_window_start', 'reaction_incomplete_at_window_end'} & set(full['quality']), full['quality'])
        finally:
            rt.MAX_POINTS = saved


if __name__ == '__main__':
    unittest.main()
