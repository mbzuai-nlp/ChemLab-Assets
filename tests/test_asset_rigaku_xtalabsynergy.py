"""tests for CHEM-161 rigaku_xtalabsynergy (Rigaku XtaLAB Synergy-R with the HyPix-6000HE detector): python3 -m unittest tests/test_asset_rigaku_xtalabsynergy.py

Topics (pipeline.md): settings only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances are
isolated, agent/evaluator truth isolation (truth slots, the substance of the records, fault events, other agents' records and operations),
conservation (electrical energy, X-ray and filament hours, frames and exposure, crystal dose), real return values validated against the declared
schemas (asset_tool lint_schema / validate_value), asynchronous completion and cancellation remnants, host-step independence, declared immediate
and on_step effects; plus the instrument (boot, the safety circuit with doors, Doors OK, emergency stop, X-ray key, Motion Enable; the generator
with its vacuum, spin-up, ramps, power limit and trips; shutter; divergence slit; detector threshold; the goniometer speeds, the collision model
and routed moves; crystal mounting, camera and centring; Cryostream cooling, icing) and the physics (Bragg positions and the cell, Lorentz and
polarisation, Cu versus Mo, divergence and distance, fluorescence and the energy threshold, absorption, temperature factors and thermal expansion,
radiation decay, count-rate overloads, frames and the beam-stop shadow) and the processing (pre-experiment and FFT indexing, reduction, Laue
class and space group, What is this? and the refinement, strategies, indexing failures of twinned, polycrystalline and amorphous samples).
"""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import rigaku_xtalabsynergy as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}
DEG = rt.DEG


def _meta():
    if not _META:
        _META.update(rt.export_rigaku_xtalabsynergy_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.RigakuXtalabsynergyRuntime.invoke


def _instrumented_invoke(self, capability_id, **a):
    """Every successful call is checked against its declared output schema and immediate state effects (a call does not advance time); every
    rejection code is recorded for the declaration check."""
    before = self.read('evaluator') if capability_id != 'sim.step' else None
    t0 = self.t
    try:
        out = _orig_invoke(self, capability_id, **a)
    except rt.ActionRejected as exc:
        REJECTIONS.setdefault(capability_id, set()).add(str(exc))
        raise
    meta = _meta()['capabilities'][capability_id]
    errs = asset_tool.validate_value(json.loads(json.dumps(out, default=float)), meta['output_schema'])
    if errs:
        raise AssertionError(f'{capability_id} output violates its schema: {errs[:3]}')
    if before is not None:
        assert self.t == t0, capability_id
        after = self.read('evaluator')
        undeclared = {k for k in after if _changed(before[k], after[k])} - set(rt.EFFECTS.get(capability_id, []))
        if undeclared:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=json.loads(json.dumps(a, default=float)), result_keys=sorted(out)))
    return out


rt.RigakuXtalabsynergyRuntime.invoke = _instrumented_invoke

PLANT = dict(rt.PLANT_EXAMPLE)
ASPIRIN = dict(sample_id='asp', substance_id='aspirin', size_m=[1.5e-4, 1.2e-4, 1.0e-4], mosaicity_rad=0.6 * DEG, seed=3)
NACL = dict(sample_id='salt', substance_id='nacl', size_m=[1.0e-4, 1.0e-4, 1.0e-4], mosaicity_rad=0.3 * DEG, seed=4)


def configured(seed=1, target='Cu', samples=(ASPIRIN,), **plant):
    h = rt.RigakuXtalabsynergyRuntime()
    h.invoke('plant.configure', **dict(PLANT, noise_seed=seed, target=target, **plant))
    for s in samples:
        h.invoke('sample.provide', **s)
    return h


def ready(seed=1, **kw):
    h = configured(seed, **kw)
    h.invoke('power.set', enabled=True)
    h.step(rt.BOOT_S + rt.TURBO_S + 1.0)
    return h


def wait_op(h, oid, tmax=20000.0, dt=5.0):
    t = 0.0
    while h.operations[oid]['phase'] == 'running' and t < tmax:
        h.step(dt)
        t += dt
    return h.operations[oid]


def mounted(seed=1, sample='asp', cold=True, center=True, **kw):
    h = ready(seed, **kw)
    h.invoke('door.open', door='left', angle_rad=1.6)
    h.invoke('motion_enable.hold', held=True)
    wait_op(h, h.invoke('goniometer.mount_position')['operation_id'])
    h.invoke('crystal.mount', sample_id=sample)
    if cold:
        h.invoke('cryostream.set', temperature_k=100.0, ramp_k_s=0.1)
    if center:
        wait_op(h, h.invoke('crystal.center')['operation_id'])
    h.invoke('motion_enable.hold', held=False)
    h.invoke('door.close', door='left')
    h.invoke('safety.doors_ok')
    if cold:
        h.step(3600.0)
    return h


def xray(h, kv=40e3, ma=30e-3):
    if not h.armed:
        h.invoke('safety.doors_ok')
    h.invoke('xray.set_key', angle_rad=math.pi / 2)
    h.invoke('xray.on', voltage_v=kv, current_a=ma)
    h.step(rt.SPINUP_S + 30.0)
    assert h.xr['state'] == 'on', h.xr
    return h


def rejected(h, cap, code, **a):
    before = h.read('evaluator')
    with_ = None
    try:
        h.invoke(cap, **a)
    except rt.ActionRejected as exc:
        with_ = str(exc)
    after = h.read('evaluator')
    assert with_ == code, (cap, with_, code)
    assert not any(_changed(before[k], after[k]) for k in before if k != 'truth.flux_ph_s'), cap
    return True


RUN = dict(omega_start_rad=-30 * DEG, omega_end_rad=30 * DEG, kappa_rad=0.0, phi_rad=0.0, theta_rad=0.0, distance_m=0.045, exposure_s=0.2)


def collect(h, runs=(RUN,), fw=0.5 * DEG):
    oid = h.invoke('data.collect', runs=[dict(r) for r in runs], strategy_record_id=None, frame_width_rad=fw)['operation_id']
    wait_op(h, oid)
    return h.operations[oid]['record_id']


def full_dataset(h, fw=0.5 * DEG, distance=0.045, expo=(0.2, 0.1, 0.4)):
    pre = wait_op(h, h.invoke('experiment.pre_experiment', exposure_s=expo[0], frame_width_rad=fw, distance_m=distance)['operation_id'])
    idx = h.records[pre['record_id']]['data']['auto_index']['record_id']
    lam = rt.line_data(h.plant['target'])['lam1']
    st = h.invoke('strategy.calculate', record_id=idx, resolution_m=max(0.84e-10, lam / 1.7), completeness=0.95, redundancy=2.0, exposure_low_s=expo[1],
                  exposure_high_s=expo[2], frame_width_rad=fw, distance_m=distance)
    oid = h.invoke('data.collect', runs=None, strategy_record_id=st['record_id'], frame_width_rad=fw)['operation_id']
    wait_op(h, oid)
    return h.operations[oid]['record_id'], idx, st


class ContractAndSchemas(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.rigaku_xtalabsynergy')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-161')
        self.assertEqual((rt.NX, rt.NY), (775, 803))
        self.assertAlmostEqual(rt.NX * rt.PIXEL_M, rt.ACTIVE_M[0], places=9)
        self.assertAlmostEqual(rt.NY * rt.PIXEL_M, rt.ACTIVE_M[1], places=9)

    def test_schemas_lint_and_examples_validate(self):
        meta = _meta()
        for k, c in meta['capabilities'].items():
            for sch in ('input_schema', 'output_schema'):
                self.assertEqual(asset_tool.lint_schema(c[sch]), [], (k, sch))
            if c['availability'] == 'implemented':
                self.assertEqual(asset_tool.validate_value(c['example_arguments'], c['input_schema']), [], k)
        for k, s in meta['state_slots'].items():
            self.assertEqual(asset_tool.lint_schema(s['value_schema']), [], k)

    def test_metadata_references_are_consistent(self):
        meta = _meta()
        slots = set(meta['state_slots'])
        comps = {p['id'] for p in json.loads((rt.ASSET_DIR / 'geometry_manifest.json').read_text())['components']}
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            for x in c['observes']:
                self.assertIn(x, slots, k)
            for pre in c['preconditions']:
                for x in pre['state_ids']:
                    self.assertIn(x, slots, k)
            for cid in c['component_ids']:
                self.assertIn(cid, comps, (k, cid))
        for k, s in meta['state_slots'].items():
            for cid in s['component_ids']:
                self.assertIn(cid, comps, (k, cid))
        for cid in meta['mechanisms']['xtalab_synergy_r']['component_ids']:
            self.assertIn(cid, comps)

    def test_declared_and_unsupported_capabilities(self):
        h = configured()
        for k in rt.DECLARED:
            with self.assertRaises(rt.ActionRejected) as e:
                h.invoke(k)
            self.assertEqual(str(e.exception), 'NOT_IMPLEMENTED')
        for k in rt.UNSUPPORTED:
            with self.assertRaises(rt.ActionRejected) as e:
                h.invoke(k)
            self.assertEqual(str(e.exception), 'UNSUPPORTED')

    def test_libraries_carry_evidence(self):
        lib = rt.crystal_lib()
        self.assertGreaterEqual(len(lib), 50)
        for sid, e in lib.items():
            self.assertTrue(e['cod_id'] and e['cif_sha256'] and e['evidence'] == 'documented', sid)
            m = rt.substance_model(sid)
            self.assertAlmostEqual(float(m['occ'].sum()), sum(e['cell_content'].values()), delta=1e-6 + 1e-3 * sum(e['cell_content'].values()), msg=sid)
        sc = rt.scatter_lib()
        self.assertEqual(sc['counts']['elements'], 92)
        self.assertAlmostEqual(sc['lines']['Cu']['Ka1']['wavelength_m'], 1.540593e-10, delta=2e-15)
        self.assertAlmostEqual(sc['lines']['Mo']['Ka1']['wavelength_m'], 0.709317e-10, delta=2e-15)
        self.assertAlmostEqual(rt.element('Fe')['k_edge_ev'], 7112.0, delta=5)
        prov = configured().invoke('provenance.read')['libraries']
        self.assertEqual(set(prov), {'crystal_structure_library', 'xray_scattering_library'})

    def test_structure_factors_obey_the_space_group(self):
        for sid in ('aspirin', 'calcite', 'nacl', 'sucrose'):
            e = rt.crystal_lib()[sid]
            A, u = rt.model_at(sid, 150.0)
            H = np.array([[1, 2, 3], [2, 1, 1], [3, 0, 2], [1, 1, 1], [2, 3, 4]])
            F0 = np.abs(rt.structure_factors(sid, H, A, u, 'Cu_Ka1')) ** 2
            for R, t in rt._symops(e):
                Fr = np.abs(rt.structure_factors(sid, np.round(H @ R).astype(int), A, u, 'Cu_Ka1')) ** 2
                np.testing.assert_allclose(Fr, F0, rtol=1e-9, atol=1e-9)
        A, u = rt.model_at('nacl', 295.0)
        f = np.abs(rt.structure_factors('nacl', np.array([[2, 0, 0], [1, 1, 1], [1, 0, 0]]), A, u, 'Mo_Ka1'))
        self.assertGreater(f[0], f[1])                                        # (200): Na + Cl in phase; (111): out of phase
        self.assertLess(f[2], 1e-6)                                           # (100) forbidden by F centring


