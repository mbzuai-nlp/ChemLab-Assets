"""tests for CHEM-092 camag_tlcscanner: python3 -m unittest tests/test_asset_camag_tlcscanner.py

Topics (pipeline.md): setpoints only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances are
isolated, agent / evaluator truth isolation, conservation (plate mass, nothing consumed by scanning), real returned values against the contract
schemas (asset_tool.validate_value), asynchronous completion and cancellation residue, real templates (measured Rf, NIST / HSDB absorption),
interlocks (door / stage), evaluation (peaks, Rf, spectra, matching, calibration)."""
import copy
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import camag_tlcscanner as rt  # noqa: E402

spec = importlib.util.spec_from_file_location('tlc_asset_tool', ROOT / 'skills/chemistry-lab-assets/scripts/asset_tool.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
APP, FRONT = rt.PLATE_EXAMPLE['application_y_m'], rt.PLATE_EXAMPLE['front_y_m']


def run(h, seconds, dt=0.1):
    for _ in range(int(round(seconds / dt))):
        h.step(dt)


def ready(plant=None, plate=None, method=None, warm=True):
    """Configured, powered, plate on the stage, door closed, stage home, method set, lamps warm."""
    h = rt.CamagTlcscannerRuntime('t')
    h.invoke('plant.configure', **(plant or rt.FAST_PLANT))
    h.invoke('plate.register', **copy.deepcopy(plate or rt.PLATE_EXAMPLE))
    h.invoke('power.set', enabled=True)
    h.invoke('door.set', opened=True)
    h.invoke('lamp.select', source='auto')
    run(h, 1.2)
    h.invoke('stage.load')
    run(h, 1.5)
    h.invoke('plate.place')
    h.invoke('stage.home')
    run(h, 1.5)
    h.invoke('door.set', opened=False)
    run(h, 1.2)
    h.invoke('method.configure', **copy.deepcopy(method or rt.METHOD_EXAMPLE))
    if warm:
        run(h, 2.0, 0.5)
    return h


def finish(h, cap='scan.start', **a):
    h.invoke(cap, **a)
    for _ in range(100000):
        if h.phase != 'running':
            break
        h.step(0.25)
    return h


class CamagTlcscannerRuntimeTests(unittest.TestCase):
    def reject(self, h, cap, code='INVALID_ARGUMENT', **a):
        before = h.debug_snapshot()
        with self.assertRaisesRegex(rt.ActionRejected, code):
            h.invoke(cap, **a)
        self.assertEqual(before, h.debug_snapshot())

    # ---- identity, library ---------------------------------------------------------------------------------------
    def test_01_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.camag_tlcscanner')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-092')

    def test_02_library_real_records(self):
        h = rt.CamagTlcscannerRuntime()
        self.assertGreaterEqual(len(h.templates), 50)
        self.assertGreaterEqual(len(h._uv), 50)
        self.assertEqual(sum(r['values']['n_points'] for r in h.templates.values()), 4944)
        for r in h.templates.values():
            self.assertEqual(r['evidence'], 'measured')
            self.assertIn('Apache-2.0', r['licence'])
            self.assertTrue(r['source']['data_sha256'])

    def test_03_measured_rf_used_exactly(self):
        h = rt.CamagTlcscannerRuntime()
        rec = h.templates['xu_tlc_038']
        pt = next(p for p in rec['values']['rf_points'] if abs(p['mobile_phase_volume_fraction']['ethyl_acetate'] - 0.25) < 1e-4
                  and abs(p['mobile_phase_volume_fraction']['hexane'] - 0.75) < 1e-4)
        rf, ev, fam = h.rf_for('xu_tlc_038', rt.PLATE_EXAMPLE['mobile_phase'])
        self.assertEqual((rf, ev, fam), (pt['rf'], 'measured', 'hexane/ethyl_acetate'))

    def test_04_rf_interpolated_and_rejected_outside(self):
        h = rt.CamagTlcscannerRuntime()
        mp = dict(hexane=0.7, ethyl_acetate=0.3, dichloromethane=0.0, methanol=0.0, diethyl_ether=0.0)
        rf, ev, _ = h.rf_for('xu_tlc_038', mp)
        self.assertEqual(ev, 'interpolated_measured')
        a = h.rf_for('xu_tlc_038', dict(mp, hexane=0.75, ethyl_acetate=0.25))[0]
        b = h.rf_for('xu_tlc_038', dict(mp, hexane=0.5, ethyl_acetate=0.5))[0]
        self.assertTrue(min(a, b) <= rf <= max(a, b))
        with self.assertRaisesRegex(rt.ActionRejected, 'MOBILE_PHASE_OUTSIDE_TEMPLATE'):
            h.rf_for('xu_tlc_038', dict(hexane=0.5, ethyl_acetate=0.25, dichloromethane=0.25, methanol=0.0, diethyl_ether=0.0))

    def test_05_uv_template_units(self):
        h = rt.CamagTlcscannerRuntime()
        u = h.templates['xu_tlc_382']['uv_absorption']                  # 4-nitrophenol, NIST log epsilon curve
        self.assertEqual(u['basis'], 'nist_webbook_jcamp_log_epsilon')
        self.assertTrue(300e-9 < u['lambda_max_m'] < 340e-9)
        self.assertTrue(500 < u['epsilon_max_m2_mol'] < 1000)          # log eps ~3.77 -> 5900 L/mol/cm -> 590 m2/mol

    # ---- setpoints, purity, rejections ---------------------------------------------------------------------------------
    def test_06_setpoints_only_targets(self):
        h = rt.CamagTlcscannerRuntime()
        h.invoke('plant.configure', **rt.FAST_PLANT)
        h.invoke('power.set', enabled=True)
        h.invoke('door.set', opened=True)
        self.assertEqual(h.read()['door.position_m'], 0.0)
        h.step(0.4)
        self.assertAlmostEqual(h.read()['door.position_m'], 0.1)
        run(h, 1.0)
        h.invoke('stage.load')
        self.assertEqual(h.read()['stage.y_m'], 0.0)
        self.assertEqual(h.read()['stage.target_y_m'], rt.LOAD_Y)

    def test_07_read_and_describe_pure(self):
        h = ready()
        before = h.debug_snapshot()
        h.read('evaluator'), h.read('agent'), h.describe(), h.invoke('process.measure'), h.invoke('results.read')
        self.assertEqual(before, h.debug_snapshot())

    def test_08_initialization_only(self):
        h = ready()
        self.reject(h, 'plant.configure', 'INITIALIZATION_ONLY', **rt.FAST_PLANT)

    def test_09_argument_types(self):
        h = ready()
        self.reject(h, 'power.set', enabled=1)
        self.reject(h, 'stage.jog', axis='z', distance_m=0.001)
        self.reject(h, 'method.configure', **dict(rt.METHOD_EXAMPLE, bandwidth_m=10e-9))
        self.reject(h, 'method.configure', **dict(rt.METHOD_EXAMPLE, scan_speed_m_s=0.2))
        self.reject(h, 'power.set', enabled=True, extra=1)

    def test_10_evidence_ack(self):
        h = rt.CamagTlcscannerRuntime()
        self.reject(h, 'plant.configure', 'EVIDENCE_ACK_REQUIRED', **dict(rt.FAST_PLANT, accept_uncalibrated_model=False))

    def test_11_no_template_requires_ack(self):
        h = rt.CamagTlcscannerRuntime()
        h.invoke('plant.configure', **rt.FAST_PLANT)
        p = copy.deepcopy(rt.PLATE_EXAMPLE)
        p['tracks'][0]['spots'] = [dict(substance_id='xu_tlc_001', amount_kg=1e-10)]       # no public absorption template
        self.reject(h, 'plate.register', 'NO_ABSORPTION_TEMPLATE', **p)
        h2 = rt.CamagTlcscannerRuntime()
        h2.invoke('plant.configure', **dict(rt.FAST_PLANT, accept_synthetic_response=True))
        h2.invoke('plate.register', **p)
        self.assertEqual(h2.read('evaluator')['truth.plate']['spots'][0]['uv_basis'], 'synthetic_flat_200_400nm')

    # ---- interlocks -------------------------------------------------------------------------------------------------------
    def test_12_load_needs_open_door(self):
        h = rt.CamagTlcscannerRuntime()
        h.invoke('plant.configure', **rt.FAST_PLANT)
        h.invoke('power.set', enabled=True)
        self.reject(h, 'stage.load', 'DOOR_CLOSED')

    def test_13_door_cannot_close_on_stage(self):
        h = rt.CamagTlcscannerRuntime()
        h.invoke('plant.configure', **rt.FAST_PLANT)
        h.invoke('power.set', enabled=True)
        h.invoke('door.set', opened=True)
        run(h, 1.2)
        h.invoke('stage.load')
        run(h, 1.5)
        self.assertTrue(h.read()['stage.at_load'])
        self.reject(h, 'door.set', 'STAGE_OBSTRUCTS_DOOR', opened=False)

    def test_14_scan_needs_closed_door_plate_lamp(self):
        h = ready(warm=False)
        h.lamp_on_s = 0.0
        self.reject(h, 'scan.start', 'LAMP_NOT_READY')
        run(h, 2.0, 0.5)
        h.invoke('door.set', opened=True)
        self.reject(h, 'scan.start', 'DOOR_OPEN')

    def test_15_place_needs_load_position(self):
        h = rt.CamagTlcscannerRuntime()
        h.invoke('plant.configure', **rt.FAST_PLANT)
        h.invoke('plate.register', **rt.PLATE_EXAMPLE)
        h.invoke('power.set', enabled=True)
        h.invoke('door.set', opened=True)
        run(h, 1.2)
        self.reject(h, 'plate.place', 'STAGE_NOT_AT_LOAD')

    # ---- scanning and evaluation ----------------------------------------------------------------------------------------
    def test_16_scan_finds_measured_rf(self):
        h = finish(ready())
        self.assertEqual(h.phase, 'succeeded')
        res = h.invoke('results.read')
        self.assertTrue(res['complete'])
        self.assertEqual(res['template_coverage'], 'complete')
        for tr in res['traces']:
            rfs = sorted(p['rf'] for p in tr['peaks'])
            self.assertEqual(len(rfs), 2)
            truth = sorted(h.rf_for(s, rt.PLATE_EXAMPLE['mobile_phase'])[0] for s in ('xu_tlc_038', 'xu_tlc_048'))
            for a, b in zip(rfs, truth):
                self.assertLess(abs(a - b), 0.01)

    def test_17_response_monotonic_and_saturating(self):
        res = finish(ready()).invoke('results.read')
        hi = {t['track_index']: max(t['peaks'], key=lambda p: p['rf']) for t in res['traces']}
        a1, a2 = hi[0]['area_au_m'], hi[1]['area_au_m']
        self.assertGreater(a2, a1)
        self.assertLess(a2, 2 * a1)            # Kubelka-Munk-like saturation: doubling the amount less than doubles the area

    def test_18_signal_follows_template_epsilon(self):
        m = dict(rt.METHOD_EXAMPLE, wavelengths_m=[260e-9, 400e-9], tracks_x_m=[0.015])
        plate = copy.deepcopy(rt.PLATE_EXAMPLE)
        for t in plate['tracks']:
            t['spots'] = [sp for sp in t['spots'] if sp['substance_id'] == 'xu_tlc_038']
        res = finish(ready(method=m, plate=plate)).invoke('results.read')
        top = {t['wavelength_m']: max((p for p in t['peaks'] if p['rf'] > 0.4), key=lambda p: p['height_au'], default=None) for t in res['traces']}
        self.assertIsNotNone(top[260e-9])
        h = rt.CamagTlcscannerRuntime()
        e260 = rt._gauss_conv(*h._uv['xu_tlc_038'], 260e-9, 20e-9)[0]
        e400 = rt._gauss_conv(*h._uv['xu_tlc_038'], 400e-9, 20e-9)[0]
        self.assertGreater(e260, e400)
        self.assertTrue(top[400e-9] is None or top[400e-9]['height_au'] < top[260e-9]['height_au'])

    def test_19_stage_moves_during_scan(self):
        h = ready()
        h.invoke('scan.start')
        ys = []
        for _ in range(40):
            h.step(0.1)
            ys.append(h.read()['stage.y_m'])
        self.assertGreater(max(ys) - min(ys), 0.01)
        self.assertTrue(all(rt.Y_INSIDE - 1e-9 <= y <= rt.STAGE_Y_RANGE[1] for y in ys))

    def test_20_cancel_keeps_partial(self):
        h = ready()
        h.invoke('scan.start')
        run(h, 2.0)
        n = h.read()['scan.completed_points']
        self.assertGreater(n, 0)
        h.invoke('scan.stop')
        self.assertEqual(h.phase, 'cancelled')
        run(h, 1.0)
        self.assertEqual(h.read()['scan.completed_points'], n)
        self.assertFalse(h.read()['stage.moving'])

    def test_21_spectrum_and_match(self):
        h = finish(ready())
        y = APP + h.rf_for('xu_tlc_038', rt.PLATE_EXAMPLE['mobile_phase'])[0] * (FRONT - APP)
        finish(h, 'spectrum.start', plate_x_m=0.030, plate_y_m=y, start_m=220e-9, end_m=400e-9, interval_m=2e-9)
        sp = h.invoke('spectrum.read')
        self.assertTrue(sp['complete'])
        self.assertEqual(len(sp['signal']), 91)
        top = h.invoke('spectrum.match', min_overlap_m=40e-9)['candidates']
        self.assertIn('xu_tlc_038', [c['substance_id'] for c in top[:3]])

    def test_22_quantify_uses_caller_standards_only(self):
        h = finish(ready())
        rf = h.rf_for('xu_tlc_038', rt.PLATE_EXAMPLE['mobile_phase'])[0]
        before = h.debug_snapshot()
        out = h.invoke('quantify.fit', standard_tracks=[0, 1], standard_amounts_kg=[2e-10, 4e-10], unknown_tracks=[0], target_rf=rf, rf_window=0.03,
                       wavelength_m=254e-9, model='linear')
        self.assertEqual(before, h.debug_snapshot())
        self.assertAlmostEqual(out['unknowns'][0]['estimated_amount_kg'], 2e-10, delta=1e-13)
        with self.assertRaisesRegex(rt.ActionRejected, 'CALIBRATION_INPUT'):
            h.invoke('quantify.fit', standard_tracks=[0, 1], standard_amounts_kg=[2e-10, 4e-10], unknown_tracks=[], target_rf=rf, rf_window=0.03,
                     wavelength_m=254e-9, model='quadratic')

    def test_23_fluorescence_quench_only(self):
        m = dict(rt.METHOD_EXAMPLE, mode='fluorescence')
        res = finish(ready(method=m)).invoke('results.read')
        sig = res['traces'][0]['signal']
        self.assertLess(min(sig), 1.0)                         # dark (quenched) bands on the F254 background
        self.assertTrue(all(s <= 1.0 + 1e-12 for s in sig))
        self.assertGreater(len(res['traces'][0]['peaks']), 0)
        h = ready(method=m)
        self.reject(h, 'method.configure', 'EXCITATION_ABOVE_CUTOFF', **dict(m, wavelengths_m=[450e-9]))

    # ---- isolation, conservation, protocol ---------------------------------------------------------------------------------------
    def test_24_two_instances_isolated(self):
        a, b = ready(), ready()
        finish(a)
        self.assertEqual(b.phase, 'idle')
        self.assertEqual(b.read()['scan.completed_points'], 0)
        self.assertEqual(a.phase, 'succeeded')
        self.assertGreater(a.read()['scan.completed_points'], 0)

    def test_25_agent_cannot_see_truth(self):
        h = ready()
        ag = h.session()
        self.assertNotIn('truth.plate', h.read('agent'))
        r = ag.invoke(instance_id='t', capability_id='product.read', arguments={}, request_id='x')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = ag.invoke(instance_id='t', capability_id='plate.register', arguments=rt.PLATE_EXAMPLE, request_id='y')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        d = ag.describe(instance_id='t')
        self.assertNotIn('truth.plate', d['state_slots'])
        self.assertNotIn('product.read', d['capabilities'])
        text = json.dumps(finish(h).invoke('results.read'))
        self.assertNotIn('xu_tlc_038', text)

    def test_26_mass_conserved(self):
        h = finish(ready())
        led = h.invoke('product.read')
        total = sum(s['amount_kg'] for t in rt.PLATE_EXAMPLE['tracks'] for s in t['spots'])
        self.assertAlmostEqual(led['registered_kg'], total)
        self.assertAlmostEqual(led['on_stage_kg'], total)
        self.assertEqual(led['consumed_kg'], 0.0)
        self.assertAlmostEqual(led['residual_kg'], 0.0)
        self.assertGreater(led['photon_exposure_s'], 0.0)
        self.reject(h, 'plate.register', 'PLATE_EXISTS', **rt.PLATE_EXAMPLE)
        h.invoke('door.set', opened=True)
        run(h, 1.2)
        h.invoke('stage.load')
        run(h, 2.0)
        h.invoke('plate.remove', recipient_id='rack')
        led = h.invoke('product.read')
        self.assertEqual(led['location'], 'removed')
        self.assertAlmostEqual(led['removed_kg'], total)
        self.assertAlmostEqual(led['on_stage_kg'], 0.0)
        self.assertAlmostEqual(led['residual_kg'], 0.0)
        h.invoke('plate.register', **dict(rt.PLATE_EXAMPLE, plate_id='second'))         # a new plate after the first left the instrument
        led = h.invoke('product.read')
        self.assertAlmostEqual(led['registered_kg'], 2 * total)
        self.assertAlmostEqual(led['external_kg'] + led['removed_kg'], 2 * total)
        self.assertEqual(led['plates_registered'], 2)

    def test_27_request_idempotency_and_conflict(self):
        h = ready()
        s = h.session()
        a = s.invoke(instance_id='t', capability_id='illumination.set', arguments={'enabled': True}, request_id='r1')
        b = s.invoke(instance_id='t', capability_id='illumination.set', arguments={'enabled': True}, request_id='r1')
        self.assertEqual(a, b)
        c = s.invoke(instance_id='t', capability_id='illumination.set', arguments={'enabled': False}, request_id='r1')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')

    def test_28_unsupported_explicit(self):
        h = ready()
        for cap in rt.UNSUPPORTED:
            r = h.session().invoke(instance_id='t', capability_id=cap, arguments={}, request_id=cap)
            self.assertEqual(r['status'], 'unsupported')

    def test_29_poll_and_cancel_async(self):
        h = ready()
        s = h.session()
        r = s.invoke(instance_id='t', capability_id='scan.start', arguments={}, request_id='s1')
        op = r['operation_id']
        self.assertEqual(r['status'], 'accepted')
        h.step(1.0)
        self.assertEqual(s.poll(op)['status'], 'running')
        s.cancel(op, 'c1')
        self.assertEqual(s.poll(op)['status'], 'cancelled')
        h.register_principal('other', 'agent')
        with self.assertRaises(rt.ActionRejected):
            h.session('other').poll(op)                              # operations are visible to their owner only

    def test_30_schema_of_real_returns(self):
        h = ready()
        out = {}
        out['scan.start'] = h.invoke('scan.start')
        out['process.measure'] = h.invoke('process.measure')
        while h.phase == 'running':
            h.step(0.5)
        out['results.read'] = h.invoke('results.read')
        out['template.read'] = h.invoke('template.read', substance_id='xu_tlc_038')
        out['product.read'] = h.invoke('product.read')
        for cap, value in out.items():
            self.assertEqual(tool.lint_schema(rt.CAPS[cap][1]), [])
            self.assertEqual(tool.validate_value(value, rt.CAPS[cap][1]), [], cap)
        for k, v in h.read('evaluator').items():
            self.assertEqual(tool.validate_value(v, rt.SLOTS[k][0]), [], k)

    def test_31_power_off_cancels_and_stops(self):
        h = ready()
        h.invoke('scan.start')
        run(h, 1.0)
        h.invoke('power.set', enabled=False)
        self.assertEqual(h.phase, 'cancelled')
        y = h.read()['stage.y_m']
        run(h, 1.0)
        self.assertEqual(h.read()['stage.y_m'], y)
        self.assertFalse(h.read()['lamp.ready'])

    def test_31b_power_toggle(self):
        h = rt.CamagTlcscannerRuntime()
        h.invoke('plant.configure', **rt.FAST_PLANT)
        self.assertTrue(h.invoke('power.toggle')['enabled'])
        h.invoke('lamp.select', source='auto')
        self.assertFalse(h.invoke('power.toggle')['enabled'])
        self.assertEqual(h.read()['lamp.source'], 'none')

    def test_32_wavelength_outside_lamp(self):
        h = ready()
        h.invoke('lamp.select', source='halogen_tungsten')
        run(h, 2.0, 0.5)
        self.reject(h, 'scan.start', 'WAVELENGTH_OUTSIDE_LAMP_RANGE')

    def test_33_display_coordinates(self):
        h = ready()
        v = h.read()
        self.assertAlmostEqual(v['plate.x_m'], 0.10 + rt.PLATE_EXAMPLE['width_m'] / 2)
        q = h.stage_for(0.015, 0.03)
        self.assertAlmostEqual(h.plate_coords(q)[0], 0.015)
        self.assertAlmostEqual(h.plate_coords(q)[1], 0.03)

    def test_36_partially_resolved_peaks_split(self):
        plate = copy.deepcopy(rt.PLATE_EXAMPLE)
        for t in plate['tracks']:
            t['spots'] = [dict(substance_id='xu_tlc_038', amount_kg=3e-10), dict(substance_id='xu_tlc_186', amount_kg=3e-10)]
        res = finish(ready(plate=plate)).invoke('results.read')
        h = rt.CamagTlcscannerRuntime()
        want = sorted(h.rf_for(s_, plate['mobile_phase'])[0] for s_ in ('xu_tlc_038', 'xu_tlc_186'))
        self.assertGreater(want[1] - want[0], 0.04)
        rfs = sorted(pk['rf'] for pk in res['traces'][0]['peaks'])
        self.assertEqual(len(rfs), 2, rfs)
        for a, b in zip(rfs, want):
            self.assertLess(abs(a - b), 0.015)

    def test_37_spot_at_origin_baseline(self):
        plate = copy.deepcopy(rt.PLATE_EXAMPLE)
        for t in plate['tracks']:
            t['spots'] = [dict(substance_id='xu_tlc_019', amount_kg=3e-10)]          # 1,10-phenanthroline stays at the origin (Rf ~0)
        res = finish(ready(plate=plate)).invoke('results.read')
        tr = res['traces'][0]
        top = max(tr['peaks'], key=lambda p_: p_['height_au'])
        span = max(tr['signal']) - min(tr['signal'])
        self.assertLess(abs(top['rf']), 0.03)
        self.assertGreater(top['height_au'], 0.9 * span)

    def test_38_uncovered_wavelength_refused(self):
        m = dict(rt.METHOD_EXAMPLE, wavelengths_m=[330e-9])                           # 4-aminobenzonitrile template ends at 316 nm
        h = ready(method=m)
        self.reject(h, 'scan.start', 'TEMPLATE_RANGE')
        h2 = ready()
        h2.invoke('lamp.select', source='led_366')
        run(h2, 2.0, 0.5)
        self.reject(h2, 'scan.start', 'LED_FLUORESCENCE_ONLY')

    def test_39_replicates_and_conflicts(self):
        h = rt.CamagTlcscannerRuntime()
        rec = next(r for r in h.templates.values() if r['values']['rf_conflicts'])
        mp = rec['values']['rf_conflicts'][0]['mobile_phase_volume_fraction']
        with self.assertRaisesRegex(rt.ActionRejected, 'RF_TEMPLATE_CONFLICT'):
            h.rf_for(rec['substance_id'], mp)
        rec = next(r for r in h.templates.values() if r['aliases'] and not r['values']['rf_conflicts'])
        groups = {}
        for p_ in rec['values']['rf_points']:
            groups.setdefault(tuple(sorted(p_['mobile_phase_volume_fraction'].items())), []).append(p_['rf'])
        key, vals = next((k, v) for k, v in groups.items() if len(v) > 1)
        try:
            rf, ev, _ = h.rf_for(rec['substance_id'], dict(key))
        except rt.ActionRejected:
            return                                                                    # this replicate phase is outside the three binary families
        self.assertAlmostEqual(rf, sum(vals) / len(vals))
        self.assertEqual(ev, 'measured_mean')

    def test_40_finishes_in_the_completing_step(self):
        h = ready()
        h.invoke('scan.start')
        total = sum(seg['duration'] for seg in h._schedule)
        h.step(total + 1e-9)
        self.assertEqual(h.phase, 'succeeded')
        self.assertFalse(h.read()['illumination.enabled'])

    def test_34_step_requires_nonnegative(self):
        with self.assertRaises(rt.ActionRejected):
            rt.CamagTlcscannerRuntime().step(-1.0)

    def test_35_contract_metadata(self):
        m = rt.export_camag_tlcscanner_contract_metadata()
        self.assertEqual(set(m['capabilities']), set(rt.CAPS))
        for k, c in m['capabilities'].items():
            self.assertEqual(tool.lint_schema(c['input_schema']), [], k)
            self.assertEqual(c['availability'], 'unsupported' if k in rt.UNSUPPORTED else 'implemented')
        self.assertEqual(m['state_slots']['truth.plate']['visibility'], 'evaluator')


if __name__ == '__main__':
    unittest.main()
