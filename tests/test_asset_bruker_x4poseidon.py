"""tests for CHEM-156 bruker_x4poseidon: python3 -m unittest tests/test_asset_bruker_x4poseidon.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances
are isolated, agent/evaluator truth isolation, conservation (X-ray energy: every emitted joule ends in the target, window, filter, air,
sample (absorbed / scattered), detector (deposited / passed) or the shielding; travel, projection and frame ledgers), real return values
validated against the declared schemas (asset_tool lint_schema / validate_value), asynchronous completion and cancellation remnants,
host-step independence (energy ledger, dose, records), declared immediate and on_step effects (scenario states, short and long windows);
plus the instrument (boot, warm-up after every power-up, kV / current ramps, door interlock, light curtain, emergency stop latch, interlock
sensor, tube arc, mains fault, rotation stall, collision keep-out of the source head, filter slide, detector and ceiling, loading at the
door) and the physics: Kramers spectrum energy and W lines, filter hardening, focal spot growth, polychromatic Beer-Lambert transmission of
an aluminium rod against the NIST attenuation library, flat-field validity and saturation, edge spread against the focal spot, projection
count / duration / magnification / voxel size, geometry calibration against the hidden axis offset, auto-alignment, porosity / pores /
cracks against the phantom truth, beam-hardening cupping and its linearisation, ring artefacts of dead columns with ring correction and
random movement, dose.
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
from asset_runtime import bruker_x4poseidon as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}
_SC = {}


def _meta():
    if not _META:
        _META.update(rt.export_bruker_x4poseidon_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.BrukerX4PoseidonRuntime.invoke


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
    errs = asset_tool.validate_value(out, meta['output_schema'])
    if errs:
        raise AssertionError(f'{capability_id} output violates its schema: {errs[:3]}')
    if before is not None:
        assert self.t == t0, capability_id
        after = self.read('evaluator')
        undeclared = {k for k in after if _changed(before[k], after[k])} - set(rt.EFFECTS.get(capability_id, []))
        if undeclared and len(rt.EFFECTS) > 1:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=copy.deepcopy(a), result_keys=sorted(out)))
    return out


rt.BrukerX4PoseidonRuntime.invoke = _instrumented_invoke

NOP = dict(count=0, median_radius_m=1.0e-4, sigma_ln=0.0, layer=0, seed=1)
PIN = dict(substance_id='al', diameter_m=0.003, length_m=0.035)
PELLET = copy.deepcopy(rt.SAMPLE_EXAMPLE)                        # alumina 6 x 6 mm, 120 pores, one crack, Al pin 35 mm
CAL = dict(sample_id='cal_phantom', kind='calibration_phantom', diameter_m=0.010, height_m=0.010, layers=[], pores=NOP, cracks=[], inclusions=[],
           container=None, pin=PIN)
EDGE = dict(sample_id='w_edge', kind='edge_phantom', diameter_m=0.004, height_m=0.004, layers=[], pores=NOP, cracks=[], inclusions=[], container=None,
            pin=PIN)
ROD = dict(sample_id='al_rod', kind='cylinder', diameter_m=0.004, height_m=0.006, layers=[dict(thickness_m=0.006, materials=[dict(substance_id='al',
                                                                                                                              volume_fraction=1.0)])],
           pores=NOP, cracks=[], inclusions=[], container=None, pin=PIN)
DENSE = dict(PELLET, sample_id='dense_alumina', pores=NOP, cracks=[])
CRACKED = dict(PELLET, sample_id='cracked_alumina', pores=NOP,
               cracks=[dict(center_m=[0.0, 0.0, 0.003], normal=[1.0, 0.0, 0.0], radius_m=0.0022, aperture_m=150e-6)])
FAST = dict(range_deg='180', step_rad=math.radians(2.0), exposure_s=0.02, averaging=1, binning='4x4', random_movement=False)
FINE = dict(range_deg='180', step_rad=math.radians(0.4), exposure_s=0.3, averaging=2, binning='2x2', random_movement=True)
RECON_FAST = dict(record_id='PJ0001', grid='48', filter='shepp_logan', beam_hardening=0.0, bh_material=None, ring_correction=0.0, smoothing_vox=0.0,
                  post_alignment=0.0, auto_alignment=False, voi=None)
SOD_SCAN = 0.045


def centred_cz(s):
    """Chuck height that puts the middle of the sample body on the beam."""
    return rt.SOURCE_XYZ_M[2] - (s['pin']['length_m'] if s['pin'] else 0.0) - s['height_m'] / 2


def configured(seed=1, **plant):
    h = rt.BrukerX4PoseidonRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    return h


def ready(seed=1, **plant):
    h = configured(seed, **plant)
    h.invoke('power.set', enabled=True)
    h.step(rt.BOOT_S + 1.0)
    return h


def busy(h):
    return h._stage_moving() or h._filter_moving() or h._door_moving() or h.op is not None


def settle(h, limit=3600.0, dt=0.5):
    t = 0.0
    while busy(h) and t < limit:
        h.step(dt)
        t += dt
    assert not busy(h)
    return h


def wait(h, op, tmax=7200.0, dt=0.5):
    t0 = h.t
    while h.operations[op]['phase'] == 'running' and h.t - t0 < tmax:
        h.step(dt)
    return h.operations[op]['phase']


def loaded(sample=PELLET, seed=1, extra=()):
    h = ready(seed)
    for s in (sample,) + tuple(extra):
        h.invoke('sample.provide', **s)
    h.invoke('door.open')
    settle(h)
    h.invoke('sample.place', sample_id=sample['sample_id'], offset_x_m=0.0, offset_y_m=0.0)
    h.invoke('door.close')
    return settle(h)


def beam(h, kv=60e3, ia=150e-6):
    h.invoke('xray.set', voltage_v=kv, current_a=ia)
    h.invoke('xray.enable', enabled=True)
    t = 0.0
    while not h._xray_ready() and t < rt.WARMUP_S + 60.0:
        h.step(1.0)
        t += 1.0
    assert h._xray_ready()
    return h


def flat(h):
    op = h.invoke('calibration.flat_field')['operation_id']
    assert wait(h, op) == 'completed', h.operations[op]
    return h


def prepared(sample=PELLET, scan=FAST, kv=60e3, ia=150e-6, seed=1, sod=SOD_SCAN, cz=None, extra=()):
    """Sample on the chuck, beam on and warmed up, scan settings, flat field at the load position (sample out of the beam), sample moved to
    the scan position with its body centred on the beam."""
    h = loaded(sample, seed, extra)
    h.invoke('scan.configure', **scan)
    beam(h, kv, ia)
    flat(h)
    h.invoke('stage.move', sod_m=sod, chuck_z_m=centred_cz(sample) if cz is None else cz)
    return settle(h)


def scan(h):
    op = h.invoke('scan.start')['operation_id']
    assert wait(h, op) == 'completed', h.operations[op]
    return h.operations[op]['record_id']


def recon(h, rid, **kw):
    op = h.invoke('recon.start', **dict(RECON_FAST, record_id=rid, **kw))['operation_id']
    assert wait(h, op) == 'completed', h.operations[op]
    return h.operations[op]['record_id']


def rejected(h, cap, code, **a):
    before = h.read('evaluator')
    rev = h.revision
    led = h.ledger()
    try:
        h.invoke(cap, **a)
    except rt.ActionRejected as exc:
        assert str(exc) == code, (cap, str(exc), code)
        assert not _changed(h.read('evaluator'), before), cap
        assert h.revision == rev, cap
        assert not _changed(h.ledger(), led), cap
        return True
    raise AssertionError(f'{cap} was not rejected (expected {code})')


def fine_transmission(kv, fpos, sid, L):
    """Detected-energy weighted transmission of a path L through one library material, from the 0.5 keV spectrum (no energy groups)."""
    e, ph = rt.beam_spectrum(kv, fpos)
    ph = ph * rt.air_transmission(e, rt.SDD_M)
    _, edep = rt.detector_response(tuple(e))
    w = ph * edep
    mu, _ = rt.linear_mu(sid, e)
    return float((w * np.exp(-mu * L)).sum() / w.sum())


def cylinder_mask(d, radius, z_half=None):
    xs, ys, zs = rt.BrukerX4PoseidonRuntime._grids(d)
    Z, Y, X = np.meshgrid(zs, ys, xs, indexing='ij')
    m = np.hypot(X, Y) < radius
    if z_half is not None:
        m &= np.abs(Z) < z_half
    return m, np.hypot(X, Y)


class ContractAndSchemas(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.bruker_x4poseidon')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-156')

    def test_schemas_lint_and_examples_validate(self):
        m = _meta()
        for k, c in m['capabilities'].items():
            for part in ('input_schema', 'output_schema'):
                self.assertEqual(asset_tool.lint_schema(c[part]), [], (k, part))
            ex = rt.CAPS[k].get('example_arguments')
            if ex is not None:
                self.assertEqual(asset_tool.validate_value(ex, c['input_schema']), [], k)
        for k, s in m['state_slots'].items():
            self.assertEqual(asset_tool.lint_schema(s['value_schema']), [], k)
        for name in ('fresh', 'ready', 'scanning', 'reconstructed'):
            v = _scenario(name).read('evaluator')
            for k, x in v.items():
                self.assertEqual(asset_tool.validate_value(x, m['state_slots'][k]['value_schema']), [], (name, k))

    def test_metadata_references_are_consistent(self):
        m = _meta()
        slots = set(m['state_slots'])
        for k, c in m['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, (k, e))
            for x in c['observes']:
                self.assertIn(x, slots, (k, x))
            for pre in c['preconditions']:
                for x in pre['state_ids']:
                    self.assertIn(x, slots, (k, x))
            for x in c['execution']['completion_state_ids']:
                self.assertIn(x, slots, (k, x))
            self.assertEqual([e['code'] for e in c['errors']], sorted(set(e['code'] for e in c['errors']), key=[e['code'] for e in c['errors']].index), k)
        for k in rt.CAPS:
            self.assertIn(k, rt.CAP_SOURCES, k)
        self.assertEqual(set(rt.PRECONDITIONS) - set(rt.CAPS), set())
        for k, v in rt.EFFECTS.items():
            self.assertIn(k, rt.CAPS)
            self.assertEqual(set(v) - slots, set(), k)
        for k, v in rt.ON_STEP.items():
            self.assertIn(k, rt.CAPS)
            self.assertEqual(set(v) - slots, set(), k)
        for mech in m['mechanisms'].values():
            self.assertEqual(set(mech['state_ids']) - slots, set())

    def test_unsupported_capabilities_report_unsupported(self):
        h = ready()
        for k in rt.UNSUPPORTED:
            with self.assertRaises(rt.ActionRejected) as cm:
                _orig_invoke(h, k, **(rt.CAPS[k].get('example_arguments') or {}))
            self.assertEqual(str(cm.exception), 'UNSUPPORTED')
            self.assertEqual(_meta()['capabilities'][k]['availability'], 'unsupported')
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r = s.invoke(instance_id=h.instance_id, capability_id='export.dataset', arguments={}, request_id='u1')
        self.assertEqual(r['status'], 'unsupported')
        self.assertEqual(r['error']['code'], 'UNSUPPORTED')


class HostClockAndPurity(unittest.TestCase):
    def test_read_and_describe_do_not_advance_time(self):
        h = _scenario('scanning')
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        t, rev, led = h.t, h.revision, h.ledger()
        for _ in range(3):
            h.read('evaluator'); s.read(instance_id=h.instance_id); s.describe(instance_id=h.instance_id)
            s.invoke(instance_id=h.instance_id, capability_id='events.read', arguments=dict(since_s=0.0), request_id=f'e{_}')
        self.assertEqual((h.t, h.revision), (t, rev))
        self.assertFalse(_changed(h.ledger(), led))

    def test_step_zero_and_negative(self):
        h = ready()
        v = h.read('evaluator')
        h.step(0.0)
        self.assertFalse(_changed(h.read('evaluator'), v))
        for bad in (-1.0, float('nan'), float('inf')):
            with self.assertRaises(rt.ActionRejected):
                h.step(bad)
        self.assertEqual(h.invoke('sim.step', dt_s=2.5)['time_s'], v['clock.time_s'] + 2.5)

    def test_host_step_chopping_does_not_change_the_result(self):
        """Warm-up, a kV / current ramp with the beam on, stage travel, a flat field and a scan advanced in single host steps and in irregular
        small steps give identical records, positions, doses and energy ledgers (rate cells and exact V^2 I integration)."""
        def run(chunks):
            h = loaded(PELLET)
            h.invoke('scan.configure', **dict(FAST, random_movement=True))

            def adv(T):
                if chunks:
                    t, i = 0.0, 0
                    while t < T - 1e-12:
                        d = min((0.037, 0.211, 0.0043, 0.5, 1.7)[i % 5], T - t)
                        h.step(d)
                        t += d
                        i += 1
                else:
                    h.step(T)
            h.invoke('xray.enable', enabled=True)
            adv(rt.WARMUP_S + 5.0)
            h.invoke('xray.set', voltage_v=70e3, current_a=100e-6)
            adv(3.0)
            h.invoke('xray.set', voltage_v=60e3, current_a=150e-6)
            adv(3.0)
            h.invoke('calibration.flat_field')
            adv(3.0)
            h.invoke('stage.move', sod_m=SOD_SCAN, chuck_z_m=centred_cz(PELLET))
            adv(15.0)
            op = h.invoke('scan.start')['operation_id']
            adv(h.op['duration_s'] + 1.0)
            self.assertEqual(h.operations[op]['phase'], 'completed')
            return h, h.records[h.operations[op]['record_id']]
        a, ra = run(False)
        b, rb = run(True)
        self.assertEqual(ra['preview'], rb['preview'])
        self.assertEqual(ra['transmission'], rb['transmission'])
        self.assertAlmostEqual(ra['dose_gy'], rb['dose_gy'], delta=1e-10 * ra['dose_gy'])
        self.assertTrue(np.array_equal(a._data[ra['record_id']]['q'], b._data[rb['record_id']]['q']))
        self.assertAlmostEqual(a.rot, b.rot, places=12)
        self.assertAlmostEqual(a.sod, b.sod, places=12)
        for k, v in a.ledger().items():
            if k == 'energy_residual_j':
                continue
            self.assertAlmostEqual(v, b.ledger()[k], delta=1e-9 * max(1.0, abs(v)), msg=k)

    def test_reads_do_not_change_later_results(self):
        def run(reads):
            h = prepared(PELLET)
            op = h.invoke('scan.start')['operation_id']
            for i in range(20):
                if reads:
                    h.read('evaluator'); h.invoke('events.read', since_s=0.0); h.invoke('records.list'); h.invoke('ledger.read')
                    h.invoke('measurement.result', record_id='FF0001')
                h.step(0.7)
            wait(h, op)
            return h.records[h.operations[op]['record_id']]['preview'], h.ledger()
        self.assertEqual(run(False), run(True))

    def test_set_points_only_write_targets(self):
        h = _scenario('positioned')
        s0 = h.sod
        out = h.invoke('stage.move', sod_m=s0 + 0.030, chuck_z_m=h.cz - 0.005)
        self.assertEqual(h.sod, s0)
        self.assertEqual(h.read()['stage.sod_target_m'], s0 + 0.030)
        self.assertAlmostEqual(out['duration_s'], 0.030 / rt.CARRIAGE_SPEED_M_S)
        self.assertAlmostEqual(out['magnification'], rt.SDD_M / (s0 + 0.030))
        h.step(0.5)
        self.assertAlmostEqual(h.sod, s0 + 0.5 * rt.CARRIAGE_SPEED_M_S, places=12)
        self.assertTrue(h.read()['stage.moving'])
        settle(h)
        r0 = h.rot
        out = h.invoke('stage.rotate', angle_rad=(r0 - 0.6) % (2 * math.pi))
        self.assertEqual(h.rot, r0)
        self.assertAlmostEqual(out['duration_s'], 0.6 / rt.ROTATION_SPEED_RAD_S)
        h.step(0.25)
        self.assertAlmostEqual(rt._rot_delta(r0, h.rot), -0.25 * rt.ROTATION_SPEED_RAD_S, places=9)       # shortest direction
        settle(h)
        out = h.invoke('filter.select', position=3)
        self.assertEqual(h.slide, 0.0)
        self.assertEqual(h.read()['filter.position'], 3)
        self.assertAlmostEqual(out['duration_s'], 3 * rt.FILTER_S_PER_STEP)
        h.step(0.5)
        self.assertAlmostEqual(h.slide, rt.FILTER_PITCH_M, places=12)
        self.assertTrue(h.read()['filter.moving'])
        settle(h)
        v0 = h.xv
        h.invoke('xray.set', voltage_v=70e3, current_a=150e-6)
        self.assertEqual(h.xv, v0)
        self.assertFalse(h.read()['xray.ready'])
        h.step(0.5)
        self.assertAlmostEqual(h.xv, v0 + 0.5 * rt.KV_RAMP_V_S, places=6)
        h.step(0.6)
        self.assertEqual(h.xv, 70e3)
        self.assertTrue(h.read()['xray.ready'])
        self.assertFalse(h.read()['calibration.flat_field']['valid'])     # new voltage and filter: the flat field no longer matches


class PowerSafetyAndInterlocks(unittest.TestCase):
    def test_boot_and_warm_up_after_every_power_up(self):
        h = configured()
        rejected(h, 'door.open', 'POWER_OFF')
        h.invoke('power.set', enabled=True)
        self.assertEqual(h.read()['system.state'], 'booting')
        rejected(h, 'xray.enable', 'NOT_READY', enabled=True)
        h.step(rt.BOOT_S - 0.01)
        self.assertEqual(h.read()['system.state'], 'booting')
        h.step(0.02)
        self.assertEqual(h.read()['system.state'], 'ready')
        self.assertEqual(h.invoke('xray.enable', enabled=True)['warmup_s'], rt.WARMUP_S)
        h.step(rt.WARMUP_S / 2)
        v = h.read()
        self.assertAlmostEqual(v['xray.voltage_v'], 0.5 * h.xv_set, delta=1e-6)       # conditioning: linear from zero to the set point
        self.assertAlmostEqual(v['xray.current_a'], 0.5 * h.xi_set, delta=1e-15)
        self.assertFalse(v['xray.ready'])
        self.assertTrue(v['panel.status_orange'])
        h.step(rt.WARMUP_S / 2 + 0.01)
        self.assertTrue(h.read()['xray.ready'])
        h.invoke('xray.enable', enabled=False)
        self.assertFalse(h.read()['xray.on'])
        out = h.invoke('xray.enable', enabled=True)                     # same boot: no second warm-up, a ramp from zero
        self.assertEqual(out['warmup_s'], 0.0)
        self.assertAlmostEqual(out['ramp_s'], max(h.xv_set / rt.KV_RAMP_V_S, h.xi_set / rt.CURRENT_RAMP_A_S))
        h.step(out['ramp_s'] + 0.01)
        self.assertTrue(h.read()['xray.ready'])
        h.invoke('power.set', enabled=False)
        self.assertEqual(h.read()['xray.voltage_v'], 0.0)
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 0.1)
        self.assertEqual(h.invoke('xray.enable', enabled=True)['warmup_s'], rt.WARMUP_S)

    def test_door_interlock_and_light_curtain(self):
        h = beam(loaded())
        rejected(h, 'door.open', 'XRAY_ON')
        h.invoke('xray.enable', enabled=False)
        h.invoke('door.open')
        h.step(0.1)
        self.assertFalse(h.read()['door.closed'])
        self.assertFalse(h.read()['interlock.ok'])
        rejected(h, 'xray.enable', 'DOOR_OPEN', enabled=True)
        settle(h)
        self.assertAlmostEqual(h.door, rt.DOOR_TRAVEL_M)
        h.invoke('door.close')
        h.step(1.0)
        d = h.door
        h.invoke('fault.inject', fault='light_curtain_blocked', active=True)        # closing door stops and reopens
        self.assertEqual(h.door_target, rt.DOOR_TRAVEL_M)
        h.step(0.5)
        self.assertGreater(h.door, d)
        rejected(h, 'door.close', 'LIGHT_CURTAIN_BLOCKED')
        h.invoke('fault.inject', fault='light_curtain_blocked', active=False)
        settle(h)
        h.invoke('door.close')
        settle(h)
        self.assertTrue(h.read()['interlock.ok'])
        self.assertEqual(h.ledger()['door_cycles'], 2.5)      # five arrivals x 0.5: open, close (loading), open, reopen after the curtain, close
        codes = [e['code'] for e in h.invoke('events.read', since_s=0.0)['events']]
        for c in ('DOOR_OPEN', 'DOOR_CLOSED', 'LIGHT_CURTAIN'):
            self.assertIn(c, codes)

    def test_emergency_stop_latches_and_needs_release_and_acknowledge(self):
        h = prepared(PELLET)
        op = h.invoke('scan.start')['operation_id']
        h.step(3.0)
        rot = h.rot
        out = h.invoke('emergency.stop')
        self.assertTrue(out['stopped'])
        self.assertEqual(h.operations[op]['phase'], 'failed')
        self.assertEqual(h.operations[op]['reason'], 'EMERGENCY_STOP')
        self.assertEqual(h.records[h.operations[op]['record_id']]['status'], 'failed')
        v = h.read()
        self.assertFalse(v['xray.on'])
        self.assertTrue(v['panel.status_red'])
        self.assertEqual(v['system.error_code'], 'EMERGENCY_STOP')
        h.step(5.0)
        self.assertEqual(h.rot, rot)
        rejected(h, 'system.acknowledge', 'CAUSE_PERSISTS')
        rejected(h, 'xray.enable', 'EMERGENCY_STOP', enabled=True)
        rejected(h, 'stage.go_to', 'EMERGENCY_STOP', position='load')
        h.invoke('emergency.release')
        rejected(h, 'xray.enable', 'ERROR_PENDING', enabled=True)
        self.assertEqual(h.invoke('system.acknowledge')['cleared'], 'EMERGENCY_STOP')
        self.assertTrue(h.read()['panel.status_green'])
        out = h.invoke('xray.enable', enabled=True)
        self.assertEqual(out['warmup_s'], 0.0)                                     # the boot's warm-up was done already
        h.step(out['ramp_s'] + 0.01)
        self.assertTrue(h.read()['xray.ready'])

    def test_interlock_sensor_and_arc_faults(self):
        h = beam(loaded())
        h.invoke('fault.inject', fault='door_sensor', active=True)
        v = h.read()
        self.assertFalse(v['xray.on'])
        self.assertFalse(v['interlock.ok'])
        self.assertEqual(v['system.error_code'], 'INTERLOCK_FAULT')
        rejected(h, 'system.acknowledge', 'CAUSE_PERSISTS')
        h.invoke('fault.inject', fault='door_sensor', active=False)
        h.invoke('system.acknowledge')
        h.invoke('scan.configure', **FAST)
        out = h.invoke('xray.enable', enabled=True)
        self.assertEqual(out['warmup_s'], 0.0)
        self.assertAlmostEqual(out['ramp_s'], 60e3 / rt.KV_RAMP_V_S)
        h.step(out['ramp_s'] + 0.01)
        flat(h)
        h.invoke('stage.move', sod_m=SOD_SCAN, chuck_z_m=centred_cz(PELLET))
        settle(h)
        op = h.invoke('scan.start')['operation_id']
        h.step(2.0)
        h.invoke('fault.inject', fault='xray_arc', active=True)
        self.assertEqual(h.operations[op]['reason'], 'XRAY_ARC')
        self.assertFalse(h.read()['xray.enabled'])
        rejected(h, 'xray.enable', 'ERROR_PENDING', enabled=True)
        rejected(h, 'system.acknowledge', 'CAUSE_PERSISTS')
        h.invoke('fault.inject', fault='xray_arc', active=False)
        h.invoke('system.acknowledge')
        h.invoke('xray.enable', enabled=True)
        h.step(60e3 / rt.KV_RAMP_V_S + 0.01)
        self.assertTrue(h.read()['xray.ready'])

    def test_mains_fault_and_power_loss_during_a_scan(self):
        h = prepared(PELLET)
        op = h.invoke('scan.start')['operation_id']
        h.step(2.0)
        sod, rot = h.sod, h.rot
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0))
        v = h.read()
        self.assertFalse(v['power.enabled'])
        self.assertEqual(v['system.error_code'], 'MAINS_FAULT')
        self.assertEqual(h.operations[op]['reason'], 'POWER_OFF')
        h.step(10.0)
        self.assertEqual((h.sod, h.rot), (sod, rot))
        rejected(h, 'power.set', 'MAINS_OUT_OF_RANGE', enabled=True)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE))
        h.invoke('power.set', enabled=True)
        self.assertIsNone(h.read()['system.error_code'])
        h.step(rt.BOOT_S + 0.1)
        self.assertEqual(h.read()['system.state'], 'ready')
        rejected(h, 'plant.configure', 'SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=99))

    def test_power_button_toggles(self):
        h = configured()
        self.assertTrue(h.invoke('power.press_button')['enabled'])
        self.assertFalse(h.invoke('power.press_button')['enabled'])
        self.assertEqual(h.read()['system.state'], 'off')


class MotionAndCollision(unittest.TestCase):
    def test_keep_out_of_source_filter_detector_and_ceiling(self):
        h = _scenario('flat_done')
        cz = centred_cz(PELLET)                                        # 0.362: the stage housing stays below the source head
        rejected(h, 'stage.move', 'COLLISION_RISK', sod_m=0.020, chuck_z_m=cz)       # chuck (r 12 mm) against the filter changer face
        self.assertTrue(h._geometry_ok(0.0281, cz))                   # chuck radius 12 mm + filter face 14 mm + margin 2 mm
        self.assertFalse(h._geometry_ok(0.0279, cz))
        rejected(h, 'stage.move', 'COLLISION_RISK', sod_m=0.060, chuck_z_m=0.387)      # stage housing (r 56.6 mm) in the source keep-out
        self.assertTrue(h._geometry_ok(0.0727, 0.387))
        rejected(h, 'stage.move', 'COLLISION_RISK', sod_m=0.395, chuck_z_m=cz)       # detector side
        h.invoke('stage.move', sod_m=0.395, chuck_z_m=0.200)                         # everything below the detector housing
        settle(h)
        tall = dict(DENSE, sample_id='tall', height_m=0.250, layers=[dict(thickness_m=0.250, materials=[dict(substance_id='alumina', volume_fraction=1.0)])])
        h2 = _scenario('door_open')
        h2.invoke('sample.provide', **tall)
        h2.invoke('sample.place', sample_id='tall', offset_x_m=0.0, offset_y_m=0.0)
        h2.invoke('door.close')
        settle(h2)
        rejected(h2, 'stage.move', 'COLLISION_RISK', sod_m=0.250, chuck_z_m=0.399)    # ceiling
        self.assertTrue(h2._geometry_ok(0.250, rt.CHAMBER_CEILING_M - rt.COLLISION_MARGIN_M - 0.285 - 1e-6))

    def test_loading_needs_the_open_door_and_the_load_position(self):
        h = _scenario('ready')
        rejected(h, 'sample.place', 'DOOR_CLOSED', sample_id='alumina_pellet', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('door.open')
        h.step(1.0)
        rejected(h, 'sample.place', 'DOOR_CLOSED', sample_id='alumina_pellet', offset_x_m=0.0, offset_y_m=0.0)
        settle(h)
        h.invoke('stage.move', sod_m=SOD_SCAN, chuck_z_m=0.300)
        rejected(h, 'sample.place', 'MOTION_IN_PROGRESS', sample_id='alumina_pellet', offset_x_m=0.0, offset_y_m=0.0)
        settle(h)
        rejected(h, 'sample.place', 'NOT_ACCESSIBLE', sample_id='alumina_pellet', offset_x_m=0.0, offset_y_m=0.0)
        out = h.invoke('stage.go_to', position='load')
        self.assertAlmostEqual(out['duration_s'], max((rt.LOAD_POSITION['sod'] - SOD_SCAN) / rt.CARRIAGE_SPEED_M_S, 0.05 / rt.LIFT_SPEED_M_S))
        settle(h)
        self.assertEqual(h.read()['stage.position_label'], 'load')
        rejected(h, 'sample.place', 'UNKNOWN_SAMPLE', sample_id='nothing', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('sample.place', sample_id='alumina_pellet', offset_x_m=0.002, offset_y_m=-0.001)
        rejected(h, 'sample.place', 'SAMPLE_NOT_ON_BENCH', sample_id='alumina_pellet', offset_x_m=0.0, offset_y_m=0.0)
        rejected(h, 'sample.place', 'STAGE_OCCUPIED', sample_id='cal_phantom', offset_x_m=0.0, offset_y_m=0.0)
        self.assertEqual(h.read()['sample.on_stage'], dict(sample_id='alumina_pellet', offset_x_m=0.002, offset_y_m=-0.001))
        rejected(h, 'sample.remove', 'SAMPLE_NOT_ON_STAGE', sample_id='cal_phantom')
        h.invoke('sample.remove', sample_id='alumina_pellet')
        self.assertIn('alumina_pellet', h.read()['sample.bench_ids'])

    def test_sample_definitions_are_checked(self):
        h = configured()
        bad = [dict(DENSE, sample_id='b1', height_m=0.005),                                           # layers do not add up
               dict(DENSE, sample_id='b2', layers=[dict(thickness_m=0.006, materials=[dict(substance_id='alumina', volume_fraction=0.7)])]),
               dict(CAL, sample_id='b3', pores=dict(NOP, count=5)),                                   # phantoms have no pores
               dict(DENSE, sample_id='b4', cracks=[dict(center_m=[0.0025, 0.0, 0.003], normal=[1.0, 0.0, 0.0], radius_m=0.001, aperture_m=1e-5)]),
               dict(DENSE, sample_id='b5', pores=dict(count=400, median_radius_m=0.9e-3, sigma_ln=0.0, layer=0, seed=1))]    # too dense
        for s in bad:
            rejected(h, 'sample.provide', 'INVALID_SAMPLE', **s)
        rejected(h, 'sample.provide', 'UNKNOWN_SUBSTANCE', **dict(DENSE, sample_id='b6', layers=[dict(thickness_m=0.006, materials=[
            dict(substance_id='unobtainium', volume_fraction=1.0)])]))
        out = h.invoke('sample.provide', **PELLET)
        pores = h.samples['alumina_pellet']['_pores']
        self.assertEqual(len(pores), 120)
        crack = PELLET['cracks'][0]
        vol = (math.pi * 0.003 ** 2 * 0.006 - sum(4 / 3 * math.pi * p['radius_m'] ** 3 for p in pores) - math.pi * crack['radius_m'] ** 2 * crack['aperture_m'])
        self.assertAlmostEqual(out['mass_kg'], vol * rt.density('alumina'), delta=1e-12)
        rejected(h, 'sample.provide', 'SAMPLE_EXISTS', **PELLET)
        for i in range(rt.MAX_SAMPLES - 1):
            h.invoke('sample.provide', **dict(ROD, sample_id=f'r{i}'))
        rejected(h, 'sample.provide', 'TOO_MANY_SAMPLES', **dict(ROD, sample_id='one_more'))

    def test_rotation_stall_stops_the_scan_and_the_stage(self):
        h = prepared(PELLET)
        op = h.invoke('scan.start')['operation_id']
        h.step(2.0)
        h.invoke('fault.inject', fault='rotation_stall', active=True)
        self.assertEqual(h.operations[op]['reason'], 'STAGE_FAULT')
        self.assertEqual(h.read()['system.error_code'], 'STAGE_FAULT')
        rot = h.rot
        h.step(3.0)
        self.assertEqual(h.rot, rot)
        h.invoke('fault.inject', fault='rotation_stall', active=False)
        h.invoke('system.acknowledge')
        h.invoke('stage.rotate', angle_rad=0.0)
        settle(h)
        self.assertEqual(h.rot, 0.0)


class XrayPhysics(unittest.TestCase):
    def test_kramers_spectrum_energy_and_tungsten_lines(self):
        for kv in (30e3, 60e3, 100e3):
            e, ph = rt.unabsorbed_spectrum(kv)
            energy = 4 * math.pi * float((ph * e).sum()) * 0.5 * rt.KEV
            self.assertAlmostEqual(energy / (rt.KRAMERS_K_PER_V * 74 * kv * kv * 1.602176634e-19), 1.0, places=9)     # eta V e per electron
            self.assertLess(e.max(), kv / 1e3)
        e, ph = rt.emitted_spectrum(100e3)
        j = int(np.argmin(np.abs(e - 59.32)))
        self.assertGreater(ph[j] / (0.5 * (ph[j - 2] + ph[j + 2])), 3.0)                   # W K-alpha above the K edge (69.5 kV)
        e, ph = rt.emitted_spectrum(60e3)
        j = int(np.argmin(np.abs(e - 58.5)))
        self.assertLess(ph[j] / ph[j - 2], 1.0)                                          # below the K edge: bremsstrahlung only
        k = int(np.argmin(np.abs(e - 8.40)))
        self.assertGreater(ph[k] / (0.5 * (ph[k - 1] + ph[k + 1])), 2.0)                   # W L-alpha (L-beta sits two bins higher)
        e2, ph2 = rt.emitted_spectrum(10.5e3)
        self.assertAlmostEqual(4 * math.pi * float((rt.unabsorbed_spectrum(10.5e3)[1] * e2).sum()) * 0.5 * rt.KEV /
                               (rt.KRAMERS_K_PER_V * 74 * 10.5e3 ** 2 * 1.602176634e-19), 1.0, places=9)   # lines above the voltage are not excited

    def test_filters_harden_the_beam_and_power_sets_the_spot(self):
        def mean_e(pos):
            e, ph = rt.beam_spectrum(60e3, pos)
            return float((ph * e * e).sum() / (ph * e).sum())
        m = [mean_e(p) for p in range(rt.FILTER_POSITIONS)]
        self.assertTrue(m[0] < m[1] < m[2] < m[3])
        self.assertTrue(m[4] < m[5])
        self.assertGreater(m[6], max(m[3], m[4]))
        e, ph = rt.emitted_spectrum(60e3)
        _, pb = rt.beam_spectrum(60e3, 0)
        self.assertLess(pb[int(np.argmin(np.abs(e - 3.0)))] / ph[int(np.argmin(np.abs(e - 3.0)))], pb[-10] / ph[-10])      # Be window
        h = configured()
        self.assertAlmostEqual(h.invoke('xray.set', voltage_v=100e3, current_a=0.2e-3)['focal_spot_m'], 25e-6)
        self.assertAlmostEqual(h.invoke('xray.set', voltage_v=40e3, current_a=0.1e-3)['focal_spot_m'], 5e-6)
        self.assertAlmostEqual(h.invoke('xray.set', voltage_v=20e3, current_a=0.1e-3)['focal_spot_m'], 5e-6)
        rejected(h, 'xray.set', 'POWER_LIMIT', voltage_v=100e3, current_a=0.25e-3)

    def test_radiograph_transmission_follows_polychromatic_beer_lambert(self):
        """Transmission profile of a 4 mm aluminium rod against the fine-spectrum prediction with the NIST attenuation of Al: the chord of
        each ray through the rod follows from the cone-beam geometry and the hidden axis offset."""
        h = prepared(ROD, dict(FAST, exposure_s=0.3, averaging=4, binning='2x2'))
        op = h.invoke('xray.radiograph', roi=None)['operation_id']
        self.assertEqual(wait(h, op), 'completed')
        d = h._data['RG0001']
        t, w = d['image'].astype(float), d['window']
        us = w['u0'] + (np.arange(w['nu']) + 0.5) * w['pitch']
        vs = w['v1'] - (np.arange(w['nv']) + 0.5) * w['pitch']
        rows = np.abs(vs) < 0.2 * 0.006 * rt.SDD_M / SOD_SCAN
        prof = t[rows].mean(0)
        a = h.truth['axis_offset']
        th = np.arctan(us / rt.SDD_M)
        dist = np.abs(SOD_SCAN * np.sin(th) - a * np.cos(th))
        L = 2 * np.sqrt(np.clip(0.002 ** 2 - dist ** 2, 0.0, None))
        sel = L > 1.0e-3
        self.assertGreater(sel.sum(), 5)
        pred = np.array([fine_transmission(60e3, 0, 'al', x) for x in L[sel]])
        rel = np.abs(prof[sel] / pred - 1)
        self.assertLess(float(rel.mean()), 0.03, (prof[sel], pred))
        self.assertLess(float(rel.max()), 0.06)
        outside = L == 0
        self.assertAlmostEqual(float(prof[outside].mean()), 1.0, delta=0.01)
        mu_thin = -math.log(fine_transmission(60e3, 0, 'al', 1e-3)) / 1e-3
        mu_thick = -math.log(fine_transmission(60e3, 0, 'al', 4e-3)) / 4e-3
        self.assertGreater(mu_thin, 1.3 * mu_thick)                                      # polychromatic beam: hardening with path length
        self.assertIn('RG0001', h.invoke('records.list')['records'])

    def test_flat_field_validity_and_saturation(self):
        h = _scenario('positioned')
        self.assertTrue(h.read()['calibration.flat_field']['valid'])
        h.invoke('xray.set', voltage_v=70e3, current_a=150e-6)
        rejected(h, 'xray.radiograph', 'XRAY_NOT_READY', roi=None)
        h.step(1.5)
        rejected(h, 'xray.radiograph', 'FLAT_FIELD_REQUIRED', roi=None)
        rejected(h, 'calibration.flat_field', 'SAMPLE_IN_BEAM')
        h.invoke('xray.set', voltage_v=60e3, current_a=150e-6)
        h.step(1.5)
        h.invoke('xray.radiograph', roi=None)
        settle(h)
        h.invoke('filter.select', position=2)
        rejected(h, 'xray.radiograph', 'MOTION_IN_PROGRESS', roi=None)
        settle(h)
        rejected(h, 'xray.radiograph', 'FLAT_FIELD_REQUIRED', roi=None)
        h.invoke('filter.select', position=0)
        settle(h)
        h.invoke('scan.configure', **dict(FAST, binning='2x2'))
        rejected(h, 'scan.start', 'FLAT_FIELD_REQUIRED')
        h2 = _scenario('beam')
        h2.invoke('xray.set', voltage_v=50e3, current_a=0.4e-3)
        h2.invoke('scan.configure', **dict(FAST, exposure_s=10.0))
        h2.step(3.0)
        op = h2.invoke('calibration.flat_field')['operation_id']
        wait(h2, op)
        r = h2.records[h2.operations[op]['record_id']]
        self.assertEqual(r['flat_field']['mean_bright_adu'], rt.FULL_SCALE_ADU)
        self.assertTrue([x for x in r['warnings'] if '饱和' in x])
        op = h2.invoke('xray.radiograph', roi=None)['operation_id']
        wait(h2, op)
        self.assertGreater(h2.records[h2.operations[op]['record_id']]['saturated_fraction'], 0.5)

    def test_edge_resolution_follows_the_focal_spot(self):
        """Tungsten edge at magnification 10 in a native-pixel ROI: the 10-90 % width of the edge spread function follows the penumbra of the
        focal spot (5 um at 4 W, 25 um at 20 W) combined with the scintillator spread."""
        widths = {}
        for P in (4.0, 20.0):
            h = prepared(EDGE, dict(FAST, exposure_s=0.5, averaging=4, binning='1x1'), kv=80e3, ia=P / 80e3)
            u_edge = h.truth['axis_offset'] * rt.SDD_M / SOD_SCAN
            op = h.invoke('xray.radiograph', roi=dict(center_u_m=u_edge, center_v_m=0.0, size_px=128))['operation_id']
            wait(h, op)
            e = h.invoke('data.edge_resolution', record_id=h.operations[op]['record_id'])
            M = rt.SDD_M / SOD_SCAN
            sig = math.hypot(rt.focal_spot_m(P) / 2.355 * (M - 1) / rt.PIXEL_PITCH_M, rt.DETECTOR_PSF_PX)
            self.assertAlmostEqual(e['width_detector_px'] / (2.563 * sig), 1.0, delta=0.2, msg=(P, e))
            self.assertAlmostEqual(e['width_10_90_m'], e['width_detector_px'] * rt.PIXEL_PITCH_M / M, delta=1e-12)
            self.assertLess(abs(e['edge_angle_rad']), 0.01)
            widths[P] = e['width_10_90_m']
        self.assertGreater(widths[20.0] / widths[4.0], 1.8)
        h = _scenario('radiographed')
        res = h.invoke('data.edge_resolution', record_id='RG0001')         # a porous pellet is no straight edge
        self.assertIsNone(res['width_10_90_m'])


class CTWorkflowPhysics(unittest.TestCase):
    def test_projection_count_duration_and_geometry(self):
        h = _scenario('flat_done')
        out = h.invoke('scan.configure', **dict(FAST, step_rad=math.radians(1.0)))
        half_fan = math.atan(rt.DETECTOR_PIXELS[0] * rt.PIXEL_PITCH_M / 2 / rt.SDD_M)
        n = int(math.floor((math.pi + 2 * half_fan) / math.radians(1.0) + 1e-9)) + 1
        self.assertEqual(out['projections'], n)
        self.assertAlmostEqual(out['duration_s'], n * (1 * (0.02 + rt.FRAME_OVERHEAD_S) + rt.STEP_OVERHEAD_S))
        out = h.invoke('scan.configure', **dict(FAST, range_deg='360', step_rad=math.radians(1.0), averaging=3))
        self.assertEqual(out['projections'], 360)
        self.assertAlmostEqual(out['duration_s'], 360 * (3 * (0.02 + rt.FRAME_OVERHEAD_S) + rt.STEP_OVERHEAD_S))
        h.invoke('scan.configure', **FAST)
        h.invoke('calibration.flat_field')
        settle(h)
        h.invoke('stage.move', sod_m=0.050, chuck_z_m=centred_cz(PELLET))
        settle(h)
        v = h.read()
        self.assertAlmostEqual(v['geometry.magnification'], 9.0)
        self.assertAlmostEqual(v['geometry.voxel_size_m'], rt.PIXEL_PITCH_M * 4 / 9.0)
        self.assertAlmostEqual(v['geometry.fov_m'][0], 0.1386 / 9.0)
        self.assertAlmostEqual(v['geometry.fov_m'][1], 0.1188 / 9.0)
        led0 = h.ledger()
        rot0 = h.rot
        op = h.invoke('scan.start')['operation_id']
        dur = h.op['duration_s']
        self.assertAlmostEqual(dur, v['scan.duration_s'])
        h.step(dur - 0.01)
        self.assertEqual(h.operations[op]['phase'], 'running')
        self.assertAlmostEqual(h.read()['measurement.progress'], (dur - 0.01) / dur)
        h.step(0.02)
        self.assertEqual(h.operations[op]['phase'], 'completed')
        span = math.pi + 2 * half_fan
        self.assertAlmostEqual(rt._rot_delta(rot0, h.rot), rt._rot_delta(0.0, span), places=9)
        r = h.records[h.operations[op]['record_id']]
        self.assertEqual(r['projections_nominal'], v['scan.projections'])
        self.assertAlmostEqual(r['magnification'], 9.0)
        self.assertAlmostEqual(r['voxel_size_nominal_m'], rt.PIXEL_PITCH_M * 4 / 9.0)
        L = h.ledger()
        self.assertEqual(L['projections'] - led0['projections'], r['projections_nominal'])
        self.assertEqual(L['frames'] - led0['frames'], r['projections_nominal'] * FAST['averaging'])
        self.assertAlmostEqual(L['rotation_rad'] - led0['rotation_rad'], span, places=9)

    def test_geometry_calibration_recovers_the_hidden_axis_offset(self):
        found = {}
        for seed in (1, 2):
            h = prepared(CAL, seed=seed, sod=0.050)
            op = h.invoke('calibration.geometry')['operation_id']
            self.assertEqual(wait(h, op), 'completed')
            g = h.records[h.operations[op]['record_id']]['geometry']
            self.assertEqual(g['balls'], rt.CAL_BALLS)
            self.assertAlmostEqual(g['axis_offset_m'], h.truth['axis_offset'], delta=2.0e-6)
            self.assertEqual(h.read()['calibration.geometry']['axis_offset_m'], g['axis_offset_m'])
            found[seed] = (g['axis_offset_m'], h.truth['axis_offset'])
        self.assertGreater(abs(found[1][1] - found[2][1]), 5e-6)
        h = _scenario('positioned')
        rejected(h, 'calibration.geometry', 'PHANTOM_REQUIRED')

    def test_axis_alignment_calibrated_manual_and_automatic(self):
        h = _scenario('scanned')
        a = h.truth['axis_offset']
        vid = recon(h, 'PJ0001', post_alignment=None, auto_alignment=False)
        self.assertTrue([w for w in h.records[vid]['warnings'] if '未做几何标定' in w])
        self.assertEqual(h.records[vid]['volume']['axis_offset_used_m'], 0.0)
        vid = recon(h, 'PJ0001', post_alignment=a + 1e-6, auto_alignment=False)
        self.assertAlmostEqual(h.records[vid]['volume']['axis_offset_used_m'], a + 1e-6, delta=1e-15)
        vid = recon(h, 'PJ0001', post_alignment=None, auto_alignment=True, grid='48')
        self.assertAlmostEqual(h.records[vid]['volume']['axis_offset_used_m'], a, delta=0.35 * rt.PIXEL_PITCH_M * 4 * SOD_SCAN / rt.SDD_M + 5e-6)
        h.geo_cal = dict(axis_offset_m=a - 2e-6, calibrated_s=h.t)
        vid = recon(h, 'PJ0001', post_alignment=None, auto_alignment=False)
        self.assertAlmostEqual(h.records[vid]['volume']['axis_offset_used_m'], a - 2e-6, delta=1e-15)
        self.assertEqual(h.records[vid]['warnings'], [])

    def test_porosity_and_cracks_against_the_phantom_truth(self):
        h = prepared(PELLET, FINE)
        pj = scan(h)
        vid = recon(h, pj, grid='96', filter='shepp_logan', beam_hardening=0.5, bh_material='alumina', post_alignment=None, auto_alignment=True,
                    voi=dict(center_m=[0.0, 0.0, 0.0], size_m=[0.0066, 0.0066, 0.0066]))
        p = h.invoke('data.porosity', record_id=vid, region=None, threshold_1_m=None, min_pore_voxels=2)
        tr = rt.sample_truth(h.samples['alumina_pellet'])
        self.assertAlmostEqual(p['porosity'] / tr['porosity'], 1.0, delta=0.25, msg=(p, tr['porosity']))
        self.assertAlmostEqual(p['pore_count'] / tr['pore_count'], 1.0, delta=0.15)
        d50 = float(np.median(tr['pore_equivalent_diameters_m']))
        self.assertAlmostEqual(p['equivalent_diameter_m']['d50'] / d50, 1.0, delta=0.25)
        self.assertAlmostEqual(p['open_porosity'] + p['closed_porosity'], p['porosity'], delta=1e-12)
        self.assertEqual(h.read()['result.last_porosity'], p['porosity'])
        att = h.invoke('data.attenuation', record_id=vid, region=None)
        self.assertGreater(att['mean_1_m'], 300.0)
        sl = h.invoke('data.slice', record_id=vid, axis='z', position_m=0.0, size_px=32)
        self.assertEqual(len(sl['mu_1_m']), 32)
        rejected(h, 'data.slice', 'OUT_OF_VOLUME', record_id=vid, axis='z', position_m=0.01, size_px=32)
        rejected(h, 'data.porosity', 'INVALID_REGION', record_id=vid, region=dict(center_m=[0.0, 0.0, 0.0], radius_m=0.001, z_min_m=0.001, z_max_m=0.0),
                 threshold_1_m=None, min_pore_voxels=2)
        rejected(h, 'data.porosity', 'RECORD_NOT_SUITABLE', record_id=pj, region=None, threshold_1_m=None, min_pore_voxels=2)
        h2 = prepared(CRACKED, FINE)
        pj2 = scan(h2)
        vid2 = recon(h2, pj2, grid='96', beam_hardening=0.5, bh_material='alumina', post_alignment=None, auto_alignment=True,
                     voi=dict(center_m=[0.0, 0.0, 0.0], size_m=[0.0066, 0.0066, 0.0066]))
        c = h2.invoke('data.cracks', record_id=vid2, region=None, threshold_1_m=None, min_voxels=20)
        self.assertGreaterEqual(c['crack_count'], 1, c)
        big = c['cracks'][0]
        self.assertGreater(abs(big['normal'][0]), 0.9)
        self.assertAlmostEqual(big['area_m2'] / (math.pi * 0.0022 ** 2), 1.0, delta=0.5)
        c0 = h.invoke('data.cracks', record_id=vid, region=None, threshold_1_m=None, min_voxels=20)
        self.assertEqual(c0['crack_count'], 0)                            # the 30 um crack of the pellet is below the 69 um voxels

    def test_beam_hardening_cupping_and_its_linearisation(self):
        h = prepared(DENSE, dict(FAST, step_rad=math.radians(1.0), exposure_s=0.1, binning='2x2'))
        pj = scan(h)
        a = h.truth['axis_offset']
        out = {}
        for bh in (0.0, 1.0):
            vid = recon(h, pj, grid='64', beam_hardening=bh, bh_material='alumina' if bh else None, post_alignment=a,
                        voi=dict(center_m=[0.0, 0.0, 0.0], size_m=[0.0068, 0.0068, 0.002]))
            d = h._data[vid]
            _, rr = cylinder_mask(d, 1.0)
            v = d['volume']
            out[bh] = (float(v[rr < 0.0008].mean()), float(v[(rr > 0.0022) & (rr < 0.0027)].mean()))
        cup0 = (out[0.0][1] - out[0.0][0]) / out[0.0][1]
        cup1 = (out[1.0][1] - out[1.0][0]) / out[1.0][1]
        self.assertGreater(cup0, 0.10)
        self.assertLess(abs(cup1), 0.02)
        e_mean = h._data[pj]['grp']['mean_e']
        self.assertAlmostEqual(out[1.0][0] / float(rt.linear_mu('alumina', [e_mean])[0][0]), 1.0, delta=0.03)
        rejected(h, 'recon.start', 'UNKNOWN_SUBSTANCE', **dict(RECON_FAST, record_id=pj, beam_hardening=1.0, bh_material='kryptonite'))

    def test_ring_artefacts_dead_columns_ring_correction_and_random_movement(self):
        def run(fault, rm, ring):
            h = prepared(ROD, dict(FAST, range_deg='360', step_rad=math.radians(1.0), exposure_s=0.1, binning='2x2', random_movement=rm))
            if fault:
                h.invoke('fault.inject', fault='detector_defects', active=True)
            pj = scan(h)
            vid = recon(h, pj, grid='64', ring_correction=ring, post_alignment=h.truth['axis_offset'],
                        voi=dict(center_m=[0.0, 0.0, 0.0], size_m=[0.005, 0.005, 0.002]))
            d = h._data[vid]
            m, _ = cylinder_mask(d, 0.0017)
            return float(d['volume'][m].std())
        clean, dead, corrected, moved = run(False, False, 0.0), run(True, False, 0.0), run(True, False, 1.0), run(True, True, 0.0)
        self.assertGreater(dead, 4 * clean)
        self.assertLess(corrected, 1.5 * clean)
        self.assertLess(moved, 0.9 * dead)

    def test_dose_and_energy_ledger_close(self):
        h = _scenario('scanned')
        L = h.ledger()
        sinks = sum(L[k] for k in rt.ENERGY_SINKS)
        self.assertLess(abs(L['energy_residual_j']), 1e-12 * L['xray_emitted_j'])
        self.assertAlmostEqual(sinks / L['xray_emitted_j'], 1.0, places=12)
        for k in rt.ENERGY_SINKS:
            self.assertGreaterEqual(L[k], 0.0, k)
        for k in ('sample_absorbed_j', 'sample_scattered_j', 'detector_absorbed_j', 'detector_passed_j', 'air_absorbed_j', 'target_absorbed_j'):
            self.assertGreater(L[k], 0.0, k)
        self.assertLess(L['xray_emitted_j'] / L['electrical_energy_j'], rt.KRAMERS_K_PER_V * 74 * 60e3 * 1.0000001)   # efficiency eta = K Z V
        s = h.samples['alumina_pellet']
        self.assertAlmostEqual(L['sample_dose_gy'], s['dose_gy'], delta=1e-12 * s['dose_gy'])
        r = h.records['PJ0001']
        self.assertGreater(r['dose_gy'], 0.0)
        g = copy.deepcopy(h)                                             # steady beam: dE/dt = K Z V^2 I, electrical V I
        L0 = g.ledger()
        g.step(10.0)
        L1 = g.ledger()
        self.assertAlmostEqual(L1['electrical_energy_j'] - L0['electrical_energy_j'], 60e3 * 150e-6 * 10.0, delta=1e-9)
        self.assertAlmostEqual(L1['xray_emitted_j'] - L0['xray_emitted_j'], rt.KRAMERS_K_PER_V * 74 * 60e3 ** 2 * 150e-6 * 10.0, delta=1e-12)
        self.assertAlmostEqual(L1['xray_on_s'] - L0['xray_on_s'], 10.0, delta=1e-9)


class FaultsCancelAndData(unittest.TestCase):
    def test_cancel_scan_and_recon_keep_the_remnants(self):
        h = _scenario('positioned')
        h.register_principal('a1', 'agent')
        s = h.session('a1')
        r = s.invoke(instance_id=h.instance_id, capability_id='scan.start', arguments={}, request_id='s1')
        self.assertEqual(r['status'], 'accepted')
        h.step(4.0)
        rot = h.rot
        c = s.cancel(r['operation_id'], 'c1')
        self.assertEqual(c['result']['phase'], 'cancelled')
        self.assertEqual(s.cancel(r['operation_id'], 'c1'), c)
        with self.assertRaises(rt.ActionRejected):
            s.cancel(r['operation_id'], 'c2')
        rid = h.operations[r['operation_id']]['record_id']
        self.assertEqual(h.records[rid]['status'], 'cancelled')
        self.assertEqual(h.records[rid]['reason'], 'USER_CANCEL')
        self.assertNotIn(rid, h._data)
        h.step(2.0)
        self.assertEqual(h.rot, rot)
        self.assertEqual(s.poll(r['operation_id'])['status'], 'cancelled')
        h = _scenario('scanned')
        h.register_principal('a1', 'agent')
        s = h.session('a1')
        r = s.invoke(instance_id=h.instance_id, capability_id='recon.start', arguments=RECON_FAST, request_id='r1')
        h.step(1.0)
        s.cancel(r['operation_id'], 'c1')
        self.assertIsNone(h.read()['recon.id'])
        self.assertEqual(h.records[h.operations[r['operation_id']]['record_id']]['status'], 'cancelled')

    def test_data_eviction_fails_a_pending_reconstruction(self):
        h = _scenario('scanned')
        op = h.invoke('recon.start', **RECON_FAST)['operation_id']
        for _ in range(8):
            o = h.invoke('xray.radiograph', roi=None)['operation_id']
            wait(h, o, dt=0.01)
        self.assertNotIn('PJ0001', h._data)
        wait(h, op)
        self.assertEqual(h.operations[op]['reason'], 'DATA_EVICTED')
        self.assertEqual(h.records[h.operations[op]['record_id']]['status'], 'failed')
        rejected(h, 'data.porosity', 'RECORD_NOT_SUITABLE', record_id=h.operations[op]['record_id'], region=None, threshold_1_m=None, min_pore_voxels=2)

    def test_scan_and_reconstruction_run_in_parallel(self):
        h = _scenario('scanned')
        op_r = h.invoke('recon.start', **RECON_FAST)['operation_id']
        rejected(h, 'recon.start', 'RECON_BUSY', **RECON_FAST)
        op_s = h.invoke('scan.start')['operation_id']
        v = h.read()
        self.assertEqual(v['measurement.kind'], 'projections')
        self.assertEqual(v['recon.id'], h.operations[op_r]['record_id'])
        self.assertGreater(v['recon.remaining_s'], 0.0)
        self.assertEqual(wait(h, op_r), 'completed')
        self.assertEqual(h.operations[op_s]['phase'], 'running')
        self.assertEqual(wait(h, op_s), 'completed')
        self.assertEqual(h.read()['result.last_record_id'], h.operations[op_s]['record_id'])

    def test_records_full(self):
        h = _scenario('scanned')
        r = h.records['PJ0001']
        for i in range(rt.MAX_RECORDS - len(h.records)):
            h.records[f'X{i:04d}'] = dict(copy.deepcopy(r), record_id=f'X{i:04d}', finished_s=0.0)
        rejected(h, 'scan.start', 'RECORDS_FULL')
        rejected(h, 'xray.radiograph', 'RECORDS_FULL', roi=None)
        rejected(h, 'recon.start', 'RECORDS_FULL', **RECON_FAST)


class LedgersIsolationSessions(unittest.TestCase):
    def test_two_instances_are_isolated(self):
        a, b = _scenario('positioned'), _scenario('positioned')
        b2 = copy.deepcopy(b.read('evaluator'))
        a.invoke('stage.rotate', angle_rad=1.0)
        a.step(3.0)
        a.invoke('fault.inject', fault='xray_arc', active=True)
        self.assertFalse(_changed(b.read('evaluator'), b2))
        c = configured(seed=2)
        self.assertNotEqual(c.truth['axis_offset'], a.truth['axis_offset'])

    def test_agent_does_not_see_truth_faults_or_templates(self):
        h = _scenario('positioned')
        h.register_principal('agent', 'agent')
        h.register_principal('judge', 'evaluator')
        s, e = h.session('agent'), h.session('judge')
        obs = s.read(instance_id=h.instance_id)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        self.assertTrue([k for k in e.read(instance_id=h.instance_id)['observations'] if k.startswith('truth.')])
        with self.assertRaises(rt.ActionRejected):
            s.read(instance_id=h.instance_id, state_ids=['truth.axis_offset_m'])
        d = s.describe(instance_id=h.instance_id)
        for k in ('fault.inject', 'ledger.read', 'plant.configure', 'sample.provide', 'sim.step'):
            self.assertNotIn(k, d['capabilities'])
        self.assertNotIn('truth.samples', d['state_slots'])
        for k in ('fault.inject', 'ledger.read'):
            r = s.invoke(instance_id=h.instance_id, capability_id=k, arguments=rt.CAPS[k]['example_arguments'], request_id='x' + k)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        e.invoke(instance_id=h.instance_id, capability_id='fault.inject', arguments=dict(fault='detector_defects', active=True), request_id='f1')
        ev = s.invoke(instance_id=h.instance_id, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e1')['result']['events']
        self.assertFalse([x for x in ev if x['kind'] == 'fault'])
        r = s.invoke(instance_id=h.instance_id, capability_id='scan.start', arguments={}, request_id='m1')
        wait(h, r['operation_id'])
        rid = h.operations[r['operation_id']]['record_id']
        res = s.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=dict(record_id=rid), request_id='r1')['result']
        self.assertEqual(res['template_source']['substances'], [])
        self.assertIsNone(res['dose_gy'])                                   # the dose follows the true sample mass
        self.assertEqual(s.poll(r['operation_id'])['result']['template_source']['substances'], [])
        self.assertIsNone(s.poll(r['operation_id'])['result']['dose_gy'])
        full = e.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=dict(record_id=rid), request_id='r2')['result']
        self.assertEqual(full['template_source']['substances'], ['al', 'alumina'])
        self.assertGreater(full['dose_gy'], 0.0)
        self.assertEqual(e.poll(r['operation_id'])['status'], 'completed')   # evaluators observe every operation
        self.assertEqual(s.read(instance_id=h.instance_id)['observations']['result.last_record_id']['value'], rid)

    def test_records_and_control_belong_to_their_agent(self):
        h = _scenario('positioned')
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        r = s1.invoke(instance_id=h.instance_id, capability_id='scan.start', arguments={}, request_id='m1')
        h.step(0.5)
        o2 = s2.read(instance_id=h.instance_id)['observations']
        self.assertTrue(o2['measurement.started_by_other_agent']['value'])
        self.assertIsNone(o2['measurement.id']['value'])
        x = s2.invoke(instance_id=h.instance_id, capability_id='stage.rotate', arguments=dict(angle_rad=0.1), request_id='s1')
        self.assertEqual(x['error']['code'], 'ACCESS_DENIED')
        with self.assertRaises(rt.ActionRejected):
            s2.poll(r['operation_id'])
        with self.assertRaises(rt.ActionRejected):
            s2.cancel(r['operation_id'], 'c1')
        wait(h, r['operation_id'])
        rid = h.operations[r['operation_id']]['record_id']
        v = s1.invoke(instance_id=h.instance_id, capability_id='recon.start', arguments=dict(RECON_FAST, record_id=rid), request_id='v1')
        wait(h, v['operation_id'])
        vid = h.operations[v['operation_id']]['record_id']
        for cap, args in (('measurement.result', dict(record_id=rid)), ('recon.start', dict(RECON_FAST, record_id=rid)),
                          ('data.porosity', dict(record_id=vid, region=None, threshold_1_m=None, min_pore_voxels=2))):
            self.assertEqual(s2.invoke(instance_id=h.instance_id, capability_id=cap, arguments=args, request_id='q' + cap)['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn(rid, s2.invoke(instance_id=h.instance_id, capability_id='records.list', arguments={}, request_id='l1')['result']['records'])
        self.assertEqual(s2.read(instance_id=h.instance_id)['observations']['result.last_record_id']['value'], 'FF0001')    # the host's record only
        s1.invoke(instance_id=h.instance_id, capability_id='data.porosity', arguments=dict(record_id=vid, region=None, threshold_1_m=None, min_pore_voxels=2),
                  request_id='p1')
        self.assertIsNotNone(s1.read(instance_id=h.instance_id)['observations']['result.last_porosity']['value'])
        self.assertIsNone(s2.read(instance_id=h.instance_id)['observations']['result.last_porosity']['value'])
        a = s1.invoke(instance_id=h.instance_id, capability_id='stage.rotate', arguments=dict(angle_rad=0.2), request_id='s9')
        self.assertEqual(s1.invoke(instance_id=h.instance_id, capability_id='stage.rotate', arguments=dict(angle_rad=0.2), request_id='s9'), a)
        c = s1.invoke(instance_id=h.instance_id, capability_id='stage.rotate', arguments=dict(angle_rad=0.3), request_id='s9')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(s1.invoke(instance_id='other', capability_id='records.list', arguments={}, request_id='z')['error']['code'], 'UNKNOWN_INSTANCE')
        r = s1.invoke(instance_id=h.instance_id, capability_id='scan.start', arguments={}, request_id='m2')
        h.step(0.5)
        self.assertTrue(s2.invoke(instance_id=h.instance_id, capability_id='emergency.stop', arguments={}, request_id='es')['result']['stopped'])   # always works
        with self.assertRaises(rt.ActionRejected):
            s1.write(instance_id=h.instance_id, state_id='xray.voltage_set_v', value=50e3, request_id='w1')

    def test_queries_and_bookkeeping(self):
        h = _scenario('scanned')
        p = h.invoke('provenance.read')
        self.assertEqual(p['substances'], len(rt.library()['substances']))
        self.assertEqual(len(p['library_sha256']), 64)
        codes = [e['code'] for e in h.invoke('events.read', since_s=0.0)['events']]
        for c in ('POWER_ON', 'READY', 'SAMPLE_PROVIDED', 'SAMPLE_PLACED', 'DOOR_OPEN', 'DOOR_CLOSED', 'WARMUP', 'XRAY_ON', 'WARMUP_DONE', 'OP_DONE'):
            self.assertIn(c, codes)
        self.assertEqual(h.invoke('events.read', since_s=h.t + 1.0)['events'], [])
        self.assertEqual(h.invoke('records.list')['records'], ['FF0001', 'PJ0001'])
        rejected(h, 'measurement.result', 'UNKNOWN_RECORD', record_id='PJ9999')
        rejected(h, 'recon.start', 'UNKNOWN_RECORD', **dict(RECON_FAST, record_id='PJ9999'))
        rejected(h, 'recon.start', 'RECORD_NOT_SUITABLE', **dict(RECON_FAST, record_id='FF0001'))
        rejected(h, 'recon.start', 'INVALID_VOI', **dict(RECON_FAST, voi=dict(center_m=[0.02, 0.0, 0.0], size_m=[0.001, 0.001, 0.001])))
        rejected(h, 'data.edge_resolution', 'RECORD_NOT_SUITABLE', record_id='PJ0001')
        r = h.invoke('measurement.result', record_id='FF0001')
        self.assertEqual(r['kind'], 'flat_field')
        self.assertEqual(r['flat_field']['frames'], rt.FLAT_FIELD_FRAMES)

    def test_go_to_rotate_and_host_step(self):
        h = _scenario('flat_done')
        out = h.invoke('stage.go_to', position='center')
        settle(h)
        self.assertEqual((h.sod, h.cz), (rt.CENTER_POSITION['sod'], rt.CENTER_POSITION['cz']))
        self.assertEqual(h.read()['stage.position_label'], 'center')
        self.assertAlmostEqual(out['duration_s'], max(abs(rt.CENTER_POSITION['sod'] - rt.LOAD_POSITION['sod']) / rt.CARRIAGE_SPEED_M_S,
                                                      abs(rt.CENTER_POSITION['cz'] - rt.LOAD_POSITION['cz']) / rt.LIFT_SPEED_M_S))
        h.invoke('stage.move', sod_m=0.2, chuck_z_m=0.31)
        self.assertIsNone(h.read()['stage.position_label'])
        h.invoke('stage.rotate', angle_rad=6.0)                           # the shortest way from 0 is backwards
        h.step(0.1)
        self.assertGreater(h.rot, 6.0)
        t = h.t
        self.assertAlmostEqual(h.invoke('sim.step', dt_s=4.0)['time_s'], t + 4.0)


class RuntimeReviewRound1Regressions(unittest.TestCase):
    def test_F01_short_scan_parker_weights_give_a_uniform_disc(self):
        """Exact line integrals of a 16 mm disc (mu 100 1/m): the 180 deg + fan reconstruction is as uniform as the 360 deg one."""
        h = rt.BrukerX4PoseidonRuntime()
        sod, R, mu = 0.10, 0.008, 100.0
        span = math.pi + 2 * math.atan(rt.DETECTOR_PIXELS[0] * rt.PIXEL_PITCH_M / 2 / rt.SDD_M)
        for full in (False, True):
            n_p = 360
            angles = np.array([2 * math.pi * k / n_p for k in range(n_p)]) if full else np.array([span * k / (n_p - 1) for k in range(n_p)])
            nu, nv = 192, 6
            pitch = 2 * R * rt.SDD_M / sod * 1.2 / nu
            w = dict(u0=-nu / 2 * pitch, v1=nv / 2 * pitch, pitch=pitch, nu=nu, nv=nv, binning=1)
            D, _, _, _ = h._window_rays(w)
            q = []
            for b in angles:
                S, Ds = h._to_sample(dict(pin=None), D, sod, rt.SOURCE_XYZ_M[2], b, 0.0, 0.0, 0.0)
                c1, c2 = rt._cyl(S, Ds, R)
                q.append((mu * np.clip(c2 - c1, 0.0, None)).reshape(nv, nu))
            vol, origin, voxel = h._fdk(np.array(q), angles, full, 2 * math.pi if full else span, sod, w, 0.0, 48, 'ram_lak',
                                        dict(center_m=[0.0, 0.0, 0.0], size_m=[0.012, 0.012, 0.0005]))
            xs = origin[0] + np.arange(vol.shape[2]) * voxel
            ys = origin[1] + np.arange(vol.shape[1]) * voxel
            Y, X = np.meshgrid(ys, xs, indexing='ij')
            sl = vol[vol.shape[0] // 2] / mu
            inside = np.hypot(X, Y) < 0.8 * R
            self.assertLess(float(np.abs(sl[inside] - 1).max()), 0.005, full)
            self.assertAlmostEqual(float(sl[inside & (Y > 0.001)].mean()), float(sl[inside & (Y < -0.001)].mean()), delta=0.002)

    def test_F02_the_swept_path_of_carriage_and_lift_is_checked(self):
        tall = dict(DENSE, sample_id='tall_rod', diameter_m=0.040, height_m=0.100, pin=None,
                    layers=[dict(thickness_m=0.100, materials=[dict(substance_id='nist_pmma', volume_fraction=1.0)])])
        h = loaded(tall)
        h.invoke('stage.move', sod_m=0.250, chuck_z_m=0.399)
        settle(h)
        self.assertTrue(h._geometry_ok(0.030, 0.160))                     # the target itself is clear ...
        rejected(h, 'stage.move', 'COLLISION_RISK', sod_m=0.030, chuck_z_m=0.160)   # ... but the path crosses the source keep-out
        h.invoke('stage.move', sod_m=0.250, chuck_z_m=0.160)               # lower first, then approach
        settle(h)
        h.invoke('stage.move', sod_m=0.030, chuck_z_m=0.160)
        while busy(h):
            self.assertTrue(h._geometry_ok(h.sod, h.cz), (h.sod, h.cz))
            h.step(0.1)
        self.assertEqual((h.sod, h.cz), (0.030, 0.160))

    def test_F03_integral_floats_in_nested_integer_fields(self):
        a, b = configured(), configured()
        a.invoke('sample.provide', **dict(PELLET, sample_id='p', pores=dict(PELLET['pores'], count=120.0, seed=7.0)))
        b.invoke('sample.provide', **dict(PELLET, sample_id='p'))
        self.assertEqual(a.samples['p']['_pores'], b.samples['p']['_pores'])
        h = prepared(PELLET)
        op = h.invoke('xray.radiograph', roi=dict(center_u_m=0.0, center_v_m=0.0, size_px=64.0))['operation_id']
        self.assertEqual(wait(h, op), 'completed')

    def test_F04_only_the_pin_in_the_field_is_not_a_scan(self):
        high = dict(DENSE, sample_id='high_body', pin=dict(substance_id='al', diameter_m=0.003, length_m=0.060))
        h = prepared(high, cz=0.348)                                     # body 8-14 mm above the beam, field +-5.9 mm at the axis
        rejected(h, 'scan.start', 'SAMPLE_NOT_IN_FIELD')

    def test_F05_dose_counts_only_the_sample_body(self):
        def rate(s):
            h = loaded(s)
            h.invoke('stage.move', sod_m=SOD_SCAN, chuck_z_m=centred_cz(s))
            settle(h)
            beam(h)
            d0 = h.samples[s['sample_id']].get('dose_gy', 0.0)
            h.step(20.0)
            return (h.samples[s['sample_id']]['dose_gy'] - d0) / 20.0, h
        al, h = rate(DENSE)
        pb, _ = rate(dict(DENSE, sample_id='pb_pin', pin=dict(substance_id='pb', diameter_m=0.003, length_m=0.035)))
        cont, hc = rate(dict(DENSE, sample_id='pb_cont', container=dict(substance_id='pb', wall_m=0.3e-3)))
        self.assertAlmostEqual(pb / al, 1.0, delta=0.01)
        self.assertLess(cont, 0.2 * al)
        self.assertGreater(hc.ledger()['holder_absorbed_j'], hc.ledger()['sample_absorbed_j'])

    def test_F06_small_sample_absorbed_power_follows_the_inverse_square_law(self):
        tiny = dict(DENSE, sample_id='tiny', diameter_m=0.001, height_m=0.001, layers=[dict(thickness_m=0.001, materials=[dict(substance_id='alumina', volume_fraction=1.0)])])
        h = loaded(tiny)
        beam(h)
        out = {}
        for sod in (0.10, 0.20):
            h.invoke('stage.move', sod_m=sod, chuck_z_m=centred_cz(tiny))
            settle(h)
            out[sod] = h._energy_rates()['sample_absorbed_j']
        self.assertGreater(out[0.10], 0.0)
        self.assertAlmostEqual(out[0.10] / out[0.20], (0.20 / 0.10) ** 2 * math.exp(0.0), delta=0.3)

    def test_F07_set_point_change_during_the_warm_up(self):
        base = loaded(PELLET)
        base.invoke('stage.move', sod_m=SOD_SCAN, chuck_z_m=centred_cz(PELLET))
        settle(base)
        base.invoke('xray.set', voltage_v=20e3, current_a=150e-6)
        base.invoke('xray.enable', enabled=True)
        base.step(162.0)
        v0 = base.xv
        base.invoke('xray.set', voltage_v=100e3, current_a=150e-6)
        base.step(1e-6)
        self.assertLess(abs(base.xv - v0), 1.0)                           # the program keeps its end point: no jump
        a, b = copy.deepcopy(base), copy.deepcopy(base)
        a.step(30.0)
        for _ in range(3000):
            b.step(0.01)
        for k, v in a.ledger().items():
            if k != 'energy_residual_j':
                self.assertAlmostEqual(v, b.ledger()[k], delta=1e-9 * max(1.0, abs(v)), msg=k)
        self.assertAlmostEqual(a.xv, min(100e3, 20e3 + (30.0 - (rt.WARMUP_S - 162.0 - 1e-6)) * rt.KV_RAMP_V_S), delta=1.0)

    def test_F09_F10_F11_detector_field_and_region_limits(self):
        h = _scenario('positioned')
        rejected(h, 'xray.radiograph', 'ROI_OUTSIDE_DETECTOR', roi=dict(center_u_m=0.07, center_v_m=0.06, size_px=256))
        h.invoke('xray.radiograph', roi=dict(center_u_m=0.05, center_v_m=0.04, size_px=64))
        h2 = _scenario('scanned')
        for voi in (dict(center_m=[0.0, 0.0, 0.15], size_m=[0.002, 0.002, 0.002]), dict(center_m=[0.0, 0.0, -0.15], size_m=[0.01, 0.01, 0.2])):
            rejected(h2, 'recon.start', 'INVALID_VOI', **dict(RECON_FAST, voi=voi))
        h3 = prepared(CAL, sod=0.030)
        rejected(h3, 'calibration.geometry', 'PHANTOM_NOT_IN_FIELD')

    def test_F12_auto_alignment_needs_the_central_plane(self):
        h = prepared(DENSE, dict(FAST, step_rad=math.radians(1.0), exposure_s=0.1, binning='2x2'), cz=centred_cz(DENSE) - 0.004, seed=3)
        pj = scan(h)
        vid = recon(h, pj, post_alignment=None, auto_alignment=True)
        self.assertEqual(h.records[vid]['volume']['axis_offset_used_m'], 0.0)
        self.assertTrue([w for w in h.records[vid]['warnings'] if '中心平面' in w])

    def test_F13_the_brass_chuck_attenuates(self):
        nopin = dict(DENSE, sample_id='nopin', pin=None)
        h = loaded(nopin)
        h.invoke('scan.configure', **dict(FAST, binning='2x2', exposure_s=0.1))
        beam(h)
        flat(h)
        h.invoke('stage.move', sod_m=0.080, chuck_z_m=0.397)
        settle(h)
        rejected(h, 'calibration.flat_field', 'SAMPLE_IN_BEAM')
        op = h.invoke('xray.radiograph', roi=None)['operation_id']
        wait(h, op)
        self.assertLess(h.records[h.operations[op]['record_id']]['transmission']['min'], 0.01)   # chuck and housing are opaque

    def test_F14_safety_button_pressed_while_switched_off(self):
        h = configured()
        h.invoke('emergency.stop')
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 1.0)
        self.assertEqual(h.read()['system.error_code'], 'EMERGENCY_STOP')
        h.invoke('emergency.release')
        rejected(h, 'door.open', 'ERROR_PENDING')
        self.assertEqual(h.invoke('system.acknowledge')['cleared'], 'EMERGENCY_STOP')
        h.invoke('door.open')

    def test_F15_F17_latched_errors_are_deterministic_and_not_overwritten(self):
        h = configured()
        for f in ('rotation_stall', 'xray_arc', 'door_sensor'):
            h.invoke('fault.inject', fault=f, active=True)
        h.invoke('power.set', enabled=True)
        self.assertEqual(h.read()['system.error_code'], 'INTERLOCK_FAULT')
        h.invoke('fault.inject', fault='door_sensor', active=False)
        h.invoke('system.acknowledge')
        self.assertEqual(h.read()['system.error_code'], 'XRAY_ARC')        # the next persisting cause latches its own error
        h.invoke('fault.inject', fault='xray_arc', active=False)
        h.invoke('fault.inject', fault='rotation_stall', active=False)
        h.invoke('system.acknowledge')
        h2 = _scenario('beam')
        h2.invoke('fault.inject', fault='xray_arc', active=True)
        h2.invoke('fault.inject', fault='xray_arc', active=False)
        h2.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0))
        h2.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE))
        h2.invoke('power.set', enabled=True)
        h2.step(rt.BOOT_S + 1.0)
        self.assertEqual(h2.read()['system.error_code'], 'XRAY_ARC')
        rejected(h2, 'xray.enable', 'ERROR_PENDING', enabled=True)

    def test_F16_the_target_absorbs_the_back_half_and_its_self_absorption(self):
        h = _scenario('positioned')
        L0 = h.ledger()
        h.step(20.0)
        L1 = h.ledger()
        em = L1['xray_emitted_j'] - L0['xray_emitted_j']
        self.assertGreater((L1['target_absorbed_j'] - L0['target_absorbed_j']) / em, 0.5)
        self.assertLess((L1['shielding_j'] - L0['shielding_j']) / em, 0.5)
        self.assertLess(abs(L1['energy_residual_j'] - L0['energy_residual_j']), 1e-12 * em)

    def test_F19_mass_is_consistent_with_the_ray_traced_geometry(self):
        h = configured()
        incl = dict(DENSE, sample_id='incl', inclusions=[dict(substance_id='w', center_m=[0.0, 0.0, 0.003], radius_m=0.0015)])
        crk = dict(DENSE, sample_id='crk', cracks=[dict(center_m=[0.0, 0.0, 0.003], normal=[1.0, 0.0, 0.0], radius_m=0.0025, aperture_m=1e-3)])
        body = math.pi * 0.003 ** 2 * 0.006
        vi = 4 / 3 * math.pi * 0.0015 ** 3
        vc = math.pi * 0.0025 ** 2 * 1e-3
        self.assertAlmostEqual(h.invoke('sample.provide', **incl)['mass_kg'], (body - vi) * rt.density('alumina') + vi * rt.density('w'), delta=1e-12)
        self.assertAlmostEqual(h.invoke('sample.provide', **crk)['mass_kg'], (body - vc) * rt.density('alumina'), delta=1e-12)

    def test_F20_F21_empty_region_and_evicted_projection_data(self):
        h = _scenario('reconstructed')
        rejected(h, 'data.attenuation', 'INVALID_REGION', record_id='VO0001', region=dict(center_m=[0.19, 0.19, 0.0], radius_m=0.001, z_min_m=0.15, z_max_m=0.2))
        for _ in range(8):
            o = h.invoke('xray.radiograph', roi=None)['operation_id']
            wait(h, o, dt=0.01)
        rejected(h, 'recon.start', 'DATA_NOT_CACHED', **RECON_FAST)

    def test_F22_saturated_and_starved_bright_fields(self):
        h = _scenario('beam')
        h.invoke('stage.move', sod_m=0.25, chuck_z_m=0.16)
        settle(h)
        h.invoke('xray.set', voltage_v=60e3, current_a=150e-6)
        h.invoke('scan.configure', **dict(FAST, exposure_s=0.1))
        h.step(1.0)
        op = h.invoke('calibration.flat_field')['operation_id']
        wait(h, op)
        self.assertEqual(h.records[h.operations[op]['record_id']]['flat_field']['mean_bright_adu'], rt.FULL_SCALE_ADU)
        op = h.invoke('xray.radiograph', roi=dict(center_u_m=0.05, center_v_m=0.045, size_px=32))['operation_id']
        wait(h, op)
        self.assertAlmostEqual(h.records[h.operations[op]['record_id']]['transmission']['mean'], 1.0, delta=0.02)   # air, clipped bright field
        h.invoke('xray.set', voltage_v=20e3, current_a=1e-6)
        h.invoke('filter.select', position=5)
        h.invoke('scan.configure', **dict(FAST, binning='1x1'))
        settle(h)
        h.step(3.0)
        op = h.invoke('calibration.flat_field')['operation_id']
        wait(h, op)
        self.assertTrue([w for w in h.records[h.operations[op]['record_id']]['warnings'] if '亮场信号不足' in w])
        self.assertFalse(h.read()['calibration.flat_field']['valid'])
        rejected(h, 'xray.radiograph', 'FLAT_FIELD_REQUIRED', roi=None)

    def test_F23_records_can_be_deleted(self):
        h = _scenario('scanned')
        r = h.records['PJ0001']
        for i in range(rt.MAX_RECORDS - len(h.records)):
            h.records[f'X{i:04d}'] = dict(copy.deepcopy(r), record_id=f'X{i:04d}', finished_s=0.0)
        rejected(h, 'xray.radiograph', 'RECORDS_FULL', roi=None)
        op = h.invoke('recon.start', **RECON_FAST)['operation_id'] if len(h.records) < rt.MAX_RECORDS else None
        self.assertIsNone(op)
        self.assertEqual(h.invoke('records.delete', record_id='X0000')['remaining'], rt.MAX_RECORDS - 1)
        op = h.invoke('recon.start', **RECON_FAST)['operation_id']
        rejected(h, 'records.delete', 'RECORD_IN_USE', record_id='PJ0001')
        rejected(h, 'records.delete', 'UNKNOWN_RECORD', record_id='X0000')
        wait(h, op)
        h.invoke('records.delete', record_id='PJ0001')
        self.assertNotIn('PJ0001', h.invoke('records.list')['records'])

    def test_F25_porosity_belongs_to_the_agent_that_ran_it(self):
        h = _scenario('reconstructed')
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        r = s1.invoke(instance_id=h.instance_id, capability_id='data.porosity', arguments=dict(record_id='VO0001', region=None, threshold_1_m=None, min_pore_voxels=2),
                      request_id='p1')
        self.assertEqual(r['status'], 'succeeded')
        self.assertIsNotNone(s1.read(instance_id=h.instance_id)['observations']['result.last_porosity']['value'])
        self.assertIsNone(s2.read(instance_id=h.instance_id)['observations']['result.last_porosity']['value'])

    def test_F26_F27_units_are_si_and_no_inert_gain_pattern(self):
        si = {'1', 'm', 's', 'V', 'A', 'W', 'J', 'K', 'kg', 'rad', 'Gy', '1/m', 'm2', 'rad/s', 'm/s', 'V/s', 'A/s', '1/s', '1/V', '1/J', 'kg/m3'}
        bad = []

        def walk(x, path=''):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k in ('x-unit', 'unit') and isinstance(v, str) and v not in si:
                        bad.append((path + '.' + k, v))
                    walk(v, path + '.' + k)
            elif isinstance(x, list):
                for i, v in enumerate(x):
                    walk(v, f'{path}[{i}]')
        walk(_meta())
        self.assertEqual(bad, [])
        self.assertNotIn('gain', configured().truth)



def _ordered_reference(h, pose, n=120, nu=None, nv=None, chunk=6000):
    """Independent reference for the ledger's sample-body absorption: a dense grid (nu x nv, default n x n) over the projection box of the
    body's rotation envelope at all angles (its own box, not the runtime's), every object's entry / exit parameters along each ray (stage
    housing, chuck, pin, container near and far wall, body), sorted by entry, sequential attenuation; each object absorbs the mu_en / mu share
    of what it removes from the beam reaching it (J per electron)."""
    key = h._rate_key(pose)
    kv, fpos = key[0] * rt.RATE_KV_CELL_V, key[1]
    sod, cz, rot = key[4] * rt.RATE_POS_CELL_M, key[5] * rt.RATE_POS_CELL_M, key[6] * rt.RATE_ROT_CELL_RAD
    s = h.samples[h.on_stage]
    e, _ = rt.unabsorbed_spectrum(kv)
    _, ph = rt.beam_spectrum(kv, fpos)
    c = rt.SDD_M * math.tan(rt.CONE_HALF_ANGLE_RAD)
    renv = rt.sample_radius_total(s) + math.hypot(s['offset_x_m'], s['offset_y_m'])
    uc, uh = rt.SDD_M * h.truth['axis_offset'] / sod, rt.SDD_M * renv / math.sqrt(sod * sod - renv * renv) * 1.02 + 2 * rt.PIXEL_PITCH_M
    z0 = cz - rt.SOURCE_XYZ_M[2] + (s['pin']['length_m'] if s['pin'] else 0.0)
    vv = [rt.SDD_M * z / (sod + sg * renv) for z in (z0, z0 + s['height_m']) for sg in (-1, 1)]
    pad = 0.02 * (max(vv) - min(vv)) + 2 * rt.PIXEL_PITCH_M
    u0, u1, v0, v1 = max(uc - uh, -c), min(uc + uh, c), max(min(vv) - pad, -c), min(max(vv) + pad, c)
    nu, nv = nu or n, nv or n
    pu, pv = (u1 - u0) / nu, (v1 - v0) / nv
    U, V = np.meshgrid(u0 + (np.arange(nu) + 0.5) * pu, v0 + (np.arange(nv) + 0.5) * pv)
    U, V = U.ravel(), V.ravel()
    mu_air = rt.linear_mu('nist_air', e)[0]
    coef = {}

    def mu(m):
        if m not in coef:
            coef[m] = rt.linear_mu(m, e)
        return coef[m]

    def inter(t1, t2, lo, hi):
        return np.maximum(t1, lo), np.minimum(t2, hi)
    body = 0.0
    for k0 in range(0, len(U), chunk):
        P = np.stack([np.full(len(U[k0:k0 + chunk]), rt.SDD_M), U[k0:k0 + chunk], V[k0:k0 + chunk]], -1)
        r = np.linalg.norm(P, axis=1)
        D, om = P / r[:, None], pu * pv * rt.SDD_M / r ** 3
        S, Ds, ax, ay, zt = h._frame(s, D, sod, cz, rot, h.truth['axis_offset'])
        segs = []                                                     # (entry, exit, {material: path} or material, is body)
        for sid, rad, za, zb in (('cu', rt.CHUCK_R_M, zt - rt.CHUCK_H_M, zt), ('al', rt.STAGE_HOUSING_R_M, zt - rt.CHUCK_H_M - rt.STAGE_H_M, zt - rt.CHUCK_H_M)):
            segs.append(inter(*rt._cyl(S, Ds, rad, ax, ay), *rt._slab(S, Ds, za, zb, 2)) + (sid, False))
        if s['pin']:
            segs.append(inter(*rt._cyl(S, Ds, s['pin']['diameter_m'] / 2), *rt._slab(S, Ds, -s['pin']['length_m'], 0.0, 2)) + (s['pin']['substance_id'], False))
        R = s['diameter_m'] / 2
        zl, zh = rt._slab(S, Ds, 0.0, s['height_m'], 2)
        il, ih = inter(*rt._cyl(S, Ds, R), zl, zh)
        segs.append((il, ih, rt.body_paths(s, S, Ds), True))
        if s['container']:
            ol, oh = inter(*rt._cyl(S, Ds, R + s['container']['wall_m']), zl, zh)
            has = ih > il
            segs.append((ol, np.where(has, il, oh), s['container']['substance_id'], False))
            segs.append((np.where(has, ih, oh), oh, s['container']['substance_id'], False))
        MT, ME, entry = [], [], []
        for lo, hi, mat, _ in segs:
            with np.errstate(invalid='ignore'):
                L = np.clip(hi - lo, 0.0, None)
            L = np.where(np.isfinite(L), L, 0.0)
            paths = mat if isinstance(mat, dict) else {mat: L}
            MT.append(sum((np.outer(v, mu(m)[0]) for m, v in paths.items()), np.zeros((len(om), len(e)))))
            ME.append(sum((np.outer(v, mu(m)[1]) for m, v in paths.items()), np.zeros((len(om), len(e)))))
            entry.append(np.where(L > 0, lo, np.inf))
        order = np.argsort(np.stack(entry, 1), 1)
        MT, ME = np.stack(MT), np.stack(ME)
        isbody = np.array([x[3] for x in segs])
        cur = ph[None, :] * np.exp(-np.outer(r, mu_air))
        rows = np.arange(len(om))
        for p_ in range(len(segs)):
            k = order[:, p_]
            mt, me = MT[k, rows], ME[k, rows]
            Tk = np.exp(-mt)
            A = np.divide(cur * (1 - Tk) * me, mt, out=np.zeros_like(cur), where=mt > 0) * e[None, :]
            sel = isbody[k]
            body += float((om[sel] @ A[sel]).sum()) * 0.5 * rt.KEV
            cur = cur * Tk
    return body


class RuntimeReviewRound2Regressions(unittest.TestCase):
    """Regression tests for the findings of the second independent runtime review (F28-F32)."""

    def test_F28_objects_absorb_in_their_order_along_the_ray(self):
        """The ledger follows each ray through the stage hardware, pin, container walls and body in their order (independent sorted-interval
        reference); the body's dose does not depend on what lies behind it."""
        disc = dict(DENSE, sample_id='disc', pin=None, diameter_m=0.060, height_m=0.0005,
                    layers=[dict(thickness_m=0.0005, materials=[dict(substance_id='alumina', volume_fraction=1.0)])])
        cases = [(dict(DENSE, sample_id='on_chuck', pin=None), 0.045, 0.0), (dict(DENSE, sample_id='low_on_chuck', pin=None), 0.045, -0.002),
                 (dict(DENSE, sample_id='pb_tube', container=dict(substance_id='pb', wall_m=0.3e-3)), 0.045, 0.0), (disc, 0.200, 0.0)]
        rates = {}
        for s, sod, dz in cases:
            h = loaded(s)
            pose = dict(xv=60e3, rot=0.0, sod=sod, cz=centred_cz(s) + dz, slide=h.slide)
            b, _ = h._base_rates(pose)
            rates[s['sample_id']] = b['sample_absorbed_j']
            self.assertAlmostEqual(b['sample_absorbed_j'] / _ordered_reference(h, pose), 1.0, delta=0.03, msg=s['sample_id'])
            self.assertLess(abs(b['xray_emitted_j'] - sum(b[k] for k in rt.ENERGY_SINKS)), 1e-12 * b['xray_emitted_j'])
            self.assertTrue(all(b[k] >= 0.0 for k in rt.ENERGY_SINKS))
        h = loaded(DENSE)
        b, _ = h._base_rates(dict(xv=60e3, rot=0.0, sod=0.045, cz=centred_cz(DENSE), slide=h.slide))
        self.assertAlmostEqual(rates['on_chuck'] / b['sample_absorbed_j'], 1.0, delta=0.005)      # pin or chuck behind the body: same dose

    def test_F29_thin_tall_samples_get_enough_ledger_rays(self):
        """Thin tall samples on and off the rotation axis: the fine ledger rays cover the body's projection at the cell's angle with at least
        16 rays across (a box over every angle left an off-axis fibre between the rays in some cells)."""
        needle = dict(DENSE, sample_id='needle', diameter_m=0.0005, height_m=0.100,
                      layers=[dict(thickness_m=0.100, materials=[dict(substance_id='alumina', volume_fraction=1.0)])])
        h = loaded(needle)
        pose = dict(xv=60e3, rot=0.0, sod=0.300, cz=centred_cz(needle), slide=h.slide)
        b, _ = h._base_rates(pose)
        self.assertAlmostEqual(b['sample_absorbed_j'] / _ordered_reference(h, pose, 160), 1.0, delta=0.04)
        fibre = dict(DENSE, sample_id='fibre', diameter_m=0.0005, height_m=0.050,
                     layers=[dict(thickness_m=0.050, materials=[dict(substance_id='alumina', volume_fraction=1.0)])])
        h = ready()
        h.invoke('sample.provide', **fibre)
        h.invoke('door.open')
        settle(h)
        h.invoke('sample.place', sample_id='fibre', offset_x_m=0.005, offset_y_m=0.0)          # 5 mm off the rotation axis
        ratios = []
        for k in range(0, rt.RATE_ROT_CELLS, 4):
            pose = dict(xv=60e3, rot=k * rt.RATE_ROT_CELL_RAD, sod=0.300, cz=centred_cz(fibre), slide=h.slide)
            b, _ = h._base_rates(pose)
            ratios.append(b['sample_absorbed_j'] / _ordered_reference(h, pose, nu=2400, nv=40))
        self.assertLess(max(abs(x - 1.0) for x in ratios), 0.06, ratios)

    def test_F30_moves_along_a_keep_out_boundary_are_accepted(self):
        """The carriage reaches the chuck's source-side limit first and the lift then runs along it: the corner of the swept path lies exactly
        on the carriage target, so rounding cannot put it inside the keep-out."""
        h = loaded(DENSE)
        xb = rt.FILTER_CLEAR_M + rt.COLLISION_MARGIN_M + rt.CHUCK_R_M
        self.assertTrue(h._geometry_ok(xb, 0.355))
        refused = []
        for k in range(400):
            h.sod, h.cz = 0.0285 + k * 0.0375 / 400, 0.335
            if not h._path_ok(xb, 0.355):
                refused.append(h.sod)
        self.assertEqual(refused, [])
        g = loaded(DENSE)
        g.invoke('stage.move', sod_m=0.0413, chuck_z_m=0.335)
        settle(g)
        g.invoke('stage.move', sod_m=xb, chuck_z_m=0.355)
        settle(g)
        self.assertEqual((g.sod, g.cz), (xb, 0.355))
        rejected(g, 'stage.move', 'COLLISION_RISK', sod_m=xb - 1e-4, chuck_z_m=0.355)

    def test_F31_agents_delete_only_their_own_and_the_hosts_records(self):
        h = _scenario('scanned')
        h.register_principal('a1', 'agent')
        h.register_principal('ev', 'evaluator')
        a1, ev = h.session('a1'), h.session('ev')
        op = ev.invoke(instance_id=h.instance_id, capability_id='xray.radiograph', arguments=dict(roi=None), request_id='r1')['operation_id']
        self.assertEqual(wait(h, op), 'completed')
        rid = h.operations[op]['record_id']
        r = a1.invoke(instance_id=h.instance_id, capability_id='records.delete', arguments=dict(record_id=rid), request_id='d1')
        self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'))
        self.assertIn(rid, h.records)
        op2 = a1.invoke(instance_id=h.instance_id, capability_id='xray.radiograph', arguments=dict(roi=None), request_id='r2')['operation_id']
        self.assertEqual(wait(h, op2), 'completed')
        own = h.operations[op2]['record_id']
        for i, x in enumerate((own, 'FF0001')):                         # its own record and a host record
            r = a1.invoke(instance_id=h.instance_id, capability_id='records.delete', arguments=dict(record_id=x), request_id=f'd{i + 2}')
            self.assertEqual(r['status'], 'succeeded', r['error'])
        r = ev.invoke(instance_id=h.instance_id, capability_id='records.delete', arguments=dict(record_id=rid), request_id='d9')
        self.assertEqual(r['status'], 'succeeded')

    def test_F32_deleting_the_analysed_volume_clears_the_porosity_result(self):
        h = _scenario('analysed')
        self.assertIsNotNone(h.read()['result.last_porosity'])
        h.invoke('records.delete', record_id='VO0001')
        self.assertIsNone(h.read()['result.last_porosity'])
        self.assertIn('result.last_porosity', rt.EFFECTS['records.delete'])
        self.assertIn('analysed', SCENARIOS)
        self.assertIn(dict(record_id='VO0001'), _variants('records.delete'))