class HostClockAndPurity(unittest.TestCase):
    def test_read_and_describe_do_not_advance_time(self):
        h = ready()
        s = h.register_principal('agent', 'agent') or h.session('agent')
        t = h.t
        s.read(instance_id=rt.INSTANCE_ID)
        s.describe(instance_id=rt.INSTANCE_ID)
        h.read('evaluator')
        self.assertEqual(h.t, t)

    def test_step_zero_and_negative(self):
        h = configured()
        h.step(0)
        self.assertEqual(h.t, 0.0)
        with self.assertRaises(rt.ActionRejected):
            h.step(-1.0)
        with self.assertRaises(rt.ActionRejected):
            h.step(float('nan'))

    def test_settings_only_write_targets(self):
        h = mounted(cold=False)
        h.invoke('xray.set_key', angle_rad=math.pi / 2)
        out = h.invoke('xray.on', voltage_v=40e3, current_a=30e-3)
        self.assertEqual(out['state'], 'starting')
        self.assertEqual(h.read()['xray.voltage_v'], 0.0)
        h.step(rt.SPINUP_S + 1.0)
        self.assertEqual(h.read()['xray.state'], 'ramping')
        h.step(20.0)
        self.assertEqual((h.read()['xray.state'], h.read()['xray.voltage_v']), ('on', 40000.0))
        h.invoke('cryostream.set', temperature_k=150.0, ramp_k_s=0.1)
        self.assertAlmostEqual(h.read()['cryostream.gas_temperature_k'], PLANT['room_k'], delta=0.06)
        h.step(600.0)
        self.assertAlmostEqual(h.read()['cryostream.gas_temperature_k'], PLANT['room_k'] - 60.0, delta=0.1)
        self.assertEqual(h.read()['cryostream.state'], 'ramping')

    def test_host_step_chopping_does_not_change_the_result(self):
        outs = []
        for chop in (None, 7.0):
            h = xray(mounted(seed=2))
            oid = h.invoke('data.collect', runs=[dict(RUN, omega_end_rad=0.0)], strategy_record_id=None, frame_width_rad=0.5 * DEG)['operation_id']
            if chop is None:
                h.step(200.0)
            else:
                for _ in range(int(200 / chop)):
                    h.step(chop)
                h.step(200.0 - chop * int(200 / chop))
            rec = h.records[h.operations[oid]['record_id']]
            o = rec['runs'][0]['obs']
            outs.append((round(h.t, 9), o['n'], float(o['i_obs'].sum()), h.read('evaluator')['truth.crystal']['dose_gy']))
        self.assertEqual(outs[0][:3], outs[1][:3])
        self.assertAlmostEqual(outs[0][3], outs[1][3], delta=1e-6 * max(1.0, outs[0][3]))

    def test_reads_do_not_change_later_results(self):
        res = []
        for reads in (False, True):
            h = xray(mounted(seed=3))
            oid = h.invoke('data.collect', runs=[dict(RUN, omega_end_rad=-20 * DEG)], strategy_record_id=None, frame_width_rad=0.5 * DEG)['operation_id']
            for _ in range(10):
                h.step(3.0)
                if reads:
                    h.read('evaluator'); h.invoke('camera.view'); h.invoke('ledger.read')
            h.step(100.0)
            rec = h.records[h.operations[oid]['record_id']]
            res.append(float(rec['runs'][0]['obs']['i_obs'].sum()))
        self.assertEqual(res[0], res[1])


