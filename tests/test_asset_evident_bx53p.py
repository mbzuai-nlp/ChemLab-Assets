"""tests for CHEM-162 evident_bx53p: python3 -m unittest tests/test_asset_evident_bx53p.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances
are isolated, agent/evaluator truth isolation, conservation (electrical energy = electronics + LED power x time; frame / exposure / rotation
ledgers), real return values validated against the declared schemas (asset_tool validate_value), asynchronous completion and cancellation
remnants, host-step independence, declared immediate and on_step effects over scenario states; plus the polarized-light physics: extinction of a
birefringent crystal when its vibration directions are parallel to the polarizer, maximum brightness at 45 deg, Michel-Levy colours (quartz 30 um
first-order white/yellow, calcite high-order white), the first-order red plate (magenta on isotropic, addition / subtraction with a quartz section),
isotropic sections dark between crossed polars, a nematic Schlieren texture, the extinction scan against the truth, the retardation estimate with
a non-unique candidate list, objectives (field of view FN 22 / M, defocus, oil), condenser top lens and iris, click stops, camera saturation, faults.
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
from asset_runtime import evident_bx53p as rt  # noqa: E402
import asset_tool  # noqa: E402

DEG = rt.DEG
CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}


def _meta():
    if not _META:
        _META.update(rt.export_evident_bx53p_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.EvidentBx53pRuntime.invoke


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
        if undeclared:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=copy.deepcopy(a), result_keys=sorted(out)))
    return out


rt.EvidentBx53pRuntime.invoke = _instrumented_invoke

QUARTZ = dict(rt.SAMPLE_EXAMPLE)
QUARTZ_XTAL = dict(QUARTZ, sample_id='quartz_plate', kind='single_crystal', grains=1, orientation=dict(tilt_rad=math.pi / 2, azimuth_rad=0.0, spin_rad=0.0))
CALCITE_XTAL = dict(QUARTZ_XTAL, sample_id='calcite_plate', substance_id='main_caco3')
LC = dict(QUARTZ, sample_id='lc_cell', kind='liquid_crystal', substance_id='other_5cb', thickness_m=5e-6, grains=1, defects=4)
GLASS = dict(QUARTZ, sample_id='glass', kind='isotropic', substance_id=None, grains=1)
THICK = dict(QUARTZ, sample_id='thick_slide', slide_thickness_m=2.0e-3)
OIL = dict(QUARTZ, sample_id='oiled', immersion_oil=True)


def configured(seed=1, **plant):
    h = rt.EvidentBx53pRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    return h


def ready(seed=1):
    h = configured(seed)
    h.invoke('power.set', enabled=True)
    h.invoke('illumination.set_level', level=0.6)
    return h


def loaded(sample=QUARTZ, seed=1, objective=1):
    h = ready(seed)
    h.invoke('sample.provide', **sample)
    h.invoke('sample.place', sample_id=sample['sample_id'])
    h.invoke('objective.select', index=objective)
    h.invoke('focus.move', stage_z_m=h._focus_target(h.samples[sample['sample_id']]))
    return h


def wait(h, oid, tmax=600.0, dt=0.25):
    t0 = h.t
    while h.operations[oid]['phase'] == 'running' and h.t - t0 < tmax:
        h.step(dt)
    return h.operations[oid]['phase']


def acquire(h):
    oid = h.invoke('camera.acquire')['operation_id']
    assert wait(h, oid) == 'completed'
    return h.records[h.operations[oid]['record_id']]


def center(r, half=4):
    c = np.asarray(r['image']['counts'], float)
    H, W = c.shape[:2]
    return c[H // 2 - half:H // 2 + half, W // 2 - half:W // 2 + half].reshape(-1, 3).mean(0)


def check(cid, **detail):
    CHECKS.append(dict(check=cid, status='passed', **detail))


class ContractAndSchemas(unittest.TestCase):
    def test_module_identity_and_export(self):
        m = _meta()
        self.assertEqual((rt.ASSET_TYPE_ID, rt.CATALOG_ID), ('instrument.evident_bx53p', 'CHEM-162'))
        self.assertEqual(set(m['capabilities']), set(rt.CAPS))
        self.assertEqual(set(m['state_slots']), set(rt.SLOTS))
        for k, c in m['capabilities'].items():
            for sch in (c['input_schema'], c['output_schema']):
                self.assertEqual(asset_tool.lint_schema(sch) if hasattr(asset_tool, 'lint_schema') else [], [], k)
            self.assertTrue(c['errors'], k)
            self.assertEqual(asset_tool.validate_value(c['example_arguments'], c['input_schema']), [], k)
        for k, v in rt.EFFECTS.items():
            self.assertTrue(set(v) <= set(rt.SLOTS), k)
        check('contract_export_consistent')

    def test_library_and_colour_data(self):
        lib = rt.biref_lib()
        self.assertGreaterEqual(len(lib['substances']), 50)
        e = lib['substances']['main_caco3']
        self.assertAlmostEqual(e['n_589']['o'], 1.6583, delta=5e-4)
        self.assertAlmostEqual(e['n_589']['e'], 1.4864, delta=5e-4)
        q = lib['substances']['main_sio2']
        self.assertAlmostEqual(q['birefringence_589'], 0.0091, delta=3e-4)
        self.assertTrue(all(e['source']['references'] for e in lib['substances'].values()))
        cmf = rt.cmf()
        self.assertEqual(cmf.shape, (len(rt.LAM_M), 3))
        self.assertAlmostEqual(float(rt.rgb_of(np.full(len(rt.LAM_M), 1.0)) @ np.array([0.2126, 0.7152, 0.0722])), 1.0, delta=0.02)
        check('library_and_cie_data')


class HostClockAndPurity(unittest.TestCase):
    def test_read_describe_are_pure_and_calls_do_not_advance_time(self):
        h = loaded()
        v0, t0 = h.read('evaluator'), h.t
        h.register_principal('ag', 'agent')
        h.session('ag').describe(instance_id=rt.INSTANCE)
        h.session('ag').read(instance_id=rt.INSTANCE)
        self.assertEqual(h.read('evaluator'), v0)
        oid = h.invoke('camera.acquire')['operation_id']
        self.assertEqual(h.t, t0)
        self.assertEqual(h.operations[oid]['phase'], 'running')
        h.step(0.01)
        self.assertEqual(h.operations[oid]['phase'], 'running')
        h.step(1.0)
        self.assertEqual(h.operations[oid]['phase'], 'completed')

    def test_step_partition_does_not_change_results(self):
        a, b = loaded(seed=4), loaded(seed=4)
        a.invoke('scan.extinction', step_rad=15 * DEG, region=None)
        b.invoke('scan.extinction', step_rad=15 * DEG, region=None)
        a.step(100.0)
        for _ in range(400):
            b.step(0.25)
        ra, rb = a.records['SC0001'], b.records['SC0001']
        self.assertEqual(ra['scan'], rb['scan'])
        self.assertAlmostEqual(a.ledger()['electrical_energy_j'], b.ledger()['electrical_energy_j'], delta=1e-6)
        check('host_step_partition_independent')

    def test_energy_and_usage_ledgers(self):
        h = ready()
        e0 = h.ledger()['electrical_energy_j']
        h.step(100.0)
        self.assertAlmostEqual(h.ledger()['electrical_energy_j'] - e0, (rt.IDLE_W + rt.LED_W_MAX * 0.6) * 100.0, delta=1e-6)
        self.assertAlmostEqual(h.ledger()['led_energy_j'], rt.LED_W_MAX * 0.6 * 100.0, delta=1e-6)
        h.invoke('illumination.set_level', level=1.0)
        e1 = h.ledger()['electrical_energy_j']
        h.step(10.0)
        self.assertAlmostEqual(h.ledger()['electrical_energy_j'] - e1, rt.POWER_W * 10.0, delta=1e-6)     # documented approx. 24 W at full level
        h.invoke('power.set', enabled=False)
        e2 = h.ledger()['electrical_energy_j']
        h.step(50.0)
        self.assertEqual(h.ledger()['electrical_energy_j'], e2)
        g = loaded()
        acquire(g)
        g.invoke('stage.rotate', angle_rad=math.pi / 2)
        L = g.ledger()
        self.assertEqual(L['frames'], 1.0)
        self.assertAlmostEqual(L['exposure_s'], 0.05, delta=1e-12)
        self.assertAlmostEqual(L['stage_rotation_rad'], math.pi / 2, delta=1e-9)
        check('energy_ledger_closes')


class Physics(unittest.TestCase):
    def test_extinction_and_45_degree_brightness(self):
        h = loaded(QUARTZ_XTAL)
        dark = center(acquire(h))
        h.invoke('stage.rotate', angle_rad=math.pi / 4)
        bright = center(acquire(h))
        self.assertLess(dark.mean(), 0.05 * bright.mean())
        self.assertGreater(bright.mean(), 50.0)
        h.invoke('stage.rotate', angle_rad=math.pi / 2)
        self.assertLess(center(acquire(h)).mean(), 0.05 * bright.mean())                           # extinct every 90 deg
        check('extinction_every_90_deg')

    def test_michel_levy_colours(self):
        q = rt.chart(275e-9)
        self.assertGreater(q[0], q[2])
        self.assertGreater(q[1], q[2])                                                             # first-order white / pale yellow: blue lowest
        red = rt.chart(530e-9)
        self.assertGreater(red[0], red[1])
        self.assertGreater(red[2], red[1])                                                         # first-order red: magenta, green lowest
        blue2 = rt.chart(700e-9)
        self.assertGreater(blue2[2], blue2[0])                                                     # second-order blue
        h = loaded(CALCITE_XTAL)
        h.invoke('stage.rotate', angle_rad=math.pi / 4)
        c = center(acquire(h))
        g = loaded(GLASS)
        g.invoke('analyzer.set_inserted', inserted=False)
        w = center(acquire(g))
        self.assertLess(np.abs(c / c.sum() - w / w.sum()).max(), 0.04)                             # high-order white: the lamp colour, no hue
        check('michel_levy_colours')

    def test_tint_plate_on_isotropic_addition_and_subtraction(self):
        g = loaded(GLASS)
        g.invoke('compensator.insert', plate='U-TP530')
        m = center(acquire(g))
        self.assertGreater(m[0], m[1])
        self.assertGreater(m[2], m[1])                                                             # magenta background
        h = loaded(QUARTZ_XTAL)                                                                    # slow axis along the stage 0 direction (x)
        h.invoke('compensator.insert', plate='U-TP530')                                            # plate slow axis NE-SW = +45 deg in the image frame
        h.invoke('stage.rotate', angle_rad=math.pi / 4)                                            # sample slow axis parallel to the plate: addition
        add = center(acquire(h))
        h.invoke('stage.rotate', angle_rad=3 * math.pi / 4)                                        # perpendicular: subtraction
        sub = center(acquire(h))
        R = h.read('evaluator')['truth.center_retardation_m']
        ref_add, ref_sub = rt.chart(R + 530e-9), rt.chart(530e-9 - R)                              # addition (805 nm, second-order green) / subtraction (255 nm)
        chrom = lambda v: np.asarray(v) / np.sum(v)                                                 # noqa: E731
        qe = np.asarray(rt.CAMERA['qe']) / max(rt.CAMERA['qe'])
        self.assertLess(np.abs(chrom(add) - chrom(ref_add * qe)).sum(), np.abs(chrom(add) - chrom(ref_sub * qe)).sum())
        self.assertLess(np.abs(chrom(sub) - chrom(ref_sub * qe)).sum(), np.abs(chrom(sub) - chrom(ref_add * qe)).sum())
        check('tint_plate_addition_subtraction')

    def test_isotropic_dark_and_open_analyzer(self):
        g = loaded(GLASS)
        dark = center(acquire(g))
        g.invoke('analyzer.set_inserted', inserted=False)
        open_ = center(acquire(g))
        self.assertLess(dark.mean(), 0.01 * open_.mean())
        g.invoke('analyzer.set_inserted', inserted=True)
        g.invoke('analyzer.set_angle', angle_rad=0.0)                                              # parallel polars
        par = center(acquire(g))
        self.assertGreater(par.mean(), 0.8 * open_.mean())
        check('isotropic_dark_crossed')

    def test_nematic_schlieren_texture(self):
        h = loaded(LC)
        r = acquire(h)
        c = np.asarray(r['image']['counts'], float).mean(2)
        H, W = c.shape
        yy, xx = np.mgrid[0:H, 0:W]
        inside = ((xx - (W - 1) / 2) ** 2 + (yy - (H - 1) / 2) ** 2) <= ((W - 1) / 2 * 0.95) ** 2
        frac_dark = float((c[inside] < 0.1 * np.percentile(c[inside], 95)).mean())
        self.assertGreater(frac_dark, 0.02)                                                        # dark brushes where the director is parallel to a polar
        self.assertLess(frac_dark, 0.6)
        h.invoke('stage.rotate', angle_rad=20 * DEG)
        c2 = np.asarray(acquire(h)['image']['counts'], float).mean(2)
        self.assertGreater(np.abs(c2 - c)[inside].mean(), 5.0)                                     # the brushes rotate with the stage
        check('nematic_schlieren')

    def test_extinction_scan_against_truth(self):
        for az in (0.3, 1.1):
            s = dict(QUARTZ_XTAL, sample_id=f'q{az}', orientation=dict(tilt_rad=math.pi / 2, azimuth_rad=az, spin_rad=0.0))
            h = loaded(s)
            oid = h.invoke('scan.extinction', step_rad=5 * DEG, region=None)['operation_id']
            self.assertEqual(wait(h, oid), 'completed')
            r = h.records[h.operations[oid]['record_id']]
            truth = h.read('evaluator')['truth.center_slow_axis_rad']
            ext_truth = (-truth) % (math.pi / 2)                                                   # stage angle bringing the slow axis onto a polar
            d = abs(((r['scan']['extinction_rad'] - ext_truth) + math.pi / 4) % (math.pi / 2) - math.pi / 4)
            self.assertLess(d, 1.0 * DEG, az)
            self.assertGreater(r['scan']['contrast'], 0.8)
            self.assertEqual(h.stage, 0.0)                                                         # returns to the start angle
        check('extinction_scan_recovers_truth')

    def test_retardation_estimate_is_not_a_phase_identification(self):
        h = loaded(QUARTZ_XTAL)
        h.invoke('stage.rotate', angle_rad=math.pi / 4)
        r = acquire(h)
        w = h.invoke('analysis.retardation', record_id=r['record_id'], region=None, thickness_m=30e-6)
        R_true = h.read('evaluator')['truth.center_retardation_m']
        self.assertLess(abs(w['retardation_m'] - R_true) / R_true, 0.15)
        self.assertFalse(w['unique_identification'])
        ids = [c['substance_id'] for c in w['candidates']]
        self.assertIn('main_sio2', ids)
        self.assertGreater(len(ids), 5)
        h.invoke('compensator.insert', plate='U-TP530')
        r2 = acquire(h)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('analysis.retardation', record_id=r2['record_id'], region=None, thickness_m=None)
        check('retardation_candidates_not_unique')


class Conoscopy(unittest.TestCase):
    def _figure(self, sid, orient, stage=0.0, t=300e-6):
        h = loaded(dict(QUARTZ_XTAL, sample_id='c' + sid, substance_id=sid, thickness_m=t, orientation=orient), objective=2)
        h.invoke('condenser.set_iris', diameter_m=21e-3)
        h.invoke('camera.configure', exposure_s=0.01, gain=1.0)
        h.invoke('bertrand.set_inserted', inserted=True)
        h.invoke('stage.rotate', angle_rad=stage)
        return h, acquire(h)

    def test_uniaxial_optic_axis_figure(self):
        oa = dict(tilt_rad=0.0, azimuth_rad=0.0, spin_rad=0.0)
        h, r = self._figure('main_caco3', oa)
        self.assertIn('conoscopic', r['warnings'])
        c = np.asarray(r['image']['counts'], float).mean(2)
        H, W = c.shape
        axis_vals = [c[H // 2, W // 2 + 20], c[H // 2, W // 2 - 20], c[H // 2 + 20, W // 2], c[H // 2 - 20, W // 2]]
        diag_vals = [c[H // 2 + 14, W // 2 + 14], c[H // 2 - 14, W // 2 - 14]]
        self.assertLess(max(axis_vals), 0.2 * min(diag_vals))                                 # isogyres along the polarizer and analyzer directions
        _, r2 = self._figure('main_caco3', oa, stage=0.5)
        self.assertLess(np.abs(np.asarray(r2['image']['counts'], float).mean(2) - c).mean(), 2.0)   # the optic-axis figure does not move with the stage
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('analysis.retardation', record_id=r['record_id'], region=None, thickness_m=None)
        self.assertEqual(str(cm.exception), 'CONOSCOPIC_IMAGE')
        check('conoscopic_optic_axis_figure')

    def test_biaxial_figure_moves_and_focus_blurs(self):
        o = dict(tilt_rad=0.3, azimuth_rad=0.0, spin_rad=0.0)
        h, a = self._figure('main_ktiopo4', o)
        _, b = self._figure('main_ktiopo4', o, stage=0.6)
        self.assertGreater(np.abs(np.asarray(a['image']['counts'], float) - np.asarray(b['image']['counts'], float)).mean(), 50.0)
        sharp = np.asarray(a['image']['counts'], float).std()
        h.invoke('bertrand.focus', focus_rad=3.0)
        self.assertLess(np.asarray(acquire(h)['image']['counts'], float).std(), sharp)
        check('biaxial_figure_moves_bertrand_focus')


class OpticsAndMechanics(unittest.TestCase):
    def test_objectives_fov_defocus_and_oil(self):
        h = loaded(QUARTZ)
        fovs = []
        for i in range(4):
            fovs.append(h.invoke('objective.select', index=i)['fov_m'])
        self.assertEqual([round(f * 1e3, 4) for f in fovs], [5.5, 2.2, 0.55, 0.22])
        h.invoke('objective.select', index=2)
        sharp = np.asarray(acquire(h)['image']['counts'], float).std()
        h.invoke('focus.move', stage_z_m=h.z + 100e-6)
        blurred = np.asarray(acquire(h)['image']['counts'], float).std()
        self.assertLess(blurred, 0.8 * sharp)
        h.invoke('focus.move', stage_z_m=h._focus_target(h.samples['quartz_section']))
        h.invoke('objective.select', index=3)
        self.assertIn('no_immersion_oil', acquire(h)['warnings'])
        g = loaded(OIL, objective=3)
        self.assertNotIn('no_immersion_oil', acquire(g)['warnings'])
        check('objectives_fov_defocus_oil')

    def test_condenser_top_lens_iris_and_vignetting(self):
        h = loaded(GLASS, objective=0)
        h.invoke('analyzer.set_inserted', inserted=False)
        self.assertIn('field_not_fully_illuminated', acquire(h)['warnings'])
        h.invoke('condenser.set_top_lens', state='out')
        self.assertNotIn('field_not_fully_illuminated', acquire(h)['warnings'])
        h.invoke('objective.select', index=2)
        self.assertIn('condenser_na_low', acquire(h)['warnings'])
        h.invoke('condenser.set_top_lens', state='in')
        wide = center(acquire(h)).mean()
        h.invoke('condenser.set_iris', diameter_m=2e-3)
        self.assertLess(center(acquire(h)).mean(), wide)
        check('condenser_top_lens_iris')

    def test_click_stops_and_scales(self):
        h = loaded()
        self.assertTrue(h.invoke('stage.rotate', angle_rad=45.6 * DEG)['click_stop'])
        self.assertAlmostEqual(h.stage, math.pi / 4, delta=1e-12)
        out = h.invoke('stage.rotate', angle_rad=47.0 * DEG)
        self.assertFalse(out['click_stop'])
        self.assertAlmostEqual(out['indicated_rad'], math.radians(47.0), places=12)
        self.assertEqual(h.invoke('polarizer.set_angle', angle_rad=0.6 * DEG)['angle_rad'], 0.0)
        self.assertAlmostEqual(h.invoke('analyzer.set_angle', angle_rad=90.04 * DEG)['angle_rad'], 90.0 * DEG, delta=1e-9)
        self.assertTrue(h.read()['optics.crossed'])
        h.invoke('analyzer.set_angle', angle_rad=92.0 * DEG)
        self.assertFalse(h.read()['optics.crossed'])
        check('click_stops')

    def test_slide_limits_and_sample_rules(self):
        h = ready()
        h.invoke('sample.provide', **THICK)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('sample.place', sample_id='thick_slide')
        self.assertEqual(str(cm.exception), 'SLIDE_TOO_THICK')
        for bad, code in ((dict(QUARTZ, sample_id='x', substance_id='nope'), 'UNKNOWN_SUBSTANCE'),
                          (dict(LC, sample_id='y', substance_id='main_ktiopo4'), 'NOT_A_LIQUID_CRYSTAL')):
            with self.assertRaises(rt.ActionRejected) as cm:
                h.invoke('sample.provide', **bad)
            self.assertEqual(str(cm.exception), code)
        h.invoke('sample.provide', **QUARTZ)
        h.invoke('sample.place', sample_id='quartz_section')
        h.invoke('sample.provide', **GLASS)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('sample.place', sample_id='glass')
        v0 = h.read('evaluator')
        with self.assertRaises(rt.ActionRejected):
            h.invoke('sample.remove', sample_id='glass')
        self.assertEqual(h.read('evaluator'), v0)
        h.invoke('sample.remove', sample_id='quartz_section')
        self.assertIsNone(h.read()['sample.on_stage'])

    def test_camera_saturation_lamp_and_power(self):
        h = loaded(GLASS)
        h.invoke('analyzer.set_inserted', inserted=False)
        h.invoke('camera.configure', exposure_s=2.0, gain=4.0)
        self.assertIn('saturated', acquire(h)['warnings'])
        h.invoke('power.press_switch')
        self.assertFalse(h.read()['power.enabled'])
        r = acquire(h)
        self.assertIn('lamp_off', r['warnings'])
        self.assertLess(r['image']['mean_counts'][1], 5.0)
        h.invoke('power.press_switch')
        self.assertTrue(h.read()['illumination.lamp_on'])
        with self.assertRaises(rt.ActionRejected):
            configured(mains_voltage_v=60.0).invoke('power.set', enabled=True)


class FaultsAndOperations(unittest.TestCase):
    def test_faults(self):
        h = loaded(GLASS)
        h.invoke('analyzer.set_inserted', inserted=False)
        h.invoke('fault.inject', fault='lamp_failure', active=True)
        self.assertFalse(h.read()['illumination.lamp_on'])
        self.assertLess(acquire(h)['image']['mean_counts'][1], 5.0)
        h.invoke('fault.inject', fault='lamp_failure', active=False)
        g = loaded(QUARTZ_XTAL)
        g.invoke('stage.rotate', angle_rad=0.2)
        oid = g.invoke('scan.extinction', step_rad=10 * DEG, region=None)['operation_id']
        g.step(15.0)
        self.assertNotAlmostEqual(g.stage, 0.2, places=6)
        g.invoke('fault.inject', fault='camera_disconnected', active=True)
        self.assertEqual(g.operations[oid]['phase'], 'failed')
        self.assertAlmostEqual(g.stage, 0.2, delta=1e-12)
        self.assertGreater(len(g.records['SC0001']['scan']['stage_rad']), 0)
        with self.assertRaises(rt.ActionRejected) as cm:
            g.invoke('camera.acquire')
        self.assertEqual(str(cm.exception), 'CAMERA_FAULT')
        v = loaded(QUARTZ)
        v.invoke('fault.inject', fault='vibration', active=True)
        self.assertLess(np.asarray(acquire(v)['image']['counts'], float).std(), np.asarray(acquire(loaded(QUARTZ))['image']['counts'], float).std())

    def test_cancellation_keeps_partial_scan_and_restores_the_stage(self):
        h = loaded(QUARTZ_XTAL)
        h.register_principal('ag', 'agent')
        s = h.session('ag')
        r = s.invoke(instance_id=rt.INSTANCE, capability_id='scan.extinction', arguments=dict(step_rad=10 * DEG, region=None), request_id='scan1')
        self.assertEqual(r['status'], 'accepted')
        h.step(12.0)
        c = s.cancel(r['operation_id'], 'c1')
        self.assertEqual(c['result']['phase'], 'cancelled')
        rec = h.records['SC0001']
        self.assertEqual(rec['status'], 'cancelled')
        self.assertEqual(len(rec['scan']['stage_rad']), 5)
        self.assertEqual(h.stage, 0.0)
        self.assertIsNone(h.op)
        with self.assertRaises(rt.ActionRejected):
            s.cancel(r['operation_id'], 'c2')

    def test_operation_blocks_manual_parts(self):
        h = loaded()
        h.invoke('camera.acquire')
        for k, a in (('stage.rotate', dict(angle_rad=1.0)), ('objective.select', dict(index=0)), ('focus.move', dict(stage_z_m=0.0)),
                     ('analyzer.set_inserted', dict(inserted=False)), ('camera.acquire', {})):
            v0 = h.read('evaluator')
            with self.assertRaises(rt.ActionRejected) as cm:
                h.invoke(k, **a)
            self.assertEqual(str(cm.exception), 'OPERATION_RUNNING')
            self.assertEqual(h.read('evaluator'), v0)


class RecordsSessions(unittest.TestCase):
    def test_two_instances_are_isolated(self):
        a, b = loaded(), loaded()
        acquire(a)
        a.invoke('stage.rotate', angle_rad=1.0)
        self.assertEqual(b.records, {})
        self.assertEqual(b.stage, 0.0)
        self.assertEqual(b.ledger()['frames'], 0.0)

    def test_agent_truth_isolation(self):
        h = loaded(QUARTZ_XTAL)
        h.register_principal('ag', 'agent')
        h.register_principal('ev', 'evaluator')
        s, e = h.session('ag'), h.session('ev')
        obs = s.read(instance_id=rt.INSTANCE)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        self.assertIn('truth.samples', e.read(instance_id=rt.INSTANCE)['observations'])
        d = s.describe(instance_id=rt.INSTANCE)
        self.assertFalse([k for k in d['state_slots'] if k.startswith('truth.')])
        self.assertNotIn('ledger.read', d['capabilities'])
        self.assertEqual(s.invoke(instance_id=rt.INSTANCE, capability_id='ledger.read', arguments={}, request_id='l')['error']['code'], 'ACCESS_DENIED')
        r = s.invoke(instance_id=rt.INSTANCE, capability_id='camera.acquire', arguments={}, request_id='a1')
        h.step(1.0)
        res = s.invoke(instance_id=rt.INSTANCE, capability_id='measurement.result', arguments=dict(record_id='IM0001'), request_id='m1')['result']
        self.assertEqual(res['template_source']['substances'], [])                                  # the sample identity is truth
        self.assertEqual(e.invoke(instance_id=rt.INSTANCE, capability_id='measurement.result', arguments=dict(record_id='IM0001'), request_id='m1')['result']['template_source']['substances'], ['main_sio2'])
        self.assertEqual(s.poll(r['operation_id'])['status'], 'succeeded')   # protocol.md status names

    def test_requests_are_idempotent_and_foreign_records_hidden(self):
        h = loaded()
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        s1, s2 = h.session('a1'), h.session('a2')
        r1 = s1.invoke(instance_id=rt.INSTANCE, capability_id='stage.rotate', arguments=dict(angle_rad=0.5), request_id='x')
        self.assertEqual(s1.invoke(instance_id=rt.INSTANCE, capability_id='stage.rotate', arguments=dict(angle_rad=0.5), request_id='x'), r1)
        self.assertEqual(s1.invoke(instance_id=rt.INSTANCE, capability_id='stage.rotate', arguments=dict(angle_rad=0.6), request_id='x')['error']['code'],
                         'REQUEST_CONFLICT')
        s1.invoke(instance_id=rt.INSTANCE, capability_id='camera.acquire', arguments={}, request_id='acq')
        self.assertEqual(s2.invoke(instance_id=rt.INSTANCE, capability_id='stage.rotate', arguments=dict(angle_rad=0.1), request_id='y')['error']['code'], 'ACCESS_DENIED')
        h.step(1.0)
        self.assertEqual(s2.invoke(instance_id=rt.INSTANCE, capability_id='records.list', arguments={}, request_id='r')['result']['records'], [])
        self.assertEqual(s2.invoke(instance_id=rt.INSTANCE, capability_id='measurement.result', arguments=dict(record_id='IM0001'), request_id='m')['error']['code'],
                         'ACCESS_DENIED')
        self.assertIsNone(s2.read(instance_id=rt.INSTANCE)['observations']['result.last_record_id']['value'])
        self.assertEqual(s1.read(instance_id=rt.INSTANCE)['observations']['result.last_record_id']['value'], 'IM0001')

    def test_queries_and_unsupported(self):
        h = loaded()
        acquire(h)
        self.assertEqual(h.invoke('records.list')['records'], ['IM0001'])
        self.assertTrue(h.invoke('events.read', since_s=0.0)['events'])
        self.assertEqual(h.invoke('ledger.read')['frames'], 1.0)
        p = h.invoke('provenance.read')
        self.assertGreaterEqual(p['substances'], 50)
        for k in rt.UNSUPPORTED:
            with self.assertRaises(rt.ActionRejected):
                h.invoke(k)
        h.invoke('sim.step', dt_s=5.0)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('measurement.result', record_id='nope')


# ---------------------------------------------------------------- declared effects over scenario states
_SC = {}


def _build(name):
    if name == 'fresh':
        return rt.EvidentBx53pRuntime()
    if name == 'off':
        return configured()
    if name == 'ready':
        h = ready()
        for s in (QUARTZ, QUARTZ_XTAL, GLASS, LC, THICK):
            h.invoke('sample.provide', **s)
        return h
    if name == 'loaded':
        h = _scenario('ready')
        h.invoke('sample.place', sample_id='quartz_plate')
        h.invoke('focus.move', stage_z_m=0.0)
        return h
    if name == 'imaged':
        h = _scenario('loaded')
        acquire(h)
        return h
    if name == 'acquiring':
        h = _scenario('loaded')
        h.invoke('camera.acquire')
        return h
    if name == 'scanning':
        h = _scenario('loaded')
        h.invoke('scan.extinction', step_rad=20 * DEG, region=None)
        h.step(5.0)
        return h
    if name == 'lamp_fault':
        h = _scenario('loaded')
        h.invoke('fault.inject', fault='lamp_failure', active=True)
        return h
    if name == 'camera_fault':
        h = _scenario('loaded')
        h.invoke('fault.inject', fault='camera_disconnected', active=True)
        return h
    if name == 'mains_fault':
        h = _scenario('loaded')
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0))
        return h
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'off', 'ready', 'loaded', 'imaged', 'acquiring', 'scanning', 'lamp_fault', 'camera_fault', 'mains_fault']
STEP_DTS = (0.02, 1.0, 3.0, 30.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(room_k=300.15, mains_voltage_v=60.0, noise_seed=1)],
        'sample.provide': [dict(QUARTZ, sample_id='new')], 'sample.place': [dict(sample_id='quartz_section'), dict(sample_id='lc_cell')],
        'sample.remove': [dict(sample_id='quartz_plate')], 'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_switch': [{}],
        'illumination.set_level': [dict(level=0.0), dict(level=1.0)], 'polarizer.set_angle': [dict(angle_rad=0.3), dict(angle_rad=0.0)],
        'analyzer.set_inserted': [dict(inserted=False), dict(inserted=True)], 'analyzer.set_angle': [dict(angle_rad=0.2), dict(angle_rad=math.pi / 2)],
        'compensator.insert': [dict(plate='U-TP530'), dict(plate='none')], 'condenser.set_top_lens': [dict(state='out'), dict(state='in')],
        'condenser.set_iris': [dict(diameter_m=3e-3)], 'objective.select': [dict(index=0), dict(index=3)],
        'stage.rotate': [dict(angle_rad=0.5), dict(angle_rad=math.pi / 4)], 'focus.move': [dict(stage_z_m=0.002)],
        'camera.configure': [dict(exposure_s=0.2, gain=2.0)], 'camera.acquire': [{}], 'scan.extinction': [dict(step_rad=30 * DEG, region=None)],
        'bertrand.set_inserted': [dict(inserted=True), dict(inserted=False)], 'bertrand.focus': [dict(focus_rad=1.0)],
        'fault.inject': [dict(fault=f, active=True) for f in rt.FAULTS] + [dict(fault=f, active=False) for f in rt.FAULTS],
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


class ReviewRound1Fixes(unittest.TestCase):
    """Regression tests for the round-1 runtime review findings."""
    def test_conoscopic_angles_scale_with_na_over_n(self):
        def first_dark_ring(obj, oil):
            s = dict(CALCITE_XTAL, sample_id='c', thickness_m=100e-6, orientation=dict(tilt_rad=0.0, azimuth_rad=0.0, spin_rad=0.0), immersion_oil=oil)
            h = loaded(s, objective=obj)
            h.invoke('analyzer.set_angle', angle_rad=math.pi / 2)
            h.invoke('bertrand.set_inserted', inserted=True)
            h.invoke('condenser.set_iris', diameter_m=0.021)
            Y = h._linear_image(h.stage, []).sum(-1)
            H, W = Y.shape
            rs = np.linspace(0, 1, 200)
            v = np.array([Y[int(round((H - 1) / 2 - r * (W - 1) / 2 * math.sin(math.pi / 4))), int(round((W - 1) / 2 + r * (W - 1) / 2 * math.cos(math.pi / 4)))] for r in rs])
            k = next(i for i in range(2, len(v) - 2) if v[i] < v[i - 1] and v[i] <= v[i + 1] and v[i] < 0.3 * v.max())
            return rs[k]
        ratio = first_dark_ring(3, True) / first_dark_ring(2, False)
        self.assertAlmostEqual(ratio, 0.75 / 1.30, delta=0.08)          # sin(theta_c) = NA r / n: the same ring at r proportional to 1 / NA
        check('conoscopic_ring_radius_scales_with_1_over_na', ratio=ratio, expected=0.75 / 1.30)

    def test_scan_step_and_fit(self):
        h = loaded(QUARTZ_XTAL)
        h.invoke('analyzer.set_angle', angle_rad=math.pi / 2)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('scan.extinction', step_rad=45 * DEG, region=None)       # 45 deg steps see 4 theta at two phases only
        for slow in (10, 20, 30):
            h = loaded(dict(QUARTZ_XTAL, orientation=dict(tilt_rad=math.pi / 2, azimuth_rad=slow * DEG, spin_rad=0.0)))
            h.invoke('analyzer.set_angle', angle_rad=math.pi / 2)
            oid = h.invoke('scan.extinction', step_rad=40 * DEG, region=None)['operation_id']
            self.assertEqual(wait(h, oid), 'completed')
            ext = h.records[h.operations[oid]['record_id']]['scan']['extinction_rad']
            truth = (math.pi / 2 - slow * DEG) % (math.pi / 2)
            self.assertLess(abs((ext - truth + math.pi / 4) % (math.pi / 2) - math.pi / 4), 2 * DEG, (slow, ext))
        check('scan_45deg_step_refused_and_40deg_fit_correct')

    def test_scan_with_lamp_failure_gives_no_fit_and_ledger_counts_the_way_back(self):
        h = loaded(QUARTZ_XTAL)
        h.invoke('analyzer.set_angle', angle_rad=math.pi / 2)
        oid = h.invoke('scan.extinction', step_rad=20 * DEG, region=None)['operation_id']
        h.step(6.0)
        h.invoke('fault.inject', fault='lamp_failure', active=True)
        self.assertEqual(wait(h, oid), 'completed')
        sc = h.records[h.operations[oid]['record_id']]
        self.assertIsNone(sc['scan']['extinction_rad'])
        self.assertIn('lamp_off', sc['warnings'])
        h2 = loaded(QUARTZ_XTAL)
        oid = h2.invoke('scan.extinction', step_rad=20 * DEG, region=None)['operation_id']
        h2.step(6.5)                                                    # stage turned by 20 or 40 deg
        turned = abs(h2.stage - 0.0)
        h2._cancel_op(h2.operations[oid])
        self.assertAlmostEqual(h2.ledger()['stage_rotation_rad'], 2 * turned, places=9)
        self.assertEqual(h2.stage, 0.0)
        check('scan_lamp_off_no_fit_and_return_trip_in_ledger', turned_rad=turned)

    def test_retardation_candidates_and_saturation(self):
        for d in (10e-6, 30e-6, 50e-6):
            h = loaded(dict(QUARTZ_XTAL, thickness_m=d))
            h.invoke('analyzer.set_angle', angle_rad=math.pi / 2)
            h.invoke('stage.rotate', angle_rad=45 * DEG)
            rec = acquire(h)
            truth = h.read('evaluator')['truth.center_retardation_m']
            out = h.invoke('analysis.retardation', record_id=rec['record_id'], region=None, thickness_m=d)
            self.assertLess(min(abs(x - truth) for x in [out['retardation_m']] + out['alternatives_m']), 15e-9, (d, out['retardation_m'], out['alternatives_m']))
            self.assertIn('main_sio2', [c['substance_id'] for c in out['candidates']])
            self.assertFalse(out['unique_identification'])
        h = loaded(dict(QUARTZ_XTAL, thickness_m=30e-6))
        h.invoke('analyzer.set_angle', angle_rad=math.pi / 2)
        h.invoke('stage.rotate', angle_rad=45 * DEG)
        h.invoke('camera.configure', exposure_s=1.0, gain=4.0)
        rec = acquire(h)
        out = h.invoke('analysis.retardation', record_id=rec['record_id'], region=None, thickness_m=30e-6)
        self.assertIsNone(out['retardation_m'])
        self.assertEqual(out['warnings'], ['saturated_region'])
        check('retardation_candidates_cover_all_orders_and_saturation_refused')

    def test_objective_front_lens_clearance(self):
        h = loaded(QUARTZ, objective=3)
        z0, before = h.z, h.read('evaluator')
        for z in (z0 + 0.3e-3, z0 + 3e-3):
            with self.assertRaises(rt.ActionRejected) as cm:
                h.invoke('focus.move', stage_z_m=z)
            self.assertEqual(str(cm.exception), 'OBJECTIVE_COLLISION')
        self.assertEqual(h.read('evaluator'), before)
        h.invoke('focus.move', stage_z_m=z0 + 0.1e-3)                    # inside the 0.2 mm working distance
        h = loaded(QUARTZ, objective=1)
        h.invoke('focus.move', stage_z_m=h.z + 2e-3)                    # 10x: 10 mm working distance
        with self.assertRaises(rt.ActionRejected):
            h.invoke('objective.select', index=2)                       # 40x (0.51 mm) would hit the slide
        check('objective_working_distance_clearance')

    def test_specimen_turns_with_the_stage(self):
        rng = np.random.default_rng(3)
        s = rt.make_sample(dict(kind='thin_section', substance_id='main_sio2', thickness_m=30e-6, grains=40), rng)
        a = rt.render_linear(s, 0.0, 0.0, math.pi / 2, True, 0.0)
        b = rt.render_linear(s, math.pi, 0.0, math.pi / 2, True, 0.0)
        np.testing.assert_allclose(b[::-1, ::-1], a, atol=1e-9)         # a half turn maps the mosaic onto itself rotated by 180 deg
        c = rt.render_linear(s, math.pi / 2, 0.0, math.pi / 2, True, 0.0)
        self.assertGreater(float(np.abs(c - a).mean()), 1e-3)
        seeds = rt.make_sample(dict(kind='thin_section', substance_id='main_sio2', thickness_m=30e-6, grains=40), np.random.default_rng(3))
        H, W = a.shape[:2]
        pts = np.array(min((g['xy'] for g in seeds['grains']), key=lambda q: abs(math.hypot(*q) - 0.4)))   # a seed near the centre; slide (x, y) appears at R(+stage)(x, y)
        ang = math.radians(90)
        rot = np.array([math.cos(ang) * pts[0] - math.sin(ang) * pts[1], math.sin(ang) * pts[0] + math.cos(ang) * pts[1]])
        col = lambda p: (int(round(p[0] * (W - 1) / 2 + (W - 1) / 2)), int(round(-p[1] * (W - 1) / 2 + (H - 1) / 2)))  # noqa: E731  (+y up)
        c0, c1 = col(pts), col(rot)
        self.assertTrue(0 <= c0[1] < H and 0 <= c1[1] < H and 0 <= c0[0] < W and 0 <= c1[0] < W, (c0, c1))
        np.testing.assert_allclose(c[c1[1], c1[0]], a[c0[1], c0[0]], atol=1e-9)   # the seed's grain (same colour after 90 deg) moves counter-clockwise
        check('specimen_mosaic_turns_with_the_stage')


    def test_round2_specimen_height_image_axes_and_dry_oil_objective(self):
        thick = dict(QUARTZ_XTAL, sample_id='thick_xtal', thickness_m=0.9e-3)
        h = loaded(thick, objective=1)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('objective.select', index=2)                       # 40x: 0.51 mm working distance < 0.9 mm crystal
        self.assertEqual(str(cm.exception), 'OBJECTIVE_COLLISION')
        tilted = dict(QUARTZ_XTAL, sample_id='tilted', substance_id='main_caco3', thickness_m=100e-6, orientation=dict(tilt_rad=0.35, azimuth_rad=0.0, spin_rad=0.0))
        h = loaded(tilted, objective=2)
        h.invoke('analyzer.set_angle', angle_rad=math.pi / 2)
        h.invoke('bertrand.set_inserted', inserted=True)
        h.invoke('condenser.set_iris', diameter_m=0.021)

        def melatope_angle(stage):
            Y = h._linear_image(stage, []).sum(-1)
            H, W = Y.shape
            yy, xx = np.mgrid[0:H, 0:W]
            x, y = (xx - (W - 1) / 2) / ((W - 1) / 2), (yy - (H - 1) / 2) / ((W - 1) / 2)
            w = np.where((x ** 2 + y ** 2 < 0.9) & (x ** 2 + y ** 2 > 0.02), Y.max() - Y, 0.0) ** 4    # the dark isogyre cross
            return math.atan2(float((w * y).sum()), float((w * x).sum()))
        turn = (melatope_angle(math.pi / 2) - melatope_angle(0.0)) % (2 * math.pi)   # measured in pixel rows (downward): counter-clockwise = -90 deg
        self.assertLess(abs(turn - 3 * math.pi / 2), 20 * DEG, turn)     # the figure turns counter-clockwise on screen with the stage (joint about +z)
        dry = dict(QUARTZ_XTAL, sample_id='dry', substance_id='main_caco3', thickness_m=100e-6, orientation=dict(tilt_rad=0.0, azimuth_rad=0.0, spin_rad=0.0))
        h = loaded(dry, objective=3)
        h.invoke('bertrand.set_inserted', inserted=True)
        h.invoke('condenser.set_iris', diameter_m=0.021)
        w = []
        lin = h._conoscopic(h._obj(), h.stage, 0.0, w)
        self.assertIn('no_immersion_oil', w)
        H, W = lin.shape[:2]
        yy, xx = np.mgrid[0:H, 0:W]
        r = np.hypot(xx - (W - 1) / 2, yy - (H - 1) / 2) / ((W - 1) / 2)
        self.assertLess(float(lin[(r > 1 / 1.30 + 0.05) & (r <= 1)].max()), 1e-3 * float(lin.max()))   # no ray beyond n sin(theta) = 1 through air
        check('round2_clearance_axes_and_dry_oil_objective', melatope_turn=turn)

    def test_contract_semantics_of_the_fixes(self):
        meta = _meta()
        self.assertEqual(meta['capabilities']['analysis.retardation']['output_schema']['properties']['unique_identification'].get('enum'), [False])
        for k in ('stage.angle_rad', 'polarizer.angle_rad', 'analyzer.angle_rad', 'objective.nosepiece_rad', 'focus.stage_z_m'):
            self.assertTrue(meta['state_slots'][k].get('reference_conditions'), k)
        check('never_unique_and_reference_frames_declared')


    def test_optic_sign_with_the_tint_plate_textbook_rule(self):
        for sid, d, positive in (('main_sio2', 300e-6, True), ('main_caco3', 30e-6, False)):
            s = dict(QUARTZ_XTAL, sample_id='x', substance_id=sid, thickness_m=d, orientation=dict(tilt_rad=0.0, azimuth_rad=0.0, spin_rad=0.0))
            h = loaded(s, objective=2)
            h.invoke('analyzer.set_angle', angle_rad=math.pi / 2)
            h.invoke('bertrand.set_inserted', inserted=True)
            h.invoke('condenser.set_iris', diameter_m=0.021)
            h.invoke('compensator.insert', plate='U-TP530')
            lin = h._linear_image(h.stage, [])
            H, W = lin.shape[:2]
            yy, xx = np.mgrid[0:H, 0:W]
            x, y = (xx - (W - 1) / 2) / ((W - 1) / 2), (yy - (H - 1) / 2) / ((W - 1) / 2)
            ring = (np.hypot(x, y) > 0.08) & (np.hypot(x, y) < 0.3)
            ne = lin[ring & (x > 0) & (y < 0)].mean(0)                     # north = decreasing image rows
            self.assertEqual(bool(ne[2] > ne[0]), positive, (sid, ne))    # positive crystal: blue NE / SW next to the melatope
        check('optic_sign_textbook_rule_with_u_tp530')


class ZDeclaredEffects(unittest.TestCase):
    def test_immediate_and_on_step_effects_are_declared(self):
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
                        if _leaf_changed(v0[k], v1[k]) and k not in rt.EFFECTS.get(cap, []):
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
        self.assertFalse([s for v in rt.ON_STEP.values() for s in v if rt.SLOTS[s]['origin'] == 'setpoint' and s != 'stage.angle_rad'])
        check('declared_effects_scenarios')

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


class ZZRejectionCodes(unittest.TestCase):
    def test_every_observed_rejection_code_is_declared(self):
        undeclared = {k: sorted(v - set(rt.CAP_ERRORS.get(k, [])) - {'INVALID_ARGUMENT', 'UNSUPPORTED', 'UNKNOWN_CAPABILITY'}) for k, v in REJECTIONS.items()}
        self.assertEqual({k: v for k, v in undeclared.items() if v}, {})


if __name__ == '__main__':
    unittest.main()