# ---------------------------------------------------------------- declared effects (scenario states)
def _into(h, phase, frac=0.5):
    """Advance event-exactly to the next programme item of the given phase and then to `frac` of its duration."""
    for _ in range(200):
        o = h.op
        assert o is not None, phase
        end = o['ends'][o['i']]
        if o['phase'] == phase:
            h.step(max(0.0, (end - h.t) * frac))
            return h
        h.step(max(0.0, end - h.t) + 1e-9)
    raise AssertionError(phase)


def _build(name):
    if name == 'fresh':
        return rt.BrukerX4PoseidonRuntime()
    if name == 'off':
        h = configured()
        for s in (PELLET, CAL, EDGE):
            h.invoke('sample.provide', **s)
        return h
    if name == 'booting':
        h = _scenario('off')
        h.invoke('power.set', enabled=True)
        h.step(5.0)
        return h
    if name == 'ready':
        h = _scenario('booting')
        h.step(rt.BOOT_S)
        return h
    if name == 'door_opening':
        h = _scenario('ready')
        h.invoke('door.open')
        h.step(1.0)
        return h
    if name == 'door_open':
        h = _scenario('ready')
        h.invoke('door.open')
        return settle(h)
    if name == 'loaded_open':
        h = _scenario('door_open')
        h.invoke('sample.place', sample_id='alumina_pellet', offset_x_m=0.0, offset_y_m=0.0)
        return h
    if name == 'door_closing':
        h = _scenario('loaded_open')
        h.invoke('door.close')
        h.step(1.0)
        return h
    if name == 'loaded':
        h = _scenario('loaded_open')
        h.invoke('door.close')
        settle(h)
        h.invoke('scan.configure', **FAST)
        return h
    if name == 'warming':
        h = _scenario('loaded')
        h.invoke('xray.enable', enabled=True)
        h.step(20.0)
        return h
    if name == 'beam':
        h = _scenario('warming')
        h.step(rt.WARMUP_S)
        assert h._xray_ready()
        return h
    if name == 'ramping':
        h = _scenario('beam')
        h.invoke('xray.set', voltage_v=70e3, current_a=100e-6)
        h.step(0.3)
        return h
    if name == 'flat_running':
        h = _scenario('beam')
        h.invoke('calibration.flat_field')
        h.step(0.1)
        return h
    if name == 'flat_bright':
        return _into(_scenario('flat_running'), 'bright')
    if name == 'flat_done':
        h = _scenario('flat_running')
        return settle(h)
    if name == 'positioning':
        h = _scenario('flat_done')
        h.invoke('stage.move', sod_m=SOD_SCAN, chuck_z_m=centred_cz(PELLET))
        h.step(1.0)
        return h
    if name == 'positioned':
        h = _scenario('positioning')
        return settle(h)
    if name == 'rotating':
        h = _scenario('positioned')
        h.invoke('stage.rotate', angle_rad=math.pi / 2)
        h.step(0.5)
        return h
    if name == 'filter_moving':
        h = _scenario('positioned')
        h.invoke('filter.select', position=3)
        h.step(0.2)
        return h
    if name == 'radiographing':
        h = _scenario('positioned')
        h.invoke('xray.radiograph', roi=None)
        h.step(0.01)
        return h
    if name == 'radiographed':
        return settle(_scenario('radiographing'))
    if name == 'scanning':
        h = _scenario('positioned')
        h.invoke('scan.start')
        h.step(5.0)
        return h
    if name == 'scanned':
        return settle(_scenario('scanning'))
    if name == 'reconstructing':
        h = _scenario('scanned')
        h.invoke('recon.start', **RECON_FAST)
        h.step(1.0)
        return h
    if name == 'reconstructed':
        h = _scenario('reconstructing')
        while h.recon is not None:
            h.step(0.5)
        return h
    if name == 'analysed':
        h = _scenario('reconstructed')
        h.invoke('data.porosity', record_id='VO0001', region=None, threshold_1_m=None, min_pore_voxels=2)
        return h
    if name == 'cal_positioned':
        h = _scenario('door_open')
        h.invoke('sample.place', sample_id='cal_phantom', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('door.close')
        settle(h)
        h.invoke('scan.configure', **FAST)
        beam(h)
        flat(h)
        h.invoke('stage.move', sod_m=0.050, chuck_z_m=centred_cz(CAL))
        return settle(h)
    if name == 'geo_calibrating':
        h = _scenario('cal_positioned')
        h.invoke('calibration.geometry')
        h.step(2.0)
        return h
    if name == 'estopped':
        h = _scenario('positioned')
        h.invoke('emergency.stop')
        return h
    if name == 'estop_released':
        h = _scenario('estopped')
        h.invoke('emergency.release')
        return h
    if name == 'arc_fault':
        h = _scenario('beam')
        h.invoke('fault.inject', fault='xray_arc', active=True)
        return h
    if name == 'door_sensor_fault':
        h = _scenario('beam')
        h.invoke('fault.inject', fault='door_sensor', active=True)
        return h
    if name == 'stall_fault':
        h = _scenario('scanning')
        h.invoke('fault.inject', fault='rotation_stall', active=True)
        return h
    if name == 'light_curtain':
        h = _scenario('door_closing')
        h.invoke('fault.inject', fault='light_curtain_blocked', active=True)
        return h
    if name == 'defects':
        h = _scenario('positioned')
        h.invoke('fault.inject', fault='detector_defects', active=True)
        return h
    if name == 'mains_fault':
        h = _scenario('positioned')
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0))
        return h
    if name == 'mains_restored':
        h = _scenario('mains_fault')
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE))
        return h
    if name == 'unpowered_loaded':
        h = _scenario('loaded')
        h.invoke('power.set', enabled=False)
        return h
    if name == 'door_just_opening':                                     # the door target moved, the door has not (stop paths change door.closed)
        h = _scenario('ready')
        h.invoke('door.open')
        return h
    if name == 'beam_just_enabled':                                     # the tube is enabled but has not emitted yet
        h = _scenario('flat_done')
        h.invoke('xray.enable', enabled=False)
        h.invoke('xray.enable', enabled=True)
        return h
    if name == 'estopped_unpowered':                                    # safety button pressed while switched off, then booted
        h = _scenario('off')
        h.invoke('emergency.stop')
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 1.0)
        return h
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'off', 'booting', 'ready', 'door_opening', 'door_open', 'loaded_open', 'door_closing', 'loaded', 'warming', 'beam', 'ramping',
             'flat_running', 'flat_bright', 'flat_done', 'positioning', 'positioned', 'rotating', 'filter_moving', 'radiographing', 'radiographed',
             'scanning', 'scanned', 'reconstructing', 'reconstructed', 'cal_positioned', 'geo_calibrating', 'estopped', 'estop_released', 'arc_fault',
             'door_sensor_fault', 'stall_fault', 'light_curtain', 'defects', 'mains_fault', 'mains_restored', 'unpowered_loaded', 'door_just_opening',
             'beam_just_enabled', 'estopped_unpowered', 'analysed']
