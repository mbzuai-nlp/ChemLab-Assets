"""tests for CHEM-158 park_nx10 (Park Systems NX10 AFM): python3 -m unittest tests/test_asset_park_nx10.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged, two instances
are isolated, agent/evaluator truth isolation (truth slots, template substances of the sample, evaluator-only warnings and fault events,
other agents' records and operations), conservation (electrical energy = 200 W x powered time, beam time, stage / scanner travel, line and
curve counts), real return values validated against the declared schemas (asset_tool lint_schema / validate_value), asynchronous completion
and cancellation remnants, host-step independence, declared immediate and on_step effects (scenario states, short and long windows); plus
the instrument (initialisation, SLD beam and PSPD alignment with the knobs, head removal and probe exchange, Z-stage interlock, touch stop and
crash, camera focus, collision guards, automatic approach and its failure modes) and the physics: sphere-cone tip dilation, the calibration
grating (step height 100 +- 7 nm and pitch), HOPG atomic steps, feedback dynamics (error with scan speed and gain, parachuting, oscillation
above the critical gain, Z-range saturation), noise floor with the acoustic enclosure and isolation, thermal drift after loading, tapping
phase contrast of a PS/PMMA blend, True Non-Contact operating point (drive side of the resonance), contact-mode indentation and tip wear,
force-distance curves (jump-in, DMT pull-off, humidity), thermal-tune spring constant and its error propagation from the sensitivity,
sensitivity from force curves, DMT fit of the modulus, frequency sweep, and SmartAnalysis-like flattening / roughness / profiles / steps.
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
from asset_runtime import park_nx10 as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE, REJECTIONS = [], {}, {}
_META = {}
_SC = {}


def _meta():
    if not _META:
        _META.update(rt.export_park_nx10_contract_metadata())
    return _META


def _changed(a, b):
    return json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str)


_orig_invoke = rt.ParkNx10Runtime.invoke


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


rt.ParkNx10Runtime.invoke = _instrumented_invoke

GRATING = dict(sample_id='grating', template_id='calibration_grating_xyz', seed=7, tilt_rad=[0.002, -0.001])
HOPG = dict(sample_id='hopg', template_id='hopg', seed=3, tilt_rad=[0.0005, 0.0])
MICA = dict(sample_id='mica', template_id='mica', seed=5, tilt_rad=[0.0, 0.0])
BLEND = dict(sample_id='blend', template_id='ps_pmma_blend', seed=11, tilt_rad=[0.0, 0.0])
AUNP = dict(sample_id='aunp', template_id='au_np_on_mica', seed=2, tilt_rad=[0.0, 0.0])
SIO2 = dict(sample_id='sio2', template_id='sio2_on_si', seed=4, tilt_rad=[0.0, 0.0])
PMMA = dict(sample_id='pmma', template_id='pmma_film_on_si', seed=6, tilt_rad=[0.0, 0.0])
WAFER = dict(sample_id='wafer', template_id='si_wafer', seed=8, tilt_rad=[0.0, 0.0])
TALL = dict(sample_id='tall', template_id='grating_1d_20nm', seed=9, tilt_rad=[0.0, 0.0], topography_overrides=dict(height_m=18e-6, pitch_m=10e-6, edge_width_m=4e-6))
SAMPLES = (GRATING, HOPG, MICA, BLEND, AUNP, SIO2, PMMA, WAFER, TALL)
PROBES = (('tap1', 'ac160ts'), ('tap2', 'tap300al_g'), ('cont1', 'contal_g'), ('multi1', 'multi75al_g'), ('nc1', 'nchr'))
SCAN = dict(size_m=5e-6, pixels_x=64, pixels_y=64, rate_hz=2.0, angle_rad=0.0, offset_x_m=0.0, offset_y_m=0.0, setpoint_force_n=None, setpoint_amplitude_m=16e-9,
            z_servo_gain=1.0, save_retrace=False, retract_after=False)


def configured(seed=1, samples=SAMPLES, probes=PROBES, **plant):
    h = rt.ParkNx10Runtime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    for s in samples:
        h.invoke('sample.provide', **s)
    for pid, tid in probes:
        h.invoke('probe.provide', probe_id=pid, type_id=tid)
    return h


def ready(seed=1, **kw):
    h = configured(seed, **kw)
    h.invoke('power.set', enabled=True)
    h.step(rt.BOOT_S + 1.0)
    return h


def settle(h, limit=600.0):
    t = 0.0
    while (h._xy_moving() or h._z_moving() or h._f_moving() or h.head_move or h.exchange) and t < limit:
        h.step(0.5)
        t += 0.5
    assert not (h._xy_moving() or h._z_moving() or h._f_moving() or h.head_move or h.exchange)
    return h


def mount(h, pid):
    """Raise the Z stage, take the head off, exchange the probe, put the head back, switch the beam on (as an operator would)."""
    if h.qz < 0.004:
        h.invoke('z_stage.move', z_m=0.005)
        settle(h)
    if h.beam_switch:
        h.invoke('beam.set', on=False)                                                   # manual: beam off before the head is unmounted
    h.invoke('head.remove')
    settle(h)
    if h.mounted is not None:
        h.invoke('probe.unmount')
        settle(h)
    h.invoke('probe.mount', probe_id=pid)
    settle(h)
    h.invoke('head.attach')
    settle(h)
    h.invoke('beam.set', on=True)
    return h


def align(h, inset=12e-6):
    """Agent-level beam alignment: centre the PSPD from the VERTICAL / LATERAL readings, put the SLD spot `inset` inside the cantilever end from
    the camera, centre the PSPD again (knob gains as declared)."""
    def centre():
        from scipy.special import erfinv
        for _ in range(3):
            r = h.read()
            I = r['pspd.intensity_v']
            for knob, key in (('pspd_y', 'pspd.vertical_v'), ('pspd_x', 'pspd.lateral_v')):
                v = h.read()[key]
                u = float(erfinv(max(-0.999, min(0.999, v / I)))) * rt.PSPD_SPOT_W_M
                h.invoke('head.turn_knob', knob=knob, turns=max(-5.0, min(5.0, -u / rt.KNOB_PSPD_M_PER_TURN)))
    centre()
    cam = h.invoke('camera.read')
    dx, dy = cam['spot_offset_m']
    h.invoke('head.turn_knob', knob='beam_x', turns=(-inset - dx) / rt.KNOB_BEAM_M_PER_TURN)
    h.invoke('head.turn_knob', knob='beam_y', turns=-dy / rt.KNOB_BEAM_M_PER_TURN)
    centre()
    return h


def dynamic(h, mode='tapping', db=None, above=1.0003, amplitude=20e-9):
    """Configure a dynamic mode, sweep the resonance and set the drive slightly above the peak."""
    pr = h._probe()
    db = db or pr['type_id']
    h.invoke('head.configure', mode=mode, cantilever_db=db)
    f0 = rt.probe_nominal(db)['f0']
    op = h.invoke('ncm.frequency_sweep', start_hz=0.7 * f0, stop_hz=1.3 * f0, points=2048)['operation_id']
    wait(h, op)
    rec = h.records[h.operations[op]['record_id']]
    fpk = rec['data']['peak_hz']
    op = h.invoke('ncm.frequency_sweep', start_hz=fpk * 0.99, stop_hz=fpk * 1.01, points=1024)['operation_id']
    wait(h, op)
    fpk = h.records[h.operations[op]['record_id']]['data']['peak_hz']
    h.invoke('ncm.set_drive', frequency_hz=fpk * above, amplitude_m=amplitude)
    return h


def wait(h, op, tmax=4000.0, dt=1.0):
    t = 0.0
    while h.operations[op]['phase'] == 'running' and t < tmax:
        h.step(dt)
        t += dt
    assert h.operations[op]['phase'] != 'running', op
    rid = h.operations[op]['record_id']
    return h.records[rid] if rid else h.operations[op]


def near(h, gap=100e-6):
    """Lower the Z stage to `gap` above the surface under the tip (the operator judges it from the camera focus; here from the touch point)."""
    h.invoke('z_stage.move', z_m=h._touch_point() + gap)
    return settle(h)


def engage(h, sample=GRATING, mode='tapping', setpoint=16e-9, gain=1.0, offset=(0.0, 0.0)):
    if h.on_chuck == sample['sample_id'] and h.engaged is not None:
        return h, None
    if h.on_chuck != sample['sample_id']:
        if h.on_chuck is not None:
            if h.engaged is not None:
                h.invoke('retract', distance_m=100e-6)
            h.invoke('z_stage.move', z_m=min(rt.Z_STAGE_LIMITS_M[1], h.qz + 0.004))
            settle(h)
            h.invoke('sample.remove', sample_id=h.on_chuck)
        h.invoke('sample.place', sample_id=sample['sample_id'], offset_x_m=offset[0], offset_y_m=offset[1])
    near(h)
    a = dict(setpoint_force_n=setpoint if mode == 'contact' else None, setpoint_amplitude_m=None if mode == 'contact' else setpoint, z_servo_gain=gain)
    op = h.invoke('approach', **a)['operation_id']
    wait(h, op)
    return h, op


def tapping_engaged(seed=1, sample=GRATING, probe='tap1', **kw):
    h = ready(seed, **kw)
    mount(h, probe)
    align(h)
    dynamic(h)
    engage(h, sample)
    assert h.engaged is not None
    return h


def contact_engaged(seed=1, sample=GRATING, probe='cont1', setpoint=5e-9, **kw):
    h = ready(seed, **kw)
    mount(h, probe)
    align(h)
    h.invoke('head.configure', mode='contact', cantilever_db=h._probe()['type_id'])
    engage(h, sample, mode='contact', setpoint=setpoint)
    assert h.engaged is not None
    return h


def scan(h, **kw):
    a = dict(SCAN, **kw)
    if h.mode == 'contact':
        a['setpoint_amplitude_m'] = None
        a['setpoint_force_n'] = a['setpoint_force_n'] or 5e-9
    op = h.invoke('scan.start', **a)['operation_id']
    return wait(h, op, dt=5.0)


def img(rec, ch='height', d='trace', flatten='none'):
    return rt.flatten_image(np.asarray(rec['data'][ch][d], float)[:rec['meta']['lines_completed']], flatten)


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


def set_true_calibration(h):
    """Evaluator shortcut: write the hidden true k and sensitivity into the calibration table (to test physics independent of calibration)."""
    pr = h._probe()
    h.invoke('calibration.set', spring_constant_n_m=pr['truth']['k'], sensitivity_v_per_m=h._optics()['S'])


def _close(a, b, rel=1e-9):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], rel) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y, rel) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return True
        return abs(a - b) <= rel * max(abs(a), abs(b), 1e-300)
    return a == b


# ---------------------------------------------------------------- scenario states (declared-effect checks)
def _build(name):
    if name == 'fresh':
        return configured()
    if name == 'booting':
        h = configured()
        h.invoke('power.set', enabled=True)
        h.step(5.0)
        return h
    if name == 'ready':
        return ready()
    if name == 'head_off':
        h = ready()
        h.invoke('z_stage.move', z_m=0.005)
        settle(h)
        h.invoke('head.remove')
        return settle(h)
    if name == 'head_off_mounted':
        h = _build('mounted')
        h.invoke('head.remove')
        return settle(h)
    if name == 'exchanging':
        h = _build('head_off')
        h.invoke('probe.mount', probe_id='tap1')
        h.step(5.0)
        return h
    if name == 'mounted':
        h = ready()
        return mount(h, 'tap1')
    if name == 'aligned':
        h = mount(ready(), 'tap1')
        align(h)
        dynamic(h)
        h.invoke('sample.place', sample_id='grating', offset_x_m=0.0, offset_y_m=0.0)
        return h
    if name == 'near':
        return near(_build('aligned'))
    if name == 'approaching':
        h = _build('near')
        h.invoke('approach', setpoint_force_n=None, setpoint_amplitude_m=16e-9, z_servo_gain=1.0)
        h.step(1.0)
        return h
    if name == 'engaged':
        h = _build('near')
        wait(h, h.invoke('approach', setpoint_force_n=None, setpoint_amplitude_m=16e-9, z_servo_gain=1.0)['operation_id'])
        return h
    if name == 'scanning':
        h = _build('engaged')
        h.invoke('scan.start', **dict(SCAN, pixels_y=64, rate_hz=2.0))
        h.step(12.0)
        return h
    if name == 'scanned':
        h = _build('engaged')
        scan(h)
        return h
    if name == 'fd_running':
        h = _build('scanned')
        h.invoke('fd.acquire', z_range_m=100e-9, rate_hz=1.0, trigger_force_n=300e-9, dwell_s=0.0, count=4, positions_m=[[0.0, 0.0]])
        h.step(1.5)
        return h
    if name == 'fd_done':
        h = _build('scanned')
        wait(h, h.invoke('fd.acquire', z_range_m=100e-9, rate_hz=1.0, trigger_force_n=300e-9, dwell_s=0.0, count=2, positions_m=[[0.0, 0.0]])['operation_id'])
        return h
    if name == 'tuning':
        h = _build('aligned')
        h.invoke('calibration.thermal_tune', temperature_k=295.15, averages=50)
        h.step(2.0)
        return h
    if name == 'sweeping':
        h = _build('aligned')
        h.invoke('ncm.frequency_sweep', start_hz=250e3, stop_hz=330e3, points=512)
        h.step(2.0)
        return h
    if name == 'contact_engaged':
        return contact_engaged(sample=SIO2)
    if name == 'nc_engaged':
        h = mount(ready(), 'nc1')
        align(h)
        dynamic(h, mode='non_contact', amplitude=10e-9, above=1.0008)
        engage(h, SIO2, setpoint=9.5e-9)
        return h
    if name == 'moving':
        h = _build('aligned')
        h.invoke('stage.move_xy', x_m=0.004, y_m=-0.002)
        h.invoke('focus.move', position_m=0.010)
        h.step(0.5)
        return h
    if name == 'z_moving':
        h = _build('aligned')
        h.invoke('z_stage.move', z_m=0.008)
        h.step(0.5)
        return h
    if name == 'sld_fault':
        h = _build('engaged')
        h.invoke('fault.inject', fault='sld_failure', active=True)
        return h
    if name == 'sld_cleared':
        h = _build('sld_fault')
        h.invoke('fault.inject', fault='sld_failure', active=False)
        return h
    if name == 'z_stall':
        h = _build('aligned')
        h.invoke('fault.inject', fault='z_stage_stall', active=True)
        h.invoke('z_stage.move', z_m=0.008)
        return h
    if name == 'burst':
        h = _build('engaged')
        h.invoke('fault.inject', fault='vibration_burst', active=True)
        return h
    if name == 'unpowered_mounted':
        h = _build('aligned')
        h.invoke('power.set', enabled=False)
        return h
    raise KeyError(name)


def _scenario(name):
    if name not in _SC:
        _SC[name] = _build(name)
    return copy.deepcopy(_SC[name])


SCENARIOS = ['fresh', 'booting', 'ready', 'head_off', 'head_off_mounted', 'exchanging', 'mounted', 'aligned', 'near', 'approaching', 'engaged', 'scanning', 'scanned', 'fd_running',
             'fd_done', 'tuning', 'sweeping', 'contact_engaged', 'nc_engaged', 'moving', 'z_moving', 'sld_fault', 'sld_cleared', 'z_stall', 'burst', 'unpowered_mounted']
LONG_SCENARIOS = ['ready', 'aligned', 'near', 'engaged', 'scanned', 'contact_engaged']
STEP_DTS = (0.4, 3.0, 25.0)


def _variants(cap):
    V = {
        'plant.configure': [dict(rt.PLANT_EXAMPLE), dict(rt.PLANT_EXAMPLE, noise_seed=2, humidity=0.7, acoustic_enclosure=False, vibration_isolation=False)],
        'sample.provide': [dict(rt.SAMPLE_EXAMPLE, sample_id='new')],
        'probe.provide': [dict(probe_id='newp', type_id='nchr')],
        'sample.place': [dict(sample_id='grating', offset_x_m=0.0, offset_y_m=0.0), dict(sample_id='mica', offset_x_m=0.002, offset_y_m=0.0)],
        'sample.remove': [dict(sample_id='grating')],
        'power.set': [dict(enabled=True), dict(enabled=False)], 'power.press_button': [{}],
        'beam.set': [dict(on=True), dict(on=False)], 'beam.press_switch': [{}],
        'head.remove': [{}], 'head.attach': [{}], 'probe.mount': [dict(probe_id='tap1'), dict(probe_id='tap2')], 'probe.unmount': [{}],
        'head.configure': [dict(mode='tapping', cantilever_db='ac160ts'), dict(mode='contact', cantilever_db='contal_g')],
        'head.turn_knob': [dict(knob='beam_x', turns=0.1), dict(knob='pspd_y', turns=-0.5), dict(knob='optics_x', turns=1.0)],
        'head.set_knob': [dict(knob='beam_y', turns_total=0.2), dict(knob='pspd_x', turns_total=-1.0)],
        'stage.move_xy': [dict(x_m=0.003, y_m=0.0), dict(x_m=0.0, y_m=0.0)],
        'z_stage.move': [dict(z_m=0.006), dict(z_m=-0.0099)],
        'focus.move': [dict(position_m=0.004)],
        'ncm.frequency_sweep': [dict(start_hz=250e3, stop_hz=330e3, points=256)],
        'ncm.set_drive': [dict(frequency_hz=287.5e3, amplitude_m=20e-9)],
        'approach': [dict(setpoint_force_n=None, setpoint_amplitude_m=16e-9, z_servo_gain=1.0), dict(setpoint_force_n=5e-9, setpoint_amplitude_m=None, z_servo_gain=1.0)],
        'retract': [dict(distance_m=100e-6)],
        'scan.start': [dict(SCAN, pixels_y=32), dict(SCAN, pixels_y=32, setpoint_force_n=5e-9, setpoint_amplitude_m=None)],
        'fd.acquire': [dict(z_range_m=100e-9, rate_hz=2.0, trigger_force_n=100e-9, dwell_s=0.0, count=1, positions_m=[[0.0, 0.0]])],
        'calibration.thermal_tune': [dict(temperature_k=295.15, averages=20)],
        'calibration.sensitivity': [dict(record_id='FD0001', curve_index=0, branch='approach')],
        'calibration.set': [dict(spring_constant_n_m=30.0, sensitivity_v_per_m=None)],
        'analysis.flatten': [dict(record_id='IMG0001', channel='height', direction='trace', method='plane')],
        'analysis.roughness': [dict(record_id='IMG0001', channel='height', direction='trace', flatten='plane', region_px=None)],
        'analysis.line_profile': [dict(record_id='IMG0001', channel='height', direction='trace', flatten='plane', start_px=[0, 10], end_px=[60, 10], points=64)],
        'analysis.step_height': [dict(record_id='IMG0001', channel='height', direction='trace', flatten='plane_upper', method='histogram', region_a_px=None, region_b_px=None)],
        'analysis.fd_fit': [dict(record_id='FD0001', curve_index=0, branch='retract', model='dmt', tip_radius_m=10e-9, sample_poisson_ratio=0.17, tip_modulus_pa=169e9,
                                 tip_poisson_ratio=0.27)],
        'records.delete': [dict(record_id='IMG0001'), dict(record_id='FS0001'), dict(record_id='FD0001')],
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


# ---------------------------------------------------------------- contract and schemas
class ContractAndSchemas(unittest.TestCase):
    def test_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.park_nx10')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-158')
        self.assertEqual(rt.XY_SCAN_RANGE_M, 50e-6)
        self.assertEqual(rt.Z_SCAN_RANGE_M, 15e-6)
        self.assertEqual(2 * rt.XY_STAGE_HALF_M, 0.020)
        self.assertEqual(rt.Z_STAGE_RANGE_M, 0.025)
        self.assertAlmostEqual(rt.Z_STAGE_LIMITS_M[1] - rt.Z_STAGE_LIMITS_M[0], rt.Z_STAGE_RANGE_M, places=12)
        self.assertEqual((rt.SAMPLE_OPEN_MAX_M, rt.SAMPLE_MAX_THICKNESS_M), (0.100, 0.020))
        self.assertEqual(len(rt.nanomech()['subs']), 66)
        self.assertGreaterEqual(len(rt.probes()['types']), 12)
        self.assertEqual(len(rt.sample_templates()['templates']), 15)

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
        for name in ('fresh', 'ready', 'aligned', 'engaged', 'scanning', 'scanned', 'fd_done', 'contact_engaged', 'nc_engaged', 'sld_fault'):
            v = _scenario(name).read('evaluator')
            for k, x in v.items():
                self.assertEqual(asset_tool.validate_value(x, m['state_slots'][k]['value_schema']), [], (name, k))
        h = _scenario('fd_done')
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
        manifest = ROOT / 'assets/expansion_batch04/t3_analytical/park_nx10/geometry_manifest.json'
        raw = json.loads(manifest.read_text()).get('components', []) if manifest.exists() else []
        comps = set(raw) if isinstance(raw, dict) else {c['id'] for c in raw}
        if len(comps) > 5:                                                                     # the real geometry (not the scaffold box)
            used = {c for v in m['capabilities'].values() for c in v['component_ids']} | {s['component_ids'][0] for s in m['state_slots'].values()}
            self.assertEqual(used - comps, set())

    def test_unsupported_capabilities_report_unsupported(self):
        h = ready()
        for k in rt.UNSUPPORTED:
            with self.assertRaises(rt.ActionRejected) as cm:
                _orig_invoke(h, k)
            self.assertEqual(str(cm.exception), 'UNSUPPORTED')
            self.assertEqual(_meta()['capabilities'][k]['availability'], 'unsupported')
        h.register_principal('agent', 'agent')
        s = h.session('agent')
        r = s.invoke(instance_id=h.instance_id, capability_id='kpfm.scan', arguments={'bias_v': 1.0}, request_id='u1')
        self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))

    def test_libraries_carry_evidence(self):
        for s in rt.nanomech()['subs'].values():
            for f in ('youngs_modulus', 'poisson_ratio', 'ambient_surface_energy', 'water_contact_angle', 'hamaker_constant', 'loss_tangent'):
                self.assertIn(s[f]['evidence'], ('documented', 'derived', 'assumption'), (s['substance_id'], f))
                self.assertTrue(s[f]['source_ids'], (s['substance_id'], f))
        for t in rt.sample_templates()['templates'].values():
            for k, v in t['topography'].items():
                if isinstance(v, dict):
                    self.assertIn(v['evidence'], ('documented', 'assumption'), (t['template_id'], k))
        for pid in rt.probes()['types']:
            nom = rt.probe_nominal(pid)
            self.assertGreater(nom['L'], 20e-6, pid)
            self.assertLess(nom['L'], 600e-6, pid)
            c = rt.BEAM_FACTOR * math.sqrt((rt.E_SIN if nom['material'] == 'Si3N4' else rt.E_SI) / (rt.RHO_SIN if nom['material'] == 'Si3N4' else rt.RHO_SI))
            if 'length_m' in nom['derived'] and 'thickness_m' in nom['derived']:
                E = rt.E_SIN if nom['material'] == 'Si3N4' else rt.E_SI
                self.assertAlmostEqual(E * nom['w'] * nom['t'] ** 3 / (4 * nom['L'] ** 3) / nom['k'], 1.0, places=6)
                self.assertAlmostEqual(c * nom['t'] / nom['L'] ** 2 / nom['f0'], 1.0, places=6)


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
            h.invoke('camera.read')
            h.invoke('records.list')
        self.assertEqual((h.t, h.revision), (t, rev))
        self.assertFalse(_changed(before, h.read('evaluator')))

    def test_step_zero_and_negative(self):
        h = _scenario('engaged')
        t = h.t
        h.step(0.0)
        self.assertEqual(h.t, t)
        for bad in (-1.0, float('nan'), float('inf'), rt.MAX_STEP_S * 2, True):
            with self.assertRaises(rt.ActionRejected):
                h.step(bad)
        self.assertEqual(h.t, t)

    def test_host_step_chopping_does_not_change_the_result(self):
        """The same scan, force curves and drift with coarse and fine host steps: identical images and curves (blocks of 16 lines, per-line noise)."""
        outs = []
        for chop in (0.37, 7.0, 40.0):
            h = _scenario('engaged')
            op = h.invoke('scan.start', **dict(SCAN, pixels_y=48))['operation_id']
            t = 0.0
            while h.operations[op]['phase'] == 'running':
                h.step(chop)
                t += chop
            rec = h.records[h.operations[op]['record_id']]
            op2 = h.invoke('fd.acquire', z_range_m=100e-9, rate_hz=2.0, trigger_force_n=100e-9, dwell_s=0.1, count=3, positions_m=[[0.0, 0.0]])['operation_id']
            while h.operations[op2]['phase'] == 'running':
                h.step(chop)
            fd = h.records[h.operations[op2]['record_id']]
            h.step(123.4 - (h.t % 1.0))
            outs.append((img(rec), img(rec, 'phase'), fd['data']['curves'][2]['deflection_retract_v'], h._probe()['radius'], h.read('evaluator')['truth.drift_m']))
        for o in outs[1:]:
            self.assertTrue(np.allclose(o[0], outs[0][0], rtol=0, atol=1e-15))
            self.assertTrue(np.allclose(o[1], outs[0][1], rtol=0, atol=1e-12))
            self.assertTrue(np.allclose(o[2], outs[0][2], rtol=0, atol=1e-12))
            self.assertAlmostEqual(o[3], outs[0][3], delta=1e-18)

    def test_reads_do_not_change_later_results(self):
        a, b = _scenario('engaged'), _scenario('engaged')
        oa = a.invoke('scan.start', **SCAN)['operation_id']
        ob = b.invoke('scan.start', **SCAN)['operation_id']
        for _ in range(10):
            a.read('evaluator')
            a.invoke('camera.read')
            a.step(3.3)
            b.step(3.3)
        wait(a, oa)
        wait(b, ob)
        self.assertTrue(np.array_equal(img(a.records['IMG0001']), img(b.records['IMG0001'])))

    def test_set_points_only_write_targets(self):
        h = _scenario('aligned')
        xy, qz, qf = list(h.xy), h.qz, h.qf
        r = h.invoke('stage.move_xy', x_m=0.004, y_m=-0.003)
        self.assertEqual(h.xy, xy)
        self.assertEqual(h.read()['stage.xy_target_m'], [0.004, -0.003])
        self.assertAlmostEqual(r['duration_s'], math.hypot(0.004, 0.003) / rt.XY_STAGE_SPEED_M_S)
        rejected(h, 'z_stage.move', 'MOTION_IN_PROGRESS', z_m=qz + 0.001)                      # one motorized stage at a time (F13)
        settle(h)
        self.assertTrue(np.allclose(h.read()['stage.xy_m'], [0.004, -0.003], atol=1e-12))
        h.invoke('z_stage.move', z_m=qz + 0.001)
        rejected(h, 'stage.move_xy', 'MOTION_IN_PROGRESS', x_m=0.0, y_m=0.0)
        h.invoke('focus.move', position_m=0.012)
        self.assertEqual((h.qz, h.qf), (qz, qf))
        h.step(0.5)
        self.assertAlmostEqual(h.qz, qz + 0.5 * rt.Z_STAGE_MAX_SPEED_M_S, places=12)
        self.assertAlmostEqual(h.qf, qf + 0.5 * rt.FOCUS_SPEED_M_S, places=12)
        settle(h)
        self.assertAlmostEqual(h.qz, qz + 0.001, places=12)
        self.assertAlmostEqual(h.qf, 0.012, places=12)
        # the resolution of the XY stage (5 um) rounds the target
        self.assertAlmostEqual(h.invoke('stage.move_xy', x_m=0.0012344, y_m=0.0)['target_m'][0], 0.001235, places=15)


# ---------------------------------------------------------------- instrument
class PowerBeamHeadProbe(unittest.TestCase):
    def test_boot_power_beam_and_power_loss(self):
        h = configured()
        rejected(h, 'head.configure', 'POWER_OFF', mode='tapping', cantilever_db='ac160ts')
        h.invoke('power.set', enabled=True)
        self.assertEqual(h.state, 'booting')
        rejected(h, 'stage.move_xy', 'NOT_READY', x_m=0.001, y_m=0.0)
        h.step(rt.BOOT_S - 0.5)
        self.assertEqual(h.state, 'booting')
        h.step(1.0)
        self.assertEqual(h.state, 'ready')
        mount(h, 'tap1')
        self.assertTrue(h.read()['beam.emitting'])
        h.invoke('beam.press_switch')
        self.assertFalse(h.read()['beam.emitting'])
        self.assertEqual(h.read()['pspd.intensity_v'], 0.0)
        h.invoke('beam.press_switch')
        align(h)
        dynamic(h)
        engage(h)
        op = h.invoke('scan.start', **SCAN)['operation_id']
        rejected(h, 'beam.set', 'MEASUREMENT_RUNNING', on=False)
        h.step(20.0)
        h.invoke('power.set', enabled=False)
        rec = h.records[h.operations[op]['record_id']]
        self.assertEqual((h.operations[op]['phase'], rec['status']), ('failed', 'failed'))
        self.assertEqual(rec['meta']['lines_completed'], 40)                               # the lines scanned before the power loss are kept
        self.assertTrue(np.isfinite(img(rec)).all())
        self.assertIsNone(h.engaged)
        self.assertEqual(h.read()['zscanner.extension_m'], 0.0)
        self.assertFalse(h.read()['beam.emitting'])

    def test_head_removal_probe_exchange_and_prealignment(self):
        h = ready()
        rejected(h, 'probe.mount', 'HEAD_ATTACHED', probe_id='tap1')
        h.invoke('sample.place', sample_id='grating', offset_x_m=0.0, offset_y_m=0.0)
        rejected(h, 'z_stage.move', 'INTENSITY_LOW', z_m=rt.Z_STAGE_LIMITS_M[0])           # no probe: the interlock stops the descent
        h.invoke('head.remove')
        self.assertEqual(h.read()['head.state'], 'moving')
        rejected(h, 'head.attach', 'MOTION_IN_PROGRESS')
        h.step(rt.HEAD_SWAP_S)
        self.assertEqual(h.read()['head.state'], 'removed')
        h.invoke('probe.mount', probe_id='tap1')
        self.assertTrue(h.read()['probe.exchanging'])
        rejected(h, 'probe.mount', 'MOTION_IN_PROGRESS', probe_id='tap2')
        h.step(rt.PROBE_MOUNT_S)
        self.assertEqual(h.read()['probe.mounted'], dict(probe_id='tap1', type_id='ac160ts'))
        rejected(h, 'probe.mount', 'PROBE_MOUNTED', probe_id='tap2')
        h.invoke('head.attach')
        settle(h)
        h.invoke('beam.set', on=True)
        cam = h.invoke('camera.read')
        self.assertTrue(cam['cantilever_in_view'] and cam['spot_visible'])
        off = cam['spot_offset_m']
        self.assertLess(abs(off[0] + rt.PREALIGN_INSET_M), 4 * rt.PREALIGN_SIGMA_M)          # pre-aligned: the spot lands near the end
        self.assertLess(abs(off[1]), 4 * rt.PREALIGN_SIGMA_M)
        self.assertGreater(h.read()['pspd.intensity_v'], 0.5)
        # a second probe of the same box: new mounting offsets, the knobs keep their positions
        k0 = dict(h.knobs)
        h.invoke('z_stage.move', z_m=0.005)
        settle(h)
        h.invoke('beam.set', on=False)
        h.invoke('head.remove')
        settle(h)
        h.invoke('probe.unmount')
        settle(h)
        self.assertIn('tap1', h.read()['probe.box_ids'])
        h.invoke('probe.mount', probe_id='tap2')
        settle(h)
        h.invoke('head.attach')
        settle(h)
        h.invoke('beam.set', on=True)
        self.assertEqual(h.knobs, k0)
        self.assertEqual(h.faults, set())
        self.assertNotEqual(h.invoke('camera.read')['spot_offset_m'], off)
        self.assertEqual(h.ledger()['probe_exchanges'], 3.0)
        self.assertEqual(h.ledger()['head_swaps'], 4.0)

    def test_head_handling_rules_of_the_manual(self):
        h = mount(ready(), 'tap1')
        h.invoke('z_stage.move', z_m=0.006)
        settle(h)
        h.invoke('focus.move', position_m=rt.FOCUS_REF_M + 0.003)
        settle(h)
        rejected(h, 'head.remove', 'FOCUS_TOO_LOW')
        h.invoke('focus.move', position_m=rt.FOCUS_REF_M)
        settle(h)
        self.assertTrue(h.beam_switch)
        h.invoke('head.remove')                                                                # with the beam switch on: the beam diode is damaged
        self.assertIn('sld_failure', h.faults)
        self.assertEqual(h.error, 'SLD_FAILURE')
        self.assertIn('SLD_DAMAGED', [e['code'] for e in h.events])
        settle(h)
        h.invoke('head.attach')
        settle(h)
        self.assertFalse(h.read()['beam.emitting'])
        h.invoke('fault.inject', fault='sld_failure', active=False)                            # evaluator: the diode is replaced
        h.invoke('system.acknowledge')
        self.assertTrue(h.read()['beam.emitting'])
        self.assertEqual(h.invoke('power.press_button')['enabled'], False)
        self.assertEqual(h.invoke('power.press_button')['state'], 'booting')

    def test_beam_alignment_intensity_sensitivity_and_pspd(self):
        h = mount(ready(), 'tap1')
        align(h)
        pr = h._probe()
        r = h.read()
        self.assertLess(abs(r['pspd.vertical_v']), rt.PSPD_CENTRE_TOL_V)
        self.assertLess(abs(r['pspd.lateral_v']), rt.PSPD_CENTRE_TOL_V)
        self.assertGreater(r['pspd.intensity_v'], 2.0)                                     # coated lever, aligned: 2-3 V (manual)
        # intensity versus the spot position along the cantilever: falls off the end and toward the chip; sensitivity follows 2 xi - xi^2
        rows = []
        for dx in (-60e-6, -30e-6, 0.0, 30e-6):
            a = copy.deepcopy(h)
            a.invoke('head.turn_knob', knob='beam_x', turns=dx / rt.KNOB_BEAM_M_PER_TURN)
            o = a._optics()
            rows.append((o['x'], o['I'], o['S']))
        self.assertGreater(rows[0][1], rows[3][1])                                          # 30 um beyond the free end the spot misses the lever
        self.assertLess(rows[3][1], 0.2)
        for x, I, S in rows:
            xi = min(1.0, max(0.0, 1 + x / pr['truth']['L']))
            self.assertAlmostEqual(S, rt.SENSITIVITY_V_PER_M_100UM * I / 2.5 * pr['truth']['lever_gain'] * max(2 * xi - xi * xi, 0.02) * 100e-6 / pr['truth']['L'], delta=1e-6 * S)
        # sideways off the lever: intensity drops with the Gaussian overlap
        a = copy.deepcopy(h)
        a.invoke('head.turn_knob', knob='beam_y', turns=(pr['truth']['w'] / 2 + 3 * rt.SPOT_SIGMA_M) / rt.KNOB_BEAM_M_PER_TURN)
        self.assertLess(a.read()['pspd.intensity_v'], 0.05 * r['pspd.intensity_v'])
        # the PSPD knobs move VERTICAL / LATERAL through the erf response and saturate at +-INTENSITY
        a = copy.deepcopy(h)
        a.invoke('head.turn_knob', knob='pspd_y', turns=0.1)
        v1 = a.read()['pspd.vertical_v']
        I = a.read()['pspd.intensity_v']
        self.assertAlmostEqual(v1, I * math.erf(0.1 * rt.KNOB_PSPD_M_PER_TURN / rt.PSPD_SPOT_W_M + (a._optics()['p_v'] - 0.1 * rt.KNOB_PSPD_M_PER_TURN) / rt.PSPD_SPOT_W_M), delta=1e-9)
        a.invoke('head.turn_knob', knob='pspd_y', turns=8.0)
        self.assertAlmostEqual(a.read()['pspd.vertical_v'], I, delta=1e-3 * I)
        rejected(a, 'head.turn_knob', 'KNOB_LIMIT', knob='pspd_y', turns=5.0)
        self.assertEqual(a.invoke('head.set_knob', knob='pspd_y', turns_total=0.0)['turns_total'], 0.0)       # absolute setting (scene knob coupling)
        self.assertEqual(a.read()['knob.pspd_y_turns'], 0.0)
        rejected(a, 'head.set_knob', 'KNOB_LIMIT', knob='pspd_x', turns_total=12.0)
        # an uncoated backside reflects less (manual: about 1 V instead of 2-3 V)
        self.assertFalse(rt.probe_nominal('cdt_contr')['reflective'])
        self.assertEqual(rt.INTENSITY_UNCOATED_V, 1.0)

    def test_z_stage_interlock_touch_stop_and_crash(self):
        h = mount(ready(), 'cont1')
        align(h)
        h.invoke('sample.place', sample_id='sio2', offset_x_m=0.0, offset_y_m=0.0)
        a = copy.deepcopy(h)
        a.invoke('head.turn_knob', knob='beam_y', turns=4.0)                                  # the spot off the lever
        rejected(a, 'z_stage.move', 'INTENSITY_LOW', z_m=a.qz - 0.001)
        a.invoke('z_stage.move', z_m=a.qz + 0.001)                                           # up is always allowed
        # touch stop with the beam aligned
        q_touch = h._touch_point()
        r0 = h._probe()['radius']
        h.invoke('z_stage.move', z_m=rt.Z_STAGE_LIMITS_M[0])
        settle(h)
        self.assertAlmostEqual(h.qz, q_touch - rt.TOUCH_OVERSHOOT_M, delta=1e-12)
        self.assertAlmostEqual(h._probe()['radius'], r0 + rt.TOUCH_WEAR_M, delta=1e-15)
        self.assertEqual(h._probe()['damage'], 'none')
        self.assertIn('TOUCH_STOP', [e['code'] for e in h.events])
        rejected(h, 'stage.move_xy', 'COLLISION_RISK', x_m=0.001, y_m=0.0)                   # the tip rests on the surface
        rejected(h, 'sample.remove', 'COLLISION_RISK', sample_id='sio2')
        # with the PSPD saturated the touch is not detected: the cantilever breaks
        b = mount(ready(), 'cont1')
        align(b)
        b.invoke('sample.place', sample_id='sio2', offset_x_m=0.0, offset_y_m=0.0)
        b.invoke('head.turn_knob', knob='pspd_y', turns=3.0)
        self.assertGreater(abs(b.read()['pspd.vertical_v']), rt.PSPD_TOUCH_LIMIT * b.read()['pspd.intensity_v'])
        q_touch = b._touch_point()
        b.invoke('z_stage.move', z_m=rt.Z_STAGE_LIMITS_M[0])
        settle(b)
        self.assertAlmostEqual(b.qz, q_touch - rt.CRASH_OVERTRAVEL_M, delta=1e-12)
        self.assertEqual(b._probe()['damage'], 'cantilever_broken')
        self.assertEqual(b.read()['pspd.intensity_v'], 0.0)
        self.assertEqual(b.ledger()['tip_crashes'], 1.0)

    def test_camera_focus_depth_of_field(self):
        h = mount(ready(), 'tap1')
        align(h)
        h.invoke('sample.place', sample_id='grating', offset_x_m=0.0, offset_y_m=0.0)
        near(h, 500e-6)
        cam = h.invoke('camera.read')
        self.assertGreater(cam['cantilever_sharpness'], 0.99)                                # focus stage at its reference: the lever is sharp
        self.assertLess(cam['sample_sharpness'], 1e-6)
        gap_to_lever = 500e-6 + h._probe()['truth']['tip_height']
        h.invoke('focus.move', position_m=rt.FOCUS_REF_M + gap_to_lever)                     # focus down onto the sample surface
        settle(h)
        cam = h.invoke('camera.read')
        self.assertGreater(cam['sample_sharpness'], 0.99)
        self.assertLess(cam['cantilever_sharpness'], 1e-6)
        self.assertFalse(cam['spot_visible'])                                                 # the blurred lever: the spot is not located
        h.invoke('focus.move', position_m=rt.FOCUS_REF_M + gap_to_lever + rt.DEPTH_OF_FIELD_M)
        settle(h)
        self.assertAlmostEqual(h.invoke('camera.read')['sample_sharpness'], math.exp(-1.0), delta=1e-6)
        # the optics knobs pan the view: the lever leaves the 480 x 360 um field
        h.invoke('head.turn_knob', knob='optics_y', turns=2.5)
        self.assertFalse(h.invoke('camera.read')['cantilever_in_view'])

    def test_collision_guards_and_sample_area(self):
        h = mount(ready(), 'tap1')
        big = dict(sample_id='wafer_big', template_id='si_wafer', seed=1, tilt_rad=[0.0, 0.0])
        h.invoke('sample.provide', **big)
        self.assertEqual(h.samples['wafer_big']['mount'], 'holder')                          # 10 mm pieces go on the magnetic holder
        rejected(h, 'sample.place', 'SAMPLE_OUT_OF_AREA', sample_id='grating', offset_x_m=0.02, offset_y_m=0.0)
        h.invoke('z_stage.move', z_m=h._top_z(h.samples['grating']) - rt.TIP_Z0 + 0.001)   # 1 mm above the top of the sample to come
        settle(h)
        rejected(h, 'sample.place', 'COLLISION_RISK', sample_id='grating', offset_x_m=0.0, offset_y_m=0.0)
        h.invoke('z_stage.move', z_m=0.005)
        settle(h)
        h.invoke('sample.place', sample_id='grating', offset_x_m=0.0, offset_y_m=0.0)
        rejected(h, 'sample.place', 'CHUCK_OCCUPIED', sample_id='mica', offset_x_m=0.0, offset_y_m=0.0)
        near(h, 50e-6)
        rejected(h, 'head.remove', 'COLLISION_RISK')
        rejected(h, 'stage.move_xy', 'COLLISION_RISK', x_m=0.009, y_m=0.0)                   # the sample edge would pass under the tip
        h.invoke('stage.move_xy', x_m=0.001, y_m=0.0)                                         # on the same sample
        with self.assertRaises(rt.ActionRejected):
            _orig_invoke(h, 'sample.provide', sample_id='huge', template_id='si_wafer', seed=1, tilt_rad=[0.0, 0.0], topography_overrides={'pitch_m': 1e-6})


class ApproachAndFeedback(unittest.TestCase):
    def test_approach_timing_final_extension_and_gap(self):
        h = mount(ready(), 'tap1')
        align(h)
        dynamic(h)
        h.invoke('sample.place', sample_id='grating', offset_x_m=0.0, offset_y_m=0.0)
        near(h, 300e-6)
        gap = h.read('evaluator')['truth.gap_m']
        self.assertAlmostEqual(gap, 300e-6, delta=0.2e-6)
        op = h.invoke('approach', setpoint_force_n=None, setpoint_amplitude_m=16e-9, z_servo_gain=1.0)['operation_id']
        self.assertEqual(h.read()['engage.state'], 'approaching')
        self.assertEqual(h.read()['zscanner.extension_m'], rt.Z_SCAN_RANGE_M)              # the scanner is extended during the descent
        dur = h.op['duration_s']
        self.assertAlmostEqual(dur, (gap - rt.Z_SCAN_RANGE_M + rt.Z_SCAN_RANGE_M / 2) / rt.APPROACH_SPEED_M_S + rt.APPROACH_SETTLE_S, delta=0.05)
        h.step(dur / 2)
        self.assertLess(h.read('evaluator')['truth.gap_m'], gap / 2 + 1e-6)
        wait(h, op)
        r = h.read('evaluator')
        self.assertEqual(r['engage.state'], 'engaged')
        self.assertAlmostEqual(r['zscanner.extension_m'], rt.Z_SCAN_RANGE_M * rt.APPROACH_TARGET_EXTENSION, delta=5e-9)
        self.assertGreater(r['truth.tip_force_n'], 0.0)
        rejected(h, 'stage.move_xy', 'ENGAGED', x_m=0.001, y_m=0.0)
        rejected(h, 'head.turn_knob', 'ENGAGED', knob='beam_x', turns=0.1)
        h.invoke('retract', distance_m=rt.RETRACT_DISTANCE_M)
        settle(h)
        self.assertIsNone(h.engaged)
        self.assertAlmostEqual(h.read('evaluator')['truth.gap_m'], rt.RETRACT_DISTANCE_M + (h._floor_under_tip() and 0.0) + 7.5e-6
                               - (h.engaged or {}).get('x', 0.0) + (r['truth.gap_m'] or 0.0), delta=3e-6)

    def test_contact_crash_and_dynamic_false_engage(self):
        h = mount(ready(), 'cont1')
        align(h)
        h.invoke('head.configure', mode='contact', cantilever_db='contal_g')
        h.invoke('head.turn_knob', knob='pspd_y', turns=2.5)
        h.invoke('sample.place', sample_id='sio2', offset_x_m=0.0, offset_y_m=0.0)
        near(h)
        op = h.invoke('approach', setpoint_force_n=5e-9, setpoint_amplitude_m=None, z_servo_gain=1.0)['operation_id']
        wait(h, op)
        self.assertEqual((h.operations[op]['phase'], h.operations[op]['reason']), ('failed', 'APPROACH_FAILED'))
        self.assertEqual(h._probe()['damage'], 'tip_broken')
        self.assertIsNone(h.engaged)
        settle(h)
        self.assertGreater(h.read('evaluator')['truth.gap_m'], 50e-6)                        # SmartScan lifts the head after the failure
        g = mount(ready(), 'tap1')
        align(g)
        dynamic(g)
        g.invoke('head.turn_knob', knob='pspd_y', turns=2.5)
        _, op = engage(g, GRATING)
        self.assertEqual(g.operations[op]['phase'], 'completed')
        self.assertTrue(g.engaged['false'])
        rec = scan(g)
        self.assertIn('false_engage', rec['warnings'])
        self.assertIn('error_saturated', rec['warnings'])
        self.assertLess(np.ptp(img(rec)), 1e-9)                                               # nothing imaged: the tip hovers far above

    def test_feedback_error_speed_gain_parachuting_and_oscillation(self):
        h = _scenario('engaged')
        err = {}
        for gain, rate in ((0.3, 2.0), (1.0, 2.0), (3.0, 2.0), (1.0, 0.5)):
            rec = scan(h, z_servo_gain=gain, rate_hz=rate, save_retrace=True)
            err[(gain, rate)] = float(np.std(img(rec, 'error')))
            if (gain, rate) == (0.3, 2.0):
                slow = rec
        self.assertGreater(err[(0.3, 2.0)], err[(1.0, 2.0)])
        self.assertGreater(err[(1.0, 2.0)], err[(3.0, 2.0)])
        self.assertGreater(err[(1.0, 2.0)], err[(1.0, 0.5)])
        self.assertIn('error_saturated', slow['warnings'])                                     # the tip parachutes off the pit walls at low gain
        t, r = img(slow, 'height', 'trace'), img(slow, 'height', 'retrace')
        self.assertGreater(float(np.std(t - r)), 3e-9)                                       # trace and retrace differ where the feedback lags
        m = _scenario('engaged')
        m.invoke('retract', distance_m=100e-6)
        settle(m)
        engage(m, MICA)
        below = scan(m, size_m=1e-6, z_servo_gain=3.0)
        above = scan(m, size_m=1e-6, z_servo_gain=8.0)
        self.assertGreater(float(np.std(img(above, 'error'))), 10 * float(np.std(img(below, 'error'))))   # above the critical gain 4 of tapping

    def test_z_range_saturation(self):
        h = _scenario('engaged')
        h.invoke('retract', distance_m=200e-6)
        settle(h)
        engage(h, TALL)
        rec = scan(h, size_m=40e-6, pixels_x=64, pixels_y=32, rate_hz=1.0)
        self.assertIn('z_scanner_saturated', rec['warnings'])
        z = img(rec)
        self.assertLessEqual(float(np.ptp(z)), rt.Z_SCAN_RANGE_M + 1e-9)

    def test_scan_limits(self):
        h = _scenario('engaged')
        rejected(h, 'scan.start', 'TIP_SPEED_TOO_HIGH', **dict(SCAN, size_m=50e-6, rate_hz=3.0))
        rejected(h, 'scan.start', 'SCAN_OUT_OF_RANGE', **dict(SCAN, size_m=40e-6, offset_x_m=10e-6, rate_hz=1.0))
        rejected(h, 'scan.start', 'SCAN_OUT_OF_RANGE', **dict(SCAN, size_m=40e-6, angle_rad=math.pi / 4, rate_hz=1.0))
        rejected(h, 'scan.start', 'SIMULATION_LIMIT', **dict(SCAN, pixels_x=4096, pixels_y=2048, rate_hz=0.1))
        rejected(h, 'scan.start', 'SETPOINT_REQUIRED', **dict(SCAN, setpoint_force_n=5e-9, setpoint_amplitude_m=None))
        rejected(h, 'scan.start', 'SETPOINT_OUT_OF_RANGE', **dict(SCAN, setpoint_amplitude_m=25e-9))


# ---------------------------------------------------------------- physics
class ImagingPhysics(unittest.TestCase):
    def test_tip_dilation_of_a_sphere_matches_the_analytic_envelope(self):
        """A sphere of radius r imaged by a spherical apex R: apparent surface = sphere of radius r + R about the particle centre (apex contour)."""
        r, R, px, m = 10e-9, 8e-9, 1e-9, 20
        n = 61 + 2 * m
        x = (np.arange(n) - n // 2) * px
        X, Y = np.meshgrid(x, x)
        g = np.where(X ** 2 + Y ** 2 < r * r, r + np.sqrt(np.maximum(r * r - X ** 2 - Y ** 2, 0.0)), 0.0)
        alpha = math.radians(20.0)
        hd = rt.dilate_grid(g, px, px, R, alpha, m)
        xi = x[m:-m]
        env = np.where(np.abs(xi) < r + R, r + np.sqrt(np.maximum((r + R) ** 2 - xi ** 2, 0.0)) - R, 0.0)
        line = hd[hd.shape[0] // 2]
        core = np.abs(xi) < 0.95 * (r + R) * math.cos(alpha)                                   # contact on the spherical cap of the tip
        self.assertLess(float(np.max(np.abs(line[core] - np.maximum(env[core], 0.0)))), 0.6e-9)
        self.assertAlmostEqual(float(line.max()), 2 * r, delta=1e-12)
        half = xi[line > r]
        self.assertAlmostEqual(float(half.max() - half.min()), 2 * math.sqrt((r + R) ** 2 - R * R), delta=2 * px)

    def test_calibration_grating_step_height_and_pitch(self):
        h = _scenario('engaged')
        rec = scan(h, size_m=10e-6, pixels_x=128, pixels_y=128, rate_hz=1.0)
        st = h.invoke('analysis.step_height', record_id=rec['record_id'], channel='height', direction='trace', flatten='plane_upper', method='histogram',
                      region_a_px=None, region_b_px=None)
        self.assertAlmostEqual(st['step_m'], 100e-9, delta=7e-9)                               # manual: 100 nm +- 7 nm
        z = img(rec, flatten='plane_upper')
        prof = z.mean(axis=0) - z.mean()
        spec = np.abs(np.fft.rfft(prof))
        k = int(np.argmax(spec[1:])) + 1
        self.assertAlmostEqual(10e-6 / k, 3e-6, delta=0.4e-6)                                 # 3 um pitch
        line_biased = h.invoke('analysis.step_height', record_id=rec['record_id'], channel='height', direction='trace', flatten='line_1', method='histogram',
                               region_a_px=None, region_b_px=None)
        self.assertGreater(abs(line_biased['step_m'] - 100e-9), 20e-9)                         # line flattening through the pits biases it (as in XEI)

    def test_hopg_monatomic_step(self):
        """Step height from two narrow regions on either side of each step edge after levelling on one terrace (as in XEI): the interlayer spacing 0.335 nm."""
        h = tapping_engaged(sample=HOPG)
        rec = scan(h, size_m=1.2e-6, pixels_x=128, pixels_y=128, rate_hz=1.0)
        z = img(rec)
        s = copy.deepcopy(h._sample())
        s['tilt_rad'] = [0.0, 0.0]
        s['topography'] = dict(s['topography'], rq_m=0.0)
        ideal = rt.Surface(s)
        n = 128
        u = (np.arange(n) + 0.5) * 1.2e-6 / n - 0.6e-6
        U, V = np.meshgrid(u, u)
        p0 = h._tip_in_sample()
        dr = h._drift(h.records[rec['record_id']]['started_s'])
        lev = np.round(ideal.evaluate(p0[0] + U + dr[0], p0[1] + V + dr[1])[0] / 0.335e-9)
        z = z - rt._plane_fit(z, lev == np.median(lev))                                          # level the image on one terrace (plane by region)
        steps = []
        for j in range(n):
            for i in np.nonzero(np.diff(lev[j]))[0]:
                if 12 <= i < n - 12 and len(set(lev[j, i - 11:i + 13].tolist())) == 2:
                    steps.append((np.median(z[j, i + 3:i + 11]) - np.median(z[j, i - 9:i - 1])) / (lev[j, i + 1] - lev[j, i]))
        self.assertGreater(len(steps), 20)
        self.assertAlmostEqual(float(np.median(steps)), 0.335e-9, delta=0.04e-9)
        terrace = z[lev == np.median(lev)]
        self.assertLess(float(np.std(rt.flatten_image(z, 'plane')[lev == np.median(lev)])), 0.12e-9)
        self.assertGreater(terrace.size, 100)


    def test_phase_contrast_and_height_of_a_ps_pmma_blend(self):
        h = tapping_engaged(sample=BLEND)
        rec = scan(h, size_m=10e-6, pixels_x=128, pixels_y=128, rate_hz=0.5)
        H, P = img(rec, flatten='plane'), img(rec, 'phase')
        s = h._sample()['surface']
        n = 128
        u = (np.arange(n) + 0.5) * 10e-6 / n - 5e-6
        U, V = np.meshgrid(u, u)
        p0 = h._tip_in_sample()
        dr = h._drift(h.records[rec['record_id']]['started_s'])
        _, mat = s.evaluate(p0[0] + U + dr[0], p0[1] + V + dr[1])
        dh = float(np.median(H[mat == 0]) - np.median(H[mat == 1]))
        dphi = float(np.median(P[mat == 1]) - np.median(P[mat == 0]))
        self.assertAlmostEqual(dh, 12e-9, delta=2e-9)                                          # PMMA domains 12 nm lower (template)
        self.assertGreater(abs(dphi), math.radians(0.5))                                       # dissipation contrast of PS / PMMA
        mp = h.engaged['mp']
        self.assertGreater(mp['mats'][1]['E_dis'], mp['mats'][0]['E_dis'])                     # PMMA: larger surface energy -> adhesion hysteresis
        self.assertGreater(dphi, 0.0)
        self.assertTrue(np.all(P < math.pi / 2 + 1e-3))                                        # repulsive regime: phase lag below 90 deg

    def test_true_non_contact_operating_point_and_drive_side(self):
        nc = _scenario('nc_engaged')
        mp = nc.engaged['mp']
        self.assertEqual(mp['regime'], 'non_contact')
        gap = mp['mats'][0]['gap']
        self.assertTrue(0.3e-9 < gap < 5e-9, gap)
        self.assertLess(nc.read('evaluator')['truth.tip_force_n'], 2e-9)                       # attractive van der Waals force, below 2 nN
        rec = scan(nc, size_m=2e-6, setpoint_amplitude_m=9.5e-9)
        self.assertTrue(np.all(img(rec, 'phase') > math.pi / 2))                               # attractive regime: phase lag above 90 deg
        self.assertNotIn('nc_contact_regime', rec['warnings'])
        r0 = nc._probe()['radius']
        for _ in range(3):
            scan(nc, size_m=2e-6, setpoint_amplitude_m=9.5e-9)
        self.assertLess(nc._probe()['radius'] - r0, 0.2e-9)                                    # practically no wear
        # the drive below the resonance: the attraction raises the amplitude first, the operating point does not exist
        h = mount(ready(), 'nc1')
        align(h)
        dynamic(h, mode='non_contact', amplitude=10e-9, above=0.9992)
        engage(h, SIO2, setpoint=9.5e-9)
        self.assertEqual(h.engaged['mp']['regime'], 'contact_regime')
        self.assertIn('NC_CONTACT_REGIME', [e['code'] for e in h.events])
        rec = scan(h, size_m=2e-6, setpoint_amplitude_m=9.5e-9)
        self.assertIn('nc_contact_regime', rec['warnings'])
        # table physics: frequency shift of small amplitudes is -f0 F' / 2k
        k, f0, C, D = 40.0, 3e5, 1e-28, 5e-9
        self.assertAlmostEqual(float(rt.nc_shift(np.array([D]), np.array([0.01e-9]), k, f0, C)[0]), -f0 * (2 * C / (D + 0.01e-9) ** 3) / (2 * k), delta=1e-3 * abs(f0 * 2 * C / D ** 3 / (2 * k)))

    def test_contact_mode_indentation_and_tip_wear(self):
        h = contact_engaged(sample=SIO2, setpoint=10e-9)
        r0 = h._probe()['radius']
        for _ in range(2):
            scan(h, size_m=10e-6, rate_hz=2.0, setpoint_force_n=10e-9)
        worn_hard = h._probe()['radius'] - r0
        p = contact_engaged(sample=PMMA, setpoint=10e-9)
        r1 = p._probe()['radius']
        for _ in range(2):
            scan(p, size_m=10e-6, rate_hz=2.0, setpoint_force_n=10e-9)
        worn_soft = p._probe()['radius'] - r1
        self.assertGreater(worn_hard, 1e-9)
        self.assertGreater(worn_hard, 1.5 * worn_soft)                                         # Archard-type wear scales with the sample hardness
        t = _scenario('engaged')
        tr0 = t._probe()['radius']
        for _ in range(2):
            scan(t, size_m=10e-6, rate_hz=2.0)
        self.assertLess((t._probe()['radius'] - tr0) / 11.4e-9, worn_hard / r0)                # tapping wears less than contact
        # the load indents the softer film more (DMT), visible in the true force and offsets
        mh, ms = h.engaged['mp'], p.engaged['mp']
        self.assertGreater(-ms['mats'][0]['offset'] - ms['d_set'], -mh['mats'][0]['offset'] - mh['d_set'])
        # the feedback holds VERTICAL = V_free + dV on the erf response of the photodiode (F07): true load = k x deflection at that signal
        o = h._optics()
        dV = 10e-9 * h.cal['sensitivity_v_per_m'] / h.cal['spring_constant_n_m']
        V_free = float(rt.pspd_signal(o['I'], o['p_v'], o['S'], 0.0))
        d = rt.pspd_inverse(o['I'], o['p_v'], o['S'], V_free + dV)
        self.assertAlmostEqual(h.read('evaluator')['truth.tip_force_n'], h._probe()['truth']['k'] * d, delta=1e-15)
        self.assertAlmostEqual(h.read()['pspd.vertical_v'], V_free + dV, delta=1e-9)

    def test_noise_floor_with_enclosure_isolation_and_bursts(self):
        rq = {}
        for enc in (True, False):
            h = tapping_engaged(sample=MICA, acoustic_enclosure=enc, vibration_isolation=enc)
            rec = scan(h, size_m=500e-9, pixels_x=128, pixels_y=128, rate_hz=2.0)
            rq[enc] = float(np.std(img(rec, flatten='line_1')))
        self.assertLess(rq[True], 0.05e-9)                                                     # mica about 0.03 nm with enclosure and isolation
        self.assertGreater(rq[False], 1.4 * rq[True])
        h.invoke('fault.inject', fault='vibration_burst', active=True)
        rec = scan(h, size_m=500e-9, pixels_x=128, pixels_y=128, rate_hz=2.0)
        self.assertGreater(float(np.std(img(rec, flatten='line_1'))), 5 * rq[False])
        self.assertGreaterEqual(rt.Z_DETECTOR_NOISE_M, 0.02e-9)

    def test_thermal_drift_settles_after_loading(self):
        h = tapping_engaged(sample=AUNP, acoustic_enclosure=False)
        def shift(a, b):
            A, B = np.fft.fft2(a - a.mean()), np.fft.fft2(b - b.mean())
            c = np.fft.ifft2(A * np.conj(B)).real
            j, i = np.unravel_index(np.argmax(c), c.shape)
            n = a.shape[0]
            return math.hypot((i + n // 2) % n - n // 2, (j + n // 2) % n - n // 2)
        early = [img(scan(h, size_m=500e-9, pixels_x=128, pixels_y=128, rate_hz=1.0), flatten='line_1') for _ in range(2)]
        d0 = h.read('evaluator')['truth.drift_m']
        h.step(4 * 3600.0)
        late = [img(scan(h, size_m=500e-9, pixels_x=128, pixels_y=128, rate_hz=1.0), flatten='line_1') for _ in range(2)]
        self.assertGreaterEqual(shift(early[1], early[0]), 1.0)                                # nm per minute right after loading
        self.assertLess(shift(late[1], late[0]), shift(early[1], early[0]))
        d1 = h.read('evaluator')['truth.drift_m']
        self.assertGreater(math.hypot(d1[0] - d0[0], d1[1] - d0[1]), 0.0)


class SpectroscopyAndCalibration(unittest.TestCase):
    def test_thermal_tune_recovers_k_and_propagates_the_sensitivity_error(self):
        h = mount(ready(), 'cont1')
        align(h)
        h.invoke('head.configure', mode='contact', cantilever_db='contal_g')
        tr = h._probe()['truth']
        S = h._optics()['S']
        h.invoke('calibration.set', spring_constant_n_m=None, sensitivity_v_per_m=S)
        rec = wait(h, h.invoke('calibration.thermal_tune', temperature_k=295.15, averages=200)['operation_id'])
        fit = rec['data']['fit']
        self.assertAlmostEqual(fit['spring_constant_n_m'] / tr['k'], 1.0, delta=0.05)
        self.assertAlmostEqual(fit['resonance_hz'] / tr['f0'], 1.0, delta=0.002)
        self.assertAlmostEqual(fit['quality_factor'] / tr['Q'], 1.0, delta=0.15)
        self.assertEqual(h.cal['spring_constant_source'], 'thermal_tune')
        h.invoke('calibration.set', spring_constant_n_m=None, sensitivity_v_per_m=1.2 * S)
        k2 = wait(h, h.invoke('calibration.thermal_tune', temperature_k=295.15, averages=200)['operation_id'])['data']['fit']['spring_constant_n_m']
        self.assertAlmostEqual(k2 / fit['spring_constant_n_m'], 1.44, delta=0.08)             # k ~ S^2
        h.invoke('calibration.set', spring_constant_n_m=None, sensitivity_v_per_m=S)
        k3 = wait(h, h.invoke('calibration.thermal_tune', temperature_k=320.0, averages=200)['operation_id'])['data']['fit']['spring_constant_n_m']
        self.assertAlmostEqual(k3 / fit['spring_constant_n_m'], 320.0 / 295.15, delta=0.06)                # the user-entered temperature scales k
        # a wrong cantilever entry puts the resonance outside the window
        h.invoke('head.configure', mode='contact', cantilever_db='ac160ts')
        rec = wait(h, h.invoke('calibration.thermal_tune', temperature_k=295.15, averages=50)['operation_id'])
        self.assertIn('no_resonance_in_window', rec['warnings'])

    def test_sensitivity_from_force_curves_and_the_pull_off(self):
        h = mount(ready(humidity=0.40), 'cont1')
        align(h)
        h.invoke('head.configure', mode='contact', cantilever_db='contal_g')
        set_true_calibration(h)
        engage(h, SIO2, mode='contact', setpoint=2e-9)
        fd = wait(h, h.invoke('fd.acquire', z_range_m=200e-9, rate_hz=1.0, trigger_force_n=4e-9, dwell_s=0.0, count=2, positions_m=[[0.0, 0.0]])['operation_id'])
        r = h.invoke('calibration.sensitivity', record_id=fd['record_id'], curve_index=0, branch='approach')
        self.assertAlmostEqual(r['sensitivity_v_per_m'] / h._optics()['S'], 1.0, delta=0.02)   # hard sample, soft lever, small deflection
        self.assertGreater(r['r2'], 0.999)
        c = fd['data']['curves'][0]
        self.assertAlmostEqual(-min(c['force_retract_n']) / c['_truth']['adhesion_n'], 1.0, delta=0.08)
        self.assertLess(min(c['force_approach_n']), 0.0)                                       # jump-in to contact
        self.assertTrue(c['triggered'])
        # more humidity: capillary condensation adds adhesion
        w = mount(ready(humidity=0.70), 'cont1')
        align(w)
        w.invoke('head.configure', mode='contact', cantilever_db='contal_g')
        set_true_calibration(w)
        engage(w, SIO2, mode='contact', setpoint=2e-9)
        fdw = wait(w, w.invoke('fd.acquire', z_range_m=300e-9, rate_hz=1.0, trigger_force_n=4e-9, dwell_s=0.0, count=1, positions_m=[[0.0, 0.0]])['operation_id'])
        self.assertGreater(-min(fdw['data']['curves'][0]['force_retract_n']), 1.3 * -min(c['force_retract_n']))
        # analytic check of the branches: pull-off at z_rel = F_ad / k, jump-in at 1.5 (2C / k)^(1/3)
        k, R, Es, Fad, C = 0.2, 10e-9, 50e9, 5e-9, 1e-19 * 10e-9 / 6
        z = np.linspace(60e-9, -30e-9, 4000)
        d_app, d_ret, info = rt.fd_branches(z, k, R, Es, Fad, C)
        self.assertAlmostEqual(info['pull_off_z_m'], Fad / k, delta=1e-15)
        self.assertAlmostEqual(float(np.min(k * d_ret)), -Fad, delta=0.02 * Fad)
        self.assertAlmostEqual(info['jump_in_z_m'], 1.5 * (2 * C / k) ** (1 / 3), delta=1e-15)

    def test_dmt_fit_recovers_the_modulus_of_a_polymer_film(self):
        h = mount(ready(), 'multi1')
        align(h)
        h.invoke('head.configure', mode='contact', cantilever_db='multi75al_g')
        set_true_calibration(h)
        engage(h, PMMA, mode='contact', setpoint=2e-9)
        fd = wait(h, h.invoke('fd.acquire', z_range_m=80e-9, rate_hz=1.0, trigger_force_n=60e-9, dwell_s=0.0, count=1, positions_m=[[0.0, 0.0]])['operation_id'])
        c = fd['data']['curves'][0]
        m = rt.material('pmma')
        fit = h.invoke('analysis.fd_fit', record_id=fd['record_id'], curve_index=0, branch='retract', model='dmt', tip_radius_m=c['_truth']['tip_radius_m'],
                       sample_poisson_ratio=m['nu'], tip_modulus_pa=169e9, tip_poisson_ratio=0.27)
        self.assertAlmostEqual(fit['reduced_modulus_pa'] / c['_truth']['reduced_modulus_pa'], 1.0, delta=0.25)
        self.assertAlmostEqual(fit['sample_modulus_pa'] / m['E'], 1.0, delta=0.3)
        self.assertAlmostEqual(fit['adhesion_n'] / c['_truth']['adhesion_n'], 1.0, delta=0.1)
        hz = h.invoke('analysis.fd_fit', record_id=fd['record_id'], curve_index=0, branch='approach', model='hertz', tip_radius_m=c['_truth']['tip_radius_m'],
                      sample_poisson_ratio=m['nu'], tip_modulus_pa=169e9, tip_poisson_ratio=0.27)
        self.assertGreater(hz['reduced_modulus_pa'], 0.0)
        rejected(h, 'analysis.fd_fit', 'UNKNOWN_CURVE', record_id=fd['record_id'], curve_index=5, branch='retract', model='dmt', tip_radius_m=10e-9,
                 sample_poisson_ratio=0.35, tip_modulus_pa=169e9, tip_poisson_ratio=0.27)

    def test_frequency_sweep_and_drive(self):
        h = mount(ready(), 'tap1')
        align(h)
        rejected(h, 'ncm.frequency_sweep', 'MODE_NOT_CONFIGURED', start_hz=250e3, stop_hz=330e3, points=512)
        h.invoke('head.configure', mode='contact', cantilever_db='ac160ts')
        rejected(h, 'ncm.set_drive', 'MODE_NOT_DYNAMIC', frequency_hz=3e5, amplitude_m=20e-9)
        h.invoke('head.configure', mode='tapping', cantilever_db='ac160ts')
        tr = h._probe()['truth']
        rec = wait(h, h.invoke('ncm.frequency_sweep', start_hz=tr['f0'] * 0.98, stop_hz=tr['f0'] * 1.02, points=2048)['operation_id'])
        self.assertAlmostEqual(rec['data']['peak_hz'] / tr['f0'], 1.0, delta=1e-4)
        self.assertAlmostEqual(rec['data']['quality_factor_estimate'] / tr['Q'], 1.0, delta=0.15)
        ph = np.asarray(rec['data']['phase_rad'])
        self.assertLess(ph[0], 0.3)
        self.assertGreater(ph[-1], math.pi - 0.3)
        rejected(h, 'ncm.set_drive', 'DRIVE_OFF_RESONANCE', frequency_hz=tr['f0'] * 1.5, amplitude_m=50e-9)
        h.invoke('ncm.set_drive', frequency_hz=tr['f0'], amplitude_m=20e-9)
        self.assertAlmostEqual(h.read()['ncm.amplitude_m'], 20e-9, delta=1e-12)
        h.invoke('calibration.set', spring_constant_n_m=None, sensitivity_v_per_m=2 * h.cal['sensitivity_v_per_m'])
        self.assertAlmostEqual(h.read()['ncm.amplitude_m'], 10e-9, delta=1e-12)              # the displayed amplitude follows the calibration


# ---------------------------------------------------------------- faults, cancellation, data
class FaultsCancelAndData(unittest.TestCase):
    def test_sld_failure_during_a_scan_keeps_the_scanned_lines(self):
        h = _scenario('engaged')
        op = h.invoke('scan.start', **SCAN)['operation_id']
        h.step(9.0)
        h.invoke('fault.inject', fault='sld_failure', active=True)
        rec = h.records['IMG0001']
        self.assertEqual((h.operations[op]['phase'], h.operations[op]['reason'], rec['status']), ('failed', 'SLD_FAILURE', 'failed'))
        self.assertEqual(rec['meta']['lines_completed'], 18)                                   # 9 s at 2 Hz, including the partial block
        self.assertTrue(np.isnan(np.asarray(rec['data']['height']['trace'])[18:]).all())
        self.assertIsNone(h.engaged)
        self.assertEqual(h.error, 'SLD_FAILURE')
        rejected(h, 'system.acknowledge', 'CAUSE_PERSISTS')
        rejected(h, 'z_stage.move', 'ERROR_PENDING', z_m=0.001)
        h.invoke('fault.inject', fault='sld_failure', active=False)
        self.assertEqual(h.invoke('system.acknowledge')['cleared'], 'SLD_FAILURE')
        self.assertTrue(h.read()['beam.emitting'])

    def test_cancel_keeps_lines_and_the_engagement(self):
        h = _scenario('engaged')
        h.register_principal('a', 'agent')
        s = h.session('a')
        r = s.invoke(instance_id=h.instance_id, capability_id='scan.start', arguments=SCAN, request_id='s1')
        h.step(20.25)
        c = s.cancel(r['operation_id'], 'c1')
        self.assertEqual(c['result']['phase'], 'cancelled')
        self.assertEqual(s.cancel(r['operation_id'], 'c1'), c)                                 # idempotent
        c2 = s.cancel(r['operation_id'], 'c2')                                                  # a refused cancel answers like invoke (F11)
        self.assertEqual((c2['status'], c2['error']['code'], c2['error']['side_effects']), ('rejected', 'NOT_RUNNING', 'none'))
        self.assertEqual(s.cancel(r['operation_id'], 'c2'), c2)
        rec = h.records['IMG0001']
        self.assertEqual((rec['status'], rec['meta']['lines_completed']), ('cancelled', 40))
        self.assertIsNotNone(h.engaged)
        # cancelling an approach stops the Z stage where it is
        a = _scenario('near')
        op = a.invoke('approach', setpoint_force_n=None, setpoint_amplitude_m=16e-9, z_servo_gain=1.0)['operation_id']
        a.step(0.7)
        q = a.qz
        a._cancel_op(a.operations[op])
        a.step(5.0)
        self.assertEqual(a.qz, q)
        self.assertIsNone(a.engaged)
        # a cancelled thermal tune leaves the calibration unchanged
        t = _scenario('tuning')
        cal = dict(t.cal)
        t._cancel_op(t.operations[t.op['op']])
        self.assertEqual(t.cal, cal)
        self.assertEqual(t.records['TT0001']['data'], {})

    def test_stage_stalls_and_the_broken_tip(self):
        h = _scenario('aligned')
        h.invoke('fault.inject', fault='xy_stage_stall', active=True)
        h.invoke('stage.move_xy', x_m=0.003, y_m=0.0)
        self.assertEqual((h.error, h.xy), ('XY_STAGE_FAULT', [0.0, 0.0]))
        h.invoke('fault.inject', fault='xy_stage_stall', active=False)
        h.invoke('system.acknowledge')
        z = _scenario('near')
        op = z.invoke('approach', setpoint_force_n=None, setpoint_amplitude_m=16e-9, z_servo_gain=1.0)['operation_id']
        z.step(0.5)
        z.invoke('fault.inject', fault='z_stage_stall', active=True)
        self.assertEqual((z.operations[op]['phase'], z.operations[op]['reason'], z.error), ('failed', 'Z_STAGE_FAULT', 'Z_STAGE_FAULT'))
        # a broken tip (evaluator) blurs the image of the gold particles
        b = tapping_engaged(sample=AUNP)
        sharp = img(scan(b, size_m=500e-9, pixels_x=128, pixels_y=128, rate_hz=1.0))
        b.invoke('fault.inject', fault='tip_broken', active=True)
        self.assertEqual(b._probe()['damage'], 'tip_broken')
        blunt = img(scan(b, size_m=500e-9, pixels_x=128, pixels_y=128, rate_hz=1.0))
        above = [float(np.mean(z - np.percentile(z, 10, axis=1, keepdims=True) > 5e-9)) for z in (sharp, blunt)]   # substrate level per line (drift)
        self.assertGreater(above[1], 3.0 * above[0])                                               # the 20 nm particles look much wider

    def test_records_delete_full_and_result_extraction(self):
        h = _scenario('scanned')
        r = h.invoke('measurement.result', record_id='IMG0001', channel='phase', direction='trace', region_px=[8, 8, 40, 24], max_values=128)
        self.assertEqual(r['data']['unit'], 'rad')
        self.assertLessEqual(r['data']['shape'][0] * r['data']['shape'][1], 128)
        self.assertEqual(r['data']['factor'], 2)
        rejected(h, 'measurement.result', 'CHANNEL_NOT_RECORDED', record_id='IMG0001', channel='height', direction='retrace', region_px=None, max_values=128)
        rejected(h, 'measurement.result', 'REGION_OUT_OF_RANGE', record_id='IMG0001', channel='height', direction='trace', region_px=[0, 0, 65, 10], max_values=128)
        f = h.invoke('analysis.flatten', record_id='IMG0001', channel='height', direction='trace', method='plane_upper')
        pr = h.invoke('measurement.result', record_id=f['record_id'], channel=None, direction=None, region_px=None, max_values=65536)
        self.assertEqual(pr['record']['kind'], 'processed')
        self.assertAlmostEqual(pr['data']['stats']['mean'], float(np.mean(rt.flatten_image(img(h.records['IMG0001']), 'plane_upper'))), delta=1e-12)
        self.assertEqual(h.invoke('records.delete', record_id=f['record_id'])['remaining'], len(h.records))
        rejected(h, 'records.delete', 'UNKNOWN_RECORD', record_id=f['record_id'])
        h.records.update({f'X{i:04d}': dict(h.records['IMG0001'], record_id=f'X{i:04d}') for i in range(rt.MAX_RECORDS - len(h.records))})
        rejected(h, 'analysis.flatten', 'RECORDS_FULL', record_id='IMG0001', channel='height', direction='trace', method='plane')
        rejected(h, 'scan.start', 'RECORDS_FULL', **SCAN)


class AnalysisFunctions(unittest.TestCase):
    def test_flatten_roughness_profile_and_regions(self):
        yy, xx = np.mgrid[0:64, 0:64]
        rng = np.random.default_rng(1)
        noise = rng.standard_normal((64, 64)) * 1e-9
        z = 3e-9 + 2e-10 * xx - 1e-10 * yy + noise
        f = rt.flatten_image(z, 'plane')
        self.assertLess(abs(float(np.polyfit(np.arange(64), f.mean(axis=0), 1)[0])), 1e-12)
        st = rt.roughness_stats(f)
        self.assertAlmostEqual(st['rq_m'], float(np.std(noise)), delta=0.03e-9)
        self.assertAlmostEqual(st['ra_m'], st['rq_m'] * math.sqrt(2 / math.pi), delta=0.05e-9)   # Gaussian surface: Ra = Rq sqrt(2 / pi)
        self.assertAlmostEqual(st['rku'], 3.0, delta=0.3)
        self.assertAlmostEqual(st['rsk'], 0.0, delta=0.2)
        for order in (0, 1, 2):
            ff = rt.flatten_image(z + 5e-9 * ((xx / 63.0) * 2 - 1) ** order, f'line_{order}')
            self.assertLess(float(np.max(np.abs(ff.mean(axis=1)))), 1e-12)
        p = rt.line_profile(np.tile(np.arange(10.0), (5, 1)), (0, 2), (9, 2), 19)
        self.assertTrue(np.allclose(p, np.linspace(0, 9, 19)))
        two = np.where(xx < 32, 0.0, 7e-9) + noise * 0.1
        self.assertAlmostEqual(rt.step_height_histogram(two)['step_m'], 7e-9, delta=0.1e-9)
        small, fac = rt.downsample(np.arange(100.0).reshape(10, 10), 25)
        self.assertEqual((small.shape, fac), ((5, 5), 2))
        self.assertAlmostEqual(small[0, 0], np.mean([0, 1, 10, 11]))

    def test_image_analysis_capabilities(self):
        h = _scenario('scanned')
        rr = h.invoke('analysis.roughness', record_id='IMG0001', channel='height', direction='trace', flatten='plane', region_px=[0, 0, 32, 32])
        self.assertEqual(rr['pixels'], 32 * 32)
        lp = h.invoke('analysis.line_profile', record_id='IMG0001', channel='height', direction='trace', flatten='plane', start_px=[0, 10], end_px=[63, 10],
                      points=64)
        self.assertAlmostEqual(lp['distance_m'][-1], 63 * 5e-6 / 64, delta=1e-15)
        rg = h.invoke('analysis.step_height', record_id='IMG0001', channel='height', direction='trace', flatten='plane', method='regions', region_a_px=[0, 0, 4, 4],
                      region_b_px=[20, 20, 30, 30])
        self.assertEqual(rg['method'], 'regions')
        rejected(h, 'analysis.step_height', 'REGION_REQUIRED', record_id='IMG0001', channel='height', direction='trace', flatten='plane', method='regions',
                 region_a_px=None, region_b_px=None)
        rejected(h, 'analysis.roughness', 'REGION_OUT_OF_RANGE', record_id='IMG0001', channel='height', direction='trace', flatten='plane', region_px=[0, 0, 1, 1])
        rejected(h, 'analysis.roughness', 'RECORD_NOT_SUITABLE', record_id='FS0001', channel='height', direction='trace', flatten='plane', region_px=None)


# ---------------------------------------------------------------- ledgers, isolation, sessions
class LedgersIsolationSessions(unittest.TestCase):
    def test_energy_travel_and_count_ledgers_close(self):
        h = configured()
        h.invoke('power.set', enabled=True)
        h.step(100.0)
        mount(h, 'tap1')
        align(h)
        dynamic(h)
        engage(h)
        scan(h, pixels_y=32)
        led = h.ledger()
        self.assertAlmostEqual(led['electrical_energy_j'], rt.ELECTRONICS_POWER_W * h.t, delta=1e-6)
        self.assertAlmostEqual(led['scan_lines'], 32.0)
        self.assertAlmostEqual(led['scanner_path_m'], 32 * 2 * SCAN['size_m'], delta=1e-15)
        self.assertEqual(led['approaches'], 1.0)
        z_moves = h.led['z_stage_travel_m'].value()
        self.assertGreater(z_moves, 0.005)
        h.invoke('power.set', enabled=False)
        e0, t0 = h.ledger()['electrical_energy_j'], h.t
        h.step(50.0)
        self.assertEqual(h.ledger()['electrical_energy_j'], e0)                                 # no energy while off
        self.assertEqual(h.ledger()['beam_on_s'], led['beam_on_s'] + 0.0 * (h.t - t0))

    def test_two_instances_are_isolated(self):
        a, b = _scenario('engaged'), _scenario('engaged')
        a.invoke('fault.inject', fault='vibration_burst', active=True)
        scan(a)
        self.assertNotIn('IMG0001', b.records)
        self.assertEqual(b.faults, set())
        self.assertNotEqual(a.ledger()['scan_lines'], b.ledger()['scan_lines'])

    def test_agent_does_not_see_truth_templates_or_evaluator_warnings(self):
        h = _scenario('near')
        h.register_principal('agent', 'agent')
        h.register_principal('eval', 'evaluator')
        s, e = h.session('agent'), h.session('eval')
        iid = h.instance_id
        obs = s.read(instance_id=iid)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        with self.assertRaises(rt.ActionRejected):
            s.read(instance_id=iid, state_ids=['truth.probe'])
        self.assertEqual(s.invoke(instance_id=iid, capability_id='fault.inject', arguments=dict(fault='tip_broken', active=True), request_id='f1')['error']['code'],
                         'ACCESS_DENIED')
        d = s.describe(instance_id=iid)
        self.assertNotIn('fault.inject', d['capabilities'])
        self.assertFalse([k for k in d['state_slots'] if k.startswith('truth.')])
        h.invoke('head.turn_knob', knob='pspd_y', turns=2.5)
        r = s.invoke(instance_id=iid, capability_id='approach', arguments=dict(setpoint_force_n=None, setpoint_amplitude_m=16e-9, z_servo_gain=1.0), request_id='a1')
        wait(h, r['operation_id'])
        r = s.invoke(instance_id=iid, capability_id='scan.start', arguments=SCAN, request_id='s1')
        wait(h, r['operation_id'])
        mine = s.invoke(instance_id=iid, capability_id='measurement.result', arguments=dict(record_id='IMG0001', channel=None, direction=None, region_px=None,
                                                                                               max_values=256), request_id='m1')['result']
        full = e.invoke(instance_id=iid, capability_id='measurement.result', arguments=dict(record_id='IMG0001', channel=None, direction=None, region_px=None,
                                                                                               max_values=256), request_id='m2')['result']
        self.assertIn('false_engage', full['record']['warnings'])
        self.assertNotIn('false_engage', mine['record']['warnings'])
        self.assertEqual(mine['record']['template_source'], dict(library='sample_library.json', template_id=None, substances=[]))
        self.assertEqual(full['record']['template_source']['template_id'], 'calibration_grating_xyz')
        self.assertNotIn('false_engage', s.poll(r['operation_id'])['result']['warnings'])
        ev = s.invoke(instance_id=iid, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e1')['result']['events']
        self.assertFalse([x for x in ev if x['kind'] == 'fault'])
        self.assertTrue([x for x in e.invoke(instance_id=iid, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e2')['result']['events']
                         if x['code'] == 'FALSE_ENGAGE'])

    def test_records_and_control_belong_to_their_agent(self):
        h = _scenario('engaged')
        for n in ('a', 'b'):
            h.register_principal(n, 'agent')
        a, b = h.session('a'), h.session('b')
        iid = h.instance_id
        r = a.invoke(instance_id=iid, capability_id='scan.start', arguments=SCAN, request_id='s1')
        self.assertEqual(b.invoke(instance_id=iid, capability_id='retract', arguments=dict(distance_m=1e-4), request_id='x')['error']['code'], 'ACCESS_DENIED')
        self.assertTrue(b.read(instance_id=iid)['observations']['operation.started_by_other_agent']['value'])
        self.assertIsNone(b.read(instance_id=iid)['observations']['operation.id']['value'])
        with self.assertRaises(rt.ActionRejected):
            b.poll(r['operation_id'])
        wait(h, r['operation_id'])
        self.assertEqual(b.invoke(instance_id=iid, capability_id='measurement.result', arguments=dict(record_id='IMG0001', channel=None, direction=None, region_px=None,
                                                                                                        max_values=256), request_id='r')['error']['code'], 'ACCESS_DENIED')
        self.assertNotIn('IMG0001', b.invoke(instance_id=iid, capability_id='records.list', arguments={}, request_id='l')['result']['records'])
        self.assertEqual(b.invoke(instance_id=iid, capability_id='records.delete', arguments=dict(record_id='IMG0001'), request_id='d')['error']['code'], 'ACCESS_DENIED')
        self.assertIsNone(b.read(instance_id=iid)['observations']['result.last_record_id']['value'] if 'FS0002' not in h.records else None)
        ok = a.invoke(instance_id=iid, capability_id='analysis.flatten', arguments=dict(record_id='IMG0001', channel='height', direction='trace', method='plane'),
                      request_id='f')
        self.assertEqual(h._owners[ok['result']['record_id']], 'a')
        again = a.invoke(instance_id=iid, capability_id='analysis.flatten', arguments=dict(record_id='IMG0001', channel='height', direction='trace', method='plane'),
                         request_id='f')
        self.assertEqual(again, ok)                                                             # same request id: the same response, no new record
        self.assertEqual(a.invoke(instance_id=iid, capability_id='analysis.flatten', arguments=dict(record_id='IMG0001', channel='phase', direction='trace',
                                                                                                    method='plane'), request_id='f')['error']['code'], 'REQUEST_CONFLICT')

    def test_queries_and_bookkeeping(self):
        h = _scenario('fd_done')
        led = h.invoke('ledger.read')
        self.assertEqual(led, h.ledger())
        self.assertEqual(led['force_curves'], 2.0)
        prov = h.invoke('provenance.read')
        self.assertEqual(set(prov['libraries']), {'nanomechanical_library.json', 'probe_library.json', 'sample_library.json'})
        self.assertEqual((prov['substances'], prov['sample_templates']), (66, 15))
        t = h.t
        self.assertAlmostEqual(h.invoke('sim.step', dt_s=2.5)['time_s'], t + 2.5, places=9)
        self.assertEqual(h.invoke('records.list')['records'], sorted(h.records))
        ev = h.invoke('events.read', since_s=t)
        self.assertTrue(all(e['time_s'] >= t for e in ev['events']))
        cam = h.invoke('camera.read')
        self.assertEqual(cam['field_of_view_m'], list(rt.FIELD_OF_VIEW_M))
        self.assertEqual(h.invoke('system.acknowledge')['cleared'], None)

    def test_defects_become_device_errors(self):
        h = _scenario('scanned')
        h.register_principal('a', 'agent')
        s = h.session('a')
        h.records['IMG0001']['data'] = None                                                     # a corrupted record
        r = s.invoke(instance_id=h.instance_id, capability_id='analysis.roughness', arguments=dict(record_id='IMG0001', channel='height', direction='trace',
                                                                                                    flatten='plane', region_px=None), request_id='x')
        self.assertEqual((r['status'], r['error']['code'], r['error']['side_effects']), ('rejected', 'DEVICE_ERROR', 'unknown'))


# ---------------------------------------------------------------- declared effects
class DeclaredEffects(unittest.TestCase):
    def test_immediate_and_on_step_effects_are_declared(self):
        """For every capability accepted in any scenario state: the slots it changes at once are declared effects, the slots whose change during the
        following step differs from an identical run without the call are declared on_step effects, and the step itself only changes slots declared
        as on_step effects of sim.step (the runtime noise is seeded by record and line counters, so both runs draw identical noise)."""
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
        """Every accepted asynchronous call is followed until its operation has left 'running' (+ 5 s), every other call for 120 s, comparing with
        the identical run without the call every 0.25 s for the first 5 s and every 2 s after."""
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


class ReviewRound1Fixes(unittest.TestCase):
    """Regression tests of the independent review round 1 (runtime F01-F12, contract C02-C04)."""

    def test_F01_F02_F12_topography_parameters_are_bounded_and_work_limited(self):
        h = configured(samples=(), probes=())
        for i, (tid, ov) in enumerate([('hopg', {'terrace_width_m': 0.0}), ('ps_pmma_blend', {'domain_size_m': 0.0}), ('calibration_grating_xyz', {'pitch_m': 0.0}),
                                       ('au_np_on_mica', {'diameter_m': 0.0}), ('graphene_on_sio2', {'flake_size_m': 0.0}), ('dna_on_mica', {'width_m': 0.0}),
                                       ('dna_on_mica', {'persistence_length_m': 0.0}), ('mica', {'corr_length_m': 0.0}),
                                       ('calibration_grating_xyz', {'pitch_m': 1e-6, 'edge_width_m': 0.9e-6}),       # edges wider than the box
                                       ('au_np_on_mica', {'diameter_m': 3e-9, 'diameter_sd_m': 2e-9})]):          # spread above a third
            rejected(h, 'sample.provide', 'INVALID_ARGUMENT', sample_id=f'b{i}', template_id=tid, seed=1, tilt_rad=[0.0, 0.0], topography_overrides=ov)
        for i, (tid, ov) in enumerate([('graphene_on_sio2', {'flake_size_m': 5e-8}), ('au_np_on_mica', {'diameter_m': 2e-9, 'diameter_sd_m': 0.0, 'coverage': 0.5}),
                                       ('dna_on_mica', {'width_m': 5e-10, 'coverage': 0.9})]):
            rejected(h, 'sample.provide', 'SIMULATION_LIMIT', sample_id=f'w{i}', template_id=tid, seed=1, tilt_rad=[0.0, 0.0], topography_overrides=ov)
        for tid, tpl in rt.sample_templates()['templates'].items():                          # every library template is in range and cheap
            w = rt.topography_work(rt.flatten_topography(tpl['topography']))
            self.assertTrue(w is not None and w <= 1.0, (tid, w))
        # the smallest accepted objects keep a read fast; a defect inside read becomes DEVICE_ERROR, never a raw exception
        import time
        g = ready(samples=(dict(sample_id='g', template_id='dna_on_mica', seed=1, tilt_rad=[0.0, 0.0], topography_overrides={'width_m': 1e-9}),), probes=())
        g.invoke('sample.place', sample_id='g', offset_x_m=0.0, offset_y_m=0.0)
        g.register_principal('a', 'agent')
        a = g.session('a')
        t0 = time.time()
        for _ in range(5):
            a.read(instance_id=g.instance_id, state_ids=['clock.time_s'])
        self.assertLess(time.time() - t0, 5.0)
        g._pspd = lambda: 1 / 0
        with self.assertRaises(rt.ActionRejected) as ctx:
            a.read(instance_id=g.instance_id)
        self.assertEqual(str(ctx.exception), 'DEVICE_ERROR')

    def test_F03_xy_moves_check_the_whole_path_and_a_resting_tip(self):
        h = ready()
        mount(h, 'cont1')
        align(h)
        h.invoke('sample.place', sample_id='grating', offset_x_m=0.0, offset_y_m=0.0)
        settle(h)
        h.invoke('stage.move_xy', x_m=-0.0095, y_m=0.0)
        settle(h)
        h.invoke('z_stage.move', z_m=rt.Z_STAGE_LIMITS_M[0])                                   # touch stop on the holder beside the sample
        settle(h)
        self.assertEqual(h.events[-1]['code'], 'TOUCH_STOP')
        rejected(h, 'stage.move_xy', 'COLLISION_RISK', x_m=0.0095, y_m=0.0)                     # the tip rests on the holder
        h.invoke('z_stage.move', z_m=h.qz + 0.5e-3)
        settle(h)
        self.assertLess(h._tip_z(), h._top_z(h._sample()))                                     # still below the sample top
        rejected(h, 'stage.move_xy', 'COLLISION_RISK', x_m=0.0095, y_m=0.0)                     # the sample lies on the path
        h.invoke('stage.move_xy', x_m=-0.0090, y_m=0.0)                                         # along the holder: allowed
        settle(h)
        h.invoke('z_stage.move', z_m=h.qz + 3e-3)
        settle(h)
        h.invoke('stage.move_xy', x_m=0.0095, y_m=0.0)                                          # high above everything: allowed
        settle(h)
        self.assertTrue(np.allclose(h.read()['stage.xy_m'], [0.0095, 0.0], atol=1e-12))

    def test_F04_the_approach_holds_the_tip_after_the_trigger(self):
        h = ready()
        mount(h, 'tap1')
        align(h)
        dynamic(h)
        h.invoke('sample.place', sample_id='grating', offset_x_m=0.0, offset_y_m=0.0)
        settle(h)
        near(h)
        op = h.invoke('approach', setpoint_force_n=None, setpoint_amplitude_m=16e-9, z_servo_gain=1.0)['operation_id']
        rows = []
        while h.operations[op]['phase'] == 'running':
            h.step(0.25)
            ev = h.read('evaluator')
            rows.append((ev['truth.gap_m'], ev['zscanner.extension_m'], h._tip_z(), ev['engage.state']))
        before = [r for r in rows if r[0] is not None]
        after = [r for r in rows if r[0] is None and r[3] == 'approaching']
        self.assertTrue(before and after)
        self.assertGreater(min(r[0] for r in before), 0.0)                                      # never inside the sample
        ext = [r[1] for r in after]
        self.assertTrue(all(b <= a + 1e-15 for a, b in zip(ext, ext[1:])))                     # the Z detector falls continuously ...
        self.assertAlmostEqual(ext[-1], rt.Z_SCAN_RANGE_M * rt.APPROACH_TARGET_EXTENSION, delta=1e-9)   # ... to half extension
        self.assertLess(max(r[2] for r in after) - min(r[2] for r in after), 1e-10)           # the tip stays at the operating point
        self.assertAlmostEqual(h.read()['zscanner.extension_m'], rt.Z_SCAN_RANGE_M * rt.APPROACH_TARGET_EXTENSION, delta=1e-9)

    def test_F05_the_drive_belongs_to_the_mounted_probe(self):
        h = tapping_engaged()
        pr = h._probe()
        self.assertAlmostEqual(h.engaged['mp']['A_free'],
                               h.drive['A_res'] * float(rt.lorentz_amp(h.drive['frequency_hz'], pr['truth']['f0'], pr['truth']['Q'])), delta=1e-15)
        h.invoke('retract', distance_m=100e-6)
        settle(h)
        mount(h, 'multi1')
        align(h)
        self.assertIsNone(h.read()['ncm.drive'])                                               # the exchange clears the drive
        h.invoke('head.configure', mode='tapping', cantilever_db='multi75al_g')
        near(h)
        rejected(h, 'approach', 'DRIVE_NOT_SET', setpoint_force_n=None, setpoint_amplitude_m=16e-9, z_servo_gain=1.0)
        dynamic(h, db='multi75al_g')
        shown = h.read()['ncm.amplitude_m']
        rejected(h, 'approach', 'SETPOINT_OUT_OF_RANGE', setpoint_force_n=None, setpoint_amplitude_m=shown * 1.01, z_servo_gain=1.0)
        h.invoke('head.configure', mode='tapping', cantilever_db='ac160ts')                    # another database entry clears it too
        self.assertIsNone(h.read()['ncm.drive'])

    def test_F06_error_channels_are_volts(self):
        h = tapping_engaged()
        rec = scan(h, pixels_x=64, pixels_y=32)
        e = np.asarray(rec['data']['error']['trace'], float)
        S = h._optics()['S']
        self.assertEqual(rec['meta']['channel_units']['error'], 'V')
        self.assertGreater(np.nanstd(e), 1e-4)                                                  # lock-in amplitude signal (V), not metres
        self.assertLess(np.nanstd(e) / S, 5e-9)
        c = contact_engaged(sample=SIO2)
        rc = scan(c, pixels_x=64, pixels_y=32)
        self.assertGreater(np.nanstd(np.asarray(rc['data']['error']['trace'], float)), 1e-5)

    def test_F07_the_contact_set_point_is_held_on_the_erf_response(self):
        c = ready()
        mount(c, 'cont1')
        align(c)
        c.invoke('head.configure', mode='contact', cantilever_db='contal_g')
        from scipy.special import erfinv
        c.invoke('head.turn_knob', knob='pspd_y', turns=float(erfinv(-0.85)) * rt.PSPD_SPOT_W_M / rt.KNOB_PSPD_M_PER_TURN)
        V_free = c.read()['pspd.vertical_v']
        self.assertAlmostEqual(V_free / c.read()['pspd.intensity_v'], -0.85, delta=1e-6)
        c.invoke('sample.place', sample_id='wafer', offset_x_m=0.0, offset_y_m=0.0)
        settle(c)
        near(c)
        wait(c, c.invoke('approach', setpoint_force_n=20e-9, setpoint_amplitude_m=None, z_servo_gain=1.0)['operation_id'])
        o = c._optics()
        V_set = V_free + 20e-9 * c.cal['sensitivity_v_per_m'] / c.cal['spring_constant_n_m']
        self.assertAlmostEqual(c.read()['pspd.vertical_v'], V_set, delta=1e-9)                 # the engaged VERTICAL is the set point
        d = rt.pspd_inverse(o['I'], o['p_v'], o['S'], V_set)
        self.assertAlmostEqual(c.read('evaluator')['truth.tip_force_n'], c._probe()['truth']['k'] * d, delta=1e-15)
        self.assertGreater(c.read('evaluator')['truth.tip_force_n'], 1.3 * 20e-9 * c._probe()['truth']['k'] / c.cal['spring_constant_n_m']
                           * c.cal['sensitivity_v_per_m'] / o['S'])                             # the erf slope is lower off-centre: more load

    def test_F08_a_touch_stopped_tip_rests_on_the_surface(self):
        d = ready()
        mount(d, 'cont1')
        align(d)
        d.invoke('head.configure', mode='contact', cantilever_db='contal_g')
        d.invoke('sample.place', sample_id='wafer', offset_x_m=0.0, offset_y_m=0.0)
        settle(d)
        near(d, gap=50e-6)
        d.invoke('z_stage.move', z_m=d.qz - 200e-6)
        settle(d)
        ev = d.read('evaluator')
        self.assertEqual((d.events[-1]['code'], ev['truth.gap_m']), ('TOUCH_STOP', 0.0))
        self.assertAlmostEqual(ev['truth.tip_force_n'], d._probe()['truth']['k'] * rt.TOUCH_OVERSHOOT_M, delta=1e-12)
        o = d._optics()
        self.assertAlmostEqual(ev['pspd.vertical_v'], float(rt.pspd_signal(o['I'], o['p_v'], o['S'], rt.TOUCH_OVERSHOOT_M)), delta=1e-12)
        rejected(d, 'calibration.thermal_tune', 'TIP_IN_CONTACT', temperature_k=295.15, averages=10)
        rejected(d, 'approach', 'TIP_IN_CONTACT', setpoint_force_n=5e-9, setpoint_amplitude_m=None, z_servo_gain=1.0)
        d.invoke('z_stage.move', z_m=d.qz + 100e-6)
        settle(d)
        ev = d.read('evaluator')
        self.assertGreater(ev['truth.gap_m'], 90e-6)
        self.assertIsNone(ev['truth.tip_force_n'])
        self.assertAlmostEqual(ev['pspd.vertical_v'], float(rt.pspd_signal(o['I'], o['p_v'], o['S'], 0.0)), delta=1e-12)

    def test_F09_record_memory_and_large_replays_are_bounded(self):
        h = tapping_engaged()
        old = rt.RECORD_MEMORY_BYTES
        try:
            rt.RECORD_MEMORY_BYTES = h._record_bytes() + 4 * 64 * 64 * 4 * 2 - 1              # one more 64 x 64 image with retrace does not fit
            rejected(h, 'scan.start', 'RECORDS_FULL', **dict(SCAN, save_retrace=True))
        finally:
            rt.RECORD_MEMORY_BYTES = old
        rec = scan(h, pixels_x=128, pixels_y=128)
        h.register_principal('ev', 'evaluator')
        e = h.session('ev')
        args = dict(record_id=rec['record_id'], channel=None, direction=None, region_px=None, max_values=65536)
        first = e.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=args, request_id='q0')
        self.assertEqual(e.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=args, request_id='q0'), first)
        for i in range(1, rt.LARGE_RESPONSE_KEEP + 3):
            e.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=args, request_id=f'q{i}')
        self.assertLessEqual(len(h._large_requests['ev']), rt.LARGE_RESPONSE_KEEP)
        self.assertIsNone(h._requests[('ev', 'q0')][1])                                         # the oldest large result is no longer held,
        self.assertEqual(e.invoke(instance_id=h.instance_id, capability_id='records.delete', arguments=dict(record_id=rec['record_id']),
                                  request_id='q0')['error']['code'], 'REQUEST_CONFLICT')                   # but its request id stays bound (F17)
        again = e.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=args, request_id='q0')
        self.assertEqual(again['result'], first['result'])                                     # a replayed read-only query gives the same result

    def test_F10_a_truncated_tip_footprint_is_flagged_for_the_evaluator(self):
        h = tapping_engaged(sample=TALL)
        h.register_principal('a', 'agent')
        a = h.session('a')
        r = a.invoke(instance_id=h.instance_id, capability_id='scan.start', request_id='fp',
                     arguments=dict(SCAN, size_m=2e-6, pixels_x=512, pixels_y=16, rate_hz=1.0))
        h.step(20.0)
        rec = h.records[h.operations[r['operation_id']]['record_id']]
        self.assertIn('tip_footprint_truncated', rec['warnings'])
        seen = a.invoke(instance_id=h.instance_id, capability_id='measurement.result', request_id='fp2',
                        arguments=dict(record_id=rec['record_id'], channel='height', direction='trace', region_px=None, max_values=64))
        self.assertNotIn('tip_footprint_truncated', seen['result']['record']['warnings'])

    def test_F11_a_refused_cancel_answers_with_the_response_envelope(self):
        h = _scenario('engaged')
        h.register_principal('a', 'agent')
        h.register_principal('b', 'agent')
        a, b = h.session('a'), h.session('b')
        r = a.invoke(instance_id=h.instance_id, capability_id='scan.start', arguments=SCAN, request_id='s1')
        c = b.cancel(r['operation_id'], 'x1')
        self.assertEqual((c['status'], c['error']['code'], c['error']['side_effects']), ('rejected', 'ACCESS_DENIED', 'none'))
        self.assertEqual(h.operations[r['operation_id']]['phase'], 'running')
        self.assertEqual(a.cancel('OP9999', 'x2')['error']['code'], 'UNKNOWN_OPERATION')

    def test_C02_C03_C04_contract_texts_and_latched_stage_faults(self):
        m = rt.export_park_nx10_contract_metadata()
        slots, caps = m['state_slots'], m['capabilities']
        self.assertIn('逆时针', slots['knob.beam_x_turns']['meaning_zh'])
        self.assertIn('逆时针', caps['head.turn_knob']['input_schema']['properties']['turns']['x-reference-conditions'])
        self.assertTrue(slots['truth.drift_m'].get('reference_conditions'))
        codes = lambda k: {e['code'] for e in caps[k]['errors']}                               # noqa: E731
        self.assertNotIn('XY_STAGE_FAULT', codes('stage.move_xy'))
        self.assertNotIn('Z_STAGE_FAULT', codes('z_stage.move') | codes('retract'))
        self.assertNotIn('MOTION_IN_PROGRESS', codes('ncm.set_drive'))
        h = ready()
        h.invoke('fault.inject', fault='xy_stage_stall', active=True)
        out = h.invoke('stage.move_xy', x_m=0.004, y_m=0.0)                                   # accepted, the stall is latched
        self.assertEqual((out['duration_s'], out['target_m'], h.read()['system.error_code']), (0.0, [0.0, 0.0], 'XY_STAGE_FAULT'))


class ReviewRound2Fixes(unittest.TestCase):
    """Regression tests of the independent review round 2 (runtime F13-F19, contract C06-C08)."""

    def _over_holder_below_the_sample_top(self, xy):
        h = ready()
        mount(h, 'cont1')
        align(h)
        h.invoke('sample.place', sample_id='grating', offset_x_m=0.0, offset_y_m=0.0)
        settle(h)
        h.invoke('stage.move_xy', x_m=xy[0], y_m=xy[1])
        settle(h)
        h.invoke('z_stage.move', z_m=rt.Z_STAGE_LIMITS_M[0])                                   # touch stop on the holder
        settle(h)
        h.invoke('z_stage.move', z_m=h.qz + 0.5e-3)
        settle(h)
        self.assertLess(h._tip_z(), h._top_z(h._sample()))
        return h

    def test_F13_one_motorized_stage_at_a_time(self):
        h = self._over_holder_below_the_sample_top((-0.0095, 0.0))
        h.invoke('z_stage.move', z_m=h.qz - 0.4e-3)                                             # still above the holder
        rejected(h, 'stage.move_xy', 'MOTION_IN_PROGRESS', x_m=0.0, y_m=0.0)
        settle(h)
        h.invoke('stage.move_xy', x_m=-0.0090, y_m=0.0)
        rejected(h, 'z_stage.move', 'MOTION_IN_PROGRESS', z_m=rt.Z_STAGE_LIMITS_M[0])
        settle(h)
        self.assertGreaterEqual(h.read('evaluator')['truth.gap_m'] if h.read('evaluator')['truth.gap_m'] is not None else 1.0, 0.0)

    def test_F14_a_path_clipping_a_sample_corner_is_refused(self):
        # sample-frame segment p(t) = (2.5 mm - 5 um + t, 2.5 mm - 5 um - t), |t| <= 1 mm: it cuts the corner of the 5 x 5 mm grating by a 14 um chord
        a, b = (-0.001495, -0.003495), (-0.003495, -0.001495)
        h = self._over_holder_below_the_sample_top(a)
        rejected(h, 'stage.move_xy', 'COLLISION_RISK', x_m=b[0], y_m=b[1])
        self.assertTrue(rt._segment_hits_box((0.002495 - 0.001, 0.002495 + 0.001), (0.002495 + 0.001, 0.002495 - 0.001), 0.0025, 0.0025))
        self.assertFalse(rt._segment_hits_box((0.002505 - 0.001, 0.002505 + 0.001), (0.002505 + 0.001, 0.002505 - 0.001), 0.0025, 0.0025))

    def test_F15_F16_a_broken_lever_carries_no_load_and_a_pressed_tip_is_not_lowered_again(self):
        d = ready()
        mount(d, 'cont1')
        align(d)
        d.invoke('sample.place', sample_id='wafer', offset_x_m=0.0, offset_y_m=0.0)
        settle(d)
        near(d, gap=50e-6)
        d.invoke('z_stage.move', z_m=d.qz - 200e-6)
        settle(d)
        n_touch, r0 = sum(e['code'] == 'TOUCH_STOP' for e in d.events), d._probe()['radius']
        rejected(d, 'z_stage.move', 'TIP_IN_CONTACT', z_m=d.qz - 50e-6)                        # F16: no second touch stop, no extra wear
        self.assertEqual((sum(e['code'] == 'TOUCH_STOP' for e in d.events), d._probe()['radius']), (n_touch, r0))
        c = ready()
        mount(c, 'cont1')
        align(c)
        c.invoke('sample.place', sample_id='wafer', offset_x_m=0.0, offset_y_m=0.0)
        settle(c)
        near(c, gap=50e-6)
        from scipy.special import erfinv
        c.invoke('head.turn_knob', knob='pspd_y', turns=float(erfinv(0.95)) * rt.PSPD_SPOT_W_M / rt.KNOB_PSPD_M_PER_TURN)    # PSPD far off: no touch detection
        c.invoke('z_stage.move', z_m=c.qz - 200e-6)
        settle(c)
        self.assertEqual(c._probe()['damage'], 'cantilever_broken')
        self.assertIsNone(c.read('evaluator')['truth.tip_force_n'])                            # F15

    def test_F17_an_evicted_request_id_stays_bound_to_its_query(self):
        h = tapping_engaged()
        rec = scan(h, pixels_x=128, pixels_y=128)
        h.register_principal('ev', 'evaluator')
        e = h.session('ev')
        args = dict(record_id=rec['record_id'], channel=None, direction=None, region_px=None, max_values=65536)
        first = e.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=args, request_id='q0')
        for i in range(1, rt.LARGE_RESPONSE_KEEP + 2):
            e.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=args, request_id=f'q{i}')
        c = e.invoke(instance_id=h.instance_id, capability_id='records.delete', arguments=dict(record_id=rec['record_id']), request_id='q0')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')
        self.assertIn(rec['record_id'], h.records)
        self.assertEqual(e.invoke(instance_id=h.instance_id, capability_id='measurement.result', arguments=args, request_id='q0')['result'], first['result'])

    def test_F18_topography_statistics_only_for_the_height_channel(self):
        h = tapping_engaged()
        rec = scan(h, pixels_x=64, pixels_y=32)
        rejected(h, 'analysis.roughness', 'INVALID_ARGUMENT', record_id=rec['record_id'], channel='error', direction='trace', flatten='line_1', region_px=None)
        rejected(h, 'analysis.step_height', 'INVALID_ARGUMENT', record_id=rec['record_id'], channel='phase', direction='trace', flatten='line_1',
                 method='histogram', region_a_px=None, region_b_px=None)
        f = h.invoke('analysis.flatten', record_id=rec['record_id'], channel='error', direction='trace', method='line_1')
        self.assertIsNone(f['stats'])
        self.assertEqual(h.records[f['record_id']]['meta']['unit'], 'V')
        self.assertIsNotNone(h.invoke('analysis.flatten', record_id=rec['record_id'], channel='height', direction='trace', method='line_1')['stats'])

    def test_C06_C07_C08_error_meanings_drift_frame_and_the_flatten_memory_cap(self):
        m = rt.export_park_nx10_contract_metadata()
        err = {e['code']: e['meaning_zh'] for e in m['capabilities']['sample.provide']['errors']}
        self.assertIn('程序化形貌对象过多', err['SIMULATION_LIMIT'])
        self.assertIn('2048', {e['code']: e['meaning_zh'] for e in m['capabilities']['scan.start']['errors']}['SIMULATION_LIMIT'])
        self.assertIn('针尖相对样品', m['state_slots']['truth.drift_m']['reference_conditions'])
        h = configured(samples=(), probes=())
        h.register_principal('ev', 'evaluator')
        r = h.session('ev').invoke(instance_id=h.instance_id, capability_id='sample.provide', request_id='p',
                                   arguments=dict(sample_id='g', template_id='graphene_on_sio2', seed=1, tilt_rad=[0.0, 0.0], topography_overrides={'flake_size_m': 5e-8}))
        self.assertEqual(r['error']['code'], 'SIMULATION_LIMIT')
        self.assertIn('程序化形貌对象过多', r['error']['message'])
        t = tapping_engaged()
        rec = scan(t, pixels_x=64, pixels_y=32)
        old = rt.RECORD_MEMORY_BYTES
        try:
            rt.RECORD_MEMORY_BYTES = t._record_bytes() + 4 * 64 * 32 - 1
            rejected(t, 'analysis.flatten', 'RECORDS_FULL', record_id=rec['record_id'], channel='height', direction='trace', method='plane')
        finally:
            rt.RECORD_MEMORY_BYTES = old


class ReviewRound3Fixes(unittest.TestCase):
    """Regression tests of the independent review round 3 (runtime F20, F21; contract C11, C12)."""

    def test_F20_the_path_guard_includes_the_sample_tilt(self):
        tilted = dict(sample_id='tilted', template_id='si_wafer', seed=3, tilt_rad=[0.02, 0.0])
        h = ready(samples=(tilted,), probes=(('cont1', 'contal_g'),))
        mount(h, 'cont1')
        align(h)
        h.invoke('sample.place', sample_id='tilted', offset_x_m=0.0, offset_y_m=0.0)
        settle(h)
        near(h, 50e-6)                                                                          # 50 um above the surface at the centre
        rejected(h, 'stage.move_xy', 'COLLISION_RISK', x_m=-0.004, y_m=0.0)                     # uphill: the surface rises 80 um under the path
        h.invoke('stage.move_xy', x_m=0.004, y_m=0.0)                                           # downhill: allowed
        settle(h)
        self.assertGreater(h.read('evaluator')['truth.gap_m'], 50e-6)
        self.assertGreater(h._top_z_max(h._sample()) - h._top_z(h._sample()), 0.02 * 0.005 - 1e-12)   # the tilted top counts in the clearances

    def test_F21_no_focus_move_while_the_head_slides(self):
        h = ready()
        h.invoke('z_stage.move', z_m=0.005)
        settle(h)
        h.invoke('head.remove')
        self.assertEqual(h.read()['head.state'], 'moving')
        rejected(h, 'focus.move', 'MOTION_IN_PROGRESS', position_m=0.015)
        settle(h)
        h.invoke('focus.move', position_m=rt.FOCUS_REF_M)

    def test_C11_C12_record_and_topography_limit_meanings(self):
        m = rt.export_park_nx10_contract_metadata()
        meaning = lambda k, c: {e['code']: e['meaning_zh'] for e in m['capabilities'][k]['errors']}[c]     # noqa: E731
        self.assertIn('2 GiB', meaning('scan.start', 'RECORDS_FULL'))
        self.assertIn('2 GiB', meaning('analysis.flatten', 'RECORDS_FULL'))
        for k in ('fd.acquire', 'calibration.thermal_tune', 'ncm.frequency_sweep'):
            self.assertNotIn('GiB', meaning(k, 'RECORDS_FULL'))                                  # these only meet the 200-record limit
        self.assertNotIn('400', meaning('sample.provide', 'SIMULATION_LIMIT'))                  # no unreachable cause


class ReviewRound4Fixes(unittest.TestCase):
    """Regression tests of the independent review round 4 (runtime F22, F23; contract C13)."""

    def test_F22_the_step_staircase_slope_counts_in_the_path_guard(self):
        h = ready()
        mount(h, 'cont1')
        align(h)
        h.invoke('sample.place', sample_id='hopg', offset_x_m=0.0, offset_y_m=0.0)
        settle(h)
        near(h, 1.5e-6)                                                                         # 1.5 um above the terraces at the centre
        d = rt.sample_templates()['templates']['hopg']['topography']['direction_rad']
        d = d['value'] if isinstance(d, dict) else d
        up = (-0.0045 * math.cos(d), -0.0045 * math.sin(d))                                    # the staircase rises along +u of the sample frame
        rejected(h, 'stage.move_xy', 'COLLISION_RISK', x_m=round(up[0], 5), y_m=round(up[1], 5))
        h.invoke('stage.move_xy', x_m=round(-up[0], 5), y_m=round(-up[1], 5))                  # downhill: allowed
        settle(h)
        self.assertGreater(h.read('evaluator')['truth.gap_m'], 1.5e-6)
        g = configured(samples=(), probes=())
        rejected(g, 'sample.provide', 'INVALID_ARGUMENT', sample_id='steep', template_id='hopg', seed=1, tilt_rad=[0.0, 0.0],
                 topography_overrides={'step_height_m': 1e-7, 'terrace_width_m': 1e-6})       # slope 0.1: not an atomic-step surface
        g.invoke('sample.provide', sample_id='vicinal', template_id='hopg', seed=1, tilt_rad=[0.0, 0.0], topography_overrides={'step_height_m': 1e-8})
        self.assertAlmostEqual(math.hypot(*g.samples['vicinal']['surface'].mean_slope()), 1e-8 / 8e-7, delta=1e-12)

    def test_F23_the_head_does_not_slide_while_the_focus_moves(self):
        h = ready()
        h.invoke('z_stage.move', z_m=0.005)
        settle(h)
        h.invoke('focus.move', position_m=0.014)
        rejected(h, 'head.remove', 'MOTION_IN_PROGRESS')
        settle(h)
        h.invoke('focus.move', position_m=rt.FOCUS_REF_M)
        settle(h)
        h.invoke('head.remove')
        settle(h)
        h.invoke('focus.move', position_m=0.009)
        rejected(h, 'head.attach', 'MOTION_IN_PROGRESS')

    def test_C13_clearance_texts_name_the_highest_point(self):
        caps = rt.export_park_nx10_contract_metadata()['capabilities']
        for k in ('sample.place', 'sample.remove', 'head.remove', 'head.attach'):
            self.assertIn('最高点', caps[k]['function_zh'] if 'function_zh' in caps[k] else json.dumps(caps[k], ensure_ascii=False))
        self.assertIn('沿直线路径', json.dumps(caps['stage.move_xy'], ensure_ascii=False))


class ZZRejectionCodes(unittest.TestCase):
    def test_every_observed_rejection_code_is_declared(self):
        """Runs last (class name): every code raised by a device call anywhere in this test module is declared for that capability."""
        undeclared = {k: sorted(v - set(rt.CAP_ERRORS.get(k, [])) - {'INVALID_ARGUMENT', 'UNSUPPORTED', 'UNKNOWN_CAPABILITY'})
                      for k, v in REJECTIONS.items()}
        self.assertEqual({k: v for k, v in undeclared.items() if v}, {})


if __name__ == '__main__':
    unittest.main()
