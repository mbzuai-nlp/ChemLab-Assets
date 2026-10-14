"""CHEM-146 ZwickRoell Mflow runtime tests: python3 -m unittest tests/test_asset_zwick_mflow.py

Themes (pipeline.md): setpoints only write targets; read/describe/poll are pure; rejected preconditions leave the state
unchanged; two instances are isolated; agent/evaluator truth isolation and result redaction; mass and energy
conservation; real return values validate against the contract schemas; async completion / cancellation residue.
Physics: PI heating and recovery, melting/compaction, die-flow transfer (load, temperature, friction, history),
quantisation of the encoder / time base / display, cutter half turns, external weighing, ISO method A/B arithmetic.
No native scene, robot or hardware claims.
"""
import copy
import importlib.util
import json
import math
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.asset_runtime.zwick_mflow import *  # noqa: E402,F401,F403
from scripts.asset_runtime import zwick_mflow as rt  # noqa: E402

PROCESS_EVIDENCE = {}
CHECKS = []
_spec = importlib.util.spec_from_file_location('mflow_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
asset_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(asset_tool)
_ispec = importlib.util.spec_from_file_location('mflow_interface', ROOT / 'outputs/expansion_batch04/t1_thermal/zwick_mflow/interface_definition.py')
interface = importlib.util.module_from_spec(_ispec)
_ispec.loader.exec_module(interface)
TARGET = 523.15


def call(h, k, **a):
    """Invoke through the runtime, validate the returned value and check that only declared slots changed."""
    before = h.read('debug')
    r = h.invoke(k, **a)
    validate(CAPS[k]['output_schema'], r)
    problems = asset_tool.validate_value(r, CAPS[k]['output_schema'])
    if problems:
        raise AssertionError(f'{k}: {problems}')
    after = h.read('debug')
    delta = {x for x in after if before[x] != after[x]}
    if not delta <= set(CAPS[k]['effects']):
        raise AssertionError(f'{k}: undeclared state changes {sorted(delta - set(CAPS[k]["effects"]))}')
    PROCESS_EVIDENCE.setdefault(k, []).append(dict(time_s=h.time_s, phase=h.phase, result_keys=sorted(r), actual_call=True))
    return r


def configured(plant=None, method=None, balance=True):
    h = ZwickMflowRuntime()
    call(h, 'plant.configure', **dict(PLANT_EXAMPLE, **(plant or {})))
    if balance:
        call(h, 'balance.configure', **BALANCE_EXAMPLE)
    call(h, 'method.configure', **dict(METHOD_EXAMPLE, **(method or {})))
    return h


def heated(plant=None, method=None, target=TARGET, seconds=1500.0):
    h = configured(plant, method)
    call(h, 'power.set', enabled=True)
    call(h, 'thermal.set_temperature', target_k=target)
    h.step(seconds)
    return h


def charged(plant=None, method=None, sample=None, target=TARGET, preheat=True, load=2.16):
    """Heated barrel, charge loaded, piston inserted (piston load only) and preheated, then the test weight applied."""
    h = heated(plant, method, target)
    call(h, 'sample.load', **dict(SAMPLE_EXAMPLE, **(sample or {})))
    call(h, 'piston.insert')
    h.step(8.0)
    if preheat:
        h.step(h.method['preheat_time_s'] - 8.0 + 1.0)
    call(h, 'weight.configure', total_load_kg=load)
    return h


def run(h, limit=900.0):
    call(h, 'acquisition.start')
    t = 0.0
    while h.phase == 'running' and t < limit:
        h.step(1.0)
        t += 1.0
    h.step(30.0)        # let the automatic weighings settle
    return call(h, 'acquisition.result')


def reference_mvr():
    ref = library()[SAMPLE_EXAMPLE['reference_id']]
    return next(m['mvr_m3_s'] for m in ref['measurements'] if m['mvr_m3_s'] is not None and abs(m['reference_temperature_K'] - 523.15) < 1e-9)


class MflowTest(unittest.TestCase):
    def check(self, condition, key, **data):
        self.assertTrue(condition, key)
        CHECKS.append(dict(id=key, status='passed', layer='process', **data))

    def balanced(self, h):
        b = call(h, 'ledger.read')
        throughput = b['heater_input_j'] + b['ambient_export_j'] + abs(b['host_work_j'])
        tol = 1e-6 + 1e-11 * throughput                        # double-precision accumulation over millions of sub-steps
        self.check(abs(b['energy_residual_j']) < tol, 'energy_balance', residual_j=b['energy_residual_j'], tolerance_j=tol)
        self.check(abs(b['mass_residual_kg']) < 1e-15, 'mass_balance', residual_kg=b['mass_residual_kg'])
        return b

    def reject(self, h, k, code=None, **a):
        state = copy.deepcopy(h.__dict__)
        with self.assertRaises(ActionRejected) as e:
            h.invoke(k, **a)
        if code:
            self.assertEqual(str(e.exception), code)
        self.assertEqual(state, h.__dict__)

    # ------------------------------------------------------------ documented constants and geometry agreement
    def test01_documented_constants(self):
        self.assertAlmostEqual(BARREL_DIAMETER_M, 0.00955)
        self.assertAlmostEqual(DIE_DIAMETER_M, 0.002095)
        self.assertAlmostEqual(DIE_LENGTH_M, 0.008)
        self.assertEqual(LOAD_RANGE_KG, (0.325, 21.6))
        self.assertEqual(TEMPERATURE_RANGE_K, (323.15, 723.15))
        self.assertAlmostEqual(ENCODER_RESOLUTION_M, 3e-6)
        self.assertAlmostEqual(TIME_RESOLUTION_S, 0.002)
        self.assertAlmostEqual(STANDARD_AREA_M2 * 1e4, 0.7106, places=3)          # ISO 1133 mean area 0.711 cm^2
        self.check(True, 'documented_specifications_and_iso_area')

    def test02_runtime_matches_geometry_interface(self):
        for q in [0.0, 0.01, 0.05, 0.08, 0.107, 0.12, 0.18]:
            self.assertAlmostEqual(sensor_arm_angle(q), interface.sensor_arm_angle(q), places=12)
        self.assertAlmostEqual(SENSOR_RANGE_M[1], interface.SENSOR_CONTACT_RANGE_M[1], places=12)
        self.assertAlmostEqual(PISTON_TRAVEL_M, interface.PISTON_TRAVEL_M)
        self.assertAlmostEqual(DIE_DIAMETER_M, interface.DIE_DIAMETER_M)
        self.assertEqual({m[1] for m in interface.MOTION}, {'piston.position_m', 'sensor_arm.angle_rad', 'cutter.angle_rad', 'power.angle_rad'})
        for part, sid, capability, attr, unit, hi in interface.MOTION:
            self.assertIn(sid, SLOTS)
            self.assertIn(capability, CAPS)
        for row in interface.READOUTS:
            self.assertIn(row['source']['bridge'][0], SLOTS)
        for key, capability in interface.KEY_COMMANDS.items():
            self.assertIn(capability, CAPS)
        self.check(True, 'runtime_constants_equal_geometry_interface')

    # ------------------------------------------------------------ setpoints, heating, control
    def test03_setpoint_only_writes_target(self):
        h = configured()
        call(h, 'power.set', enabled=True)
        T = h.T_b
        call(h, 'thermal.set_temperature', target_k=TARGET)
        self.assertEqual(h.T_b, T)
        self.assertEqual(h.read()['thermal.target_k'], TARGET)
        h.step(1.0)
        self.assertGreater(h.T_b, T)
        self.check(True, 'set_temperature_is_target_only')

    def test04_heat_up_rate_and_stability(self):
        h = configured()
        call(h, 'power.set', enabled=True)
        call(h, 'thermal.set_temperature', target_k=TARGET)
        h.step(60.0)
        rate = (h.T_b - PLANT_EXAMPLE['ambient_k']) / 60.0
        self.assertGreater(rate, 0.3)            # full heater power, >0.3 K/s at the start
        times = []
        for t in range(1, 1801):
            h.step(1.0)
            if abs(h.T_b - TARGET) < 0.1 and not times:
                times.append(t + 60)
        self.assertTrue(times and times[0] < 1200, times)
        self.assertLess(abs(h.T_b - TARGET), 0.01)
        self.balanced(h)
        self.check(True, 'pi_heating_within_20min_and_steady_state', first_within_0p1K_s=times[0], initial_rate_k_s=rate)

    def test05_cooling_after_power_off(self):
        h = heated()
        call(h, 'power.set', enabled=False)
        T0 = h.T_b
        h.step(600.0)
        tau = PLANT_EXAMPLE['barrel_heat_capacity_j_k'] / PLANT_EXAMPLE['barrel_loss_w_k']
        expected = PLANT_EXAMPLE['ambient_k'] + (T0 - PLANT_EXAMPLE['ambient_k']) * math.exp(-600.0 / tau)
        self.assertAlmostEqual(h.T_b, expected, delta=0.05)
        self.assertIsNone(h.read()['thermal.indicated_k'])
        self.balanced(h)
        self.check(True, 'passive_cooling_matches_analytic_first_order')

    def test06_display_quantisation(self):
        h = heated(seconds=200.0)
        v = h.read()['thermal.indicated_k']
        self.assertAlmostEqual(v / 0.01, round(v / 0.01), places=6)
        self.assertLessEqual(abs(v - h.T_b), 0.005 + 1e-9)
        self.check(True, 'temperature_display_0p01K')

    # ------------------------------------------------------------ loading, melting, compaction
    def test07_loading_requires_withdrawn_piston(self):
        h = heated()
        call(h, 'piston.insert')
        h.step(20.0)
        self.reject(h, 'sample.load', 'PISTON_NOT_WITHDRAWN', **SAMPLE_EXAMPLE)
        call(h, 'piston.withdraw')
        h.step(20.0)
        r = call(h, 'sample.load', **SAMPLE_EXAMPLE)
        self.assertAlmostEqual(r['column_height_m'], 0.006 / (800.0 * STANDARD_AREA_M2))
        self.reject(h, 'sample.load', 'SAMPLE_PRESENT', **SAMPLE_EXAMPLE)
        self.check(True, 'loading_geometry_and_column_height')

    def test08_melting_compaction_and_premature_flow(self):
        h = heated()
        call(h, 'sample.load', **SAMPLE_EXAMPLE)
        call(h, 'piston.insert')
        h.step(20.0)
        self.assertTrue(h.contact)
        x0 = h.x
        self.assertAlmostEqual(x0, 0.006 / (800.0 * STANDARD_AREA_M2), places=9)
        h.step(280.0)
        melted_height = h.m_s / (SAMPLE_EXAMPLE['melt_density_kg_m3'] * STANDARD_AREA_M2)
        self.assertAlmostEqual(h.x, melted_height, places=9)
        self.assertEqual(h.pellet, 0.0)
        self.assertGreater(h.strand_kg, 0.0)
        self.assertLess(h.strand_kg, 0.1 * SAMPLE_EXAMPLE['mass_kg'])      # piston-only preload keeps premature flow small
        self.assertGreater(h._sample_T(), TARGET - 2.0)
        self.balanced(h)
        self.check(True, 'melting_compaction_premature_flow', piston_mm=h.x * 1000, strand_g=h.strand_kg * 1000)

    def test09_resolidification_does_not_restore_pellets(self):
        h = heated()
        call(h, 'sample.load', **SAMPLE_EXAMPLE)
        call(h, 'piston.insert')
        h.step(300.0)
        x = h.x
        call(h, 'power.set', enabled=False)
        h.step(3000.0)
        self.assertLess(h._sample_T(), SAMPLE_EXAMPLE['melting_low_k'])
        self.assertEqual(h.pellet, 0.0)
        self.assertLessEqual(h.x, x)
        self.balanced(h)
        self.check(True, 'monotonic_compaction')

    def test10_cold_charge_barrel_recovery(self):
        h = heated()
        call(h, 'sample.load', **SAMPLE_EXAMPLE)
        call(h, 'piston.insert')
        dips = []
        for _ in range(240):
            h.step(1.0)
            dips.append(h.T_b)
        self.assertLess(min(dips), TARGET)
        self.assertLess(abs(h.T_b - TARGET), 0.3)          # back within ISO-like tolerance within 4 min
        self.balanced(h)
        self.check(True, 'temperature_recovery_after_charging', dip_k=TARGET - min(dips))

    # ------------------------------------------------------------ die flow and ISO results
    def test11_reference_condition_mvr(self):
        h = charged()
        r = run(h)
        self.assertEqual(r['phase'], 'succeeded')
        self.assertEqual(r['validity'], 'valid')
        mvr = r['flow']['mvr_m3_s']
        ref = reference_mvr()
        friction = ((2.16 * GRAVITY - PLANT_EXAMPLE['piston_friction_n']) / (2.16 * GRAVITY)) ** (1 / SAMPLE_EXAMPLE['power_law_index'])
        self.assertLess(abs(mvr / ref - 1.0), 0.03)
        self.assertLess(mvr, ref * math.exp(SAMPLE_EXAMPLE['history_rate_per_s'] * 600))
        self.assertGreater(r['flow']['mfr_kg_s'], 0.0)
        self.assertAlmostEqual(r['flow']['density_at_test_temperature_kg_m3'], SAMPLE_EXAMPLE['melt_density_kg_m3'], delta=15.0)
        self.assertEqual(r['flow']['weighed_segments'], sum(1 for s in r['segments'] if s['measurement_segment']))
        self.balanced(h)
        self.check(True, 'iso_reference_condition_recovered', mvr_cm3_10min=mvr * 6e8, reference_cm3_10min=ref * 6e8, friction_factor=friction)

    def test12_independent_flow_law_at_contact(self):
        h = charged()
        T, phi = h._sample_state()
        n, Ea = SAMPLE_EXAMPLE['power_law_index'], SAMPLE_EXAMPLE['activation_energy_j_mol']
        expected = (reference_mvr() * ((2.16 * GRAVITY - PLANT_EXAMPLE['piston_friction_n']) / (2.16 * GRAVITY)) ** (1 / n)
                    * math.exp(-(Ea / (n * GAS_CONSTANT)) * (1 / T - 1 / 523.15)) * math.exp(SAMPLE_EXAMPLE['history_rate_per_s'] * h.t_hist) * phi ** 3)
        self.assertAlmostEqual(h._flow(T, phi), expected, delta=expected * 1e-12)
        self.check(True, 'independent_power_law_transfer_formula')

    def test13_load_scaling_power_law(self):
        a = charged(method=dict(test_load_kg=2.16), load=2.16)
        b = charged(method=dict(test_load_kg=5.0), load=5.0)
        T = a._sample_T()
        ratio = b._flow(T, 1.0) / a._flow(T, 1.0)
        n = SAMPLE_EXAMPLE['power_law_index']
        f = PLANT_EXAMPLE['piston_friction_n']
        self.assertAlmostEqual(ratio, ((5.0 * GRAVITY - f) / (2.16 * GRAVITY - f)) ** (1 / n), places=9)
        self.check(True, 'load_scaling_one_over_n')

    def test14_temperature_scaling_arrhenius(self):
        h = charged()
        n, Ea = SAMPLE_EXAMPLE['power_law_index'], SAMPLE_EXAMPLE['activation_energy_j_mol']
        r = h._flow(533.15, 1.0) / h._flow(523.15, 1.0)
        self.assertAlmostEqual(r, math.exp(-(Ea / (n * GAS_CONSTANT)) * (1 / 533.15 - 1 / 523.15)), places=9)
        self.assertGreater(r, 1.0)
        self.check(True, 'arrhenius_temperature_shift')

    def test15_friction_reduces_flow_and_heats_barrel(self):
        a = charged(plant=dict(piston_friction_n=0.0))
        b = charged(plant=dict(piston_friction_n=2.0))
        T = a._sample_T()
        self.assertGreater(a._flow(T, 1.0), b._flow(T, 1.0))
        rb = run(b)
        self.assertGreater(b.book['friction_heat_j'], 0.0)
        self.assertLess(rb['flow']['mvr_m3_s'], reference_mvr())
        self.balanced(b)
        self.check(True, 'piston_friction_effect')

    def test16_thermal_history_changes_flow(self):
        a = charged(sample=dict(history_rate_per_s=0.0))
        b = charged(sample=dict(history_rate_per_s=5e-4))
        T = a._sample_T()
        self.assertAlmostEqual(b._flow(T, 1.0) / a._flow(T, 1.0), math.exp(5e-4 * b.t_hist), places=9)
        self.assertGreater(b.t_hist, 100.0)
        self.check(True, 'thermal_history_multiplier')

    def test17_preheat_and_load_guards(self):
        h = heated()
        call(h, 'sample.load', **SAMPLE_EXAMPLE)
        call(h, 'piston.insert')
        h.step(30.0)
        call(h, 'weight.configure', total_load_kg=2.16)
        self.reject(h, 'acquisition.start', 'PREHEAT_NOT_COMPLETE')
        h.step(300.0)
        call(h, 'weight.configure', total_load_kg=5.0)
        self.reject(h, 'acquisition.start', 'LOAD_MISMATCH')
        call(h, 'thermal.set_temperature', target_k=533.15)
        call(h, 'weight.configure', total_load_kg=2.16)
        self.reject(h, 'acquisition.start', 'TEMPERATURE_NOT_STABLE')
        self.check(True, 'acquisition_preconditions')

    def test18_window_and_encoder_quantisation(self):
        h = charged()
        r = run(h)
        pos = [x['encoder_position_m'] for x in r['rows'] if x['encoder_position_m'] is not None]
        self.assertTrue(all(abs(p / ENCODER_RESOLUTION_M - round(p / ENCODER_RESOLUTION_M)) < 1e-6 for p in pos))
        times = [x['time_s'] for x in r['rows']]
        self.assertTrue(all(abs(t / TIME_RESOLUTION_S - round(t / TIME_RESOLUTION_S)) < 1e-6 for t in times))
        a, b = r['flow']['window_positions_m']
        self.assertLessEqual(a, METHOD_EXAMPLE['window_upper_m'])
        self.assertGreaterEqual(b, METHOD_EXAMPLE['window_lower_m'])
        manual = analyze_flow(a, b, *r['flow']['window_times_s'], STANDARD_AREA_M2, [], None)
        self.assertAlmostEqual(manual['mvr_m3_s'], r['flow']['mvr_m3_s'], places=18)
        self.check(True, 'encoder_3um_time_2ms_quantised_window')

    def test19_segments_mass_conservation_and_weighing(self):
        h = charged()
        r = run(h)
        segs = r['segments']
        self.assertFalse(segs[0]['measurement_segment'])                    # trim cut removes the preheat extrudate
        meas = [s for s in segs if s['measurement_segment']]
        self.assertTrue(all(s['status'] == 'weighed' for s in meas))
        self.assertTrue(all(abs(s['duration_s'] - METHOD_EXAMPLE['cut_interval_s']) < 1e-9 for s in meas))
        for s in meas:
            true = next(g['true_mass_kg'] for g in h.segments if g['segment_id'] == s['segment_id'])
            self.assertLessEqual(abs(s['observed_mass_kg'] - true), BALANCE_EXAMPLE['resolution_kg'] / 2 + 1e-15)
        b = self.balanced(h)
        self.assertAlmostEqual(b['extruded_mass_kg'], b['strand_mass_kg'] + b['segments_mass_kg'] + b['collected_mass_kg'], places=15)
        self.check(True, 'cut_segments_weighed_on_external_balance', segments=len(meas))

    def test20_manual_cut_half_turn(self):
        h = charged()
        call(h, 'piston.insert') if not h.contact else None
        strand = h.strand_kg
        self.assertGreater(strand, 0.0)
        call(h, 'cutter.cut')
        self.reject(h, 'cutter.cut', 'CUTTER_BUSY')
        h.step(PLANT_EXAMPLE['cutter_half_turn_s'] * 0.25)
        self.assertAlmostEqual(h.cutter_angle, math.pi * 0.25, places=6)
        self.assertGreater(h.strand_kg, strand)                                # still uncut before pi/2
        h.step(PLANT_EXAMPLE['cutter_half_turn_s'] * 0.30)
        self.assertEqual(len(h.segments), 1)
        h.step(1.0)
        self.assertEqual(h.cutter_angle, math.pi)
        call(h, 'cutter.cut')
        h.step(1.0)
        self.assertEqual(h.cutter_angle, 0.0)                                   # alternate half turns
        self.assertEqual(len(h.segments), 2)
        self.balanced(h)
        self.check(True, 'cutter_alternate_half_turn_sever_at_pi_over_2')

    def test21_balance_resolution_gain_offset_settling(self):
        h = charged(method=dict(automatic_weighing=False))
        call(h, 'balance.configure', **dict(BALANCE_EXAMPLE, resolution_kg=1e-6, gain=1.01, offset_kg=2e-6, settling_s=6.0))
        r = run(h)
        seg = next(s for s in r['segments'] if s['measurement_segment'])
        self.assertEqual(seg['status'], 'unweighed')
        call(h, 'balance.weigh', segment_id=seg['segment_id'])
        self.reject(h, 'balance.weigh', 'SEGMENT_ALREADY_WEIGHED_OR_QUEUED', segment_id=seg['segment_id'])
        h.step(5.9)
        self.assertEqual(next(g for g in h.segments if g['segment_id'] == seg['segment_id'])['status'], 'weighing')
        h.step(0.2)
        g = next(g for g in h.segments if g['segment_id'] == seg['segment_id'])
        self.assertEqual(g['status'], 'weighed')
        self.assertAlmostEqual(g['observed_mass_kg'], round((1.01 * g['true_mass_kg'] + 2e-6) / 1e-6) * 1e-6, places=15)
        c = call(h, 'calibration.balance', observed_low_kg=2e-6, observed_high_kg=1.01e-3 + 2e-6, reference_low_kg=0.0, reference_high_kg=1e-3)
        self.assertAlmostEqual(c['scale'], 1 / 1.01, places=9)
        self.check(True, 'external_balance_proxy_behaviour')

    def test22_encoder_two_point_calibration(self):
        h = charged()
        before = h.read()['encoder.position_m']
        c = call(h, 'calibration.encoder', observed_low_m=0.010, observed_high_m=0.061, reference_low_m=0.010, reference_high_m=0.060)
        self.assertAlmostEqual(c['scale'], 50 / 51, places=12)
        after = h.read()['encoder.position_m']
        self.assertAlmostEqual(after, quantize(c['scale'] * h.x + c['offset_m'], ENCODER_RESOLUTION_M), places=12)
        self.assertNotEqual(before, after)
        self.reject(h, 'calibration.encoder', 'DEGENERATE_CALIBRATION', observed_low_m=0.01, observed_high_m=0.01, reference_low_m=0.01, reference_high_m=0.06)
        self.check(True, 'encoder_calibration_from_external_observations')

    def test23_encoder_range_and_arm(self):
        h = configured()
        call(h, 'power.set', enabled=True)
        self.assertIsNone(h.read()['encoder.position_m'])                    # withdrawn piston above the transducer range
        self.assertAlmostEqual(h.read('debug')['sensor_arm.angle_rad'], interface.SENSOR_ARM_LIMITS_RAD[1], places=12)
        call(h, 'piston.insert')
        h.step(20.0)
        self.assertEqual(h.x, 0.0)
        self.assertEqual(h.read()['encoder.position_m'], 0.0)
        self.assertEqual(h.read('debug')['sensor_arm.angle_rad'], 0.0)
        self.check(True, 'encoder_range_and_arm_mapping')

    def test24_validity_preheat_below_iso(self):
        h = charged(method=dict(preheat_time_s=120.0))
        r = run(h)
        self.assertEqual(r['validity'], 'preheat_below_iso_minimum')
        self.assertIsNone(h.read()['measurement.mvr_m3_s'])
        self.check(True, 'iso_preheat_minimum_flag')

    def test25_validity_started_below_window(self):
        h = charged()
        h.step(60.0)                        # full load already pushes the piston through the upper window edge
        r = run(h)
        self.assertEqual(r['validity'], 'window_not_traversed')
        self.assertEqual(r['terminal_reason'], 'STARTED_BELOW_WINDOW')
        self.assertEqual(r['phase'], 'failed')
        self.check(True, 'window_must_be_traversed')

    def test26_temperature_excursion_invalidates(self):
        h = charged(method=dict(temperature_tolerance_k=0.2))
        call(h, 'acquisition.start')
        call(h, 'thermal.set_temperature', target_k=528.15)
        while h.phase == 'running':
            h.step(1.0)
        self.assertEqual(h.validity, 'temperature_out_of_tolerance')
        self.check(True, 'temperature_excursion_flag')

    def test27_duration_limit(self):
        h = charged(method=dict(duration_s=5.0))
        r = run(h)
        self.assertEqual(r['terminal_reason'], 'DURATION_ELAPSED')
        self.assertEqual(r['validity'], 'window_not_traversed')
        self.check(True, 'duration_limit')

    def test28_sample_exhausted(self):
        h = charged(sample=dict(mass_kg=0.0032), method=dict(window_lower_m=0.005, window_upper_m=0.020))
        r = run(h)
        self.assertIn(r['terminal_reason'], ('SAMPLE_EXHAUSTED', 'WINDOW_TRAVERSED', 'STARTED_BELOW_WINDOW'))
        self.balanced(h)
        self.check(True, 'small_charge_end_conditions')

    # ------------------------------------------------------------ async, cancellation, power
    def test29_stop_keeps_physics(self):
        h = charged()
        call(h, 'acquisition.start')
        h.step(12.0)
        call(h, 'acquisition.stop')
        snap = call(h, 'acquisition.result')
        x = h.x
        strand = h.strand_kg
        h.step(20.0)
        self.assertLess(h.x, x)                    # the loaded piston keeps extruding
        self.assertGreater(h.strand_kg + sum(g['true_mass_kg'] for g in h.segments), strand)
        self.assertEqual(call(h, 'acquisition.result')['rows'], snap['rows'])
        self.assertEqual(snap['phase'], 'cancelled')
        self.balanced(h)
        self.check(True, 'cancel_freezes_record_not_physics')

    def test30_power_off_cancels(self):
        h = charged()
        call(h, 'acquisition.start')
        h.step(3.0)
        call(h, 'power.set', enabled=False)
        self.assertEqual(h.phase, 'cancelled')
        self.assertEqual(h.terminal_reason, 'POWER_OFF')
        self.assertIsNone(h.read()['encoder.position_m'])
        self.assertIsNone(h.read()['thermal.indicated_k'])
        self.reject(h, 'cutter.cut', 'POWER_OFF')
        h.step(60.0)
        self.assertLess(h.T_b, TARGET)
        self.balanced(h)
        self.check(True, 'power_off_semantics')

    def test31_busy_guard_atomic(self):
        h = charged()
        call(h, 'acquisition.start')
        self.reject(h, 'weight.configure', 'BUSY', total_load_kg=5.0)
        self.reject(h, 'piston.withdraw', 'BUSY')
        self.reject(h, 'sample.remove', 'BUSY')
        self.reject(h, 'acquisition.start', 'BUSY')
        self.check(True, 'busy_rejections_atomic')

    # ------------------------------------------------------------ removal, clearing, conservation
    def test32_remove_and_clear_conserve(self):
        h = charged()
        run(h)
        call(h, 'piston.withdraw')
        h.step(15.0)
        rem = call(h, 'sample.remove')
        self.assertGreater(rem['removed_mass_kg'], 0.0)
        clr = call(h, 'collection.clear')
        self.assertGreater(clr['collected_mass_kg'], 0.0)
        b = self.balanced(h)
        self.assertAlmostEqual(b['loaded_mass_kg'], b['removed_mass_kg'] + b['collected_mass_kg'], places=15)
        self.assertGreater(b['host_work_j'], 0.0)
        self.check(True, 'remove_and_collect_close_ledgers')

    def test33_step_partition_independence(self):
        a = charged()
        b = charged()
        call(a, 'acquisition.start')
        call(b, 'acquisition.start')
        a.step(6.0)
        for _ in range(360):
            b.step(1 / 60)
        self.assertEqual(a.time_ns, b.time_ns)
        self.assertEqual(a.rows, b.rows)                                     # quantised observations identical
        self.assertAlmostEqual(a.x, b.x, delta=ENCODER_RESOLUTION_M / 10)     # explicit sub-steps: O(dt) truth difference
        self.assertAlmostEqual(a.T_b, b.T_b, delta=1e-4)
        self.balanced(a)
        self.balanced(b)
        self.check(True, 'partition_changes_truth_below_resolution_rows_identical', piston_difference_m=abs(a.x - b.x), barrel_difference_k=abs(a.T_b - b.T_b))

    def test34_fractional_nanosecond_clock(self):
        h = configured()
        t = h.time_ns
        for _ in range(60):
            call(h, 'sim.step', dt_s=1 / 60)
        self.assertEqual(h.time_ns - t, 1_000_000_000)
        self.check(True, 'fractional_ns_no_drift')

    # ------------------------------------------------------------ roles, redaction, sessions
    def test35_agent_truth_isolation(self):
        h = charged()
        agent_view = h.read('agent')
        self.assertFalse(any(k.startswith('truth.') for k in agent_view))
        for k in ('piston.position_m', 'piston.target_m', 'sensor_arm.angle_rad', 'cutter.angle_rad', 'extrudate.length_m', 'heater.output_fraction'):
            self.assertNotIn(k, agent_view)
        h.register_principal('a', 'agent')
        s = h.session('a')
        r = s.invoke(instance_id=h.instance_id, capability_id='provenance.read', arguments={}, request_id='p')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = s.invoke(instance_id=h.instance_id, capability_id='sample.remove', arguments={}, request_id='x')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        with self.assertRaises(ActionRejected):
            s.read(instance_id=h.instance_id, state_ids=['truth.flow_m3_s'])
        self.check(True, 'agent_cannot_read_truth_or_provenance')

    def test36_result_redacted_for_all_roles(self):
        h = charged()
        run(h)
        for role in ['agent', 'evaluator', 'debug']:
            h.register_principal(role, role)
            h._owners[h.operation_id] = role
            s = h.session(role)
            r = s.invoke(instance_id=h.instance_id, capability_id='acquisition.result', arguments={}, request_id='r')
            payload = json.dumps(r) + json.dumps(s.poll(h.operation_id))
            self.assertNotIn(SAMPLE_EXAMPLE['reference_id'], payload)
            self.assertNotIn('B4520', payload)
            self.assertNotIn('Ultradur', payload)
        p = call(h, 'provenance.read')
        self.assertIn('basf_ultradur_b4520', p['operation_source'])
        self.check(True, 'public_result_redaction_private_provenance')

    def test37_idempotence_owner_cancel(self):
        h = charged()
        h.register_principal('a', 'agent')
        h.register_principal('b', 'agent')
        a = h.session('a')
        b = h.session('b')
        r = a.invoke(instance_id=h.instance_id, capability_id='acquisition.start', arguments={}, request_id='start')
        self.assertEqual(a.invoke(instance_id=h.instance_id, capability_id='acquisition.start', arguments={}, request_id='start'), r)
        h.step(1.0)
        with self.assertRaises(ActionRejected):
            b.poll(r['operation_id'])
        denied = b.invoke(instance_id=h.instance_id, capability_id='acquisition.stop', arguments={}, request_id='s')
        self.assertEqual(denied['error']['code'], 'ACCESS_DENIED')
        c = a.cancel(r['operation_id'], 'cancel')
        self.assertEqual(a.cancel(r['operation_id'], 'cancel'), c)
        bad = a.invoke(instance_id=h.instance_id, capability_id='power.set', arguments={'enabled': False}, request_id='start')
        self.assertEqual(bad['error']['code'], 'REQUEST_CONFLICT')
        self.check(True, 'request_idempotence_and_operation_owner')

    def test38_read_describe_poll_pure(self):
        h = charged()
        h.register_principal('a', 'agent')
        s = h.session('a')
        r = s.invoke(instance_id=h.instance_id, capability_id='acquisition.start', arguments={}, request_id='s')
        h.step(2.0)
        snapshot = copy.deepcopy(h.__dict__)
        for _ in range(5):
            s.read(instance_id=h.instance_id)
            s.poll(r['operation_id'])
            s.describe(instance_id=h.instance_id)
        self.assertEqual(snapshot, h.__dict__)
        self.check(True, 'pure_read_describe_poll')

    def test39_write_via_capability(self):
        h = configured()
        h.register_principal('e', 'evaluator')
        s = h.session('e')
        r = s.write(instance_id=h.instance_id, state_id='thermal.target_k', value=493.15, request_id='w')
        self.assertEqual(r['status'], 'succeeded')
        self.assertEqual(h.target_k, 493.15)
        with self.assertRaises(ActionRejected):
            s.write(instance_id=h.instance_id, state_id='truth.sample_mass_kg', value=1.0, request_id='w2')
        self.check(True, 'read_write_slots_use_capabilities')

    def test40_instance_isolation(self):
        a = charged(sample=dict(reference_id='basf_ultradur_b4520'))
        b = charged(sample=dict(reference_id='basf_ultradur_b1520_fc_r01'))
        ra = run(a)
        self.assertEqual(b.phase, 'idle')
        rb = run(b)
        self.assertGreater(rb['flow']['mvr_m3_s'], 3 * ra['flow']['mvr_m3_s'])
        self.balanced(a)
        self.balanced(b)
        self.check(True, 'independent_instances_different_grades')

    # ------------------------------------------------------------ inputs, library, schemas, unsupported
    def test41_reference_cell_guards(self):
        h = heated()
        self.reject(h, 'sample.load', 'NO_NUMERIC_REFERENCE_AT_CONDITION', **dict(SAMPLE_EXAMPLE, reference_temperature_k=548.15))
        self.reject(h, 'sample.load', 'UNKNOWN_REFERENCE', **dict(SAMPLE_EXAMPLE, reference_id='pure_water'))
        self.reject(h, 'sample.load', 'CHARGE_EXCEEDS_BARREL', **dict(SAMPLE_EXAMPLE, mass_kg=0.02, bulk_density_kg_m3=500.0))
        self.reject(h, 'sample.load', 'ENGINEERING_ACCEPTANCE_REQUIRED', **dict(SAMPLE_EXAMPLE, accept_rheology_thermal_density_and_packing_assumptions=False))
        self.reject(h, 'sample.load', 'INVALID_MELTING_RANGE', **dict(SAMPLE_EXAMPLE, melting_low_k=500.0, melting_high_k=490.0))
        self.check(True, 'reference_cell_and_input_guards')

    def test42_library_real_references(self):
        records = library()
        self.assertGreaterEqual(len(records), 50)
        numeric = 0
        for r in records.values():
            self.assertTrue(r['source'])
            self.assertIsNone(r['melt_density_kg_m3'])
            self.assertIsNone(r['power_law_exponent'])
            for m in r['measurements']:
                if m['mvr_m3_s'] is not None:
                    numeric += 1
                    self.assertAlmostEqual(m['mvr_m3_s'], float(m['raw_value']) * 1e-6 / 600, places=18)
        self.assertGreaterEqual(numeric, 60)
        self.check(True, 'real_material_library_units', grades=len(records), numeric_cells=numeric)

    def test43_every_numeric_cell_loadable(self):
        loaded = 0
        for rid, r in library().items():
            for m in r['measurements']:
                if m['mvr_m3_s'] is None:
                    continue
                h = configured()
                call(h, 'sample.load', **dict(SAMPLE_EXAMPLE, reference_id=rid, reference_temperature_k=m['reference_temperature_K'], reference_load_kg=m['reference_load_kg']))
                loaded += 1
        self.assertGreaterEqual(loaded, 60)
        self.check(True, 'all_numeric_reference_cells_loadable', cells=loaded)

    def test44_invalid_inputs_atomic(self):
        h = configured()
        self.reject(h, 'power.set', 'INVALID_ARGUMENT', enabled=1)
        self.reject(h, 'method.configure', 'INVALID_ARGUMENT', **dict(METHOD_EXAMPLE, sample_interval_s=float('nan')))
        self.reject(h, 'method.configure', 'INVALID_WINDOW', **dict(METHOD_EXAMPLE, window_upper_m=0.02, window_lower_m=0.05))
        self.reject(h, 'method.configure', 'SAMPLING_NOT_ON_TIME_BASE', **dict(METHOD_EXAMPLE, sample_interval_s=0.003))
        self.reject(h, 'thermal.set_temperature', 'INVALID_ARGUMENT', target_k=800.0)
        self.reject(h, 'weight.configure', 'INVALID_ARGUMENT', total_load_kg=25.0)
        self.reject(h, 'plant.configure', 'PLANT_ALREADY_CONFIGURED', **PLANT_EXAMPLE)
        for dt in [True, -1, float('inf'), float('nan')]:
            before = h.time_ns
            with self.assertRaises(ActionRejected):
                h.step(dt)
            self.assertEqual(h.time_ns, before)
        g = ZwickMflowRuntime()
        self.reject(g, 'plant.configure', 'ENGINEERING_ACCEPTANCE_REQUIRED', **dict(PLANT_EXAMPLE, accept_engineering=False))
        self.reject(g, 'plant.configure', 'THERMAL_TIME_CONSTANT_OUTSIDE_NUMERICAL_DOMAIN', **dict(PLANT_EXAMPLE, barrel_heat_capacity_j_k=100.0, controller_gain_w_k=1000.0))
        self.check(True, 'input_schema_and_rejection_atomicity')

    def test45_unsupported_negative_only(self):
        h = configured()
        for k in UNSUPPORTED:
            self.reject(h, k, 'UNSUPPORTED')
        self.check(True, 'unsupported_is_negative_only')

    def test46_contract_schemas_examples_and_reads(self):
        meta = export_zwick_mflow_contract_metadata()
        schemas = [v[s] for v in CAPS.values() for s in ['input_schema', 'output_schema']] + [v['schema'] for v in SLOTS.values()]
        r = subprocess.run([sys.executable, '-c', 'import sys,json;from jsonschema import Draft202012Validator;[Draft202012Validator.check_schema(s) for s in json.load(sys.stdin)]'],
                           input=json.dumps(schemas), text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for s in schemas:
            self.assertEqual(asset_tool.lint_schema(s), [])
        for k, c in CAPS.items():
            validate(c['input_schema'], c['example_arguments'])
            self.assertEqual(meta['capabilities'][k]['availability'], 'unsupported' if k in UNSUPPORTED else 'implemented')
        h = charged()
        run(h)
        for k, v in h.read('debug').items():
            validate(SLOTS[k]['schema'], v)
            self.assertEqual(asset_tool.validate_value(v, SLOTS[k]['schema']), [])
        self.assertEqual(set(meta['state_slots']), set(SLOTS))
        self.check(True, 'all_contract_schemas_units_examples')

    def test47_analysis_pure_function(self):
        h = configured()
        r = call(h, 'analysis.flow', start_position_m=0.05, end_position_m=0.02, start_time_s=0.0, end_time_s=60.0, area_prior_m2=STANDARD_AREA_M2,
                 weighed_segments=[dict(mass_kg=3.8e-4, duration_s=10.0), dict(mass_kg=3.9e-4, duration_s=10.0)], melt_density_prior_kg_m3=1110.0)
        self.assertAlmostEqual(r['mvr_m3_s'], 0.03 * STANDARD_AREA_M2 / 60.0, places=20)
        self.assertAlmostEqual(r['mfr_g_10min'], 7.7e-4 / 20.0 * 6e5, places=9)
        self.assertAlmostEqual(r['density_derived_mfr_kg_s'], r['mvr_m3_s'] * 1110.0, places=18)
        with self.assertRaises(ActionRejected):
            h.invoke('analysis.flow', **dict(CAPS['analysis.flow']['example_arguments'], end_time_s=0.0))
        self.check(True, 'iso_arithmetic_on_supplied_observations')

    def test48_weight_change_host_work(self):
        h = configured()
        call(h, 'piston.insert')
        h.step(10.0)
        call(h, 'weight.configure', total_load_kg=2.16)
        call(h, 'piston.withdraw')
        h.step(12.0)
        b = self.balanced(h)
        self.assertAlmostEqual(b['load_potential_j'], 2.16 * GRAVITY * PISTON_TRAVEL_M, places=9)
        self.check(True, 'manual_lift_and_weights_host_work')

    def test49_panel_started_operation_visible_to_operators(self):
        h = charged()
        h.register_principal('panel', 'evaluator')
        h.register_principal('a', 'agent')
        h.register_principal('b', 'agent')
        started = h.session('panel').invoke(instance_id=h.instance_id, capability_id='acquisition.start', arguments={}, request_id='key')
        self.assertEqual(started['status'], 'accepted')
        h.step(2.0)
        r = h.session('a').invoke(instance_id=h.instance_id, capability_id='acquisition.result', arguments={}, request_id='r')
        self.assertEqual(r['status'], 'succeeded')
        stop = h.session('b').invoke(instance_id=h.instance_id, capability_id='acquisition.stop', arguments={}, request_id='s')
        self.assertEqual(stop['status'], 'succeeded')
        self.check(True, 'panel_or_host_started_operation_shared_with_operators')

    # ------------------------------------------------------------ regressions for the independent review findings
    def test50_agent_never_sees_exact_position(self):
        h = charged()
        call(h, 'acquisition.start')
        for _ in range(40):
            h.step(0.5)
            view = h.read('agent')
            self.assertNotIn('piston.target_m', view)
            self.assertNotIn('piston.position_m', view)
            enc = view['encoder.position_m']
            self.assertAlmostEqual(enc / ENCODER_RESOLUTION_M, round(enc / ENCODER_RESOLUTION_M), places=6)
        self.check(True, 'F01_exact_piston_position_restricted')

    def test51_finished_record_immutable_after_clear_method_change_and_other_weighings(self):
        h = charged()
        r = run(h)
        before = call(h, 'acquisition.result')
        call(h, 'method.configure', **dict(METHOD_EXAMPLE, window_upper_m=0.040, window_lower_m=0.030))
        call(h, 'collection.clear')
        h.step(20.0)                               # keep extruding, then a manual cut and its weighing
        call(h, 'cutter.cut')
        h.step(1.0)
        manual = h.segments[-1]['segment_id']
        call(h, 'balance.weigh', segment_id=manual)
        h.step(10.0)
        after = call(h, 'acquisition.result')
        self.assertEqual(after, before)
        self.assertEqual(h.read()['measurement.mvr_m3_s'], r['flow']['mvr_m3_s'])
        self.assertEqual(h.read()['measurement.mfr_kg_s'], r['flow']['mfr_kg_s'])
        self.check(True, 'F02_F03_record_immutable')

    def test52_late_weighing_reaches_its_own_record(self):
        h = charged(method=dict(automatic_weighing=False))
        first = run(h)
        seg = next(s for s in first['segments'] if s['measurement_segment'])
        call(h, 'piston.withdraw')
        h.step(10.0)
        call(h, 'sample.remove')
        call(h, 'sample.load', **SAMPLE_EXAMPLE)
        call(h, 'piston.insert')
        h.step(320.0)
        call(h, 'balance.weigh', segment_id=seg['segment_id'])
        call(h, 'acquisition.start')                  # a new run starts while the old segment is still settling
        h.step(10.0)
        h.register_principal('e', 'evaluator')
        old = h.records[first['operation_id']]
        weighed = next(s for s in old['segments'] if s['segment_id'] == seg['segment_id'])
        self.assertEqual(weighed['status'], 'weighed')
        self.assertIsNotNone(old['flow']['mfr_kg_s'])
        self.check(True, 'F04_late_weighing_backfills_owner_record')

    def test53_small_charge_high_conductance_stable(self):
        h = heated(plant=dict(sample_conductance_w_k=20.0))
        call(h, 'sample.load', **dict(SAMPLE_EXAMPLE, mass_kg=1e-4))
        for _ in range(200):
            h.step(0.05)
            T = h._sample_T()
            self.assertTrue(PLANT_EXAMPLE['ambient_k'] - 1e-9 <= T <= TARGET + 0.5, T)
        self.assertAlmostEqual(h._sample_T(), h.T_b, delta=0.05)
        self.balanced(h)
        self.check(True, 'F05_unconditionally_stable_exchange')

    def test54_history_multiplier_bounded(self):
        h = heated()
        call(h, 'sample.load', **dict(SAMPLE_EXAMPLE, history_rate_per_s=0.005))
        h.step(20 * 3600.0)
        h.step(20 * 3600.0)                         # 40 h molten (one step is limited to 24 h)
        call(h, 'piston.insert')
        h.step(30.0)
        T, phi = h._sample_state()
        self.assertTrue(math.isfinite(h._flow(T, phi)))
        self.balanced(h)
        self.check(True, 'F06_history_bounded_no_overflow')

    def test55_cutter_continuous_after_power_off_mid_turn(self):
        h = charged()
        call(h, 'cutter.cut')
        h.step(PLANT_EXAMPLE['cutter_half_turn_s'] * 0.3)
        stopped = h.cutter_angle
        call(h, 'power.set', enabled=False)
        call(h, 'power.set', enabled=True)
        call(h, 'cutter.cut')
        last = h.cutter_angle
        self.assertEqual(last, stopped)
        n = len(h.segments)
        while h._cut is not None:
            h.step(0.001)
            self.assertLessEqual(abs(h.cutter_angle - last), math.pi / PLANT_EXAMPLE['cutter_half_turn_s'] * 0.001 + 1e-9)
            last = h.cutter_angle
        self.assertEqual(h.cutter_angle, math.pi)
        self.assertEqual(len(h.segments), n + 1)
        self.check(True, 'F07_no_cutter_angle_jump')

    def test56_bulk_density_and_zero_width_melting(self):
        h = heated()
        self.reject(h, 'sample.load', 'BULK_DENSITY_EXCEEDS_MELT_DENSITY', **dict(SAMPLE_EXAMPLE, bulk_density_kg_m3=1200.0))
        call(h, 'sample.load', **dict(SAMPLE_EXAMPLE, melting_low_k=493.15, melting_high_k=493.15))
        s = h.sample
        h_lo = s['specific_heat_j_kg_k'] * (493.15 - PLANT_EXAMPLE['ambient_k'])
        T, phi = h._phase_of_h(h_lo + 0.3 * s['latent_heat_j_kg'])
        self.assertEqual(T, 493.15)
        self.assertAlmostEqual(phi, 0.3, places=12)
        self.check(True, 'F08_F09_density_and_enthalpy_melt_fraction')

    def test57_row_bound_and_structured_session_errors(self):
        h = configured()
        self.reject(h, 'method.configure', 'TOO_MANY_SAMPLES', **dict(METHOD_EXAMPLE, duration_s=3600.0, sample_interval_s=0.002))
        h.register_principal('a', 'agent')
        s = h.session('a')
        for bad in ([1], {'x': 1}, None):
            with self.assertRaises(ActionRejected):
                s.poll(bad)
            with self.assertRaises(ActionRejected):
                s.write(instance_id=h.instance_id, state_id=bad, value=1.0, request_id='w')
            with self.assertRaises(ActionRejected):
                s.cancel(bad, 'c')
        self.check(True, 'F11_F12_row_bound_structured_errors')

    def test58_segment_ids_unique_after_clear(self):
        h = charged()
        ids = []
        for _ in range(3):
            h.step(3.0)
            call(h, 'cutter.cut')
            h.step(1.0)
            ids.append(h.segments[-1]['segment_id'])
            call(h, 'collection.clear')
        self.assertEqual(len(set(ids)), 3)
        self.check(True, 'F13_unique_segment_ids')

    def test59_foreign_agent_cannot_cut_during_agent_run(self):
        h = charged()
        h.register_principal('a', 'agent')
        h.register_principal('b', 'agent')
        h.session('a').invoke(instance_id=h.instance_id, capability_id='acquisition.start', arguments={}, request_id='s')
        h.step(1.0)
        r = h.session('b').invoke(instance_id=h.instance_id, capability_id='cutter.cut', arguments={}, request_id='c')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.check(True, 'F14_foreign_agent_cut_denied')

    def test60_cycle_key_component_and_unselected_options(self):
        self.assertIn('button_cycle', CAPS['cutter.cut']['component_ids'])
        for k in ('piston.preheat_hold', 'collection.separating_pane', 'thermal.recool', 'die.change'):
            self.assertIn(k, UNSUPPORTED)
        self.assertIn('耐酸', UNSUPPORTED['material.fluorinated_or_pvc'])
        self.check(True, 'K05_K06_F16_declarations')


    def test61_die_work_leaves_with_extrudate(self):
        h = charged(method=dict(test_load_kg=21.6, window_lower_m=0.010), load=21.6)
        call(h, 'acquisition.start')
        worst = -1e9
        while h.phase == 'running':
            h.step(0.2)
            if h.m_s > 0:
                worst = max(worst, h._sample_T() - h.T_b)
        dp = 21.6 * GRAVITY / STANDARD_AREA_M2
        adiabatic = dp / (SAMPLE_EXAMPLE['melt_density_kg_m3'] * SAMPLE_EXAMPLE['specific_heat_j_kg_k'])
        self.assertLess(worst, adiabatic)
        self.balanced(h)
        self.check(True, 'N01_die_dissipation_exported_with_extrudate', worst_excess_k=worst, adiabatic_limit_k=adiabatic)

    def test62_foreign_agent_cannot_change_setpoint_during_run(self):
        h = charged()
        h.register_principal('a', 'agent')
        h.register_principal('b', 'agent')
        h.session('a').invoke(instance_id=h.instance_id, capability_id='acquisition.start', arguments={}, request_id='s')
        r = h.session('b').invoke(instance_id=h.instance_id, capability_id='thermal.set_temperature', arguments={'target_k': 533.15}, request_id='t')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = h.session('a').invoke(instance_id=h.instance_id, capability_id='thermal.set_temperature', arguments={'target_k': 523.15}, request_id='t')
        self.assertEqual(r['status'], 'succeeded')
        self.check(True, 'N02_setpoint_guarded_for_foreign_agents')

    def test63_segment_budget_keeps_result_within_schema(self):
        saved = rt.MAX_SEGMENTS
        try:
            rt.MAX_SEGMENTS = 30                      # budget = 30 - (240 // 10 + 2) = 4 manual cuts
            h = charged()
            call(h, 'acquisition.start')
            h.step(1.0)
            for _ in range(4):
                call(h, 'cutter.cut')
                h.step(0.3)
            self.reject(h, 'cutter.cut', 'TOO_MANY_SEGMENTS')
            while h.phase == 'running':
                h.step(1.0)
            self.assertLessEqual(len(call(h, 'acquisition.result')['segments']), 30)
        finally:
            rt.MAX_SEGMENTS = saved
        self.check(True, 'N03_segment_budget')



if __name__ == '__main__':
    unittest.main(verbosity=2)