LONG_SCENARIOS = ['loaded', 'warming', 'positioned', 'scanning', 'reconstructed', 'cal_positioned', 'unpowered_loaded', 'beam_just_enabled']
STEP_DTS = (0.4, 1.0, 3.0, 15.0, 120.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0), dict(rt.PLANT_EXAMPLE, room_k=300.15)],
        'sample.provide': [dict(rt.SAMPLE_EXAMPLE, sample_id='new')],
        'sample.place': [dict(sample_id='alumina_pellet', offset_x_m=0.0, offset_y_m=0.0), dict(sample_id='cal_phantom', offset_x_m=0.001, offset_y_m=0.0)],
        'sample.remove': [dict(sample_id='alumina_pellet'), dict(sample_id='cal_phantom')],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_button': [{}],
        'emergency.stop': [{}], 'emergency.release': [{}], 'system.acknowledge': [{}],
        'fault.inject': [dict(fault=f, active=True) for f in rt.FAULTS] + [dict(fault=f, active=False) for f in rt.FAULTS],
        'door.open': [{}], 'door.close': [{}],
        'xray.set': [dict(voltage_v=70e3, current_a=100e-6), dict(voltage_v=40e3, current_a=200e-6)],
        'xray.enable': [dict(enabled=True), dict(enabled=False)],
        'filter.select': [dict(position=3), dict(position=0)],
        'stage.move': [dict(sod_m=SOD_SCAN, chuck_z_m=centred_cz(PELLET)), dict(sod_m=0.250, chuck_z_m=0.250), dict(sod_m=0.020, chuck_z_m=0.387)],
        'stage.rotate': [dict(angle_rad=math.pi / 2), dict(angle_rad=0.0)],
        'stage.go_to': [dict(position='load'), dict(position='center')],
        'scan.configure': [dict(FAST), dict(FAST, range_deg='360', random_movement=True), dict(FAST, binning='2x2', exposure_s=0.05)],
        'calibration.flat_field': [{}], 'calibration.geometry': [{}], 'scan.start': [{}],
        'xray.radiograph': [dict(roi=None), dict(roi=dict(center_u_m=0.0, center_v_m=0.0, size_px=32))],
        'recon.start': [dict(RECON_FAST), dict(RECON_FAST, auto_alignment=True, post_alignment=None, beam_hardening=0.5, bh_material='alumina',
                                                ring_correction=0.5, voi=dict(center_m=[0.0, 0.0, 0.0], size_m=[0.007, 0.007, 0.007]))],
        'data.porosity': [dict(record_id='VO0001', region=None, threshold_1_m=None, min_pore_voxels=2)],
        'data.cracks': [dict(record_id='VO0001', region=None, threshold_1_m=None, min_voxels=20)],
        'data.attenuation': [dict(record_id='VO0001', region=None)],
        'data.slice': [dict(record_id='VO0001', axis='z', position_m=0.0, size_px=32)],
        'data.edge_resolution': [dict(record_id='RG0001')],
        'records.delete': [dict(record_id='FF0001'), dict(record_id='PJ0001'), dict(record_id='RG0001'), dict(record_id='VO0001')],
        'sim.step': [dict(dt_s=0.5), dict(dt_s=30.0)],
    }
    return V.get(cap, [])


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _flat(v, prefix=''):
    out = {}
    if isinstance(v, dict):
        for k, x in v.items():
            out.update(_flat(x, f'{prefix}.{k}'))
    elif isinstance(v, list) and len(v) <= 64:
        for i, x in enumerate(v):
            out.update(_flat(x, f'{prefix}[{i}]'))
    else:
        out[prefix] = json.dumps(v, sort_keys=True, default=str) if isinstance(v, list) else v
    return out