class SafetyAndGenerator(unittest.TestCase):
    def test_boot_vacuum_and_power_loss(self):
        h = configured()
        h.invoke('power.press_button')
        self.assertEqual(h.read()['system.state'], 'booting')
        h.step(rt.BOOT_S + 0.5)
        self.assertEqual(h.read()['system.state'], 'ready')
        self.assertFalse(h.read()['xray.vacuum_ready'])
        h.step(rt.TURBO_S)
        self.assertTrue(h.read()['xray.vacuum_ready'])
        self.assertIsNotNone(h.read()['xray.voltage_v'])
        h.invoke('power.press_button')
        r = h.read()
        self.assertEqual((r['power.enabled'], r['system.state'], r['xray.voltage_v']), (False, 'off', None))

    def test_doors_ok_and_xray_interlocks(self):
        h = ready()
        h.invoke('door.open', door='right', angle_rad=1.0)
        rejected(h, 'safety.doors_ok', 'DOORS_OPEN')
        h.invoke('door.close', door='right')
        h.invoke('safety.doors_ok')
        self.assertTrue(h.read()['safety.armed'])
        rejected(h, 'xray.on', 'KEY_OFF', voltage_v=40e3, current_a=30e-3)
        h.invoke('xray.set_key', angle_rad=math.pi / 2)
        rejected(h, 'xray.on', 'POWER_LIMIT', voltage_v=60e3, current_a=30e-3)
        rejected(h, 'xray.on', 'SETTING_STEP', voltage_v=40.5e3, current_a=30e-3)
        h.invoke('door.open', door='left', angle_rad=0.5)
        self.assertFalse(h.read()['safety.armed'])
        rejected(h, 'xray.on', 'SAFETY_NOT_ARMED', voltage_v=40e3, current_a=30e-3)
        h.invoke('door.close', door='left')
        h.invoke('safety.doors_ok')
        h.invoke('xray.on', voltage_v=50e3, current_a=24e-3)
        h.step(rt.SPINUP_S + 30.0)
        self.assertEqual(h.read()['xray.state'], 'on')
        self.assertAlmostEqual(h.read()['xray.voltage_v'], 50e3)
        h.invoke('xray.standby')
        h.step(30.0)
        self.assertEqual((h.read()['xray.state'], h.read()['xray.voltage_v']), ('standby', 20e3))

    def test_trips_key_emergency_stop_chiller_vacuum_filament(self):
        h = xray(ready())
        h.invoke('xray.set_key', angle_rad=0.0)
        self.assertEqual(h.read()['xray.state'], 'off')
        h = xray(ready(seed=4))
        h.invoke('safety.emergency_stop')
        r = h.read()
        self.assertEqual((r['xray.state'], r['safety.estop_latched'], r['safety.armed']), ('off', True, False))
        rejected(h, 'safety.doors_ok', 'EMERGENCY_STOP')
        h.invoke('safety.reset_emergency_stop')
        h.invoke('safety.doors_ok')
        h = xray(ready(seed=5))
        h.invoke('fault.inject', fault='chiller_failure', active=True)
        self.assertEqual(h.read()['xray.state'], 'off')
        rejected(h, 'xray.on', 'COOLING_WATER', voltage_v=40e3, current_a=30e-3)
        h = xray(ready(seed=6))
        h.invoke('fault.inject', fault='vacuum_loss', active=True)
        self.assertEqual(h.read()['xray.state'], 'off')
        h.invoke('fault.inject', fault='vacuum_loss', active=False)
        h.step(rt.PUMPDOWN_S - 5.0)
        self.assertFalse(h.read()['xray.vacuum_ready'])
        h.step(10.0)
        self.assertTrue(h.read()['xray.vacuum_ready'])
        h = xray(ready(seed=7))
        h.invoke('fault.inject', fault='filament_failure', active=True)
        rejected(h, 'xray.on', 'FILAMENT_FAILURE', voltage_v=40e3, current_a=30e-3)
        rejected(h, 'maintenance.replace_filament', 'POWER_ON')
        h.invoke('power.set', enabled=False)
        h.invoke('maintenance.replace_filament')
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + rt.TURBO_S + 5.0)
        self.assertFalse(h.read()['xray.vacuum_ready'])                         # vented by the filament change
        h.step(rt.PUMPDOWN_S)
        self.assertTrue(h.read()['xray.vacuum_ready'])

    def test_filament_life_ends_the_emission(self):
        h = xray(ready(seed=8))
        life = h.filament_life_s
        self.assertTrue(0.9 * rt.FILAMENT_INTERVAL_S <= life <= 1.1 * rt.FILAMENT_INTERVAL_S)
        while h.t < life + 100.0:
            h.step(min(rt.MAX_STEP_S, life + 100.0 - h.t))
        self.assertEqual(h.read()['xray.state'], 'off')
        self.assertIn('filament_failure', h.read('evaluator')['truth.faults'])

    def test_shutter_divergence_threshold(self):
        h = ready()
        rejected(h, 'shutter.open', 'XRAY_NOT_STABLE')
        h.invoke('safety.doors_ok')
        xray(h)
        h.invoke('shutter.open')
        self.assertGreater(h.read('evaluator')['truth.flux_ph_s'], 0)
        f10 = h.read('evaluator')['truth.flux_ph_s']
        h.invoke('optics.set_divergence', divergence_rad=1e-3)
        self.assertAlmostEqual(h.read('evaluator')['truth.flux_ph_s'] / f10, 0.01, delta=1e-9)
        h.invoke('door.open', door='left', angle_rad=0.4)
        self.assertFalse(h.read()['shutter.open'])
        rejected(h, 'detector.set_threshold', 'THRESHOLD_OUT_OF_RANGE', threshold_ev=7900.0)
        h.invoke('detector.set_threshold', threshold_ev=7000.0)
        self.assertAlmostEqual(h.read()['detector.threshold_ev'], 7000.0)

    def test_flux_follows_the_generator_setting(self):
        f1 = rt.source_flux('Cu', 40e3, 30e-3, 10e-3)
        self.assertAlmostEqual(f1, rt.FLUX_REF['Cu']['flux'], delta=1e-6 * f1)
        self.assertGreater(rt.source_flux('Cu', 50e3, 24e-3, 10e-3), f1)
        self.assertEqual(rt.source_flux('Mo', 19e3, 30e-3, 10e-3), 0.0)                # below the Mo K edge no K-alpha
        self.assertAlmostEqual(rt.source_flux('Cu', 40e3, 15e-3, 10e-3) / f1, 0.5, delta=1e-9)


    def test_ramps_keep_the_load_within_1_2_kw(self):
        for (kv0, ma0), (kv1, ma1) in (((60e3, 20e-3), (40e3, 30e-3)), ((40e3, 30e-3), (60e3, 20e-3)), ((40e3, 30e-3), (35e3, 20e-3)), ((50e3, 24e-3), (46e3, 26e-3))):
            h = xray(ready(seed=2), kv0, ma0)
            e0, t0 = h.ledger()['electrical_energy_j'], h.t
            h.invoke('xray.set', voltage_v=kv1, current_a=ma1)
            plan = dict(h.xr['ramp'])
            worst = max(np.prod(rt.ramp_values(plan, t0 + plan['dur'] * i / 2000)) for i in range(2001))
            self.assertLessEqual(worst, rt.POWER_MAX_W + 1e-6, (kv0, ma0, kv1, ma1))
            span = plan['dur'] + 3.0
            for _ in range(int(span / 0.5)):
                h.step(0.5)
                self.assertLessEqual(np.prod(h._xray_kv_ma()), rt.POWER_MAX_W + 1e-6)
            self.assertEqual(h.xr['state'], 'on')
            d1, d2 = plan['d1'], plan['dur'] - plan['d1']                      # the load is linear in each phase: midpoint integrals are exact
            ramp = d1 * np.prod(rt.ramp_values(plan, t0 + d1 / 2)) + d2 * np.prod(rt.ramp_values(plan, t0 + d1 + d2 / 2))
            expected = rt.ELECTRONICS_W * (h.t - t0) + (ramp + kv1 * ma1 * (h.t - t0 - plan['dur'])) / rt.GENERATOR_EFFICIENCY
            self.assertAlmostEqual(h.ledger()['electrical_energy_j'] - e0, expected, delta=1e-6 * expected)

    def test_a_key_at_its_stop_is_accepted_as_float32(self):
        h = ready()
        h.register_principal('a1', 'agent')
        s = h.session('a1')
        r = s.invoke(instance_id=rt.INSTANCE_ID, capability_id='xray.set_key', arguments={'angle_rad': float(np.float32(math.pi / 2))}, request_id='k1')
        self.assertEqual(r['status'], 'succeeded', r['error'])
        self.assertEqual(h.read('evaluator')['xray.key_angle_rad'], math.pi / 2)
        r = s.invoke(instance_id=rt.INSTANCE_ID, capability_id='xray.set_key', arguments={'angle_rad': math.pi / 2 + 1e-5}, request_id='k2')
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')


class GoniometerAndCollisions(unittest.TestCase):
    def test_move_timing_follows_the_axis_speeds(self):
        h = ready()
        h.invoke('safety.doors_ok')
        out = h.invoke('goniometer.move', omega_rad=20 * DEG, kappa_rad=0.0, phi_rad=90 * DEG, theta_rad=30 * DEG, distance_m=0.070)
        self.assertAlmostEqual(out['duration_s'], max(2.0, 4.5, 3.0, 3.8), delta=1e-6)
        h.step(2.0)
        self.assertAlmostEqual(h.read()['goniometer.omega_rad'], 20 * DEG, delta=1e-9)
        self.assertAlmostEqual(h.read()['goniometer.phi_rad'], 40 * DEG, delta=1e-9)
        h.step(3.0)
        r = h.read()
        self.assertFalse(r['goniometer.moving'])
        self.assertAlmostEqual(r['detector.distance_m'], 0.070, delta=1e-12)

    def test_collision_model_refuses_and_routes(self):
        hit, names = rt.collisions([0.0], [0.0], [0.0], [rt.DIST_MIN_M])
        self.assertFalse(hit[0], names)
        hit, names = rt.collisions([180 * DEG], [0.0], [0.0], [rt.DIST_MIN_M])
        self.assertTrue(hit[0])
        self.assertTrue(any('detector' in n or 'arm' in n or 'carriage' in n for n in names[0]))
        hit, names = rt.collisions([0.0], [120 * DEG], [0.0], [0.05])
        self.assertTrue(any('collimator' in n for n in names[0]))
        h = ready()
        h.invoke('safety.doors_ok')
        rejected(h, 'goniometer.move', 'COLLISION_PREDICTED', omega_rad=180 * DEG, kappa_rad=0.0, phi_rad=0.0, theta_rad=0.0, distance_m=0.032)
        self.assertNotIn('collisions_refused', h.ledger())                       # a refusal writes nothing
        legs = rt.plan_route(dict(omega=0.0, kappa=60 * DEG, phi=0.0, theta=0.0, distance=0.1), dict(omega=40 * DEG, kappa=-60 * DEG, phi=0.0, theta=-60 * DEG, distance=0.1))
        if legs is not None:
            for a, b in legs:
                self.assertIsNone(rt.path_collision(a, b))

    def test_motion_with_open_door_needs_motion_enable(self):
        h = ready()
        h.invoke('door.open', door='left', angle_rad=1.0)
        rejected(h, 'goniometer.move', 'MOTION_ENABLE_REQUIRED', omega_rad=10 * DEG, kappa_rad=0.0, phi_rad=0.0, theta_rad=0.0, distance_m=0.1)
        h.invoke('motion_enable.hold', held=True)
        oid = h.invoke('goniometer.move', omega_rad=30 * DEG, kappa_rad=0.0, phi_rad=0.0, theta_rad=0.0, distance_m=0.1)['operation_id']
        h.step(1.0)
        h.invoke('motion_enable.hold', held=False)
        self.assertEqual(h.operations[oid]['phase'], 'failed')
        self.assertEqual(h.operations[oid]['reason'], 'MOTION_ENABLE_RELEASED')
        om = h.read()['goniometer.omega_rad']
        self.assertTrue(0 < om < 30 * DEG)
        h.step(10.0)
        self.assertEqual(h.read()['goniometer.omega_rad'], om)

    def test_stop_keeps_the_pose(self):
        h = ready()
        h.invoke('safety.doors_ok')
        h.invoke('goniometer.move', omega_rad=40 * DEG, kappa_rad=0.0, phi_rad=0.0, theta_rad=0.0, distance_m=0.032)
        h.step(2.0)
        h.invoke('goniometer.stop')
        self.assertAlmostEqual(h.read()['goniometer.omega_rad'], 20 * DEG, delta=1e-9)
        rejected(h, 'goniometer.stop', 'NOT_MOVING')


    def test_enclosure_walls_limit_the_detector_arm(self):
        hit, names = rt.collisions([0.0], [0.0], [-90 * DEG], [rt.DIST_MAX_M])
        self.assertTrue(hit[0] and any('enclosure_front' in n for n in names[0]), names[0])
        hit, names = rt.collisions([0.0], [0.0], [90 * DEG], [rt.DIST_MAX_M])
        self.assertTrue(hit[0] and any('enclosure_rear' in n for n in names[0]), names[0])
        hit, names = rt.collisions([0.0, 0.0], [0.0, 0.0], [0.0, -60 * DEG], [rt.DIST_MAX_M, 0.060])          # reachable poses stay free
        self.assertFalse(hit.any(), names)
        h = ready()
        h.invoke('safety.doors_ok')
        rejected(h, 'goniometer.move', 'COLLISION_PREDICTED', omega_rad=0.0, kappa_rad=0.0, phi_rad=0.0, theta_rad=-90 * DEG, distance_m=0.15)

    def test_refused_moves_write_nothing(self):
        h = ready()
        h.invoke('safety.doors_ok')
        led, ev = h.ledger(), len(h.events)
        rejected(h, 'goniometer.move', 'COLLISION_PREDICTED', omega_rad=180 * DEG, kappa_rad=0.0, phi_rad=0.0, theta_rad=0.0, distance_m=0.032)
        self.assertEqual(h.ledger(), led)
        self.assertEqual(len(h.events), ev)


