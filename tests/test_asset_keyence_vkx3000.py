"""tests for CHEM-155 keyence_vkx3000: python3 -m unittest tests/test_asset_keyence_vkx3000.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances
are isolated, agent/evaluator truth isolation, conservation (laser energy: reflected + absorbed + transmitted = energy to the sample; travel and
frame ledgers), real return values validated against the declared schemas (asset_tool lint_schema / validate_value), asynchronous completion
and cancellation remnants, host-step independence, declared immediate and on_step effects (scenario states, short and long windows); plus the
instrument (boot and Z homing, motorized XY, manual lift without power, revolver timing and the empty optical axis while turning, lens-change
retraction, collision protection, faults) and the measurement physics: laser-confocal heights on opaque steps against the hidden Z scale,
transparent-step errors of the strongest / first peak, confocal film mode and its resolution limit, slope cut-off of the numerical aperture,
double scan on dark pixels, focus variation needing texture, white light interferometry with the phase change on reflection of Au against Si,
spectral film thickness (right and wrong materials, range), Z and XY calibration with step and pitch standards, stitching, ISO analyses.
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
from asset_runtime import keyence_vkx3000 as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}


def _meta():
    if not _META:
        _META.update(rt.export_keyence_vkx3000_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.KeyenceVkx3000Runtime.invoke


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


rt.KeyenceVkx3000Runtime.invoke = _instrumented_invoke

AU = dict(sample_id='au_step', substrate_substance_id='si', thickness_m=5.25e-4, size_x_m=0.010, size_y_m=0.010,
          features=[dict(kind='step', substance_id='au', height_m=200e-9, width_m=0.3e-6, pitch_m=0.0, x_m=0.0, y_m=0.0, extent_m=0.004)], film=None,
          roughness=dict(sq_m=0.5e-9, correlation_length_m=2e-6), tilt_x_rad=0.0, tilt_y_rad=0.0)
TALL = dict(AU, sample_id='tall', thickness_m=0.033, features=[])
PMMA3 = dict(sample_id='pmma_al', substrate_substance_id='al', thickness_m=0.001, size_x_m=0.010, size_y_m=0.010, features=[],
             film=dict(substance_id='pmma', thickness_m=3.0e-6), roughness=dict(sq_m=0.5e-9, correlation_length_m=5e-6), tilt_x_rad=0.0, tilt_y_rad=0.0)
STEEL = dict(sample_id='worn_steel', substrate_substance_id='stainless_steel', thickness_m=0.003, size_x_m=0.020, size_y_m=0.020,
             features=[dict(kind='groove', substance_id=None, height_m=-5e-6, width_m=200e-6, pitch_m=0.0, x_m=0.0, y_m=0.0, extent_m=0.010)], film=None,
             roughness=dict(sq_m=0.2e-6, correlation_length_m=10e-6), tilt_x_rad=0.0, tilt_y_rad=0.0)
STEP_STD = dict(sample_id='step_standard', substrate_substance_id='sio2_fused', thickness_m=0.00635, size_x_m=0.025, size_y_m=0.025,
                features=[dict(kind='step', substance_id='cr', height_m=1.803e-6, width_m=0.5e-6, pitch_m=0.0, x_m=0.0, y_m=0.0, extent_m=0.005)],
                film=dict(substance_id='cr', thickness_m=60e-9), roughness=dict(sq_m=0.5e-9, correlation_length_m=3e-6), tilt_x_rad=0.001, tilt_y_rad=0.0)
GRATING = dict(sample_id='grating', substrate_substance_id='si', thickness_m=5.25e-4, size_x_m=0.010, size_y_m=0.010,
               features=[dict(kind='grating', substance_id=None, height_m=0.5e-6, width_m=5e-6, pitch_m=10e-6, x_m=0.0, y_m=0.0, extent_m=0.004)], film=None,
               roughness=dict(sq_m=0.5e-9, correlation_length_m=3e-6), tilt_x_rad=0.0, tilt_y_rad=0.0)
FAST = dict(rt.SETTINGS_EXAMPLE, quality='high_speed')


def configured(seed=1, **plant):
    h = rt.KeyenceVkx3000Runtime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    return h


def ready(seed=1, **plant):
    h = configured(seed, **plant)
    h.invoke('power.set', enabled=True)
    h.step(rt.BOOT_S + 1.0)
    return h


def busy(h):
    return h._z_moving() or not h._revolver_settled() or h._stage_moving() or h.op is not None or h._lift_moving()


def settle(h, limit=900.0, dt=0.5):
    t = 0.0
    while busy(h) and t < limit:
        h.step(dt)
        t += dt
    assert not busy(h)
    return h


def focus_near(h, above=1.0e-4):
    s = h.samples[h.on_stage]
    hi = rt.sample_extremes(s)[0]
    h.invoke('focus.move', z_m=h._z_for_focus(rt.SAMPLE_PLATE_Z + h.lift + s['thickness_m'] + 0.5 * hi) - above)
    return settle(h)


def loaded(sample=AU, slot=1, seed=1, x=0.0, y=0.0):
    h = ready(seed)
    if sample['sample_id'] not in h.samples:
        h.invoke('sample.provide', **sample)
    h.invoke('sample.place', sample_id=sample['sample_id'], x_m=x, y_m=y)
    h.invoke('lens.select', slot=slot)
    settle(h)
    return focus_near(h)


def wait(h, op, tmax=3600.0, dt=0.5):
    t0 = h.t
    while h.operations[op]['phase'] == 'running' and h.t - t0 < tmax:
        h.step(dt)
    return h.operations[op]['phase']


def prepare(h, mode='laser_confocal', **settings):
    h.invoke('measurement.configure', **dict(rt.SETTINGS_EXAMPLE, mode=mode, **settings))
    op = h.invoke('focus.auto')['operation_id']
    assert wait(h, op) == 'completed', h.operations[op]
    op = h.invoke('measurement.auto_range')['operation_id']
    assert wait(h, op) == 'completed', h.operations[op]
    return h


def measure(h, mode='laser_confocal', autofocus=True, **settings):
    if autofocus:
        prepare(h, mode, **settings)
    op = h.invoke('measurement.start')['operation_id']
    assert wait(h, op) == 'completed', h.operations[op]
    rid = h.operations[op]['record_id']
    return rid, h.records[rid]


def hmap(r, key='height_m'):
    return rt._none_to_nan(r[key])


def step_height(h, rid, lower, upper, axis='x'):
    return h.invoke('data.step_height', record_id=rid, axis=axis, lower=lower, upper=upper, leveling='none')['step_height_m']


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


def pcor_offset(a, b, lam=rt.WLI_LAMBDA0_M):
    """Apparent height offset of material a against b in phase-shifting interferometry (phase change on reflection at normal incidence)."""
    pa = np.angle(-(1 - rt.nk(a, lam)) / (1 + rt.nk(a, lam)))
    pb = np.angle(-(1 - rt.nk(b, lam)) / (1 + rt.nk(b, lam)))
    return float(-(pa - pb) * lam / (4 * math.pi))


class ContractAndSchemas(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.keyence_vkx3000')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-155')

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
        v = ready().read('evaluator')
        for k, x in v.items():
            self.assertEqual(asset_tool.validate_value(x, m['state_slots'][k]['value_schema']), [], k)

    def test_metadata_references_are_consistent(self):
        m = _meta()
        slots, comps = set(m['state_slots']), set(rt.COMPONENTS) if hasattr(rt, 'COMPONENTS') else None
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
            if comps is not None:
                for x in c['component_ids']:
                    self.assertIn(x, comps, (k, x))
        for k in rt.CAPS:
            self.assertIn(k, rt.CAP_SOURCES, k)
        self.assertEqual(set(rt.PRECONDITIONS) - set(rt.CAPS), set())
        for k, v in rt.EFFECTS.items():
            self.assertIn(k, rt.CAPS)
            self.assertEqual(set(v) - slots, set(), k)
        for k, v in rt.ON_STEP.items():
            self.assertIn(k, rt.CAPS)
            self.assertEqual(set(v) - slots, set(), k)

    def test_unsupported_capabilities_report_unsupported(self):
        h = ready()
        for k in rt.UNSUPPORTED:
            with self.assertRaises(rt.ActionRejected) as cm:
                _orig_invoke(h, k, **(rt.CAPS[k].get('example_arguments') or {}))
            self.assertEqual(str(cm.exception), 'UNSUPPORTED')
            self.assertEqual(_meta()['capabilities'][k]['availability'], 'unsupported')
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r = s.invoke(instance_id=h.instance_id, capability_id='export.vk_file', arguments=rt.CAPS['export.vk_file'].get('example_arguments') or {}, request_id='u1')
        self.assertEqual(r['status'], 'unsupported')


class HostClockAndPurity(unittest.TestCase):
    def test_read_and_describe_do_not_advance_time(self):
        h = loaded()
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        t, rev = h.t, h.revision
        for _ in range(3):
            h.read('evaluator'); s.read(instance_id=h.instance_id); s.describe(instance_id=h.instance_id)
        self.assertEqual((h.t, h.revision), (t, rev))

    def test_step_zero_and_negative(self):
        h = ready()
        t = h.t
        h.step(0.0)
        self.assertEqual(h.t, t)
        for bad in (-1.0, float('nan'), float('inf')):
            with self.assertRaises(rt.ActionRejected):
                h.step(bad)

    def test_host_step_chopping_does_not_change_the_result(self):
        """The same measurement advanced in one host step and in irregular small steps gives identical records, positions and ledgers."""
        def run(chunks):
            h = loaded(AU, slot=3)
            prepare(h, **{k: v for k, v in FAST.items() if k != 'mode'})
            op = h.invoke('measurement.start')['operation_id']
            T = h.op['duration_s'] + 0.5
            if chunks:
                t, i = 0.0, 0
                while t < T:
                    d = (0.037, 0.211, 0.0043, 0.5)[i % 4]
                    h.step(d); t += d; i += 1
            else:
                h.step(T)
            self.assertEqual(h.operations[op]['phase'], 'completed')
            r = h.records[h.operations[op]['record_id']]
            return h, r
        a, ra = run(False)
        b, rb = run(True)
        self.assertEqual(ra['height_m'], rb['height_m'])
        self.assertEqual(ra['intensity'], rb['intensity'])
        self.assertAlmostEqual(a.z, b.z, places=12)
        for k, v in a.ledger().items():
            self.assertAlmostEqual(v, b.ledger()[k], delta=1e-9 * max(1.0, abs(v)), msg=k)

    def test_reads_do_not_change_later_results(self):
        def run(reads):
            h = loaded(AU, slot=1)
            prepare(h, **{k: v for k, v in FAST.items() if k != 'mode'})
            op = h.invoke('measurement.start')['operation_id']
            for _ in range(20):
                if reads:
                    h.read('evaluator'); h.invoke('events.read', since_s=0.0); h.invoke('records.list'); h.invoke('ledger.read')
                h.step(0.25)
            wait(h, op)
            return h.records[h.operations[op]['record_id']]['height_m']
        self.assertEqual(run(False), run(True))

    def test_set_points_only_write_targets(self):
        h = loaded(AU, slot=1)
        x0 = h.stage['x']
        out = h.invoke('stage.move', x_m=0.004, y_m=-0.002)
        self.assertEqual(h.stage['x'], x0)
        self.assertEqual(h.read()['stage.target_m'], [0.004, -0.002])
        self.assertAlmostEqual(out['duration_s'], 0.004 / rt.STAGE_SPEED_M_S)
        h.step(0.2)
        self.assertAlmostEqual(h.stage['x'], 0.002, places=12)
        self.assertAlmostEqual(h.stage['y'], -0.002, places=12)
        self.assertTrue(h.read()['stage.moving'])
        settle(h)
        z0 = h.z
        h.invoke('focus.move', z_m=z0 - 0.001)
        self.assertEqual(h.z, z0)
        h.step(0.1)
        self.assertAlmostEqual(h.z, z0 - 0.0005, places=12)
        settle(h)
        l0 = h.lift
        h.invoke('stage.set_lift', lift_m=-0.004)
        self.assertEqual(h.lift, l0)
        h.step(1.0)
        self.assertAlmostEqual(h.lift, -0.002, places=12)
        self.assertAlmostEqual(h.read()['stage.focus_drive_rad'], 0.002 / rt.COARSE_PITCH_M * 2 * math.pi, places=9)
        settle(h)
        h.invoke('lens.select', slot=2)
        self.assertEqual(h.read()['head.objective'], '10x')                  # the revolver has not turned yet
        self.assertTrue(h.read()['head.revolver_moving'])
        self.assertEqual(h.read()['head.lens_slot'], 2)
        h.step(0.4)
        self.assertIsNone(h.read()['head.objective'])                       # the optical axis is between two objectives
        h.step(0.41)
        self.assertEqual(h.read()['head.objective'], '20x')


class PowerMotionAndProtection(unittest.TestCase):
    def test_boot_homes_the_z_stage_and_power_loss_stops_the_axes(self):
        h = configured()
        h.invoke('power.set', enabled=True)
        self.assertEqual(h.state, 'booting')
        rejected(h, 'stage.move', 'NOT_READY', x_m=0.001, y_m=0.0)
        h.step(rt.BOOT_S - 0.01)
        self.assertEqual(h.state, 'booting')
        h.step(0.02)
        self.assertEqual(h.state, 'ready')
        h.invoke('stage.move', x_m=0.02, y_m=0.0)
        h.step(1.0)
        h.invoke('power.set', enabled=False)
        x = h.stage['x']
        h.step(5.0)
        self.assertAlmostEqual(x, 0.010, places=12)
        self.assertEqual(h.stage['x'], x)
        self.assertEqual(h.read()['stage.x_indicated_m'], None)
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 0.1)
        self.assertEqual(h.stage['x'], x)                                  # the XY stage keeps its position; only Z homes
        self.assertEqual(h.z, 0.0)

    def test_manual_lift_works_without_power_and_mains_fault(self):
        h = configured()
        h.invoke('stage.set_lift', lift_m=-0.010)
        h.step(10.0)
        self.assertAlmostEqual(h.lift, -0.010, places=12)
        self.assertAlmostEqual(h.ledger()['lift_travel_m'], 0.010, places=12)
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S + 1)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0))
        self.assertFalse(h.power)
        self.assertEqual(h.error, 'MAINS_FAULT')
        rejected(h, 'power.set', 'MAINS_OUT_OF_RANGE', enabled=True)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE))
        h.invoke('power.set', enabled=True)
        self.assertIsNone(h.error)
        rejected(h, 'plant.configure', 'SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=5))

    def test_revolver_timing_and_lens_change_retraction(self):
        h = loaded(AU, slot=1)
        z10 = h.z
        out = h.invoke('lens.select', slot=3)                               # 50x: 0.35 mm working distance -> Z retracts first
        self.assertLess(out['z_retract_m'], 0.0)
        tip_after = h._tip_z(rt.LENSES[3], z10 + out['z_retract_m'])
        self.assertAlmostEqual(tip_after, h._sample_top_z() + rt.SAFE_TIP_CLEARANCE_M, places=9)
        t_retract = abs(out['z_retract_m']) / rt.HEAD_Z_SPEED_M_S
        self.assertAlmostEqual(out['duration_s'], t_retract + 2 * rt.REVOLVER_S_PER_STEP, places=9)
        h.step(t_retract * 0.5)
        self.assertAlmostEqual(h.rev, 2 * math.pi / 6 * 1, places=12)       # the revolver waits for the retraction
        settle(h)
        self.assertEqual(h.read()['head.objective'], '50x')
        self.assertAlmostEqual(h.ledger()['revolver_steps'], 3.0, places=9)   # 0 -> 1 (loading) and 1 -> 3

    def test_collision_protection(self):
        h = loaded(AU, slot=3)
        top = h._sample_top_z()
        z_hit = h._z_for_focus(top + rt.COLLISION_MARGIN_M * 0.5 - rt.LENSES[3]['wd_m'])
        rejected(h, 'focus.move', 'COLLISION_RISK', z_m=z_hit)
        z_ok = h._z_for_focus(top + rt.COLLISION_MARGIN_M * 2 - rt.LENSES[3]['wd_m'])
        h.invoke('focus.move', z_m=z_ok)
        settle(h)
        h.invoke('stage.set_lift', lift_m=-0.001)
        settle(h)
        h.invoke('focus.move', z_m=h._z_for_focus(h._sample_top_z() + 0.3e-3 - rt.LENSES[3]['wd_m']))
        settle(h)
        rejected(h, 'stage.set_lift', 'COLLISION_RISK', lift_m=-0.0005)       # raising 0.5 mm would push the sample into the 50x tip
        h.invoke('stage.set_lift', lift_m=-0.0009)
        g = ready()
        g.invoke('sample.provide', **TALL)
        g.invoke('lens.select', slot=3)
        settle(g)
        g.invoke('focus.move', z_m=0.0)
        settle(g)
        rejected(g, 'sample.place', 'SAMPLE_TOO_TALL', sample_id='tall', x_m=0.0, y_m=0.0)
        g.invoke('stage.set_lift', lift_m=-0.010)
        settle(g)
        g.invoke('sample.place', sample_id='tall', x_m=0.0, y_m=0.0)
        rejected(g, 'sample.place', 'SAMPLE_NOT_ON_BENCH', sample_id='tall', x_m=0.0, y_m=0.0)

    def test_sample_and_argument_validation(self):
        h = configured()
        rejected(h, 'sample.provide', 'UNKNOWN_SUBSTANCE', **dict(AU, sample_id='x', substrate_substance_id='unobtainium'))
        rejected(h, 'sample.provide', 'INVALID_FEATURE', **dict(AU, sample_id='x', features=[dict(GRATING['features'][0], pitch_m=4e-6)]))
        rejected(h, 'sample.provide', 'INVALID_FEATURE', **dict(AU, sample_id='x', features=[dict(kind='cap', substance_id=None, height_m=80e-6, width_m=100e-6,
                                                                                                  pitch_m=0.0, x_m=0.0, y_m=0.0, extent_m=0.001)]))
        h.invoke('sample.provide', **AU)
        rejected(h, 'sample.provide', 'SAMPLE_EXISTS', **AU)
        h.invoke('sample.provide', **dict(AU, sample_id='wafer', size_x_m=0.1, size_y_m=0.1))
        rejected(h, 'sample.place', 'OUTSIDE_PLATE', sample_id='wafer', x_m=0.03, y_m=0.0)
        rejected(h, 'sample.place', 'UNKNOWN_SAMPLE', sample_id='nope', x_m=0.0, y_m=0.0)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('stage.move', x_m=0.2, y_m=0.0)
        rejected(h, 'stage.move', 'POWER_OFF', x_m=0.01, y_m=0.0)
        rejected(h, 'measurement.configure', 'FILM_INDEX_REQUIRED', **dict(rt.SETTINGS_EXAMPLE, peak_mode='film'))
        rejected(h, 'measurement.configure', 'SETTING_NOT_FOR_MODE', **dict(rt.SETTINGS_EXAMPLE, mode='focus_variation', double_scan=True))
        rejected(h, 'measurement.set_z_range', 'INVALID_Z_RANGE', upper_z_m=0.03, lower_z_m=0.02)
        g = loaded(AU, slot=0)
        rejected(g, 'focus.auto', 'LENS_NOT_SUPPORTED')                     # the 2.5x lens is for observation only (engineering configuration)
        g.invoke('lens.select', slot=5)
        settle(g)
        g.invoke('measurement.configure', **rt.SETTINGS_EXAMPLE)
        rejected(g, 'measurement.auto_range', 'LENS_NOT_SUPPORTED')        # laser confocal with the interference objective
        rejected(g, 'measurement.start', 'LENS_NOT_SUPPORTED')
        g.invoke('lens.select', slot=1)
        settle(g)
        rejected(g, 'measurement.start', 'Z_RANGE_NOT_SET')
        z = g.z
        g.invoke('measurement.set_z_range', upper_z_m=z, lower_z_m=z + 1e-7)
        rejected(g, 'measurement.start', 'Z_RANGE_TOO_SMALL')
        g.invoke('measurement.configure', **dict(rt.SETTINGS_EXAMPLE, pitch_m=1e-9))
        g.invoke('measurement.set_z_range', upper_z_m=z, lower_z_m=z + 1e-4)
        rejected(g, 'measurement.start', 'TOO_MANY_STEPS')
        g.invoke('focus.move', z_m=0.010)
        settle(g)
        g.invoke('measurement.configure', **rt.SETTINGS_EXAMPLE)
        rejected(g, 'focus.auto', 'NO_SURFACE_FOUND')


class ConfocalPhysics(unittest.TestCase):
    def test_opaque_step_heights_follow_the_hidden_z_scale(self):
        for slot in (2, 3):
            h = loaded(AU, slot=slot)
            rid, r = measure(h)
            self.assertEqual(r['status'], 'completed')
            self.assertGreater(r['valid_fraction'], 0.99)
            st = step_height(h, rid, [-150e-6, -20e-6], [20e-6, 150e-6])
            expect = 200e-9 / (1 + h.truth['z_scale'])
            self.assertAlmostEqual(st, expect, delta=0.04 * expect, msg=rt.LENSES[slot]['label'])
            self.assertEqual(r['template_source']['substances'], ['au', 'si'])
            self.assertEqual(r['n_frames'], int(math.floor((r['z_range_m'][1] - r['z_range_m'][0]) / r['pitch_m'] + 1e-9)) + 1)
            self.assertAlmostEqual(r['pitch_m'], rt.axial_fwhm_m(rt.LENSES[slot]['na']) / 5.0, places=15)
            CHECKS.append(dict(check=f'opaque_step_{rt.LENSES[slot]["label"]}', measured_m=st, expected_m=expect))

    def test_transparent_step_strongest_and_first_peak(self):
        """A 2 um SiO2 step on Si (resolved at 50x): the SiO2/Si interface reflects ~9x more than the SiO2 top, so the strongest peak is the
        apparent interface t (1 - 1/n_eff) above the bare Si while the first-peak mode finds the top surface; a 500 nm step is not resolved
        (apparent separation 0.24 um < axial FWHM 0.34 um) and both modes give the merged peak."""
        n = rt.nk('sio2_film', rt.LASER_WAVELENGTH_M).real
        thick = dict(rt.SAMPLE_EXAMPLE, sample_id='sio2_2um', features=[dict(rt.SAMPLE_EXAMPLE['features'][0], height_m=2.0e-6)])
        h = loaded(thick, slot=3)
        sz = 1 + h.truth['z_scale']
        rid, r = measure(h)
        s_strong = step_height(h, rid, [-150e-6, -20e-6], [20e-6, 150e-6])
        rid, r = measure(h, peak_mode='first')
        s_first = step_height(h, rid, [-150e-6, -20e-6], [20e-6, 150e-6])
        apparent = 2.0e-6 * (1 - 1 / rt.n_effective(n, 0.95)) / sz
        self.assertAlmostEqual(s_strong, apparent, delta=0.1 * apparent)
        self.assertAlmostEqual(s_first, 2.0e-6 / sz, delta=0.03 * 2.0e-6)
        g = loaded(dict(rt.SAMPLE_EXAMPLE), slot=3)
        rid, r = measure(g)
        t_strong = step_height(g, rid, [-150e-6, -20e-6], [20e-6, 150e-6])
        rid, r = measure(g, peak_mode='first')
        t_first = step_height(g, rid, [-150e-6, -20e-6], [20e-6, 150e-6])
        self.assertLess(abs(t_first - t_strong), 40e-9)
        self.assertLess(t_strong, 0.6 * 500e-9)
        CHECKS.append(dict(check='transparent_step', strongest_2um_m=s_strong, first_2um_m=s_first, apparent_interface_m=apparent,
                           strongest_500nm_m=t_strong, first_500nm_m=t_first))

    def test_film_mode_and_its_resolution_limit(self):
        h = loaded(PMMA3, slot=3)
        rid, r = measure(h, peak_mode='film', film_refractive_index=1.49)
        ft = hmap(r, 'film_thickness_m')
        self.assertGreater(np.isfinite(ft).mean(), 0.95)
        self.assertAlmostEqual(float(np.nanmedian(ft)), 3.0e-6, delta=0.05 * 3.0e-6)
        g = loaded(PMMA3, slot=2)                                            # 20x: apparent separation 1.95 um, axial FWHM 2.06 um
        rid, r = measure(g, peak_mode='film', film_refractive_index=1.49)
        self.assertLess(np.isfinite(hmap(r, 'film_thickness_m')).mean(), 0.2)
        CHECKS.append(dict(check='confocal_film_mode', median_50x_m=float(np.nanmedian(ft))))

    def test_numerical_aperture_limits_measurable_slopes(self):
        lens = dict(sample_id='microlens', substrate_substance_id='sio2_fused', thickness_m=0.001, size_x_m=0.010, size_y_m=0.010,
                    features=[dict(kind='cap', substance_id='sio2_fused', height_m=60e-6, width_m=600e-6, pitch_m=0.0, x_m=0.0, y_m=0.0, extent_m=0.001)],
                    film=None, roughness=dict(sq_m=1e-9, correlation_length_m=3e-6), tilt_x_rad=0.0, tilt_y_rad=0.0)
        h = loaded(lens, slot=1)                                             # 10x NA 0.30: slopes above 17.5 deg return no specular light
        rid, r = measure(h, **{k: v for k, v in FAST.items() if k != 'mode'})
        R = (0.3e-3 ** 2 + 60e-6 ** 2) / (2 * 60e-6)
        ring = math.pi * ((0.3e-3) ** 2 - (R * 0.30) ** 2)
        fov = r['fov_m'][0] * r['fov_m'][1]
        self.assertAlmostEqual(1 - r['valid_fraction'], ring / fov, delta=0.5 * ring / fov)
        g = loaded(lens, slot=3)                                             # 50x NA 0.95 sees the centre of the cap completely
        rid, r = measure(g, **{k: v for k, v in FAST.items() if k != 'mode'})
        self.assertGreater(r['valid_fraction'], 0.99)

    def test_double_scan_recovers_dark_steep_pixels(self):
        wavy = dict(sample_id='wavy', substrate_substance_id='si', thickness_m=5.25e-4, size_x_m=0.010, size_y_m=0.010,
                    features=[dict(kind='sine', substance_id=None, height_m=36e-6, width_m=1e-6, pitch_m=200e-6, x_m=0.0, y_m=0.0, extent_m=0.008)],
                    film=None, roughness=dict(sq_m=10e-9, correlation_length_m=1e-6), tilt_x_rad=0.0, tilt_y_rad=0.0)
        vf = {}
        for ds in (False, True):
            h = loaded(wavy, slot=1)
            rid, r = measure(h, double_scan=ds, **{k: v for k, v in FAST.items() if k not in ('mode', 'double_scan')})
            vf[ds] = r['valid_fraction']
            n = int(math.floor((r['z_range_m'][1] - r['z_range_m'][0]) / r['pitch_m'] + 1e-9)) + 1
            self.assertEqual(r['n_frames'], n * (2 if ds else 1))
        self.assertLess(vf[False], 0.9)
        self.assertGreater(vf[True], vf[False] + 0.05)
        CHECKS.append(dict(check='double_scan', valid_single=vf[False], valid_double=vf[True]))

    def test_z_and_xy_calibration_with_standards(self):
        h = loaded(STEP_STD, slot=3)
        rid, r = measure(h)
        raw = step_height(h, rid, [-180e-6, -60e-6], [60e-6, 180e-6])
        cal = h.invoke('calibration.z_scale', record_id=rid, certified_height_m=1.803e-6, axis='x', lower=[-180e-6, -60e-6], upper=[60e-6, 180e-6])
        self.assertAlmostEqual(cal['factor'], 1.803e-6 / raw, delta=0.01)
        vals = []
        for _ in range(3):
            op = h.invoke('measurement.start')['operation_id']
            wait(h, op)
            vals.append(step_height(h, h.operations[op]['record_id'], [-180e-6, -60e-6], [60e-6, 180e-6]))
        self.assertAlmostEqual(float(np.mean(vals)), 1.803e-6, delta=0.01 * 1.803e-6)
        g = loaded(GRATING, slot=3)
        rid, r = measure(g)
        c = g.invoke('calibration.xy_scale', record_id=rid, certified_pitch_m=10e-6, axis='x')
        self.assertAlmostEqual(c['factor'], 1.0 + g.truth['xy_scale']['obj_50x'], delta=1.5e-3)       # the real pixel is (1 + e) x nominal
        self.assertEqual(g.read()['calibration.xy_scale']['obj_50x'], c['factor'])
        rid2, r2 = measure(g, autofocus=False)
        self.assertAlmostEqual(r2['pixel_pitch_m'][0], r['pixel_pitch_m'][0] * c['factor'], places=15)
        rejected(g, 'calibration.xy_scale', 'UNKNOWN_RECORD', record_id='HM9999', certified_pitch_m=10e-6, axis='x')
        CHECKS.append(dict(check='calibration', z_factor=cal['factor'], z_after_m=float(np.mean(vals)), xy_factor=c['factor']))

    def test_stitching_and_150x(self):
        h = loaded(AU, slot=2)
        prepare(h, **{k: v for k, v in FAST.items() if k != 'mode'})
        x0 = h.stage['x']
        op = h.invoke('measurement.stitch', nx=3, ny=2, overlap=0.2)['operation_id']
        h.step(1.0 + 0.001)
        self.assertEqual(h.op['phase'], 'xy')
        self.assertTrue(h.read()['stage.moving'])
        self.assertEqual(wait(h, op), 'completed')
        r = h.records[h.operations[op]['record_id']]
        fx, fy = rt.fov_m(20.0, 1.0)
        self.assertAlmostEqual(r['fov_m'][0], fx * (1 + 2 * 0.8) * h.cal['xy']['obj_20x'], delta=0.02 * fx)
        self.assertAlmostEqual(r['fov_m'][1], fy * (1 + 1 * 0.8) * h.cal['xy']['obj_20x'], delta=0.02 * fy)
        self.assertGreater(r['valid_fraction'], 0.99)
        self.assertEqual(h.stage['x'], x0)
        self.assertEqual(r['kind'], 'stitched_map')
        st = h.invoke('data.step_height', record_id=r['record_id'], axis='x', lower=None, upper=None, leveling='none')
        self.assertAlmostEqual(st['step_height_m'], 200e-9 / (1 + h.truth['z_scale']), delta=20e-9)
        g = loaded(AU, slot=4)
        rid, r = measure(g, **{k: v for k, v in FAST.items() if k != 'mode'})
        self.assertAlmostEqual(r['fov_m'][0], rt.fov_m(150.0, 1.0)[0], delta=0.01 * rt.fov_m(150.0, 1.0)[0])
        self.assertGreater(r['valid_fraction'], 0.99)


class OtherPrinciples(unittest.TestCase):
    def test_focus_variation_needs_texture(self):
        h = loaded(STEEL, slot=1)
        rid, r = measure(h, mode='focus_variation')
        self.assertGreater(r['valid_fraction'], 0.98)
        depth = step_height(h, rid, [-25e-6, 25e-6], [350e-6, 800e-6])
        self.assertAlmostEqual(depth, 5e-6 / (1 + h.truth['z_scale']), delta=0.05 * 5e-6)
        self.assertIsNone(r['gain'])
        g = loaded(PMMA3, slot=2)                                            # smooth film on smooth aluminium: no texture, no focus-variation data
        rid, r = measure(g, mode='focus_variation')
        self.assertLess(r['valid_fraction'], 0.02)
        CHECKS.append(dict(check='focus_variation_groove', depth_m=depth))

    def test_white_light_interferometry_phase_change_on_reflection(self):
        au = dict(AU, sample_id='au_on_si', features=[dict(AU['features'][0], height_m=50e-9)], roughness=dict(sq_m=0.3e-9, correlation_length_m=3e-6))
        h = loaded(au, slot=5)
        rid, r = measure(h, mode='white_light_interferometry')
        self.assertGreater(r['valid_fraction'], 0.99)
        self.assertEqual(r['pitch_m'], rt.WLI_LAMBDA0_M / 8)
        st = step_height(h, rid, [-300e-6, -40e-6], [40e-6, 300e-6])
        expect = (50e-9 + pcor_offset('au', 'si')) / (1 + h.truth['z_scale'])
        self.assertAlmostEqual(st, expect, delta=3e-9)
        self.assertLess(st, 40e-9)                                           # Au reads ~30 nm low against Si
        rg = h.invoke('data.roughness', record_id=rid, region=dict(x0_m=-300e-6, y0_m=-200e-6, x1_m=-40e-6, y1_m=200e-6), leveling='plane', s_filter_m=None,
                      l_filter_m=None, profile_axis='x', lambda_c_m=None)
        self.assertLess(rg['areal']['sq_m'], 0.5e-9)
        CHECKS.append(dict(check='wli_pcor', step_m=st, expected_m=expect))

    def test_spectral_film_thickness(self):
        h = loaded(PMMA3, slot=1)                                      # 10x: top and interface both inside the axial response
        wait(h, h.invoke('focus.auto')['operation_id'])
        op = h.invoke('film.measure', film_substance_id='pmma', substrate_substance_id='al')['operation_id']
        self.assertEqual(wait(h, op), 'completed')
        f = h.records[h.operations[op]['record_id']]['film']
        self.assertTrue(f['in_range'])
        self.assertAlmostEqual(f['thickness_m'], 3.0e-6, delta=0.006 * 3.0e-6)       # documented accuracy +-0.6 %
        self.assertEqual(len(f['wavelength_m']), len(f['reflectance']))
        self.assertTrue(4.0e-7 < f['wavelength_m'][0] < f['wavelength_m'][-1] < 8.0e-7)   # SI
        op = h.invoke('film.measure', film_substance_id='ps', substrate_substance_id='al')['operation_id']
        wait(h, op)
        wrong = h.records[h.operations[op]['record_id']]['film']['thickness_m']
        n_pmma, n_ps = rt.nk('pmma', 600e-9).real, rt.nk('ps', 600e-9).real
        self.assertAlmostEqual(wrong, 3.0e-6 * n_pmma / n_ps, delta=0.02 * 3.0e-6)   # the optical thickness is what the spectrum fixes
        thick = dict(PMMA3, sample_id='thick', film=dict(substance_id='pmma', thickness_m=9.0e-6))
        g = loaded(thick, slot=1)
        wait(g, g.invoke('focus.auto')['operation_id'])
        op = g.invoke('film.measure', film_substance_id='pmma', substrate_substance_id='al')['operation_id']
        wait(g, op)
        rec = g.records[g.operations[op]['record_id']]
        self.assertIsNone(rec['film']['thickness_m'])
        self.assertEqual(rec['valid_fraction'], 0.0)
        rejected(g, 'film.measure', 'UNKNOWN_SUBSTANCE', film_substance_id='nope', substrate_substance_id='al')
        CHECKS.append(dict(check='spectral_film', thickness_m=f['thickness_m'], wrong_material_m=wrong))

    def test_autofocus_locks_onto_the_brighter_buried_interface(self):
        thick = dict(PMMA3, sample_id='pmma10', film=dict(substance_id='pmma', thickness_m=10.0e-6))
        h = loaded(thick, slot=2)
        op = h.invoke('focus.auto')['operation_id']
        wait(h, op)
        n = rt.nk('pmma', rt.LASER_WAVELENGTH_M).real
        err = h.read('evaluator')['truth.focus_error_m']
        self.assertAlmostEqual(err, -10e-6 / rt.n_effective(n, 0.46), delta=0.2 * rt.axial_fwhm_m(0.46) + 0.3e-6)


class FaultsCancelAndPower(unittest.TestCase):
    def test_laser_failure_during_a_measurement(self):
        h = loaded(AU, slot=2)
        prepare(h)
        op = h.invoke('measurement.start')['operation_id']
        h.step(1.05)
        self.assertTrue(h.read()['laser.on'])
        h.invoke('fault.inject', fault='laser_failure', active=True)
        self.assertEqual(h.operations[op]['phase'], 'failed')
        r = h.records[h.operations[op]['record_id']]
        self.assertEqual((r['status'], r['reason'], r['height_m']), ('failed', 'LASER_FAULT', None))
        self.assertFalse(h.read()['laser.on'])
        self.assertEqual(h.error, 'LASER_FAULT')
        rejected(h, 'system.acknowledge', 'CAUSE_PERSISTS')
        rejected(h, 'measurement.start', 'ERROR_PENDING')
        h.invoke('fault.inject', fault='laser_failure', active=False)
        self.assertEqual(h.invoke('system.acknowledge')['cleared'], 'LASER_FAULT')
        h.invoke('measurement.start')

    def test_stage_disconnection_and_revolver_jam(self):
        h = loaded(AU, slot=1)
        prepare(h, **{k: v for k, v in FAST.items() if k != 'mode'})
        op = h.invoke('measurement.stitch', nx=2, ny=2, overlap=0.2)['operation_id']
        h.step(1.001)
        h.invoke('fault.inject', fault='stage_disconnected', active=True)
        self.assertEqual(h.operations[op]['phase'], 'failed')
        self.assertEqual(h.records[h.operations[op]['record_id']]['reason'], 'STAGE_FAULT')
        self.assertFalse(h._stage_moving())
        self.assertFalse(h.read()['panel.stage_lamp'])
        h.invoke('fault.inject', fault='stage_disconnected', active=False)
        h.invoke('system.acknowledge')
        g = loaded(AU, slot=1)
        g.invoke('lens.select', slot=4)
        g.step(1.0)
        g.invoke('fault.inject', fault='revolver_jam', active=True)
        rev = g.rev
        g.step(10.0)
        self.assertEqual(g.rev, rev)
        self.assertIsNone(g.read()['head.objective'])
        rejected(g, 'lens.select', 'ERROR_PENDING', slot=1)
        g.invoke('fault.inject', fault='revolver_jam', active=False)
        g.invoke('system.acknowledge')
        g.invoke('lens.select', slot=4)
        settle(g)
        self.assertEqual(g.read()['head.objective'], '150x')

    def test_vibration_adds_scan_noise(self):
        sq = {}
        for vib in (False, True):
            flat = dict(AU, sample_id='flat', features=[], roughness=dict(sq_m=0.2e-9, correlation_length_m=5e-6))
            h = loaded(flat, slot=3)
            prepare(h)
            if vib:
                h.invoke('fault.inject', fault='vibration', active=True)
            rid, r = measure(h, autofocus=False)
            sq[vib] = h.invoke('data.roughness', record_id=rid, region=None, leveling='plane', s_filter_m=None, l_filter_m=None, profile_axis='x',
                               lambda_c_m=None)['areal']['sq_m']
        self.assertGreater(sq[True], 1.5 * sq[False])

    def test_power_loss_and_cancel_keep_the_remnants(self):
        h = loaded(AU, slot=2)
        prepare(h)
        op = h.invoke('measurement.start')['operation_id']
        h.step(1.2)
        z = h.z
        h.invoke('power.set', enabled=False)
        self.assertEqual(h.operations[op]['phase'], 'failed')
        self.assertEqual(h.records[h.operations[op]['record_id']]['reason'], 'POWER_OFF')
        h.step(5.0)
        self.assertEqual(h.z, z)
        g = loaded(AU, slot=2)
        prepare(g)
        g.register_principal('agent', 'agent')
        s = g.session('agent')
        r = s.invoke(instance_id=g.instance_id, capability_id='measurement.start', arguments={}, request_id='m1')
        g.step(1.2)
        z = g.z
        c = s.cancel(r['operation_id'], 'c1')
        self.assertEqual(c['result']['phase'], 'cancelled')
        self.assertEqual(s.cancel(r['operation_id'], 'c1'), c)
        with self.assertRaises(rt.ActionRejected):
            s.cancel(r['operation_id'], 'c2')
        rec = g.records[g.operations[r['operation_id']]['record_id']]
        self.assertEqual((rec['status'], rec['reason'], rec['height_m']), ('cancelled', 'USER_CANCEL', None))
        g.step(3.0)
        self.assertEqual(g.z, z)
        self.assertFalse(g.read()['laser.on'])
        self.assertEqual(s.poll(r['operation_id'])['status'], 'cancelled')


class LedgersIsolationSessions(unittest.TestCase):
    def test_ledgers_close(self):
        h = loaded(dict(rt.SAMPLE_EXAMPLE), slot=3)
        rid, r = measure(h)
        L = h.ledger()
        self.assertAlmostEqual(L['laser_energy_j'], rt.LASER_MAX_W * L['laser_on_s'], delta=1e-15)
        self.assertAlmostEqual(L['energy_to_sample_j'], rt.OPTICS_TRANSMISSION * L['laser_energy_j'], delta=1e-15)
        self.assertLess(abs(L['energy_residual_j']), 1e-12 * max(L['energy_to_sample_j'], 1e-30))
        self.assertGreater(L['energy_transmitted_j'], 0.0)
        self.assertEqual(L['frames'], r['n_frames'])
        self.assertAlmostEqual(L['laser_on_s'], r['n_frames'] * h._frame_s('laser_confocal') + rt.AUTORANGE_S, delta=1e-6)   # scan + laser pre-scan
        before = h.ledger()['stage_travel_m']
        h.invoke('stage.move', x_m=0.003, y_m=-0.004)
        settle(h)
        self.assertAlmostEqual(h.ledger()['stage_travel_m'] - before, 0.007, places=12)

    def test_two_instances_are_isolated(self):
        a, b = loaded(AU, slot=1), loaded(AU, slot=1)
        b2 = copy.deepcopy(b.read('evaluator'))
        a.invoke('stage.move', x_m=0.01, y_m=0.0)
        a.step(3.0)
        a.invoke('fault.inject', fault='laser_failure', active=True)
        self.assertFalse(_changed(b.read('evaluator'), b2))
        c = loaded(AU, slot=1, seed=2)
        self.assertNotEqual(c.truth['z_scale'], a.truth['z_scale'])

    def test_agent_does_not_see_truth_faults_or_templates(self):
        h = loaded(AU, slot=2)
        h.register_principal('agent', 'agent')
        h.register_principal('judge', 'evaluator')
        s, e = h.session('agent'), h.session('judge')
        obs = s.read(instance_id=h.instance_id)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        self.assertTrue([k for k in e.read(instance_id=h.instance_id)['observations'] if k.startswith('truth.')])
        with self.assertRaises(rt.ActionRejected):
            s.read(instance_id=h.instance_id, state_ids=['truth.focus_error_m'])
        d = s.describe(instance_id=h.instance_id)
        self.assertNotIn('fault.inject', d['capabilities'])
        self.assertNotIn('truth.samples', d['state_slots'])
        r = s.invoke(instance_id=h.instance_id, capability_id='fault.inject', arguments=dict(fault='vibration', active=True), request_id='f1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        e.invoke(instance_id=h.instance_id, capability_id='fault.inject', arguments=dict(fault='vibration', active=True), request_id='f1')
        ev = s.invoke(instance_id=h.instance_id, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e1')['result']['events']
        self.assertFalse([x for x in ev if x['kind'] == 'fault'])
        prepare(h)
        r = s.invoke(instance_id=h.instance_id, capability_id='measurement.start', arguments={}, request_id='m1')
        wait(h, r['operation_id'])
        rid = h.operations[r['operation_id']]['record_id']
        res = s.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=dict(record_id=rid), request_id='r1')['result']
        self.assertEqual(res['template_source']['substances'], [])
        self.assertEqual(s.poll(r['operation_id'])['result']['template_source']['substances'], [])
        full = e.read(instance_id=h.instance_id)['observations']
        self.assertEqual(full['result.last_record_id']['value'], rid)
        self.assertEqual(s.read(instance_id=h.instance_id)['observations']['result.last_record_id']['value'], rid)

    def test_records_and_control_belong_to_their_agent(self):
        h = loaded(AU, slot=2)
        prepare(h, **{k: v for k, v in FAST.items() if k != 'mode'})
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        r = s1.invoke(instance_id=h.instance_id, capability_id='measurement.start', arguments={}, request_id='m1')
        h.step(0.5)
        self.assertTrue(s2.read(instance_id=h.instance_id)['observations']['measurement.started_by_other_agent']['value'])
        self.assertIsNone(s2.read(instance_id=h.instance_id)['observations']['measurement.id']['value'])
        x = s2.invoke(instance_id=h.instance_id, capability_id='stage.move', arguments=dict(x_m=0.001, y_m=0.0), request_id='s1')
        self.assertEqual(x['error']['code'], 'ACCESS_DENIED')
        with self.assertRaises(rt.ActionRejected):
            s2.poll(r['operation_id'])
        with self.assertRaises(rt.ActionRejected):
            s2.cancel(r['operation_id'], 'c1')
        wait(h, r['operation_id'])
        rid = h.operations[r['operation_id']]['record_id']
        for cap, args in (('measurement.result', dict(record_id=rid)), ('data.profile', dict(record_id=rid, start_m=[0.0, 0.0], end_m=[1e-4, 0.0], points=5))):
            self.assertEqual(s2.invoke(instance_id=h.instance_id, capability_id=cap, arguments=args, request_id='q' + cap)['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn(rid, s2.invoke(instance_id=h.instance_id, capability_id='records.list', arguments={}, request_id='l1')['result']['records'])
        self.assertIsNone(s2.read(instance_id=h.instance_id)['observations']['result.last_record_id']['value'])
        self.assertEqual(s1.read(instance_id=h.instance_id)['observations']['result.last_record_id']['value'], rid)
        a = s1.invoke(instance_id=h.instance_id, capability_id='stage.move', arguments=dict(x_m=0.001, y_m=0.0), request_id='s9')
        self.assertEqual(s1.invoke(instance_id=h.instance_id, capability_id='stage.move', arguments=dict(x_m=0.001, y_m=0.0), request_id='s9'), a)
        c = s1.invoke(instance_id=h.instance_id, capability_id='stage.move', arguments=dict(x_m=0.002, y_m=0.0), request_id='s9')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(s1.invoke(instance_id='other', capability_id='records.list', arguments={}, request_id='z')['error']['code'], 'UNKNOWN_INSTANCE')

    def test_queries_bookkeeping_and_records_full(self):
        h = loaded(AU, slot=1)
        p = h.invoke('provenance.read')
        self.assertEqual(p['substances'], len(rt.library()['meta']['substances']))
        ev = h.invoke('events.read', since_s=0.0)['events']
        self.assertIn('SAMPLE_PLACED', [e['code'] for e in ev])
        rid, r = measure(h, **{k: v for k, v in FAST.items() if k != 'mode'})
        for i in range(rt.MAX_RECORDS - 1):
            h.records[f'X{i:04d}'] = dict(copy.deepcopy(r), record_id=f'X{i:04d}', finished_s=0.0)
        rejected(h, 'measurement.start', 'RECORDS_FULL')
        rejected(h, 'film.measure', 'RECORDS_FULL', film_substance_id='pmma', substrate_substance_id='al')
        self.assertIn(rid, h.invoke('records.list')['records'])


class RemainingCapabilities(unittest.TestCase):
    def test_zoom_go_to_profile_remove_and_host_step(self):
        h = loaded(AU, slot=2)
        fx1 = h.read()['optics.fov_m'][0]
        out = h.invoke('zoom.set', zoom=2.0)
        self.assertAlmostEqual(out['fov_m'][0], fx1 / 2, places=15)
        self.assertAlmostEqual(h.read()['optics.total_magnification'], 2 * rt.total_magnification(20.0, 1.0), places=6)
        rid, r = measure(h, **{k: v for k, v in FAST.items() if k != 'mode'})
        self.assertAlmostEqual(r['fov_m'][0], fx1 / 2 * h.cal['xy']['obj_20x'], delta=1e-12)
        self.assertEqual(r['zoom'], 2.0)
        prof = h.invoke('data.profile', record_id=rid, start_m=[-150e-6, 0.0], end_m=[150e-6, 0.0], points=61)
        z = np.array([v if v is not None else np.nan for v in prof['height_m']])
        self.assertAlmostEqual(prof['distance_m'][-1], 300e-6, places=12)
        self.assertAlmostEqual(float(np.nanmedian(z[-15:]) - np.nanmedian(z[:15])), 200e-9 / (1 + h.truth['z_scale']), delta=20e-9)
        h.invoke('zoom.set', zoom=1.0)
        s = dict(AU, sample_id='offset', features=[])
        h.invoke('sample.remove', sample_id='au_step')
        self.assertEqual(h.read()['sample.bench_ids'], ['au_step'])
        h.invoke('sample.provide', **s)
        h.invoke('sample.place', sample_id='offset', x_m=0.012, y_m=-0.006)
        out = h.invoke('stage.go_to', position='sample_center')
        self.assertEqual(out['target_m'], [-0.012, 0.006])
        self.assertAlmostEqual(out['duration_s'], 0.012 / rt.STAGE_SPEED_M_S, places=12)
        t0 = h.t
        self.assertEqual(h.invoke('sim.step', dt_s=out['duration_s'])['time_s'], t0 + out['duration_s'])
        self.assertEqual(h.read()['stage.position_label'], 'sample_center')
        self.assertAlmostEqual(h.read('evaluator')['truth.surface_z_on_axis_m'], rt.SAMPLE_PLATE_Z + h.lift + AU['thickness_m'], delta=5e-9)
        out = h.invoke('stage.go_to', position='load')
        self.assertEqual(out['target_m'], [0.0, -rt.MOTORIZED_XY_M[1] / 2])
        settle(h)
        self.assertEqual(h.read()['stage.position_label'], 'load')
        h.invoke('stage.go_to', position='center')
        settle(h)
        self.assertEqual((h.stage['x'], h.stage['y']), (0.0, 0.0))
        h.invoke('sample.remove', sample_id='offset')
        rejected(h, 'stage.go_to', 'NO_SAMPLE', position='sample_center')
        rejected(h, 'sample.remove', 'SAMPLE_NOT_ON_STAGE', sample_id='offset')


class RuntimeReviewRound1Regressions(unittest.TestCase):
    def test_F01_focus_variation_and_wli_need_the_surface_inside_the_scan_range(self):
        for sample, slot, mode in ((STEEL, 1, 'focus_variation'), (dict(AU, sample_id='au_wli'), 5, 'white_light_interferometry')):
            h = loaded(sample, slot=slot)
            rid, r = measure(h, mode=mode)
            self.assertGreater(r['valid_fraction'], 0.98, mode)
            up, low = h.z_range
            h.invoke('measurement.set_z_range', upper_z_m=up - 0.002, lower_z_m=low - 0.002)       # 2 mm above the surface
            rid, r = measure(h, mode=mode, autofocus=False)
            self.assertEqual(r['valid_fraction'], 0.0, mode)

    def test_F02_film_thickness_needs_the_spot_in_focus(self):
        h = loaded(PMMA3, slot=3)
        wait(h, h.invoke('focus.auto')['operation_id'])
        op = h.invoke('film.measure', film_substance_id='pmma', substrate_substance_id='al')['operation_id']
        wait(h, op)
        f = h.records[h.operations[op]['record_id']]['film']
        self.assertGreater(max(f['collected_fraction'], f['collected_fraction_interface']), 0.9)   # autofocus locks onto the brighter interface
        self.assertAlmostEqual(f['thickness_m'], 3.0e-6, delta=0.006 * 3.0e-6)
        h.invoke('focus.move', z_m=0.0)
        settle(h)
        op = h.invoke('film.measure', film_substance_id='pmma', substrate_substance_id='al')['operation_id']
        wait(h, op)
        rec = h.records[h.operations[op]['record_id']]
        self.assertLess(max(rec['film']['collected_fraction'], rec['film']['collected_fraction_interface']), rt.FILM_MIN_SIGNAL)
        self.assertIsNone(rec['film']['thickness_m'])
        self.assertIn('失焦', rec['note_zh'])

    def test_F14_the_two_film_beams_couple_separately(self):
        h = loaded(PMMA3, slot=3)                                      # 50x: the two planes are 1.7 um apart, the axial response is 0.4 um wide
        top = h._surface_z_at(*h._axis_in_sample())
        interface = top - 3.0e-6 / rt.n_effective(rt.nk('pmma', 600e-9).real, 0.95)
        for label, zf in (('top', top), ('interface', interface), ('midway', 0.5 * (top + interface))):
            h.invoke('focus.move', z_m=h._z_for_focus(zf))
            settle(h)
            op = h.invoke('film.measure', film_substance_id='pmma', substrate_substance_id='al')['operation_id']
            wait(h, op)
            f = h.records[h.operations[op]['record_id']]['film']
            self.assertAlmostEqual(f['thickness_m'], 3.0e-6, delta=0.01 * 3.0e-6, msg=label)     # fringe positions keep the thickness
            if label == 'top':
                self.assertGreater(f['collected_fraction'], 0.9); self.assertLess(f['collected_fraction_interface'], 0.05)
            elif label == 'interface':
                self.assertLess(f['collected_fraction'], 0.05); self.assertGreater(f['collected_fraction_interface'], 0.9)
            else:
                self.assertAlmostEqual(f['collected_fraction'], f['collected_fraction_interface'], delta=0.02)

    def test_F15_opaque_films_give_no_thickness(self):
        for sample, fid, sid in ((dict(STEP_STD, sample_id='cr_film', features=[]), 'cr', 'sio2_fused'),
                                 (dict(AU, sample_id='au_film', features=[], film=dict(substance_id='au', thickness_m=100e-9)), 'au', 'si')):
            h = loaded(sample, slot=2)
            wait(h, h.invoke('focus.auto')['operation_id'])
            op = h.invoke('film.measure', film_substance_id=fid, substrate_substance_id=sid)['operation_id']
            wait(h, op)
            rec = h.records[h.operations[op]['record_id']]
            self.assertIsNone(rec['film']['thickness_m'], fid)
            self.assertIn('不透明', rec['note_zh'])

    def test_F16_no_fringes_no_thickness(self):
        for sample, fid, sid in ((dict(STEP_STD, sample_id='bare_fs', features=[], film=None), 'sio2_film', 'sio2_fused'),   # index-matched: no fringes
                                 (dict(PMMA3, sample_id='bare_al', film=None), 'pmma', 'al'),
                                 (dict(AU, sample_id='bulk_au', substrate_substance_id='au', features=[]), 'pmma', 'au')):
            h = loaded(sample, slot=2)
            wait(h, h.invoke('focus.auto')['operation_id'])
            for _ in range(4):
                op = h.invoke('film.measure', film_substance_id=fid, substrate_substance_id=sid)['operation_id']
                wait(h, op)
                rec = h.records[h.operations[op]['record_id']]
                self.assertIsNone(rec['film']['thickness_m'], sample['sample_id'])
                self.assertIn('没有膜干涉条纹', rec['note_zh'])
                self.assertLess(rec['film']['gain_over_bare_substrate'], rt.FILM_BARE_GAIN)
        h = loaded(PMMA3, slot=2)                                        # a real film clears both tests by a wide margin
        wait(h, h.invoke('focus.auto')['operation_id'])
        op = h.invoke('film.measure', film_substance_id='pmma', substrate_substance_id='al')['operation_id']
        wait(h, op)
        f = h.records[h.operations[op]['record_id']]['film']
        self.assertAlmostEqual(f['thickness_m'], 3.0e-6, delta=0.006 * 3.0e-6)
        self.assertGreater(f['fringe_to_noise'], 10 * rt.FILM_FRINGE_SNR)
        self.assertGreater(f['gain_over_bare_substrate'], 5 * rt.FILM_BARE_GAIN)
        self.assertTrue(all(0.0 <= w <= rt.FILM_GAIN_MAX for w in f['fit_collected_fractions']))

    def test_F17_black_sample_plate_gives_no_film(self):
        small = dict(PMMA3, sample_id='off_axis', film=None)
        for slot in (1, 2):
            h = loaded(small, slot=slot, x=0.02)                          # the optical axis sees the black anodised plate, 15 mm beside the sample
            h.invoke('focus.move', z_m=h._z_for_focus(rt.SAMPLE_PLATE_Z + h.lift))
            settle(h)
            for fid, sid in (('sio2_film', 'sio2_fused'), ('pmma', 'sio2_fused'), ('sio2_film', 'caf2')):
                op = h.invoke('film.measure', film_substance_id=fid, substrate_substance_id=sid)['operation_id']
                wait(h, op)
                rec = h.records[h.operations[op]['record_id']]
                self.assertGreater(rec['film']['collected_fraction'], 0.9)          # in focus on the plate: the no-fringe test decides, not the signal
                self.assertIsNone(rec['film']['thickness_m'], (slot, fid, sid))
                self.assertIn('没有膜干涉条纹', rec['note_zh'])

    def test_F18_thin_film_beams_follow_one_focus_offset(self):
        thin = dict(PMMA3, sample_id='pmma110', film=dict(substance_id='pmma', thickness_m=110e-9))
        h = loaded(thin, slot=3)
        wait(h, h.invoke('focus.auto')['operation_id'])
        for _ in range(6):
            op = h.invoke('film.measure', film_substance_id='pmma', substrate_substance_id='al')['operation_id']
            wait(h, op)
            f = h.records[h.operations[op]['record_id']]['film']
            # a thin film gives less than one fringe in the band: noise-limited to about 0.3-0.6 nm (sigma) at 50x, well above the 0.1 nm typical value
            self.assertAlmostEqual(f['thickness_m'], 110e-9, delta=0.02 * 110e-9)
            w_top, w_int = f['fit_collected_fractions']
            self.assertAlmostEqual(w_top, w_int, delta=0.1)                 # both planes lie well inside the 50x axial response

    def test_F03_collision_checks_use_the_target_of_a_rising_lift(self):
        h = loaded(AU, slot=3)
        h.invoke('stage.set_lift', lift_m=-0.003)
        settle(h)
        h.invoke('stage.set_lift', lift_m=0.0)                       # rising again
        h.step(0.2)
        top_final = rt.SAMPLE_PLATE_Z + AU['thickness_m'] + rt.sample_extremes(h.samples['au_step'])[0]
        z_bad = h._z_for_focus(top_final + 0.5 * rt.COLLISION_MARGIN_M - rt.LENSES[3]['wd_m'])
        rejected(h, 'focus.move', 'COLLISION_RISK', z_m=z_bad)
        g = loaded(AU, slot=1)
        g.invoke('stage.set_lift', lift_m=-0.003)
        settle(g)
        g.invoke('focus.move', z_m=g._z_for_focus(g._sample_top_z() + 2.0e-3 - rt.LENSES[3]['wd_m']))    # a 50x would clear the lowered top by 2 mm
        settle(g)
        g.invoke('stage.set_lift', lift_m=0.0)
        g.step(0.2)
        self.assertLess(g.invoke('lens.select', slot=3)['z_retract_m'], 0.0)             # retracts for the lift target, not the current lift
        k = loaded(AU, slot=1)
        k.invoke('sample.remove', sample_id='au_step')
        k.invoke('stage.set_lift', lift_m=-0.003)
        settle(k)
        k.invoke('focus.move', z_m=k._z_for_focus(rt.SAMPLE_PLATE_Z - 0.003 + TALL['thickness_m'] + 2.5e-3 - rt.LENSES[1]['wd_m']))
        settle(k)
        k.invoke('sample.provide', **TALL)
        k.invoke('stage.set_lift', lift_m=0.0)
        k.step(0.2)
        rejected(k, 'sample.place', 'SAMPLE_TOO_TALL', sample_id='tall', x_m=0.0, y_m=0.0)    # fits now, not once the lift is up

    def test_F04_lens_change_protects_every_objective_swept_through_the_axis(self):
        tall = dict(AU, sample_id='tall_step', features=[dict(AU['features'][0], height_m=0.45e-3)])
        h = loaded(tall, slot=1)
        h.invoke('lens.select', slot=2)
        settle(h)
        h.invoke('focus.move', z_m=h._z_for_focus(h._sample_top_z() + 1.2e-3 - rt.LENSES[2]['wd_m']))
        settle(h)
        out = h.invoke('lens.select', slot=5)                        # 20x -> 20x DI sweeps the 50x and the 150x through the axis
        self.assertLess(out['z_retract_m'], 0.0)
        z_safe = h.z + out['z_retract_m']
        for L in (rt.LENSES[3], rt.LENSES[4]):
            self.assertGreaterEqual(h._tip_z(L, z_safe) - h._sample_top_z(), rt.SAFE_TIP_CLEARANCE_M - 1e-9)

    def test_F05_stitching_in_y_moves_the_y_stage_and_is_declared(self):
        self.assertTrue({'stage.y_m', 'stage.y_indicated_m'} <= set(rt.ON_STEP['measurement.stitch']))
        h = loaded(AU, slot=3)
        prepare(h, **{k: v for k, v in FAST.items() if k != 'mode'})
        op = h.invoke('measurement.stitch', nx=1, ny=2, overlap=0.2)['operation_id']
        ys = set()
        while h.operations[op]['phase'] == 'running':
            h.step(0.05)
            ys.add(round(h.stage['y'], 9))
        self.assertGreater(len(ys), 2)

    def test_F06_related_model_sources_are_cited(self):
        m = _meta()['capabilities']
        for k in ('measurement.configure', 'measurement.start'):
            self.assertIn('related_vk_x250k', rt.CAP_SOURCES[k])
            self.assertIn('related_vk_x1000', rt.CAP_SOURCES[k])
        self.assertIn('related_vk_x250k', rt.CAP_SOURCES['measurement.auto_range'])
        self.assertIn('VK-X250K', ''.join(rt.CAP_LIMITS['measurement.start']))
        self.assertTrue(m['measurement.start'])

    def test_F07_histogram_step_with_plane_levelling(self):
        for tx, ty in ((0.0, 0.0), (-0.003, 0.001), (0.0, 0.003)):          # tilts larger than the step (0.8-1.1 um over the field)
            h = loaded(dict(AU, sample_id='tilted', tilt_x_rad=tx, tilt_y_rad=ty), slot=3)
            rid, r = measure(h)
            st = h.invoke('data.step_height', record_id=rid, axis='x', lower=None, upper=None, leveling='plane')['step_height_m']
            self.assertAlmostEqual(st, 200e-9 / (1 + h.truth['z_scale']), delta=12e-9, msg=(tx, ty))

    def test_F08_vibration_affects_focus_variation_and_wli(self):
        for sample, slot, mode in ((STEEL, 2, 'focus_variation'), (dict(AU, sample_id='flat_wli', features=[], roughness=dict(sq_m=0.2e-9, correlation_length_m=5e-6)), 5,
                                                                  'white_light_interferometry')):
            sq = {}
            for vib in (False, True):
                h = loaded(sample, slot=slot)
                prepare(h, mode=mode)
                if vib:
                    h.invoke('fault.inject', fault='vibration', active=True)
                rid, r = measure(h, mode=mode, autofocus=False)
                sq[vib] = h.invoke('data.roughness', record_id=rid, region=None, leveling='plane', s_filter_m=None, l_filter_m=None, profile_axis='x',
                                   lambda_c_m=None)['areal']['sq_m']
            self.assertGreater(sq[True], sq[False], mode)

    def test_F09_engineering_constants_are_declared_parameters(self):
        params = _meta()['mechanisms']['vk_x3000_profiler']['parameters']
        for k in ('pixel_z_noise_m', 'pinhole_v', 'side_lobe_margin', 'same_peak_fwhm', 'valid_peak', 'aag_target', 'double_scan_gain', 'z_repeat_m',
                  'fv_texture_ref_m', 'film_noise_rel', 'film_band_m'):
            self.assertEqual(params[k]['evidence'], 'assumption', k)
        self.assertNotIn('parfocal_m', params)

    def test_F10_displayed_result_of_another_agent_is_hidden(self):
        h = loaded(PMMA3, slot=3)
        wait(h, h.invoke('focus.auto')['operation_id'])
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        r = s1.invoke(instance_id=h.instance_id, capability_id='film.measure', arguments=dict(film_substance_id='pmma', substrate_substance_id='al'), request_id='f1')
        wait(h, r['operation_id'])
        self.assertIsNotNone(s1.read(instance_id=h.instance_id)['observations']['result.last_value']['value'])
        self.assertIsNone(s2.read(instance_id=h.instance_id)['observations']['result.last_value']['value'])

    def test_F11_laser_pre_scan_of_the_automatic_limits(self):
        h = loaded(AU, slot=3)
        h.invoke('measurement.configure', **rt.SETTINGS_EXAMPLE)
        before = h.ledger()['laser_on_s']
        op = h.invoke('measurement.auto_range')['operation_id']
        h.step(1.0)
        self.assertTrue(h.read()['laser.on'])
        wait(h, op)
        self.assertAlmostEqual(h.ledger()['laser_on_s'] - before, rt.AUTORANGE_S, delta=1e-9)
        self.assertLess(abs(h.ledger()['energy_residual_j']), 1e-15)

    def test_F12_opaque_top_materials_absorb(self):
        h = loaded(STEP_STD, slot=3)
        measure(h)
        L = h.ledger()
        self.assertEqual(L['energy_transmitted_j'], 0.0)
        self.assertGreater(L['energy_absorbed_j'], 0.0)
        g = loaded(dict(AU, sample_id='silica', substrate_substance_id='sio2_fused', features=[]), slot=3)
        measure(g)
        self.assertGreater(g.ledger()['energy_transmitted_j'], 0.9 * g.ledger()['energy_to_sample_j'])

    def test_power_button_toggles(self):
        h = configured()
        self.assertEqual(h.invoke('power.press_button'), dict(enabled=True, state='booting'))
        h.step(rt.BOOT_S + 1)
        self.assertEqual(h.invoke('power.press_button'), dict(enabled=False, state='off'))
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0))
        rejected(h, 'power.press_button', 'MAINS_OUT_OF_RANGE')


# ---------------------------------------------------------------- declared effects (scenario states)
_SC = {}
_SETTINGS = [dict(FAST), dict(rt.SETTINGS_EXAMPLE, mode='focus_variation'), dict(FAST, peak_mode='film', film_refractive_index=1.49),
             dict(FAST, double_scan=True)]


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
        return rt.KeyenceVkx3000Runtime()
    if name == 'off':
        h = configured()
        for s in (AU, TALL, PMMA3):
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
    if name == 'loaded':
        h = _scenario('ready')
        h.invoke('sample.place', sample_id='au_step', x_m=0.0, y_m=0.0)
        h.invoke('lens.select', slot=1)
        settle(h)
        return focus_near(h)
    if name == 'focused':
        h = _scenario('loaded')
        return prepare(h, **{k: v for k, v in FAST.items() if k != 'mode'})
    if name == 'stage_moving':
        h = _scenario('focused')
        h.invoke('stage.move', x_m=0.002, y_m=0.001)
        h.step(0.05)
        return h
    if name == 'z_moving':
        h = _scenario('focused')
        h.invoke('focus.move', z_m=h.z - 0.002)
        h.step(0.1)
        return h
    if name == 'lens_retracting':
        h = _scenario('focused')
        h.invoke('lens.select', slot=3)
        h.step(0.02)
        assert h._z_moving()
        return h
    if name == 'revolver_turning':
        h = _scenario('lens_retracting')
        while h._z_moving():
            h.step(0.05)
        h.step(0.3)
        assert not h._revolver_settled()
        return h
    if name == 'lifting':
        h = _scenario('focused')
        h.invoke('stage.set_lift', lift_m=-0.004)
        h.step(0.5)
        return h
    if name == 'unpowered_loaded':
        h = _scenario('focused')
        h.invoke('power.set', enabled=False)
        return h
    if name == 'unpowered_lifting':
        h = _scenario('unpowered_loaded')
        h.invoke('stage.set_lift', lift_m=-0.004)
        h.step(0.5)
        return h
    if name == 'autofocusing':
        h = _scenario('focused')
        h.invoke('focus.move', z_m=h.z - 2e-4)
        settle(h)
        h.invoke('focus.auto')
        h.step(0.5)
        return h
    if name == 'autofocus_moving':
        h = _scenario('autofocusing')
        return _into(h, 'z')
    if name == 'auto_ranging':
        h = _scenario('focused')
        h.invoke('measurement.auto_range')
        h.step(1.0)
        return h
    if name == 'measure_overhead':
        h = _scenario('focused')
        h.invoke('measurement.start')
        h.step(0.5)
        return h
    if name == 'measuring':
        h = _scenario('measure_overhead')
        return _into(h, 'scan')
    if name == 'measure_returning':
        h = _scenario('measuring')
        _into(h, 'z')
        return h
    if name == 'measured':
        h = _scenario('measuring')
        settle(h)
        return h
    if name == 'fv_measuring':
        h = _scenario('focused')
        h.invoke('measurement.configure', **dict(rt.SETTINGS_EXAMPLE, mode='focus_variation'))
        h.invoke('measurement.start')
        return _into(h, 'scan')
    if name == 'double_scanning':
        h = _scenario('focused')
        h.invoke('measurement.configure', **dict(FAST, double_scan=True))
        h.invoke('measurement.start')
        return _into(h, 'scan')
    if name == 'stitching':
        h = _scenario('focused')
        h.invoke('measurement.stitch', nx=2, ny=1, overlap=0.2)
        return _into(h, 'xy')
    if name == 'stitch_scanning':
        h = _scenario('stitching')
        _into(h, 'scan')
        return h
    if name == 'film_measuring':
        h = _scenario('focused')
        h.invoke('film.measure', film_substance_id='pmma', substrate_substance_id='al')
        h.step(0.5)
        return h
    if name == 'laser_fault':
        h = _scenario('focused')
        h.invoke('fault.inject', fault='laser_failure', active=True)
        return h
    if name == 'laser_fault_cleared':
        h = _scenario('laser_fault')
        h.invoke('fault.inject', fault='laser_failure', active=False)
        return h
    if name == 'stage_fault':
        h = _scenario('focused')
        h.invoke('fault.inject', fault='stage_disconnected', active=True)
        return h
    if name == 'revolver_jammed':
        h = _scenario('revolver_turning')
        h.invoke('fault.inject', fault='revolver_jam', active=True)
        return h
    if name == 'vibration_measuring':
        h = _scenario('focused')
        h.invoke('fault.inject', fault='vibration', active=True)
        h.invoke('measurement.start')
        _into(h, 'scan')
        return h
    if name == 'mains_fault':
        h = _scenario('focused')
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0))
        return h
    if name == 'mains_restored':
        h = _scenario('mains_fault')
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE))
        return h
    if name == 'tall_lowered':
        h = _scenario('ready')
        h.invoke('stage.set_lift', lift_m=-0.012)
        settle(h)
        h.invoke('sample.place', sample_id='tall', x_m=0.0, y_m=0.0)
        return h
    if name == 'film_sample_50x':
        h = _scenario('ready')
        h.invoke('sample.place', sample_id='pmma_al', x_m=0.0, y_m=0.0)
        h.invoke('lens.select', slot=3)
        settle(h)
        focus_near(h, above=2e-5)
        return prepare(h, peak_mode='film', film_refractive_index=1.49, quality='high_speed')
    if name == 'wli_ready':
        h = _scenario('loaded')
        h.invoke('lens.select', slot=5)
        settle(h)
        focus_near(h, above=2e-5)
        return prepare(h, mode='white_light_interferometry')
    if name == 'wli_measuring':
        h = _scenario('wli_ready')
        h.invoke('measurement.start')
        return _into(h, 'scan')
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'off', 'booting', 'ready', 'loaded', 'focused', 'stage_moving', 'z_moving', 'lens_retracting', 'revolver_turning', 'lifting',
             'unpowered_loaded', 'unpowered_lifting', 'autofocusing', 'autofocus_moving', 'auto_ranging', 'measure_overhead', 'measuring', 'measure_returning',
             'measured', 'fv_measuring', 'double_scanning', 'stitching', 'stitch_scanning', 'film_measuring', 'laser_fault', 'laser_fault_cleared',
             'stage_fault', 'revolver_jammed', 'vibration_measuring', 'mains_fault', 'mains_restored', 'tall_lowered', 'film_sample_50x', 'wli_ready',
             'wli_measuring']
STEP_DTS = (0.4, 1.0, 3.0, 15.0, 120.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(room_k=300.15, mains_voltage_v=60.0, relative_humidity=0.5, noise_seed=1)],
        'sample.provide': [dict(rt.SAMPLE_EXAMPLE, sample_id='new')],
        'sample.place': [dict(sample_id='au_step', x_m=0.0, y_m=0.0), dict(sample_id='pmma_al', x_m=0.01, y_m=-0.005)],
        'sample.remove': [dict(sample_id='au_step'), dict(sample_id='tall')],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_button': [{}], 'system.acknowledge': [{}],
        'fault.inject': [dict(fault=f, active=True) for f in rt.FAULTS] + [dict(fault=f, active=False) for f in rt.FAULTS],
        'stage.move': [dict(x_m=0.002, y_m=-0.001), dict(x_m=-0.03, y_m=0.02)],
        'stage.go_to': [dict(position='center'), dict(position='load'), dict(position='sample_center')],
        'stage.set_lift': [dict(lift_m=-0.010), dict(lift_m=0.0), dict(lift_m=-0.0005)],
        'lens.select': [dict(slot=2), dict(slot=3), dict(slot=5)],
        'zoom.set': [dict(zoom=2.0), dict(zoom=1.0)],
        'focus.move': [dict(z_m=0.030), dict(z_m=0.0322)],
        'focus.auto': [{}],
        'measurement.configure': _SETTINGS,
        'measurement.set_z_range': [dict(upper_z_m=0.03225, lower_z_m=0.03229)],
        'measurement.auto_range': [{}], 'measurement.start': [{}], 'measurement.stitch': [dict(nx=2, ny=1, overlap=0.2), dict(nx=1, ny=2, overlap=0.2)],
        'film.measure': [dict(film_substance_id='pmma', substrate_substance_id='al')],
        'calibration.z_scale': [dict(record_id='HM0001', certified_height_m=200e-9, axis='x', lower=[-400e-6, -50e-6], upper=[50e-6, 400e-6])],
        'calibration.xy_scale': [dict(record_id='HM0001', certified_pitch_m=10e-6, axis='x')],
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
    return cap not in rt.UNSUPPORTED and rt.CAPS[cap]['kind'] not in ('query', 'analysis')


class DeclaredEffects(unittest.TestCase):
    def test_immediate_and_on_step_effects_are_declared(self):
        """For every capability accepted in any scenario state: the slots it changes at once are declared effects, the slots whose change during the
        following step differs from an identical run without the call are declared on_step effects, and the step itself only changes slots declared
        as on_step effects of sim.step (the runtime noise is seeded by counters, so both runs draw identical noise)."""
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

    def test_every_device_capability_is_accepted_in_some_scenario(self):
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
        """Every accepted asynchronous call is followed until its operation has left 'running' (+ 5 s), every other call for 150 s, comparing with
        the identical run without the call every 0.05 s for the first 10 s and every 1 s after (transient phases and completion effects outside
        the short windows)."""
        missing = {}
        for sname in ('focused', 'loaded', 'measuring', 'stitching', 'film_sample_50x', 'wli_ready', 'unpowered_loaded', 'revolver_turning', 'laser_fault'):
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
                    oid = res.get('operation_id') if isinstance(res, dict) else None
                    v1 = A.read('evaluator')
                    B = copy.deepcopy(S)
                    t, t_end = 0.0, (1200.0 if oid else 150.0)
                    while t < t_end - 1e-9:
                        d = 0.05 if t < 10.0 - 1e-9 else 1.0          # fine first: transient phases (laser on during a 0.1 s scan)
                        A.step(d)
                        B.step(d)
                        t += d
                        a2, b2 = A.read('evaluator'), B.read('evaluator')
                        for k in v0:
                            if k not in rt.ON_STEP.get(cap, []) and _step_differs(v1[k], a2[k], v0[k], b2[k]):
                                missing.setdefault(cap, set()).add((sname, k))
                        if oid and A.operations[oid]['phase'] != 'running' and t_end == 1200.0:
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