def _rel_changed(a, b, scale=None):
    if _num(a) and _num(b):
        return abs(a - b) > 1e-9 * (max(abs(a), abs(b)) if scale is None else scale) + 1e-300
    return _changed(a, b)


def _leaf_changed(a, b):
    fa, fb = _flat(a), _flat(b)
    return any(_rel_changed(fa.get(k, '<absent>'), fb.get(k, '<absent>')) for k in set(fa) | set(fb))


def _step_differs(v1, a2, v0, b2):
    """Does the step change the value differently with the call (v1 -> a2) than without it (v0 -> b2)?  (leaf-wise, numeric increments)"""
    f1, fa, f0, fb = _flat(v1), _flat(a2), _flat(v0), _flat(b2)
    for leaf in set(f1) | set(fa) | set(f0) | set(fb):
        x1, xa, x0, xb = (f.get(leaf, '<absent>') for f in (f1, fa, f0, fb))
        ca, cb = _rel_changed(x1, xa), _rel_changed(x0, xb)
        if not (ca or cb):
            continue
        if ca and cb and all(_num(x) for x in (x1, xa, x0, xb)) and not _rel_changed(xa - x1, xb - x0, max(abs(x1), abs(xa), abs(x0), abs(xb))):
            continue
        if ca and cb and (x1, xa) == (x0, xb):
            continue
        if ca and cb and x1 == x0 and not _rel_changed(xa, xb):
            continue
        return True
    return False