class CrystalHandling(unittest.TestCase):
    def test_mount_guards_camera_and_centring(self):
        h = ready()
        rejected(h, 'crystal.mount', 'DOOR_CLOSED', sample_id='asp')
        h.invoke('door.open', door='left', angle_rad=1.6)
        rejected(h, 'crystal.mount', 'NOT_AT_MOUNT_POSITION', sample_id='asp')
        h.invoke('motion_enable.hold', held=True)
        wait_op(h, h.invoke('goniometer.mount_position')['operation_id'])
        h.invoke('crystal.mount', sample_id='asp')
        rejected(h, 'crystal.mount', 'SAMPLE_NOT_ON_BENCH', sample_id='asp')
        off0 = np.linalg.norm(h.read('evaluator')['truth.crystal']['offset_m'])
        self.assertTrue(0.1e-3 <= off0 <= rt.MOUNT_OFFSET_MAX_M)
        v = h.invoke('camera.view')
        self.assertTrue(v['crystal_present'])
        self.assertAlmostEqual(np.hypot(*v['offset_m']), 0, delta=off0 + 1e-5)
        for x in v['offset_m'] + v['size_m']:
            self.assertAlmostEqual(x / rt.CAMERA_QUANT_M, round(x / rt.CAMERA_QUANT_M), delta=1e-6)
        wait_op(h, h.invoke('crystal.center')['operation_id'])
        off1 = np.linalg.norm(h.read('evaluator')['truth.crystal']['offset_m'])
        self.assertLess(off1, 5 * rt.CENTER_RESIDUAL_M)
        self.assertAlmostEqual(h.read()['goniometer.phi_rad'], 0.0, delta=1e-9)
        v2 = h.invoke('camera.view')
        self.assertLess(np.hypot(*v2['offset_m']), 3e-5)
        h.invoke('crystal.unmount')
        self.assertIn('asp', h.read()['sample.bench_ids'])

    def test_cryostream_cools_the_crystal_and_icing_with_the_door_open(self):
        h = mounted(cold=True)
        self.assertAlmostEqual(h.read('evaluator')['truth.crystal']['temperature_k'], 100.0 + rt.CRYO_OFFSET_K, delta=0.05)
        self.assertEqual(h.read()['cryostream.state'], 'stable')
        h.invoke('door.open', door='left', angle_rad=1.6)
        h.step(600.0)
        ice = h.read('evaluator')['truth.crystal']['ice_m3']
        self.assertAlmostEqual(ice, rt.ICE_RATE_M3_S * 600.0 * PLANT['humidity'] / 0.4, delta=1e-18)
        h.invoke('cryostream.stop')
        h.step(600.0)
        c = h.read('evaluator')['truth.crystal']
        self.assertAlmostEqual(c['temperature_k'], PLANT['room_k'], delta=0.1)
        self.assertEqual(c['ice_m3'], 0.0)

    def test_nitrogen_supply(self):
        h = configured(nitrogen_available=False)
        rejected(h, 'cryostream.set', 'NITROGEN_UNAVAILABLE', temperature_k=100.0, ramp_k_s=0.1)
        h = configured(seed=2)
        h.invoke('cryostream.set', temperature_k=100.0, ramp_k_s=0.1)
        h.invoke('fault.inject', fault='nitrogen_empty', active=True)
        self.assertEqual(h.read()['cryostream.state'], 'off')


class DiffractionPhysics(unittest.TestCase):
    def test_bragg_positions_lorentz_and_polarisation(self):
        rng = np.random.default_rng(1)
        cr = dict(sid='aspirin', U=rt.random_rotation(rng), U2=None, frac2=0.0, offset=np.zeros(3), size=np.array([1e-4] * 3), mosaic=0.5 * DEG, defect='none',
                  inverted=False, T=100.0, dose_gy=0.0, ice_m3=0.0)
        src = dict(target='Cu', flux=rt.source_flux('Cu', 40e3, 30e-3, 10e-3), divergence=10e-3, threshold_frac=0.5)
        run = dict(omega_start=-30 * DEG, omega_end=30 * DEG, kappa=0.0, phi=0.0, theta=0.0, distance=0.05, frame_width_rad=0.5 * DEG, exposure_s=0.5)
        o = simulate(cr, src, run)
        H, F2, A, _ = rt.reflection_set('aspirin', 100, 'Cu', False)
        d_hkl = 1 / np.linalg.norm(o['h'] @ rt.reciprocal(A).T, axis=1)
        lam = rt.line_data('Cu')['lam1']
        np.testing.assert_allclose(lam / (2 * np.sin(o['tth'] / 2)), d_hkl, rtol=1e-9)             # Bragg's law for every spot
        s1, t = rt.pixel_rays(0.0, 0.05, o['x'], o['y'])
        np.testing.assert_allclose(o['L'], 1 / np.abs(s1[:, 1]), rtol=1e-6)                       # L = 1 / |e . (s0 x s1)|
        np.testing.assert_allclose(o['P'], (1 + s1[:, 0] ** 2) / 2, rtol=1e-9)
        self.assertTrue(np.all(o['x'] >= 0) and np.all(o['x'] < rt.NX))

    def test_cu_versus_mo_and_distance(self):
        rng = np.random.default_rng(2)
        cr = base_crystal('aspirin', rng)
        runs = dict(omega_start=-30 * DEG, omega_end=30 * DEG, kappa=0.0, phi=0.0, theta=0.0, distance=0.045, frame_width_rad=0.5 * DEG, exposure_s=0.5)
        cu = simulate(cr, dict(target='Cu', flux=1e9, divergence=10e-3, threshold_frac=0.5), runs)
        mo = simulate(cr, dict(target='Mo', flux=1e9, divergence=10e-3, threshold_frac=0.5), runs)
        self.assertLess(mo['d'].min(), cu['d'].min())                                    # shorter wavelength reaches higher resolution
        self.assertGreater(cu['S'].sum() / len(cu['S']), mo['S'].sum() / len(mo['S']))    # lambda^3: stronger per reflection with Cu
        far = simulate(cr, dict(target='Cu', flux=1e9, divergence=10e-3, threshold_frac=0.5), dict(runs, distance=0.120))
        self.assertLess(far['d'].min(), 1e-9)
        self.assertGreater(far['d'].min(), cu['d'].min())                                   # the detector covers a smaller 2theta range

    def test_divergence_changes_spot_size_and_flux(self):
        rng = np.random.default_rng(3)
        cr = base_crystal('aspirin', rng)
        r = dict(omega_start=-20 * DEG, omega_end=20 * DEG, kappa=0.0, phi=0.0, theta=0.0, distance=0.1, frame_width_rad=0.5 * DEG, exposure_s=0.5)
        a = simulate(cr, dict(target='Cu', flux=rt.source_flux('Cu', 40e3, 30e-3, 10e-3), divergence=10e-3, threshold_frac=0.5), r)
        b = simulate(cr, dict(target='Cu', flux=rt.source_flux('Cu', 40e3, 30e-3, 1e-3), divergence=1e-3, threshold_frac=0.5), r)
        self.assertLess(np.median(b['sig_px']), 0.8 * np.median(a['sig_px']))
        self.assertLess(np.median(b['sig_om']), np.median(a['sig_om']))

    def test_fluorescence_and_the_energy_threshold(self):
        rng = np.random.default_rng(4)
        fe = base_crystal('fe_acac3', rng)
        r = dict(omega_start=-5 * DEG, omega_end=5 * DEG, kappa=0.0, phi=0.0, theta=0.0, distance=0.05, frame_width_rad=0.5 * DEG, exposure_s=1.0)
        src = dict(target='Cu', flux=2.5e9, divergence=10e-3, threshold_frac=0.5)
        bg_lo = rt.background_terms(fe, src, r, 0.4)
        bg_hi = rt.background_terms(fe, dict(src, threshold_frac=7000.0 / rt.line_data('Cu')['e1']), r, 0.4)
        self.assertTrue(bg_lo['fluo'] and bg_lo['fluo'][0][0] == rt.element('Fe')['ka1_ev'])
        x, y = np.array([150.0, 600.0]), np.array([150.0, 650.0])
        lo = rt.background_rate(bg_lo, 0.0, 0.05, x, y)
        hi = rt.background_rate(bg_hi, 0.0, 0.05, x, y)
        self.assertTrue(np.all(hi < 0.35 * lo))                                          # Fe K-alpha (6.4 keV) rejected above 7 keV
        asp = rt.background_terms(base_crystal('aspirin', rng), src, r, 0.4)
        self.assertEqual(asp['fluo'], [])
        mo = rt.background_terms(fe, dict(src, target='Mo'), r, 0.4)
        self.assertTrue(mo['fluo'])                                                        # Fe fluoresces under Mo as well

    def test_temperature_factors_expansion_and_decay(self):
        H, F2c, Ac, _ = rt.reflection_set('aspirin', 100, 'Cu', False)
        H2, F2w, Aw, _ = rt.reflection_set('aspirin', 295, 'Cu', False)
        self.assertGreater(abs(np.linalg.det(Aw)), abs(np.linalg.det(Ac)))
        dmap = {tuple(h): f for h, f in zip(H2, F2w)}
        d = 1 / np.linalg.norm(H @ rt.reciprocal(Ac).T, axis=1)
        hi = [i for i in np.nonzero(d < 1.0e-10)[0] if tuple(H[i]) in dmap]
        self.assertGreater(sum(F2c[i] for i in hi), 1.3 * sum(dmap[tuple(H[i])] for i in hi))    # high-angle intensity stronger when cold
        self.assertAlmostEqual(rt.decay_half_dose(100.0), rt.DECAY_HALF_DOSE_GY['cold'][1], delta=1.0)
        self.assertLess(rt.decay_half_dose(295.0), rt.decay_half_dose(100.0))

    def test_absorption_depends_on_mu_and_path(self):
        rng = np.random.default_rng(5)
        light = base_crystal('aspirin', rng)
        heavy = base_crystal('k2ptcl4', rng)
        mu_l = rt.mu_linear('aspirin', 'Cu_Ka1', rt.substance_model('aspirin')['A'])[0]
        mu_h = rt.mu_linear('k2ptcl4', 'Cu_Ka1', rt.substance_model('k2ptcl4')['A'])[0]
        d0 = np.array([[1.0, 0.0, 0.0]])
        tl = rt.crystal_absorption(light, mu_l, d0, d0)[0]
        th = rt.crystal_absorption(heavy, mu_h, d0, d0)[0]
        self.assertTrue(0.8 < tl < 1.0 and th < 0.2 and th < tl)

    def test_count_rate_losses_and_overloads(self):
        rng = np.random.default_rng(6)
        cr = base_crystal('nacl', rng, T=295.0, size=3e-4)
        run = dict(omega_start=-30 * DEG, omega_end=30 * DEG, kappa=0.0, phi=0.0, theta=0.0, distance=0.032, frame_width_rad=0.2 * DEG, exposure_s=0.1)
        home = simulate(cr, dict(target='Mo', flux=rt.source_flux('Mo', 50e3, 24e-3, 3e-3), divergence=3e-3, threshold_frac=0.5), run)
        self.assertFalse(home['overload'].any())                                   # a 1.2 kW source stays inside the counting range
        self.assertTrue(np.all(home['S_meas'] <= home['S_meas_ideal'] + 1e-9))
        hot = simulate(cr, dict(target='Mo', flux=100 * rt.source_flux('Mo', 50e3, 24e-3, 3e-3), divergence=3e-3, threshold_frac=0.5), run)
        self.assertTrue(hot['overload'].any())                                      # a 100x brighter beam saturates the strongest pixels
        ratio = hot['S_meas'] / np.maximum(hot['S_meas_ideal'], 1e-30)
        self.assertLess(float(ratio[hot['overload']].min()), 0.92)                  # paralysable dead time: measured = true exp(-n tau / 2)

    def test_frames_and_the_beam_stop_shadow(self):
        h = xray(mounted(seed=9, sample='salt', samples=(ASPIRIN, NACL)))
        rid = collect(h, runs=[dict(RUN, exposure_s=0.5, distance_m=0.032)], fw=0.5 * DEG)
        f1 = h.invoke('frame.read', record_id=rid, run_index=0, frame_index=10, binning=4, region_px=None)
        f2 = h.invoke('frame.read', record_id=rid, run_index=0, frame_index=10, binning=4, region_px=None)
        self.assertEqual(f1['counts'], f2['counts'])                                        # deterministic
        c = np.array(f1['counts'])
        self.assertEqual(c.shape, (201, 194))
        self.assertTrue(np.all(c >= 0) and c.sum() > 0)
        cx, cy = rt.NX // 2, rt.NY // 2                                                     # beam-stop shadow at the direct-beam position
        reg = h.invoke('frame.read', record_id=rid, run_index=0, frame_index=10, binning=1, region_px=[cx - 5, cy - 5, cx + 5, cy + 5])
        self.assertEqual(int(np.array(reg['counts']).sum()), 0)
        rejected(h, 'frame.read', 'SIMULATION_LIMIT', record_id=rid, run_index=0, frame_index=10, binning=2, region_px=None)
        rejected(h, 'frame.read', 'UNKNOWN_FRAME', record_id=rid, run_index=0, frame_index=999, binning=4, region_px=None)


