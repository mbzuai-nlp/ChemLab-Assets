"""tests for CHEM-159 nanosurf_naiostm (Nanosurf NaioSTM scanning tunnelling microscope): python3 -m unittest tests/test_asset_nanosurf_naiostm.py

Topics (pipeline.md): settings only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances are
isolated, agent/evaluator truth isolation (truth slots, template and substance of the records, evaluator-only warnings and fault events, other
agents' records and operations), conservation (electrical energy = 30 W x powered time, motor travel, wire used, line and curve counts), real
return values validated against the declared schemas (asset_tool lint_schema / validate_value), asynchronous completion and cancellation
remnants, host-step independence, declared immediate and on_step effects (scenario states, short and long windows); plus the instrument (power
and LED, magnifying cover, tip removal / cutting / wire stock, sample mounting and cleaving, holder insertion / push / rotation / removal guards,
magnifier gap estimates, Advance / Retract / Home / Stop / Withdraw, advance into contact, dirty guides, the automatic approach: timing, found
extension, move speed too small, overshoot and crash with too large steps, insulators / oxides / 0 V running the tip into the surface) and the
physics: the exponential current-distance law and the apparent barrier from I-z, I-V shapes (metal, V-shaped graphite, semiconductor gaps with
the Fermi-level position, oxide), set-point quantisation and amplifier saturation, the HOPG lattice from the FFT against the hidden scanner
calibration, HOPG and Au(111) step heights with terrace levelling, the 1T-TaS2 CDW superlattice, constant height versus constant current,
thermal drift distortion settling, double-tip ghost images, cleaning pulse, tip pick-up, crashes, Z-range saturation, faults, ledgers, sessions.
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
from asset_runtime import nanosurf_naiostm as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}
_SC = {}


def _meta():
    if not _META:
        _META.update(rt.export_nanosurf_naiostm_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.NanosurfNaiostmRuntime.invoke


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


rt.NanosurfNaiostmRuntime.invoke = _instrumented_invoke

HOPG = dict(sample_id='hopg', template_id='hopg_kit', seed=3, tilt_rad=[0.004, -0.002])
GOLD = dict(sample_id='gold', template_id='gold_film_kit', seed=5, tilt_rad=[0.0, 0.0])
AU111 = dict(sample_id='au111', template_id='au111_mica', seed=4, tilt_rad=[0.002, 0.0])
MOS2 = dict(sample_id='mos2', template_id='mos2_crystal', seed=6, tilt_rad=[0.0, 0.0])
TAS2 = dict(sample_id='tas2', template_id='tas2_1t_crystal', seed=7, tilt_rad=[0.0, 0.0])
GLASS = dict(sample_id='glass', template_id='glass_slide', seed=8, tilt_rad=[0.0, 0.0])
ALU = dict(sample_id='alu', template_id='al_foil', seed=9, tilt_rad=[0.0, 0.0])
SAMPLES = (HOPG, GOLD, AU111, MOS2, TAS2, GLASS, ALU)
QUIET = dict(floor_vibration='quiet', temperature_stability_k=0.0)
ZC_HOPG = dict(setpoint_a=1e-9, tip_voltage_v=0.05, p_gain=1.0, i_gain=15.0)
SCAN = dict(image_size_m=50e-9, time_per_line_s=0.05, points=32, rotation_rad=0.0, offset_x_m=0.0, offset_y_m=0.0, slope_x_rad=0.0, slope_y_rad=0.0)
IV = dict(start_v=-0.5, range_v=1.0, mod_time_s=0.2, data_points=128, repetitions=1, positions_m=[[0.0, 0.0]], keep_z_controller=False)
IZ = dict(start_m=-1e-9, range_m=1.2e-9, mod_time_s=0.2, data_points=128, repetitions=1, positions_m=[[0.0, 0.0]])


def configured(seed=1, samples=SAMPLES, **plant):
    h = rt.NanosurfNaiostmRuntime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    for s in samples:
        h.invoke('sample.provide', **s)
    return h


def ready(seed=1, **kw):
    h = configured(seed, **kw)
    h.invoke('power.set', enabled=True)
    h.step(rt.BOOT_S + 1.0)
    return h


def settle(h, limit=600.0):
    t = 0.0
    while (h._holder_moving() or h.tip_work) and t < limit:
        h.step(0.5)
        t += 0.5
    return h


def holder_in(h, sample='hopg', distance=1.0e-3, angle=0.0):
    """Mount the sample, insert the holder with the sample surface `distance` from the tip, put the cover back."""
    h.invoke('cover.lift')
    stack = h.invoke('sample.mount', sample_id=sample)['stack_thickness_m']
    h.invoke('holder.insert', position_m=h.samples[sample]['stack_m'] + distance + (stack - stack), angle_rad=angle)
    h.invoke('cover.place')
    return h


def near(h, gap=0.12e-3):
    g = h.read('evaluator')['truth.gap_m']
    r = h.invoke('approach.advance', duration_s=(g - gap) / rt.MOTOR_SPEED_M_S)
    h.step(r['duration_s'] + 0.05)
    return h


def wait_op(h, oid, tmax=4000.0, dt=1.0):
    t = 0.0
    while h.operations[oid]['phase'] == 'running' and t < tmax:
        h.step(dt)
        t += dt
    return h.operations[oid]


def engaged(seed=1, sample='hopg', zc=ZC_HOPG, speed=50.0, settle_s=0.0, **plant):
    h = near(holder_in(ready(seed, **plant), sample))
    h.invoke('z_controller.set', **zc)
    op = wait_op(h, h.invoke('approach.start', move_speed_percent=speed, max_steps=20000)['operation_id'])
    assert op['phase'] == 'completed', op
    if settle_s:
        h.step(settle_s)
    return h


def scan(h, **kw):
    oid = h.invoke('scan.start', **dict(SCAN, **kw))['operation_id']
    wait_op(h, oid, dt=5.0)
    return h.records[h.operations[oid]['record_id']]


def spec(h, cap, **kw):
    base = IV if cap == 'spectroscopy.iv' else IZ
    oid = h.invoke(cap, **dict(base, **kw))['operation_id']
    wait_op(h, oid)
    return h.records[h.operations[oid]['record_id']]


def rejected(h, cap, code, **a):
    before = h.read('evaluator')
    rev = h.revision
    with unittest.TestCase().assertRaises(rt.ActionRejected) as cm:
        h.invoke(cap, **a)
    assert str(cm.exception) == code, (cap, str(cm.exception), code)
    after = h.read('evaluator')
    assert not {k for k in after if _changed(before[k], after[k])}, (cap, 'changed state on rejection')
    assert h.revision == rev
    return True


def lattice_ring_power(img, px, a):
    """Share of the image power in the ring of the (10) reflections of a hexagonal lattice of constant a (|k| = 2 / (sqrt3 a))."""
    z = rt.flatten_image(img, 'plane')
    F = np.abs(np.fft.fft2(z)) ** 2
    k = np.fft.fftfreq(z.shape[0], px)
    KX, KY = np.meshgrid(k, k)
    kr = np.hypot(KX, KY)
    k0 = 2 / (math.sqrt(3) * a)
    ring = np.abs(kr - k0) < 0.12 * k0
    return float(F[ring].sum() / F.sum())


def topo(rec, d='forward'):
    return np.asarray(rec['data']['topography'][d], float)[:rec['meta']['lines_completed']]


def current(rec, d='forward'):
    return np.asarray(rec['data']['current'][d], float)[:rec['meta']['lines_completed']]


# ---------------------------------------------------------------- scenario states (declared-effect checks)
def _build(name):
    if name == 'fresh':
        return configured()
    if name == 'booting':
        h = configured()
        h.invoke('power.set', enabled=True)
        h.step(3.0)
        return h
    if name == 'ready':
        return ready()
    if name == 'cover_off':
        h = ready()
        h.invoke('cover.lift')
        return h
    if name == 'tip_off':
        h = _build('cover_off')
        h.invoke('tip.remove')
        return settle(h)
    if name == 'tip_cutting':
        h = _build('tip_off')
        h.invoke('tip.prepare')
        h.step(5.0)
        return h
    if name == 'mounted':
        h = _build('cover_off')
        h.invoke('sample.mount', sample_id='hopg')
        return h
    if name == 'holder_in':
        h = ready()
        h.invoke('cover.lift')
        h.invoke('sample.mount', sample_id='hopg')
        h.invoke('holder.insert', position_m=h.samples['hopg']['stack_m'] + 1.0e-3, angle_rad=0.0)
        return h
    if name == 'near':
        return near(holder_in(ready(), 'hopg'))
    if name == 'moving':
        h = _build('near')
        h.invoke('approach.retract', duration_s=2.0)
        h.step(0.4)
        return h
    if name == 'approaching':
        h = _build('near')
        h.invoke('z_controller.set', **ZC_HOPG)
        h.invoke('approach.start', move_speed_percent=50.0, max_steps=20000)
        h.step(1.0)
        return h
    if name == 'engaged':
        return engaged()
    if name == 'scanning':
        h = engaged()
        h.invoke('scan.start', **dict(SCAN, points=64))
        h.step(2.0)
        return h
    if name == 'scanned':
        h = engaged()
        scan(h)
        return h
    if name == 'spec_running':
        h = _build('scanned')
        h.invoke('spectroscopy.iv', **dict(IV, repetitions=3))
        h.step(0.35)
        return h
    if name == 'spec_done':
        h = _build('scanned')
        spec(h, 'spectroscopy.iv')
        spec(h, 'spectroscopy.iz')
        return h
    if name == 'dirty':
        h = _build('near')
        h.invoke('fault.inject', fault='approach_motor_dirty', active=True)
        return h
    if name == 'burst':
        h = engaged()
        h.invoke('fault.inject', fault='vibration_burst', active=True)
        return h
    if name == 'unpowered_holder':
        h = _build('near')
        h.invoke('power.set', enabled=False)
        return h
    if name == 'crashed':
        h = near(holder_in(ready(), 'glass'))
        h.invoke('z_controller.set', **ZC_HOPG)
        wait_op(h, h.invoke('approach.start', move_speed_percent=50.0, max_steps=3000)['operation_id'])
        return h
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'booting', 'ready', 'cover_off', 'tip_off', 'tip_cutting', 'mounted', 'holder_in', 'near', 'moving', 'approaching', 'engaged', 'scanning',
             'scanned', 'spec_running', 'spec_done', 'dirty', 'burst', 'unpowered_holder', 'crashed']
LONG_SCENARIOS = ['ready', 'holder_in', 'near', 'engaged', 'scanned']
STEP_DTS = (0.4, 3.0, 25.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(rt.PLANT_EXAMPLE, noise_seed=2, humidity=0.7, floor_vibration='busy', temperature_stability_k=0.2)],
        'sample.provide': [dict(rt.SAMPLE_EXAMPLE, sample_id='new')],
        'cover.lift': [{}], 'cover.place': [{}], 'tip.remove': [{}], 'tip.prepare': [{}],
        'sample.mount': [dict(sample_id='hopg'), dict(sample_id='gold')], 'sample.unmount': [{}], 'sample.cleave': [dict(sample_id='hopg'), dict(sample_id='mos2')],
        'holder.insert': [dict(position_m=0.0035, angle_rad=0.0), dict(position_m=0.008, angle_rad=1.0)],
        'holder.push': [dict(position_m=0.0045), dict(position_m=0.0030)], 'holder.rotate': [dict(angle_rad=0.7)], 'holder.remove': [{}],
        'maintenance.clean_guides': [{}],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_button': [{}],
        'z_controller.set': [dict(ZC_HOPG), dict(setpoint_a=2e-9, tip_voltage_v=0.1, p_gain=0.0, i_gain=4.0)],
        'approach.advance': [dict(duration_s=0.2), dict(duration_s=2.0)], 'approach.retract': [dict(duration_s=0.5)], 'approach.home': [{}], 'approach.stop': [{}],
        'approach.withdraw': [dict(steps=10, move_speed_percent=50.0)], 'approach.start': [dict(move_speed_percent=50.0, max_steps=20000), dict(move_speed_percent=5.0, max_steps=50)],
        'scan.start': [dict(SCAN), dict(SCAN, image_size_m=4e-9, points=32, time_per_line_s=0.06)],
        'spectroscopy.iv': [dict(IV)], 'spectroscopy.iz': [dict(IZ)],
        'tip.cleaning_pulse': [{}],
        'analysis.flatten': [dict(record_id='IMG0001', channel='topography', direction='forward', method='plane')],
        'analysis.roughness': [dict(record_id='IMG0001', channel='topography', direction='forward', flatten='plane', region_px=None)],
        'analysis.line_profile': [dict(record_id='IMG0001', channel='current', direction='backward', flatten='none', start_px=[0, 10], end_px=[30, 10], points=32)],
        'analysis.step_height': [dict(record_id='IMG0001', channel='topography', direction='forward', flatten='terraces', method='regions', region_a_px=[0, 0, 8, 8],
                                      region_b_px=[20, 20, 30, 30])],
        'analysis.lattice': [dict(record_id='IMG0001', channel='topography', direction='forward', region_px=None)],
        'analysis.iv': [dict(record_id='SPC0001', curve_index=0, branch='forward', threshold_a=10e-12)],
        'analysis.iz': [dict(record_id='SPC0002', curve_index=0, branch='forward', i_min_a=20e-12, i_max_a=50e-9)],
        'records.delete': [dict(record_id='IMG0001'), dict(record_id='SPC0001')],
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
    return cap not in rt.UNSUPPORTED and cap not in rt.DECLARED and rt.CAPS[cap]['kind'] != 'query'


# ---------------------------------------------------------------- contract and schemas
class ContractAndSchemas(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.nanosurf_naiostm')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-159')
        self.assertEqual((rt.XY_RANGE_M, rt.Z_RANGE_M), (500e-9, 200e-9))
        self.assertAlmostEqual(rt.XY_RANGE_M / 2 ** 16, rt.XY_DAC_M, delta=0.05e-12)          # 16-bit digital step, not accuracy
        self.assertAlmostEqual(rt.Z_RANGE_M / 2 ** 16, rt.Z_DAC_M, delta=0.05e-12)
        self.assertEqual((rt.CURRENT_RANGE_A, rt.SETPOINT_STEP_A, rt.TIP_VOLTAGE_RANGE_V), ((0.1e-9, 100e-9), 25e-12, (-10.0, 10.0)))
        self.assertEqual((rt.MAX_POINTS, rt.LINE_RATE_MAX_HZ, rt.SAMPLE_MAX_DIAMETER_M, rt.SAMPLE_MAX_THICKNESS_M), (2048, 10.0, 0.010, 0.003))
        self.assertEqual((rt.BODY_DIMENSIONS_M, rt.MASS_KG, rt.POWER_W), ((0.204, 0.204, 0.104), 3.45, 30.0))
        self.assertGreaterEqual(len(rt.tunnel_lib()['subs']), 50)
        self.assertEqual(len(rt.sample_templates()['templates']), 19)

    def test_schemas_lint_and_examples_validate(self):
        m = _meta()
        for k, c in m['capabilities'].items():
            for part in ('input_schema', 'output_schema'):
                self.assertEqual(asset_tool.lint_schema(c[part]), [], (k, part))
            ex = rt.CAPS[k].get('example_arguments')
            if ex is not None:
                self.assertEqual(asset_tool.validate_value(ex, c['input_schema']), [], k)
                rt.validate(rt.CAPS[k]['input_schema'], ex)
        for k, s in m['state_slots'].items():
            self.assertEqual(asset_tool.lint_schema(s['value_schema']), [], k)
        for name in ('fresh', 'ready', 'tip_off', 'holder_in', 'engaged', 'scanning', 'scanned', 'spec_done', 'crashed', 'approaching'):
            v = _scenario(name).read('evaluator')
            for k, x in v.items():
                self.assertEqual(asset_tool.validate_value(x, m['state_slots'][k]['value_schema']), [], (name, k))
        h = _scenario('spec_done')
        for rid in h.records:
            out = h.invoke('measurement.result', record_id=rid, channel=None, direction=None, region_px=None, max_values=4096)
            self.assertEqual(asset_tool.validate_value(out, m['capabilities']['measurement.result']['output_schema']), [], rid)

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
        for k, v in list(rt.EFFECTS.items()) + list(rt.ON_STEP.items()):
            self.assertIn(k, rt.CAPS)
            self.assertEqual(set(v) - slots, set(), k)
        for mech in m['mechanisms'].values():
            self.assertEqual(set(mech['state_ids']) - slots, set())
        manifest = json.loads((rt.ASSET_DIR / 'geometry_manifest.json').read_text())
        comps = {c['id'] if 'id' in c else c['name'] for c in manifest['components']}
        used = {c for v in m['capabilities'].values() for c in v['component_ids']} | {s['component_ids'][0] for s in m['state_slots'].values()}
        used |= set(m['mechanisms']['naiostm_stm']['component_ids'])
        self.assertEqual(used - comps, set())

    def test_declared_and_unsupported_capabilities(self):
        h = ready()
        for k in rt.UNSUPPORTED:
            with self.assertRaises(rt.ActionRejected) as cm:
                _orig_invoke(h, k)
            self.assertEqual(str(cm.exception), 'UNSUPPORTED')
            self.assertEqual(_meta()['capabilities'][k]['availability'], 'unsupported')
        for k in rt.DECLARED:
            with self.assertRaises(rt.ActionRejected) as cm:
                _orig_invoke(h, k)
            self.assertEqual(str(cm.exception), 'NOT_IMPLEMENTED')
            c = _meta()['capabilities'][k]
            self.assertEqual((c['availability'], c['implementation']['process'], c['binding']), ('declared', 'declared', None))
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r = s.invoke(instance_id=h.instance_id, capability_id='afm.scan', arguments={'x': 1}, request_id='u1')
        self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))
        r = s.invoke(instance_id=h.instance_id, capability_id='lithography.patterning', arguments={}, request_id='u2')
        self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'NOT_IMPLEMENTED'))

    def test_libraries_carry_evidence(self):
        levels = {'documented', 'derived', 'assumption', 'unknown', 'not_applicable'}
        documented_wf = 0
        for s in rt.tunnel_lib()['subs'].values():
            for f in ('work_function', 'band_gap', 'resistivity', 'stm_corrugation_m'):
                self.assertIn(s[f]['evidence'], levels, (s['substance_id'], f))
                self.assertTrue(s[f]['source_ids'] or s[f]['evidence'] in ('unknown', 'not_applicable'), (s['substance_id'], f))
            documented_wf += s['work_function']['evidence'] == 'documented'
            self.assertTrue(s['source'], s['substance_id'])
        self.assertGreaterEqual(documented_wf, 35)
        for t in rt.sample_templates()['templates'].values():
            self.assertIn(t['material'], rt.tunnel_lib()['subs'])
            for k, v in t['topography'].items():
                if isinstance(v, dict):
                    self.assertIn(v['evidence'], ('documented', 'derived', 'assumption'), (t['template_id'], k))
            w, l = t['size_m']['value']
            span = math.hypot(w, l) if t['shape'] == 'square' else max(w, l)                  # circumscribed diameter
            self.assertLessEqual(span, rt.SAMPLE_MAX_DIAMETER_M + 1e-12, t['template_id'])       # manual: max. 10 mm diameter


# ---------------------------------------------------------------- host clock and purity
class HostClockAndPurity(unittest.TestCase):
    def test_read_and_describe_do_not_advance_time(self):
        h = _scenario('scanning')
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        t, rev = h.t, h.revision
        before = h.read('evaluator')
        for _ in range(3):
            s.read(instance_id=h.instance_id)
            s.describe(instance_id=h.instance_id)
        self.assertEqual((h.t, h.revision), (t, rev))
        self.assertFalse(_changed(before, h.read('evaluator')))

    def test_step_zero_and_negative(self):
        h = ready()
        rev = h.revision
        h.step(0.0)
        self.assertEqual(h.revision, rev)
        for bad in (-1.0, float('nan'), True, rt.MAX_STEP_S * 2):
            with self.assertRaises(rt.ActionRejected):
                h.step(bad)

    def test_host_step_chopping_does_not_change_the_result(self):
        A, B = _scenario('near'), _scenario('near')
        for h in (A, B):
            h.invoke('z_controller.set', **ZC_HOPG)
            h.invoke('approach.start', move_speed_percent=50.0, max_steps=20000)
        A.step(40.0)
        for _ in range(160):
            B.step(0.25)
        for h in (A, B):
            h.invoke('scan.start', **SCAN)
        A.step(60.0)
        for _ in range(24):
            B.step(2.5)
        va, vb = A.read('evaluator'), B.read('evaluator')
        for k in va:
            fa, fb = _flat(va[k]), _flat(vb[k])
            for leaf in set(fa) | set(fb):
                x, y = fa.get(leaf), fb.get(leaf)
                if _num(x) and _num(y):
                    self.assertAlmostEqual(x, y, delta=1e-9 * max(abs(x), abs(y), 1e-12) + 1e-18, msg=(k, leaf))
                else:
                    self.assertEqual(x, y, (k, leaf))
        ra, rb = A.records['IMG0001'], B.records['IMG0001']
        np.testing.assert_allclose(topo(ra), topo(rb), rtol=0, atol=1e-15)

    def test_reads_do_not_change_later_results(self):
        A, B = _scenario('engaged'), _scenario('engaged')
        for h in (A, B):
            h.invoke('scan.start', **SCAN)
        for _ in range(30):
            A.read('evaluator')
            A.step(1.0)
            B.step(1.0)
        np.testing.assert_array_equal(topo(A.records['IMG0001']), topo(B.records['IMG0001']))

    def test_settings_only_write_targets(self):
        h = _scenario('near')
        q0 = h.holder['q']
        r = h.invoke('approach.retract', duration_s=2.0)
        self.assertEqual(h.holder['q'], q0)                         # the motor moves only with the host clock
        self.assertEqual(r, dict(travel_m=2.0 * rt.MOTOR_SPEED_M_S, duration_s=2.0))          # the command, not a position
        self.assertAlmostEqual(h.read('evaluator')['holder.target_m'], q0 + 2.0 * rt.MOTOR_SPEED_M_S, places=12)
        h.step(1.0)
        self.assertAlmostEqual(h.holder['q'], q0 + rt.MOTOR_SPEED_M_S, places=12)
        h = _scenario('ready')
        h.invoke('z_controller.set', setpoint_a=1.010e-9, tip_voltage_v=0.3, p_gain=2.0, i_gain=10.0)
        self.assertAlmostEqual(h.zc['setpoint_a'], 1.0e-9, places=18)        # 25 pA steps
        h.invoke('z_controller.set', setpoint_a=1.015e-9, tip_voltage_v=0.3, p_gain=2.0, i_gain=10.0)
        self.assertAlmostEqual(h.zc['setpoint_a'], 1.025e-9, places=18)


# ---------------------------------------------------------------- instrument handling
class InstrumentHandling(unittest.TestCase):
    def test_power_led_boot_and_power_loss(self):
        h = configured()
        v = h.read()
        self.assertEqual((v['power.enabled'], v['power.led_on'], v['system.state'], v['probe_status.light']), (False, False, 'off', 'off'))
        h.invoke('power.press_button')
        self.assertEqual((h.read()['power.led_on'], h.read()['system.state']), (True, 'booting'))
        rejected(h, 'z_controller.set', 'NOT_READY', **ZC_HOPG)
        h.step(rt.BOOT_S - 0.5)
        self.assertEqual(h.read()['system.state'], 'booting')
        h.step(1.0)
        self.assertEqual(h.read()['system.state'], 'ready')
        h = _scenario('scanning')
        h.invoke('power.set', enabled=False)
        rec = h.records['IMG0002'] if 'IMG0002' in h.records else h.records['IMG0001']
        self.assertEqual(rec['status'], 'failed')
        self.assertGreater(rec['meta']['lines_completed'], 0)
        v = h.read()
        self.assertEqual((v['approach.state'], v['zscanner.extension_m'], v['current.reading_a']), ('retracted', None, None))   # no readings without power

    def test_cover_tip_exchange_and_wire_stock(self):
        h = ready()
        rejected(h, 'tip.remove', 'COVER_ON')
        h.invoke('cover.lift')
        self.assertEqual(h.read()['cover.lift_m'], rt.COVER_LIFT_M)
        rejected(h, 'tip.prepare', 'TIP_INSTALLED')
        self.assertEqual(h.read('evaluator')['truth.tip']['kind'], 'sharp')          # the factory tip gives atomic resolution
        h.invoke('tip.remove')
        self.assertTrue(h.read()['tip.handling'])
        rejected(h, 'tip.prepare', 'MOTION_IN_PROGRESS')
        settle(h)
        self.assertFalse(h.read()['tip.installed'])
        kinds = []
        for i in range(40):
            h.invoke('tip.prepare')
            settle(h)
            self.assertTrue(h.read()['tip.installed'])
            kinds.append(h.read('evaluator')['truth.tip']['kind'])
            h.invoke('tip.remove')
            settle(h)
        self.assertAlmostEqual(h.read()['tip.wire_remaining_m'], rt.WIRE_LENGTH_M - 40 * rt.TIP_CUT_LENGTH_M, places=12)
        self.assertEqual(h.ledger()['tips_prepared'], 40.0)
        self.assertGreaterEqual(len(set(kinds)), 3)                               # sharp, double, blunt, unstable after cutting
        h.wire_m = 0.003
        rejected(h, 'tip.prepare', 'WIRE_EXHAUSTED')

    def test_sample_mounting_and_cleaving(self):
        h = ready()
        h.invoke('cover.lift')
        rejected(h, 'sample.cleave', 'NOT_CLEAVABLE', sample_id='gold')          # manual: the gold sample cannot be cleaned
        th = h.samples['hopg']['thickness_m']
        topo_before = h.samples['hopg']['surface'].evaluate(np.linspace(0, 2e-6, 50), np.zeros(50))[0]
        h.invoke('sample.cleave', sample_id='hopg')
        self.assertAlmostEqual(h.samples['hopg']['thickness_m'], th - rt.CLEAVE_LOSS_M, places=12)
        topo_after = h.samples['hopg']['surface'].evaluate(np.linspace(0, 2e-6, 50), np.zeros(50))[0]
        self.assertTrue(np.any(np.abs(topo_after - topo_before) > 1e-12))          # a fresh surface: new terraces
        r = h.invoke('sample.mount', sample_id='hopg')
        self.assertAlmostEqual(r['stack_thickness_m'], round(h.samples['hopg']['stack_m'] / 0.05e-3) * 0.05e-3, places=12)
        rejected(h, 'sample.mount', 'HOLDER_OCCUPIED', sample_id='gold')
        rejected(h, 'sample.cleave', 'SAMPLE_NOT_ON_BENCH', sample_id='hopg')
        h.invoke('sample.unmount')
        rejected(h, 'sample.unmount', 'HOLDER_EMPTY')

    def test_holder_guards_and_magnifier(self):
        h = ready()
        h.invoke('cover.lift')
        h.invoke('sample.mount', sample_id='hopg')
        stack = h.samples['hopg']['stack_m']
        rejected(h, 'holder.insert', 'COLLISION_RISK', position_m=stack + 0.05e-3, angle_rad=0.0)   # judged by eye as 0.1 mm or less
        h.invoke('holder.insert', position_m=stack + 1.0e-3, angle_rad=0.0)
        rejected(h, 'holder.push', 'COLLISION_RISK', position_m=stack + 0.05e-3)             # bias <= 1.4 and jitter <= 60 um: at most 0.13 mm seen
        for dq in (0.12e-3, 0.16e-3, 0.2e-3, 0.24e-3, 0.3e-3):                              # the guard is the eye estimate of the new gap
            c = copy.deepcopy(h)
            want = c._eye_gap(c._hand_gap(stack + dq)) < rt.PUSH_MIN_GAP_M
            if want:
                rejected(c, 'holder.push', 'COLLISION_RISK', position_m=stack + dq)
            else:
                c.invoke('holder.push', position_m=stack + dq)
        rejected(h, 'tip.remove', 'HOLDER_IN_HEAD')
        rejected(h, 'sample.unmount', 'HOLDER_IN_HEAD')
        m = h.invoke('magnifier.view')
        self.assertEqual(m['magnification'], 1.0)                                  # the magnifier is in the cover
        h.invoke('cover.place')
        rejected(h, 'holder.push', 'COVER_ON', position_m=stack + 0.8e-3)
        m = h.invoke('magnifier.view')
        g = h.read('evaluator')['truth.gap_m']
        self.assertEqual(m['magnification'], 10.0)
        self.assertTrue(m['reflection_visible'])
        self.assertEqual(m['tip_over'], 'sample')
        self.assertAlmostEqual(m['gap_estimate_m'], round(h._seen_gap(g, True) / rt.MAGNIFIER_QUANT_M) * rt.MAGNIFIER_QUANT_M, delta=1e-15)
        self.assertLess(abs(m['gap_estimate_m'] - g * h.holder['view_bias']), 0.5 * rt.MAGNIFIER_QUANT_M + 3 * rt.MAGNIFIER_SIGMA_M + 1e-12)
        near(h, gap=10e-6)
        self.assertTrue(h.invoke('magnifier.view')['appears_touching'])

    def test_motor_moves_stop_home_and_withdraw(self):
        h = _scenario('near')
        q = h.holder['q']
        h.invoke('approach.home')
        h.step(2.0)
        self.assertEqual(h.invoke('approach.stop'), dict(stopped=True))
        self.assertAlmostEqual(h.holder['q'], q + 2.0 * rt.MOTOR_SPEED_M_S, places=12)
        h.step(5.0)
        self.assertAlmostEqual(h.holder['q'], q + 2.0 * rt.MOTOR_SPEED_M_S, places=12)
        h.invoke('approach.home')
        settle(h)
        self.assertAlmostEqual(h.holder['q'], rt.HOLDER_TRAVEL_M[1], places=12)
        self.assertAlmostEqual(h.ledger()['motor_travel_m'], (h.samples['hopg']['stack_m'] + 1e-3 - q) + (rt.HOLDER_TRAVEL_M[1] - q), delta=1e-9)
        h = engaged()
        q = h.holder['q']
        r = h.invoke('approach.withdraw', steps=10, move_speed_percent=50.0)
        self.assertEqual(h.read()['approach.state'], 'retracted')
        self.assertEqual(r, dict(travel_m=10 * 150e-9, duration_s=10 * rt.CYCLE_S))
        rejected(h, 'approach.advance', 'MOTION_IN_PROGRESS', duration_s=1.0)
        settle(h)
        self.assertAlmostEqual(h.holder['q'] - q, 10 * 150e-9, places=15)
        self.assertEqual(h.invoke('approach.stop'), dict(stopped=False))

    def test_advance_into_contact_crashes_and_bends_the_tip(self):
        h = _scenario('near')
        g = h.read('evaluator')['truth.gap_m']
        h.invoke('approach.advance', duration_s=(g + 50e-6) / rt.MOTOR_SPEED_M_S)
        settle(h)
        v = h.read('evaluator')
        self.assertEqual(v['truth.tip']['kind'], 'crashed')
        self.assertAlmostEqual(v['truth.tip']['recess_m'], rt.BEND_M, delta=1e-9)     # the motor stalls against the bent wire
        self.assertEqual(h.ledger()['tip_crashes'], 1.0)
        self.assertIn('TIP_CRASH', [e['code'] for e in h.events])

    def test_dirty_guides_slow_the_motor_until_cleaned(self):
        h = _scenario('dirty')
        q = h.holder['q']
        h.invoke('approach.retract', duration_s=1.0)
        settle(h)
        self.assertAlmostEqual(h.holder['q'] - q, rt.MOTOR_SPEED_M_S * rt.DIRTY_MOTOR_FACTOR, places=12)
        h.invoke('cover.lift')
        h.invoke('approach.retract', duration_s=10.0)
        settle(h)
        rejected(h, 'maintenance.clean_guides', 'HOLDER_IN_HEAD')
        h.invoke('holder.remove')
        h.invoke('maintenance.clean_guides')
        self.assertNotIn('approach_motor_dirty', h.read('evaluator')['truth.faults'])


# ---------------------------------------------------------------- approach
class Approach(unittest.TestCase):
    def test_timing_and_found_extension(self):
        h = _scenario('near')
        h.invoke('z_controller.set', **ZC_HOPG)
        g0 = h._gap_mech()
        d = h._dset()[0]
        oid = h.invoke('approach.start', move_speed_percent=50.0, max_steps=20000)['operation_id']
        self.assertEqual(h.read()['probe_status.light'], 'blinking')
        wait_op(h, oid, dt=0.5)
        zr = rt.Z_RANGE_M * (1 + h.cal['z'])                                                # real Z range (hidden calibration)
        k = math.ceil((g0 - zr - d) / 150e-9)
        t_start = [e for e in h.events if e['code'] == 'APPROACH_STARTED'][-1]['time_s']
        t_done = [e for e in h.events if e['code'] == 'APPROACH_DONE'][-1]['time_s']
        self.assertAlmostEqual(t_done - t_start, (k + 1) * rt.CYCLE_S + rt.APPROACH_SETTLE_S, delta=1e-6)
        self.assertAlmostEqual(h.ledger()['approach_steps'], float(k))
        v = h.read('evaluator')
        self.assertEqual((v['approach.state'], v['probe_status.light']), ('engaged', 'green'))
        self.assertGreater(v['zscanner.extension_m'], rt.Z_RANGE_M - 150e-9 / (1 + h.cal['z']) - 1e-10)   # found during the last extension (nominal)
        self.assertAlmostEqual(v['truth.gap_m'], d, delta=1e-12)
        self.assertAlmostEqual(abs(v['current.reading_a']), 1e-9, delta=0.1e-9)
        self.assertLess(k * rt.CYCLE_S, 20.0)                                              # manual: 10-20 s from a fraction of a millimetre

    def test_small_move_speed_and_max_steps_fail(self):
        h = _scenario('near')
        h.invoke('z_controller.set', **ZC_HOPG)
        oid = h.invoke('approach.start', move_speed_percent=5.0, max_steps=100)['operation_id']
        q = h.holder['q']
        self.assertEqual(wait_op(h, oid)['reason'], 'APPROACH_FAILED')
        self.assertEqual(h.holder['q'], q)                                                 # below 10 % the stick-slip motor does not move
        oid = h.invoke('approach.start', move_speed_percent=50.0, max_steps=100)['operation_id']
        self.assertEqual(wait_op(h, oid)['reason'], 'APPROACH_FAILED')
        self.assertAlmostEqual(q - h.holder['q'], 100 * 150e-9, delta=1e-15)
        self.assertEqual(h.read('evaluator')['truth.tip']['kind'], 'sharp')

    def test_too_large_steps_overshoot_or_crash(self):
        outcomes = {}
        for i in range(12):
            h = _scenario('near')
            h.invoke('z_controller.set', **ZC_HOPG)
            h.holder['q'] += i * 37e-9                                                     # different remaining gaps
            oid = h.invoke('approach.start', move_speed_percent=95.0, max_steps=20000)['operation_id']
            x = h.op['extra']['outcome']
            wait_op(h, oid)
            outcomes.setdefault(x, 0)
            outcomes[x] += 1
            if x == 'crash_step':
                self.assertEqual(h.read('evaluator')['truth.tip']['kind'], 'crashed')
                self.assertEqual(h.read()['probe_status.light'], 'red')
            if x == 'overshoot':
                self.assertEqual(h.read()['probe_status.light'], 'red')
        self.assertIn('found', outcomes)
        self.assertTrue(outcomes.get('overshoot', 0) + outcomes.get('crash_step', 0) > 0, outcomes)

    def test_no_current_runs_the_tip_into_the_surface(self):
        for sample, zc in (('glass', ZC_HOPG), ('alu', ZC_HOPG), ('hopg', dict(ZC_HOPG, tip_voltage_v=0.0))):
            h = near(holder_in(ready(), sample))
            h.invoke('z_controller.set', **zc)
            op = wait_op(h, h.invoke('approach.start', move_speed_percent=50.0, max_steps=3000)['operation_id'])
            self.assertEqual(op['reason'], 'APPROACH_FAILED', sample)
            v = h.read('evaluator')
            self.assertEqual(v['truth.tip']['kind'], 'crashed', sample)
            self.assertEqual(v['truth.gap_m'], rt.D_CONTACT_M, sample)
            self.assertGreater(v['truth.tip']['recess_m'], 0.0)
            self.assertEqual(h.ledger()['tip_crashes'], 1.0)

    def test_cancel_stops_the_holder(self):
        h = _scenario('approaching')
        h.register_principal('ev', 'evaluator')
        s = h.session('ev')
        oid = h.op['op']
        q = h.holder['q']
        r = s.cancel(oid, 'c1')
        self.assertEqual((r['status'], r['result']['phase']), ('succeeded', 'cancelled'))
        h.step(5.0)
        self.assertEqual(h.holder['q'], q)
        self.assertEqual(h.read()['approach.state'], 'retracted')
        r = s.cancel(oid, 'c2')
        self.assertEqual((r['status'], r['error']['code']), ('rejected', 'NOT_RUNNING'))


# ---------------------------------------------------------------- tunnelling physics
class TunnellingPhysics(unittest.TestCase):
    def test_exponential_law_and_apparent_barrier(self):
        m = rt.material('au')
        phi = rt.barrier_ev(m, 0.4)
        d = np.linspace(0.6e-9, 1.2e-9, 7)
        i = np.abs(rt.tunnel_current(0.1, d, m, phi, 0.0254))
        slope = -np.polyfit(d, np.log(i), 1)[0]
        self.assertAlmostEqual(slope / (2 * rt.KAPPA_1EV * math.sqrt(phi)), 1.0, delta=0.03)   # ln I falls by 2 kappa per metre
        h = engaged(**QUIET)
        rec = spec(h, 'spectroscopy.iz')
        a = h.invoke('analysis.iz', record_id=rec['record_id'], curve_index=0, branch='forward', i_min_a=20e-12, i_max_a=50e-9)
        self.assertAlmostEqual(a['apparent_barrier_ev'], rt.barrier_ev(rt.material('hopg'), 0.4) * (1 + h.cal['z']) ** 2, delta=0.1)   # nominal z axis
        self.assertGreater(a['r2'], 0.99)
        self.assertLess(a['apparent_barrier_ev'] / (1 + h.cal['z']) ** 2, rt.vacuum_barrier_ev(rt.material('hopg')) / 2)   # air lowers the apparent barrier

    def test_iv_shapes_metal_semimetal_semiconductor_oxide(self):
        kT = 0.0254
        V = np.linspace(-0.5, 0.5, 201)
        def curve(sid, v_set=0.1, Vs=V):
            m = rt.material(sid)
            d = rt.setpoint_gap(m, v_set, 1e-9, 0.4, 295.15)
            return np.array([float(rt.tunnel_current(v, np.array([d[0]]), m, rt.barrier_ev(m, 0.4), kT)[0]) for v in Vs]), d
        au, _ = curve('au')
        a = rt.iv_analysis(V, au, 1e-12)
        self.assertAlmostEqual(a['normalized_conductance'][100], 1.0, delta=0.05)
        self.assertLess(a['normalized_conductance'][-10], 1.25)                              # nearly ohmic at low bias
        hopg, _ = curve('hopg', 0.05)
        a = rt.iv_analysis(V, hopg, 1e-12)
        self.assertGreater(a['normalized_conductance'][-10], 1.35)                           # V-shaped density of states of graphite
        Vw = np.linspace(-2.5, 2.5, 501)
        mos2, _ = curve('mos2', -1.5, Vw)
        g = rt.iv_analysis(Vw, mos2, 10e-12)['gap']
        self.assertIsNotNone(g)
        self.assertGreater(g['width_v'], 1.1)
        self.assertLess(g['width_v'], 2.0)
        self.assertLess(g['v_low'], 0.0)                                                     # n-type: the empty conduction band is closer to EF
        self.assertGreater(g['v_high'], abs(g['v_low']))
        self.assertIsNone(rt.setpoint_gap(rt.material('al'), 0.1, 1e-9, 0.4, 295.15))       # passive oxide: no tunnelling
        self.assertIsNone(rt.setpoint_gap(rt.material('glass'), 1.0, 1e-9, 0.4, 295.15))
        self.assertIsNotNone(rt.setpoint_gap(rt.material('cu'), 0.1, 1e-9, 0.4, 295.15))     # tarnish: tunnelling at a small gap
        self.assertLess(rt.setpoint_gap(rt.material('cu'), 0.1, 1e-9, 0.4, 295.15)[0], rt.setpoint_gap(rt.material('au'), 0.1, 1e-9, 0.4, 295.15)[0])

    def test_amplifier_saturation_and_iv_with_the_feedback_on_through_zero(self):
        h = engaged(**QUIET)
        rec = spec(h, 'spectroscopy.iz', start_m=0.0, range_m=0.9e-9)
        self.assertIn('current_saturated', rec['warnings'])
        c = rec['data']['curves'][0]
        self.assertLessEqual(max(abs(x) for x in c['current_forward_a']), rt.I_SAT_A)
        h = engaged(**QUIET)
        rec = spec(h, 'spectroscopy.iv', start_v=-0.3, range_v=0.6, keep_z_controller=True)
        self.assertEqual(h.read('evaluator')['truth.tip']['kind'], 'crashed')                # at 0 V the feedback drives the tip into the surface
        self.assertIn('z_forward_m', rec['data']['curves'][0])
        self.assertLessEqual(h.ledger()['tip_crashes'], 1.0)


# ---------------------------------------------------------------- imaging physics
class ImagingPhysics(unittest.TestCase):
    def test_hopg_lattice_from_the_fft_against_the_hidden_calibration(self):
        h = engaged(settle_s=3 * 3600.0, **QUIET)
        rec = scan(h, image_size_m=4e-9, time_per_line_s=0.06, points=128)
        self.assertEqual(rec['warnings'], [])
        L = h.invoke('analysis.lattice', record_id=rec['record_id'], channel='topography', direction='forward', region_px=None)
        a_true = rt.material('hopg')['a_s']
        cal = 1 + 0.5 * (h.cal['x'] + h.cal['y'])
        self.assertIsNotNone(L['hexagonal_lattice_m'])
        self.assertAlmostEqual(L['hexagonal_lattice_m'] * cal / a_true, 1.0, delta=0.035)     # the image scale is the commanded one
        self.assertAlmostEqual(L['hexagonal_lattice_m'], 0.25e-9, delta=0.015e-9)           # manual: 0.25 nm between the bright hills
        c = current(rec)
        self.assertGreater(np.std(c) / np.mean(np.abs(c)), 0.05)                           # atomic contrast in the current

    def test_step_heights_with_terrace_levelling(self):
        for sample, sid in (('hopg', 'hopg'), ('au111', 'au')):
            h = engaged(sample=sample, zc=dict(ZC_HOPG, tip_voltage_v=0.5), settle_s=3600.0, **QUIET)
            rec = scan(h, image_size_m=300e-9, time_per_line_s=0.1, points=256)
            st = h.invoke('analysis.step_height', record_id=rec['record_id'], channel='topography', direction='forward', flatten='terraces', method='histogram',
                          region_a_px=None, region_b_px=None)
            step = rt.material(sid)['step'] / (1 + h.cal['z'])                              # nominal Z output = real / (1 + cal_z)
            n = round(st['step_m'] / step)
            self.assertGreaterEqual(n, 1, sample)
            self.assertAlmostEqual(st['step_m'] / (n * step), 1.0, delta=0.06, msg=sample)

    def test_tas2_charge_density_wave(self):
        h = engaged(sample='tas2', zc=dict(ZC_HOPG, tip_voltage_v=0.1), settle_s=3600.0, **QUIET)
        rec = scan(h, image_size_m=10e-9, time_per_line_s=0.06, points=192)
        L = h.invoke('analysis.lattice', record_id=rec['record_id'], channel='topography', direction='forward', region_px=None)
        cdw = rt.material('tas2_1t')['cdw']['period']
        cal = 1 + 0.5 * (h.cal['x'] + h.cal['y'])
        self.assertAlmostEqual(L['hexagonal_lattice_m'] * cal / cdw, 1.0, delta=0.05)       # sqrt13 a superlattice dominates

    def test_constant_height_versus_constant_current(self):
        out = {}
        for mode, gains in (('cc', dict(p_gain=1.0, i_gain=40.0)), ('ch', dict(rt.CONSTANT_HEIGHT_GAINS))):
            h = engaged(settle_s=3600.0, **QUIET)
            h.invoke('z_controller.set', **dict(ZC_HOPG, **gains))
            rec = scan(h, image_size_m=4e-9, time_per_line_s=0.06, points=128)
            out[mode] = (np.std(rt.flatten_image(topo(rec), 'line_1')), np.std(current(rec)) / np.mean(np.abs(current(rec))))
        self.assertGreater(out['cc'][0], 2 * out['ch'][0])                                  # topography follows the atoms at high gain
        self.assertGreater(out['ch'][1], 2 * out['cc'][1])                                  # current image at low gain (manual: I 4, P 0)

    def test_thermal_drift_distorts_early_images_and_settles(self):
        shift, lat = {}, {}
        for wait in (0.0, 3 * 3600.0):
            h = engaged(settle_s=wait, floor_vibration='quiet', temperature_stability_k=0.0)
            t0 = h.t
            rec = scan(h, image_size_m=4e-9, time_per_line_s=0.06, points=128)
            shift[wait] = float(np.linalg.norm(np.array(h._drift(h.t)[:2]) - np.array(h._drift(t0)[:2])))     # lateral drift during the image
            lat[wait] = h.invoke('analysis.lattice', record_id=rec['record_id'], channel='topography', direction='forward', region_px=None)['hexagonal_lattice_m']
        self.assertGreater(shift[0.0], 0.3e-9)                                                  # comparable to the lattice: a distorted image
        self.assertLess(shift[3 * 3600.0], 0.02e-9)                                             # manual: stabilises within about an hour
        a = rt.material('hopg')['a_s']
        self.assertIsNotNone(lat[3 * 3600.0])
        self.assertTrue(lat[0.0] is None or abs(lat[0.0] / a - 1) > abs(lat[3 * 3600.0] / a - 1))

    def test_double_tip_ghost_and_cleaning_pulse(self):
        base = engaged(settle_s=3600.0, **QUIET)
        imgs = {}
        for double in (False, True):
            h = copy.deepcopy(base)
            if double:
                h.tip['second'] = dict(dx=0.61e-9, dy=0.37e-9, recess=0.0)                         # not a lattice vector: ghost atoms between the atoms
            rec = scan(h, image_size_m=4e-9, time_per_line_s=0.06, points=128)
            imgs[double] = rt.flatten_image(topo(rec), 'plane')
        self.assertIn('double_tip', rec['warnings'])
        self.assertGreater(np.std(imgs[True] - imgs[False]), 0.3 * np.std(imgs[False]))
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r = s.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=dict(record_id=rec['record_id'], channel=None, direction=None, region_px=None,
                     max_values=64), request_id='m1')
        self.assertNotIn('double_tip', r['result']['record']['warnings'])                  # evaluator knowledge
        kinds = set()
        for _ in range(12):
            h.invoke('tip.cleaning_pulse')
            kinds.add(h.tip['kind'])
        self.assertGreaterEqual(len(kinds), 2)
        self.assertEqual(h.ledger()['cleaning_pulses'], 12.0)

    def test_crash_at_a_tall_grain_with_a_fast_low_gain_scan_and_z_saturation(self):
        h = engaged(sample='gold', zc=dict(ZC_HOPG, tip_voltage_v=0.5, i_gain=1.0, p_gain=0.0), settle_s=600.0, **QUIET)
        rec = scan(h, image_size_m=400e-9, time_per_line_s=0.05, points=128)
        self.assertEqual(h.read('evaluator')['truth.tip']['kind'], 'crashed')
        self.assertIn('tip_crash', rec['warnings'])
        h = engaged(settle_s=600.0, **QUIET)
        h.samples['hopg']['surface'].tilt = [0.9, 0.0]                                      # a strongly tilted mounting
        rec = scan(h, image_size_m=400e-9, time_per_line_s=0.1, points=64)
        self.assertIn('z_range_exceeded', rec['warnings'])

    def test_topography_uses_the_16_bit_z_steps_and_line_rate_limit(self):
        h = engaged(**QUIET)
        rec = scan(h)
        t = topo(rec)
        np.testing.assert_allclose(t / rt.Z_DAC_M, np.round(t / rt.Z_DAC_M), atol=1e-3)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('scan.start', **dict(SCAN, time_per_line_s=0.04))                      # 10 Hz line rate: at least 0.05 s per line
        rejected(h, 'scan.start', 'SCAN_OUT_OF_RANGE', **dict(SCAN, image_size_m=400e-9, offset_x_m=100e-9))

    def test_tip_pickup_reduces_the_atomic_contrast(self):
        rq = {}
        for pick in (False, True):
            h = engaged(settle_s=3600.0, **QUIET)
            if pick:
                h.invoke('fault.inject', fault='tip_pickup', active=True)
            rec = scan(h, image_size_m=4e-9, time_per_line_s=0.06, points=64)
            rq[pick] = lattice_ring_power(topo(rec), 4e-9 / 64, rt.material('hopg')['a_s'])
        self.assertGreater(rq[False], 3 * rq[True])


# ---------------------------------------------------------------- faults, records, analysis, ledgers, sessions
class FaultsRecordsLedgersSessions(unittest.TestCase):
    def test_vibration_burst_and_thermal_disturbance(self):
        noise = {}
        for burst in (False, True):
            h = engaged(settle_s=3600.0, **QUIET)
            if burst:
                h.invoke('fault.inject', fault='vibration_burst', active=True)
            rec = scan(h, image_size_m=30e-9, time_per_line_s=0.05, points=64)          # pixels >> lattice: the atomic corrugation averages out
            noise[burst] = np.std(rt.flatten_image(topo(rec), 'terraces'))              # the gap vibration is followed by the feedback (correlated noise)
        self.assertGreater(noise[True], 3 * noise[False])
        h = engaged(settle_s=3600.0, **QUIET)
        d0 = h._drift()
        h.invoke('fault.inject', fault='thermal_disturbance', active=True)
        h.step(600.0)
        self.assertGreater(np.linalg.norm(np.array(h._drift()) - np.array(d0)), 30e-9)

    def test_cancel_keeps_lines_and_the_engagement(self):
        h = _scenario('scanning')
        h.register_principal('ev', 'evaluator')
        s = h.session('ev')
        oid = h.op['op']
        rid = h.op['record_id']
        h.step(0.3)
        r = s.cancel(oid, 'x1')
        self.assertEqual(r['status'], 'succeeded')
        rec = h.records[rid]
        self.assertEqual(rec['status'], 'cancelled')
        self.assertGreater(rec['meta']['lines_completed'], 16)
        self.assertEqual(h.read()['approach.state'], 'engaged')
        r2 = s.cancel(oid, 'x1')
        self.assertEqual(r2, r)
        r3 = s.cancel(oid, 'x2')
        self.assertEqual((r3['status'], r3['error']['code']), ('rejected', 'NOT_RUNNING'))

    def test_records_analysis_and_result_extraction(self):
        h = _scenario('spec_done')
        out = h.invoke('measurement.result', record_id='IMG0001', channel='current', direction='backward', region_px=[0, 0, 16, 16], max_values=64)
        self.assertEqual((out['data']['shape'], out['data']['unit'], out['data']['factor']), ([8, 8], 'A', 2))
        f = h.invoke('analysis.flatten', record_id='IMG0001', channel='topography', direction='forward', method='terraces')
        self.assertEqual(h.read()['result.last_record_id'], f['record_id'])
        p = h.invoke('analysis.line_profile', record_id='IMG0001', channel='topography', direction='forward', flatten='plane', start_px=[0, 5], end_px=[31, 5], points=32)
        self.assertAlmostEqual(p['distance_m'][-1], 31 * SCAN['image_size_m'] / 32, places=15)
        spc = h.invoke('measurement.result', record_id='SPC0001', channel=None, direction=None, region_px=None, max_values=64)
        self.assertEqual(spc['data']['units']['x'], 'V')
        rejected(h, 'analysis.iz', 'RECORD_NOT_SUITABLE', record_id='SPC0001', curve_index=0, branch='forward', i_min_a=20e-12, i_max_a=50e-9)
        rejected(h, 'analysis.iv', 'UNKNOWN_CURVE', record_id='SPC0001', curve_index=5, branch='forward', threshold_a=1e-11)
        h.invoke('records.delete', record_id='IMG0001')
        rejected(h, 'measurement.result', 'UNKNOWN_RECORD', record_id='IMG0001', channel=None, direction=None, region_px=None, max_values=64)

    def test_remaining_capabilities_through_the_api(self):
        h = _scenario('spec_done')
        st = h.invoke('analysis.roughness', record_id='IMG0001', channel='topography', direction='forward', flatten='plane', region_px=[0, 0, 16, 16])
        self.assertEqual(st['pixels'], 256)
        a = h.invoke('analysis.iv', record_id='SPC0001', curve_index=0, branch='backward', threshold_a=5e-12)
        self.assertEqual(len(a['voltage_v']), IV['data_points'])
        self.assertEqual(h.invoke('ledger.read'), h.ledger())
        pv = h.invoke('provenance.read')
        self.assertEqual(pv['libraries'], rt.library_sha256())
        self.assertEqual((pv['substances'], pv['sample_templates']), (len(rt.tunnel_lib()['subs']), len(rt.sample_templates()['templates'])))
        t = h.t
        self.assertAlmostEqual(h.invoke('sim.step', dt_s=2.5)['time_s'], t + 2.5, places=9)
        h = _scenario('holder_in')
        stack = h.samples['hopg']['stack_m']
        self.assertEqual(h.invoke('holder.push', position_m=stack + 0.6e-3)['position_m'], stack + 0.6e-3)
        p = h._tip_point()
        hp = float(h.samples['hopg']['surface'].evaluate(np.array([p[0]]), np.array([p[1]]))[0][0])     # mounting tilt and terraces at the tip spot
        self.assertAlmostEqual(h.read('evaluator')['truth.gap_m'], 0.6e-3 - hp + h.tip['recess'] - h._drift()[2], delta=1e-12)
        rejected(h, 'holder.push', 'COLLISION_RISK', position_m=stack + 0.05e-3)             # seen by eye as 0.1 mm or less
        self.assertEqual(h.invoke('holder.rotate', angle_rad=1.2)['angle_rad'], 1.2)
        p1 = h._tip_point()
        self.assertAlmostEqual(math.hypot(*p1), math.hypot(*h.tip['offset']), delta=1e-6)       # the spot moves on a circle about the axis
        g = h.read('evaluator')['truth.gap_m']
        g_t = (0.21e-3 - h._look_noise(False)) / h.holder['view_bias']                    # seen by eye as 0.21 mm: pushed, too close to turn
        h.invoke('holder.push', position_m=h.holder['q'] + g_t - g)
        rejected(h, 'holder.rotate', 'COLLISION_RISK', angle_rad=0.3)                        # turning needs 0.3 mm

    def test_energy_and_count_ledgers_close(self):
        h = _scenario('spec_done')
        t_on = h.t - [e for e in h.events if e['code'] == 'POWER_ON'][0]['time_s']
        L = h.ledger()
        self.assertAlmostEqual(L['electrical_energy_j'], rt.POWER_W * t_on, delta=1e-6)
        self.assertEqual(L['scan_lines'], 2 * SCAN['points'])
        self.assertEqual(L['spectroscopy_curves'], 2.0)
        self.assertEqual((L['approaches'], L['holder_insertions']), (1.0, 1.0))

    def test_two_instances_are_isolated(self):
        a, b = _scenario('engaged'), _scenario('engaged')
        a.invoke('scan.start', **SCAN)
        a.step(20.0)
        self.assertNotIn('IMG0001', b.records)
        self.assertNotEqual(a.t, b.t)

    def test_agent_does_not_see_truth_or_foreign_objects(self):
        h = _scenario('scanned')
        h.register_principal('agent', 'agent')
        h.register_principal('agent2', 'agent')
        h.register_principal('ev', 'evaluator')
        s, s2, ev = h.session('agent'), h.session('agent2'), h.session('ev')
        v = s.read(instance_id=h.instance_id)['observations']
        self.assertFalse([k for k in v if k.startswith('truth.')])
        with self.assertRaises(rt.ActionRejected):
            s.read(instance_id=h.instance_id, state_ids=['truth.tip'])
        self.assertIn('truth.tip', ev.read(instance_id=h.instance_id)['observations'])
        r = s.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=dict(record_id='IMG0001', channel=None, direction=None, region_px=None,
                     max_values=64), request_id='r1')
        self.assertEqual((r['result']['record']['template_source']['template_id'], r['result']['record']['template_source']['substance']), (None, None))
        r = s.invoke(instance_id=h.instance_id, capability_id='scan.start', arguments=dict(SCAN), request_id='r2')
        self.assertEqual(r['status'], 'accepted')
        rid = h.op['record_id']
        r = s2.invoke(instance_id=h.instance_id, capability_id='tip.cleaning_pulse', arguments={}, request_id='r3')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        h.step(10.0)
        r = s2.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=dict(record_id=rid, channel=None, direction=None, region_px=None,
                      max_values=64), request_id='r4')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn(rid, s2.invoke(instance_id=h.instance_id, capability_id='records.list', arguments={}, request_id='r5')['result']['records'])
        ev_events = s.invoke(instance_id=h.instance_id, capability_id='events.read', arguments=dict(since_s=0.0), request_id='r6')['result']['events']
        self.assertFalse([e for e in ev_events if e['kind'] == 'fault'])

    def test_idempotent_requests_conflicts_and_large_replays(self):
        h = _scenario('scanned')
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        a = s.invoke(instance_id=h.instance_id, capability_id='z_controller.set', arguments=dict(ZC_HOPG), request_id='q1')
        b = s.invoke(instance_id=h.instance_id, capability_id='z_controller.set', arguments=dict(ZC_HOPG), request_id='q1')
        self.assertEqual(a, b)
        c = s.invoke(instance_id=h.instance_id, capability_id='z_controller.set', arguments=dict(ZC_HOPG, i_gain=3.0), request_id='q1')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')
        scan(h, points=128)
        big = dict(record_id='IMG0002', channel=None, direction=None, region_px=None, max_values=65536)
        s.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=big, request_id='L0')
        for i in range(rt.LARGE_RESPONSE_KEEP + 2):
            s.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=dict(big, max_values=65536 - i - 1), request_id=f'L{i + 1}')
        self.assertIsNone(h._requests[('agent', 'L0')][1])
        again = s.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=big, request_id='L0')
        self.assertEqual(again['status'], 'succeeded')
        other = s.invoke(instance_id=h.instance_id, capability_id='records.list', arguments={}, request_id='L0')
        self.assertEqual(other['error']['code'], 'REQUEST_CONFLICT')

    def test_defects_become_device_errors(self):
        h = _scenario('engaged')
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        orig = rt.NanosurfNaiostmRuntime._cmd_tip_cleaning_pulse
        try:
            rt.NanosurfNaiostmRuntime._cmd_tip_cleaning_pulse = lambda self: 1 / 0
            r = s.invoke(instance_id=h.instance_id, capability_id='tip.cleaning_pulse', arguments={}, request_id='d1')
        finally:
            rt.NanosurfNaiostmRuntime._cmd_tip_cleaning_pulse = orig
        self.assertEqual((r['status'], r['error']['code'], r['error']['side_effects']), ('rejected', 'DEVICE_ERROR', 'unknown'))


# ---------------------------------------------------------------- declared effects
class DeclaredEffects(unittest.TestCase):
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
                    t, t_end = 0.0, (1800.0 if oid else 120.0)
                    while t < t_end - 1e-9:
                        d = 0.25 if t < 5.0 - 1e-9 else 2.0
                        A.step(d)
                        B.step(d)
                        t += d
                        a2, b2 = A.read('evaluator'), B.read('evaluator')
                        for k in v0:
                            if k not in rt.ON_STEP.get(cap, []) and _step_differs(v1[k], a2[k], v0[k], b2[k]):
                                missing.setdefault(cap, set()).add((sname, k))
                        if oid and A.operations[oid]['phase'] != 'running' and t_end == 1800.0:
                            t_end = t + 5.0
        self.assertEqual(missing, {})


# ---------------------------------------------------------------- hidden truth, motor model and feedback edge cases (independent review, round 1)
class HiddenTruthAndMotorModel(unittest.TestCase):
    def test_advance_reports_the_command_not_the_gap_or_the_crash(self):
        h = _scenario('near')
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r = s.invoke(instance_id=h.instance_id, capability_id='approach.advance', arguments=dict(duration_s=2.0), request_id='a1')
        self.assertEqual(r['result'], dict(travel_m=2.0 * rt.MOTOR_SPEED_M_S, duration_s=2.0))
        obs = s.read(instance_id=h.instance_id)['observations']
        self.assertNotIn('holder.position_m', obs)
        self.assertNotIn('holder.target_m', obs)
        h.step(1.0)                                                                           # contact after 0.48 s, stalled 20 um later
        self.assertEqual(h.read('evaluator')['truth.tip']['kind'], 'crashed')
        self.assertTrue(s.read(instance_id=h.instance_id)['observations']['holder.moving']['value'])      # the motor is still driven
        h.step(1.1)
        self.assertFalse(h.read()['holder.moving'])
        ev = s.invoke(instance_id=h.instance_id, capability_id='events.read', arguments=dict(since_s=0.0), request_id='a2')['result']['events']
        self.assertFalse({'MOTOR_STALLED', 'TIP_CRASH'} & {e['code'] for e in ev})
        self.assertIn(('fault', 'MOTOR_STALLED'), [(e['kind'], e['code']) for e in h.events])
        self.assertAlmostEqual(h.read('evaluator')['truth.tip']['recess_m'], rt.BEND_M, delta=1e-9)

    def test_approach_does_not_publish_its_outcome(self):
        bound = (20000 + 1) * rt.CYCLE_S + rt.APPROACH_SETTLE_S
        for sample in ('hopg', 'glass'):
            h = near(holder_in(ready(), sample))
            h.invoke('z_controller.set', **ZC_HOPG)
            h.invoke('approach.start', move_speed_percent=50.0, max_steps=20000)
            v = h.read()
            self.assertNotIn('holder.target_m', v)
            self.assertEqual((v['operation.progress'], v['operation.remaining_s']), (0.0, bound))     # the same bound for a found and a failing approach
            h.step(1.0)
            v = h.read()
            self.assertAlmostEqual(v['operation.progress'], 50 / 20000, places=12)                    # cycles done / max steps
            self.assertAlmostEqual(v['operation.remaining_s'], bound - 1.0, places=9)
            self.assertTrue(v['holder.moving'])

    def test_rejected_insertion_leaves_the_holder_unchanged(self):
        h = ready()
        h.invoke('cover.lift')
        h.invoke('sample.mount', sample_id='hopg')
        rejected(h, 'holder.insert', 'COLLISION_RISK', position_m=h.samples['hopg']['stack_m'] + 0.05e-3, angle_rad=1.0)
        self.assertEqual((h.holder['angle'], h.holder['location'], h.holder['q'], h.holder['view_bias']), (0.0, 'container', None, 1.0))

    def test_no_current_while_engaged_drives_the_tip_into_the_surface(self):
        h = engaged(**QUIET)
        h.invoke('z_controller.set', **dict(ZC_HOPG, tip_voltage_v=0.0))
        self.assertEqual(h.read('evaluator')['truth.tip']['kind'], 'crashed')                 # the feedback extends at once
        h.step(0.1)
        v = h.read('evaluator')
        self.assertEqual((v['truth.tip']['kind'], v['probe_status.light']), ('crashed', 'orange'))
        self.assertAlmostEqual(v['truth.gap_m'], rt.D_CONTACT_M, delta=1e-18)                  # pressed back to point contact
        self.assertEqual(h.ledger()['tip_crashes'], 1.0)
        self.assertIn('TIP_CRASH', [e['code'] for e in h.events])
        h.invoke('z_controller.set', **ZC_HOPG)
        h.step(0.1)
        v = h.read('evaluator')
        self.assertAlmostEqual(v['truth.gap_m'], v['truth.setpoint_gap_m'], delta=1e-12)          # the feedback holds the set point again
        self.assertEqual((h.ledger()['tip_crashes'], v['probe_status.light']), (1.0, 'green'))

    def test_high_tip_voltage_holds_the_tip_farther_away(self):
        m = rt.material('ito')
        d = rt.setpoint_gap(m, 10.0, 0.1e-9, 0.4, 295.15)
        self.assertIsNotNone(d)
        self.assertGreater(d[0], 5e-9)
        i = abs(float(rt.tunnel_current(10.0, np.array([d[0]]), m, rt.barrier_ev(m, 0.4), rt.KB * 295.15 / rt.E_CH)[0]))
        self.assertAlmostEqual(i / 0.1e-9, 1.0, delta=1e-6)
        ito = dict(sample_id='ito', template_id='ito_glass', seed=2, tilt_rad=[0.0, 0.0])
        h = near(holder_in(ready(samples=SAMPLES + (ito,)), 'ito'))
        h.invoke('z_controller.set', setpoint_a=0.1e-9, tip_voltage_v=10.0, p_gain=1.0, i_gain=15.0)
        op = wait_op(h, h.invoke('approach.start', move_speed_percent=50.0, max_steps=20000)['operation_id'])
        self.assertEqual(op['phase'], 'completed')
        self.assertNotEqual(h.read('evaluator')['truth.tip']['kind'], 'crashed')

    def test_hand_guard_is_the_eye_estimate_not_the_exact_gap(self):
        h = _scenario('holder_in')
        b = h.holder['view_bias']
        q_now, g_now = h.holder['q'], h.read('evaluator')['truth.gap_m']
        lo, hi = q_now - g_now, q_now                                                        # rejected at contact, accepted here
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            try:
                copy.deepcopy(h).invoke('holder.push', position_m=mid)
                hi = mid
            except rt.ActionRejected:
                lo = mid
        g_edge = g_now + (hi - q_now)
        self.assertAlmostEqual(g_edge * b + h._look_noise(False), rt.EYE_TOUCH_M, delta=1e-9)   # seen (g x bias + jitter) closer than 0.2 mm appears to touch
        self.assertGreater(abs(g_edge - rt.PUSH_MIN_GAP_M), 1e-6)                             # not an oracle of the true 0.2 mm

    def test_z_output_and_topography_share_the_nominal_scale(self):
        h = engaged(settle_s=600.0, **QUIET)
        v = h.read('evaluator')
        ext, cal = h._z_state()['ext'], h.cal['z']
        self.assertAlmostEqual(v['zscanner.extension_m'], ext / (1 + cal), delta=3 * rt.Z_DAC_M + 5 * rt.VIBRATION_M['quiet'])
        self.assertAlmostEqual(v['zscanner.extension_m'] / rt.Z_DAC_M, round(v['zscanner.extension_m'] / rt.Z_DAC_M), places=6)    # 16-bit grid
        self.assertGreater(abs(v['zscanner.extension_m'] - ext), 10 * rt.Z_DAC_M)                # not the real extension
        rec = scan(h, image_size_m=20e-9, time_per_line_s=0.05, points=32)
        self.assertAlmostEqual(rt.Z_RANGE_M / 2 - float(np.mean(topo(rec))), v['zscanner.extension_m'], delta=0.3e-9)   # both nominal

    def test_extension_is_held_while_the_approach_settles(self):
        h = _scenario('near')
        h.invoke('z_controller.set', **ZC_HOPG)
        h.invoke('approach.start', move_speed_percent=50.0, max_steps=20000)
        x = h.op['extra']
        h.step(x['drive_end'] - h.t + 1e-6)                                                   # the engaging cycle is over, 0.5 s of settling left
        self.assertIsNotNone(h.op)
        exts = []
        for _ in range(20):
            v = h.read('evaluator')
            self.assertAlmostEqual(v['truth.gap_m'], x['d_found'], delta=1e-12)
            exts.append(v['zscanner.extension_m'])
            h.step(0.02)
        self.assertLess(max(exts) - min(exts), 0.05e-9)                                       # held, not sweeping
        self.assertGreater(min(exts), 0.5 * rt.Z_RANGE_M)

    def test_empty_holder_approach_stays_within_the_travel(self):
        h = ready()
        h.invoke('cover.lift')
        h.invoke('holder.insert', position_m=1.0e-3, angle_rad=0.0)
        h.invoke('cover.place')
        near(h)
        h.invoke('z_controller.set', **ZC_HOPG)
        wait_op(h, h.invoke('approach.start', move_speed_percent=50.0, max_steps=20000)['operation_id'])
        q = h.read('evaluator')['holder.position_m']
        self.assertGreaterEqual(q, rt.HOLDER_TRAVEL_M[0])
        self.assertEqual(asset_tool.validate_value(q, _meta()['state_slots']['holder.position_m']['value_schema']), [])

    def test_dirty_guides_slow_a_move_already_running(self):
        h = _scenario('near')
        h.invoke('approach.retract', duration_s=4.0)
        h.step(1.0)
        q1 = h.holder['q']
        h.invoke('fault.inject', fault='approach_motor_dirty', active=True)
        h.step(2.0)
        self.assertAlmostEqual(h.holder['q'] - q1, 2.0 * rt.MOTOR_SPEED_M_S * rt.DIRTY_MOTOR_FACTOR, places=12)
        h = _scenario('approaching')                                                          # during the automatic approach: smaller steps
        step0 = h.op['extra']['step_m']
        h.invoke('fault.inject', fault='approach_motor_dirty', active=True)
        self.assertAlmostEqual(h.op['extra']['step_m'], step0 * rt.DIRTY_MOTOR_FACTOR, places=15)
        op = wait_op(h, h.op['op'])
        self.assertEqual((op['phase'], h.read()['approach.state']), ('completed', 'engaged'))

    def test_sub_dac_pixels_are_flagged_and_a_slow_withdraw_books_no_steps(self):
        h = engaged(**QUIET)
        rec = scan(h, image_size_m=1e-9, time_per_line_s=0.05, points=256)
        self.assertIn('pixel_below_xy_dac_step', rec['warnings'])
        n0, trav0, q = h.ledger()['approach_steps'], h.ledger()['motor_travel_m'], h.holder['q']
        self.assertEqual(h.invoke('approach.withdraw', steps=100, move_speed_percent=5.0), dict(travel_m=0.0, duration_s=100 * rt.CYCLE_S))
        settle(h)
        self.assertEqual((h.holder['q'], h.ledger()['approach_steps'], h.ledger()['motor_travel_m']), (q, n0, trav0))

    def test_approach_output_is_the_commanded_ramp_even_after_a_run_in(self):
        h = near(holder_in(ready(), 'glass'))
        h.invoke('z_controller.set', **ZC_HOPG)
        h.invoke('approach.start', move_speed_percent=50.0, max_steps=20000)
        x = h.op['extra']
        t_touch = x['t_plan'] + math.ceil((x['q_start'] - h.holder['stop']['q_crash']) / x['step_m'] - 1e-9) * rt.CYCLE_S
        for cycles_after in (-2, 0, 3, 50):
            t = t_touch + (cycles_after + 0.95) * rt.CYCLE_S
            h.step(t - h.t)
            self.assertAlmostEqual(h.read()['zscanner.extension_m'], 0.95 * rt.Z_RANGE_M, delta=2 * rt.Z_DAC_M)   # the ramp, before and after the contact
        self.assertEqual(h.read('evaluator')['truth.tip']['kind'], 'crashed')
        self.assertEqual(h.read('evaluator')['truth.gap_m'], rt.D_CONTACT_M)

    def test_looks_jitter_in_time_and_touching_uses_the_estimate(self):
        h = _scenario('near')                                                                # cover on: the 10x magnifier
        a, b = h.invoke('magnifier.view'), h.invoke('magnifier.view')
        self.assertEqual(a, b)                                                               # the same look at the same time
        g = h.read('evaluator')['truth.gap_m']
        seen = [g * h.holder['view_bias'] + h._look_noise(True, h.t + 0.1 * i) for i in range(20)]
        self.assertGreater(np.std(seen), 0.3 * rt.MAGNIFIER_SIGMA_M)                         # looks at other times vary
        self.assertLessEqual(max(abs(s - g * h.holder['view_bias']) for s in seen), 3 * rt.MAGNIFIER_SIGMA_M + 1e-15)
        c = copy.deepcopy(h)
        c.holder['view_bias'] = (25e-6 - c._look_noise(True)) / g                            # this look sees 25 um ...
        self.assertGreater(g, 30e-6)                                                         # ... of a larger true gap
        self.assertTrue(c.invoke('magnifier.view')['appears_touching'])

    def test_two_guards_leave_the_gap_uncertain_by_the_look_jitter(self):
        h = _scenario('holder_in')
        b, n = h.holder['view_bias'], h._look_noise(False)
        q_now, g_now = h.holder['q'], h.read('evaluator')['truth.gap_m']

        def edge(accept):
            lo, hi = q_now - g_now, q_now + 1e-3
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if accept(mid):
                    hi = mid
                else:
                    lo = mid
            return hi

        def push_ok(q):
            try:
                copy.deepcopy(h).invoke('holder.push', position_m=q)
                return True
            except rt.ActionRejected:
                return False

        def rotate_ok(q):
            c = copy.deepcopy(h)
            try:
                c.invoke('holder.push', position_m=q)
                c.invoke('holder.rotate', angle_rad=0.0)
                return True
            except rt.ActionRejected:
                return False
        q1, q2 = edge(push_ok), edge(rotate_ok)
        b_solved = (0.25e-3 - 0.20e-3) / (q2 - q1)                                            # the two thresholds give the bias ...
        g_solved = 0.20e-3 / b_solved - (q1 - q_now)                                         # ... but the gap only up to the jitter of the look
        self.assertAlmostEqual(b_solved, b, delta=1e-6)
        self.assertAlmostEqual(g_now - g_solved, -n / b, delta=1e-9)

    def test_every_insertion_is_looked_at_afresh(self):
        h = _scenario('holder_in')
        n1 = h._look_noise(False)
        h.invoke('holder.remove')
        stack = h.samples['hopg']['stack_m']
        for dq in (0.05e-3, 0.12e-3, 0.16e-3, 0.2e-3, 0.3e-3, 1.0e-3):                        # the insertion check uses the look at the new insertion
            c = copy.deepcopy(h)
            b_new = float(np.clip(1.0 + 0.15 * c._rng(29, c.seq['insert'] + 1).standard_normal(), 0.6, 1.4))
            c.holder.update(location='head', q=stack + dq, view_bias=b_new)
            want = c._eye_gap(c._gap_mech(), c.seq['insert'] + 1) < rt.PUSH_MIN_GAP_M
            c = copy.deepcopy(h)
            if want:
                rejected(c, 'holder.insert', 'COLLISION_RISK', position_m=stack + dq, angle_rad=0.0)
            else:
                c.invoke('holder.insert', position_m=stack + dq, angle_rad=0.0)
                self.assertNotEqual(c._look_noise(False), n1)                                  # a new look, not the jitter of the previous insertion

    def test_guard_and_magnifier_agree_with_the_cover_lifted(self):
        h = _scenario('holder_in')
        for dq in np.linspace(-0.9e-3, 0.0, 19):
            c = copy.deepcopy(h)
            q = h.holder['q'] + dq
            est = c._eye_gap(c._hand_gap(q))
            try:
                c.invoke('holder.push', position_m=q)
                ok = True
            except rt.ActionRejected:
                ok = False
            if ok:
                m = c.invoke('magnifier.view')
                self.assertFalse(m['appears_touching'])
                self.assertGreaterEqual(m['gap_estimate_m'], rt.PUSH_MIN_GAP_M - 1e-15)
            self.assertEqual(ok, est >= rt.PUSH_MIN_GAP_M)

    def test_dirty_guides_after_a_run_in_shorten_the_remaining_steps(self):
        h = near(holder_in(ready(), 'glass'))
        h.invoke('z_controller.set', **ZC_HOPG)
        h.invoke('approach.start', move_speed_percent=50.0, max_steps=20000)
        x = h.op['extra']
        t_touch = x['t_plan'] + math.ceil((x['q_start'] - h.holder['stop']['q_crash']) / x['step_m'] - 1e-9) * rt.CYCLE_S
        h.step(t_touch + 2.5 * rt.CYCLE_S - h.t)
        self.assertTrue(h.holder['stop']['crashed'])
        h.invoke('fault.inject', fault='approach_motor_dirty', active=True)
        q0 = h.holder['q']
        h.step(10 * rt.CYCLE_S)
        self.assertAlmostEqual(q0 - h.holder['q'], 10 * 150e-9 * rt.DIRTY_MOTOR_FACTOR, delta=1e-15)
        self.assertEqual(h.ledger()['tip_crashes'], 1.0)

    def test_readings_need_the_controller(self):
        h = configured()
        h.register_principal('agent', 'agent')
        obs = h.session('agent').read(instance_id=h.instance_id)['observations']
        for k in ('zscanner.extension_m', 'current.reading_a'):
            self.assertEqual((obs[k]['value'], obs[k]['quality']), (None, 'unavailable'))
        h.invoke('power.set', enabled=True)
        h.step(rt.BOOT_S - 1.0)
        self.assertIsNone(h.read()['current.reading_a'])                                      # still initialising
        h.step(2.0)
        self.assertEqual(h.read()['zscanner.extension_m'], 0.0)                               # ready, Z fully retracted
        self.assertLess(abs(h.read()['current.reading_a']), 20e-12)                           # amplifier noise only


class ZZRejectionCodes(unittest.TestCase):
    def test_every_observed_rejection_code_is_declared(self):
        """Runs last (class name): every code raised by a device call anywhere in this test module is declared for that capability."""
        undeclared = {k: sorted(v - set(rt.CAP_ERRORS.get(k, [])) - {'INVALID_ARGUMENT', 'UNSUPPORTED', 'NOT_IMPLEMENTED', 'UNKNOWN_CAPABILITY'})
                      for k, v in REJECTIONS.items()}
        self.assertEqual({k: v for k, v in undeclared.items() if v}, {})


if __name__ == '__main__':
    unittest.main()
