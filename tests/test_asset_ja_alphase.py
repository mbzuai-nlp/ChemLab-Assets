"""tests for CHEM-157 ja_alphase (J.A. Woollam alpha-SE): python3 -m unittest tests/test_asset_ja_alphase.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances
are isolated, agent/evaluator truth isolation (truth slots, template substances of the true sample, fault events, other agents' records
and operations), conservation (electrical energy = integral of the lamp and electronics power, lamp hours, stage and arm travel, liquid
volume added = drained + in the cell), real return values validated against the declared schemas (asset_tool lint_schema / validate_value),
asynchronous completion and cancellation remnants, host-step independence, declared immediate and on_step effects (scenario states, short
and long windows); plus the instrument (initialisation, lamp warm-up, vacuum chuck, manual angle change with the plunger, Z stage and
alignment, collision guard, liquid cell, faults) and the physics: exact Fresnel / Airy / Abeles optics, Brewster angle, Kramers-Kronig of the
Tauc-Lorentz model, Bruggeman limits, pseudo dielectric inversion, the straight-through accuracy and the 2 nm SiO2 repeatability of the
specification, calibration against the hidden angle / azimuth / retardance errors, %R against the model reflectance, Mueller-matrix
structure of an isotropic sample, depolarization and backside reflections, and the CompleteEASE-like analysis (prefit, multi-angle fit,
confidence limits, uniqueness, Tauc-Lorentz and library models).
"""
import copy
import json
import math
import sys
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import ja_alphase as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}
_SC = {}