def base_crystal(sid, rng, T=100.0, size=1e-4):
    return dict(sid=sid, U=rt.random_rotation(rng), U2=None, frac2=0.0, offset=np.zeros(3), size=np.array([size] * 3), mosaic=0.5 * DEG, defect='none',
                inverted=False, T=T, dose_gy=0.0, ice_m3=0.0)


def simulate(cr, src, run, frames=None):
    n = frames or int(round(abs(run['omega_end'] - run['omega_start']) / run['frame_width_rad']))
    return rt.simulate_run(cr, src, run, n, (1, 2, 3), 0.4)['obs']


def pipeline(sid, target, seed, inverted=False, size=1.2e-4, mosaic=0.5, dims=None, sample_seed=None, expo=(0.2, 0.1, 0.4)):
    """Mounted crystal, generator on, pre-experiment -> strategy -> collection -> reduction -> What is this? (the review probes)."""
    s = dict(sample_id='x', substance_id=sid, size_m=list(dims or [size, size * 0.9, size * 0.8]), mosaicity_rad=mosaic * DEG,
             seed=seed if sample_seed is None else sample_seed, inverted=inverted)
    kv, ma = (40e3, 30e-3) if target == 'Cu' else (50e3, 24e-3)
    h = xray(mounted(seed=seed, sample='x', samples=(s,), target=target), kv, ma)
    ds, idx, st = full_dataset(h, expo=expo)
    red = h.invoke('processing.reduce', record_id=ds, index_record_id=None)
    return h, st, red, h.invoke('processing.what_is_this', record_id=red['record_id'])