def _executable(cap):
    return cap not in rt.UNSUPPORTED and rt.CAPS[cap]['kind'] != 'query'


class DeclaredEffects(unittest.TestCase):
    def test_immediate_and_on_step_effects_are_declared(self):
        """For every capability accepted in any scenario state: the slots it changes at once are declared effects, the slots whose change during the
        following step differs from an identical run without the call are declared on_step effects, and the step itself only changes slots declared
        as on_step effects of sim.step (the runtime noise is seeded by operation counters, so both runs draw identical noise)."""
        missing_imm, missing_step = {}, {}
        states = {n: _scenario(n) for n in SCENARIOS}
        for cap in rt.CAPS:
            if not _executable(cap):
                continue
            for sname, S in states.items():
                for args in _variants(cap):
                    A = copy.deepcopy(S)
                    v0 = A.read('evaluator')
                    try:
                        _orig_invoke(A, cap, **args)
                    except rt.ActionRejected:
                        continue
                    v1 = A.read('evaluator')
                    if cap == 'sim.step':
                        for k in v0:
                            if _leaf_changed(v0[k], v1[k]) and k not in rt.ON_STEP['sim.step'] + rt.EFFECTS['sim.step']:
                                missing_step.setdefault('sim.step', set()).add((sname, k))
                        continue
                    for k in v0:
                        if _changed(v0[k], v1[k]) and _leaf_changed(v0[k], v1[k]) and k not in rt.EFFECTS.get(cap, []):
                            missing_imm.setdefault(cap, set()).add((sname, k))
                    for dt in STEP_DTS:
                        A2, B2 = copy.deepcopy(A), copy.deepcopy(S)
                        A2.step(dt)
                        B2.step(dt)
                        a2, b2 = A2.read('evaluator'), B2.read('evaluator')
                        for k in v0:
                            if k not in rt.ON_STEP.get(cap, []) and _step_differs(v1[k], a2[k], v0[k], b2[k]):
                                missing_step.setdefault(cap, set()).add((sname, k))
                            if k not in rt.ON_STEP['sim.step'] + ['clock.time_s'] and _leaf_changed(v0[k], b2[k]):
                                missing_step.setdefault('sim.step', set()).add((sname, k))
        self.assertEqual(missing_imm, {})
        self.assertEqual(missing_step, {})
        self.assertFalse([s for v in rt.ON_STEP.values() for s in v if rt.SLOTS[s]['origin'] == 'setpoint'])

    def test_every_executable_capability_is_accepted_in_some_scenario(self):
        accepted = set()
        for n in SCENARIOS:
            S = _scenario(n)
            for cap in rt.CAPS:
                if not _executable(cap) or cap in accepted:
                    continue
                for args in _variants(cap):
                    try:
                        _orig_invoke(copy.deepcopy(S), cap, **args)
                        accepted.add(cap)
                        break
                    except rt.ActionRejected:
                        pass
        self.assertEqual({c for c in rt.CAPS if _executable(c)} - accepted, set())

    def test_long_window_effects_of_operations_are_declared(self):
        """Every accepted asynchronous call is followed until its operation has left 'running' (+ 5 s), every other call for 200 s (longer than
        the 180 s warm-up), comparing with the identical run without the call every 0.05 s for the first 10 s and every 1 s after."""
        missing = {}
        for sname in LONG_SCENARIOS:
            S = _scenario(sname)
            for cap in rt.CAPS:
                if not _executable(cap) or cap == 'sim.step':
                    continue
                for args in _variants(cap):
                    A = copy.deepcopy(S)
                    v0 = A.read('evaluator')
                    try:
                        res = _orig_invoke(A, cap, **args)
                    except rt.ActionRejected:
                        continue
                    oid = res.get('operation_id') if isinstance(res, dict) and cap in rt.ASYNC else None
                    v1 = A.read('evaluator')
                    B = copy.deepcopy(S)
                    t, t_end = 0.0, (3600.0 if oid else 200.0)
                    while t < t_end - 1e-9:
                        d = 0.05 if t < 10.0 - 1e-9 else 1.0
                        A.step(d)
                        B.step(d)
                        t += d
                        a2, b2 = A.read('evaluator'), B.read('evaluator')
                        for k in v0:
                            if k not in rt.ON_STEP.get(cap, []) and _step_differs(v1[k], a2[k], v0[k], b2[k]):
                                missing.setdefault(cap, set()).add((sname, k))
                        if oid and A.operations[oid]['phase'] != 'running' and t_end == 3600.0:
                            t_end = t + 5.0
        self.assertEqual(missing, {})


class ZZRejectionCodes(unittest.TestCase):
    def test_every_observed_rejection_code_is_declared(self):
        """Runs last (class name): every code raised by a device call anywhere in this test module is declared for that capability."""
        undeclared = {k: sorted(v - set(rt.CAP_ERRORS.get(k, [])) - {'INVALID_ARGUMENT', 'UNSUPPORTED', 'UNKNOWN_CAPABILITY'})
                      for k, v in REJECTIONS.items()}
        self.assertEqual({k: v for k, v in undeclared.items() if v}, {})


if __name__ == '__main__':
    unittest.main()