def _meta():
    if not _META:
        _META.update(rt.export_ja_alphase_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.JaAlphaseRuntime.invoke


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


rt.JaAlphaseRuntime.invoke = _instrumented_invoke

MZ = rt.MEASURE_POINT_M[2]
PMMA = copy.deepcopy(rt.SAMPLE_EXAMPLE)                   # 120 nm PMMA over 1.8 nm SiO2 on a 2 in Si wafer
CAL = copy.deepcopy(rt.CALIBRATION_WAFER)                  # 25 nm thermal oxide on Si
OX2 = dict(sample_id='ox2_si', kind='film_stack', shape='disc', size_m=[0.1, 0.1], substrate_thickness_m=0.000525, substrate={'library': 'si'},
           layers=[dict(material={'library': 'sio2'}, thickness_m=2e-9)], roughness_m=0.0, backside='rough')
BARE = dict(sample_id='bare_si', kind='film_stack', shape='disc', size_m=[0.0508, 0.0508], substrate_thickness_m=0.000275, substrate={'library': 'si'}, layers=[],
            roughness_m=0.0, backside='rough')
SLIDE = dict(sample_id='tio2_slide', kind='film_stack', shape='rectangle', size_m=[0.0762, 0.0254], substrate_thickness_m=0.001, substrate={'library': 'soda_lime'},
             layers=[dict(material={'library': 'tio2'}, thickness_m=80e-9)], roughness_m=2e-9, backside='polished')
W200 = dict(sample_id='wafer_200', kind='film_stack', shape='disc', size_m=[0.2, 0.2], substrate_thickness_m=0.000725, substrate={'library': 'si'},
            layers=[dict(material={'library': 'sio2'}, thickness_m=100e-9)], roughness_m=0.0, backside='rough')
BLOCK = dict(sample_id='glass_block', kind='film_stack', shape='rectangle', size_m=[0.025, 0.025], substrate_thickness_m=0.006, substrate={'library': 'soda_lime'},
             layers=[], roughness_m=0.0, backside='rough')
TINY = dict(sample_id='tiny', kind='film_stack', shape='disc', size_m=[0.006, 0.006], substrate_thickness_m=0.0005, substrate={'library': 'si'}, layers=[],
            roughness_m=0.0, backside='rough')
MGF2 = dict(sample_id='mgf2_plate', kind='film_stack', shape='rectangle', size_m=[0.025, 0.025], substrate_thickness_m=0.001, substrate={'library': 'mgf2'},
            layers=[], roughness_m=0.0, backside='rough')
FIT = dict(record_ids=['SE0001'], model_id='cauchy_on_si', parameters={}, fit=['film.thickness_m', 'film.A', 'film.B'], thickness_prefit=True)


def configured(seed=1, samples=(PMMA, CAL), **plant):
    h = rt.JaAlphaseRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    for s in samples:
        h.invoke('sample.provide', **s)
    return h


def ready(seed=1, warm=600.0, samples=(PMMA, CAL), **plant):
    h = configured(seed, samples, **plant)
    h.invoke('power.set', enabled=True)
    h.step(warm)
    return h


def settle(h, limit=600.0, dt=0.5):
    t = 0.0
    while (h._stage_moving() or h._arm_seat_at is not None) and t < limit:
        h.step(dt)
        t += dt
    assert not (h._stage_moving() or h._arm_seat_at is not None)
    return h


def wait(h, op, tmax=900.0, dt=0.5):
    t = 0.0
    while h.operations[op]['phase'] == 'running' and t < tmax:
        h.step(dt)
        t += dt
    assert h.operations[op]['phase'] != 'running', op
    return h.records[h.operations[op]['record_id']]


def held(sample=PMMA, seed=1, offset=(0.0, 0.0), samples=(PMMA, CAL), **plant):
    h = ready(seed, samples=tuple(samples) + ((sample,) if sample not in samples else ()), **plant)
    h.invoke('sample.place', sample_id=sample['sample_id'], offset_x_m=offset[0], offset_y_m=offset[1])
    h.invoke('vacuum.set', on=True)
    return h


def measure(h, mode='standard', alignment='standard', fixed_height_m=None, model_id=None):
    op = h.invoke('measure', mode=mode, alignment=alignment, fixed_height_m=fixed_height_m, model_id=model_id)['operation_id']
    return wait(h, op)


def straight_through(h):
    settle(h)
    h.invoke('aoi.set', position='90')
    settle(h)
    return measure(h, alignment='none')


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


def _into(h, phase, frac=0.5):
    """Advance event-exactly to the given phase of the running operation and then to `frac` of its duration."""
    for _ in range(20):
        o = h.op
        assert o is not None, phase
        end = o['ends'][o['i']]
        if o['phases'][o['i']][0] == phase:
            h.step(max(0.0, (end - h.t) * frac))
            return h
        h.step(max(0.0, end - h.t) + 1e-9)
    raise AssertionError(phase)


def _ncs(psi, delta):
    return rt.ncs_of(np.asarray(psi), np.asarray(delta))


def _close(a, b, rel=1e-8):
    """Nested equality with a relative tolerance for floats (host-step chopping only reorders floating-point sums)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], rel) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y, rel) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return abs(a - b) <= rel * max(abs(a), abs(b), 1e-300)
    return a == b


class ContractAndSchemas(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.ja_alphase')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-157')
        self.assertEqual(len(rt.wavelengths_nm()), 180)
        self.assertAlmostEqual(rt.wavelengths_nm()[0], 380.0, places=9)
        self.assertAlmostEqual(rt.wavelengths_nm()[-1], 900.0, places=9)
        e = rt.HC_EV_NM / rt.wavelengths_nm()
        self.assertTrue(np.allclose(np.diff(e[::-1]), rt.PIXEL_WIDTH_EV))                   # pixels equal in photon energy

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
        for name in ('fresh', 'ready', 'acquiring', 'measured', 'cell_filled', 'calibrated'):
            v = _scenario(name).read('evaluator')
            for k, x in v.items():
                self.assertEqual(asset_tool.validate_value(x, m['state_slots'][k]['value_schema']), [], (name, k))
        h = _scenario('measured')
        for rid in h.records:
            self.assertEqual(asset_tool.validate_value(h._out(h.records[rid]), m['capabilities']['measurement.result']['output_schema']), [], rid)

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
            codes = [e['code'] for e in c['errors']]
            self.assertEqual(len(codes), len(set(codes)), k)
            for e in c['errors']:
                self.assertIn(e['code'], rt.ERROR_ZH, (k, e['code']))
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
        h = _scenario('acquiring')
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        t, rev, led = h.t, h.revision, h.ledger()
        for i in range(3):
            h.read('evaluator'); s.read(instance_id=h.instance_id); s.describe(instance_id=h.instance_id)
            s.invoke(instance_id=h.instance_id, capability_id='events.read', arguments=dict(since_s=0.0), request_id=f'e{i}')
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
        """Power-up and warm-up, an angle change, a Z move, a calibration and a fitted measurement advanced in single host steps and in irregular small
        steps give identical records, positions and ledgers (phases, motions and the lamp are cut exactly at events)."""
        def run(chunks):
            h = configured()

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
            h.invoke('power.set', enabled=True)
            adv(40.0)
            h.invoke('aoi.set', position='65')
            adv(5.0)
            h.invoke('stage.move_z', z_m=MZ - 0.005)
            adv(6.0)
            h.invoke('sample.place', sample_id='cal_wafer', offset_x_m=0.0, offset_y_m=0.0)
            h.invoke('vacuum.set', on=True)
            op = h.invoke('calibration.run')['operation_id']
            adv(h.op['duration_s'] + 0.5)
            h.invoke('vacuum.set', on=False)
            h.invoke('sample.remove', sample_id='cal_wafer')
            h.invoke('sample.place', sample_id='pmma_on_si', offset_x_m=0.0, offset_y_m=0.0)
            h.invoke('vacuum.set', on=True)
            op2 = h.invoke('measure', mode='fast', alignment='standard', fixed_height_m=None, model_id='cauchy_on_si')['operation_id']
            adv(h.op['duration_s'] + 0.5)
            self.assertEqual(h.operations[op]['phase'], 'completed')
            self.assertEqual(h.operations[op2]['phase'], 'completed')
            return h, h.records[h.operations[op]['record_id']], h.records[h.operations[op2]['record_id']]
        a, ca, ra = run(False)
        b, cb, rb = run(True)
        self.assertTrue(_close(ca['calibration'], cb['calibration']))
        self.assertTrue(_close(ra['psi_rad'], rb['psi_rad'], 1e-10))
        self.assertTrue(_close(ra['delta_rad'], rb['delta_rad'], 1e-10))
        self.assertTrue(_close(ra['fit']['parameters'], rb['fit']['parameters'], 1e-6))
        self.assertAlmostEqual(a.z, b.z, places=12)
        self.assertAlmostEqual(a.arm_el, b.arm_el, places=12)
        for k, v in a.ledger().items():
            self.assertAlmostEqual(v, b.ledger()[k], delta=1e-9 * max(1.0, abs(v)), msg=k)

    def test_reads_do_not_change_later_results(self):
        def run(reads):
            h = held()
            op = h.invoke('measure', mode='standard', alignment='standard', fixed_height_m=None, model_id='cauchy_on_si')['operation_id']
            for i in range(30):
                if reads:
                    h.read('evaluator'); h.invoke('events.read', since_s=0.0); h.invoke('records.list'); h.invoke('ledger.read'); h.invoke('models.list')
                h.step(0.7)
            rec = wait(h, op)
            return rec['psi_rad'], rec['fit']['mse'], h.ledger()
        self.assertEqual(run(False), run(True))

    def test_set_points_only_write_targets(self):
        h = _scenario('held')
        z0 = h.z
        out = h.invoke('stage.move_z', z_m=z0 + 0.004)
        self.assertEqual(h.z, z0)
        self.assertEqual(h.read()['stage.z_target_m'], z0 + 0.004)
        self.assertAlmostEqual(out['duration_s'], 0.004 / rt.STAGE_Z_SPEED_M_S)
        h.step(0.5)
        self.assertAlmostEqual(h.z, z0 + 0.5 * rt.STAGE_Z_SPEED_M_S, places=12)
        self.assertTrue(h.read()['stage.moving'])
        settle(h)
        e0 = h.arm_el
        out = h.invoke('aoi.set', position='75')
        self.assertEqual(h.arm_el, e0)
        self.assertIsNone(h.read()['aoi.position'])
        self.assertTrue(h.read()['aoi.moving'])
        self.assertAlmostEqual(out['duration_s'], math.radians(5.0) / rt.ARM_SPEED_RAD_S + 2.0)
        h.step(1.0)
        self.assertEqual(h.arm_el, e0)                                        # the plunger is pulled first
        h.step(0.25)
        self.assertAlmostEqual(h.arm_el, e0 - 0.25 * rt.ARM_SPEED_RAD_S, places=12)
        settle(h)
        self.assertEqual(h.read()['aoi.position'], '75')
        self.assertAlmostEqual(h.arm_el, math.radians(15.0), places=12)
        h2 = configured()
        h2.invoke('power.set', enabled=True)
        self.assertEqual(h2.read()['system.state'], 'booting')
        h2.step(rt.BOOT_S - 0.01)
        self.assertEqual(h2.read()['system.state'], 'booting')
        h2.step(0.02)
        self.assertEqual(h2.read()['system.state'], 'ready')


class PowerLampVacuum(unittest.TestCase):
    def test_lamp_warm_up_and_ageing(self):
        h = configured()
        h.invoke('power.set', enabled=True)
        age = 1.0 - rt.LAMP_END_OF_LIFE_LOSS * rt.PLANT_EXAMPLE['lamp_age_s'] / rt.LAMP_RATED_S
        self.assertAlmostEqual(h.read('evaluator')['truth.lamp_intensity_rel'], age * (1 - rt.LAMP_WARMUP_DEFICIT), places=12)
        h.step(90.0)
        age = 1.0 - rt.LAMP_END_OF_LIFE_LOSS * (rt.PLANT_EXAMPLE['lamp_age_s'] + 90.0) / rt.LAMP_RATED_S
        self.assertAlmostEqual(h.read('evaluator')['truth.lamp_intensity_rel'], age * (1 - rt.LAMP_WARMUP_DEFICIT * math.exp(-1.0)), places=9)
        self.assertFalse(h.read()['lamp.warmed_up'])
        h.step(rt.LAMP_WARMUP_TAU_S * math.log(rt.LAMP_WARMUP_DEFICIT / 0.01) - 90.0 + 0.5)
        self.assertTrue(h.read()['lamp.warmed_up'])
        self.assertAlmostEqual(h.read()['lamp.on_time_s'], rt.PLANT_EXAMPLE['lamp_age_s'] + h.t, places=6)
        old = ready(lamp_age_s=3000.0 * 3600.0)
        self.assertLess(old.read('evaluator')['truth.lamp_intensity_rel'], h.read('evaluator')['truth.lamp_intensity_rel'] * 0.8)
        cold = configured()
        cold.invoke('aoi.set', position='90')
        settle(cold)
        cold.invoke('power.set', enabled=True)
        cold.step(20.0)
        rec = measure(cold, alignment='none')
        self.assertIn('lamp_not_warmed_up', rec['warnings'])
        warm = straight_through(ready())
        self.assertNotIn('lamp_not_warmed_up', warm['warnings'])

    def test_power_mains_and_power_loss_during_a_measurement(self):
        h = configured(mains_voltage_v=60.0)
        rejected(h, 'power.set', 'MAINS_OUT_OF_RANGE', enabled=True)
        rejected(h, 'measure', 'POWER_OFF', mode='standard', alignment='none', fixed_height_m=None, model_id=None)
        h = held()
        rejected(h, 'plant.configure', 'POWER_ON', **rt.PLANT_EXAMPLE)
        op = h.invoke('measure', mode='long', alignment='standard', fixed_height_m=None, model_id=None)['operation_id']
        _into(h, 'acquiring', 0.5)
        h.invoke('power.set', enabled=False)
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual((rec['status'], rec['reason'], rec['psi_rad']), ('failed', 'POWER_OFF', None))
        self.assertFalse(h.read()['lamp.on'])
        h.invoke('power.press_button')
        self.assertTrue(h.power)
        h.invoke('power.press_button')
        self.assertFalse(h.power)

    def test_vacuum_holds_only_a_sample_covering_the_holes_with_enough_supply(self):
        h = held()
        self.assertTrue(h.read('evaluator')['sample.held'])
        self.assertAlmostEqual(h.read('evaluator')['vacuum.pressure_pa'], rt.PLANT_EXAMPLE['vacuum_supply_pa'])
        rejected(h, 'sample.remove', 'SAMPLE_HELD', sample_id='pmma_on_si')
        h.invoke('vacuum.press_switch')
        self.assertFalse(h.read('evaluator')['sample.held'])
        self.assertAlmostEqual(h.read('evaluator')['vacuum.pressure_pa'], 101325.0)
        loose = measure(h)                                                  # the vacuum only holds the sample; measuring works without it
        self.assertEqual(loose['status'], 'completed')
        self.assertIn('sample_not_held', loose['warnings'])
        h.invoke('sample.remove', sample_id='pmma_on_si')
        h.invoke('sample.provide', **TINY)
        h.invoke('sample.place', sample_id='tiny', offset_x_m=0.0015, offset_y_m=0.0)     # one hole uncovered
        h.invoke('vacuum.set', on=True)
        self.assertFalse(h.read('evaluator')['sample.held'])
        self.assertGreater(h.read('evaluator')['vacuum.pressure_pa'], 80.0e3)
        weak = held(vacuum_supply_pa=90.0e3)
        self.assertFalse(weak.read('evaluator')['sample.held'])
        self.assertIn('sample_not_held', measure(weak)['warnings'])
        op = h.invoke('vacuum.set', on=False)
        self.assertEqual(op['on'], False)


class MechanicsAndCollision(unittest.TestCase):
    def test_angle_positions_arm_elevation_and_plunger(self):
        h = ready()
        for a in ('65', '75', '90', '70'):
            h.invoke('aoi.set', position=a)
            rejected(h, 'measure', 'MOTION_IN_PROGRESS', mode='standard', alignment='none', fixed_height_m=None, model_id=None)
            settle(h)
            self.assertEqual(h.read()['aoi.position'], a)
            self.assertAlmostEqual(h.read()['aoi.arm_elevation_rad'], math.radians(90.0 - float(a)), places=12)
        self.assertEqual(h.invoke('aoi.set', position='70')['duration_s'], 0.0)              # already seated there
        self.assertEqual(h.ledger()['aoi_changes'], 4.0)
        self.assertAlmostEqual(h.ledger()['arm_travel_rad'], math.radians(5 + 10 + 15 + 20), places=9)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('aoi.set', position='60')
        h.invoke('fault.inject', fault='plunger_not_seated', active=True)
        self.assertIsNone(h.read()['aoi.position'])
        rejected(h, 'measure', 'AOI_NOT_SEATED', mode='standard', alignment='none', fixed_height_m=None, model_id=None)
        h.invoke('aoi.set', position='70')
        settle(h)
        self.assertIsNone(h.read()['aoi.position'])                                       # still not seated
        h.invoke('fault.inject', fault='plunger_not_seated', active=False)
        h.invoke('aoi.set', position='70')
        settle(h)
        self.assertEqual(h.read()['aoi.position'], '70')

    def test_collision_guard_for_large_and_raised_samples(self):
        h = ready(samples=(PMMA, CAL, W200))
        rejected(h, 'sample.place', 'COLLISION_RISK', sample_id='wafer_200', offset_x_m=0.0, offset_y_m=0.0)      # would run into the Z guide
        rejected(h, 'sample.place', 'NOT_ON_PLATFORM', sample_id='pmma_on_si', offset_x_m=0.03, offset_y_m=0.0)  # the beam would miss it
        out = h.invoke('sample.place', sample_id='wafer_200', offset_x_m=0.0, offset_y_m=-0.052)                   # overhangs the base front
        self.assertTrue(out['covers_vacuum_holes'])
        h.invoke('vacuum.set', on=True)
        rec = measure(h)
        self.assertEqual(rec['status'], 'completed')
        rejected(h, 'aoi.set', 'COLLISION_RISK', position='90')                  # the horizontal arms would hit the wafer at the measuring height
        h.invoke('stage.move_z', z_m=rt.STAGE_Z_RANGE_M[0])
        settle(h)
        h.invoke('aoi.set', position='90')
        settle(h)
        rejected(h, 'stage.move_z', 'COLLISION_RISK', z_m=rt.STAGE_Z_RANGE_M[1])
        st = measure(h, alignment='none')
        self.assertEqual(st['status'], 'completed')                               # lowered far enough: the beam passes over the wafer
        h2 = ready(samples=(PMMA, CAL, BLOCK))
        h2.invoke('aoi.set', position='90')
        settle(h2)
        h2.invoke('stage.move_z', z_m=MZ - 0.004)
        settle(h2)
        h2.invoke('sample.place', sample_id='glass_block', offset_x_m=0.0, offset_y_m=0.0)     # 6 mm block reaches into the beam
        h2.invoke('vacuum.set', on=True)
        rec = measure(h2, alignment='none')
        self.assertEqual((rec['status'], rec['reason']), ('failed', 'BEAM_BLOCKED'))
        rejected(h2, 'measure', 'ALIGNMENT_NOT_POSSIBLE_STRAIGHT_THROUGH', mode='standard', alignment='standard', fixed_height_m=None, model_id=None)
        rejected(h2, 'measure', 'MODE_NOT_AVAILABLE', mode='mm', alignment='none', fixed_height_m=None, model_id=None)
        rejected(h2, 'measure', 'NO_SAMPLE_IN_BEAM', mode='standard', alignment='none', fixed_height_m=None, model_id='cauchy_on_si')

    def test_sample_definitions_are_checked(self):
        h = configured()
        for bad, code in [(dict(PMMA, sample_id='big', size_m=[0.25, 0.25]), 'SAMPLE_SIZE_OUT_OF_RANGE'),
                          (dict(PMMA, sample_id='small', size_m=[0.004, 0.004]), 'SAMPLE_SIZE_OUT_OF_RANGE'),
                          (dict(PMMA, sample_id='thick', substrate_thickness_m=0.017), 'SAMPLE_TOO_THICK'),
                          (dict(PMMA, sample_id='pmma_on_si'), 'DUPLICATE_SAMPLE'),
                          (dict(PMMA, sample_id='oval', size_m=[0.05, 0.04]), 'INVALID_ARGUMENT')]:
            rejected(h, 'sample.provide', code, **bad)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('sample.provide', **dict(PMMA, sample_id='unobtainium', substrate={'library': 'unobtainium'}))
        for i in range(rt.MAX_SAMPLES - 2):
            h.invoke('sample.provide', **dict(BARE, sample_id=f's{i}'))
        rejected(h, 'sample.provide', 'TOO_MANY_SAMPLES', **dict(BARE, sample_id='one_more'))


class OpticsPhysics(unittest.TestCase):
    def test_bare_silicon_and_oxide_growth(self):
        lam = np.array([632.8])
        N = rt.library_index('si', lam)
        self.assertAlmostEqual(N.real[0], rt.substance('si')['at_632_8nm']['n'], places=4)
        rp, rs = rt.stack_rps(lam, np.ones(1, complex), [], N, math.radians(70.0))
        psi, delta = rt.psi_delta(*rt.rncs(rp, rs)[1:])
        self.assertAlmostEqual(psi[0], 10.5, delta=0.1)
        self.assertGreater(delta[0], 178.5)                                    # Woollam convention: bare Si just below 180 deg at 70 deg
        last = delta[0]
        for d in (5e-9, 25e-9, 50e-9):
            rp, rs = rt.stack_rps(lam, np.ones(1, complex), [(rt.library_index('sio2', lam), d)], N, math.radians(70.0))
            psi, dl = rt.psi_delta(*rt.rncs(rp, rs)[1:])
            self.assertLess(dl[0], last)                                     # Delta falls as the oxide grows
            last = dl[0]
        self.assertAlmostEqual(last, 91.4, delta=6.0)

    def test_airy_and_abeles_agree_and_conserve_energy(self):
        lam = rt.wavelengths_nm()
        n1, n2, ns = rt.library_index('tio2', lam), rt.library_index('sio2', lam), rt.library_index('n_bk7', lam).real + 0j
        layers = [(n1.real + 0j, 80e-9), (n2.real + 0j, 150e-9)]
        th = math.radians(65.0)
        rp, rs = rt.stack_rps(lam, np.ones_like(n1), layers, ns, th)
        for pol, r in (('p', rp), ('s', rs)):
            r2, t2, T = rt._stack_rt(lam, np.ones_like(n1), layers, ns, np.sin(th) * np.ones_like(n1), pol)
            self.assertLess(np.max(np.abs(r2 - r)), 1e-12)
            self.assertLess(np.max(np.abs(np.abs(r2) ** 2 + T - 1.0)), 1e-12)
            q_in, q_out = rt._q(np.ones_like(n1), np.sin(th)), rt._q(ns, np.sin(th))
            eta = (lambda N, q: q) if pol == 's' else (lambda N, q: N * N / q)
            self.assertLess(np.max(np.abs(np.abs(t2) ** 2 * np.real(eta(ns, q_out)) / np.real(eta(np.ones_like(n1), q_in)) - T)), 1e-12)

    def test_brewster_angle_of_glass(self):
        lam = np.array([632.8])
        n = rt.library_index('n_bk7', lam).real[0]
        tb = math.atan(n)
        rp, rs = rt.stack_rps(lam, np.ones(1, complex), [], np.array([n + 0j]), tb)
        psi, _ = rt.psi_delta(*rt.rncs(rp, rs)[1:])
        self.assertLess(psi[0], 1e-6)
        rp2, _ = rt.stack_rps(lam, np.ones(1, complex), [], np.array([n + 0j]), tb + math.radians(1.0))
        self.assertGreater(abs(rp2[0]), 1e-3)

    def test_tauc_lorentz_kramers_kronig_and_bruggeman_limits(self):
        from scipy.integrate import quad
        p = dict(eps_inf=1.15, Eg=1.2, A=122.0, E0=3.45, C=2.54)
        E = np.array([1.5, 2.5, 3.2])
        eps = rt.tauc_lorentz_eps(E, p['eps_inf'], p['Eg'], p['A'], p['E0'], p['C'])

        def e2(x):
            return p['A'] * p['E0'] * p['C'] * (x - p['Eg']) ** 2 / ((x * x - p['E0'] ** 2) ** 2 + p['C'] ** 2 * x * x) / x if x > p['Eg'] else 0.0
        for Ei, ei in zip(E, eps):
            pv = quad(lambda x: x * e2(x) / (x + Ei), p['Eg'], 2000.0, weight='cauchy', wvar=Ei, limit=400)[0]
            self.assertAlmostEqual(ei.real, p['eps_inf'] + 2 / math.pi * pv, delta=2e-3 * abs(ei.real))
            self.assertAlmostEqual(ei.imag, e2(Ei), places=12)
        ea, eb = np.array([2.1 + 0.0j]), np.array([12.0 + 1.0j])
        self.assertAlmostEqual(rt.bruggeman(ea, eb, 0.0)[0], ea[0])
        self.assertAlmostEqual(rt.bruggeman(ea, eb, 1.0)[0], eb[0])
        self.assertAlmostEqual(rt.bruggeman(ea, eb, 0.3)[0], rt.bruggeman(eb, ea, 0.7)[0])

    def test_pseudo_dielectric_inversion_is_exact_for_a_bare_substrate(self):
        lam = rt.wavelengths_nm()
        for amb in ('air', 'water'):
            N0 = np.ones(lam.shape, complex) if amb == 'air' else rt.library_index('water', lam)
            N = rt.library_index('gaas', lam)
            rp, rs = rt.stack_rps(lam, N0, [], N, math.radians(70.0))
            psi, delta = rt.psi_delta(*rt.rncs(rp, rs)[1:])
            ps = rt.pseudo_constants(dict(record_id='x', wavelengths_m=list(lam * 1e-9), aoi_rad=math.radians(70.0), ambient=amb, psi_rad=list(np.radians(psi)),
                                          delta_rad=list(np.radians(delta))))
            self.assertLess(np.max(np.abs(np.array(ps['n']) + 1j * np.array(ps['k']) - N)), 1e-9, amb)

    def test_depolarization_from_non_uniformity_and_backside_beams(self):
        lam = rt.wavelengths_nm()
        base = dict(PMMA, layers=[dict(material={'library': 'pmma'}, thickness_m=1.0e-6)])
        uni = rt.sample_mueller(base, lam, math.radians(70.0))
        non = rt.sample_mueller(dict(base, layers=[dict(material={'library': 'pmma'}, thickness_m=1.0e-6, nonuniformity=0.02)]), lam, math.radians(70.0))
        self.assertGreater(float(np.mean(non['depol'])), 5 * float(np.mean(uni['depol'])))
        rough = rt.sample_mueller(dict(SLIDE, backside='rough'), lam, math.radians(70.0))
        pol = rt.sample_mueller(SLIDE, lam, math.radians(70.0))
        self.assertGreater(float(np.mean(pol['R'])), float(np.mean(rough['R'])))            # the back face adds light
        self.assertGreater(float(np.mean(pol['depol'])), float(np.mean(rough['depol'])) + 0.01)


class InstrumentAccuracy(unittest.TestCase):
    def test_straight_through_meets_the_accuracy_specification(self):
        """Specification: straight-through empty beam Psi = 45 +- 0.1 deg and Delta = 0 +- 0.2 deg (98 % of the wavelengths, 10 s)."""
        for seed in (1, 2, 3, 4):
            rec = straight_through(ready(seed))
            dp = np.abs(np.degrees(rec['psi_rad']) - 45.0)
            dd = np.abs((np.degrees(rec['delta_rad']) + 180.0) % 360.0 - 180.0)
            frac = float(np.mean((dp <= rt.ACCURACY_PSI_DEG) & (dd <= rt.ACCURACY_DELTA_DEG)))
            CHECKS.append(dict(check='straight_through_accuracy', seed=seed, fraction_within=frac, psi_p98=float(np.percentile(dp, 98)),
                               delta_p98=float(np.percentile(dd, 98))))
            self.assertGreaterEqual(frac, 0.98, seed)

    def test_repeatability_of_2nm_oxide_and_mode_times(self):
        """Specification: 30 repeated 10 s measurements of 2 nm SiO2 on Si at 70 deg, thickness 1 sigma 0.01 nm; the noise falls with the mode time."""
        sd = {}
        for mode in ('fast', 'standard', 'long'):
            h = held(OX2, offset=(0.0, -0.02))
            th = []
            for i in range(30):
                rec = measure(h, mode=mode, alignment='standard' if i == 0 else 'none', model_id='si_native_oxide')
                th.append(rec['fit']['parameters']['oxide.thickness_m']['value'])
            sd[mode] = float(np.std(th, ddof=1))
            self.assertAlmostEqual(float(np.mean(th)), 2e-9, delta=0.05e-9)
            self.assertAlmostEqual(rec['finished_s'] - rec['started_s'], rt.ALIGN_TIME_S['none'] + rt.MODE_TIME_S[mode] + rt.FIT_TIME_S, places=6)
        CHECKS.append(dict(check='repeatability_2nm_sio2', sigma_m=sd))
        self.assertLessEqual(sd['standard'], rt.REPEATABILITY_2NM_SIO2_M)
        self.assertGreater(sd['fast'], sd['standard'])
        self.assertGreater(sd['standard'], sd['long'])

    def test_calibration_recovers_the_hidden_instrument_errors(self):
        h = held(CAL, calibration='nominal')
        rec0 = measure(h, model_id='si_native_oxide')
        self.assertIn('instrument_not_calibrated', rec0['warnings'])
        op = h.invoke('calibration.run')['operation_id']
        rec = wait(h, op)
        c, tr = h.cal, h.truth
        self.assertEqual(c['status'], 'calibrated')
        self.assertLess(abs(math.degrees(c['aoi_rad']['70']) - tr['aoi_deg'][70.0]), 0.01)
        self.assertLess(abs(math.degrees(c['polarizer_offset_rad'] - tr['P_offset'])), 0.05)
        self.assertLess(abs(math.degrees(c['analyzer_rad'] - tr['A'])), 0.05)
        self.assertAlmostEqual(rec['calibration']['reference_thickness_m'], 25e-9, delta=0.1e-9)
        self.assertEqual(c['aoi_rad']['65'], math.radians(65.0))                               # only the calibrated position changes
        CHECKS.append(dict(check='calibration', aoi_error_rad=c['aoi_rad']['70'] - math.radians(tr['aoi_deg'][70.0]),
                           nominal_aoi_error_rad=math.radians(70.0 - tr['aoi_deg'][70.0])))
        rec1 = measure(h, model_id='si_native_oxide')
        self.assertNotIn('instrument_not_calibrated', rec1['warnings'])
        self.assertLess(rec1['fit']['mse'], rec0['fit']['mse'])
        rejected(h, 'records.delete', 'UNKNOWN_RECORD', record_id='XX0001')
        h.invoke('vacuum.set', on=False)
        h.invoke('sample.remove', sample_id='cal_wafer')
        h.invoke('sample.place', sample_id='pmma_on_si', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('vacuum.set', on=True)
        rejected(h, 'calibration.run', 'CALIBRATION_WAFER_REQUIRED')

    def test_reflectance_follows_the_straight_through_baseline(self):
        h = ready(samples=(PMMA, CAL, BARE))
        straight_through(h)
        self.assertTrue(h.read()['calibration.baseline']['valid'])
        h.invoke('aoi.set', position='70')
        settle(h)
        h.invoke('sample.place', sample_id='bare_si', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('vacuum.set', on=True)
        rec = measure(h)
        truth = rt.sample_mueller(BARE, rt.wavelengths_nm(), math.radians(h.truth['aoi_deg'][70.0]))['R']
        rel = np.abs(np.array(rec['reflectance']) / truth - 1.0)
        self.assertLess(float(np.median(rel)), 0.01)
        fresh = held(BARE, samples=(PMMA, CAL, BARE))
        self.assertIsNone(measure(fresh)['reflectance'])                       # no baseline yet

    def test_mueller_matrix_of_an_isotropic_sample(self):
        h = held()
        se = measure(h)
        mm = measure(h, mode='mm')
        M = {k: np.array(v) for k, v in mm['mueller'].items()}
        for k in ('m13', 'm23', 'm31', 'm32', 'm41', 'm42'):
            self.assertLess(float(np.max(np.abs(M[k]))), 0.01, k)
        self.assertLess(float(np.max(np.abs(M['m12'] - M['m21']))), 0.01)
        self.assertLess(float(np.max(np.abs(M['m22'] - 1.0))), 0.01)
        for a, b in ((mm['N'], se['N']), (mm['C'], se['C']), (mm['S'], se['S'])):
            self.assertLess(float(np.max(np.abs(np.array(a) - np.array(b)))), 0.01)
        self.assertAlmostEqual(mm['finished_s'] - mm['started_s'], rt.ALIGN_TIME_S['standard'] + rt.MODE_TIME_S['mm'], delta=0.5)


class AlignmentAndHeight(unittest.TestCase):
    def test_alignment_modes(self):
        h = held()
        rec = measure(h, alignment='none')                                     # the stage is still at its reference height
        self.assertEqual((rec['status'], rec['reason']), ('failed', 'NO_SIGNAL'))
        rec = measure(h, alignment='standard')
        self.assertEqual(rec['status'], 'completed')
        z_ok = rec['stage_z_m']
        self.assertAlmostEqual(z_ok + rt.sample_height_m(PMMA), MZ, delta=15e-6)
        self.assertEqual(h.read()['stage.last_aligned_z_m'], z_ok)
        rec = measure(h, alignment='fixed_height', fixed_height_m=MZ - rt.sample_height_m(PMMA) - 50e-6)
        self.assertEqual(rec['status'], 'completed')
        self.assertIn('height_offset_um:-50', rec['warnings'])
        rec = measure(h, alignment='fixed_height', fixed_height_m=MZ - rt.sample_height_m(PMMA) - 1.5e-3)
        self.assertEqual((rec['status'], rec['reason']), ('failed', 'NO_SIGNAL'))
        rejected(h, 'measure', 'INVALID_ARGUMENT', mode='standard', alignment='standard', fixed_height_m=MZ - 0.001, model_id=None)
        h.invoke('vacuum.set', on=False)
        h.invoke('sample.remove', sample_id='pmma_on_si')
        h.invoke('sample.provide', **BLOCK)
        h.invoke('sample.place', sample_id='glass_block', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('vacuum.set', on=True)
        rec = measure(h, alignment='standard')                                 # 5.7 mm higher than the last aligned height
        self.assertEqual((rec['status'], rec['reason']), ('failed', 'ALIGNMENT_FAILED'))
        rec = measure(h, alignment='robust')
        self.assertEqual(rec['status'], 'completed')
        self.assertAlmostEqual(rec['stage_z_m'] + 0.006, MZ, delta=15e-6)

    def test_too_little_reflected_light_fails_the_alignment(self):
        h = ready(samples=(PMMA, CAL, MGF2))
        h.invoke('liquid_cell.install', sample_id='mgf2_plate')
        h.invoke('liquid_cell.fill', liquid_id='ethanol', volume_m3=480e-9)     # index-matched: almost no reflection
        h.invoke('vacuum.set', on=True)
        rec = measure(h, alignment='robust')
        self.assertEqual((rec['status'], rec['reason']), ('failed', 'ALIGNMENT_FAILED'))


class LiquidCell(unittest.TestCase):
    def test_cell_rules_volumes_and_a_fit_in_water(self):
        h = ready()
        h.invoke('aoi.set', position='65')
        settle(h)
        rejected(h, 'liquid_cell.install', 'AOI_NOT_70', sample_id='pmma_on_si')
        h.invoke('aoi.set', position='70')
        settle(h)
        rejected(h, 'liquid_cell.fill', 'NO_LIQUID_CELL', liquid_id='water', volume_m3=100e-9)
        h.invoke('liquid_cell.install', sample_id='pmma_on_si')
        rejected(h, 'aoi.set', 'LIQUID_CELL_INSTALLED', position='75')
        rejected(h, 'sample.place', 'SAMPLE_NOT_ON_BENCH', sample_id='pmma_on_si', offset_x_m=0.0, offset_y_m=0.0)
        rejected(h, 'sample.place', 'STAGE_OCCUPIED', sample_id='cal_wafer', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('vacuum.set', on=True)
        air = measure(h)
        self.assertEqual((air['status'], air['ambient']), ('completed', 'air'))
        h.invoke('liquid_cell.fill', liquid_id='water', volume_m3=300e-9)
        rejected(h, 'measure', 'LIQUID_LEVEL_LOW', mode='standard', alignment='standard', fixed_height_m=None, model_id=None)
        rejected(h, 'liquid_cell.fill', 'CELL_OVERFLOW', liquid_id='water', volume_m3=250e-9)
        rejected(h, 'liquid_cell.fill', 'DIFFERENT_LIQUID', liquid_id='ethanol', volume_m3=10e-9)
        h.invoke('liquid_cell.fill', liquid_id='water', volume_m3=150e-9)
        wet = measure(h)
        self.assertEqual((wet['status'], wet['ambient']), ('completed', 'water'))
        self.assertGreater(float(np.max(np.abs(np.array(wet['psi_rad']) - np.array(air['psi_rad'])))), math.radians(2.0))
        fa = h.invoke('analysis.fit', **dict(FIT, record_ids=[air['record_id']]))
        fw = h.invoke('analysis.fit', **dict(FIT, record_ids=[wet['record_id']]))
        self.assertAlmostEqual(fw['parameters']['film.thickness_m']['value'], fa['parameters']['film.thickness_m']['value'], delta=1.0e-9)
        self.assertLess(fw['mse'], 5.0)
        rejected(h, 'liquid_cell.remove', 'CELL_NOT_EMPTY')
        self.assertEqual(h.invoke('liquid_cell.drain')['drained_m3'], 450e-9)
        led = h.ledger()
        self.assertAlmostEqual(led['liquid_added_m3'], led['liquid_drained_m3'] + led['liquid_in_cell_m3'], places=18)
        self.assertEqual(led['liquid_residual_m3'], 0.0)
        h.invoke('vacuum.set', on=False)
        self.assertEqual(h.invoke('liquid_cell.remove')['sample_id'], 'pmma_on_si')
        self.assertIn('pmma_on_si', h.read()['sample.bench_ids'])
        h2 = ready(samples=(PMMA, CAL, W200))
        rejected(h2, 'liquid_cell.install', 'SAMPLE_DOES_NOT_FIT_CELL', sample_id='wafer_200')


class Analysis(unittest.TestCase):
    def test_fit_confidence_limits_and_nk(self):
        h = _scenario('measured')
        f = h.records['SE0001']['fit']
        p = f['parameters']
        self.assertAlmostEqual(p['film.thickness_m']['value'], 121.8e-9, delta=0.6e-9)          # 120 nm PMMA + 1.8 nm oxide as one layer
        self.assertAlmostEqual(f['nk_632_8nm']['film']['n'], rt.substance('pmma')['at_632_8nm']['n'], delta=0.01)
        self.assertLess(f['mse'], 5.0)
        self.assertTrue(f['converged'])
        for k in f['fitted_parameters']:
            self.assertGreater(p[k]['ci90'], 0.0)
        C = np.array(f['correlation'])
        self.assertTrue(np.allclose(C, C.T))
        self.assertTrue(np.allclose(np.diag(C), 1.0))
        self.assertEqual(h.read()['result.last_thickness_m'], p['film.thickness_m']['value'])
        self.assertEqual(h.read()['result.last_mse'], f['mse'])

    def test_thickness_prefit_finds_the_global_minimum(self):
        h = _scenario('measured')
        far = dict(FIT, parameters={'film.thickness_m': 700e-9})
        good = h.invoke('analysis.fit', **far)
        bad = h.invoke('analysis.fit', **dict(far, thickness_prefit=False))
        self.assertAlmostEqual(good['parameters']['film.thickness_m']['value'], 121.8e-9, delta=0.6e-9)
        self.assertGreater(bad['mse'], 10 * good['mse'])
        self.assertIsNotNone(h.records[good['record_id']]['fit']['prefit'])
        self.assertIsNone(h.records[bad['record_id']]['fit']['prefit'])

    def test_multi_angle_fit_and_uniqueness(self):
        h = held()
        rids = []
        for a in ('65', '70', '75'):
            h.invoke('aoi.set', position=a)
            settle(h)
            rids.append(measure(h)['record_id'])
        one = h.invoke('analysis.fit', **dict(FIT, record_ids=[rids[1]], fit=FIT['fit'] + ['film.C']))
        three = h.invoke('analysis.fit', **dict(FIT, record_ids=rids, fit=FIT['fit'] + ['film.C']))
        self.assertAlmostEqual(three['parameters']['film.thickness_m']['value'], one['parameters']['film.thickness_m']['value'], delta=1.0e-9)
        self.assertLess(three['parameters']['film.thickness_m']['ci90'], one['parameters']['film.thickness_m']['ci90'])
        self.assertEqual(len(h.records[three['record_id']]['model_curves']), 3)
        u = h.invoke('analysis.uniqueness', record_ids=[rids[1]], model_id='cauchy_on_si', parameters={}, fit=['film.A', 'film.B'],
                     scan_parameter='film.thickness_m', values=list(np.linspace(80e-9, 160e-9, 17)))
        self.assertAlmostEqual(u['best_value'], 120e-9, delta=5.1e-9)
        self.assertEqual(len(u['local_minima']), 1)
        self.assertLess(min(u['mse']) * 5, max(u['mse']))

    def test_library_tauc_lorentz_and_pseudo_models(self):
        asi = dict(PMMA, sample_id='asi_on_oxide', layers=[dict(material={'library': 'a_si'}, thickness_m=30e-9), dict(material={'library': 'sio2'}, thickness_m=100e-9)])
        gold = dict(BARE, sample_id='gold', substrate={'library': 'au'})
        h = held(asi, samples=(PMMA, CAL, gold))
        rec = measure(h, alignment='robust')
        out = h.invoke('analysis.fit', record_ids=[rec['record_id']], model_id='tauc_lorentz_on_si',
                       parameters={'interface.thickness_m': 100e-9, 'film.thickness_m': 25e-9, 'film.eps_inf': 1.15, 'film.Eg_ev': 1.2, 'film.A_ev': 122.0,
                                   'film.E0_ev': 3.45, 'film.C_ev': 2.54}, fit=['film.thickness_m', 'film.A_ev', 'film.E0_ev', 'film.C_ev', 'film.Eg_ev'], thickness_prefit=False)
        self.assertAlmostEqual(out['parameters']['film.thickness_m']['value'], 30e-9, delta=0.5e-9)
        self.assertAlmostEqual(out['nk_632_8nm']['film']['n'], rt.substance('a_si')['at_632_8nm']['n'], delta=0.05)
        self.assertLess(out['mse'], 5.0)
        h.invoke('vacuum.set', on=False)
        h.invoke('sample.remove', sample_id='asi_on_oxide')
        h.invoke('sample.place', sample_id='gold', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('vacuum.set', on=True)
        rec = measure(h, alignment='robust')
        lib = h.invoke('analysis.fit', record_ids=[rec['record_id']], model_id='library_bulk:au', parameters={}, fit=['rough.thickness_m'], thickness_prefit=False)
        self.assertLess(lib['mse'], 5.0)
        ps = h.invoke('data.pseudo', record_id=rec['record_id'])
        i = int(np.argmin(np.abs(np.array(ps['wavelengths_m']) - 632.8e-9)))
        nk = rt.library_index('au', [ps['wavelengths_m'][i] * 1e9])[0]
        self.assertAlmostEqual(ps['n'][i], nk.real, delta=0.02)
        self.assertAlmostEqual(ps['k'][i], nk.imag, delta=0.05)
        dl = h.invoke('analysis.fit', record_ids=[rec['record_id']], model_id='drude_lorentz_bulk', parameters={}, fit=['substrate.Ep_ev', 'substrate.gamma_ev',
                      'substrate.osc_A', 'substrate.osc_E0_ev', 'substrate.osc_B_ev', 'substrate.eps_inf'], thickness_prefit=False)
        self.assertLess(dl['mse'], 50.0)
        self.assertAlmostEqual(dl['nk_632_8nm']['substrate']['k'], rt.substance('au')['at_632_8nm']['k'], delta=0.2)
        for bad, code in [(dict(FIT, record_ids=[rec['record_id']], model_id='nope'), 'UNKNOWN_MODEL'),
                          (dict(FIT, record_ids=[rec['record_id']], parameters={'film.X': 1.0}), 'INVALID_ARGUMENT'),     # not a parameter name
                          (dict(FIT, record_ids=[rec['record_id']], parameters={'film.Eg_ev': 3.0}), 'UNKNOWN_PARAMETER'),  # not of this model
                          (dict(FIT, record_ids=[rec['record_id']], parameters={'film.A': 9.0}), 'PARAMETER_OUT_OF_RANGE'),
                          (dict(FIT, record_ids=[rec['record_id']], fit=['film.band_edge_ev']), 'PARAMETER_NOT_FITTABLE'),
                          (dict(FIT, record_ids=['XX0001']), 'UNKNOWN_RECORD'),
                          (dict(FIT, record_ids=[rec['record_id'], rec['record_id']]), 'INVALID_ARGUMENT')]:
            rejected(h, 'analysis.fit', code, **bad)

    def test_backside_correction_of_a_polished_glass_slide(self):
        h = held(SLIDE)
        rec = measure(h, alignment='robust')
        with_back = h.invoke('analysis.fit', record_ids=[rec['record_id']], model_id='library_film:tio2@soda_lime',
                             parameters={'substrate.backside_polished': 1.0, 'substrate.thickness_m': 0.001}, fit=['film.thickness_m', 'rough.thickness_m'],
                             thickness_prefit=True)
        without = h.invoke('analysis.fit', record_ids=[rec['record_id']], model_id='library_film:tio2@soda_lime', parameters={},
                           fit=['film.thickness_m', 'rough.thickness_m'], thickness_prefit=True)
        self.assertAlmostEqual(with_back['parameters']['film.thickness_m']['value'], 80e-9, delta=1e-9)
        self.assertLess(with_back['mse'] * 5, without['mse'])
        self.assertGreater(max(rec['depolarization']), 0.02)


class FaultsCancelAndData(unittest.TestCase):
    def test_lamp_failure_stage_stall_and_vacuum_leak(self):
        h = held()
        op = h.invoke('measure', mode='long', alignment='standard', fixed_height_m=None, model_id=None)['operation_id']
        _into(h, 'acquiring', 0.3)
        h.invoke('fault.inject', fault='lamp_failure', active=True)
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual((rec['status'], rec['reason']), ('failed', 'LAMP_FAILURE'))
        self.assertEqual(h.read()['system.error_code'], 'LAMP_FAILURE')
        self.assertFalse(h.read()['lamp.on'])
        rejected(h, 'system.acknowledge', 'CAUSE_PERSISTS')
        rejected(h, 'measure', 'ERROR_PENDING', mode='standard', alignment='standard', fixed_height_m=None, model_id=None)
        h.invoke('fault.inject', fault='lamp_failure', active=False)
        self.assertEqual(h.invoke('system.acknowledge')['cleared'], 'LAMP_FAILURE')
        self.assertFalse(h.read()['lamp.warmed_up'])                           # the new lamp warms up again
        h.step(600.0)
        z0 = h.z
        h.invoke('stage.move_z', z_m=rt.STAGE_Z_RANGE_M[0])
        rejected(h, 'measure', 'MOTION_IN_PROGRESS', mode='standard', alignment='standard', fixed_height_m=None, model_id=None)
        settle(h)
        op = h.invoke('measure', mode='standard', alignment='standard', fixed_height_m=None, model_id=None)['operation_id']
        _into(h, 'aligning', 0.2)
        zmid = h.z
        h.invoke('fault.inject', fault='stage_stall', active=True)
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual((rec['status'], rec['reason']), ('failed', 'STAGE_FAULT'))
        h.step(5.0)
        self.assertEqual(h.z, zmid)                                            # stopped where it was
        self.assertNotEqual(zmid, z0)
        rejected(h, 'stage.move_z', 'ERROR_PENDING', z_m=MZ - 0.001)
        h.invoke('fault.inject', fault='stage_stall', active=False)
        h.invoke('system.acknowledge')
        op = h.invoke('measure', mode='long', alignment='standard', fixed_height_m=None, model_id=None)['operation_id']
        _into(h, 'acquiring', 0.5)
        h.invoke('fault.inject', fault='vacuum_leak', active=True)
        self.assertFalse(h.read('evaluator')['sample.held'])
        rec = wait(h, op)                                                   # the loose sample is still measured (warning)
        self.assertEqual(rec['status'], 'completed')
        self.assertIn('vacuum_lost', rec['warnings'])
        self.assertIn('sample_not_held', rec['warnings'])

    def test_cancel_keeps_the_height_and_writes_no_data(self):
        h = held()
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r = s.invoke(instance_id=h.instance_id, capability_id='measure', arguments=dict(mode='long', alignment='robust', fixed_height_m=None, model_id=None),
                     request_id='m1')
        op = r['operation_id']
        _into(h, 'aligning', 0.5)
        z = h.z
        out = s.cancel(op, 'c1')
        self.assertEqual(out['result']['phase'], 'cancelled')
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual((rec['status'], rec['psi_rad']), ('cancelled', None))
        h.step(10.0)
        self.assertEqual(h.z, z)
        self.assertEqual(s.cancel(op, 'c1'), out)                               # idempotent
        with self.assertRaises(rt.ActionRejected):
            s.cancel(op, 'c2')
        self.assertIsNone(h.read()['measurement.id'])

    def test_records_delete_and_full(self):
        h = _scenario('measured')
        rejected(h, 'records.delete', 'UNKNOWN_RECORD', record_id='SE0099')
        for i in range(rt.MAX_RECORDS - len(h.records)):
            h.invoke('analysis.fit', **dict(FIT, thickness_prefit=False))
        rejected(h, 'analysis.fit', 'RECORDS_FULL', **dict(FIT, thickness_prefit=False))
        rejected(h, 'measure', 'RECORDS_FULL', mode='fast', alignment='none', fixed_height_m=None, model_id=None)
        self.assertEqual(h.invoke('records.delete', record_id='FT0001')['remaining'], rt.MAX_RECORDS - 1)
        rejected(h, 'measurement.result', 'UNKNOWN_RECORD', record_id='FT0001')
        h2 = _scenario('acquiring')
        rejected(h2, 'records.delete', 'RECORD_IN_USE', record_id=h2.op['record_id'])
        rejected(h2, 'analysis.fit', 'RECORD_NOT_SUITABLE', **dict(FIT, record_ids=[h2.op['record_id']]))


class LedgersIsolationSessions(unittest.TestCase):
    def test_energy_and_travel_ledgers_close(self):
        h = configured()
        h.invoke('power.set', enabled=True)
        h.step(100.0)
        h.invoke('stage.move_z', z_m=MZ - 0.002)
        h.step(20.0)
        h.invoke('aoi.set', position='65')
        h.step(10.0)
        led = h.ledger()
        self.assertAlmostEqual(led['electrical_energy_j'], (rt.LAMP_POWER_W + rt.ELECTRONICS_POWER_W) * 130.0, places=6)
        self.assertAlmostEqual(led['lamp_on_s'], 130.0, places=9)
        self.assertAlmostEqual(led['stage_travel_m'], abs(MZ - 0.002 - rt.STAGE_Z_REF_M), places=12)
        self.assertAlmostEqual(led['arm_travel_rad'], math.radians(5.0), places=12)
        h.invoke('power.set', enabled=False)
        h.step(50.0)
        self.assertAlmostEqual(h.ledger()['electrical_energy_j'], led['electrical_energy_j'], places=9)

    def test_two_instances_are_isolated(self):
        a, b = held(), held(seed=2)
        ra = measure(a)
        self.assertEqual(b.records, {})
        self.assertIsNone(b.read()['result.last_record_id'])
        self.assertNotEqual(a.truth['aoi_deg'], b.truth['aoi_deg'])
        self.assertEqual(ra['record_id'], 'SE0001')

    def test_agent_does_not_see_truth_faults_or_templates(self):
        h = held()
        h.register_principal('agent', 'agent')
        h.register_principal('eval', 'evaluator')
        s, e = h.session('agent'), h.session('eval')
        obs = s.read(instance_id=h.instance_id)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        self.assertIn('truth.aoi_rad', e.read(instance_id=h.instance_id)['observations'])
        for k in ('truth.samples', 'truth.instrument'):
            with self.assertRaises(rt.ActionRejected):
                s.read(instance_id=h.instance_id, state_ids=[k])
        d = s.describe(instance_id=h.instance_id)
        self.assertNotIn('fault.inject', d['capabilities'])
        self.assertNotIn('truth.samples', d['state_slots'])
        r = s.invoke(instance_id=h.instance_id, capability_id='fault.inject', arguments=dict(fault='vacuum_leak', active=True), request_id='f1')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        e.invoke(instance_id=h.instance_id, capability_id='fault.inject', arguments=dict(fault='plunger_not_seated', active=True), request_id='f2')
        ev = s.invoke(instance_id=h.instance_id, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e1')['result']['events']
        self.assertFalse([x for x in ev if x['kind'] == 'fault'])
        e.invoke(instance_id=h.instance_id, capability_id='fault.inject', arguments=dict(fault='plunger_not_seated', active=False), request_id='f3')
        h.invoke('aoi.set', position='70')
        settle(h)
        r = s.invoke(instance_id=h.instance_id, capability_id='measure', arguments=dict(mode='fast', alignment='standard', fixed_height_m=None, model_id=None),
                     request_id='m1')
        rec = wait(h, r['operation_id'])
        res = s.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=dict(record_id=rec['record_id']), request_id='r1')['result']
        self.assertEqual(res['template_source']['substances'], [])
        self.assertNotIn('_counts', res)
        full = e.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=dict(record_id=rec['record_id']), request_id='r2')['result']
        self.assertEqual(full['template_source']['substances'], ['pmma', 'si', 'sio2'])
        self.assertEqual(s.poll(r['operation_id'])['result']['template_source']['substances'], [])

    def test_records_and_control_belong_to_their_agent(self):
        h = held()
        for n in ('a1', 'a2'):
            h.register_principal(n, 'agent')
        a1, a2 = h.session('a1'), h.session('a2')
        iid = h.instance_id
        r = a1.invoke(instance_id=iid, capability_id='measure', arguments=dict(mode='standard', alignment='standard', fixed_height_m=None, model_id=None),
                      request_id='m1')
        op = r['operation_id']
        o2 = a2.read(instance_id=iid)['observations']
        self.assertTrue(o2['measurement.started_by_other_agent']['value'])
        self.assertIsNone(o2['measurement.id']['value'])
        for cap, args in [('stage.move_z', dict(z_m=MZ - 0.01)), ('vacuum.set', dict(on=False)), ('aoi.set', dict(position='65'))]:
            self.assertEqual(a2.invoke(instance_id=iid, capability_id=cap, arguments=args, request_id='x' + cap)['error']['code'], 'ACCESS_DENIED')
        with self.assertRaises(rt.ActionRejected):
            a2.poll(op)
        with self.assertRaises(rt.ActionRejected):
            a2.cancel(op, 'c1')
        rec = wait(h, op)
        rid = rec['record_id']
        self.assertEqual(a2.invoke(instance_id=iid, capability_id='measurement.result', arguments=dict(record_id=rid), request_id='r1')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(a2.invoke(instance_id=iid, capability_id='records.list', arguments={}, request_id='l1')['result']['records'], [])
        self.assertIsNone(a2.read(instance_id=iid)['observations']['result.last_record_id']['value'])
        self.assertEqual(a2.invoke(instance_id=iid, capability_id='analysis.fit', arguments=dict(FIT, record_ids=[rid]), request_id='f1')['error']['code'], 'ACCESS_DENIED')
        self.assertEqual(a2.invoke(instance_id=iid, capability_id='records.delete', arguments=dict(record_id=rid), request_id='d1')['error']['code'], 'ACCESS_DENIED')
        f = a1.invoke(instance_id=iid, capability_id='analysis.fit', arguments=dict(FIT, record_ids=[rid]), request_id='f2')
        self.assertEqual(f['status'], 'succeeded')
        o2 = a2.read(instance_id=iid)['observations']
        self.assertIsNone(o2['result.last_thickness_m']['value'])
        self.assertIsNotNone(a1.read(instance_id=iid)['observations']['result.last_thickness_m']['value'])
        again = a1.invoke(instance_id=iid, capability_id='analysis.fit', arguments=dict(FIT, record_ids=[rid]), request_id='f2')
        self.assertEqual(again, f)                                              # idempotent replay
        conflict = a1.invoke(instance_id=iid, capability_id='records.list', arguments={}, request_id='f2')
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')
        self.assertEqual(a1.invoke(instance_id=iid, capability_id='records.delete', arguments=dict(record_id=rid), request_id='d2')['status'], 'succeeded')
        with self.assertRaises(rt.ActionRejected):
            a1.write(instance_id=iid, state_id='stage.z_m', value=0.1, request_id='w1')
        self.assertEqual(a1.invoke(instance_id='other', capability_id='records.list', arguments={}, request_id='z1')['error']['code'], 'UNKNOWN_INSTANCE')

    def test_queries_and_bookkeeping(self):
        h = _scenario('measured')
        self.assertEqual(h.invoke('records.list')['records'], ['SE0001'])
        ev = h.invoke('events.read', since_s=0.0)['events']
        self.assertIn('MEASURE_DONE', [e['code'] for e in ev])
        prov = h.invoke('provenance.read')
        self.assertEqual(prov['substances'], 84)
        self.assertIn('CC0', prov['license'])
        models = h.invoke('models.list')['models']
        self.assertIn('cauchy_on_si', models)
        self.assertIn('library_film:{film}@{substrate}', models)
        self.assertEqual(len(rt.library()['subs']), 84)
        for s in rt.library()['subs'].values():
            self.assertEqual(len(s['n']), len(rt.library()['grid']))
            self.assertTrue(s['sources'], s['substance_id'])


def _dense_nonuniform(d, nu, lam, theta):
    z = np.linspace(-7, 7, 6001)
    w = np.exp(-0.5 * z * z)
    w /= w.sum()
    tot = (0.0, 0.0, 0.0, 0.0)
    Nf, Ns = rt.library_index('pmma', lam), rt.library_index('si', lam)
    for zi, wi in zip(z, w):
        rp, rs = rt.stack_rps(lam, np.ones(lam.shape, complex), [(Nf, d * (1 + nu * zi))], Ns, theta)
        tot = rt._add(tot, rt._pair(rp, rs), wi)
    return rt.normalised(tot)


def _pmma_film(d, nu=0.0):
    return dict(sample_id='film', kind='film_stack', shape='disc', size_m=[0.05, 0.05], substrate_thickness_m=0.0005, substrate={'library': 'si'},
                layers=[dict(material={'library': 'pmma'}, thickness_m=d, nonuniformity=nu)], roughness_m=0.0, backside='rough')


class RuntimeReviewRound1Regressions(unittest.TestCase):
    def test_F01_no_z_move_during_an_angle_change(self):
        h = ready(samples=(PMMA, CAL, W200))
        h.invoke('stage.move_z', z_m=rt.STAGE_Z_RANGE_M[0])
        settle(h)
        h.invoke('sample.place', sample_id='wafer_200', offset_x_m=0.0, offset_y_m=-0.052)
        h.invoke('aoi.set', position='90')
        h.step(0.2)
        rejected(h, 'stage.move_z', 'MOTION_IN_PROGRESS', z_m=rt.STAGE_Z_RANGE_M[1])
        settle(h)
        rejected(h, 'stage.move_z', 'COLLISION_RISK', z_m=rt.STAGE_Z_RANGE_M[1])       # the horizontal arms over the wafer

    def test_F02_F03_non_reflecting_samples_give_no_nan_and_never_break_the_clock(self):
        matched = dict(BARE, sample_id='matched', substrate={'cauchy': dict(A=1.0, B=0.0)})
        h = held(matched, samples=(PMMA, CAL, matched))
        op = h.invoke('measure', mode='standard', alignment='fixed_height', fixed_height_m=MZ - matched['substrate_thickness_m'],
                      model_id='cauchy_on_si')['operation_id']
        rec = wait(h, op)
        self.assertEqual((rec['status'], rec['reason'], rec['psi_rad']), ('failed', 'NO_SIGNAL', None))
        self.assertEqual(json.loads(json.dumps(h._out(rec), allow_nan=False))['record_id'], rec['record_id'])
        r = rt.normalised((np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)))
        self.assertTrue(np.all(r['depol'] == 1.0) and np.all(np.isfinite(r['N'])))
        good = measure(held())
        broken = dict(good, N=[float('nan')] * len(good['N']))
        with self.assertRaises(rt.ActionRejected) as cm:
            rt.run_fit([broken], 'cauchy_on_si', {}, ['film.thickness_m'], True)
        self.assertEqual(str(cm.exception), 'FIT_FAILED')

    def test_F04_thickness_non_uniformity_average_is_converged(self):
        lam = rt.wavelengths_nm()
        for d, nu in ((1e-6, 0.05), (5e-6, 0.02), (20e-6, 0.01)):
            a = rt.sample_mueller(_pmma_film(d, nu), lam, math.radians(70.0), bandwidth_ev=0.0, divergence_rad=0.0)
            ref = _dense_nonuniform(d, nu, lam, math.radians(70.0))
            self.assertLess(max(float(np.max(np.abs(a[k] - ref[k]))) for k in ('N', 'C', 'S')), 1e-6, (d, nu))

    def test_F05_bandwidth_average_is_converged_up_to_the_spectral_edges(self):
        lam = rt.wavelengths_nm()
        E, bw, g = rt.HC_EV_NM / lam, rt.BANDWIDTH_SIGMA_EV, rt.library()['grid']
        for d in (2e-6, 20e-6):
            a = rt.sample_mueller(_pmma_film(d), lam, math.radians(70.0), divergence_rad=0.0)
            z = np.linspace(-7, 7, 4001)
            w = np.exp(-0.5 * z * z)
            w /= w.sum()
            tot = (0.0, 0.0, 0.0, 0.0)
            for zi, wi in zip(z, w):
                lb = rt.HC_EV_NM / (E + zi * bw)                         # the tables continue linearly beyond 380 / 900 nm (edge pixels)
                rp, rs = rt.stack_rps(lb, np.ones(lam.shape, complex), [(rt.library_index('pmma', lb), d)], rt.library_index('si', lb), math.radians(70.0))
                tot = rt._add(tot, rt._pair(rp, rs), wi)
            ref = rt.normalised(tot)
            self.assertLess(max(float(np.max(np.abs(a[k] - ref[k]))) for k in ('N', 'C', 'S')), 5e-5, d)

    def test_F06_backside_beams_carry_the_film_transmission_phase(self):
        lam = rt.wavelengths_nm()
        th = math.radians(70.0)
        N0 = np.ones(lam.shape, complex)
        Nf, Ns = rt.library_index('tio2', lam).real + 0j, rt.library_index('n_bk7', lam).real + 0j
        kx = N0 * math.sin(th)

        def fres(Ni, Nj, pol):
            qi, qj = np.sqrt(Ni ** 2 - kx ** 2 + 0j), np.sqrt(Nj ** 2 - kx ** 2 + 0j)
            if pol == 's':
                return (qi - qj) / (qi + qj), 2 * qi / (qi + qj)
            return (Nj ** 2 * qi - Ni ** 2 * qj) / (Nj ** 2 * qi + Ni ** 2 * qj), 2 * Ni * Nj * qi / (Nj ** 2 * qi + Ni ** 2 * qj)

        def film_t(Na, Nb, Nc, pol, d=80e-9):                          # independent Airy sum of the E-field transmission
            _, t_ab = fres(Na, Nb, pol)
            _, t_bc = fres(Nb, Nc, pol)
            r_ba, _ = fres(Nb, Na, pol)
            r_bc, _ = fres(Nb, Nc, pol)
            e = np.exp(1j * 2 * math.pi / (lam * 1e-9) * np.sqrt(Nb ** 2 - kx ** 2 + 0j) * d)
            return t_ab * t_bc * e / (1 - r_ba * r_bc * e * e)
        amp = {pol: film_t(Ns, Nf, N0, pol) * fres(Ns, N0, pol)[0] * film_t(N0, Nf, Ns, pol) for pol in ('p', 's')}
        ref = rt._pair(amp['p'], amp['s'], 1.0)
        got = rt._backside(lam, N0, [(Nf, 80e-9)], Ns, kx, 1e-9, th, orders=1)
        self.assertLess(max(float(np.max(np.abs(np.asarray(a) - np.asarray(b)))) for a, b in zip(got, ref)), 1e-12)
        self.assertGreater(float(np.max(np.abs(np.asarray(ref[3])))), 1e-4)            # the transmission phase is not zero for a coating

    def test_F07_unphysical_dispersion_models_are_rejected(self):
        h = configured()
        for m in ({'cauchy': dict(A=1.0, B=-5e-13, k_amp=0.1)}, {'cauchy': dict(A=1.0, B=-5e-13)}):     # n < 0 in the blue (with and without a tail)
            rejected(h, 'sample.provide', 'UNPHYSICAL_MATERIAL', **dict(BARE, sample_id='bad', substrate=m))
        self.assertTrue(rt.material_physical({'cauchy': dict(A=1.46, B=4e-15)}, rt.library()['grid']))

    def test_F09_failed_or_cancelled_records_keep_no_data_and_the_fit_survives_hardware_faults(self):
        h = held()
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        op = s.invoke(instance_id=h.instance_id, capability_id='measure', arguments=dict(mode='fast', alignment='standard', fixed_height_m=None,
                                                                                      model_id='cauchy_on_si'), request_id='m1')['operation_id']
        _into(h, 'fitting', 0.5)
        s.cancel(op, 'c1')
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual((rec['status'], rec['psi_rad'], rec['N'], rec['fit']), ('cancelled', None, None, None))
        for fault in ('lamp_failure', 'vacuum_leak'):
            g = held()
            op = g.invoke('measure', mode='fast', alignment='standard', fixed_height_m=None, model_id='cauchy_on_si')['operation_id']
            _into(g, 'fitting', 0.5)
            g.invoke('fault.inject', fault=fault, active=True)
            rec = wait(g, op)
            self.assertEqual(rec['status'], 'completed', fault)
            self.assertIsNotNone(rec['fit'], fault)
        g = held()
        op = g.invoke('measure', mode='fast', alignment='standard', fixed_height_m=None, model_id='cauchy_on_si')['operation_id']
        _into(g, 'fitting', 0.5)
        g.invoke('power.set', enabled=False)                                # the workstation fit goes on
        self.assertEqual(wait(g, op)['status'], 'completed')

    def test_F10_undetermined_parameters_get_no_confidence_limit(self):
        h = _scenario('measured')
        out = h.invoke('analysis.fit', record_ids=['SE0001'], model_id='cauchy_urbach_on_si', parameters={'film.k_amp': 0.0},
                       fit=['film.thickness_m', 'film.A', 'film.B', 'film.k_exp'], thickness_prefit=True)
        f = h.records[out['record_id']]['fit']
        self.assertIsNone(out['parameters']['film.k_exp']['ci90'])
        self.assertIn('parameter_not_determined:film.k_exp', f['warnings'])
        self.assertTrue(all(abs(f['correlation'][i][i] - 1.0) < 1e-12 for i in range(4)))
        self.assertGreater(out['parameters']['film.thickness_m']['ci90'], 0.0)

    def test_F12_F19_steps_advance_the_revision_and_are_bounded(self):
        h = ready()
        rev = h.revision
        h.step(1.0)
        self.assertEqual(h.revision, rev + 1)
        h.step(0.0)
        self.assertEqual(h.revision, rev + 1)
        for bad in (1e308, rt.MAX_STEP_S * 2):
            with self.assertRaises(rt.ActionRejected):
                h.step(bad)

    def test_F13_tauc_lorentz_eps1_equals_the_kramers_kronig_integral(self):
        from scipy.integrate import quad
        E = np.array([1.5, 2.5, 3.2])
        for p in ((1.0, 0.1, 500.0, 3.0, 2.0), (2.0, 0.8, 50.0, 2.0, 0.5), (1.0, 2.0, 100.0, 3.0, 8.0)):
            e1 = rt.tauc_lorentz_eps(E, *p).real
            for Ei, ei in zip(E, e1):
                pv = quad(lambda x: x * float(rt._tl_eps2(np.array([x]), *p[1:])[0]) / (x + Ei), p[1], 3000.0, weight='cauchy', wvar=Ei, limit=600)[0]
                self.assertAlmostEqual(ei, p[0] + 2 / math.pi * pv, delta=1e-4, msg=p)

    def test_F14_F15_F16_vacuum_liquid_cell_and_lamp_replacement(self):
        h = ready()
        h.invoke('sample.place', sample_id='pmma_on_si', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('stage.move_z', z_m=rt.STAGE_Z_RANGE_M[0])
        settle(h)
        h.invoke('aoi.set', position='90')
        settle(h)
        op = h.invoke('measure', mode='standard', alignment='none', fixed_height_m=None, model_id=None)['operation_id']
        _into(h, 'acquiring', 0.5)
        h.invoke('fault.inject', fault='vacuum_leak', active=True)
        rec = wait(h, op)
        self.assertEqual(rec['status'], 'completed')
        self.assertNotIn('vacuum_lost', rec['warnings'])                   # the straight-through beam does not need the vacuum
        c = ready(lamp_age_s=1900 * 3600.0)
        c.invoke('liquid_cell.install', sample_id='pmma_on_si')
        c.invoke('vacuum.set', on=True)
        rejected(c, 'liquid_cell.remove', 'SAMPLE_HELD')
        old = c.read('evaluator')['truth.lamp_intensity_rel']
        c.invoke('fault.inject', fault='lamp_failure', active=True)
        c.invoke('fault.inject', fault='lamp_failure', active=False)
        c.invoke('system.acknowledge')
        c.step(900.0)
        self.assertLess(old, 0.8)
        new = (1.0 - rt.LAMP_END_OF_LIFE_LOSS * 900.0 / rt.LAMP_RATED_S) * (1.0 - rt.LAMP_WARMUP_DEFICIT * math.exp(-900.0 / rt.LAMP_WARMUP_TAU_S))
        self.assertAlmostEqual(c.read('evaluator')['truth.lamp_intensity_rel'], new, delta=1e-9)
        self.assertAlmostEqual(c.read()['lamp.on_time_s'], 900.0, places=6)

    def test_F17_guard_agrees_with_the_collision_proxies(self):
        proxies = json.loads((ROOT / 'assets/expansion_batch04/t3_analytical/ja_alphase/collision_proxies.json').read_text())['components']
        box = proxies['detector_housing']['primitives'][0]
        a_in = box['origin_m'][0] - box['size_m'][0] / 2 - rt.MEASURE_POINT_M[0]
        h_lo = box['origin_m'][2] - box['size_m'][2] / 2 - rt.MEASURE_POINT_M[2]
        self.assertAlmostEqual(a_in, rt.ARM_BODY_R_M[0], places=6)
        self.assertAlmostEqual(h_lo, -rt.ARM_HALF_HEIGHT_M, places=6)
        for aoi in (65.0, 70.0, 75.0, 90.0):
            e = math.radians(90.0 - aoi)
            c, s_ = math.cos(e), math.sin(e)
            corner_x = a_in * c + rt.ARM_HALF_HEIGHT_M * s_                     # inner lower corner of the proxy box
            corner_z = rt.MEASURE_POINT_M[2] + a_in * s_ - rt.ARM_HALF_HEIGHT_M * c
            self.assertLessEqual(rt.arm_lowest_z(e, corner_x - 1e-4, corner_x + 1e-4), corner_z + 1e-9, aoi)

    def test_F18_bandwidth_option_of_the_models_for_thick_films(self):
        thick = dict(BARE, sample_id='thick', layers=[dict(material={'cauchy': dict(A=1.49, B=4.6e-15)}, thickness_m=5e-6)])
        h = held(thick, samples=(PMMA, CAL, thick))
        rec = measure(h)
        truth = {'film.thickness_m': 5e-6, 'film.A': 1.49, 'film.B': 4.6e-15}
        plain = h.invoke('analysis.fit', record_ids=[rec['record_id']], model_id='cauchy_on_si', parameters=truth, fit=['film.thickness_m', 'film.A', 'film.B'],
                         thickness_prefit=False)
        opt = h.invoke('analysis.fit', record_ids=[rec['record_id']], model_id='cauchy_on_si', parameters=dict(truth, **{'options.bandwidth': 1.0, 'options.divergence': 1.0}),
                       fit=['film.thickness_m', 'film.A', 'film.B'], thickness_prefit=False)
        self.assertLess(opt['mse'] * 5, plain['mse'])
        self.assertLess(opt['mse'], 6.0)

    def test_F20_unsupported_whatever_the_arguments_and_prefit_inside_the_bounds(self):
        h = ready()
        h.register_principal('agent', 'agent')
        r = h.session('agent').invoke(instance_id=h.instance_id, capability_id='hardware.connect', arguments={'port': 'x'}, request_id='u1')
        self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))
        g = _scenario('measured')
        out = g.invoke('analysis.fit', **dict(FIT, parameters={'film.thickness_m': 15e-6}))
        self.assertLessEqual(g.records[out['record_id']]['fit']['prefit']['grid_max'], 20e-6)

    def test_F21_the_smallest_documented_samples_can_be_measured(self):
        five = dict(TINY, sample_id='five', size_m=[0.005, 0.005])
        h = held(five, samples=(PMMA, CAL, five))
        self.assertFalse(h.read('evaluator')['sample.held'])
        rec = measure(h, mode='fast', alignment='robust')
        self.assertEqual(rec['status'], 'completed')
        self.assertIn('sample_not_held', rec['warnings'])


# ---------------------------------------------------------------- scenario states and declared effects
def _build(name):
    if name == 'fresh':
        return rt.JaAlphaseRuntime()
    if name == 'off':
        return configured()
    if name == 'booting':
        h = configured()
        h.invoke('power.set', enabled=True)
        h.step(2.0)
        return h
    if name == 'warming':
        h = _scenario('booting')
        h.step(30.0)
        return h
    if name == 'ready':
        return ready()
    if name == 'placed':
        h = ready()
        h.invoke('sample.place', sample_id='pmma_on_si', offset_x_m=0.0, offset_y_m=0.0)
        return h
    if name == 'held':
        h = _scenario('placed')
        h.invoke('vacuum.set', on=True)
        return h
    if name in ('aligning', 'acquiring', 'fitting'):
        h = _scenario('held')
        h.invoke('measure', mode='standard', alignment='standard', fixed_height_m=None, model_id='cauchy_on_si')
        return _into(h, name, 0.5)
    if name == 'measured':
        h = _scenario('held')
        measure(h, model_id='cauchy_on_si')
        return h
    if name == 'mm_measuring':
        h = _scenario('measured')
        h.invoke('measure', mode='mm', alignment='none', fixed_height_m=None, model_id=None)
        return _into(h, 'acquiring', 0.5)
    if name == 'analysed':
        h = _scenario('measured')
        h.invoke('analysis.fit', **FIT)
        return h
    if name == 'aoi_changing':
        h = _scenario('held')
        h.invoke('aoi.set', position='65')
        h.step(1.2)
        return h
    if name == 'stage_moving':
        h = _scenario('held')
        h.invoke('stage.move_z', z_m=MZ - 0.005)
        h.step(1.0)
        return h
    if name == 'straight':
        h = ready()
        h.invoke('aoi.set', position='90')
        return settle(h)
    if name == 'straight_measuring':
        h = _scenario('straight')
        h.invoke('measure', mode='standard', alignment='none', fixed_height_m=None, model_id=None)
        return _into(h, 'acquiring', 0.5)
    if name == 'straight_done':
        h = _scenario('straight')
        measure(h, alignment='none')
        return h
    if name == 'straight_off':                                              # a valid %R baseline, power off: plant.configure is allowed
        h = _scenario('straight_done')
        h.invoke('power.set', enabled=False)
        return h
    if name == 'cal_held':
        h = ready()
        h.invoke('sample.place', sample_id='cal_wafer', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('vacuum.set', on=True)
        return h
    if name == 'calibrating':
        h = _scenario('cal_held')
        h.invoke('calibration.run')
        return _into(h, 'acquiring', 0.5)
    if name == 'calibrated':
        h = _scenario('cal_held')
        wait(h, h.invoke('calibration.run')['operation_id'])
        return h
    if name == 'cell_installed':
        h = ready()
        h.invoke('liquid_cell.install', sample_id='pmma_on_si')
        return h
    if name == 'cell_filled':
        h = _scenario('cell_installed')
        h.invoke('liquid_cell.fill', liquid_id='water', volume_m3=450e-9)
        h.invoke('vacuum.set', on=True)
        return h
    if name == 'cell_measuring':
        h = _scenario('cell_filled')
        h.invoke('measure', mode='fast', alignment='standard', fixed_height_m=None, model_id=None)
        return _into(h, 'acquiring', 0.5)
    if name == 'lamp_fault':
        h = _scenario('held')
        h.invoke('fault.inject', fault='lamp_failure', active=True)
        return h
    if name == 'lamp_replaced':                                           # the fault is gone, the latched error still waits for acknowledgement
        h = _scenario('lamp_fault')
        h.invoke('fault.inject', fault='lamp_failure', active=False)
        return h
    if name == 'stall_fault':
        h = _scenario('held')
        h.invoke('fault.inject', fault='stage_stall', active=True)
        return h
    if name == 'leak_fault':
        h = _scenario('held')
        h.invoke('fault.inject', fault='vacuum_leak', active=True)
        return h
    if name == 'plunger_fault':
        h = _scenario('held')
        h.invoke('fault.inject', fault='plunger_not_seated', active=True)
        return h
    if name == 'vacuum_on_empty':                                         # placing onto a running vacuum changes held / pressure at once
        h = ready()
        h.invoke('vacuum.set', on=True)
        return h
    if name == 'weak_vacuum_placed':                                      # supply too weak to hold: removal still changes the pressure
        h = ready(vacuum_supply_pa=70.0e3)
        h.invoke('sample.place', sample_id='pmma_on_si', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('vacuum.set', on=True)
        return h
    if name == 'unpowered_lamp_fault':                                    # a fault present at power-up latches its error
        h = configured()
        h.invoke('fault.inject', fault='lamp_failure', active=True)
        return h
    if name == 'unpowered_stall':
        h = configured()
        h.invoke('fault.inject', fault='stage_stall', active=True)
        return h
    if name == 'weak_vacuum_cell':                                        # cell on a vacuum too weak to hold it: removal changes the pressure
        h = ready(vacuum_supply_pa=70.0e3)
        h.invoke('liquid_cell.install', sample_id='pmma_on_si')
        h.invoke('vacuum.set', on=True)
        return h
    if name == 'cell_held':
        h = _scenario('cell_installed')
        h.invoke('vacuum.set', on=True)
        return h
    if name == 'unpowered_placed':
        h = configured()
        h.invoke('sample.place', sample_id='pmma_on_si', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('vacuum.set', on=True)
        return h
    raise KeyError(name)


def _stack_sample(layers, sub='si', size=0.05, backside='rough'):
    return dict(sample_id='stack', kind='film_stack', shape='disc', size_m=[size, size], substrate_thickness_m=0.0005, substrate={'library': sub},
                layers=[dict(material={'library': m} if isinstance(m, str) else m, thickness_m=d, nonuniformity=nu) for m, d, nu in layers], roughness_m=0.0,
                backside=backside)


def _dense_tensor(s, theta, n_nu=1, n_dv=1, n_b=1):
    """Independent reference: trapezoid tensor over (thickness |z| <= 7, angle |z| <= 6, band |z| <= 6) with the stack evaluated at every node."""
    lam = rt.wavelengths_nm()
    E = rt.HC_EV_NM / lam

    def rule(n, Z):
        if n == 1:
            return np.zeros(1), np.ones(1)
        z = np.linspace(-Z, Z, n)
        w = np.exp(-0.5 * z * z)
        return z, w / w.sum()
    (zu, wu), (za, wa), (zb, wb) = rule(n_nu, 7.0), rule(n_dv, 6.0), rule(n_b, 6.0)
    tot = [np.zeros(lam.size) for _ in range(4)]
    for b, w_b in zip(zb, wb):
        lb = rt.HC_EV_NM / (E + b * rt.BANDWIDTH_SIGMA_EV)
        N0, layers, Ns = rt.stack_media(s, lb, np.ones(lb.shape, complex))
        th = (theta + za * rt.BEAM_DIVERGENCE_RAD)[None, :, None]
        lay = [(N[None, None], d * np.clip(1 + sg * zu, 0, None)[:, None, None]) for N, d, sg in layers]
        m = rt._pair(*rt.stack_rps(lb[None, None], N0[None, None], lay, Ns[None, None], th))
        wgt = wu[:, None, None] * wa[None, :, None] * w_b
        tot = [t + np.sum(np.broadcast_to(x, (len(zu), len(za), lam.size)) * wgt, axis=(0, 1)) for t, x in zip(tot, m)]
    return rt.normalised(tuple(tot))


def _ncs_err(a, b):
    return max(float(np.max(np.abs(a[k] - b[k]))) for k in ('N', 'C', 'S'))


class RuntimeReviewRound2Regressions(unittest.TestCase):
    def test_F04_non_uniformity_converged_on_reflecting_substrates_and_at_the_largest_spread(self):
        lam = rt.wavelengths_nm()
        th = math.radians(70.0)
        for sub, mat, d, nu, n in (('ag', 'sio2', 1e-6, 0.0131, 4001), ('al', 'sio2', 1e-6, 0.0131, 4001), ('si', 'sio2', 1e-6, 0.0131, 4001),
                                   ('si', 'pmma', 20e-6, rt.NONUNIFORMITY_MAX, 40001)):
            s = _stack_sample([(mat, d, nu)], sub)
            v = rt.sample_mueller(s, lam, th, bandwidth_ev=0.0, divergence_rad=0.0, backside=False)
            ref = _dense_tensor(s, th, n_nu=n)
            self.assertLess(_ncs_err(v, ref), 1e-6, (sub, mat, d, nu))
        with self.assertRaises(rt.ActionRejected):                      # the sample model's non-uniformity range
            rt.validate(rt.CAPS['sample.provide']['input_schema'], dict(rt.SAMPLE_EXAMPLE, layers=[dict(material={'library': 'pmma'}, thickness_m=1e-6,
                                                                                                      nonuniformity=0.2)]))

    def test_F25_divergence_average_is_exact_and_converged(self):
        lam = rt.wavelengths_nm()
        for aoi, sub, mat, d in ((70.0, 'si', 'pmma', 20e-6), (75.0, 'al', 'sio2', 20e-6), (70.0, 'si', 'pmma', 2e-6)):
            th = math.radians(aoi)
            s = _stack_sample([(mat, d, 0.0)], sub)
            v = rt.sample_mueller(s, lam, th, bandwidth_ev=0.0, backside=False)
            ref = _dense_tensor(s, th, n_dv=2001)
            self.assertLess(_ncs_err(v, ref), 1e-6, (aoi, sub, mat, d))

    def test_joint_average_matches_an_independent_tensor_reference(self):
        lam = rt.wavelengths_nm()
        th = math.radians(70.0)
        s = _stack_sample([('pmma', 1e-6, 0.02)])
        v = rt.sample_mueller(s, lam, th, backside=False)
        ref = _dense_tensor(s, th, n_nu=801, n_dv=41, n_b=161)
        self.assertLess(_ncs_err(v, ref), 2e-5)

    def test_F24_model_with_options_reproduces_the_truth(self):
        lam = rt.wavelengths_nm()
        th = math.radians(70.0)
        cauchy = {'cauchy': dict(A=1.49, B=4.6e-15, C=0.0)}
        for d, nu in ((20e-6, 0.0), (5e-6, 0.002)):
            s = _stack_sample([(cauchy, d, nu)])
            truth = rt.sample_mueller(s, lam, th)
            mdl = rt.get_model('cauchy_on_si')
            p = mdl.defaults()
            p.update({'film.thickness_m': d, 'film.A': 1.49, 'film.B': 4.6e-15, 'film.C': 0.0, 'film.nonuniformity': nu, 'options.bandwidth': 1.0,
                      'options.divergence': 1.0})
            N, C, S = rt.model_ncs(mdl, p, dict(record_id='x', lam=lam, theta=th, amb=np.ones(lam.shape, complex)), {})
            self.assertLess(_ncs_err(dict(N=N, C=C, S=S), truth), 1e-4, d)
            cache = {}
            mdl.media(p, lam, cache)
            mdl.media(p, np.stack([lam * 0.999, lam]), cache)
            self.assertEqual(len(cache), 6)                              # keyed by the wavelengths as well: film, interface, substrate on two grids

    def test_F23_work_is_bounded_and_capped_averages_are_flagged(self):
        lam = rt.wavelengths_nm()
        s = _stack_sample([('pmma', 20e-6, 0.05)] * 8, size=0.1)
        info = {}
        t0 = time.time()
        rt.sample_mueller(s, lam, math.radians(70.0), info=info)
        self.assertLess(time.time() - t0, 60.0)
        self.assertTrue(info['capped'])
        h = held(dict(s, sample_id='stack8'))
        t0 = time.time()
        rec = measure(h, mode='fast', alignment='robust')
        self.assertLess(time.time() - t0, 90.0)
        self.assertEqual((rec['status'], rec['reason'], rec['psi_rad']), ('failed', 'SIMULATION_LIMIT', None))    # no unconverged spectrum is delivered
        self.assertIn('SIMULATION_LIMIT', [e['code'] for e in rt.export_ja_alphase_contract_metadata()['capabilities']['measure']['errors']])

    def test_F32_an_incoherent_layer_is_averaged_over_one_fringe_and_converges(self):
        lam = np.array([380.0, 393.0, 561.0, 800.0, 900.0])
        th = math.radians(70.0)
        s = _stack_sample([('pmma', 20e-6, rt.NONUNIFORMITY_MAX)])
        info = {}
        v = rt.sample_mueller(s, lam, th, backside=False, info=info)
        self.assertFalse(info['capped'])
        self.assertIn('phase', info['levels'])                           # the uniform one-fringe average of the incoherent layer
        E = rt.HC_EV_NM / lam
        zu = np.linspace(-7, 7, 40001); wu = np.exp(-zu ** 2 / 2); wu /= wu.sum()
        za = np.linspace(-6, 6, 21); wa = np.exp(-za ** 2 / 2); wa /= wa.sum()
        zb = np.linspace(-6, 6, 41); wb = np.exp(-zb ** 2 / 2); wb /= wb.sum()
        tot = [np.zeros(lam.size) for _ in range(4)]
        for b, w_b in zip(zb, wb):
            lb = rt.HC_EV_NM / (E + b * rt.BANDWIDTH_SIGMA_EV)
            N0, layers, Ns = rt.stack_media(s, lb, np.ones(lb.shape, complex))
            for i0 in range(0, len(zu), 4000):
                zc, wc = zu[i0:i0 + 4000], wu[i0:i0 + 4000]
                lay = [(N[None, None], d * (1 + sg * zc)[:, None, None]) for N, d, sg in layers]
                m = rt._pair(*rt.stack_rps(lb[None, None], N0[None, None], lay, Ns[None, None], (th + za * rt.BEAM_DIVERGENCE_RAD)[None, :, None]))
                wgt = wc[:, None, None] * wa[None, :, None] * w_b
                tot = [t + np.sum(np.broadcast_to(x, (len(zc), len(za), lam.size)) * wgt, axis=(0, 1)) for t, x in zip(tot, m)]
        self.assertLess(_ncs_err(v, rt.normalised(tuple(tot))), 1e-6)

    def test_F33_model_with_options_matches_the_truth_for_a_thick_non_uniform_film(self):
        lam = rt.wavelengths_nm()
        th = math.radians(70.0)
        s = _stack_sample([({'cauchy': dict(A=1.49, B=4.6e-15, C=0.0)}, 5e-6, 0.05)])
        truth = rt.sample_mueller(s, lam, th)
        mdl = rt.get_model('cauchy_on_si')
        p = dict(mdl.defaults(), **{'film.thickness_m': 5e-6, 'film.A': 1.49, 'film.B': 4.6e-15, 'film.C': 0.0, 'film.nonuniformity': 0.05,
                                    'options.bandwidth': 1.0, 'options.divergence': 1.0})
        N, C, S = rt.model_ncs(mdl, p, dict(record_id='x', lam=lam, theta=th, amb=np.ones(lam.shape, complex)), {})
        self.assertLess(_ncs_err(dict(N=N, C=C, S=S), truth), 1e-4)

    def test_F22_plunger_fault_in_the_calibration_fit_does_not_wedge_the_clock(self):
        h = ready()
        h.invoke('sample.place', sample_id='cal_wafer', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('vacuum.set', on=True)
        op = h.invoke('calibration.run')['operation_id']
        _into(h, 'fitting', 0.5)
        h.invoke('fault.inject', fault='plunger_not_seated', active=True)
        for _ in range(4):
            h.step(1.0)
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual(rec['status'], 'completed')
        self.assertEqual(rec['calibration']['position'], '70')
        self.assertEqual(h.cal['status'], 'calibrated')
        h.register_principal('ev', 'evaluator')
        r = h.session('ev').invoke(instance_id=h.instance_id, capability_id='sim.step', arguments=dict(dt_s=1.0), request_id='s')
        self.assertEqual(r['status'], 'succeeded')

    def test_defects_become_DEVICE_ERROR_instead_of_raw_exceptions(self):
        h = ready()
        h.invoke('sample.place', sample_id='cal_wafer', offset_x_m=0.0, offset_y_m=0.0)
        op = h.invoke('calibration.run')['operation_id']
        _into(h, 'fitting', 0.5)

        def broken(o, rec):
            raise RuntimeError('simulated defect')
        h._calibrate = broken
        h.step(5.0)
        self.assertEqual(h.operations[op]['phase'], 'failed')
        self.assertEqual(h.operations[op]['reason'], 'DEVICE_ERROR')
        self.assertIsNone(h.op)
        h.step(1.0)                                                      # the clock goes on
        h.register_principal('agent', 'agent')

        def boom():
            raise RuntimeError('simulated defect')
        h._cmd_records_list = boom
        r = h.session('agent').invoke(instance_id=h.instance_id, capability_id='records.list', arguments={}, request_id='l')
        self.assertEqual((r['status'], r['error']['code'], r['error']['side_effects']), ('rejected', 'DEVICE_ERROR', 'unknown'))
        self.assertIn('DEVICE_ERROR', [e['code'] for e in rt.export_ja_alphase_contract_metadata()['capabilities']['records.list']['errors']])

    def test_F26_guard_checks_the_beam_side_faces_of_the_ports(self):
        block = dict(sample_id='block', kind='film_stack', shape='rectangle', size_m=[0.177, 0.03], substrate_thickness_m=0.016, substrate={'library': 'soda_lime'},
                     layers=[], roughness_m=0.0, backside='rough')
        h = ready(samples=(PMMA, CAL, block))
        h.invoke('aoi.set', position='75')
        settle(h)
        h.invoke('sample.place', sample_id='block', offset_x_m=0.0, offset_y_m=0.0)
        rejected(h, 'stage.move_z', 'COLLISION_RISK', z_m=rt.STAGE_Z_RANGE_M[1])
        rejected(h, 'measure', 'COLLISION_RISK', mode='fast', alignment='fixed_height', fixed_height_m=rt.STAGE_Z_RANGE_M[1], model_id=None)
        rng = np.random.default_rng(3)
        worst = math.inf
        for _ in range(3000):                                            # every accepted pose keeps 2 mm to the densely sampled proxy boxes
            el = math.radians(90.0 - float(rng.choice([65.0, 70.0, 75.0, 90.0])))
            half = float(rng.uniform(0.0025, 0.1))
            ztop = float(rng.uniform(rt.STAGE_Z_RANGE_M[0], rt.STAGE_Z_RANGE_M[1] + 0.016))
            x0, x1 = -half, half
            if not rt.arm_lowest_z(el, x0 - rt.CLEARANCE_M, x1 + rt.CLEARANCE_M) > ztop + rt.CLEARANCE_M:
                continue
            for poly in rt.arm_polygons(el):
                P = np.array(poly + poly[:1])
                for (xa, za), (xb, zb) in zip(P[:-1], P[1:]):
                    f = np.linspace(0.0, 1.0, 400)
                    xs, zs = xa + f * (xb - xa), za + f * (zb - za)
                    dx = np.maximum(np.maximum(x0 - xs, xs - x1), 0.0)
                    gap = np.where(dx > 0, np.hypot(dx, np.maximum(zs - ztop, 0.0)), zs - ztop)
                    worst = min(worst, float(gap.min()))
        self.assertGreater(worst, rt.CLEARANCE_M * 0.999)

    def test_F27_negative_noise_seed_is_rejected_before_any_change(self):
        h = rt.JaAlphaseRuntime()
        rejected(h, 'plant.configure', 'INVALID_ARGUMENT', **dict(rt.PLANT_EXAMPLE, noise_seed=-1))
        self.assertIsNone(h.plant)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=2 ** 32 - 1))
        self.assertTrue(h.read()['plant.configured'])

    def test_F28_plant_configure_invalidates_the_baseline_and_declares_it(self):
        h = ready()
        straight_through(h)
        self.assertTrue(h.read()['calibration.baseline']['valid'])
        h.invoke('power.set', enabled=False)
        h.invoke('plant.configure', **rt.PLANT_EXAMPLE)
        self.assertFalse(h.read()['calibration.baseline']['valid'])
        self.assertIn('calibration.baseline', rt.EFFECTS['plant.configure'])

    def test_F29_a_leak_with_the_vacuum_off_leaves_no_warning(self):
        h = ready()
        h.invoke('sample.place', sample_id='pmma_on_si', offset_x_m=0.0, offset_y_m=0.0)
        op = h.invoke('measure', mode='standard', alignment='standard', fixed_height_m=None, model_id=None)['operation_id']
        _into(h, 'acquiring', 0.5)
        h.invoke('fault.inject', fault='vacuum_leak', active=True)
        rec = wait(h, op)
        self.assertNotIn('vacuum_lost', rec['warnings'])
        g = held()
        op = g.invoke('measure', mode='standard', alignment='standard', fixed_height_m=None, model_id=None)['operation_id']
        _into(g, 'acquiring', 0.5)
        g.invoke('fault.inject', fault='vacuum_leak', active=True)
        self.assertIn('vacuum_lost', wait(g, op)['warnings'])            # evaluator view; agents do not get it (C05)

    def test_F30_clearing_an_inactive_lamp_fault_changes_nothing(self):
        h = ready()
        before = h.read('evaluator')
        n_ev = len(h.events)
        h.invoke('fault.inject', fault='lamp_failure', active=False)
        self.assertFalse(_changed(h.read('evaluator'), before))
        self.assertEqual(len(h.events), n_ev)


class ContractReviewRound1Regressions(unittest.TestCase):
    def test_C03_every_parameter_name_has_one_declared_unit(self):
        cat = rt.models_catalog()
        models = [rt.get_model(k) for k in rt._static_models()] + [rt.get_model(k) for k in
                                                                    ('library_film:pmma@si', 'library_film:sio2@soda_lime', 'library_bulk:au', 'cauchy_on:n_bk7')]
        for m in models:
            for n, P in m.params.items():
                self.assertEqual(rt.PARAM_UNITS.get(n), P.unit, (m.model_id, n))
        for k, v in cat.items():
            self.assertEqual(sorted(v['parameter_units']), v['parameters'], k)
            self.assertTrue(all(rt.PARAM_UNITS[n] == u for n, u in v['parameter_units'].items()), k)
        out_schema = rt.CAPS['analysis.fit']['output_schema']['properties']['parameters']
        in_schema = rt.CAPS['analysis.fit']['input_schema']['properties']['parameters']
        for n, u in rt.PARAM_UNITS.items():
            self.assertEqual(out_schema['properties'][n]['properties']['value']['x-unit'], u)
            self.assertEqual(in_schema['properties'][n]['x-unit'], u)
        self.assertEqual(rt.PARAM_UNITS['film.A'], '1')                  # Cauchy A; the Tauc-Lorentz amplitude is film.A_ev
        self.assertEqual(rt.PARAM_UNITS['film.A_ev'], 'eV')
        h = held()
        rec = measure(h)
        out = h.invoke('analysis.fit', **dict(FIT, record_ids=[rec['record_id']]))
        for n, v in out['parameters'].items():
            self.assertEqual(v['unit'], rt.PARAM_UNITS[n], n)
        rt.validate(rt.CAPS['analysis.fit']['input_schema'], dict(FIT, record_ids=[rec['record_id']]))
        uq = rt.CAPS['analysis.uniqueness']
        self.assertEqual(uq['output_schema']['properties']['best_value']['x-unit'], 'm')
        self.assertEqual(uq['input_schema']['properties']['values']['items']['x-unit'], 'm')
        self.assertTrue(all(rt.PARAM_UNITS[n] == 'm' for n in uq['input_schema']['properties']['scan_parameter']['enum']))
        rejected(h, 'analysis.uniqueness', 'INVALID_ARGUMENT', record_ids=[rec['record_id']], model_id='cauchy_on_si', parameters={}, fit=['film.thickness_m'],
                 scan_parameter='film.A', values=[1.4, 1.5])

    def test_C04_pressures_heights_angles_and_offsets_name_their_reference(self):
        meta = rt.export_ja_alphase_contract_metadata()
        for k in ('vacuum.pressure_pa', 'stage.z_m', 'stage.z_target_m', 'stage.last_aligned_z_m', 'aoi.arm_elevation_rad', 'sample.on_stage',
                  'calibration.table', 'truth.aoi_rad', 'liquid_cell.volume_m3'):
            self.assertTrue(meta['state_slots'][k].get('reference_conditions'), k)
        self.assertIn('绝对压力', meta['state_slots']['vacuum.pressure_pa']['reference_conditions'])
        self.assertEqual(rt.CAPS['plant.configure']['input_schema']['properties']['vacuum_supply_pa']['x-reference-conditions'], '绝对压力')

    def test_C05_the_chuck_vacuum_is_process_truth_not_an_agent_reading(self):
        h = held()
        h.register_principal('agent', 'agent')
        h.register_principal('eval', 'evaluator')
        s, e = h.session('agent'), h.session('eval')
        iid = h.instance_id
        for k in ('vacuum.pressure_pa', 'sample.held'):
            self.assertEqual(rt.SLOTS[k]['origin'], 'process_truth')
            self.assertNotIn(k, s.read(instance_id=iid)['observations'])
            self.assertNotIn(k, s.describe(instance_id=iid)['state_slots'])
            self.assertIn(k, e.read(instance_id=iid)['observations'])
        self.assertTrue(all(k not in ('vacuum.pressure_pa', 'sample.held') for c in s.describe(instance_id=iid)['capabilities'].values()
                            for k in [x['state_id'] for x in c['state_effects']] + c['observes']))
        before = s.read(instance_id=iid)['observations']
        e.invoke(instance_id=iid, capability_id='fault.inject', arguments=dict(fault='vacuum_leak', active=True), request_id='f1')
        h.step(1.0)
        after = s.read(instance_id=iid)['observations']
        self.assertEqual({k: v['value'] for k, v in before.items() if k not in ('clock.time_s', 'lamp.on_time_s')},
                         {k: v['value'] for k, v in after.items() if k not in ('clock.time_s', 'lamp.on_time_s')})
        self.assertFalse(e.read(instance_id=iid)['observations']['sample.held']['value'])
        r = s.invoke(instance_id=iid, capability_id='vacuum.set', arguments=dict(on=True), request_id='v1')
        self.assertEqual(set(r['result']), {'on'})
        r = s.invoke(instance_id=iid, capability_id='measure', arguments=dict(mode='fast', alignment='standard', fixed_height_m=None, model_id=None), request_id='m1')
        rec = wait(h, r['operation_id'])
        self.assertIn('sample_not_held', rec['warnings'])                  # the runtime record (evaluator view) keeps it
        mine = s.invoke(instance_id=iid, capability_id='measurement.result', arguments=dict(record_id=rec['record_id']), request_id='r1')['result']
        full = e.invoke(instance_id=iid, capability_id='measurement.result', arguments=dict(record_id=rec['record_id']), request_id='r2')['result']
        self.assertNotIn('sample_not_held', mine['warnings'])
        self.assertIn('sample_not_held', full['warnings'])
        self.assertNotIn('sample_not_held', s.poll(r['operation_id'])['result']['warnings'])
        ev = s.invoke(instance_id=iid, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e1')['result']['events']
        self.assertFalse([x for x in ev if 'kPa' in x['message_zh'] or x['kind'] == 'fault'])


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'off', 'booting', 'warming', 'ready', 'placed', 'held', 'aligning', 'acquiring', 'fitting', 'measured', 'mm_measuring', 'analysed',
             'aoi_changing', 'stage_moving', 'straight', 'straight_measuring', 'straight_done', 'cal_held', 'calibrating', 'calibrated', 'cell_installed',
             'cell_filled', 'cell_measuring', 'lamp_fault', 'lamp_replaced', 'stall_fault', 'leak_fault', 'plunger_fault', 'unpowered_placed', 'vacuum_on_empty',
             'weak_vacuum_placed', 'unpowered_lamp_fault', 'unpowered_stall', 'cell_held', 'weak_vacuum_cell', 'straight_off']
LONG_SCENARIOS = ['warming', 'held', 'measured', 'straight', 'cal_held', 'cell_filled', 'lamp_replaced', 'unpowered_placed', 'unpowered_lamp_fault']
STEP_DTS = (0.4, 1.5, 6.0, 60.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(rt.PLANT_EXAMPLE, calibration='nominal', noise_seed=2), dict(rt.PLANT_EXAMPLE, mains_voltage_v=60.0),
                            dict(rt.PLANT_EXAMPLE, vacuum_supply_pa=90.0e3, lamp_age_s=1500.0 * 3600.0)],
        'sample.provide': [dict(rt.SAMPLE_EXAMPLE, sample_id='new')],
        'sample.place': [dict(sample_id='pmma_on_si', offset_x_m=0.0, offset_y_m=0.0), dict(sample_id='cal_wafer', offset_x_m=0.001, offset_y_m=0.0)],
        'sample.remove': [dict(sample_id='pmma_on_si'), dict(sample_id='cal_wafer')],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_button': [{}],
        'vacuum.set': [dict(on=True), dict(on=False)], 'vacuum.press_switch': [{}],
        'aoi.set': [dict(position='65'), dict(position='70'), dict(position='90')],
        'stage.move_z': [dict(z_m=rt.STAGE_Z_REF_M), dict(z_m=MZ - 0.0003), dict(z_m=rt.STAGE_Z_RANGE_M[0])],
        'liquid_cell.install': [dict(sample_id='pmma_on_si')], 'liquid_cell.remove': [{}],
        'liquid_cell.fill': [dict(liquid_id='water', volume_m3=450e-9), dict(liquid_id='ethanol', volume_m3=100e-9)], 'liquid_cell.drain': [{}],
        'measure': [dict(mode='standard', alignment='standard', fixed_height_m=None, model_id='cauchy_on_si'),
                    dict(mode='fast', alignment='none', fixed_height_m=None, model_id=None),
                    dict(mode='mm', alignment='robust', fixed_height_m=None, model_id=None),
                    dict(mode='fast', alignment='fixed_height', fixed_height_m=MZ - rt.sample_height_m(PMMA), model_id=None)],
        'calibration.run': [{}],
        'analysis.fit': [dict(FIT), dict(FIT, model_id='si_native_oxide', fit=['oxide.thickness_m'], thickness_prefit=False)],
        'analysis.uniqueness': [dict(record_ids=['SE0001'], model_id='cauchy_on_si', parameters={}, fit=['film.A', 'film.B'], scan_parameter='film.thickness_m',
                                     values=[100e-9, 120e-9, 140e-9])],
        'data.pseudo': [dict(record_id='SE0001')],
        'records.delete': [dict(record_id='SE0001'), dict(record_id='CA0001'), dict(record_id='FT0001'), dict(record_id='ST0001')],
        'system.acknowledge': [{}],
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
        """Every accepted asynchronous call is followed until its operation has left 'running' (+ 5 s), every other call for 400 s (longer than
        the lamp warm-up), comparing with the identical run without the call every 0.05 s for the first 10 s and every 1 s after."""
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
                    t, t_end = 0.0, (3600.0 if oid else 400.0)
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