class Processing(unittest.TestCase):
    def test_pre_experiment_strategy_collection_reduction_and_what_is_this(self):
        h = xray(mounted(seed=11))
        ds, idx, st = full_dataset(h)
        ir = h.records[idx]['data']['result']
        self.assertTrue(ir['indexed'])
        self.assertGreater(ir['indexation_ratio'], 0.95)
        self.assertGreater(st['predicted_completeness'], 0.9)
        red = h.invoke('processing.reduce', record_id=ds, index_record_id=None)
        rr = h.records[red['record_id']]['data']['result']
        self.assertEqual(rr['laue_class'], '2/m')
        self.assertEqual(rr['space_group_number'], 14)
        lib = rt.crystal_lib()['aspirin']['cell_angstrom_deg']
        got = sorted([rr['cell']['a_m'] * 1e10, rr['cell']['b_m'] * 1e10, rr['cell']['c_m'] * 1e10])
        np.testing.assert_allclose(got, sorted(lib[:3]), rtol=0.005)
        self.assertLess(rr['r_int'], 0.10)
        w = h.invoke('processing.what_is_this', record_id=red['record_id'])
        self.assertTrue(w['matched'])
        self.assertEqual(w['name'], 'aspirin')
        self.assertLess(w['r1'], 0.08)
        res = h.invoke('measurement.result', record_id=red['record_id'], max_values=500)
        self.assertLessEqual(len(res['data']['reflections']['hkl']), 100)

    def test_cubic_and_trigonal_lattices(self):
        for sample, target, number, laue in ((NACL, 'Mo', 225, 'm-3m'), (dict(ASPIRIN, sample_id='cal', substance_id='calcite'), 'Mo', 167, '-3m')):
            h = xray(mounted(seed=12, sample=sample['sample_id'], samples=(sample,), target=target), 50e3, 24e-3)
            ds, idx, st = full_dataset(h)
            red = h.invoke('processing.reduce', record_id=ds, index_record_id=None)
            rr = h.records[red['record_id']]['data']['result']
            self.assertEqual((rr['laue_class'], rr['space_group_number']), (laue, number), sample['substance_id'])
            self.assertTrue(h.invoke('processing.what_is_this', record_id=red['record_id'])['matched'])

    def test_indexing_failures(self):
        for defect, reason in (('polycrystalline', 'NO_LATTICE'), ('amorphous', 'NO_PEAKS')):
            s = dict(ASPIRIN, sample_id='bad', defect=defect)
            h = xray(mounted(seed=13, sample='bad', samples=(s,)))
            pre = wait_op(h, h.invoke('experiment.pre_experiment', exposure_s=0.2, frame_width_rad=0.5 * DEG, distance_m=0.045)['operation_id'])
            ai = h.records[pre['record_id']]['data']['auto_index']
            self.assertFalse(ai['indexed'], defect)
            self.assertIn(ai['reason'], (reason, 'TOO_FEW_PEAKS', 'NO_LATTICE'), defect)
        s = dict(ASPIRIN, sample_id='new', listed_in_database=False)
        h = xray(mounted(seed=14, sample='new', samples=(s,)))
        pre = wait_op(h, h.invoke('experiment.pre_experiment', exposure_s=0.2, frame_width_rad=0.5 * DEG, distance_m=0.045)['operation_id'])
        idx = h.records[pre['record_id']]['data']['auto_index']['record_id']
        self.assertTrue(h.records[idx]['data']['result']['indexed'])
        self.assertFalse(h.invoke('processing.what_is_this', record_id=idx)['matched'])

    def test_twinned_crystal_is_flagged_by_the_refinement(self):
        s = dict(ASPIRIN, sample_id='tw', defect='twin', twin_fraction=0.45)
        h = xray(mounted(seed=15, sample='tw', samples=(s,)))
        pre = wait_op(h, h.invoke('experiment.pre_experiment', exposure_s=0.2, frame_width_rad=0.5 * DEG, distance_m=0.045)['operation_id'])
        ai = h.records[pre['record_id']]['data']['auto_index']
        if not ai['indexed']:                                                              # the indexing already fails on the two lattices
            self.assertIn(ai['reason'], ('LOW_INDEXATION', 'NO_LATTICE'))
            return
        ds, idx, st = full_dataset(h)
        try:
            red = h.invoke('processing.reduce', record_id=ds, index_record_id=None)
        except rt.ActionRejected:
            return
        w = h.invoke('processing.what_is_this', record_id=red['record_id'])
        rr = h.records[red['record_id']]['data']['result']
        self.assertTrue((not w['matched']) or (w['r1'] is None) or w['r1'] > 0.08 or rr['r_int'] > 0.10, (w, rr['r_int']))

    def test_security_mode_and_cancellation_keep_frames(self):
        h = xray(mounted(seed=16))
        oid = h.invoke('data.collect', runs=[RUN], strategy_record_id=None, frame_width_rad=0.5 * DEG)['operation_id']
        h.step(10.0)
        h.invoke('door.open', door='left', angle_rad=0.3)
        op = h.operations[oid]
        self.assertEqual((op['phase'], op['reason']), ('failed', 'SECURITY_MODE'))
        rec = h.records[op['record_id']]
        self.assertEqual(rec['status'], 'failed')
        self.assertTrue(0 < rec['meta']['frames_recorded'] < 120)
        self.assertFalse(h.read()['shutter.open'])
        h.invoke('door.close', door='left')
        h.invoke('safety.doors_ok')
        oid = h.invoke('data.collect', runs=[RUN], strategy_record_id=None, frame_width_rad=0.5 * DEG)['operation_id']
        h.step(8.0)
        h._cancel_op(h.operations[oid])
        rec = h.records[h.operations[oid]['record_id']]
        self.assertEqual(rec['status'], 'cancelled')
        self.assertGreater(rec['meta']['frames_recorded'], 0)


    def test_what_is_this_refines_every_indexing_of_the_lattice(self):
        for sid in ('pentaerythritol', 'naclo3'):                         # 4/m in a 4/mmm lattice, m-3 in m-3m: two indexings fit the metric
            h, st, red, w = pipeline(sid, 'Mo', 21)
            ref = h.records[w['record_id']]['data']['result']['refinement']
            self.assertTrue(w['matched'], sid)
            self.assertLess(w['r1'], 0.08, sid)
            self.assertEqual(ref['settings_tested'], 2, sid)

    def test_space_group_inside_the_laue_class_and_ambiguous_ties(self):
        h, st, red, w = pipeline('cscl', 'Mo', 21)
        rr = h.records[red['record_id']]['data']['result']
        self.assertEqual((rr['laue_class'], rr['space_group_number']), ('m-3m', 221))
        self.assertFalse(red['space_group_ambiguous'])
        h, st, red, w = pipeline('zns', 'Mo', 21)
        rr = h.records[red['record_id']]['data']['result']
        self.assertTrue(red['space_group_ambiguous'])
        self.assertEqual(sorted([rr['space_group']] + rr['space_group_alternatives']), ['F -4 3 m', 'F m -3 m'])

    def test_runs_of_different_exposure_are_normalised(self):
        for sid, laue, number in (('quartz', '-3m', 154), ('epsomite', 'mmm', 19)):
            h, st, red, w = pipeline(sid, 'Cu', 21)                       # strategy exposures 0.1 s and 0.4 s
            rr = h.records[red['record_id']]['data']['result']
            self.assertEqual((rr['laue_class'], rr['space_group_number']), (laue, number), sid)
            self.assertLess(w['r1'], 0.05, sid)

    def test_strategy_prediction_matches_the_reduction(self):
        h, st, red, w = pipeline('aspirin', 'Cu', 21)                     # pseudo-orthorhombic metric (a ~ c); the pre-experiment Laue test gives 2/m
        it = h.records[red['record_id']]['internal']
        d_t = max(0.84e-10, rt.line_data('Cu')['lam1'] / 1.7)
        achieved = float(np.sum(it['d'] >= d_t) / rt.expected_unique(it['A_conv'], it['P'], it['ops_c'], d_t))
        self.assertLess(abs(achieved - st['predicted_completeness']), 0.03, (achieved, st['predicted_completeness']))
        self.assertEqual(h.records[st['record_id']]['data']['result']['laue_order_assumed'], 4)

    def test_flack_parameter_distinguishes_the_enantiomorphs(self):
        for inverted, x0 in ((False, 0.0), (True, 1.0)):
            h, st, red, w = pipeline('epsomite', 'Cu', 21, inverted=inverted)   # P2(1)2(1)2(1), chiral; S and Mg scatter anomalously at Cu
            self.assertLess(abs(w['flack_x'] - x0), max(0.1, 3 * w['flack_su']), (inverted, w['flack_x'], w['flack_su']))
        h, st, red, w = pipeline('nacl', 'Mo', 12)
        self.assertIsNone(w['flack_x'])                                          # centrosymmetric model


    def test_pa3_in_both_indexings_of_the_m3m_lattice(self):
        for sid, seed in (('sni4', 21), ('alum', 22)):                    # m-3 in an m-3m lattice: the glide test runs in both indexings
            h, st, red, w = pipeline(sid, 'Mo', seed, mosaic=0.6, dims=(1.2e-4, 1.0e-4, 0.9e-4))
            rr = h.records[red['record_id']]['data']['result']
            self.assertEqual((rr['laue_class'], rr['space_group_number']), ('m-3', 205), sid)

    def test_absorbing_crystals_keep_their_laue_class(self):
        for sid, laue, number in (('zno', '6/mmm', 186), ('cisplatin', '2/m', 14)):   # mu d about 3 with Mo: empirical absorption surface
            h, st, red, w = pipeline(sid, 'Mo', 21, mosaic=0.6, dims=(1.2e-4, 1.0e-4, 0.9e-4))
            rr = h.records[red['record_id']]['data']['result']
            self.assertEqual((rr['laue_class'], rr['space_group_number']), (laue, number), sid)
            self.assertLess(w['r1'], 0.05, sid)

    def test_pseudo_symmetry_is_rejected_by_the_group_check(self):
        for seed, sample_seed in ((21, None), (24, None), (23, 3)):      # I-4 with a pseudo-cubic F metric; seed 23 / sample 3 once crashed in conventional_cell
            h, st, red, w = pipeline('pentaerythritol', 'Cu', seed, mosaic=0.6, dims=(1.2e-4, 1.0e-4, 0.9e-4), sample_seed=sample_seed)
            rr = h.records[red['record_id']]['data']['result']
            self.assertEqual((rr['laue_class'], rr['space_group_number']), ('4/m', 82), seed)
            self.assertLess(w['r1'], 0.06, seed)
            self.assertEqual(w['name'], 'pentaerythritol')

    def test_true_symmetry_of_well_measured_crystals_is_kept(self):
        for sid, target, laue, numbers in (('mgo', 'Mo', 'm-3m', (225,)), ('calcite', 'Cu', '-3m', (167,)), ('quartz', 'Cu', '-3m', (152, 154))):
            h, st, red, w = pipeline(sid, target, 22, mosaic=0.6, dims=(1.2e-4, 1.0e-4, 0.9e-4))
            rr = h.records[red['record_id']]['data']['result']
            self.assertEqual(rr['laue_class'], laue, sid)
            self.assertIn(rr['space_group_number'], numbers, sid)
            self.assertLess(w['r1'], 0.06, sid)

    def test_space_group_setting_maps_the_cell_to_the_symbol(self):
        h, st, red, w = pipeline('aragonite', 'Mo', 21, mosaic=0.6, dims=(1.2e-4, 1.0e-4, 0.9e-4), sample_seed=3)
        rr = h.records[red['record_id']]['data']['result']
        it = h.records[red['record_id']]['internal']
        self.assertEqual((rr['space_group'], rr['space_group_number']), ('P n m a', 62))
        T = np.array(rr['space_group_setting'])
        self.assertTrue(np.array_equal(T, np.round(T)) and abs(round(np.linalg.det(T))) == 1)
        c = rr['cell']
        G = np.diag([c['a_m'] ** 2, c['b_m'] ** 2, c['c_m'] ** 2])
        np.testing.assert_allclose(np.sqrt(np.diag(T @ G @ T.T)) * 1e10, (5.7407, 4.9611, 7.9672), rtol=6e-3)   # Pnma aragonite (a, b, c)
        Hs = np.round(it['H'] @ T.T).astype(int)                         # h' = T h: the indices in the setting of the symbol
        strong = it['I'] > 5 * it['sigma']
        h_, k_, l_ = Hs[strong].T
        bad = ((h_ == 0) & ((k_ + l_) % 2 == 1)) | ((l_ == 0) & (h_ % 2 == 1)) | ((h_ == 0) & (l_ == 0) & (k_ % 2 == 1))   # 0kl k+l, hk0 h, 0k0 k (Pnma)
        self.assertEqual(int(bad.sum()), 0)
        self.assertGreater(int(((h_ == 0) | (l_ == 0)).sum()), 20)

    def test_noise_dominated_data_do_not_create_symmetry(self):
        weak = dict(mosaic=1.2, dims=(4e-5, 3.5e-5, 3e-5), expo=(0.1, 0.05, 0.1))                # I/sigma ~ 1: 0.04 mm, 1.2 deg, short frames (Mo)
        allowed = {'glucose': {'mmm', '2/m', '-1'}, 'epsomite': {'mmm', '2/m', '-1'}, 'pentaerythritol': {'4/m', '2/m', '-1'}}
        for sid, seed in (('glucose', 29), ('glucose', 32), ('epsomite', 29), ('pentaerythritol', 29)):
            h, st, red, w = pipeline(sid, 'Mo', seed, **weak)
            rr = h.records[red['record_id']]['data']['result']
            self.assertIn(rr['laue_class'], allowed[sid], (sid, seed))    # pseudo-tetragonal / pseudo-cubic metrics: no false rotations accepted
            search = [t for t in rr['laue_tests'] if t.get('group_check') == 'search'][0]
            self.assertGreater(search['r_int_p1'], 0.086, (sid, seed))    # really noise-dominated: the cap of the element bound is active
            self.assertTrue(all(t['bound'] <= max(rt.LAUE_ELEM_CAP, rt.LAUE_ELEM_NOISE_K * (t['noise'] or 0.0)) + 1e-12
                                for t in rr['laue_tests'] if 'group_check' not in t))
            tested = [t['bound'] for t in rr['laue_tests'] if 'group_check' not in t and t['pairs'] >= rt.LAUE_MIN_PAIRS and t['r'] is not None]
            self.assertAlmostEqual(search['element_max'], max(tested + [search['element_bound_base']]), delta=1e-12)   # the largest bound applied

    def test_origin_choice_two_is_reported(self):
        for sid, seed in (('spinel', 29), ('si', 25)):
            h, st, red, w = pipeline(sid, 'Mo', seed, mosaic=0.6, dims=(1.2e-4, 1.0e-4, 0.9e-4))
            rr = h.records[red['record_id']]['data']['result']
            self.assertEqual((rr['space_group'], rr['space_group_number']), ('F d -3 m :2', 227), sid)
            self.assertEqual(rr['space_group_origin_alternatives'], ['F d -3 m :1'], sid)
            self.assertFalse(rr['space_group_ambiguous'], sid)

    def test_cell_coincidences_are_resolved_by_the_intensities(self):
        h, st, red, w = pipeline('si', 'Mo', 27, mosaic=0.6, dims=(1.2e-4, 1.0e-4, 0.9e-4), sample_seed=3)   # a = 5.431 A, within the cell error of ZnS 5.409 A
        res = h.records[w['record_id']]['data']['result']
        self.assertEqual(w['name'], 'silicon')
        self.assertLess(w['r1'], 0.02)
        names = [m['name_en'] for m in res['matches']]
        self.assertIn('sphalerite', names)
        self.assertEqual(res['ranked_by'], 'refined wR2 of the cell matches')
        self.assertEqual(res['refinement']['models_refined'], len(res['matches']))
        self.assertGreater(min(m['refined_r1'] for m in res['matches'][1:]), 0.1)


class WeakCrystalsAndLattices(unittest.TestCase):
    def test_weak_crystal_pre_experiment_ends_and_the_clock_runs(self):
        for sid, seed in (('aspirin', 32), ('pentaerythritol', 32), ('aspirin', 29)):   # once LinAlgError inside sim.step: the operation never ended
            s = dict(sample_id='x', substance_id=sid, size_m=[4e-5, 3.5e-5, 3e-5], mosaicity_rad=1.2 * DEG, seed=seed)
            h = xray(mounted(seed=seed, sample='x', samples=(s,), target='Mo'), 50e3, 24e-3)
            h.register_principal('ev', 'evaluator')
            h.register_principal('ag', 'agent')
            r0 = h.session('ag').invoke(instance_id=h.instance_id, capability_id='experiment.pre_experiment',
                                        arguments=dict(exposure_s=0.1, frame_width_rad=0.5 * DEG, distance_m=0.045), request_id='pre')
            self.assertEqual(r0['status'], 'accepted')
            for i in range(4):
                t0 = h.t
                resp = h.session('ev').invoke(instance_id=h.instance_id, capability_id='sim.step', arguments=dict(dt_s=600.0), request_id=f'st{i}')
                self.assertEqual(resp['status'], 'succeeded', resp.get('error'))
                self.assertAlmostEqual(h.t - t0, 600.0, delta=1e-6)
            self.assertIsNone(h.op)
            pre = [x for x in h.records.values() if x['kind'] == 'pre_experiment'][0]
            self.assertEqual(pre['status'], 'completed')
            self.assertIn(pre['data']['auto_index']['reason'] if not pre['data']['auto_index']['indexed'] else 'indexed',
                          ('indexed', 'NO_PEAKS', 'TOO_FEW_PEAKS', 'NO_LATTICE', 'LOW_INDEXATION'))

    def test_conventional_cell_of_every_candidate_group(self):
        E = np.eye(3, dtype=int)
        key = lambda M: np.round(M).astype(int).tobytes()                                  # noqa: E731
        names = {('triclinic', 1), ('monoclinic', 2), ('orthorhombic', 4), ('tetragonal', 4), ('tetragonal', 8), ('hexagonal', 3), ('hexagonal', 6),
                 ('hexagonal', 12), ('cubic', 12), ('cubic', 24)}
        for sid in ('adp', 'epsomite', 'glucose', 'pentaerythritol', 'aspirin', 'nacl', 'quartz'):
            A = rt.database_cells()[sid]['A_red']
            lat = {key(M): np.round(M).astype(int) for M in rt.metric_ops(A)}
            props = [M for M in lat.values() if round(np.linalg.det(M)) > 0 and not np.array_equal(M, E)]
            groups = {}
            for i in range(len(props)):
                for j in range(i, len(props)):
                    G = rt._closure([props[i], props[j]])
                    ks = frozenset(key(g) for g in G)
                    if ks <= set(lat) | {key(E)}:
                        groups[ks] = G
            for G in groups.values():
                L = [np.array(g) for g in G] + [-np.array(g) for g in G]
                try:
                    Ac, P, system, cen = rt.conventional_cell(A, L)
                except rt.ConventionalCellError:
                    continue                                                                   # the callers fall back to the triclinic cell
                Pinv = np.linalg.inv(P)
                self.assertTrue(all(np.allclose(np.linalg.inv(Pinv @ M @ P), np.round(np.linalg.inv(Pinv @ M @ P))) for M in L), (sid, system))
                self.assertIn((system, len(G)), names, (sid, system, len(G)))


class IndexFallback(unittest.TestCase):
    def test_triclinic_fallback_of_the_indexing_is_recorded(self):
        h = xray(mounted(seed=13))
        rid = collect(h, runs=[dict(RUN, omega_end_rad=-20 * DEG)])
        real = rt.conventional_cell

        def failing(A, ops):                                                  # fault injection: the metric symmetry cannot be converted
            if len(ops) > 2:
                raise rt.ConventionalCellError('injected')
            return real(A, ops)
        base = h.invoke('processing.index', record_id=rid, threshold_i_sigma=5.0)
        order = h.records[base['record_id']]['data']['result']['lattice_symmetry_order']
        self.assertGreater(order, 2)
        rt.conventional_cell = failing
        try:
            ix = h.invoke('processing.index', record_id=rid, threshold_i_sigma=5.0)
        finally:
            rt.conventional_cell = real
        rec = h.records[ix['record_id']]
        self.assertEqual(rec['data']['result']['crystal_system'], 'triclinic')
        self.assertEqual(rec['data']['result']['lattice_symmetry_order'], 2)
        self.assertEqual(rec['data']['result']['metric_symmetry_order_unconverted'], order)
        self.assertIn('lattice_symmetry_not_converted', rec['warnings'])
        self.assertNotIn('lattice_symmetry_not_converted', h.records[base['record_id']]['warnings'])


class RecordsLedgersSessions(unittest.TestCase):
    def test_energy_and_count_ledgers_close(self):
        h = xray(mounted(seed=17, cold=False))
        e0, t0 = h.ledger()['electrical_energy_j'], h.t
        h.step(100.0)
        self.assertAlmostEqual(h.ledger()['electrical_energy_j'] - e0, (rt.ELECTRONICS_W + 40e3 * 30e-3 / rt.GENERATOR_EFFICIENCY) * 100.0, delta=1e-6)
        rid = collect(h, runs=[dict(RUN, omega_end_rad=-20 * DEG)])
        led = h.ledger()
        self.assertEqual(led['frames'], 20.0)
        self.assertAlmostEqual(led['exposure_s'], 20 * 0.2, delta=1e-9)
        self.assertAlmostEqual(led['crystal_dose_gy'], h.read('evaluator')['truth.crystal']['dose_gy'], delta=1e-6)
        self.assertGreater(led['crystal_dose_gy'], 0.0)
        self.assertEqual(led['xray_on_s'], led['filament_s'])

    def test_two_instances_are_isolated(self):
        a, b = xray(mounted(seed=18, cold=False)), mounted(seed=18, cold=False)
        collect(a, runs=[dict(RUN, omega_end_rad=-20 * DEG)])
        self.assertEqual(b.ledger()['frames'], 0.0)
        self.assertEqual(b.records, {})

    def test_agent_does_not_see_truth_or_foreign_objects(self):
        h = xray(mounted(seed=19, cold=False))
        h.register_principal('a1', 'agent'); h.register_principal('a2', 'agent'); h.register_principal('ev', 'evaluator')
        s1, s2, ev = h.session('a1'), h.session('a2'), h.session('ev')
        obs = s1.read(instance_id=rt.INSTANCE_ID)['observations']
        self.assertFalse(any(k.startswith('truth.') for k in obs))
        with self.assertRaises(rt.ActionRejected):
            s1.read(instance_id=rt.INSTANCE_ID, state_ids=['truth.crystal'])
        r = s1.invoke(instance_id=rt.INSTANCE_ID, capability_id='sample.provide', arguments=dict(ASPIRIN, sample_id='x2'), request_id='q1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = s1.invoke(instance_id=rt.INSTANCE_ID, capability_id='data.collect', arguments=dict(runs=[dict(RUN, omega_end_rad=-20 * DEG)], strategy_record_id=None,
                                                                                               frame_width_rad=0.5 * DEG), request_id='q2')
        self.assertEqual(r['status'], 'accepted')
        r2 = s2.invoke(instance_id=rt.INSTANCE_ID, capability_id='goniometer.move', arguments=dict(rt.POSE_EXAMPLE), request_id='q3')
        self.assertEqual(r2['error']['code'], 'ACCESS_DENIED')
        self.assertIsNone(s2.read(instance_id=rt.INSTANCE_ID)['observations']['operation.id']['value'])
        h.step(100.0)
        rid = h.operations[r['operation_id']]['record_id']
        res = s1.invoke(instance_id=rt.INSTANCE_ID, capability_id='measurement.result', arguments=dict(record_id=rid, max_values=100), request_id='q4')
        self.assertIsNone(res['result']['record']['template_source']['substance_id'])
        ev_res = ev.invoke(instance_id=rt.INSTANCE_ID, capability_id='measurement.result', arguments=dict(record_id=rid, max_values=100), request_id='q5')
        self.assertEqual(ev_res['result']['record']['template_source']['substance_id'], 'aspirin')
        r3 = s2.invoke(instance_id=rt.INSTANCE_ID, capability_id='measurement.result', arguments=dict(record_id=rid, max_values=100), request_id='q6')
        self.assertEqual(r3['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn(rid, s2.invoke(instance_id=rt.INSTANCE_ID, capability_id='records.list', arguments={}, request_id='q7')['result']['records'])
        h.invoke('fault.inject', fault='icing', active=True)
        ev_events = s1.invoke(instance_id=rt.INSTANCE_ID, capability_id='events.read', arguments=dict(since_s=0.0), request_id='q8')['result']['events']
        self.assertFalse(any(e['kind'] == 'fault' for e in ev_events))

    def test_idempotent_requests_and_conflicts(self):
        h = ready(seed=20)
        h.register_principal('a1', 'agent')
        s = h.session('a1')
        r1 = s.invoke(instance_id=rt.INSTANCE_ID, capability_id='safety.doors_ok', arguments={}, request_id='same')
        r2 = s.invoke(instance_id=rt.INSTANCE_ID, capability_id='safety.doors_ok', arguments={}, request_id='same')
        self.assertEqual(r1, r2)
        r3 = s.invoke(instance_id=rt.INSTANCE_ID, capability_id='power.set', arguments=dict(enabled=False), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')
        self.assertTrue(h.power)

    def test_defects_become_device_errors(self):
        h = ready(seed=21)
        h.register_principal('a1', 'agent')
        s = h.session('a1')
        orig = h._cmd_camera_view
        h._cmd_camera_view = lambda: 1 / 0
        r = s.invoke(instance_id=rt.INSTANCE_ID, capability_id='camera.view', arguments={}, request_id='z')
        self.assertEqual(r['error']['code'], 'DEVICE_ERROR')
        h._cmd_camera_view = orig

    def test_remaining_capabilities_through_the_api(self):
        h = xray(mounted(seed=22, cold=False))
        rid = collect(h, runs=[dict(RUN, omega_end_rad=-20 * DEG)])
        h.invoke('processing.index', record_id=rid, threshold_i_sigma=5.0)
        h.invoke('records.list')
        h.invoke('events.read', since_s=0.0)
        h.invoke('measurement.result', record_id=rid, max_values=200)
        h.invoke('xray.set', voltage_v=45e3, current_a=26e-3)
        h.step(10.0)
        h.invoke('shutter.close')
        h.invoke('xray.off')
        h.invoke('records.delete', record_id=rid)
        self.assertNotIn(rid, h.records)
        h.invoke('power.set', enabled=False)
        h.invoke('sim.step', dt_s=1.0)


    def test_agent_ledger_hides_the_crystal_dose(self):
        h = xray(mounted(seed=17, cold=False))
        h.invoke('shutter.open')
        h.step(30.0)
        h.register_principal('a1', 'agent')
        h.register_principal('ev', 'evaluator')
        a = h.session('a1').invoke(instance_id=rt.INSTANCE_ID, capability_id='ledger.read', arguments={}, request_id='l1')['result']['ledger']
        e = h.session('ev').invoke(instance_id=rt.INSTANCE_ID, capability_id='ledger.read', arguments={}, request_id='l2')['result']['ledger']
        self.assertNotIn('crystal_dose_gy', a)
        self.assertGreater(e['crystal_dose_gy'], 0.0)
        self.assertEqual({k: v for k, v in e.items() if k != 'crystal_dose_gy'}, a)

    def test_agent_summaries_come_from_measurements(self):
        h = xray(mounted(seed=5))
        rid = collect(h, runs=[dict(RUN, exposure_s=1.0)])
        rr = h.records[rid]['runs'][0]
        o = rr['obs']
        out = h.invoke('measurement.result', record_id=rid, max_values=64)
        self.assertEqual(out['data']['runs'][0]['spots_measured'], int(np.sum(o['i_obs'] / o['sigma'] > 3)))
        idx = h.invoke('processing.index', record_id=rid, threshold_i_sigma=5.0)
        est = h.records[idx['record_id']]['data']['result']['resolution_estimate_m']
        sel = o['i_obs'] / o['sigma'] > 3
        s1, _ = rt.pixel_rays(rr['run']['theta'], rr['run']['distance'], o['x_obs'][sel], o['y_obs'][sel])
        lam = rt.line_data(h.plant['target'])['lam1']
        d_meas = lam / (2 * np.sin(np.arccos(np.clip(s1[:, 0], -1, 1)) / 2))
        self.assertAlmostEqual(est, float(np.percentile(d_meas, 2)), delta=1e-18)


class ZDeclaredEffects(unittest.TestCase):
    def test_on_step_changes_are_declared(self):
        cases = {
            'goniometer.move': lambda h: h.invoke('goniometer.move', omega_rad=20 * DEG, kappa_rad=0.0, phi_rad=0.5, theta_rad=0.2, distance_m=0.06),
            'xray.on': lambda h: (h.invoke('xray.set_key', angle_rad=math.pi / 2), h.invoke('xray.on', voltage_v=40e3, current_a=30e-3)),
            'cryostream.set': lambda h: h.invoke('cryostream.set', temperature_k=150.0, ramp_k_s=0.1),
            'data.collect': lambda h: (xray(h), h.step(1.0), h.invoke('data.collect', runs=[dict(RUN, omega_end_rad=-20 * DEG)], strategy_record_id=None,
                                                                           frame_width_rad=0.5 * DEG)),
        }
        for cap, fn in cases.items():
            for window in (3.0, 60.0):
                base = mounted(seed=23, cold=False)
                other = mounted(seed=23, cold=False)
                if cap == 'data.collect':
                    xray(base); base.step(1.0)
                fn(other)
                before_b, before_o = base.read('evaluator'), other.read('evaluator')
                base.step(window); other.step(window)
                after_b, after_o = base.read('evaluator'), other.read('evaluator')
                diff = {k for k in after_o if _changed(after_o[k], before_o[k]) and not _changed(after_b[k], before_b[k])}
                allowed = set(rt.ON_STEP.get(cap, [])) | set(rt.EFFECTS.get(cap, [])) | {'clock.time_s', 'truth.flux_ph_s'}
                if cap in ('xray.on', 'data.collect'):
                    allowed |= set(rt.ON_STEP['xray.on']) | {'truth.filament_remaining_s'}
                self.assertFalse(diff - allowed, (cap, window, sorted(diff - allowed)))

    def test_every_executable_capability_was_accepted(self):
        implemented = [k for k in rt.CAPS if k not in rt.UNSUPPORTED and k not in rt.DECLARED]
        missing = [k for k in implemented if k not in PROCESS_EVIDENCE]
        self.assertEqual(missing, [], missing)

    def test_rejection_codes_are_declared(self):
        meta = _meta()['capabilities']
        for cap, codes in REJECTIONS.items():
            declared = {e['code'] for e in meta[cap]['errors']}
            self.assertFalse(codes - declared - {'INVALID_ARGUMENT'}, (cap, sorted(codes - declared)))


if __name__ == '__main__':
    unittest.main()
