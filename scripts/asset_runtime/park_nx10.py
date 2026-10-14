"""CHEM-158 Park Systems NX10 atomic force microscope (2024 brochure configuration: decoupled flexure XY scanner 50 x 50 um with closed-loop
control and flexure-guided Z scanner 15 um in the standard NX head, motorized XY stage 20 x 20 mm, Z stage 25 mm and focus stage 15 mm, samples up
to 100 x 100 mm and 20 mm thick, direct on-axis optics with a 10x ultra-long working distance objective, low-noise Z detector 0.02 nm, SLD beam
detection, thermal-method spring-constant calibration, True Non-Contact / contact / tapping imaging, phase imaging and force-distance
spectroscopy): runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time):
  * system: NX10 control electronics and SmartScan (power, initialisation), the head on its dovetail (removal for probe exchange), the SLD
    beam switch on the head, head modes (contact, non-contact, tapping) and the cantilever database of SmartScan;
  * probe: cantilever chips from the probe box (probe_library.json: manufacturer nominal k, f0, dimensions, tip radius) each with its own
    hidden actual spring constant, resonance frequency, quality factor, tip radius and mounting offset; tip wear with scanned distance and load;
  * beam detection: SLD spot on the cantilever moved by the two beam-alignment knobs (reflected intensity from the overlap of the spot with the
    cantilever, optical-lever sensitivity from the spot position along it), the reflected spot on the PSPD moved by the two PSPD knobs
    (VERTICAL / LATERAL signals, saturation at the INTENSITY), the on-axis camera panned by the two optical alignment knobs and focused by the
    focus stage (depth of field);
  * mechanics: XY stage (moves the sample under the head; not while engaged), Z stage (coarse approach; interlock on low beam intensity;
    touch stop), focus stage (moves with the Z stage), Z scanner (feedback), XY scanner (raster, offset, rotation);
  * tip-sample interaction: Hertz / DMT contact with adhesion (ambient surface energies, capillary condensation with humidity) and van der Waals
    attraction (Hamaker constants) from nanomechanical_library.json; the amplitude-distance relation of tapping (intermittent contact) and True
    Non-Contact (attractive regime) operation; phase from the dissipated energy (adhesion hysteresis, loss tangent);
  * imaging: procedural sample topographies (sample_library.json: gratings, atomic steps, roughness, particles, phase domains, flakes, fibres)
    imaged through the tip (grey dilation with the sphere-cone tip), the Z feedback loop (integral servo with the Z servo gain, error saturation
    that makes the tip parachute off down-steps, oscillation above a critical gain), Z detector noise, environmental vibration, thermal drift
    and the residual bow of the XY scanner; channels height (Z detector), error, phase and Z drive (piezo voltage: creep);
  * spectroscopy and calibration: force-distance curves (jump-to-contact, indentation, pull-off), VERTICAL sensitivity from the contact slope,
    thermal tune (Lorentzian thermal noise of the free cantilever, equipartition with the optical-lever factor 0.817), frequency sweeps;
  * analysis (SmartAnalysis / XEI-like): plane / line flattening and roughness (Ra, Rq, Rpv, Rsk, Rku), line profiles, step heights,
    DMT / Hertz fits of force curves (modulus, adhesion);
  * fault injection (evaluator): SLD failure, Z-stage stall, XY-stage stall, broken tip, acoustic / floor vibration burst;
  * ledgers: electrical energy, beam on-time, stage and scanner travel, scanned lines, force curves, probe exchanges.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
from pathlib import Path

import numpy as np

ASSET_TYPE_ID = 'instrument.park_nx10'
PROTOCOL_VERSION = '1.0.0'
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t3_analytical/park_nx10'
NANOMECH_PATH = ASSET_DIR / 'nanomechanical_library.json'
PROBE_PATH = ASSET_DIR / 'probe_library.json'
SAMPLE_LIB_PATH = ASSET_DIR / 'sample_library.json'
KB = 1.380649e-23


class ActionRejected(ValueError):
    pass


# ---------------------------------------------------------------- documented (2024 brochure p. 8; NX10 User's Manual v2.3.2)
XY_SCAN_RANGE_M = 50e-6                            # XY scanner 50 x 50 um, closed loop (brochure, manual)
Z_SCAN_RANGE_M = 15e-6                             # standard head Z scanner 15 um (brochure; manual: stroke > 15 um)
XY_STAGE_HALF_M = 0.010                            # motorized XY stage 20 x 20 mm (brochure, manual)
XY_STAGE_RESOLUTION_M = 5e-6                       # manual
Z_STAGE_RANGE_M = 0.025                            # motorized Z stage 25 mm, max speed 0.8 mm/s, resolution 80 nm (manual)
Z_STAGE_MAX_SPEED_M_S = 0.8e-3
Z_STAGE_RESOLUTION_M = 80e-9
FOCUS_RANGE_M = 0.015                              # motorized focus stage 15 mm, moves with the Z stage (manual)
SAMPLE_OPEN_MAX_M = 0.100                          # open space up to 100 x 100 mm (brochure)
SAMPLE_HOLDER_MAX_M = 0.050                        # the chuck with the magnetic holder: up to 50 x 50 x 20 mm, 500 g (manual)
SAMPLE_MAX_THICKNESS_M = 0.020
SAMPLE_MAX_MASS_KG = 0.5
SAMPLE_BIAS_RANGE_V = (-10.0, 10.0)                # manual
SLD_WAVELENGTH_M = 830e-9                          # manual
Z_DETECTOR_NOISE_M = 0.02e-9                       # brochure: low noise Z detector 0.02 nm over large bandwidth
XY_BOW_M = 2e-9                                    # brochure: less than 2 nm out-of-plane motion
MAX_PIXELS = 4096                                  # manual: maximum data size 4096 x 4096
OBJECTIVE_WD_M = 50.5e-3                           # manual: UL10X, NA 0.23, WD 50.5 mm (brochure: 0.21 NA)
OBJECTIVE_NA = 0.23
FIELD_OF_VIEW_M = (480e-6, 360e-6)                 # 10x objective (brochure, manual)
INTENSITY_COATED_V = 2.5                           # manual: 2-3 V with a metal-coated cantilever back
INTENSITY_UNCOATED_V = 1.0                         # manual: closer to 1 V without coating
PSPD_CENTRE_TOL_V = 0.5                            # manual: VERTICAL, LATERAL within +-0.5 V
ELECTRONICS_POWER_W = 200.0                        # manual specification table 'Power: 200 W'
RETRACT_DISTANCE_M = 100e-6                        # manual: 'Retract Z after Done' lifts 100 um
BODY_DIMENSIONS_M = (0.245, 0.293, 0.443)          # brochure p. 8 (workbook); manual: 250 x 294 x 450 mm
ROOM_OPERATING_K = (291.15, 297.15)                # manual: 18-24 degC operating

# ---------------------------------------------------------------- geometry shared with the 3D asset (asset frame: origin at the bench projection of the body centre,
# +X right, +Y back, +Z up; brochure p. 8 drawing at 1.249 px/mm (front), 1.222 px/mm (side), 1.230 px/mm (height))
PROBE_AXIS_XY = (0.0268, -0.0205)                  # optical axis = cantilever tip (drawing: 26.8 mm right of the centre line, 126 mm behind the front)
DECK_TOP_Z = 0.0892                                # black deck plate on the base
CHUCK_TOP_Z = 0.0855                               # sample chuck on the XY scanner, just under the deck plate (large samples directly on it)
HOLDER_HEIGHT_M = 0.0030                           # magnetic sample holder screwed on the chuck (engineering)
SAMPLE_DISK_THICKNESS_M = 0.0008                   # steel sample disk (accessory kit 'Sample disk'; size engineering)
SAMPLE_DISK_DIAMETER_M = 0.015
HOLDER_RADIUS_M = 0.0125
CHUCK_HALF_M = (0.046, 0.043)                      # chuck plate: fills the deck window over the whole XY travel (engineering)
TIP_Z0 = 0.0978                                    # tip apex height at the Z-stage reference (drawing pose), Z scanner retracted
Z_STAGE_LIMITS_M = (-0.0125, 0.0125)               # joint of the Z stage relative to the drawing pose (25 mm travel: a bare chuck to a 20 mm sample)
FOCUS_REF_M = 0.0075                               # focus-stage position at which the focal plane lies on the cantilever
TIP_TO_CANTILEVER_M = 15e-6                        # apex to cantilever plane when no tip height is published (engineering)
DEPTH_OF_FIELD_M = 10e-6                           # +-5 um sharp (lambda / NA^2 with white light; engineering)

# ---------------------------------------------------------------- engineering values of the process model (not published)
BOOT_S = 20.0                                      # control electronics + SmartScan initialisation
HEAD_SWAP_S = 15.0                                 # unlock the dovetail, slide the head off / on (host abstraction)
PROBE_MOUNT_S = 20.0                               # chip carrier onto the ruby balls of the probehand
XY_STAGE_SPEED_M_S = 2.0e-3
FOCUS_SPEED_M_S = 0.8e-3
APPROACH_SPEED_M_S = 50e-6                         # final auto approach (Z stage stepping with the Z scanner extended)
APPROACH_SETTLE_S = 3.0
APPROACH_TARGET_EXTENSION = 0.5                    # the approach ends with the Z scanner at half extension
KNOB_BEAM_M_PER_TURN = 40e-6                       # beam alignment knobs: spot on the cantilever plane
KNOB_PSPD_V_PER_TURN = 1.5                         # PSPD alignment knobs: reflected spot on the photodiode
KNOB_OPTICS_M_PER_TURN = 100e-6                    # optical alignment knobs: camera panning
SPOT_SIGMA_M = 9e-6                                # 1 sigma radius of the focused SLD spot
PREALIGN_SIGMA_M = 12e-6                           # 'pre-aligned tip mount': new chip within this of the factory beam position
PREALIGN_INSET_M = 15e-6                           # the factory beam position lies 15 um inside the free end of a nominally mounted cantilever
MOUNT_OFFSET_SIGMA_M = 60e-6                       # new cantilever relative to the camera centre
SENSITIVITY_V_PER_M_100UM = 0.045e9                # optical lever: 45 mV/nm for a 100 um cantilever with the spot at its end, 2.5 V
THERMAL_TUNE_S = 12.0
FREQ_SWEEP_S = 6.0
INTERLOCK_INTENSITY_V = 0.5                        # 'Laser intensity is too low': Z stage may not move down
Z_SERVO_F0_HZ = {'contact': 1500.0, 'tapping': 600.0, 'non_contact': 500.0}      # loop bandwidth per unit Z servo gain
Z_SERVO_CRITICAL_GAIN = {'contact': 6.0, 'tapping': 4.0, 'non_contact': 3.0}      # above: oscillation
OSC_NOISE_M = 2.0e-9                               # oscillation amplitude per unit excess gain
CREEP_FRACTION = 0.012                             # Z drive (voltage) channel: creep of the piezo after steps (the Z detector has none)
CREEP_TAU_S = 2.0
VIBRATION_OPEN_M = 0.06e-9                         # vertical noise from floor / acoustic vibration without enclosure (RMS)
VIBRATION_BURST_FACTOR = 12.0
DRIFT_XY_M_S = 0.8e-9 / 60.0                       # thermal drift of an open system
DRIFT_Z_M_S = 1.5e-9 / 60.0
DEFLECTION_NOISE_M = 0.03e-9                       # optical lever noise (RMS, imaging bandwidth)
WEAR_M_PER_M_N = 2.0e-9 / (2e-4 * 5e-9)            # contact mode: tip radius +2 nm per 200 um scanned at 5 nN (10 nm tip, hard sample)
WEAR_TAPPING_FACTOR = 0.03
WEAR_NC_FACTOR = 0.002
BROKEN_TIP_RADIUS_M = 150e-9
TIP_HALF_ANGLE_RAD = math.radians(18.0)            # when the library gives no half cone angle
Q_REF, F_REF_HZ = 450.0, 300e3                     # quality factor in air: Q = 450 sqrt(f0 / 300 kHz) (engineering)
OPTICAL_LEVER_CHI = 0.817                          # first flexural mode of a rectangular cantilever (Butt and Jaschke 1995)
HUMIDITY_CAPILLARY = (0.25, 0.65)                  # capillary adhesion grows from 25 % to full at 65 % relative humidity
GAMMA_WATER = 0.072
TIP_GAMMA = 0.045                                  # oxidised silicon tip in laboratory air (ambient effective surface energy)
TIP_WATER_ANGLE = math.radians(30.0)
VDW_CUTOFF_M = 0.165e-9
HAMAKER_TIP_J = 2.0e-19                            # silicon tip (class value of the library)
DWELL_S = 0.2                                      # force curve dwell and trigger overhead
MAX_TIP_SPEED_M_S = 200e-6                         # SmartScan refuses faster raster (engineering)
MAX_RECORDS = 200
RECORD_MEMORY_BYTES = 2 * 1024 ** 3                  # pixel memory of all records (simulation limit): scan.start and analysis.flatten answer RECORDS_FULL
LARGE_RESPONSE_BYTES = 65536                         # query responses above this size are replayed for the last LARGE_RESPONSE_KEEP request ids only
LARGE_RESPONSE_KEEP = 16
MAX_EVENTS = 500
MAX_STEP_S = 7 * 24 * 3600.0
RESULT_VALUES_MAX = 65536
RESULT_DEFAULT_SIDE = 256

MODES = ['contact', 'non_contact', 'tapping']
KNOBS = ['beam_x', 'beam_y', 'pspd_x', 'pspd_y', 'optics_x', 'optics_y']
FAULTS = ['sld_failure', 'z_stage_stall', 'xy_stage_stall', 'tip_broken', 'vibration_burst']
CHANNELS = ['height', 'error', 'phase', 'z_drive']


# ---------------------------------------------------------------- schema helpers
def number(unit='1', lo=None, hi=None):
    s = {'type': 'number', 'x-unit': unit}
    if lo is not None:
        s['minimum'] = lo
    if hi is not None:
        s['maximum'] = hi
    return s


def obj(properties, optional=()):
    return dict(type='object', properties=properties, required=[k for k in properties if k not in optional], additionalProperties=False)


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


def array(s, minimum=0, maximum=4096):
    return dict(type='array', items=s, minItems=minimum, maxItems=maximum)


def enum(items):
    return {'type': 'string', 'enum': list(items)}


def ref(schema, text):
    return dict(schema, **{'x-reference-conditions': text})


BOOL = {'type': 'boolean'}
TEXT = {'type': 'string'}
INT = {'type': 'integer', 'x-unit': '1'}


def integer(lo, hi):
    return {'type': 'integer', 'x-unit': '1', 'minimum': lo, 'maximum': hi}


def validate(s, v):
    if 'anyOf' in s:
        for o in s['anyOf']:
            try:
                validate(o, v)
                return
            except ActionRejected:
                pass
        raise ActionRejected('INVALID_ARGUMENT')
    k = s.get('type')
    if k in ('number', 'integer'):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (k == 'integer' and not float(v).is_integer()):
            raise ActionRejected('INVALID_ARGUMENT')
        if not s.get('minimum', -math.inf) <= v <= s.get('maximum', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'object':
        if not isinstance(v, dict):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'properties' in s:
            req = set(s.get('required', s['properties']))
            if not req <= set(v) or not set(v) <= set(s['properties']):
                raise ActionRejected('INVALID_ARGUMENT')
            for key, value in v.items():
                validate(s['properties'][key], value)
        elif 'additionalProperties' in s and isinstance(s['additionalProperties'], dict):
            for key, value in v.items():
                if not isinstance(key, str):
                    raise ActionRejected('INVALID_ARGUMENT')
                validate(s['additionalProperties'], value)
    elif k == 'array':
        if not isinstance(v, list) or not s.get('minItems', 0) <= len(v) <= s.get('maxItems', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
        for x in v:
            validate(s['items'], x)
    elif k == 'boolean' and type(v) is not bool:
        raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'string':
        if not isinstance(v, str) or len(v) > s.get('maxLength', 10 ** 6) or len(v) < s.get('minLength', 0):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'pattern' in s:
            import re
            if not re.fullmatch(s['pattern'], v):
                raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


def coerce(s, v):
    """Integral floats of 'integer' fields as int, recursively (the value was validated against s)."""
    if 'anyOf' in s:
        for o in s['anyOf']:
            try:
                validate(o, v)
            except ActionRejected:
                continue
            return coerce(o, v)
        return v
    k = s.get('type')
    if k == 'integer' and isinstance(v, float):
        return int(v)
    if k == 'object' and isinstance(v, dict):
        if 'properties' in s:
            return {key: coerce(s['properties'][key], val) if key in s['properties'] else val for key, val in v.items()}
        if isinstance(s.get('additionalProperties'), dict):
            return {key: coerce(s['additionalProperties'], val) for key, val in v.items()}
    if k == 'array' and isinstance(v, list):
        return [coerce(s['items'], x) for x in v]
    return v


# ---------------------------------------------------------------- template libraries
@functools.lru_cache(maxsize=1)
def nanomech():
    lib = json.loads(NANOMECH_PATH.read_text())
    return dict(meta={k: v for k, v in lib.items() if k != 'substances'}, subs={s['substance_id']: s for s in lib['substances']})


@functools.lru_cache(maxsize=1)
def probes():
    lib = json.loads(PROBE_PATH.read_text())
    return dict(meta={k: v for k, v in lib.items() if k != 'probes'}, types={p['probe_id']: p for p in lib['probes']})


@functools.lru_cache(maxsize=1)
def sample_templates():
    lib = json.loads(SAMPLE_LIB_PATH.read_text())
    return dict(meta={k: v for k, v in lib.items() if k != 'templates'}, templates={t['template_id']: t for t in lib['templates']})


def library_sha256():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (NANOMECH_PATH, PROBE_PATH, SAMPLE_LIB_PATH)}


@functools.lru_cache(maxsize=256)
def material(sid):
    """Mechanical constants of a library substance (SI): E, nu, ambient surface energy, water contact angle, Hamaker constant, loss tangent."""
    s = nanomech()['subs'].get(sid)
    if s is None:
        raise ActionRejected('UNKNOWN_MATERIAL')
    return dict(id=sid, E=float(s['youngs_modulus']['value']), nu=float(s['poisson_ratio']['value']), gamma=float(s['ambient_surface_energy']['value']),
                theta_w=float(s['water_contact_angle']['value']), H=float(s['hamaker_constant']['value']), tan_delta=float(s['loss_tangent']['value']))


TIP_MATERIAL = {'Si': 'si', 'Si3N4': 'si3n4'}


def tip_material(probe_type):
    coat = str(probe_type.get('coating', '')).lower()
    if 'diamond' in coat:
        return 'diamond'
    return TIP_MATERIAL.get(probe_type.get('material'), 'si')


# ---------------------------------------------------------------- contact mechanics (Hertz / DMT with adhesion, van der Waals)
def reduced_modulus(tip_mat, sample_mat):
    a, b = material(tip_mat), material(sample_mat)
    return 1.0 / ((1 - a['nu'] ** 2) / a['E'] + (1 - b['nu'] ** 2) / b['E'])


def adhesion_force(R, sample_mat, humidity):
    """DMT pull-off 2 pi R W with W = 2 sqrt(gamma_tip gamma_sample) (ambient effective surface energies), plus capillary condensation
    4 pi R gamma_w cos(theta) (mean of tip and sample water contact angles) that switches on between 25 % and 65 % relative humidity."""
    m = material(sample_mat)
    w = 2.0 * math.sqrt(TIP_GAMMA * m['gamma'])
    f = 2.0 * math.pi * R * w
    lo, hi = HUMIDITY_CAPILLARY
    frac = min(1.0, max(0.0, (humidity - lo) / (hi - lo)))
    cos_t = max(0.0, 0.5 * (math.cos(TIP_WATER_ANGLE) + math.cos(m['theta_w'])))
    return f + frac * 4.0 * math.pi * R * GAMMA_WATER * cos_t


def hamaker_ts(sample_mat):
    """Tip-sample Hamaker constant by the combining relation sqrt(A_tip A_sample) (air)."""
    return math.sqrt(HAMAKER_TIP_J * material(sample_mat)['H'])


def dmt_force(delta, R, Es, F_ad):
    """Force on the tip (positive = repulsive) at indentation delta >= 0 in DMT contact."""
    return 4.0 / 3.0 * Es * math.sqrt(R) * max(delta, 0.0) ** 1.5 - F_ad


def dmt_indentation(F, R, Es, F_ad):
    """Indentation at the total (applied + adhesive) load F + F_ad (DMT)."""
    return (max(F + F_ad, 0.0) / (4.0 / 3.0 * Es * math.sqrt(R))) ** (2.0 / 3.0)


def vdw_force(D, R, A):
    """Sphere-plane van der Waals force (attractive, negative) at gap D (cut off at the contact distance)."""
    return -A * R / (6.0 * max(D, VDW_CUTOFF_M) ** 2)


def tip_profile(r, R, alpha):
    """Height of the sphere-cone tip surface above its apex at lateral distance r (arrays)."""
    r = np.abs(r)
    rc = R * math.cos(alpha)
    cap = R - np.sqrt(np.maximum(R * R - np.minimum(r, rc) ** 2, 0.0))
    return np.where(r <= rc, cap, R * (1 - math.sin(alpha)) + (r - rc) / math.tan(alpha))


def quality_factor(f0):
    return Q_REF * math.sqrt(f0 / F_REF_HZ)


# ---------------------------------------------------------------- procedural sample surfaces (sample frame: origin at the sample centre, m)
DEFAULT_CORR_LENGTH_M = 30e-9                       # roughness correlation length of a template that gives none (templates that need one give it)
TILE_M = 2e-6                                      # sparse structures (particles, flakes, fibres) are generated per 2 um tile, deterministic in the seed:
                                                   # the same place shows the same structures in every scan
N_MODES = 160                                      # random Fourier modes of a Gaussian random field
HEX_PACKING = math.pi / (2 * math.sqrt(3))         # area fraction of a hexagonal close-packed sphere monolayer
INTERFACE_SHARPNESS = 20.0                         # tanh(20 f): polymer domain interfaces about lc / 28 wide (tens of nm)
FIBRE_STEP_M = 2e-9


def _rng(*keys):
    return np.random.default_rng([int(k) & 0xFFFFFFFF for k in keys])


def flat_value(v):
    return v['value'] if isinstance(v, dict) and 'value' in v else v


MAX_OBJECTS_PER_TILE = {'particles': 5000, 'flakes': 50, 'fibers': 200}      # procedural objects per 2 um tile (simulation limit)
MAX_STEP_SLOPE = 0.05                                # step height / terrace width of an atomic-step (vicinal) surface: at most about 3 degrees
MAX_FIBRE_POINTS_PER_QUERY = 1_000_000               # chain points of the fibre tiles around one evaluated region (simulation limit)


def topography_work(tp):
    """Largest ratio of the procedural object counts of a topography to their simulation limits (> 1: SIMULATION_LIMIT); None when a parameter is
    outside its physical range or the parameters contradict each other (edges wider than the box, a diameter spread above a third of it)."""
    for key, (lo, hi) in OVERRIDE_RANGES.items():
        v = tp.get(key)
        if v is not None and not (lo <= float(v) <= hi):
            return None
    k = tp['type']
    if k in ('grating_1d', 'grating_2d') and (tp.get('edge_width_m') or 0.0) > tp['pitch_m'] * min(tp['duty'], 1.0 - tp['duty']):
        return None
    if k == 'steps' and tp['step_height_m'] / tp['terrace_width_m'] > MAX_STEP_SLOPE:
        return None                                                     # steeper than a vicinal atomic-step surface
    if k == 'particles':
        d = tp['diameter_m']
        if (tp.get('diameter_sd_m') or 0.0) > d / 3.0:
            return None
        n = TILE_M ** 2 / (d * d * math.sqrt(3.0) / 2.0) if tp.get('packing') == 'hexagonal' else tp['coverage'] * TILE_M ** 2 / (math.pi * d * d / 4.0)
        return n / MAX_OBJECTS_PER_TILE['particles']
    if k == 'flakes':
        return tp['coverage'] * TILE_M ** 2 / (0.45 * tp['flake_size_m'] ** 2) / MAX_OBJECTS_PER_TILE['flakes']
    if k == 'fibers':
        n = tp['coverage'] * TILE_M ** 2 / (tp['contour_length_m'] * tp['width_m'])
        tiles = (math.ceil(2.0 * tp['contour_length_m'] / TILE_M) + 2) ** 2
        pts = n * max(64 if tp.get('closed') else 8, tp['contour_length_m'] / FIBRE_STEP_M) * tiles
        return max(n / MAX_OBJECTS_PER_TILE['fibers'], pts / MAX_FIBRE_POINTS_PER_QUERY)
    return 0.0


def flatten_topography(tp):
    """Template topography with every documented / assumed parameter replaced by its value."""
    return {k: flat_value(v) for k, v in tp.items()}


def _smooth_box(phase, duty, edge):
    """Periodic box of the given duty (fraction 'up'), with linear edges of relative width edge; phase in [0, 1)."""
    e = max(edge, 1e-6)
    up = np.clip((phase + e / 2) / e, 0.0, 1.0)
    down = np.clip((duty + e / 2 - phase) / e, 0.0, 1.0)
    return np.minimum(up, down)


class GaussianField:
    """Stationary Gaussian random field with Gaussian correlation exp(-r^2 / lc^2) and RMS 1, as a sum of random cosines (deterministic in the
    seed, evaluable at any point). ring=True gives a narrow ring spectrum |k| = 2 pi / lc (labyrinth / fingerprint patterns of lamellae)."""

    def __init__(self, seed, corr_length_m, n=N_MODES, ring=False):
        r = _rng(seed, 11)
        if ring:
            kmag = 2 * math.pi / corr_length_m * (1.0 + 0.04 * r.standard_normal(n))
        else:
            kmag = math.sqrt(2.0) / corr_length_m * np.sqrt(-np.log(1.0 - r.random(n)))      # Rayleigh magnitudes of the Gaussian spectrum
        ang = 2 * math.pi * r.random(n)
        self.kx, self.ky = kmag * np.cos(ang), kmag * np.sin(ang)
        self.ph = 2 * math.pi * r.random(n)
        self.a = math.sqrt(2.0 / n)

    def __call__(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        out = np.zeros(np.broadcast(x, y).shape)
        for i in range(0, len(self.kx), 32):                                       # chunks keep the temporary arrays small
            k = slice(i, i + 32)
            out += np.cos(np.multiply.outer(x, self.kx[k]) + np.multiply.outer(y, self.ky[k]) + self.ph[k]).sum(-1)
        return self.a * out


def _erfinv(y):
    """Inverse error function (Winitzki approximation refined by two Newton steps)."""
    y = min(max(y, -0.999999), 0.999999)
    a = 0.147
    ln = math.log(1 - y * y)
    x = math.copysign(math.sqrt(math.sqrt((2 / (math.pi * a) + ln / 2) ** 2 - ln / a) - (2 / (math.pi * a) + ln / 2)), y)
    for _ in range(2):
        x -= (math.erf(x) - y) / (2 / math.sqrt(math.pi) * math.exp(-x * x))
    return x


def _closed_loop(r, contour, persistence):
    """Random smooth closed curve (plasmid on mica) of the given contour length: Fourier series with amplitudes ~ n^-2 up to n = L / (pi P)."""
    n = max(3, int(contour / (math.pi * persistence)))
    s = np.linspace(0.0, 1.0, max(64, int(contour / FIBRE_STEP_M)), endpoint=False)
    xs, ys = np.zeros_like(s), np.zeros_like(s)
    for m in range(1, n + 1):
        a = r.standard_normal(4) / m ** 2
        xs += a[0] * np.cos(2 * math.pi * m * s) + a[1] * np.sin(2 * math.pi * m * s)
        ys += a[2] * np.cos(2 * math.pi * m * s) + a[3] * np.sin(2 * math.pi * m * s)
    seg = np.hypot(np.diff(np.append(xs, xs[0])), np.diff(np.append(ys, ys[0]))).sum()
    k = contour / seg
    return (xs - xs.mean()) * k, (ys - ys.mean()) * k


def _open_chain(r, contour, persistence):
    """Worm-like chain on a surface (2D): the tangent angle diffuses with variance ds / P."""
    m = max(8, int(contour / FIBRE_STEP_M))
    th = 2 * math.pi * r.random() + np.cumsum(r.standard_normal(m) * math.sqrt(FIBRE_STEP_M / persistence))
    xs, ys = np.cumsum(FIBRE_STEP_M * np.cos(th)), np.cumsum(FIBRE_STEP_M * np.sin(th))
    return xs - xs.mean(), ys - ys.mean()


class Surface:
    """Height (m, relative to the nominal top surface of the sample) and material index under a point of the sample, from its topography
    definition (sample_library.json types); material index 0 = top layer (film or substrate), 1 = the second material of the template."""

    def __init__(self, sample):
        self.s = sample
        tp = self.t = sample['topography']
        self.kind = tp['type']
        self.seed = int(sample['seed'])
        self.top = sample['film'] or sample['substrate']
        self.materials = [self.top]
        for key in ('material', 'material_b'):
            if tp.get(key) and tp[key] not in self.materials:
                self.materials.append(tp[key])
        self.rough = GaussianField(self.seed * 7 + 1, tp.get('corr_length_m') or DEFAULT_CORR_LENGTH_M) if (tp.get('rq_m') or 0.0) > 0 else None
        if self.kind == 'domains':
            if tp.get('morphology') == 'lamellae':
                self.dfield = GaussianField(self.seed * 7 + 2, tp['period_m'], ring=True)
                self.threshold = 0.0
            else:
                self.dfield = GaussianField(self.seed * 7 + 2, tp.get('domain_size_m') or tp.get('period_m'))
                self.threshold = math.sqrt(2.0) * _erfinv(1.0 - 2.0 * tp.get('fraction_b', 0.5))     # P(field > threshold) = fraction_b
        if self.kind == 'steps':
            self.meander = GaussianField(self.seed * 7 + 4, 4 * tp['terrace_width_m'])
        if self.kind == 'particles' and tp.get('packing') == 'hexagonal':
            th = math.pi / 3 * _rng(self.seed, 5).random()
            d = tp['diameter_m']
            self.lattice = np.array([[d * math.cos(th), d * math.cos(th + math.pi / 3)], [d * math.sin(th), d * math.sin(th + math.pi / 3)]])
        self._tiles = {}
        self._blocks = {}
        self.tilt = sample.get('tilt_rad', [0.0, 0.0])

    def mean_slope(self):
        """Mean slope (dz/dx, dz/dy) of the topography in the sample frame, besides the tilt: the atomic-step staircase rises one step per terrace
        along its direction (a vicinal surface); the other types are level on average."""
        if self.kind == 'steps':
            g = self.t['step_height_m'] / self.t['terrace_width_m']
            d = self.t.get('direction_rad') or 0.0
            return g * math.cos(d), g * math.sin(d)
        return 0.0, 0.0

    def slope(self):
        """Tilt plus the mean slope of the topography (the linear part of the height over the sample)."""
        mx, my = self.mean_slope()
        return self.tilt[0] + mx, self.tilt[1] + my

    def characteristic_height(self):
        """Height range of the topography (m): sets the tip-dilation footprint."""
        tp, k = self.t, self.kind
        base = 4.0 * (tp.get('rq_m') or 0.0)
        if k in ('grating_1d', 'grating_2d'):
            return base + tp['height_m']
        if k == 'steps':
            return base + 4 * tp['step_height_m']
        if k == 'domains':
            return base + abs(tp['height_contrast_m'])
        if k == 'particles':
            return base + tp['diameter_m'] + 3 * (tp.get('diameter_sd_m') or 0.0)
        if k == 'flakes':
            return base + (tp.get('interface_offset_m') or 0.0) + max(tp['layers']) * tp['layer_thickness_m']
        if k == 'fibers':
            return base + tp['height_m']
        return max(base, 1e-10)

    # ---- tiled sparse structures
    def _tile(self, i, j):
        key = (i, j)
        if key in self._tiles:
            return self._tiles[key]
        if len(self._tiles) > 4096:
            self._tiles.clear()
        tp, r = self.t, _rng(self.seed, 101, i, j)
        x0, y0 = i * TILE_M, j * TILE_M
        out = {}
        if self.kind == 'particles':
            d0, sd = tp['diameter_m'], tp.get('diameter_sd_m') or 0.0
            if tp.get('packing') == 'hexagonal':
                inv = np.linalg.inv(self.lattice)
                corners = inv @ np.array([[x0, x0 + TILE_M, x0, x0 + TILE_M], [y0, y0, y0 + TILE_M, y0 + TILE_M]])
                n1 = np.arange(math.floor(corners[0].min()) - 1, math.ceil(corners[0].max()) + 2)
                n2 = np.arange(math.floor(corners[1].min()) - 1, math.ceil(corners[1].max()) + 2)
                g1, g2 = np.meshgrid(n1, n2, indexing='ij')
                pts = self.lattice @ np.stack([g1.ravel(), g2.ravel()])
                keep = (pts[0] >= x0) & (pts[0] < x0 + TILE_M) & (pts[1] >= y0) & (pts[1] < y0 + TILE_M)
                pts = pts[:, keep]
                occupied = r.random(pts.shape[1]) < min(1.0, tp['coverage'] / HEX_PACKING)          # vacancies
                pts = pts[:, occupied] + 0.03 * d0 * r.standard_normal((2, int(occupied.sum())))
                d = np.clip(d0 + sd * r.standard_normal(pts.shape[1]), 0.5 * d0, d0 + 2 * sd)
                out = dict(x=pts[0], y=pts[1], r=d / 2)
            else:
                n = r.poisson(tp['coverage'] * TILE_M ** 2 / (math.pi * (d0 / 2) ** 2))
                d = np.maximum(d0 + sd * r.standard_normal(n), 0.2 * d0)
                out = dict(x=x0 + TILE_M * r.random(n), y=y0 + TILE_M * r.random(n), r=d / 2)
        elif self.kind == 'flakes':
            size = tp['flake_size_m']
            n = r.poisson(tp['coverage'] * TILE_M ** 2 / (0.45 * size ** 2))                    # polygon area about 0.45 size^2
            w = np.asarray(tp.get('layer_weights') or [1.0] * len(tp['layers']), float)
            layers = r.choice(np.asarray(tp['layers'], int), size=n, p=w / w.sum())
            polys = []
            for _ in range(n):
                nv = int(r.integers(5, 9))
                ang = 2 * math.pi * (np.arange(nv) + 0.7 * (r.random(nv) - 0.5)) / nv + 2 * math.pi * r.random()   # gaps below pi: star-shaped
                rad = size / 2 * np.exp(0.3 * r.standard_normal()) * (0.55 + 0.45 * r.random(nv))
                polys.append(np.stack([rad * np.cos(ang), rad * np.sin(ang)], axis=1))     # star-shaped polygon with straight (cleaved) edges
            out = dict(x=x0 + TILE_M * r.random(n), y=y0 + TILE_M * r.random(n), poly=polys, n=layers)
        elif self.kind == 'fibers':
            Lc, P = tp['contour_length_m'], tp['persistence_length_m']
            n = r.poisson(tp['coverage'] * TILE_M ** 2 / (Lc * tp['width_m']))
            pts = []
            for _ in range(n):
                xs, ys = (_closed_loop if tp.get('closed') else _open_chain)(r, Lc, P)
                cx, cy = x0 + TILE_M * r.random(), y0 + TILE_M * r.random()
                pts.append(np.stack([cx + xs, cy + ys], axis=1))
            out = dict(pts=np.concatenate(pts) if pts else np.zeros((0, 2)))
        self._tiles[key] = out
        return out

    def _tiles_for(self, x, y, margin):
        return self._block(x, y, margin)['tiles']

    def _block(self, x, y, margin):
        """Tiles around the evaluated points, with their particle centres / fibre points and KD-tree (cached for the last 16 tile blocks)."""
        from scipy.spatial import cKDTree
        i0, i1 = int(math.floor((np.min(x) - margin) / TILE_M)), int(math.floor((np.max(x) + margin) / TILE_M))
        j0, j1 = int(math.floor((np.min(y) - margin) / TILE_M)), int(math.floor((np.max(y) + margin) / TILE_M))
        if (i1 - i0 + 1) * (j1 - j0 + 1) > 20000:
            raise ActionRejected('SIMULATION_LIMIT')
        key = (i0, i1, j0, j1)
        b = self._blocks.pop(key, None)
        if b is None:
            tiles = [self._tile(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]
            b = dict(tiles=tiles, tree=None)
            if self.kind == 'particles':
                cx = np.concatenate([t['x'] for t in tiles])
                if len(cx):
                    b.update(r=np.concatenate([t['r'] for t in tiles]), tree=cKDTree(np.stack([cx, np.concatenate([t['y'] for t in tiles])], axis=1)))
            elif self.kind == 'fibers':
                chain = np.concatenate([t['pts'] for t in tiles]) if tiles else np.zeros((0, 2))
                if len(chain):
                    b['tree'] = cKDTree(chain)
        self._blocks[key] = b                                            # most recent last
        while len(self._blocks) > 16:
            self._blocks.pop(next(iter(self._blocks)))
        return b

    # ---- evaluation
    def evaluate(self, x, y):
        """(height m, material index) at the sample-frame points x, y (arrays of one shape)."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        tp, kind = self.t, self.kind
        z = np.zeros(x.shape)
        mat = np.zeros(x.shape, np.int8)
        if kind == 'grating_1d':
            u = x if tp.get('axis', 'y') == 'x' else y
            z = tp['height_m'] * _smooth_box(np.mod(u / tp['pitch_m'], 1.0), tp['duty'], (tp.get('edge_width_m') or 0.0) / tp['pitch_m'])
        elif kind == 'grating_2d':
            p, e = tp['pitch_m'], (tp.get('edge_width_m') or 0.0) / tp['pitch_m']
            bx = _smooth_box(np.mod(x / p, 1.0), tp['duty'], e)
            by = _smooth_box(np.mod(y / p, 1.0), tp['duty'], e)
            z = (-1.0 if tp.get('kind', 'pits') == 'pits' else 1.0) * tp['height_m'] * bx * by
        elif kind == 'steps':
            d = tp.get('direction_rad') or 0.0
            u = x * math.cos(d) + y * math.sin(d)
            v = -x * math.sin(d) + y * math.cos(d)
            z = tp['step_height_m'] * np.floor(u / tp['terrace_width_m'] + 0.35 * self.meander(v, np.zeros_like(v)))
        elif kind == 'domains':
            f = self.dfield(x, y) - self.threshold
            frac = 0.5 + 0.5 * np.tanh(INTERFACE_SHARPNESS * f)
            z = tp['height_contrast_m'] * frac
            mat = (frac > 0.5).astype(np.int8)
        elif kind in ('particles', 'flakes', 'fibers'):
            margin = {'particles': 2 * tp.get('diameter_m', 0.0), 'flakes': 2 * tp.get('flake_size_m', 0.0),
                      'fibers': tp.get('contour_length_m', 0.0)}[kind]
            blk = self._block(x, y, margin)
            tiles = blk['tiles']
            pts = np.stack([x.ravel(), y.ravel()], axis=1)
            if kind == 'particles':
                if blk['tree'] is not None:
                    tree, rr = blk['tree'], blk['r']
                    kq = min(6, len(rr))
                    dist, idx = tree.query(pts, k=kq, distance_upper_bound=float(rr.max()))
                    dist, idx = dist.reshape(len(pts), kq), idx.reshape(len(pts), kq)
                    ok = np.isfinite(dist)
                    rsel = np.where(ok, rr[np.minimum(idx, len(rr) - 1)], 0.0)
                    dd = np.where(ok, dist, 0.0)
                    h = np.where(ok & (dd < rsel), rsel + np.sqrt(np.maximum(rsel ** 2 - dd ** 2, 0.0)), 0.0)
                    z = h.max(axis=1).reshape(x.shape)
                    mat = (z > 0).astype(np.int8)
            elif kind == 'flakes':
                best = np.zeros(x.shape)
                for t in tiles:
                    for k in range(len(t['x'])):
                        inside = _in_star_polygon(x - t['x'][k], y - t['y'][k], t['poly'][k])
                        h = (tp.get('interface_offset_m') or 0.0) + t['n'][k] * tp['layer_thickness_m']
                        best = np.where(inside, np.maximum(best, h), best)
                z = best
                mat = (z > 0).astype(np.int8)
            else:
                if blk['tree'] is not None:
                    d, _ = blk['tree'].query(pts, distance_upper_bound=4 * tp['width_m'])
                    sig = tp['width_m'] / 2.355
                    z = np.where(np.isfinite(d), tp['height_m'] * np.exp(-np.minimum(d, 1.0) ** 2 / (2 * sig * sig)), 0.0).reshape(x.shape)
                    mat = (z > 0.3 * tp['height_m']).astype(np.int8)
        if self.rough is not None:
            z = z + tp['rq_m'] * self.rough(x, y)
        z = z + self.tilt[0] * x + self.tilt[1] * y
        return z, mat


def _segment_clip_box(a, b, hx, hy):
    """Parameter interval (t0, t1) of the segment a + t (b - a), 0 <= t <= 1, inside the rectangle |x| <= hx, |y| <= hy (slab method), or None."""
    t0, t1 = 0.0, 1.0
    for i, h in ((0, hx), (1, hy)):
        d = b[i] - a[i]
        if abs(d) < 1e-18:
            if abs(a[i]) > h:
                return None
            continue
        u, v = sorted(((-h - a[i]) / d, (h - a[i]) / d))
        t0, t1 = max(t0, u), min(t1, v)
        if t0 > t1:
            return None
    return t0, t1


def _segment_clip_disc(a, b, r):
    """Parameter interval (t0, t1) of the segment a + t (b - a), 0 <= t <= 1, within r of the origin, or None."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    A, B, C = dx * dx + dy * dy, 2.0 * (a[0] * dx + a[1] * dy), a[0] * a[0] + a[1] * a[1] - r * r
    if A == 0.0:
        return (0.0, 0.0) if C <= 0.0 else None
    disc = B * B - 4.0 * A * C
    if disc < 0.0:
        return None
    q = math.sqrt(disc)
    t0, t1 = max(0.0, (-B - q) / (2.0 * A)), min(1.0, (-B + q) / (2.0 * A))
    return (t0, t1) if t0 <= t1 else None


def _segment_hits_box(a, b, hx, hy):
    """Whether the segment a-b meets the rectangle |x| <= hx, |y| <= hy (touching counts)."""
    return _segment_clip_box(a, b, hx, hy) is not None


def _segment_hits_disc(a, b, r):
    """Whether the segment a-b comes within r of the origin."""
    return _segment_clip_disc(a, b, r) is not None


def _in_star_polygon(dx, dy, poly):
    """Points (dx, dy relative to the centre) inside a star-shaped polygon whose vertices are sorted by angle about the centre."""
    rho = np.hypot(dx, dy)
    th = np.mod(np.arctan2(dy, dx), 2 * math.pi)
    va = np.mod(np.arctan2(poly[:, 1], poly[:, 0]), 2 * math.pi)
    order = np.argsort(va)
    va, pv = va[order], poly[order]
    i1 = np.searchsorted(va, th) % len(va)
    i0 = (i1 - 1) % len(va)
    a, b = pv[i0], pv[i1]
    d = np.stack([np.cos(th), np.sin(th)], axis=-1)
    e = b - a
    num = np.abs(a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0])
    den = np.abs(d[..., 0] * e[..., 1] - d[..., 1] * e[..., 0])
    return rho < num / np.maximum(den, 1e-30)


def tip_inverse(h, R, alpha):
    """Lateral distance at which the sphere-cone tip surface rises h above its apex."""
    rc = R * math.cos(alpha)
    hc = R - math.sqrt(max(R * R - rc * rc, 0.0))
    if h <= hc:
        return math.sqrt(max(2 * R * h - h * h, 0.0))
    return rc + (h - hc) * math.tan(alpha)


def dilate_rows(rows, t_prof):
    """Image of lines through the tip (grey dilation along the last axis): out[..., i] = max_j (rows[..., i + j] - t_prof[j]); rows carry
    m = (len(t_prof) - 1) / 2 samples of margin on both ends."""
    m = (len(t_prof) - 1) // 2
    if m == 0:
        return rows - t_prof[0]
    win = np.lib.stride_tricks.sliding_window_view(rows, 2 * m + 1, axis=-1)
    return np.max(win - t_prof, axis=-1)


def dilate_grid(grid, px, py, R, alpha, m):
    """2D grey dilation of a height grid (rows = slow axis) by the sphere-cone tip: out[j, i] = max_{k, s} grid[j + k, i + s] - t(sqrt((k py)^2 +
    (s px)^2)) for |k|, |s| <= m; grid carries m rows / columns of margin on every side, out has the interior shape."""
    ny = grid.shape[0] - 2 * m
    out = None
    s = np.arange(-m, m + 1) * px
    for k in range(-m, m + 1):
        prof = tip_profile(np.hypot(s, k * py), R, alpha)
        d = dilate_rows(grid[m + k:m + k + ny], prof)
        out = d if out is None else np.maximum(out, d)
    return out


# ---------------------------------------------------------------- probes: nominal values from the library, hidden actual values of each chip
E_SI, RHO_SI, NU_SI = 169e9, 2330.0, 0.27           # Si <110> cantilever beams (engineering)
E_SIN, RHO_SIN = 250e9, 3100.0                      # LPCVD Si3N4 (engineering)
BEAM_FACTOR = 0.1615                                # f0 = 0.1615 (t / L^2) sqrt(E / rho), k = E w t^3 / (4 L^3) (rectangular clamped-free beam)
DEFAULT_WIDTH_M = {'soft': 40e-6, 'stiff': 30e-6}   # when the library gives no width (k < 1 N/m: contact levers)
DEFAULT_TIP_RADIUS_M = 10e-9
DEFAULT_TIP_HEIGHT_M = 15e-6
K_SIGMA_LN_DEFAULT = 0.25                           # chip-to-chip spread of k when the maker gives no range (1 sigma of ln k)
F0_SIGMA_REL = 0.02
Q_SIGMA_LN = 0.10
LEVER_GAIN_SIGMA = 0.08                             # optical-lever gain of a mounted chip (chip angle, spot shape) relative to the nominal
PSPD_SPOT_W_M = 1.0e-3                              # reflected spot on the photodiode (erf half width)
KNOB_PSPD_M_PER_TURN = 0.6e-3                       # PSPD alignment knobs: photodiode translation per turn
PSPD_MOUNT_SIGMA_M = 0.9e-3                         # reflected spot of a new chip relative to the photodiode centre
KNOB_LIMIT_TURNS = {'beam_x': 15.0, 'beam_y': 15.0, 'pspd_x': 10.0, 'pspd_y': 10.0, 'optics_x': 10.0, 'optics_y': 10.0}
P_TIP_YIELD_PA = 12e9                               # a silicon apex yields when the Hertz peak pressure exceeds this (hardness of Si; blunting model)
D_HYST_M = 1.0e-9                                   # adhesion hysteresis path of the tapping cycle (neck / meniscus rupture distance)
TAP_PEAK_FACTOR = 10.0                              # peak repulsive force of tapping = 10 k A0 / Q sqrt(1 - r^2) (engineering)
TAP_ATTRACTIVE_MAX_A_M = 10e-9                      # tapping stays in the attractive regime below this free amplitude at a setpoint ratio > 0.85
NC_D_GRID_M = np.geomspace(0.25e-9, 80e-9, 240)     # closest-approach gaps of the True Non-Contact tables
NC_CONTACT_GAP_M = 0.3e-9                           # below: repulsive contact (the NC operating point is lost)
DETECTOR_V2_PER_HZ = (0.045e9 * 20e-15) ** 2        # optical-lever detection noise floor (20 fm / sqrt(Hz) at 45 mV/nm)
FD_POINTS = 512
RADIUS_MAX_M = 500e-9


def _reflective(coating):
    c = str(coating or '').lower()
    return any(tag in c for tag in ('al', 'au', 'reflex', 'pt', 'backside'))


@functools.lru_cache(maxsize=64)
def probe_nominal(type_id):
    """Nominal values of a probe type (SmartScan cantilever database = the maker's catalogue values of probe_library.json); missing geometry is
    derived from k and f0 of a rectangular beam (width assumed) and listed in 'derived'."""
    p = probes()['types'].get(type_id)
    if p is None:
        raise ActionRejected('UNKNOWN_PROBE_TYPE')
    f0, k = float(flat_value(p['resonance_frequency_hz'])), float(flat_value(p['spring_constant_n_m']))
    k_rng = p['spring_constant_n_m'].get('range') if isinstance(p['spring_constant_n_m'], dict) else None
    E, rho = (E_SIN, RHO_SIN) if p['material'] == 'Si3N4' else (E_SI, RHO_SI)
    c = BEAM_FACTOR * math.sqrt(E / rho)
    derived = []
    w = p.get('width_m')
    if w is None:
        w = DEFAULT_WIDTH_M['soft' if k < 1.0 else 'stiff']
        derived.append('width_m')
    L = p.get('length_m')
    if L is None:
        L = (4 * k * c ** 3 / (E * w * f0 ** 3)) ** (1.0 / 3.0)
        derived.append('length_m')
    t = p.get('thickness_m')
    if t is None:
        t = f0 * L * L / c
        derived.append('thickness_m')
    tr = p.get('tip_radius_m')
    R = flat_value(tr)
    bound = tr.get('bound') if isinstance(tr, dict) else None
    if R is None:
        R, bound = DEFAULT_TIP_RADIUS_M, 'typical'
        derived.append('tip_radius_m')
    alpha = p.get('tip_half_angle_rad')
    if alpha is None:
        alpha = TIP_HALF_ANGLE_RAD
        derived.append('tip_half_angle_rad')
    h = p.get('tip_height_m')
    if h is None:
        h = DEFAULT_TIP_HEIGHT_M
        derived.append('tip_height_m')
    return dict(type_id=type_id, name=p['name'], k=k, k_range=list(k_rng) if k_rng else None, f0=f0, L=float(L), w=float(w), t=float(t), R=float(R),
                R_bound=bound, alpha=float(alpha), tip_height=float(h), material=p['material'], coating=p.get('coating'),
                reflective=_reflective(p.get('coating')), modes=list(p.get('modes') or []), tip_material=tip_material(p), derived=derived)


def draw_probe_truth(nom, rng):
    """Hidden actual values of one chip: thickness factor tau (k ~ tau^3, f0 ~ tau) spread so that +-2 sigma of ln k covers the maker's range,
    quality factor in air, tip radius (below an upper bound or around a typical value), optical-lever gain."""
    kr = nom['k_range']
    s = math.log(kr[1] / kr[0]) / 4.0 if kr and kr[0] > 0 and kr[1] > kr[0] else K_SIGMA_LN_DEFAULT
    tau = math.exp(s * float(rng.standard_normal()) / 3.0)
    f0 = nom['f0'] * tau * (1.0 + F0_SIGMA_REL * float(rng.standard_normal()))
    if nom['R_bound'] == 'upper':
        R = nom['R'] * float(rng.uniform(0.6, 1.0))
    else:
        R = nom['R'] * max(0.4, 1.0 + 0.15 * float(rng.standard_normal()))
    return dict(k=nom['k'] * tau ** 3, f0=f0, Q=quality_factor(f0) * math.exp(Q_SIGMA_LN * float(rng.standard_normal())), R0=R, alpha=nom['alpha'],
                L=nom['L'], w=nom['w'], tip_height=nom['tip_height'], lever_gain=1.0 + LEVER_GAIN_SIGMA * float(rng.standard_normal()),
                reflective=nom['reflective'], tip_material=nom['tip_material'])


# ---------------------------------------------------------------- beam detection
def spot_fraction(x, y, L, w, sigma=SPOT_SIGMA_M):
    """Fraction of the Gaussian SLD spot on the cantilever (rectangle from its free end x = 0 back to the chip x = -L, width w about y = 0)."""
    s2 = math.sqrt(2.0) * sigma
    fy = 0.5 * (math.erf((w / 2 - y) / s2) + math.erf((w / 2 + y) / s2))
    fx = 0.5 * (math.erf(-x / s2) + math.erf((x + L) / s2))
    return max(0.0, fx * fy)


def lever_sensitivity(intensity_v, x, L, gain):
    """VERTICAL signal per cantilever deflection (V/m): optical lever of an end-loaded beam with the spot at xi = 1 + x / L (slope ~ 2 xi - xi^2),
    proportional to the reflected intensity and inversely to the cantilever length."""
    xi = min(1.0, max(0.0, 1.0 + x / L))
    return SENSITIVITY_V_PER_M_100UM * (intensity_v / INTENSITY_COATED_V) * gain * max(2 * xi - xi * xi, 0.02) * (100e-6 / L)


def pspd_signal(intensity_v, p, S, d):
    """Difference signal of the quadrant photodiode (V) for the reflected spot at p (m from the diode centre) and cantilever deflection d (m):
    I erf(p / W + d S sqrt(pi) / (2 I)) - slope S at the centre, saturating at +-I."""
    if intensity_v <= 0:
        return 0.0 * np.asarray(d) if np.ndim(d) else 0.0
    from scipy.special import erf
    return intensity_v * erf(p / PSPD_SPOT_W_M + np.asarray(d) * S * math.sqrt(math.pi) / (2 * intensity_v))


def pspd_inverse(intensity_v, p, S, v):
    """Deflection giving the VERTICAL signal v (None when v is outside the diode range)."""
    if intensity_v <= 0 or abs(v) >= intensity_v:
        return None
    from scipy.special import erfinv
    return (float(erfinv(v / intensity_v)) - p / PSPD_SPOT_W_M) * 2 * intensity_v / (S * math.sqrt(math.pi))


# ---------------------------------------------------------------- thermal tune (equipartition with the optical-lever factor)
def sho_psd(f, A, f0, Q, white):
    return white + A * f0 ** 4 / ((f * f - f0 * f0) ** 2 + (f * f0 / Q) ** 2)


def thermal_mean_square_v2(S, k, T):
    """<V^2> of the first flexural mode: S^2 <z*^2> with <z*^2> = 0.817 kB T / k (Butt and Jaschke 1995, statically calibrated optical lever)."""
    return S * S * OPTICAL_LEVER_CHI * KB * T / k


def thermal_spectrum(f, S, k, f0, Q, T, averages, rng):
    msq = thermal_mean_square_v2(S, k, T)
    P = sho_psd(f, msq * 2.0 / (math.pi * f0 * Q), f0, Q, DETECTOR_V2_PER_HZ * (S / SENSITIVITY_V_PER_M_100UM) ** 2 + 1e-18)
    return P * rng.gamma(averages, 1.0 / averages, len(f))


def fit_sho(f, P, averages):
    """Least squares of ln P to the damped harmonic oscillator plus a white floor (bias of ln of an M-average removed); returns the fit and the
    area of the resonance (V^2) with its relative standard error, or None when no resonance is resolved."""
    from scipy.optimize import least_squares
    from scipy.special import digamma
    if len(f) < 16 or not np.all(np.isfinite(P)) or np.any(P <= 0):
        return None
    sm = np.convolve(P, np.ones(9) / 9, mode='same')
    i = int(np.argmax(sm[4:-4])) + 4
    white = float(np.median(np.sort(P)[:max(4, len(P) // 5)]))
    peak = float(sm[i]) - white
    if peak <= 3 * white:
        return None
    half = np.nonzero(sm > white + peak / 2)[0]
    width = max(float(f[half[-1]] - f[half[0]]), float(f[1] - f[0]))
    f0g = float(f[i])
    Qg = max(2.0, f0g / width)
    x0 = np.array([math.log(peak / Qg ** 2), math.log(f0g), math.log(Qg), math.log(white)])
    bias = float(digamma(averages) - math.log(averages))
    lp = np.log(P)

    def res(v):
        A, f0, Q, w = np.exp(v)
        return lp - bias - np.log(sho_psd(f, A, f0, Q, w))
    try:
        sol = least_squares(res, x0, method='lm', max_nfev=400)
    except (ValueError, FloatingPointError):
        return None
    A, f0, Q, w = (float(x) for x in np.exp(sol.x))
    if not (f[0] < f0 < f[-1]) or not all(math.isfinite(x) for x in (A, f0, Q, w)):
        return None
    J = sol.jac
    s2 = float(np.sum(sol.fun ** 2) / max(1, len(f) - 4))
    try:
        cov = np.linalg.inv(J.T @ J) * s2
        rel = float(math.sqrt(max(0.0, cov[0, 0] + cov[1, 1] + cov[2, 2] + 2 * (cov[0, 1] + cov[0, 2] + cov[1, 2]))))
    except np.linalg.LinAlgError:
        rel = None
    return dict(A=A, f0=f0, Q=Q, white=w, area=A * math.pi * f0 * Q / 2.0, area_rel_se=rel)


# ---------------------------------------------------------------- driven cantilever (frequency sweep, tapping, True Non-Contact)
def lorentz_amp(f, f0, Q):
    """Amplitude response relative to the response at resonance (1 at f0)."""
    x = np.asarray(f, float) / f0
    return 1.0 / (Q * np.sqrt((1 - x * x) ** 2 + (x / Q) ** 2))


def lorentz_phase(f, f0, Q):
    """Phase lag of the driven oscillator (rad): 0 far below, pi / 2 at, pi far above resonance."""
    x = np.asarray(f, float) / f0
    return np.arctan2(x / Q, 1 - x * x)


_TH = np.linspace(0.0, 2 * math.pi, 96, endpoint=False)


def nc_shift(D, A, k, f0, C):
    """Frequency shift of an oscillation with amplitude A whose lower turning point is D above the surface in the van der Waals field
    F = -C / z^2 (C = H R / 6): Delta f = -(f0 / (k A)) <F(D + A (1 + cos th)) cos th> (Giessibl 1997); small A -> -f0 F'(D + A) / (2 k)."""
    D, A = np.asarray(D, float), np.asarray(A, float)
    z = D[..., None] + A[..., None] * (1 + np.cos(_TH))
    F = -C / np.maximum(z, VDW_CUTOFF_M) ** 2
    return -(f0 / (k * A)) * np.mean(F * np.cos(_TH), axis=-1)


def nc_table(k, f0, Q, A_res, f_drive, C, D=NC_D_GRID_M):
    """Amplitude, phase lag and frequency shift of the cantilever driven at f_drive (drive strength giving A_res at resonance) as a function of the
    closest-approach gap D (self-consistent: the shift depends on the amplitude)."""
    A = np.full(len(D), A_res * float(lorentz_amp(f_drive, f0, Q)))
    for _ in range(40):
        df = nc_shift(D, A, k, f0, C)
        A = 0.5 * A + 0.5 * A_res * lorentz_amp(f_drive, f0 + df, Q)
    df = nc_shift(D, A, k, f0, C)
    return dict(D=np.asarray(D, float), A=A, phase=lorentz_phase(f_drive, f0 + df, Q), df=df)


def nc_operating_point(tab, A_set):
    """Largest gap at which the amplitude has dropped to the setpoint (approaching from far), the local slope dA/dD there; None when the amplitude
    does not fall to the setpoint before contact (e.g. the drive below the resonance: the attraction raises the amplitude first)."""
    D, A = tab['D'], tab['A']
    below = np.nonzero(A <= A_set)[0]
    if len(below) == 0:
        return None
    j = int(below[-1])
    if j >= len(D) - 1 or D[j] < NC_CONTACT_GAP_M:
        return None
    if np.any(np.diff(A[j:]) < -1e-15 * A_set):                        # the amplitude must rise monotonically back to the free value
        return None
    x = float(np.interp(A_set, [A[j], A[j + 1]], [D[j], D[j + 1]]))
    slope = float((A[j + 1] - A[j]) / (D[j + 1] - D[j]))
    return dict(gap=x, slope=max(slope, 1e-6))


# ---------------------------------------------------------------- force-distance curves (DMT contact, van der Waals approach, pull-off)
def _contact_indentation(c, a, k):
    """delta >= 0 solving a delta^1.5 + k delta = c (vectorized bisection); c <= 0 gives 0."""
    c = np.maximum(np.asarray(c, float), 0.0)
    hi = np.minimum(c / k, (c / a) ** (2.0 / 3.0)) + 1e-18
    lo = np.zeros_like(c)
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        g = a * mid ** 1.5 + k * mid - c
        lo = np.where(g < 0, mid, lo)
        hi = np.where(g < 0, hi, mid)
    return 0.5 * (lo + hi)


def fd_branches(z_rel, k, R, Es, F_ad, C):
    """Deflection d (m, + = repulsive) along the approach and the retract of the cantilever base, z_rel = base height above the surface for the
    undeflected tip (approach: decreasing values, retract: the reverse). Approach: van der Waals equilibrium k d = -C / D^2 until the jump at
    z_rel = 1.5 D_j (D_j = (2 C / k)^(1/3)), then DMT contact a delta^1.5 - F_ad = k d (a = 4/3 E* sqrt R); retract: contact until the pull-off at
    z_rel = F_ad / k (force -F_ad), then free."""
    z_rel = np.asarray(z_rel, float)
    a = 4.0 / 3.0 * Es * math.sqrt(R)
    Dj = (2.0 * C / k) ** (1.0 / 3.0) if C > 0 else 0.0

    def free(z):
        d = np.zeros_like(z)
        for _ in range(60):
            d = -C / (k * np.maximum(z + d, VDW_CUTOFF_M) ** 2)
        return d

    def contact(z):
        delta = _contact_indentation(F_ad - k * z, a, k)
        return -z - delta, delta

    jump = np.nonzero(z_rel <= 1.5 * Dj)[0]
    ja = int(jump[0]) if len(jump) else len(z_rel)
    d_app = np.empty_like(z_rel)
    d_app[:ja] = free(z_rel[:ja])
    d_app[ja:], _ = contact(z_rel[ja:])
    z_ret = z_rel[::-1]
    off = np.nonzero(z_ret > F_ad / k)[0]
    jr = int(off[0]) if len(off) else len(z_ret)
    d_ret = np.empty_like(z_ret)
    d_ret[:jr], _ = contact(z_ret[:jr])
    d_ret[jr:] = free(z_ret[jr:])
    return d_app, d_ret, dict(jump_in_z_m=float(1.5 * Dj), pull_off_z_m=float(F_ad / k), jump_index=ja, pull_off_index=jr)


def hertz_peak_pressure(F, R, Es):
    return (6.0 * max(F, 0.0) * Es * Es / (math.pi ** 3 * R * R)) ** (1.0 / 3.0)


def blunted_radius(F, R, Es):
    """Tip radius after a load F: unchanged while the Hertz peak pressure stays below the yield pressure of the apex, otherwise flattened to the
    radius at which it equals it."""
    if hertz_peak_pressure(F, R, Es) <= P_TIP_YIELD_PA:
        return R
    return min(RADIUS_MAX_M, math.sqrt(6.0 * F * Es * Es / (math.pi ** 3 * P_TIP_YIELD_PA ** 3)))


def reduced_to_sample(Es, nu_s, tip_E, tip_nu):
    inv = 1.0 / Es - (1 - tip_nu ** 2) / tip_E
    return (1 - nu_s ** 2) / inv if inv > 0 else None


def fit_fd(z, F, k, R, nu_s, tip_E, tip_nu, model, retract):
    """DMT / Hertz fit of one branch of a force curve given as base displacement z (m) and force F (N, displayed with the calibrated k and
    sensitivity). Indentation delta = z0 - z - F / k; DMT: F + F_ad = a delta^1.5 with F_ad the pull-off force (minimum of the branch),
    Hertz: F = a delta^1.5 on the repulsive part; a = 4/3 E* sqrt(R), fitted linearly as (F + F_ad)^(2/3) = a^(2/3) (z0 - z - F / k) over 5-95 % of
    the contact force range (approach: after the jump-in, retract: before the pull-off)."""
    z, F = np.asarray(z, float), np.asarray(F, float)
    warn = []
    i_min = int(np.argmin(F))
    part = np.arange(i_min, len(F)) if not retract else np.arange(0, i_min + 1)
    F_ad = max(0.0, -float(F[i_min])) if model == 'dmt' else 0.0
    Fp, zp = F[part], z[part]
    lo, hi = float(Fp.min()), float(Fp.max())
    sel = (Fp >= lo + 0.05 * (hi - lo)) & (Fp <= lo + 0.95 * (hi - lo)) & (Fp + F_ad > 0)
    if model == 'hertz':
        sel &= Fp > 0
    if sel.sum() < 8 or hi - lo <= 0:
        return None
    y = (Fp[sel] + F_ad) ** (2.0 / 3.0)
    x = zp[sel] + Fp[sel] / k
    A = np.vstack([np.ones_like(x), x]).T
    (c0, c1), *_ = np.linalg.lstsq(A, y, rcond=None)
    if not c1 < 0:
        return None
    a = (-c1) ** 1.5
    z0 = c0 / (-c1)
    Es = a * 3.0 / (4.0 * math.sqrt(R))
    delta = np.maximum(z0 - zp[sel] - Fp[sel] / k, 0.0)
    resid = float(np.sqrt(np.mean((Fp[sel] + F_ad - a * delta ** 1.5) ** 2)))
    if float(delta.max()) < 0.1e-9:
        warn.append('indentation_below_noise')
    E = reduced_to_sample(Es, nu_s, tip_E, tip_nu)
    if E is None:
        warn.append('sample_modulus_not_resolved')
    return dict(reduced_modulus_pa=float(Es), sample_modulus_pa=None if E is None else float(E), adhesion_n=float(F_ad), contact_point_m=float(z0),
                max_indentation_m=float(delta.max()), points=int(sel.sum()), residual_n=resid, warnings=warn)


# ---------------------------------------------------------------- imaging engine
BLOCK_LINES = 16                                    # lines are computed in fixed blocks of 16 (the block parameters - tip radius, lateral drift - are taken
                                                    # at the block start, so the image does not depend on how the host chops the steps)
DILATION_WORK_PER_BLOCK = 6e7                      # tip dilation budget per block of lines (offsets x pixels): +-24 px at 2048 columns, ~+-42 px at 512
DILATION_HARD_MAX_PX = 64
DILATION_MAX_PX = 24                                # tip footprint limit in pixels (+-24: 2401 offsets per pixel; simulation limit)
Z_PIEZO_M_PER_V = 0.1e-6                            # Z scanner: 15 um at 150 V (engineering; the Z drive channel is the voltage)
VIB_TAU_S = 0.005                                   # correlation time of floor / acoustic vibration (engineering)
PHASE_NOISE_RAD = 1e-3
THIN_FEATURES = ('particles', 'flakes', 'fibers')    # objects of a second material resting on the substrate
THIN_INDENT_FRACTION = 0.5                          # their indentation beyond that of the substrate is capped at this fraction of their height (engineering)
SIM_MAX_PIXELS = 2048 * 2048                        # simulation limit of one image (per channel); the NX10 itself stores up to 4096 x 4096
ENCLOSURE_VIBRATION = 0.25                          # acoustic enclosure: acoustic part x 0.25 (engineering)
ISOLATION_VIBRATION = 0.15                          # active vibration isolation: floor part x 0.15 (engineering)
ENCLOSURE_DRIFT = 0.5
ACOUSTIC_SHARE = 0.6                                # share of the open-system vibration variance that is acoustic


def vibration_rms(plant, burst):
    a = VIBRATION_OPEN_M * math.sqrt(ACOUSTIC_SHARE) * (ENCLOSURE_VIBRATION if plant['acoustic_enclosure'] else 1.0)
    f = VIBRATION_OPEN_M * math.sqrt(1 - ACOUSTIC_SHARE) * (ISOLATION_VIBRATION if plant['vibration_isolation'] else 1.0)
    return math.hypot(a, f) * (VIBRATION_BURST_FACTOR if burst else 1.0)


DRIFT_SETTLE_S = 1800.0                             # thermal settling time constant after a disturbance (engineering)
DRIFT_INITIAL_FACTOR = 10.0                         # drift right after a disturbance relative to the nominal rate; 0.3 of it remains after settling


def drift_displacement(t_since, rate):
    """Thermal drift after a disturbance (sample loading, power-on, head attach): speed rate (0.3 + 9.7 exp(-t / 1800 s)) - ten times the nominal
    rate at first, 0.3 of it after settling - integrated."""
    t = max(0.0, t_since)
    return rate * (0.3 * t + (DRIFT_INITIAL_FACTOR - 0.3) * DRIFT_SETTLE_S * (1.0 - math.exp(-t / DRIFT_SETTLE_S)))


def mode_params(mode, mats, probe, cal, drive, setpoint, humidity, I, S_true, p_v):
    """Per-material operating point of the feedback at the setpoint (true values from the hidden probe and the calibration used by SmartScan):
    offset of the scanner target relative to the surface, error clamp, and the constants of the error / phase channels."""
    k, f0, Q, R = probe['k'], probe['f0'], probe['Q'], probe['R']
    out = dict(mode=mode, mats={}, warnings=[])
    if mode == 'contact':
        dV = setpoint * cal['sensitivity_v_per_m'] / cal['spring_constant_n_m']        # SmartScan: set point (N) -> VERTICAL change (V)
        V_free = float(pspd_signal(I, p_v, S_true, 0.0)) if I > 0 else 0.0
        d_inv = pspd_inverse(I, p_v, S_true, V_free + dV) if I > 0 and S_true > 0 and abs(V_free + dV) < SAT_LIMIT * I else None
        d_set = d_inv if d_inv is not None else dV / max(S_true, 1e-30)      # the feedback holds VERTICAL = V_free + dV on the erf response (else: crash)
        out.update(I=I, p_v=p_v, V_free=V_free, V_set=V_free + dV)
        F = k * d_set
        d_sat = pspd_inverse(I, p_v, S_true, 0.98 * I) if I > 0 else None
        out.update(d_set=d_set, force_n=F, e_free=d_set, e_hard=max((d_sat if d_sat is not None else 0.0) - d_set, 0.0) if d_sat is not None else 0.0)
        for i, m in enumerate(mats):
            Es = reduced_modulus(probe['tip_material'], m)
            Fad = adhesion_force(R, m, humidity)
            out['mats'][i] = dict(offset=-(d_set + dmt_indentation(F, R, Es, Fad)), Es=Es, F_ad=Fad, force=F, E_dis=0.0)
        return out
    A_res = drive['A_res']                                                              # the drive strength: true free amplitude at resonance
    A_free = A_res * float(lorentz_amp(drive['frequency_hz'], f0, Q))                  # the physical free amplitude of the mounted probe
    A_set = setpoint * cal['sensitivity_v_per_m'] / S_true                            # the lock-in holds A S_true at the set point x S_cal
    r = A_set / A_free if A_free > 0 else 1.0
    out.update(A_free=A_free, A_set=A_set, A_res=A_res, ratio=r, f_drive=drive['frequency_hz'])
    if mode == 'tapping':
        attractive = A_free < TAP_ATTRACTIVE_MAX_A_M and r > 0.85
        F_pk = 0.0 if attractive else TAP_PEAK_FACTOR * k * A_free / Q * math.sqrt(max(0.0, 1 - r * r))
        out.update(regime='attractive' if attractive else 'repulsive', peak_force_n=F_pk, e_free=max(A_free - A_set, 0.0), e_hard=A_set)
        for i, m in enumerate(mats):
            Es = reduced_modulus(probe['tip_material'], m)
            Fad = adhesion_force(R, m, humidity)
            dp = 0.0 if attractive else dmt_indentation(F_pk, R, Es, Fad)
            E_dis = (0.0 if attractive else Fad * D_HYST_M + math.pi * material(m)['tan_delta'] * 0.4 * F_pk * dp)
            out['mats'][i] = dict(offset=A_set - dp, Es=Es, F_ad=Fad, force=F_pk, E_dis=E_dis)
        return out
    # True Non-Contact: attractive van der Waals regime with the amplitude-distance tables
    out.update(regime='non_contact', e_hard=0.0, e_free=0.0)
    for i, m in enumerate(mats):
        C = hamaker_ts(m) * R / 6.0
        tab = nc_table(k, f0, Q, A_res, drive['frequency_hz'], C)
        op = nc_operating_point(tab, A_set)
        Es = reduced_modulus(probe['tip_material'], m)
        Fad = adhesion_force(R, m, humidity)
        if op is None:
            out['regime'] = 'contact_regime'
            F_pk = TAP_PEAK_FACTOR * k * A_free / Q * math.sqrt(max(0.0, 1 - min(r, 1.0) ** 2))
            dp = dmt_indentation(F_pk, R, Es, Fad)
            out['mats'][i] = dict(offset=A_set - dp, Es=Es, F_ad=Fad, force=F_pk, E_dis=Fad * D_HYST_M, tab=None, gap=None, slope=1.0)
            out['e_free'] = max(out['e_free'], A_free - A_set)
            out['e_hard'] = max(out['e_hard'], A_set)
        else:
            out['mats'][i] = dict(offset=A_set + op['gap'], Es=Es, F_ad=Fad, force=C / op['gap'] ** 2, E_dis=0.0, tab=tab, gap=op['gap'], slope=op['slope'])
            out['e_free'] = max(out['e_free'], (A_free - A_set) / op['slope'])
            out['e_hard'] = max(out['e_hard'], op['gap'])
    if out['regime'] == 'contact_regime':
        out['warnings'].append('nc_contact_regime')
    return out


def _servo_step(e0, ydot, dt, w, lo, hi):
    """Error after one pixel interval of the first-order Z feedback with a saturating error signal, exact for a target moving at the constant
    rate ydot: linear range de/dt = ydot - w e (relaxation to ydot / w), saturated ranges de/dt = ydot - w lo (or hi): constant-rate correction;
    the switching instants inside the interval are solved exactly."""
    ess = ydot / w
    c_lo, c_hi = ydot - w * lo, ydot - w * hi
    below, above = e0 < lo, e0 > hi
    t_in = np.zeros_like(e0)
    t_in = np.where(below, np.where(c_lo > 0, (lo - e0) / np.where(c_lo > 0, c_lo, 1.0), np.inf), t_in)
    t_in = np.where(above, np.where(c_hi < 0, (hi - e0) / np.where(c_hi < 0, c_hi, -1.0), np.inf), t_in)
    stay = (below | above) & (t_in >= dt)
    e_sat = np.where(below, e0 + c_lo * dt, e0 + c_hi * dt)
    e_in = np.where(below, lo, np.where(above, hi, e0))
    tau = dt - np.minimum(t_in, dt)
    m_lo, m_hi = ess < lo, ess > hi
    ratio = np.where(m_lo, (e_in - ess) / np.where(m_lo, lo - ess, 1.0), np.where(m_hi, (e_in - ess) / np.where(m_hi, hi - ess, 1.0), 1.0))
    t_out = np.where(m_lo | m_hi, np.log(np.maximum(ratio, 1.0)) / w, np.inf)
    e_lin = ess + (e_in - ess) * np.exp(-w * tau)
    e_exit = np.where(m_lo, lo + c_lo * (tau - t_out), hi + c_hi * (tau - t_out))
    e_after = np.where(t_out < tau, e_exit, e_lin)
    return np.where(stay, e_sat, e_after)


def _servo(y, dt, omega, e_lo, e_hi, z_hi):
    """First-order Z feedback (integral servo of bandwidth omega) along the rows of y (lines x pixels), exact between pixels for the
    piecewise-linear target (linear error signal: exponential relaxation; saturated error signal: constant-rate correction - the tip parachutes
    off down-steps or is pressed beyond the signal range); the scanner saturates at 0 and z_hi. Returns (z, e)."""
    n = y.shape[1]
    z = np.empty_like(y)
    e = np.empty_like(y)
    z[:, 0] = np.clip(y[:, 0], 0.0, z_hi)
    e[:, 0] = y[:, 0] - z[:, 0]
    for i in range(1, n):
        en = _servo_step(e[:, i - 1], (y[:, i] - y[:, i - 1]) / dt, dt, omega, e_lo, e_hi)
        zi = np.clip(y[:, i] - en, 0.0, z_hi)
        z[:, i] = zi
        e[:, i] = y[:, i] - zi
    return z, e


def _ar1_lines(rngs, n, rho, sigmas):
    """Stationary AR(1) sequences (one per line generator): x_i = rho x_(i-1) + sqrt(1 - rho^2) w_i, RMS sigma."""
    from scipy.signal import lfilter
    w = np.stack([r.standard_normal(n + 1) for r in rngs])
    x = lfilter([math.sqrt(1 - rho * rho)], [1.0, -rho], w[:, 1:], axis=1, zi=(rho * w[:, :1]))[0]
    return x * np.asarray(sigmas)[:, None]


def _line_noise(rngs, n):
    return np.stack([r.standard_normal(n) for r in rngs])


def image_block(job, j0, j1, R, drift_xy, drift_z_of, burst_of, rng_of):
    """Lines j0..j1-1 of a scan: tip-dilated topography on the band of rows around them, feedback along every line (trace; retrace when it is
    saved, else its statistics are taken equal to the trace), channels, force statistics for the wear. job holds the scan definition fixed at
    its start; R is the tip radius and drift_xy the lateral drift at the block start; every line draws its noise from its own generator."""
    from scipy.signal import lfilter
    S, nx = job['size_m'], job['pixels_x']
    px, py = S / nx, S / job['pixels_y']
    mp = job['mp']
    alpha = job['probe']['alpha']
    gaps = [v['gap'] for v in mp['mats'].values() if v.get('gap')]
    Reff = R + (min(gaps) if gaps else 0.0)
    H = job['surface'].characteristic_height()
    nl = j1 - j0
    m_need = int(math.ceil(tip_inverse(H, Reff, alpha) / min(px, py)) + 1)
    m_budget = int((math.sqrt(DILATION_WORK_PER_BLOCK / max(1, BLOCK_LINES * nx)) - 1) / 2)
    m = min(m_need, min(DILATION_HARD_MAX_PX, max(DILATION_MAX_PX, m_budget)))
    u = (np.arange(-m, nx + m) + 0.5) * px - S / 2
    v = (np.arange(j0 - m, j1 + m) + 0.5) * py - S / 2
    U, V = np.meshgrid(u, v)
    c, s = math.cos(job['angle_rad']), math.sin(job['angle_rad'])
    X = job['p0'][0] + job['offset'][0] + c * U - s * V + drift_xy[0]
    Y = job['p0'][1] + job['offset'][1] + s * U + c * V + drift_xy[1]
    h, mat = job['surface'].evaluate(X, Y)
    hd = dilate_grid(h, px, py, Reff, alpha, m)
    mat_px = mat[m:m + nl, m:m + nx].astype(int)
    Uc, Vc = U[m:m + nl, m:m + nx], V[m:m + nl, m:m + nx]
    xs, ys = job['offset'][0] + c * Uc - s * Vc, job['offset'][1] + s * Uc + c * Vc          # scanner coordinates (bow)
    bow = XY_BOW_M * (xs ** 2 + ys ** 2) / (XY_SCAN_RANGE_M / 2) ** 2
    off = np.zeros(mat_px.shape)
    tilt = job['surface'].tilt
    feature = (np.maximum(h[m:m + nl, m:m + nx] - tilt[0] * X[m:m + nl, m:m + nx] - tilt[1] * Y[m:m + nl, m:m + nx], 0.0)
               if job['surface'].kind in THIN_FEATURES else None)
    for i, pm in mp['mats'].items():
        o = pm['offset']
        if i > 0 and feature is not None:                                                   # a thin object on a hard substrate: the substrate supports it,
            o = np.maximum(o, mp['mats'][0]['offset'] - THIN_INDENT_FRACTION * feature)       # the extra indentation stays below half its height
        off = np.where(mat_px == i, o, off)
    tl = job['t0'] + (np.arange(j0, j1) + 0.25) / job['rate_hz']                           # mid-time of each trace
    zdrift = np.array([drift_z_of(t) for t in tl])
    base = Z_SCAN_RANGE_M - TIP_Z0 - job['q_z'] + job['sample_top_z']
    y = base + hd + bow + off + zdrift[:, None]
    dt = 1.0 / (2.0 * job['rate_hz'] * nx)
    omega = 2 * math.pi * job['z_servo_gain'] * Z_SERVO_F0_HZ[mp['mode']]
    e_lo = -mp['e_free']
    e_hi = mp['e_hard'] if mp['e_hard'] > 0 else 1.0                                      # contact without a PSPD limit: no upper saturation
    out = {ch: {} for ch in job['channels']}
    stats = dict(force_sum=0.0, force_max=0.0, sat_free=0, sat_hard=0, scanner_sat=0, n=0,
                 Es_max=max(pm['Es'] for pm in mp['mats'].values()), footprint_truncated=m_need > m)
    osc = max(0.0, job['z_servo_gain'] / Z_SERVO_CRITICAL_GAIN[mp['mode']] - 1.0)
    sig_v = [vibration_rms(job['plant'], burst_of(t)) for t in tl]
    rho = math.exp(-dt / VIB_TAU_S)
    passes = ('trace', 'retrace') if job['save_retrace'] else ('trace',)
    weight = 1 if job['save_retrace'] else 2
    for direction in passes:
        rev = direction == 'retrace'
        yy = y[:, ::-1] if rev else y
        matd = mat_px[:, ::-1] if rev else mat_px
        rngs = [rng_of(j, direction) for j in range(j0, j1)]
        z, e = _servo(yy + _ar1_lines(rngs, nx, rho, sig_v), dt, omega, e_lo, e_hi, Z_SCAN_RANGE_M)
        if osc > 0:
            tt = np.arange(nx) * dt
            ph = np.array([r.random() * 2 * math.pi for r in rngs])
            f_osc = Z_SERVO_F0_HZ[mp['mode']] * Z_SERVO_CRITICAL_GAIN[mp['mode']]
            wob = OSC_NOISE_M * osc * np.sin(2 * math.pi * f_osc * tt[None, :] + ph[:, None])
            z, e = z + wob, e - wob
        ec = np.clip(e, e_lo, e_hi)
        stats['sat_free'] += weight * int(np.sum(e <= e_lo + 1e-15))
        stats['sat_hard'] += weight * int(np.sum(e >= e_hi - 1e-15))
        stats['scanner_sat'] += weight * int(np.sum((z <= 0.0) | (z >= Z_SCAN_RANGE_M)))
        stats['n'] += weight * e.size
        if mp['mode'] == 'contact':
            force = job['probe']['k'] * (mp['d_set'] + ec)
        else:
            force = np.zeros(e.shape)
            for i, pm in mp['mats'].items():
                force = np.where(matd == i, pm['force'], force)
            force = force + np.where(e > e_hi, job['probe']['k'] * (e - e_hi), 0.0)
        stats['force_sum'] += weight * float(np.sum(np.maximum(force, 0.0)))
        stats['force_max'] = max(stats['force_max'], float(np.max(force)))
        ch = {}
        if 'height' in out:
            ch['height'] = z + _line_noise(rngs, nx) * Z_DETECTOR_NOISE_M
        if 'error' in out:
            if mp['mode'] == 'contact':                                   # VERTICAL - set point on the erf response of the photodiode (V)
                ch['error'] = (pspd_signal(mp['I'], mp['p_v'], job['S_true'], mp['d_set'] + ec + _line_noise(rngs, nx) * DEFLECTION_NOISE_M) - mp['V_set']
                               if mp.get('I') else job['S_true'] * (ec + _line_noise(rngs, nx) * DEFLECTION_NOISE_M))
            elif mp['mode'] == 'non_contact' and mp['regime'] == 'non_contact':   # lock-in amplitude signal - set point (V)
                amp = np.zeros(e.shape)
                for i, pm in mp['mats'].items():
                    gap = np.maximum(pm['gap'] - ec, NC_D_GRID_M[0])
                    amp = np.where(matd == i, np.interp(gap, pm['tab']['D'], pm['tab']['A']), amp)
                ch['error'] = (amp - mp['A_set'] + _line_noise(rngs, nx) * DEFLECTION_NOISE_M) * job['S_true']
            else:
                ch['error'] = (-ec + _line_noise(rngs, nx) * DEFLECTION_NOISE_M) * job['S_true']
        if 'phase' in out:
            ch['phase'] = _phase_channel(mp, job, ec, matd) + _line_noise(rngs, nx) * PHASE_NOISE_RAD
        if 'z_drive' in out:
            ext = Z_SCAN_RANGE_M - z
            g = 1 - math.exp(-dt / CREEP_TAU_S)
            cr = lfilter([g], [1.0, -(1 - g)], ext, axis=1, zi=((1 - g) * ext[:, :1]))[0]
            ch['z_drive'] = (ext + CREEP_FRACTION * (ext - cr)) / Z_PIEZO_M_PER_V
        for name, arr in ch.items():
            out[name][direction] = arr[:, ::-1] if rev else arr
    return out, stats


def _phase_channel(mp, job, ec, matd):
    """Tapping: Cleveland et al. 1998, sin(phi) = (f / f0)(A / A0) + Q E_dis / (pi k A A0) with the local amplitude A = A_set - e (repulsive branch
    phi < pi / 2, attractive branch pi - asin); True Non-Contact: the phase lag of the resonance shifted by the local force gradient."""
    k, f0, Q = job['probe']['k'], job['probe']['f0'], job['probe']['Q']
    out = np.zeros(ec.shape)
    if mp['mode'] == 'non_contact' and mp['regime'] == 'non_contact':
        for i, pm in mp['mats'].items():
            gap = np.maximum(pm['gap'] - ec, NC_D_GRID_M[0])
            out = np.where(matd == i, np.interp(gap, pm['tab']['D'], pm['tab']['phase']), out)
        return out
    A0 = mp['A_free']
    A = np.clip(mp['A_set'] - ec, 1e-12, A0)
    for i, pm in mp['mats'].items():
        sphi = np.clip(mp['f_drive'] / f0 * A / A0 + Q * pm['E_dis'] / (math.pi * k * A * A0), -1.0, 1.0)
        ph = np.arcsin(sphi)
        if mp.get('regime') == 'attractive':
            ph = math.pi - ph
        out = np.where(matd == i, ph, out)
    return out


# ---------------------------------------------------------------- SmartAnalysis-like processing
FLATTEN = ['none', 'plane', 'plane_upper', 'line_0', 'line_1', 'line_2', 'line_upper']


def _plane_fit(z, mask):
    ny, nx = z.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    A = np.column_stack([np.ones(z.size), xx.ravel(), yy.ravel()])
    good = mask.ravel() & np.isfinite(z.ravel())
    coef, *_ = np.linalg.lstsq(A[good], z.ravel()[good], rcond=None)
    return (A @ coef).reshape(z.shape)


def flatten_image(img, method):
    """Plane or line-by-line polynomial background removal. The *_upper methods refit using only the pixels above the median residual (three
    iterations): the lower level (pits, trenches, substrate between particles) does not bias the background of a two-level surface, as when the
    lower regions are excluded in XEI."""
    z = np.asarray(img, float)
    ny, nx = z.shape
    if method == 'none':
        return z.copy()
    if method in ('plane', 'plane_upper'):
        bg = _plane_fit(z, np.ones(z.shape, bool))
        if method == 'plane_upper':
            for _ in range(3):
                r = z - bg
                bg = _plane_fit(z, r >= np.nanmedian(r))
        return z - bg
    x = np.linspace(-1.0, 1.0, nx)
    if method == 'line_upper':
        V = np.vander(x, 2)
        out = np.empty_like(z)
        for j in range(ny):
            row = z[j]
            ok = np.isfinite(row)
            c, *_ = np.linalg.lstsq(V[ok], row[ok], rcond=None)
            for _ in range(3):
                r = row - V @ c
                sel = ok & (r >= np.nanmedian(r[ok]))
                if sel.sum() >= 3:
                    c, *_ = np.linalg.lstsq(V[sel], row[sel], rcond=None)
            out[j] = row - V @ c
        return out
    order = int(method[-1])
    V = np.vander(x, order + 1)
    coef, *_ = np.linalg.lstsq(V, z.T, rcond=None)
    return z - (V @ coef).T


def roughness_stats(z):
    z = np.asarray(z, float)
    z = z[np.isfinite(z)]
    if z.size < 4:
        return None
    d = z - z.mean()
    rq = float(np.sqrt(np.mean(d * d)))
    return dict(ra_m=float(np.mean(np.abs(d))), rq_m=rq, rpv_m=float(z.max() - z.min()), rp_m=float(d.max()), rv_m=float(-d.min()),
                rsk=float(np.mean(d ** 3) / rq ** 3) if rq > 0 else 0.0, rku=float(np.mean(d ** 4) / rq ** 4) if rq > 0 else 0.0, mean_m=float(z.mean()),
                pixels=int(z.size))


def line_profile(img, start, end, points):
    from scipy.ndimage import map_coordinates
    (x0, y0), (x1, y1) = start, end
    t = np.linspace(0.0, 1.0, points)
    xs, ys = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
    return map_coordinates(np.asarray(img, float), [ys, xs], order=1, mode='nearest')


def step_height_histogram(z, bins=256):
    """Two-level surfaces: distance between the two strongest peaks of the height histogram (smoothed), with their mean levels."""
    z = np.asarray(z, float).ravel()
    z = z[np.isfinite(z)]
    hist, edges = np.histogram(z, bins=bins)
    k = np.exp(-0.5 * (np.arange(-6, 7) / 2.0) ** 2)
    hs = np.convolve(hist, k / k.sum(), mode='same')
    peaks = [i for i in range(1, bins - 1) if hs[i] >= hs[i - 1] and hs[i] > hs[i + 1] and hs[i] > 0.05 * hs.max()]
    if len(peaks) < 2:
        return None
    peaks = sorted(peaks, key=lambda i: hs[i], reverse=True)[:2]
    lo, hi = sorted(peaks)
    centre = 0.5 * (edges[:-1] + edges[1:])
    cut = 0.5 * (centre[lo] + centre[hi])
    low, high = z[z < cut], z[z >= cut]
    return dict(step_m=float(np.median(high) - np.median(low)), lower_level_m=float(np.median(low)), upper_level_m=float(np.median(high)),
                lower_fraction=float(low.size / z.size))


def downsample(a, max_values, max_side=None):
    """Block-average a 2D array so that it holds at most max_values values (and at most max_side per axis)."""
    a = np.asarray(a, float)
    ny, nx = a.shape
    f = 1
    while (math.ceil(ny / f) * math.ceil(nx / f) > max_values) or (max_side and max(math.ceil(ny / f), math.ceil(nx / f)) > max_side):
        f += 1
    if f == 1:
        return a, 1
    py, px = (-ny) % f, (-nx) % f
    b = np.pad(a, ((0, py), (0, px)), mode='edge')
    return b.reshape(b.shape[0] // f, f, b.shape[1] // f, f).mean(axis=(1, 3)), f


# ---------------------------------------------------------------- capabilities
SLUG = 'park_nx10'
CATALOG_ID = 'CHEM-158'
INSTANCE_ID = 'park_nx10_01'
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


MAX_SAMPLES = 12
MAX_PROBES = 24
ID = dict(TEXT, pattern='[a-z0-9_]{1,32}')
STAGE_XY_REF = 'XY电动台相对原点的位移（资产坐标X/Y）：原点时样品吸盘中心在探针轴线（x=0.0268 m, y=−0.0205 m）正下方'
Z_STAGE_REF = 'Z电动台关节位置：0为厂家尺寸图姿态（Z扫描器缩回时针尖高于台面0.0978 m），+向上'
FOCUS_REF_TEXT = '聚焦台位置（0–15 mm，随Z台一起移动）：7.5 mm时焦平面在悬臂平面，增大时焦平面下移'
PLACE_REF = '样品中心相对样品吸盘中心的偏移（资产坐标X/Y）'
SCAN_REF = '扫描中心相对XY扫描器中心的偏移（样品坐标同向俯视；XY扫描器±25 µm）'
PIXEL_REF = '图像像素坐标（列x、行y，0起；行0为最先扫描的一行，图像与样品同向俯视）'
CAL_REF = 'SmartScan校准表：力常数与VERTICAL灵敏度（V/m）来自悬臂数据库、热调谐、力曲线或手动输入'
TEMPLATE_IDS = sorted(sample_templates()['templates'])
PROBE_TYPES = sorted(probes()['types'])
OVERRIDE_UNITS = {'height_m': 'm', 'pitch_m': 'm', 'duty': '1', 'edge_width_m': 'm', 'rq_m': 'm', 'corr_length_m': 'm', 'step_height_m': 'm',
                  'terrace_width_m': 'm', 'fraction_b': '1', 'domain_size_m': 'm', 'period_m': 'm', 'height_contrast_m': 'm', 'diameter_m': 'm',
                  'diameter_sd_m': 'm', 'coverage': '1', 'layer_thickness_m': 'm', 'flake_size_m': 'm', 'interface_offset_m': 'm', 'width_m': 'm',
                  'persistence_length_m': 'm', 'contour_length_m': 'm'}
OVERRIDE_RANGES = {    # physically sensible bounds of the topography parameters (lengths in m; engineering): a length that sets a period or an object
    # size has a strictly positive minimum (zero would divide the procedural surface); templates and overrides alike; object counts: topography_work
    'height_m': (1e-10, 2e-5), 'pitch_m': (5e-8, 2e-5), 'duty': (0.05, 0.95), 'edge_width_m': (1e-9, 5e-6), 'rq_m': (0.0, 1e-6),
    'corr_length_m': (5e-9, 5e-6), 'step_height_m': (5e-11, 1e-6), 'terrace_width_m': (2e-8, 2e-5), 'fraction_b': (0.05, 0.95),
    'domain_size_m': (2e-8, 2e-5), 'period_m': (1e-8, 2e-6), 'height_contrast_m': (-1e-6, 1e-6), 'diameter_m': (2e-9, 1e-5),
    'diameter_sd_m': (0.0, 1e-6), 'coverage': (0.0, 0.9), 'layer_thickness_m': (1e-10, 1e-7), 'flake_size_m': (5e-8, 1e-5),
    'interface_offset_m': (0.0, 1e-7), 'width_m': (5e-10, 1e-7), 'persistence_length_m': (5e-9, 1e-5), 'contour_length_m': (1e-8, 5e-6)}
OVERRIDES = {'type': 'object', 'properties': {k: number(u, *OVERRIDE_RANGES[k]) for k, u in OVERRIDE_UNITS.items()}, 'required': [],
             'additionalProperties': False}
PLANT = obj(dict(humidity=ref(number('1', 0.0, 0.95), '室内相对湿度（0–1）'), room_k=number('K', 283.15, 308.15), acoustic_enclosure=BOOL, vibration_isolation=BOOL,
                 noise_seed=integer(0, 2 ** 32 - 1)))
PLANT_EXAMPLE = dict(humidity=0.40, room_k=295.15, acoustic_enclosure=True, vibration_isolation=True, noise_seed=1)
SAMPLE_PROVIDE = obj(dict(sample_id=ID, template_id=enum(TEMPLATE_IDS), seed=integer(0, 2 ** 31 - 1), tilt_rad=array(number('rad', -0.02, 0.02), 2, 2),
                          topography_overrides=OVERRIDES), optional=('topography_overrides',))
SAMPLE_EXAMPLE = dict(sample_id='grating', template_id='calibration_grating_xyz', seed=7, tilt_rad=[0.002, -0.001])
OPERATION = obj(dict(operation_id=TEXT))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
CAL_TABLE = obj(dict(cantilever_db=nullable(enum(PROBE_TYPES)), spring_constant_n_m=nullable(number('N/m')), spring_constant_source=nullable(enum(['database', 'thermal_tune', 'manual'])),
                     sensitivity_v_per_m=nullable(number('V/m')), sensitivity_source=nullable(enum(['database', 'force_curve', 'manual'])), resonance_hz=nullable(number('Hz')),
                     quality_factor=nullable(number('1')), updated_s=nullable(number('s'))))
SETPOINT_IN = dict(setpoint_force_n=nullable(ref(number('N', 1e-11, 1e-6), '接触模式设定点：相对自由悬臂的排斥力（按校准表的k与灵敏度换算成VERTICAL变化）')),
                   setpoint_amplitude_m=nullable(ref(number('m', 0.1e-9, 200e-9), '轻敲/非接触模式设定点：振幅（按校准表灵敏度显示的nm值）')))
CHANNEL_UNITS = {'height': 'm', 'error': 'V', 'phase': 'rad', 'z_drive': 'V'}
REGION = nullable(ref(array(integer(0, MAX_PIXELS), 4, 4), PIXEL_REF + '：[x0, y0, x1, y1]，含x0/y0、不含x1/y1'))
ROUGH = obj(dict(ra_m=number('m'), rq_m=number('m'), rpv_m=number('m'), rp_m=number('m'), rv_m=number('m'), rsk=number('1'), rku=number('1'), mean_m=number('m'),
                 pixels=INT))
FDFIT = obj(dict(reduced_modulus_pa=number('Pa'), sample_modulus_pa=nullable(number('Pa')), adhesion_n=number('N'), contact_point_m=number('m'),
                 max_indentation_m=number('m'), points=INT, residual_n=number('N'), warnings=array(TEXT, 0, 8)))
RESULT = obj(dict(record=obj(dict(record_id=TEXT, kind=enum(['image', 'force_curves', 'thermal_tune', 'frequency_sweep', 'processed']), status=STATUS,
                                  started_s=number('s'), finished_s=nullable(number('s')), meta={'type': 'object'}, warnings=array(TEXT, 0, 32),
                                  template_source={'type': 'object'})),
                  data={'type': 'object'}))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'beam', 'head', 'probe', 'stage', 'approach', 'scan', 'force_curve', 'calibration', 'sample', 'error', 'fault']),
                 code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['electrical_energy_j', 'beam_on_s', 'xy_stage_travel_m', 'z_stage_travel_m', 'focus_travel_m', 'scanner_path_m', 'scan_lines', 'force_curves',
               'approaches', 'probe_exchanges', 'head_swaps', 'tip_crashes']
LEDGER = obj({k: number('J' if k.endswith('_j') else 's' if k.endswith('_s') else 'm' if k.endswith('_m') else '1', 0.0) for k in LEDGER_KEYS})
POS2 = array(number('m'), 2, 2)

cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['base', 'rear_panel'],
    '宿主声明实验室条件（相对湿度、室温、是否在声学罩内、是否有主动隔振台、噪声种子）；断电且无操作时才可重新声明', 'evaluator', 'configuration')
cap('sample.provide', SAMPLE_PROVIDE, obj(dict(sample_id=TEXT, template_id=TEXT, location=TEXT)), SAMPLE_EXAMPLE, ['sample_chuck'],
    '宿主在实验台上提供一个样品（样品模板库：校准光栅、HOPG、云母、硅片、聚合物膜与共混物、嵌段共聚物、纳米颗粒、二维材料、DNA、ITO、微球；可覆盖形貌参数、给定种子与安装倾角）',
    'evaluator', 'configuration')
cap('probe.provide', obj(dict(probe_id=ID, type_id=enum(PROBE_TYPES))), obj(dict(probe_id=TEXT, type_id=TEXT, location=TEXT)), dict(probe_id='tap1', type_id='ac160ts'),
    ['cantilever_chip'], '宿主把一片探针（悬臂芯片）放进探针盒（型号的厂家标称值见探针库；每片的实际力常数、共振频率、Q与针尖半径为隐藏真值）', 'evaluator', 'configuration')
cap('sample.place', obj(dict(sample_id=ID, offset_x_m=ref(number('m', -0.05, 0.05), PLACE_REF), offset_y_m=ref(number('m', -0.05, 0.05), PLACE_REF))),
    obj(dict(sample_id=TEXT, offset_x_m=number('m'), offset_y_m=number('m'), under_tip=BOOL)), dict(sample_id='grating', offset_x_m=0.0, offset_y_m=0.0),
    ['sample_chuck', 'sample_holder'],
    '把实验台上的样品放到样品吸盘上（贴在钢片上的小样品放磁性样品座；大样品直接放吸盘，最大100×100 mm、厚20 mm、0.5 kg）；针尖须高出新样品最高点（顶面＋形貌高度范围＋倾斜与原子台阶平均斜率在样品边缘的抬高）2 mm（宿主/机器人抽象）', 'agent',
    'robot_skill')
cap('sample.remove', obj(dict(sample_id=ID)), obj(dict(sample_id=TEXT)), dict(sample_id='grating'), ['sample_chuck', 'sample_holder'],
    '把样品从吸盘取回实验台（针尖须高出样品最高点（顶面＋形貌高度范围＋倾斜与原子台阶平均斜率在样品边缘的抬高）2 mm、未进针；宿主/机器人抽象）', 'agent', 'robot_skill')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL, state=TEXT)), dict(enabled=True), ['controller', 'button_power', 'rear_panel'],
    'NX10控制电子单元与SmartScan电源（上电后初始化约20 s；断电时Z扫描器缩回、电动台停在当时位置、进行中的操作失败）')
cap('power.press_button', obj({}), obj(dict(enabled=BOOL, state=TEXT)), {}, ['button_power', 'controller'], '按控制电子单元前面板的电源键（按一次切换通断；与power.set同一过程）')
cap('beam.set', obj(dict(on=BOOL)), obj(dict(on=BOOL, emitting=BOOL)), dict(on=True), ['switch_beam', 'led_beam'],
    '测头上的SLD光束开关（830 nm；通电、测头装在燕尾导轨上且无故障时发光，指示灯亮）')
cap('beam.press_switch', obj({}), obj(dict(on=BOOL, emitting=BOOL)), {}, ['switch_beam', 'led_beam'], '按一次测头光束开关（切换；与beam.set同一过程）')
cap('head.remove', obj({}), obj(dict(state=TEXT, duration_s=number('s'))), {}, ['nx_head', 'z_stage_carriage'],
    '松开燕尾导轨锁、把NX测头滑出放到测头架上（换探针用；未进针、无操作、电动台静止、针尖高出样品最高点（顶面＋形貌高度范围＋倾斜与原子台阶平均斜率在样品边缘的抬高）1 mm；约15 s；宿主/机器人抽象）', 'agent', 'robot_skill')
cap('head.attach', obj({}), obj(dict(state=TEXT, duration_s=number('s'))), {}, ['nx_head', 'z_stage_carriage'],
    '把NX测头沿燕尾导轨推回并锁紧（针尖须高出样品最高点（顶面＋形貌高度范围＋倾斜与原子台阶平均斜率在样品边缘的抬高）1 mm；约15 s；宿主/机器人抽象）', 'agent', 'robot_skill')
cap('probe.mount', obj(dict(probe_id=ID)), obj(dict(probe_id=TEXT, duration_s=number('s'))), dict(probe_id='tap1'), ['probehand', 'cantilever_chip'],
    '把探针盒里的一片探针装到测头的探针夹上（芯片座靠红宝石球与磁铁定位，预对准：新悬臂末端在光斑附近±12 µm内；测头须已取下；约20 s）', 'agent', 'robot_skill')
cap('probe.unmount', obj({}), obj(dict(probe_id=TEXT, duration_s=number('s'))), {}, ['probehand', 'cantilever_chip'], '取下探针放回探针盒（测头须已取下；约20 s）', 'agent',
    'robot_skill')
cap('head.configure', obj(dict(mode=enum(MODES), cantilever_db=enum(PROBE_TYPES))), obj(dict(mode=TEXT, cantilever_db=TEXT, calibration=CAL_TABLE)),
    dict(mode='tapping', cantilever_db='ac160ts'), ['nx_head', 'probehand'],
    'SmartScan测头模式（接触、True Non-Contact、轻敲）与悬臂数据库条目；换数据库条目时校准表恢复为数据库标称值（k标称、灵敏度按悬臂长度的默认值）；未进针时才可', 'agent', 'configuration')
cap('head.turn_knob', obj(dict(knob=enum(KNOBS), turns=ref(number('1', -10.0, 10.0), '旋钮转过的圈数，+为从旋钮外侧看的逆时针（场景关节轴由旋钮指向外侧，右手定则）'))),
    obj(dict(knob=TEXT, turns_total=number('1'))), dict(knob='beam_x', turns=0.25),
    ['knob_beam_x', 'knob_beam_y', 'knob_pspd_x', 'knob_pspd_y', 'knob_optics_x', 'knob_optics_y'],
    '转测头或光学柱上的调节旋钮：光束X/Y（SLD光斑在悬臂平面上移动约40 µm/圈）、PSPD X/Y（光电二极管移动约0.6 mm/圈，改变LATERAL/VERTICAL）、光学X/Y（相机视野平移约100 µm/圈）；'
    '未进针且无操作时才可')
cap('head.set_knob', obj(dict(knob=enum(KNOBS), turns_total=ref(number('1', -15.0, 15.0), '旋钮自出厂零位起的累计圈数（绝对值；+为从旋钮外侧看的逆时针；光束±15、PSPD与光学±10圈）'))),
    obj(dict(knob=TEXT, turns_total=number('1'))), dict(knob='beam_x', turns_total=0.25),
    ['knob_beam_x', 'knob_beam_y', 'knob_pspd_x', 'knob_pspd_y', 'knob_optics_x', 'knob_optics_y'],
    '把调节旋钮转到给定的累计圈数（与head.turn_knob同一过程；场景中手转旋钮停稳后按转角/2π调用）')
cap('stage.move_xy', obj(dict(x_m=ref(number('m', -XY_STAGE_HALF_M, XY_STAGE_HALF_M), STAGE_XY_REF), y_m=ref(number('m', -XY_STAGE_HALF_M, XY_STAGE_HALF_M), STAGE_XY_REF))),
    obj(dict(target_m=POS2, duration_s=number('s'))), dict(x_m=0.0, y_m=0.0), ['stage_x_carriage', 'stage_y_carriage', 'sample_chuck'],
    '电动XY样品台（20×20 mm，分辨率5 µm，速度2 mm/s为工程值）把样品移到针尖下；进针时与Z台运动时不能移动；沿直线路径检查碰撞：路径上的样品（按顶面、'
    '形貌高度范围与路径上倾斜及原子台阶平均斜率的抬高）、样品座或吸盘高于针尖，或针尖离较高的支撑面不到100 µm时跨越样品或样品座边缘，或针尖压在表面上，均以COLLISION_RISK'
    '拒绝；电动台卡滞时调用仍被接受，运动停止并锁存system.error_code=XY_STAGE_FAULT（之后的调用以ERROR_PENDING被拒，清除故障并确认后恢复）')
cap('z_stage.move', obj(dict(z_m=ref(number('m', Z_STAGE_LIMITS_M[0], Z_STAGE_LIMITS_M[1]), Z_STAGE_REF))), obj(dict(target_m=number('m'), duration_s=number('s'))),
    dict(z_m=0.0), ['z_stage_carriage', 'nx_head'],
    '电动Z台升降测头与光学柱（25 mm行程、最快0.8 mm/s、分辨率80 nm）；下降要求激光强度不低于联锁阈值（手册：Laser intensity is too low）；下降途中针尖触到样品时，'
    '若光束检测正常则触碰停止（轻微磨损），否则撞针（悬臂损坏）；Z台卡滞时调用仍被接受，运动停止并锁存system.error_code=Z_STAGE_FAULT（之后的调用以ERROR_PENDING'
    '被拒，清除故障并确认后恢复）')
cap('focus.move', obj(dict(position_m=ref(number('m', 0.0, FOCUS_RANGE_M), FOCUS_REF_TEXT))), obj(dict(target_m=number('m'), duration_s=number('s'))),
    dict(position_m=FOCUS_REF_M), ['focus_stage', 'objective'], '电动聚焦台移动10×物镜与相机的焦平面（15 mm行程；聚焦台随Z台移动；速度0.8 mm/s为工程值）')
cap('camera.read', obj({}), obj(dict(cantilever_sharpness=number('1', 0.0, 1.0), sample_sharpness=number('1', 0.0, 1.0), cantilever_in_view=BOOL,
                                    cantilever_offset_m=nullable(POS2), spot_visible=BOOL, spot_offset_m=nullable(POS2), field_of_view_m=POS2)),
    {}, ['camera', 'objective', 'optical_column'],
    '同轴光学相机（10×物镜、视野480×360 µm）：悬臂与样品表面的清晰度（景深约±10 µm）、悬臂末端在视野中的位置、SLD光斑相对悬臂末端的位置（x沿悬臂指向自由端外，y横向）', 'agent', 'query')
cap('ncm.frequency_sweep', obj(dict(start_hz=number('Hz', 1e3, 3e6), stop_hz=number('Hz', 1e3, 3e6), points=integer(64, 2048))), OPERATION,
    dict(start_hz=240e3, stop_hz=360e3, points=512), ['probehand', 'cantilever_chip', 'nx_head'],
    '用探针夹上的双压电片驱动自由悬臂扫频（约6 s），记录振幅（按校准表灵敏度换算nm）与相位，给出峰值频率与Q估计；轻敲/非接触模式、未进针')
cap('ncm.set_drive', obj(dict(frequency_hz=number('Hz', 1e3, 3e6), amplitude_m=ref(number('m', 0.5e-9, 200e-9), '驱动频率处的自由振幅（按校准表灵敏度显示的nm值）'))),
    obj(dict(frequency_hz=number('Hz'), amplitude_m=number('m'))), dict(frequency_hz=301e3, amplitude_m=20e-9), ['probehand', 'cantilever_chip'],
    '设定驱动频率与自由振幅（SmartScan按当前灵敏度调节驱动电压；True Non-Contact应设在共振峰稍高频一侧）；轻敲/非接触模式、未进针')
cap('approach', obj(dict(SETPOINT_IN, z_servo_gain=number('1', 0.05, 20.0))), OPERATION, dict(setpoint_force_n=None, setpoint_amplitude_m=16e-9, z_servo_gain=1.0),
    ['z_stage_carriage', 'nx_head', 'cantilever_chip'],
    '自动进针：Z扫描器伸出、Z台以50 µm/s下降直到达到设定点（接触：VERTICAL相对自由值增加设定力对应的电压；轻敲/非接触：振幅降到设定点），然后Z台停在使Z扫描器约半伸出的位置、'
    '反馈保持设定点；光束强度须高于联锁阈值；PSPD偏离中心太多会使信号饱和（接触模式撞针）或提前触发（动态模式假进针）')
cap('retract', obj(dict(distance_m=number('m', 1e-6, 0.005))), obj(dict(target_m=number('m'), duration_s=number('s'))), dict(distance_m=RETRACT_DISTANCE_M),
    ['z_stage_carriage', 'nx_head'], '抬针：Z扫描器缩回、Z台上升给定距离（手册默认100 µm）；Z台卡滞时调用仍被接受，Z台不动并锁存system.error_code=Z_STAGE_FAULT',
    'agent', 'device_command')
cap('scan.start', obj(dict(size_m=number('m', 1e-9, XY_SCAN_RANGE_M), pixels_x=integer(16, MAX_PIXELS), pixels_y=integer(16, MAX_PIXELS), rate_hz=number('Hz', 0.01, 50.0),
                           angle_rad=ref(number('rad', -math.pi / 2, math.pi / 2), '快扫描方向相对XY扫描器X轴的旋转'),
                           offset_x_m=ref(number('m', -XY_SCAN_RANGE_M / 2, XY_SCAN_RANGE_M / 2), SCAN_REF),
                           offset_y_m=ref(number('m', -XY_SCAN_RANGE_M / 2, XY_SCAN_RANGE_M / 2), SCAN_REF), **SETPOINT_IN, z_servo_gain=number('1', 0.05, 20.0),
                           save_retrace=BOOL, retract_after=BOOL)), OPERATION,
    dict(size_m=5e-6, pixels_x=256, pixels_y=256, rate_hz=1.0, angle_rad=0.0, offset_x_m=0.0, offset_y_m=0.0, setpoint_force_n=None, setpoint_amplitude_m=16e-9,
         z_servo_gain=1.0, save_retrace=False, retract_after=False), ['xy_scanner', 'nx_head', 'cantilever_chip', 'sample_chuck'],
    '成像扫描（进针后）：XY扫描器光栅扫描（快轴往返，针尖速度=2×尺寸×行频，上限200 µm/s），Z反馈按设定点与Z伺服增益跟踪形貌；记录高度（Z检测器）、误差（接触：VERTICAL偏差V；'
    '动态：振幅偏差按V显示的m）、相位（动态模式，rad）、Z驱动电压；可选保存回扫、完成后抬针')
cap('fd.acquire', obj(dict(z_range_m=number('m', 10e-9, 10e-6), rate_hz=number('Hz', 0.05, 20.0), trigger_force_n=ref(number('N', 1e-11, 1e-6), '触发力（按校准表换算的VERTICAL变化）'),
                           dwell_s=number('s', 0.0, 10.0), count=integer(1, 64),
                           positions_m=ref(array(array(number('m', -XY_SCAN_RANGE_M / 2, XY_SCAN_RANGE_M / 2), 2, 2), 1, 64), SCAN_REF))), OPERATION,
    dict(z_range_m=200e-9, rate_hz=1.0, trigger_force_n=10e-9, dwell_s=0.0, count=1, positions_m=[[0.0, 0.0]]), ['nx_head', 'cantilever_chip', 'xy_scanner'],
    '力-距离谱（进针后）：Z扫描器以给定范围与频率逼近-回退（先抬起半个范围），达到触发力或走完范围后回退，记录Z位移与VERTICAL（V）并按校准表显示力；可在扫描窗口内多点、多次')
cap('calibration.thermal_tune', obj(dict(temperature_k=ref(number('K', 273.15, 323.15), '用户输入的悬臂温度（SmartScan用它按能量均分计算k）'), averages=integer(8, 1000))),
    OPERATION, dict(temperature_k=295.15, averages=100), ['cantilever_chip', 'nx_head'],
    '热调谐标定力常数：记录自由悬臂的热噪声功率谱（数据库标称共振频率的0.6–1.6倍）、拟合阻尼谐振子得f0、Q与共振峰面积，k=0.817 kB T S²/面积（S为当前校准灵敏度）；未进针（约2+0.1×平均次数 s）')
cap('calibration.sensitivity', obj(dict(record_id=TEXT, curve_index=integer(0, 63), branch=enum(['approach', 'retract']))),
    obj(dict(sensitivity_v_per_m=number('V/m'), previous_v_per_m=nullable(number('V/m')), points=INT, r2=number('1'))),
    dict(record_id='FD0001', curve_index=0, branch='approach'), ['cantilever_chip', 'nx_head'],
    '由硬样品上的力曲线接触段斜率标定VERTICAL灵敏度（V/m；软样品会低估），写入校准表', 'agent', 'configuration')
cap('calibration.set', obj(dict(spring_constant_n_m=nullable(number('N/m', 1e-3, 1000.0)), sensitivity_v_per_m=nullable(number('V/m', 1e5, 1e10)))),
    obj(dict(calibration=CAL_TABLE)), dict(spring_constant_n_m=26.0, sensitivity_v_per_m=None), ['nx_head'], '手动输入力常数和/或灵敏度（null保持不变）', 'agent', 'configuration')
_IMG_IN = dict(record_id=TEXT, channel=enum(CHANNELS), direction=enum(['trace', 'retrace']))
_HEIGHT_IN = dict(record_id=TEXT, channel=enum(['height']), direction=enum(['trace', 'retrace']))    # topography statistics in m: height channel only
cap('analysis.flatten', obj(dict(_IMG_IN, method=enum(FLATTEN))), obj(dict(record_id=TEXT, source_record_id=TEXT, stats=nullable(ROUGH))),
    dict(record_id='IMG0001', channel='height', direction='trace', method='line_1'), ['nx_head'],
    '图像展平（平面、逐行0–2阶多项式，或只用中位数以上像素拟合的plane_upper/line_upper：两级表面的坑、沟槽不拉偏背景），生成处理后的记录（粗糙度图）；高度通道给出'
    '粗糙度统计（m），其他通道（误差V、相位rad、Z驱动V）的统计为null', 'agent', 'analysis')
cap('analysis.roughness', obj(dict(_HEIGHT_IN, flatten=enum(FLATTEN), region_px=REGION)), ROUGH,
    dict(record_id='IMG0001', channel='height', direction='trace', flatten='plane', region_px=None), ['nx_head'],
    '粗糙度（高度通道，m：Ra、Rq、Rpv、Rp、Rv、偏度Rsk、峭度Rku；可选区域）', 'agent', 'analysis')
cap('analysis.line_profile', obj(dict(_IMG_IN, flatten=enum(FLATTEN), start_px=ref(array(number('1', 0, MAX_PIXELS), 2, 2), PIXEL_REF),
                                      end_px=ref(array(number('1', 0, MAX_PIXELS), 2, 2), PIXEL_REF), points=integer(2, 4096))),
    obj(dict(distance_m=array(number('m'), 2, 4096), values=array(number('1'), 2, 4096), unit=TEXT)),
    dict(record_id='IMG0001', channel='height', direction='trace', flatten='line_1', start_px=[0, 128], end_px=[255, 128], points=256), ['nx_head'],
    '线剖面（双线性插值；值的单位随通道：高度m、误差V、相位rad、Z驱动V）', 'agent', 'analysis')
cap('analysis.step_height', obj(dict(_HEIGHT_IN, flatten=enum(FLATTEN), method=enum(['histogram', 'regions']), region_a_px=REGION, region_b_px=REGION)),
    obj(dict(step_m=number('m'), lower_level_m=number('m'), upper_level_m=number('m'), method=TEXT)),
    dict(record_id='IMG0001', channel='height', direction='trace', flatten='line_1', method='histogram', region_a_px=None, region_b_px=None), ['nx_head'],
    '台阶高度（高度通道）：高度直方图两峰之差（histogram），或两个区域平均高度之差（regions：B−A）', 'agent', 'analysis')
cap('analysis.fd_fit', obj(dict(record_id=TEXT, curve_index=integer(0, 63), branch=enum(['approach', 'retract']), model=enum(['dmt', 'hertz']),
                                tip_radius_m=number('m', 1e-9, 1e-6), sample_poisson_ratio=number('1', 0.0, 0.5), tip_modulus_pa=number('Pa', 1e9, 1.3e12),
                                tip_poisson_ratio=number('1', 0.0, 0.5))), FDFIT,
    dict(record_id='FD0001', curve_index=0, branch='retract', model='dmt', tip_radius_m=10e-9, sample_poisson_ratio=0.35, tip_modulus_pa=169e9, tip_poisson_ratio=0.27),
    ['nx_head'], '力曲线拟合（DMT用回退曲线与拉脱力；Hertz用逼近曲线的排斥段）：约化模量、样品模量、粘附力、接触点', 'agent', 'analysis')
cap('measurement.result', obj(dict(record_id=TEXT, channel=nullable(enum(CHANNELS)), direction=nullable(enum(['trace', 'retrace'])), region_px=REGION,
                                   max_values=integer(16, RESULT_VALUES_MAX))), RESULT,
    dict(record_id='IMG0001', channel='height', direction='trace', region_px=None, max_values=65536), ['nx_head'],
    '读取记录：图像（按区域截取并按块平均降采样到不超过max_values个值）、力曲线、热调谐谱、扫频曲线或处理后的图', 'agent', 'query')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, MAX_RECORDS))), {}, ['nx_head'], '列出记录', 'agent', 'query')
cap('records.delete', obj(dict(record_id=TEXT)), obj(dict(record_id=TEXT, remaining=INT)), dict(record_id='IMG0001'), ['nx_head'], '删除一条记录（进行中操作的记录不能删）',
    'agent', 'configuration')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), ['nx_head'], '读取事件与报警', 'agent',
    'query')
cap('system.acknowledge', obj({}), obj(dict(cleared=nullable(TEXT))), {}, ['nx_head'], '确认已排除原因的锁存错误（SLD故障、电动台故障）')
cap('provenance.read', obj({}), obj(dict(libraries={'type': 'object', 'additionalProperties': TEXT}, substances=INT, probe_types=INT, sample_templates=INT)), {}, ['nx_head'],
    '模板库来源与校验和', 'agent', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 8))), dict(fault='sld_failure', active=True), ['base'],
    '注入或清除故障（评测者）：SLD失效、Z台卡滞、XY台卡滞、针尖断裂（立即）、振动冲击', 'evaluator', 'configuration')
cap('ledger.read', obj({}), LEDGER, {}, ['base'], '使用与守恒账本（电能、光束时间、电动台与扫描器行程、扫描行数、力曲线数、进针次数、换针、撞针）', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['base'], '宿主推进仿真时间', 'evaluator', 'simulation')
UNSUPPORTED = {
    'kpfm.scan': '开尔文探针力显微镜是选件（宣传册），不在本配置；不重复计为独立整机',
    'pfm.scan': '压电响应力显微镜是选件，不在本配置',
    'efm.scan': '静电力显微镜是选件，不在本配置',
    'mfm.scan': '磁力显微镜是选件，不在本配置',
    'conductive_afm.scan': '导电AFM（C-AFM、需电流放大器与导电探针）是选件，不在本配置',
    'lateral_force.scan': '侧向力（摩擦力）通道未建模（LATERAL只作光斑对准读数）',
    'sicm.scan': '扫描离子电导显微镜是选件，不在本配置',
    'liquid_cell.install': '液相池（生物/电化学）是选件，本资产只模拟大气环境',
    'temperature_stage.set': '温控台是选件，不在本配置',
    'nanolithography.run': '纳米刻蚀与纳米操纵是选件，不在本配置',
    'probe.auto_exchange': 'NX10没有自动换针（换针为手动取下测头的机器人技能probe.mount/unmount）',
    'hardware.connect': '没有真实NX10硬件或SmartScan软件接口',
    'export.dataset': 'XEI/SmartScan数据文件（.tiff）导出未建模',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['nx_head'], _why, 'agent', 'device_command')


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


PROBE_TRUTH = obj(dict(probe_id=TEXT, type_id=TEXT, spring_constant_n_m=number('N/m'), resonance_hz=number('Hz'), quality_factor=number('1'), tip_radius_m=number('m'),
                       tip_radius_initial_m=number('m'), lever_gain=number('1'), damage=enum(['none', 'tip_broken', 'cantilever_broken'])))
slot('clock.time_s', number('s'), 's', 'derived', 'base', '仿真时钟')
slot('plant.configured', BOOL, '1', 'setpoint', 'base', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'setpoint', 'button_power', '控制电子单元电源（前面板电源键与指示灯）')
slot('system.state', enum(['off', 'booting', 'ready']), '1', 'derived', 'controller', '系统状态（断电/初始化中/就绪）')
slot('system.error_code', nullable(TEXT), '1', 'derived', 'controller', '锁存的错误代码')
slot('beam.switch_on', BOOL, '1', 'setpoint', 'switch_beam', '测头SLD光束开关位置')
slot('beam.emitting', BOOL, '1', 'derived', 'led_beam', 'SLD在发光（开关开、通电就绪、测头装上且无SLD故障；指示灯）')
slot('head.state', enum(['attached', 'removed', 'moving']), '1', 'derived', 'nx_head', '测头在燕尾导轨上/已取下/移动中')
slot('head.mode', nullable(enum(MODES)), '1', 'setpoint', 'nx_head', 'SmartScan测头模式')
slot('probe.box_ids', array(TEXT, 0, MAX_PROBES), '1', 'derived', 'cantilever_chip', '探针盒里的探针')
slot('probe.mounted', nullable(obj(dict(probe_id=TEXT, type_id=TEXT))), '1', 'derived', 'cantilever_chip', '装在探针夹上的探针（ID与型号）')
slot('probe.exchanging', BOOL, '1', 'derived', 'probehand', '正在装/取探针')
KNOB_ZH = {'beam_x': '光束X旋钮（SLD光斑沿悬臂方向）', 'beam_y': '光束Y旋钮（SLD光斑横向）', 'pspd_x': 'PSPD X旋钮（LATERAL）', 'pspd_y': 'PSPD Y旋钮（VERTICAL）',
           'optics_x': '光学X旋钮（相机视野左右）', 'optics_y': '光学Y旋钮（相机视野前后）'}
for _k in KNOBS:
    slot(f'knob.{_k}_turns', number('1', -KNOB_LIMIT_TURNS[_k], KNOB_LIMIT_TURNS[_k]), '1', 'setpoint', f'knob_{_k}',
         KNOB_ZH[_k] + '自出厂零位起的累计圈数（+为从旋钮外侧看的逆时针；场景旋钮关节＝圈数×2π，关节轴朝外）')
slot('pspd.intensity_v', number('V'), 'V', 'sensor', 'nx_head', 'INTENSITY：光电二极管总信号（镀膜悬臂对准时约2–3 V）')
slot('pspd.vertical_v', number('V'), 'V', 'sensor', 'nx_head', 'VERTICAL：上下象限差信号（反映悬臂偏转；进针前应在±0.5 V内）')
slot('pspd.lateral_v', number('V'), 'V', 'sensor', 'nx_head', 'LATERAL：左右象限差信号（进针前应在±0.5 V内）')
slot('ncm.drive', nullable(obj(dict(frequency_hz=number('Hz'), amplitude_m=number('m')))), '1', 'setpoint', 'probehand', '驱动频率与自由振幅设定（显示值）')
slot('ncm.amplitude_m', nullable(number('m')), 'm', 'sensor', 'probehand', '当前振幅读数（按校准表灵敏度换算；未驱动为null）')
slot('stage.xy_m', POS2, 'm', 'derived', 'stage_x_carriage', 'XY电动台位置（场景关节stage_x、stage_y镜像）', reference=STAGE_XY_REF)
slot('stage.xy_target_m', POS2, 'm', 'derived', 'stage_x_carriage', 'XY电动台目标', reference=STAGE_XY_REF)
slot('stage.x_m', number('m', -XY_STAGE_HALF_M, XY_STAGE_HALF_M), 'm', 'derived', 'stage_x_carriage', 'XY电动台X位置（场景关节stage_x_carriage镜像）', reference=STAGE_XY_REF)
slot('stage.y_m', number('m', -XY_STAGE_HALF_M, XY_STAGE_HALF_M), 'm', 'derived', 'stage_y_carriage', 'XY电动台Y位置（场景关节stage_y_carriage镜像）', reference=STAGE_XY_REF)
slot('stage.xy_moving', BOOL, '1', 'derived', 'stage_x_carriage', 'XY电动台在移动')
slot('z_stage.position_m', number('m'), 'm', 'derived', 'z_stage_carriage', 'Z电动台位置（场景关节z_stage镜像）', reference=Z_STAGE_REF)
slot('z_stage.target_m', number('m'), 'm', 'derived', 'z_stage_carriage', 'Z电动台目标', reference=Z_STAGE_REF)
slot('z_stage.moving', BOOL, '1', 'derived', 'z_stage_carriage', 'Z电动台在移动')
slot('focus.position_m', number('m'), 'm', 'derived', 'focus_stage', '聚焦台位置（场景关节focus_stage镜像）', reference=FOCUS_REF_TEXT)
slot('focus.target_m', number('m'), 'm', 'derived', 'focus_stage', '聚焦台目标', reference=FOCUS_REF_TEXT)
slot('focus.moving', BOOL, '1', 'derived', 'focus_stage', '聚焦台在移动')
slot('sample.bench_ids', array(TEXT, 0, MAX_SAMPLES), '1', 'derived', 'sample_chuck', '实验台上的样品')
slot('sample.on_chuck', nullable(obj(dict(sample_id=TEXT, offset_x_m=number('m'), offset_y_m=number('m'), mount=enum(['holder', 'chuck'])))), '1', 'derived',
     'sample_chuck', '吸盘上的样品、偏移与安装方式（磁性样品座或直接放吸盘）', reference=PLACE_REF)
slot('engage.state', enum(['retracted', 'approaching', 'engaged']), '1', 'derived', 'nx_head', '进针状态（进针完成＝反馈在设定点）')
slot('zscanner.extension_m', number('m', 0.0, Z_SCAN_RANGE_M), 'm', 'sensor', 'nx_head', 'Z检测器读数：Z扫描器伸出量（0为全缩回，15 µm为全伸出）')
slot('feedback.setpoint', nullable(obj(dict(setpoint_force_n=nullable(number('N')), setpoint_amplitude_m=nullable(number('m')), z_servo_gain=number('1')))), '1', 'setpoint',
     'nx_head', '进针或扫描使用的设定点与Z伺服增益')
slot('calibration.table', CAL_TABLE, '1', 'derived', 'nx_head', 'SmartScan校准表', reference=CAL_REF)
slot('operation.id', nullable(TEXT), '1', 'derived', 'nx_head', '进行中的操作（记录ID；进针为操作ID）')
slot('operation.kind', nullable(enum(['approach', 'scan', 'force_curves', 'thermal_tune', 'frequency_sweep'])), '1', 'derived', 'nx_head', '进行中操作的种类')
slot('operation.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'nx_head', '进行中操作的进度')
slot('operation.remaining_s', nullable(number('s', 0.0)), 's', 'derived', 'nx_head', '进行中操作的剩余时间')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'nx_head', '进行中的操作属于另一个agent')
slot('scan.lines_completed', nullable(INT), '1', 'derived', 'nx_head', '进行中扫描已完成的行数（按16行一块更新）')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'nx_head', '最近完成的记录ID（agent只看到自己或宿主的记录）')
slot('truth.probe', nullable(PROBE_TRUTH), '1', 'process_truth', 'cantilever_chip', '装着的探针的真实参数与损伤（力常数、共振频率、Q、当前针尖半径、光杠杆增益）', 'evaluator')
slot('truth.probes', {'type': 'object'}, '1', 'process_truth', 'cantilever_chip', '全部探针的真实参数', 'evaluator')
slot('truth.samples', {'type': 'object'}, '1', 'process_truth', 'sample_chuck', '样品真实定义（模板、材料、形貌参数、种子、倾角）', 'evaluator')
slot('truth.tip_force_n', nullable(number('N')), 'N', 'process_truth', 'cantilever_chip', '进针时针尖-样品的真实平均作用力（接触模式为载荷；动态模式为峰值力或范德华力）', 'evaluator')
slot('truth.gap_m', nullable(number('m')), 'm', 'process_truth', 'cantilever_chip', '未进针时针尖到其下方样品表面的真实距离（没有样品为null）', 'evaluator')
slot('truth.drift_m', array(number('m'), 3, 3), 'm', 'process_truth', 'sample_chuck', '自最近一次扰动（上电、装样、装测头）以来的累计热漂移（x, y, z）', 'evaluator',
     reference='资产坐标系X/Y/Z（+X右、+Y后、+Z上），自最近一次扰动（上电、装样、装测头）起：x、y为针尖相对样品的横向漂移（被扫到的样品点随之移动），'
               'z为样品表面相对针尖的竖直漂移（+为表面升高、靠近针尖）')
slot('truth.faults', array(TEXT, 0, 8), '1', 'process_truth', 'base', '注入的故障', 'evaluator')


# ---------------------------------------------------------------- runtime
class _Sum:
    """Neumaier-compensated running sum."""

    def __init__(self):
        self.s = 0.0
        self.c = 0.0

    def add(self, x):
        t = self.s + x
        if abs(self.s) >= abs(x):
            self.c += (self.s - t) + x
        else:
            self.c += (x - t) + self.s
        self.s = t

    def value(self):
        return self.s + self.c


PURE_ANALYSIS = {'analysis.roughness', 'analysis.line_profile', 'analysis.step_height', 'analysis.fd_fit'}
ASYNC = {'approach', 'scan.start', 'fd.acquire', 'calibration.thermal_tune', 'ncm.frequency_sweep'}
BEAM_OPS = {'approach', 'scan', 'force_curves', 'thermal_tune', 'frequency_sweep'}
HOLDER_MAX_SIZE_M = 0.020                          # samples up to 20 mm go on a steel disk on the magnetic holder (engineering)
PLACE_CLEARANCE_M = 0.002
HEAD_CLEARANCE_M = 0.001
FOCUS_HEAD_CLEARANCE_M = 0.002                     # the objective must be within 2 mm below its reference to slide the head (engineering)
TOUCH_WEAR_M = 2e-9
TOUCH_OVERSHOOT_M = 1e-6
CRASH_OVERTRAVEL_M = 20e-6
APPROACH_FINAL_S = Z_SCAN_RANGE_M / 2 / APPROACH_SPEED_M_S
FD_MOVE_S = 0.05
DEFAULT_DRIVE_RES_M = 20e-9                        # free amplitude at resonance of the frequency sweep before a drive is set
FREQ_POINTS_TT = 2048
FALSE_ENGAGE_VERTICAL = 0.9                        # |VERTICAL| / INTENSITY beyond which the amplitude detection clips (dynamic modes)
PSPD_TOUCH_LIMIT = 0.9                             # touch detection needs |VERTICAL| below this fraction of the intensity
SAT_LIMIT = 0.98                                   # PSPD saturation (fraction of the intensity)


class ParkNx10Runtime:
    """Park NX10: control electronics and SmartScan, SLD beam and PSPD alignment, NX head on the dovetail with probe exchange, motorized XY / Z /
    focus stages, on-axis camera, automatic approach, imaging in contact / tapping / True Non-Contact modes with the Z feedback, force-distance
    spectroscopy, thermal tune and sensitivity calibration, SmartAnalysis-like processing (SI units)."""

    def __init__(self, instance_id=INSTANCE_ID):
        self.instance_id = instance_id
        self.t = 0.0
        self.revision = 0
        self._floor_memo = {}
        self._large_requests = {}
        self.plant = None
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.error = None
        self.faults = set()
        self.fault_log = []
        self.beam_switch = False
        self.head_state = 'attached'
        self.head_move = None                                      # dict(until, to) while the head slides on the dovetail
        self.exchange = None                                       # dict(until, action, probe_id) while a probe is mounted / unmounted
        self.mode = None
        self.cal = dict(cantilever_db=None, spring_constant_n_m=None, spring_constant_source=None, sensitivity_v_per_m=None, sensitivity_source=None,
                        resonance_hz=None, quality_factor=None, updated_s=None)
        self.probes = {}
        self.mounted = None
        self.knobs = {k: 0.0 for k in KNOBS}
        self.drive = None                                          # dict(frequency_hz, amplitude_m (display at set time), A_res (true))
        self.xy, self.xy_target = [0.0, 0.0], [0.0, 0.0]
        self.qz, self.qz_target, self.qz_speed = 0.0, 0.0, Z_STAGE_MAX_SPEED_M_S
        self.qz_stop = None                                        # planned stop of a manual move: dict(q, kind)
        self.qf, self.qf_target = FOCUS_REF_M, FOCUS_REF_M
        self.samples = {}
        self.on_chuck = None
        self.engaged = None                                        # dict while the feedback holds the tip on the surface
        self.feedback = None
        self.records = {}
        self.seq = dict(img=0, fd=0, tt=0, fs=0, pr=0, op=0, mount=0, probe=0)
        self.op = None
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self.disturb_t = 0.0
        self._principals, self._requests, self._owners = {}, {}, {}

    # ---------------------------------------------------------------- basics
    @property
    def time_s(self):
        return self.t

    def register_principal(self, name, visibility):
        if visibility not in ('agent', 'evaluator') or name in self._principals:
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return ParkNx10Session(self, name)

    def _event(self, kind, code, message, rid=None, t=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t if t is None else t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _seed(self):
        return self.plant['noise_seed'] if self.plant else 0

    def _rng(self, *keys):
        return np.random.default_rng([self._seed()] + [int(k) & 0xFFFFFFFF for k in keys])

    def _probe(self):
        return self.probes.get(self.mounted) if self.mounted else None

    def _fault_active(self, fault, t):
        on = False
        for (tt, f, a) in self.fault_log:
            if f == fault and tt <= t:
                on = a
        return on

    # ---------------------------------------------------------------- derived physical state
    def _ready(self):
        return self.power and self.state == 'ready'

    def _beam_emitting(self):
        return self._ready() and self.beam_switch and self.head_state == 'attached' and 'sld_failure' not in self.faults

    def _optics(self):
        """Reflected intensity, true optical-lever sensitivity and the reflected spot on the photodiode for the mounted probe and the knobs."""
        pr = self._probe()
        pv = pl = 0.0
        if pr is not None:
            pv = pr['mount']['pv0'] + KNOB_PSPD_M_PER_TURN * self.knobs['pspd_y']
            pl = pr['mount']['pl0'] + KNOB_PSPD_M_PER_TURN * self.knobs['pspd_x']
        if pr is None or not self._beam_emitting() or pr['damage'] == 'cantilever_broken':
            return dict(I=0.0, S=0.0, p_v=pv, p_l=pl, x=None, y=None)
        tr = pr['truth']
        x = KNOB_BEAM_M_PER_TURN * self.knobs['beam_x'] - pr['mount']['cx']
        y = KNOB_BEAM_M_PER_TURN * self.knobs['beam_y'] - pr['mount']['cy']
        I = (INTENSITY_COATED_V if tr['reflective'] else INTENSITY_UNCOATED_V) * spot_fraction(x, y, tr['L'], tr['w'])
        return dict(I=I, S=lever_sensitivity(I, x, tr['L'], tr['lever_gain']), p_v=pv, p_l=pl, x=x, y=y)

    def _press_depth(self):
        """Depth by which the retracted, not engaged probe is pushed into what lies under it (Z-stage touch stop or crash, an approach without a
        trigger): the tip apex rests on the surface and the cantilever takes the deflection (m; 0 when the tip is free)."""
        pr = self._probe()
        if self.engaged is not None or self._approach_triggered() or self.head_state != 'attached' or pr is None or pr['damage'] == 'cantilever_broken':
            return 0.0                                                   # a broken lever neither deflects nor carries a load
        return max(0.0, self._floor_under_tip() - self._tip_z())

    def _deflection_now(self):
        e = self.engaged
        if e is not None:
            return e['mp']['d_set'] if e['mp']['mode'] == 'contact' and not e.get('false') else 0.0
        if self._approach_triggered():
            mp = self.op['extra']['mp']
            return mp['d_set'] if mp['mode'] == 'contact' else 0.0
        return self._press_depth()

    def _pspd(self):
        o = self._optics()
        if o['I'] <= 0:
            return 0.0, 0.0, 0.0
        return o['I'], float(pspd_signal(o['I'], o['p_v'], o['S'], self._deflection_now())), float(pspd_signal(o['I'], o['p_l'], o['S'], 0.0))

    def _sample(self):
        return self.samples[self.on_chuck] if self.on_chuck else None

    @staticmethod
    def _top_z(s):
        return CHUCK_TOP_Z + (HOLDER_HEIGHT_M + SAMPLE_DISK_THICKNESS_M if s['mount'] == 'holder' else 0.0) + s['thickness_m']

    def _tip_in_sample(self, xy=None):
        """Point of the sample under the probe axis (sample frame) or None when the probe axis is not over the sample."""
        s = self._sample()
        if s is None:
            return None
        xy = self.xy if xy is None else xy
        p = (-(xy[0] + s['offset'][0]), -(xy[1] + s['offset'][1]))
        w, l = s['size_m']
        if s['shape'] == 'disc':
            inside = p[0] ** 2 + p[1] ** 2 <= (w / 2) ** 2
        else:
            inside = abs(p[0]) <= w / 2 and abs(p[1]) <= l / 2
        return p if inside else None

    def _floor_under_tip(self, xy=None):
        """World height of whatever lies under the probe axis: the sample surface (local maximum over +-60 nm), the holder, the chuck or the deck."""
        xy = self.xy if xy is None else xy
        s = self._sample()
        key = (float(xy[0]), float(xy[1]), self.on_chuck, tuple(s['offset']) if s else None, s['mount'] if s else None)
        if key not in self._floor_memo:
            if len(self._floor_memo) > 256:
                self._floor_memo.clear()
            self._floor_memo[key] = self._floor_uncached(xy)
        return self._floor_memo[key]

    def _floor_uncached(self, xy):
        p = self._tip_in_sample(xy)
        s = self._sample()
        if p is not None:
            return self._top_z(s) + self._local_height(p)
        if s is not None and s['mount'] == 'holder' and (xy[0] + s['offset'][0]) ** 2 + (xy[1] + s['offset'][1]) ** 2 <= HOLDER_RADIUS_M ** 2:
            return CHUCK_TOP_Z + HOLDER_HEIGHT_M
        if abs(xy[0]) <= CHUCK_HALF_M[0] and abs(xy[1]) <= CHUCK_HALF_M[1]:
            return CHUCK_TOP_Z
        return DECK_TOP_Z

    def _local_height(self, p, half=60e-9):
        s = self._sample()
        g = np.linspace(-half, half, 5)
        X, Y = np.meshgrid(p[0] + g, p[1] + g)
        h, _ = s['surface'].evaluate(X, Y)
        return float(np.max(h))

    def _approach_triggered(self):
        """True during the last descent and the settle of an approach whose set point is reached: the feedback holds the tip at the operating point
        while the Z stage brings the scanner to half extension."""
        o = self.op
        return (o is not None and o['kind'] == 'approach' and o['extra']['outcome'] == 'engaged' and 'q_trig' in o['extra']
                and self.qz <= o['extra']['q_trig'] + 1e-12)

    def _extension(self, t=None):
        """Z scanner extension (Z detector) of the engaged feedback: the approach value corrected for the vertical drift since, clipped to the range;
        during the approach fully extended until the trigger, then retracting with the descending Z stage (the tip stays at the operating point)."""
        e = self.engaged
        if e is None:
            if self.op is None or self.op['kind'] != 'approach':
                return 0.0
            if self._approach_triggered():
                return max(Z_SCAN_RANGE_M * APPROACH_TARGET_EXTENSION, Z_SCAN_RANGE_M - (self.op['extra']['q_trig'] - self.qz))
            return Z_SCAN_RANGE_M
        t = self.t if t is None else t
        if e.get('false'):
            return Z_SCAN_RANGE_M
        return min(Z_SCAN_RANGE_M, max(0.0, e['ext0'] - (self._drift(t)[2] - e['drift_z0'])))

    def _tip_z(self):
        return TIP_Z0 + self.qz - self._extension()

    def _drift(self, t=None):
        t = self.t if t is None else t
        if self.plant is None:
            return [0.0, 0.0, 0.0]
        f = ENCLOSURE_DRIFT if self.plant['acoustic_enclosure'] else 1.0
        r = _rng(self._seed(), 211, int(self.disturb_t * 1000.0) & 0xFFFFFFFF)
        ang = 2 * math.pi * float(r.random())
        sz = 1.0 if r.random() < 0.5 else -1.0
        dxy = drift_displacement(t - self.disturb_t, DRIFT_XY_M_S * f)
        return [dxy * math.cos(ang), dxy * math.sin(ang), sz * drift_displacement(t - self.disturb_t, DRIFT_Z_M_S * f)]

    def _xy_moving(self):
        return abs(self.xy[0] - self.xy_target[0]) > 1e-12 or abs(self.xy[1] - self.xy_target[1]) > 1e-12

    def _z_moving(self):
        return abs(self.qz - self.qz_target) > 1e-13

    def _f_moving(self):
        return abs(self.qf - self.qf_target) > 1e-13

    def _still(self):
        return not (self._xy_moving() or self._z_moving())

    def _gap(self):
        if self.engaged is not None or self._approach_triggered():
            return None
        s = self._sample()
        if s is None or self._tip_in_sample() is None:
            return None
        return max(0.0, self._tip_z() - self._floor_under_tip())       # a pressed tip rests on the surface (the cantilever deflects)

    def _tip_force(self):
        e = self.engaged
        if e is None and self._approach_triggered():
            mp = self.op['extra']['mp']
            return mp['force_n'] if mp['mode'] == 'contact' else mp['mats'][0]['force']
        if e is None:
            d = self._press_depth()
            return self._probe()['truth']['k'] * d if d > 0 else None
        if e.get('false'):
            return None
        mp = e['mp']
        if mp['mode'] == 'contact':
            return mp['force_n']
        return mp['mats'][0]['force']

    def _camera(self):
        pr = self._probe()
        z_f = TIP_Z0 + self.qz + (pr['truth']['tip_height'] if pr else DEFAULT_TIP_HEIGHT_M) - (self.qf - FOCUS_REF_M)
        out = dict(cantilever_sharpness=0.0, sample_sharpness=0.0, cantilever_in_view=False, cantilever_offset_m=None, spot_visible=False, spot_offset_m=None,
                   field_of_view_m=list(FIELD_OF_VIEW_M))
        if not self._ready() or self.head_state != 'attached':
            return out
        z_floor = self._floor_under_tip()
        out['sample_sharpness'] = math.exp(-((z_f - z_floor) / DEPTH_OF_FIELD_M) ** 2)
        if pr is None or pr['damage'] == 'cantilever_broken':
            return out
        z_c = TIP_Z0 + self.qz - self._extension() + pr['truth']['tip_height']
        out['cantilever_sharpness'] = math.exp(-((z_f - z_c) / DEPTH_OF_FIELD_M) ** 2)
        pan = (KNOB_OPTICS_M_PER_TURN * self.knobs['optics_x'], KNOB_OPTICS_M_PER_TURN * self.knobs['optics_y'])
        off = (pr['mount']['cx'] - pan[0], pr['mount']['cy'] - pan[1])
        if abs(off[0]) < FIELD_OF_VIEW_M[0] / 2 and abs(off[1]) < FIELD_OF_VIEW_M[1] / 2:
            out['cantilever_in_view'] = True
            out['cantilever_offset_m'] = [round(off[0] / 0.5e-6) * 0.5e-6, round(off[1] / 0.5e-6) * 0.5e-6]
            o = self._optics()
            if o['x'] is not None and out['cantilever_sharpness'] > 0.2:
                out['spot_visible'] = True
                out['spot_offset_m'] = [round(o['x'] / 0.5e-6) * 0.5e-6, round(o['y'] / 0.5e-6) * 0.5e-6]
        return out

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        o = self.op
        prog = rem = None
        if o is not None:
            prog = min(1.0, max(0.0, (self.t - o['t0']) / o['duration_s'])) if o['duration_s'] > 0 else 0.0
            rem = max(0.0, o['t0'] + o['duration_s'] - self.t)
        I, V, L = self._pspd()
        pr = self._probe()
        s_on = self._sample()
        amp = None
        if self.drive is not None and pr is not None and self.mode in ('tapping', 'non_contact'):
            opt = self._optics()
            if self.engaged is not None:
                amp = self.feedback.get('setpoint_amplitude_m') if not self.engaged.get('false') else 0.0
            elif opt['S'] > 0 and self.cal['sensitivity_v_per_m']:
                amp = self.drive['A_res'] * float(lorentz_amp(self.drive['frequency_hz'], pr['truth']['f0'], pr['truth']['Q'])) * opt['S'] / self.cal['sensitivity_v_per_m']
            else:
                amp = 0.0
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'system.state': self.state,
            'system.error_code': self.error, 'beam.switch_on': self.beam_switch, 'beam.emitting': self._beam_emitting(),
            'head.state': 'moving' if self.head_move else self.head_state, 'head.mode': self.mode,
            'probe.box_ids': sorted(k for k, p in self.probes.items() if p['location'] == 'box'),
            'probe.mounted': dict(probe_id=self.mounted, type_id=pr['type_id']) if pr else None, 'probe.exchanging': self.exchange is not None,
            **{f'knob.{k}_turns': self.knobs[k] for k in KNOBS}, 'pspd.intensity_v': I, 'pspd.vertical_v': V, 'pspd.lateral_v': L,
            'ncm.drive': dict(frequency_hz=self.drive['frequency_hz'], amplitude_m=self.drive['amplitude_m']) if self.drive else None, 'ncm.amplitude_m': amp,
            'stage.xy_m': list(self.xy), 'stage.xy_target_m': list(self.xy_target), 'stage.xy_moving': self._xy_moving(), 'stage.x_m': self.xy[0], 'stage.y_m': self.xy[1],
            'z_stage.position_m': self.qz, 'z_stage.target_m': self.qz_target, 'z_stage.moving': self._z_moving(),
            'focus.position_m': self.qf, 'focus.target_m': self.qf_target, 'focus.moving': self._f_moving(),
            'sample.bench_ids': sorted(k for k, s in self.samples.items() if s['location'] == 'bench'),
            'sample.on_chuck': (dict(sample_id=self.on_chuck, offset_x_m=s_on['offset'][0], offset_y_m=s_on['offset'][1], mount=s_on['mount']) if s_on else None),
            'engage.state': 'engaged' if self.engaged is not None else ('approaching' if o is not None and o['kind'] == 'approach' else 'retracted'),
            'zscanner.extension_m': self._extension(), 'feedback.setpoint': copy.deepcopy(self.feedback), 'calibration.table': dict(self.cal),
            'operation.id': (o['record_id'] or o['op']) if o else None, 'operation.kind': o['kind'] if o else None, 'operation.progress': prog,
            'operation.remaining_s': rem, 'operation.started_by_other_agent': False,
            'scan.lines_completed': (self.records[o['record_id']]['meta']['lines_completed'] if o is not None and o['kind'] == 'scan' else None),
            'result.last_record_id': self._last_record(),
            'truth.probe': self._probe_truth(self.mounted) if pr else None, 'truth.probes': {k: self._probe_truth(k) for k in self.probes},
            'truth.samples': self._truth_samples(), 'truth.tip_force_n': self._tip_force(), 'truth.gap_m': self._gap(), 'truth.drift_m': self._drift(),
            'truth.faults': sorted(self.faults),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _probe_truth(self, pid):
        p = self.probes[pid]
        tr = p['truth']
        return dict(probe_id=pid, type_id=p['type_id'], spring_constant_n_m=tr['k'], resonance_hz=tr['f0'], quality_factor=tr['Q'], tip_radius_m=p['radius'],
                    tip_radius_initial_m=tr['R0'], lever_gain=tr['lever_gain'], damage=p['damage'])

    def _truth_samples(self):
        return {k: {kk: copy.deepcopy(vv) for kk, vv in s.items() if kk != 'surface'} for k, s in self.samples.items()}

    def _last_record(self, keep=None):
        done = [k for k, r in self.records.items() if r['status'] == 'completed' and (keep is None or keep(k))]
        return max(done, key=lambda k: (self.records[k]['finished_s'] or 0.0, k), default=None)

    # ---------------------------------------------------------------- invoke
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        a = coerce(CAPS[capability_id]['input_schema'], a)
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**a)
        if CAPS[capability_id]['kind'] not in ('query', 'simulation') and capability_id not in PURE_ANALYSIS:
            self.revision += 1
        return out

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_ready(self, allow_error=False):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')
        if self.state != 'ready':
            raise ActionRejected('NOT_READY')
        if self.error is not None and not allow_error:
            raise ActionRejected('ERROR_PENDING')

    def _need_idle(self):
        if self.op is not None:
            raise ActionRejected('MEASUREMENT_RUNNING')

    def _need_retracted(self):
        if self.engaged is not None:
            raise ActionRejected('ENGAGED')

    def _need_still(self):
        if not self._still() or self.head_move is not None or self.exchange is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')

    def _need_head(self):
        if self.head_move is not None or self.head_state != 'attached':
            raise ActionRejected('HEAD_NOT_ATTACHED')

    def _need_probe(self):
        pr = self._probe()
        if pr is None:
            raise ActionRejected('NO_PROBE')
        return pr

    def _need_mode(self):
        if self.mode is None or self.cal['cantilever_db'] is None:
            raise ActionRejected('MODE_NOT_CONFIGURED')

    def _need_signal(self):
        o = self._optics()
        if not self._beam_emitting():
            raise ActionRejected('BEAM_OFF')
        if o['I'] < INTERLOCK_INTENSITY_V:
            raise ActionRejected('INTENSITY_LOW')
        return o

    def _clearance(self, top):
        return self._tip_z() - top

    def _top_z_max(self, s):
        """Highest point of a sample: the nominal top, the height range of its topography and its tilt (with the mean slope of a step staircase) at
        the edge of the footprint."""
        tx, ty = s['surface'].slope()
        w, l = s['size_m']
        edge = math.hypot(tx, ty) * w / 2 if s['shape'] == 'disc' else abs(tx) * w / 2 + abs(ty) * l / 2
        return self._top_z(s) + s['surface'].characteristic_height() + edge

    # ---------------------------------------------------------------- setup
    def _cmd_plant_configure(self, humidity, room_k, acoustic_enclosure, vibration_isolation, noise_seed):
        if self.power or self.op is not None:
            raise ActionRejected('POWER_ON')
        self.plant = dict(humidity=humidity, room_k=room_k, acoustic_enclosure=acoustic_enclosure, vibration_isolation=vibration_isolation, noise_seed=noise_seed)
        return dict(plant=dict(self.plant))

    def _cmd_sample_provide(self, sample_id, template_id, seed, tilt_rad, topography_overrides=None):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('DUPLICATE_SAMPLE')
        if len(self.samples) >= MAX_SAMPLES:
            raise ActionRejected('TOO_MANY_SAMPLES')
        tpl = sample_templates()['templates'][template_id]
        topo = flatten_topography(tpl['topography'])
        for k, v in (topography_overrides or {}).items():
            if k not in topo:
                raise ActionRejected('OVERRIDE_NOT_APPLICABLE')
            topo[k] = v
        work = topography_work(topo)
        if work is None:
            raise ActionRejected('INVALID_ARGUMENT')                   # out of range or contradictory topography parameters
        if work > 1.0:
            raise ActionRejected('SIMULATION_LIMIT')                   # too many procedural objects per tile for the simulation
        size = list(tpl['size_m'])
        th = float(flat_value(tpl['thickness_m']))
        if max(size) > SAMPLE_OPEN_MAX_M or th > SAMPLE_MAX_THICKNESS_M:
            raise ActionRejected('SAMPLE_TOO_LARGE')
        mats = [m for m in (tpl['substrate'], tpl['film'], topo.get('material'), topo.get('material_b')) if m]
        for m in mats:
            material(m)
        mount = 'holder' if max(size) <= HOLDER_MAX_SIZE_M else 'chuck'
        s = dict(sample_id=sample_id, template_id=template_id, substrate=tpl['substrate'], film=tpl['film'], shape=tpl['shape'], size_m=size, thickness_m=th,
                 mount=mount, topography=topo, seed=seed, tilt_rad=list(tilt_rad), location='bench', offset=[0.0, 0.0])
        s['surface'] = Surface(s)
        self.samples[sample_id] = s
        self._event('sample', 'SAMPLE_PROVIDED', f'样品{sample_id}放在实验台上')
        return dict(sample_id=sample_id, template_id=template_id, location='bench')

    def _cmd_probe_provide(self, probe_id, type_id):
        self._need_plant()
        if probe_id in self.probes:
            raise ActionRejected('DUPLICATE_PROBE')
        if len(self.probes) >= MAX_PROBES:
            raise ActionRejected('TOO_MANY_PROBES')
        nom = probe_nominal(type_id)
        self.seq['probe'] += 1
        truth = draw_probe_truth(nom, self._rng(307, self.seq['probe']))
        self.probes[probe_id] = dict(probe_id=probe_id, type_id=type_id, truth=truth, radius=truth['R0'], damage='none', location='box', mount=None)
        self._event('probe', 'PROBE_PROVIDED', f'探针{probe_id}（{nom["name"]}）放进探针盒')
        return dict(probe_id=probe_id, type_id=type_id, location='box')

    def _cmd_sample_place(self, sample_id, offset_x_m, offset_y_m):
        self._need_plant()
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        if self.on_chuck is not None:
            raise ActionRejected('CHUCK_OCCUPIED')
        self._need_idle()
        self._need_retracted()
        self._need_still()
        half = max(s['size_m']) / 2
        if abs(offset_x_m) + half > SAMPLE_OPEN_MAX_M / 2 or abs(offset_y_m) + half > SAMPLE_OPEN_MAX_M / 2:
            raise ActionRejected('SAMPLE_OUT_OF_AREA')
        if s['mount'] == 'holder' and math.hypot(offset_x_m, offset_y_m) > HOLDER_RADIUS_M:
            raise ActionRejected('SAMPLE_OUT_OF_AREA')
        if self._clearance(self._top_z_max(s)) < PLACE_CLEARANCE_M:
            raise ActionRejected('COLLISION_RISK')
        s['location'], s['offset'] = 'chuck', [offset_x_m, offset_y_m]
        self.on_chuck = sample_id
        self.disturb_t = self.t
        self._event('sample', 'SAMPLE_PLACED', f'样品{sample_id}放到吸盘上（{"磁性样品座" if s["mount"] == "holder" else "直接放吸盘"}）')
        return dict(sample_id=sample_id, offset_x_m=offset_x_m, offset_y_m=offset_y_m, under_tip=self._tip_in_sample() is not None)

    def _cmd_sample_remove(self, sample_id):
        self._need_plant()
        if self.on_chuck != sample_id:
            raise ActionRejected('SAMPLE_NOT_ON_CHUCK')
        self._need_idle()
        self._need_retracted()
        self._need_still()
        s = self.samples[sample_id]
        if self._clearance(self._top_z_max(s)) < PLACE_CLEARANCE_M:
            raise ActionRejected('COLLISION_RISK')
        s['location'], s['offset'] = 'bench', [0.0, 0.0]
        self.on_chuck = None
        self._event('sample', 'SAMPLE_REMOVED', f'样品{sample_id}取回实验台')
        return dict(sample_id=sample_id)

    # ---------------------------------------------------------------- power, beam, faults
    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            self.power, self.state, self.state_t0 = True, 'booting', self.t
            self.disturb_t = self.t
            self._event('power', 'POWER_ON', '控制电子单元上电，SmartScan初始化')
        elif not enabled and self.power:
            self._power_off()
        return dict(enabled=self.power, state=self.state)

    def _cmd_power_press_button(self):
        return self._cmd_power_set(not self.power)

    def _power_off(self):
        if self.op is not None:
            self._fail_op('POWER_OFF')
        self._lift_scanner()
        self.xy_target = list(self.xy)
        self.qz_target, self.qz_stop = self.qz, None
        self.qf_target = self.qf
        self.power, self.state = False, 'off'
        self._event('power', 'POWER_OFF', '断电：Z扫描器缩回，电动台停在当时位置')

    def _lift_scanner(self):
        if self.engaged is not None:
            self.engaged = None
            self._event('approach', 'TIP_LIFTED', 'Z扫描器缩回，针尖离开表面')

    def _cmd_beam_set(self, on):
        if self.op is not None and not on and self.op['kind'] in BEAM_OPS:
            raise ActionRejected('MEASUREMENT_RUNNING')
        was = self._beam_emitting()
        self.beam_switch = on
        if was and not self._beam_emitting():
            self._lift_scanner()
        self._event('beam', 'BEAM_ON' if on else 'BEAM_OFF', f'光束开关{"打开" if on else "关闭"}')
        return dict(on=self.beam_switch, emitting=self._beam_emitting())

    def _cmd_beam_press_switch(self):
        return self._cmd_beam_set(not self.beam_switch)

    def _raise_error(self, code):
        if self.power and self.error is None:
            self.error = code
            self._event('error', code, ERROR_ZH.get(code, code))

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        before = fault in self.faults
        if active:
            self.faults.add(fault)
        else:
            self.faults.discard(fault)
        if before != active:
            self.fault_log.append((self.t, fault, active))
            self._event('fault', 'FAULT_' + ('ON' if active else 'OFF'), f'{fault} {"注入" if active else "清除"}')
        if active:
            if fault == 'sld_failure':
                if self.op is not None and self.op['kind'] in BEAM_OPS:
                    self._fail_op('SLD_FAILURE')
                self._lift_scanner()
                if self.power:
                    self._raise_error('SLD_FAILURE')
            elif fault == 'z_stage_stall' and (self._z_moving() or (self.op is not None and self.op['kind'] == 'approach')):
                self._stall_z()
            elif fault == 'xy_stage_stall' and self._xy_moving():
                self.xy_target = list(self.xy)
                self._raise_error('XY_STAGE_FAULT')
            elif fault == 'tip_broken':
                pr = self._probe()
                if pr is not None and pr['damage'] == 'none':
                    pr['damage'] = 'tip_broken'
                    pr['radius'] = max(pr['radius'], BROKEN_TIP_RADIUS_M)
        return dict(faults=sorted(self.faults))

    def _stall_z(self):
        if self.op is not None and self.op['kind'] == 'approach':
            self._fail_op('Z_STAGE_FAULT')
        self.qz_target, self.qz_stop = self.qz, None
        self._raise_error('Z_STAGE_FAULT')

    def _cmd_system_acknowledge(self):
        self._need_plant()
        if self.error is None:
            return dict(cleared=None)
        cause = {'SLD_FAILURE': 'sld_failure', 'Z_STAGE_FAULT': 'z_stage_stall', 'XY_STAGE_FAULT': 'xy_stage_stall'}.get(self.error)
        if cause in self.faults:
            raise ActionRejected('CAUSE_PERSISTS')
        code, self.error = self.error, None
        self._event('error', 'ACKNOWLEDGED', f'确认错误{code}')
        return dict(cleared=code)

    # ---------------------------------------------------------------- head, probes, knobs
    def _cmd_head_remove(self):
        self._need_plant()
        self._need_idle()
        self._need_retracted()
        self._need_still()
        if self.head_state != 'attached':
            raise ActionRejected('HEAD_NOT_ATTACHED')
        if self._f_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')                 # the objective must be at rest (and high enough) while the head slides
        s = self._sample()
        if s is not None and self._clearance(self._top_z_max(s)) < HEAD_CLEARANCE_M:
            raise ActionRejected('COLLISION_RISK')
        if self.qf > FOCUS_REF_M + FOCUS_HEAD_CLEARANCE_M:
            raise ActionRejected('FOCUS_TOO_LOW')                      # manual: raise the focus stage if it is too close to the head
        self._beam_switch_rule()
        self.head_move = dict(until=self.t + HEAD_SWAP_S, to='removed')
        self._event('head', 'HEAD_REMOVING', '松开燕尾导轨锁，取下NX测头')
        return dict(state='moving', duration_s=HEAD_SWAP_S)

    def _beam_switch_rule(self):
        """Manual: the beam switch must be off before the head is mounted or unmounted, otherwise the beam diode may be damaged."""
        if self.beam_switch and self.power and 'sld_failure' not in self.faults:
            self.faults.add('sld_failure')
            self.fault_log.append((self.t, 'sld_failure', True))
            self._event('fault', 'SLD_DAMAGED', '光束开关未关就装卸测头，SLD损坏（评测者可见；清除sld_failure故障＝更换）')
            self._raise_error('SLD_FAILURE')

    def _cmd_head_attach(self):
        self._need_plant()
        if self.head_move is not None or self.exchange is not None or self._f_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')
        if self.head_state != 'removed':
            raise ActionRejected('HEAD_ATTACHED')
        s = self._sample()
        if s is not None and self._clearance(self._top_z_max(s)) < HEAD_CLEARANCE_M:
            raise ActionRejected('COLLISION_RISK')
        if self.qf > FOCUS_REF_M + FOCUS_HEAD_CLEARANCE_M:
            raise ActionRejected('FOCUS_TOO_LOW')
        self._beam_switch_rule()
        self.head_move = dict(until=self.t + HEAD_SWAP_S, to='attached')
        self._event('head', 'HEAD_ATTACHING', '把NX测头推回燕尾导轨并锁紧')
        return dict(state='moving', duration_s=HEAD_SWAP_S)

    def _cmd_probe_mount(self, probe_id):
        self._need_plant()
        if self.head_move is not None or self.head_state != 'removed':
            raise ActionRejected('HEAD_ATTACHED')
        if self.exchange is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')
        p = self.probes.get(probe_id)
        if p is None:
            raise ActionRejected('UNKNOWN_PROBE')
        if self.mounted is not None:
            raise ActionRejected('PROBE_MOUNTED')
        if p['location'] != 'box':
            raise ActionRejected('PROBE_NOT_IN_BOX')
        self.exchange = dict(until=self.t + PROBE_MOUNT_S, action='mount', probe_id=probe_id)
        p['location'] = 'handling'
        self._event('probe', 'PROBE_MOUNTING', f'装探针{probe_id}')
        return dict(probe_id=probe_id, duration_s=PROBE_MOUNT_S)

    def _cmd_probe_unmount(self):
        self._need_plant()
        if self.head_move is not None or self.head_state != 'removed':
            raise ActionRejected('HEAD_ATTACHED')
        if self.exchange is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')
        if self.mounted is None:
            raise ActionRejected('NO_PROBE')
        pid = self.mounted
        self.exchange = dict(until=self.t + PROBE_MOUNT_S, action='unmount', probe_id=pid)
        self.mounted = None
        self.drive = None                                               # the drive belongs to the resonance of this probe: set it again after the exchange
        self.probes[pid]['location'] = 'handling'
        self._event('probe', 'PROBE_UNMOUNTING', f'取下探针{pid}')
        return dict(probe_id=pid, duration_s=PROBE_MOUNT_S)

    def _mount_offsets(self, pid):
        self.seq['mount'] += 1
        r = self._rng(401, self.seq['mount'])
        return dict(cx=PREALIGN_INSET_M + float(r.normal(0.0, PREALIGN_SIGMA_M)), cy=float(r.normal(0.0, PREALIGN_SIGMA_M)), pv0=float(r.normal(0.0, PSPD_MOUNT_SIGMA_M)),
                    pl0=float(r.normal(0.0, PSPD_MOUNT_SIGMA_M)))

    def _cmd_head_configure(self, mode, cantilever_db):
        self._need_ready()
        self._need_idle()
        self._need_retracted()
        nom = probe_nominal(cantilever_db)
        if cantilever_db != self.cal['cantilever_db']:
            self.drive = None                                           # a new cantilever entry: sweep the resonance and set the drive again
            self.cal = dict(cantilever_db=cantilever_db, spring_constant_n_m=nom['k'], spring_constant_source='database',
                            sensitivity_v_per_m=SENSITIVITY_V_PER_M_100UM * (100e-6 / nom['L']), sensitivity_source='database', resonance_hz=nom['f0'],
                            quality_factor=None, updated_s=self.t)
        self.mode = mode
        return dict(mode=mode, cantilever_db=cantilever_db, calibration=dict(self.cal))

    def _cmd_head_turn_knob(self, knob, turns):
        return self._cmd_head_set_knob(knob, self.knobs[knob] + turns)

    def _cmd_head_set_knob(self, knob, turns_total):
        self._need_plant()
        self._need_idle()
        self._need_retracted()
        if abs(turns_total) > KNOB_LIMIT_TURNS[knob] + 1e-12:
            raise ActionRejected('KNOB_LIMIT')
        self.knobs[knob] = turns_total
        return dict(knob=knob, turns_total=turns_total)

    # ---------------------------------------------------------------- stages and optics
    def _cmd_stage_move_xy(self, x_m, y_m):
        self._need_ready()
        self._need_idle()
        self._need_retracted()
        if self.head_move is not None or self.exchange is not None or self._z_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')                 # one motorized stage at a time
        q = [round(x_m / XY_STAGE_RESOLUTION_M) * XY_STAGE_RESOLUTION_M, round(y_m / XY_STAGE_RESOLUTION_M) * XY_STAGE_RESOLUTION_M]
        if self.head_state == 'attached' and self._probe() is not None:
            if self._tip_z() - self._floor_under_tip() < 1e-6:
                raise ActionRejected('COLLISION_RISK')          # the tip rests on the sample, the holder or the chuck (touch stop): lift it first
            hit, edge = self._path_clearance(q)
            if hit or edge:
                raise ActionRejected('COLLISION_RISK')          # something on the straight path is above the tip, or an edge passes under it within 100 um
        self.xy_target = q
        if 'xy_stage_stall' in self.faults and self._xy_moving():
            self.xy_target = list(self.xy)
            self._raise_error('XY_STAGE_FAULT')
        return dict(target_m=list(self.xy_target),              # a stalled stage keeps its position: the returned duration is then 0
                    duration_s=math.hypot(self.xy_target[0] - self.xy[0], self.xy_target[1] - self.xy[1]) / XY_STAGE_SPEED_M_S)

    def _coarse_floor(self, xy):
        """(height, kind) of what lies under the probe axis at stage position xy, without the nanoscale topography (the sample counts with the
        height range of its topography)."""
        s = self._sample()
        if s is not None and self._tip_in_sample(xy) is not None:
            return self._top_z(s) + s['surface'].characteristic_height(), 'sample'
        if s is not None and s['mount'] == 'holder' and (xy[0] + s['offset'][0]) ** 2 + (xy[1] + s['offset'][1]) ** 2 <= HOLDER_RADIUS_M ** 2:
            return CHUCK_TOP_Z + HOLDER_HEIGHT_M, 'holder'
        if abs(xy[0]) <= CHUCK_HALF_M[0] and abs(xy[1]) <= CHUCK_HALF_M[1]:
            return CHUCK_TOP_Z, 'chuck'
        return DECK_TOP_Z, 'deck'

    def _path_supports(self, target):
        """Supports under the probe axis along the straight XY stage path from here to target: the end points and the exact intersections of the
        path with the sample footprint and the holder disc (the sample-frame point under the axis moves on p(t) = -(xy(t) + offset))."""
        kinds = {self._coarse_floor(self.xy)[1], self._coarse_floor(target)[1]}
        s = self._sample()
        if s is not None:
            a = (-(self.xy[0] + s['offset'][0]), -(self.xy[1] + s['offset'][1]))
            b = (-(target[0] + s['offset'][0]), -(target[1] + s['offset'][1]))
            w, l = s['size_m']
            if _segment_hits_disc(a, b, w / 2) if s['shape'] == 'disc' else _segment_hits_box(a, b, w / 2, l / 2):
                kinds.add('sample')
            inside = self._tip_in_sample(self.xy) is not None and self._tip_in_sample(target) is not None    # convex footprint: the whole path is on it
            if s['mount'] == 'holder' and not inside and _segment_hits_disc(a, b, HOLDER_RADIUS_M):
                kinds.add('holder')                                      # the holder disc under the sample counts only off the sample
        return kinds

    def _path_clearance(self, target):
        """(hit, edge_close) for the straight XY stage path to target: something on the path is above the retracted tip, or the path crosses from
        one support to another (sample edge, holder, chuck) while the tip is less than 100 um above the highest one."""
        s = self._sample()
        kinds = self._path_supports(target)
        heights = [dict(holder=CHUCK_TOP_Z + HOLDER_HEIGHT_M, chuck=CHUCK_TOP_Z, deck=DECK_TOP_Z)[k] for k in kinds if k != 'sample']
        if 'sample' in kinds:                                          # the tilted sample is highest at one end of the part of the path over it
            a = (-(self.xy[0] + s['offset'][0]), -(self.xy[1] + s['offset'][1]))
            b = (-(target[0] + s['offset'][0]), -(target[1] + s['offset'][1]))
            w, l = s['size_m']
            span = _segment_clip_disc(a, b, w / 2) if s['shape'] == 'disc' else _segment_clip_box(a, b, w / 2, l / 2)
            ends = [(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])) for t in (span or (0.0, 1.0))]
            tx, ty = s['surface'].slope()                               # tilt and the mean slope of a step staircase
            heights.append(self._top_z(s) + s['surface'].characteristic_height() + max(tx * p[0] + ty * p[1] for p in ends))
        clear = self._tip_z() - max(heights)
        return clear < 0.0, len(kinds) > 1 and clear < 100e-6

    def _touch_point(self):
        """Z-stage position at which the retracted tip touches what lies under it."""
        return self._floor_under_tip() - TIP_Z0

    def _cmd_z_stage_move(self, z_m):
        self._need_ready()
        self._need_idle()
        self._need_retracted()
        if self.head_move is not None or self.exchange is not None or self._xy_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')                 # one motorized stage at a time (the touch stop assumes a still sample)
        if z_m < self.qz - 1e-15 and self._press_depth() > 0.0:
            raise ActionRejected('TIP_IN_CONTACT')                     # F16: the tip already rests on the surface; lift it first
        if z_m < self.qz - 1e-15:
            o = self._optics()
            if o['I'] < INTERLOCK_INTENSITY_V:
                raise ActionRejected('INTENSITY_LOW')                  # manual: 'Laser intensity is too low' - the Z stage may not move down
        self.qz_target, self.qz_speed, self.qz_stop = z_m, Z_STAGE_MAX_SPEED_M_S, None
        if z_m < self.qz and self.head_state == 'attached' and self._probe() is not None:
            q_touch = self._touch_point()
            if z_m < q_touch:
                o = self._optics()
                I, V = o['I'], float(pspd_signal(o['I'], o['p_v'], o['S'], 0.0))
                ok = I >= INTERLOCK_INTENSITY_V and abs(V) < PSPD_TOUCH_LIMIT * I
                self.qz_stop = dict(q=max(z_m, q_touch - (TOUCH_OVERSHOOT_M if ok else CRASH_OVERTRAVEL_M)), kind='touch' if ok else 'crash', at=q_touch)
                self.qz_target = self.qz_stop['q']
        if 'z_stage_stall' in self.faults and self._z_moving():
            self._stall_z()
        return dict(target_m=self.qz_target, duration_s=abs(self.qz_target - self.qz) / Z_STAGE_MAX_SPEED_M_S)

    def _cmd_focus_move(self, position_m):
        self._need_ready()
        if self.head_move is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')                 # the head slides on the dovetail: the objective keeps its height meanwhile
        self.qf_target = position_m
        return dict(target_m=position_m, duration_s=abs(position_m - self.qf) / FOCUS_SPEED_M_S)

    def _cmd_camera_read(self):
        self._need_ready()
        return self._camera()

    def _cmd_retract(self, distance_m):
        self._need_ready(allow_error=True)
        self._need_idle()
        if self.head_move is not None or self.exchange is not None:
            raise ActionRejected('MOTION_IN_PROGRESS')
        self._lift_scanner()
        target = min(Z_STAGE_LIMITS_M[1], self.qz + distance_m)
        self.qz_target, self.qz_speed, self.qz_stop = target, Z_STAGE_MAX_SPEED_M_S, None
        if 'z_stage_stall' in self.faults and self._z_moving():
            self._stall_z()
        return dict(target_m=self.qz_target, duration_s=abs(self.qz_target - self.qz) / Z_STAGE_MAX_SPEED_M_S)

    # ---------------------------------------------------------------- drive and dynamic modes
    def _need_dynamic(self):
        self._need_mode()
        if self.mode not in ('tapping', 'non_contact'):
            raise ActionRejected('MODE_NOT_DYNAMIC')

    def _cmd_ncm_set_drive(self, frequency_hz, amplitude_m):
        self._need_ready()
        self._need_idle()
        self._need_retracted()
        self._need_head()
        pr = self._need_probe()
        self._need_dynamic()
        o = self._need_signal()
        L = float(lorentz_amp(frequency_hz, pr['truth']['f0'], pr['truth']['Q']))
        A_free = amplitude_m * self.cal['sensitivity_v_per_m'] / o['S']
        if L < 0.02 or A_free / L > 2e-6:
            raise ActionRejected('DRIVE_OFF_RESONANCE')                 # the bimorph drive cannot reach this amplitude so far from the resonance
        self.drive = dict(frequency_hz=frequency_hz, amplitude_m=amplitude_m, A_res=A_free / L)
        return dict(frequency_hz=frequency_hz, amplitude_m=amplitude_m)

    def _mode_params(self, setpoint, o):
        s = self._sample()
        mats = s['surface'].materials if s is not None else ['si']
        pr = self._probe()
        probe = dict(pr['truth'], R=pr['radius'])
        return mode_params(self.mode, mats, probe, self.cal, self.drive, setpoint, self.plant['humidity'], o['I'], o['S'], o['p_v'])

    def _setpoint(self, setpoint_force_n, setpoint_amplitude_m):
        if self.mode == 'contact':
            if setpoint_force_n is None or setpoint_amplitude_m is not None:
                raise ActionRejected('SETPOINT_REQUIRED')
            return setpoint_force_n
        if setpoint_amplitude_m is None or setpoint_force_n is not None:
            raise ActionRejected('SETPOINT_REQUIRED')
        if self.drive is None:
            raise ActionRejected('DRIVE_NOT_SET')
        pr, o = self._probe(), self._optics()
        if pr is not None and o['S'] > 0 and self.cal['sensitivity_v_per_m']:
            shown = self.drive['A_res'] * float(lorentz_amp(self.drive['frequency_hz'], pr['truth']['f0'], pr['truth']['Q'])) * o['S'] / self.cal['sensitivity_v_per_m']
            if setpoint_amplitude_m >= shown:
                raise ActionRejected('SETPOINT_OUT_OF_RANGE')          # the set point must be below the free amplitude now shown (ncm.amplitude_m)
        return setpoint_amplitude_m

    # ---------------------------------------------------------------- operations
    def _new_op(self, kind, rid, duration, extra):
        self.seq['op'] += 1
        oid = f'OP{self.seq["op"]:04d}'
        self.op = dict(op=oid, kind=kind, record_id=rid, t0=self.t, duration_s=duration, extra=extra)
        self.operations[oid] = dict(phase='running', kind=kind, record_id=rid)
        return oid

    def _new_record(self, prefix, key, kind, meta, data):
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        self.seq[key] += 1
        rid = f'{prefix}{self.seq[key]:04d}'
        s = self._sample()
        self.records[rid] = dict(record_id=rid, kind=kind, status='running', started_s=self.t, finished_s=None, meta=meta, warnings=[], data=data,
                                 template_source=dict(library=SAMPLE_LIB_PATH.name, template_id=s['template_id'] if s else None,
                                                      substances=list(s['surface'].materials) if s else []))
        return rid

    def _record_bytes(self):
        """Pixel memory held by the records (float32 channels of images, processed maps)."""
        total = 0
        for rec in self.records.values():
            for v in (rec.get('data') or {}).values():
                if isinstance(v, dict):
                    total += sum(a.nbytes for a in v.values() if isinstance(a, np.ndarray))
                elif isinstance(v, np.ndarray):
                    total += v.nbytes
        return total

    def _need_op_start(self, needs_engaged):
        self._need_ready()
        self._need_idle()
        self._need_head()
        if self.head_move is not None or self.exchange is not None or not self._still():
            raise ActionRejected('MOTION_IN_PROGRESS')
        pr = self._need_probe()
        self._need_mode()
        if needs_engaged and self.engaged is None:
            raise ActionRejected('NOT_ENGAGED')
        if not needs_engaged:
            self._need_retracted()
        return pr

    def _cmd_approach(self, setpoint_force_n, setpoint_amplitude_m, z_servo_gain):
        self._need_op_start(False)
        sp = self._setpoint(setpoint_force_n, setpoint_amplitude_m)
        o = self._need_signal()
        s = self._sample()
        if s is None or self._tip_in_sample() is None:
            raise ActionRejected('NO_SAMPLE_UNDER_TIP')
        self._need_free_lever()
        mp = self._mode_params(sp, o)
        z_s = self._floor_under_tip()
        off0 = mp['mats'][0]['offset']
        q_trig = z_s + off0 + Z_SCAN_RANGE_M - TIP_Z0                  # scanner fully extended during the descent
        q_final = q_trig - Z_SCAN_RANGE_M * (1 - APPROACH_TARGET_EXTENSION)
        V0 = float(pspd_signal(o['I'], o['p_v'], o['S'], 0.0))
        outcome = 'engaged'
        if self.mode == 'contact':
            dV = sp * self.cal['sensitivity_v_per_m'] / self.cal['spring_constant_n_m']
            if V0 + dV >= SAT_LIMIT * o['I']:
                outcome = 'crash'                                          # the VERTICAL signal saturates before the set point: no trigger
        elif abs(V0) > FALSE_ENGAGE_VERTICAL * o['I']:
            outcome = 'false'                                              # the clipped amplitude signal reads below the set point at once
        if outcome == 'false':
            q_end, t_desc = self.qz, 0.0
        elif outcome == 'crash':
            q_end = min(self.qz, q_trig) - CRASH_OVERTRAVEL_M
            t_desc = max(0.0, self.qz - q_end) / APPROACH_SPEED_M_S
        else:
            q_end = q_final
            t_desc = max(0.0, self.qz - q_trig) / APPROACH_SPEED_M_S + abs(min(self.qz, q_trig) - q_final) / APPROACH_SPEED_M_S
        if q_end < Z_STAGE_LIMITS_M[0]:
            outcome, q_end = 'limit', Z_STAGE_LIMITS_M[0]
            t_desc = max(0.0, self.qz - q_end) / APPROACH_SPEED_M_S
        dur = t_desc + (APPROACH_SETTLE_S if outcome in ('engaged', 'false') else 0.0)
        self.feedback = dict(setpoint_force_n=setpoint_force_n, setpoint_amplitude_m=setpoint_amplitude_m, z_servo_gain=z_servo_gain)
        oid = self._new_op('approach', None, dur, dict(outcome=outcome, q_start=self.qz, q_end=q_end, t_desc=t_desc, mp=mp, z_s=z_s, q_trig=q_trig))
        self.qz_target, self.qz_speed, self.qz_stop = q_end, (abs(self.qz - q_end) / t_desc if t_desc > 0 else Z_STAGE_MAX_SPEED_M_S), None
        self._event('approach', 'APPROACH_STARTED', '自动进针开始', rid=oid)
        self.led['approaches'].add(1.0)
        return dict(operation_id=oid)

    def _cmd_scan_start(self, size_m, pixels_x, pixels_y, rate_hz, angle_rad, offset_x_m, offset_y_m, setpoint_force_n, setpoint_amplitude_m, z_servo_gain,
                        save_retrace, retract_after):
        pr = self._need_op_start(True)
        sp = self._setpoint(setpoint_force_n, setpoint_amplitude_m)
        o = self._need_signal()
        if 2 * size_m * rate_hz > MAX_TIP_SPEED_M_S:
            raise ActionRejected('TIP_SPEED_TOO_HIGH')
        reach = size_m / 2 * (abs(math.cos(angle_rad)) + abs(math.sin(angle_rad)))
        if abs(offset_x_m) + reach > XY_SCAN_RANGE_M / 2 + 1e-15 or abs(offset_y_m) + reach > XY_SCAN_RANGE_M / 2 + 1e-15:
            raise ActionRejected('SCAN_OUT_OF_RANGE')
        if pixels_x * pixels_y > SIM_MAX_PIXELS:
            raise ActionRejected('SIMULATION_LIMIT')
        mp = self._mode_params(sp, o)
        chans = ['height', 'error', 'z_drive'] if self.mode == 'contact' else ['height', 'error', 'phase', 'z_drive']
        dirs = ['trace', 'retrace'] if save_retrace else ['trace']
        if self._record_bytes() + 4 * pixels_x * pixels_y * len(chans) * len(dirs) > RECORD_MEMORY_BYTES:
            raise ActionRejected('RECORDS_FULL')                        # the image store of the simulation is full: delete records first
        data = {c: {d: np.full((pixels_y, pixels_x), np.nan, np.float32) for d in dirs} for c in chans}
        s = self._sample()
        meta = dict(mode=self.mode, size_m=size_m, pixels=[pixels_x, pixels_y], rate_hz=rate_hz, angle_rad=angle_rad, offset_m=[offset_x_m, offset_y_m],
                    setpoint_force_n=setpoint_force_n, setpoint_amplitude_m=setpoint_amplitude_m, z_servo_gain=z_servo_gain, channels=chans, directions=dirs,
                    channel_units={c: CHANNEL_UNITS[c] for c in chans}, tip_speed_m_s=2 * size_m * rate_hz, lines_completed=0, probe_id=self.mounted,
                    probe_type=pr['type_id'], sample_id=self.on_chuck, stage_xy_m=list(self.xy), calibration=dict(self.cal),
                    drive=dict(frequency_hz=self.drive['frequency_hz'], amplitude_m=self.drive['amplitude_m']) if self.drive and self.mode != 'contact' else None,
                    pixel_size_m=[size_m / pixels_x, size_m / pixels_y])
        rid = self._new_record('IMG', 'img', 'image', meta, data)
        self.feedback = dict(setpoint_force_n=setpoint_force_n, setpoint_amplitude_m=setpoint_amplitude_m, z_servo_gain=z_servo_gain)
        job = dict(size_m=size_m, pixels_x=pixels_x, pixels_y=pixels_y, rate_hz=rate_hz, angle_rad=angle_rad, offset=(offset_x_m, offset_y_m),
                   p0=self._tip_in_sample() or (0.0, 0.0), q_z=self.qz, sample_top_z=self._top_z(s), surface=s['surface'], mp=mp, probe=dict(pr['truth']),
                   S_true=o['S'], S_cal=self.cal['sensitivity_v_per_m'], z_servo_gain=z_servo_gain, channels=chans, save_retrace=save_retrace,
                   plant=dict(self.plant), t0=self.t, false=bool(self.engaged.get('false')), r_start=pr['radius'])
        if job['false']:
            job['q_z'] = self.qz + 5e-3                                    # a falsely engaged tip hovers far above: the scanner cannot reach
        oid = self._new_op('scan', rid, pixels_y / rate_hz, dict(job=job, blocks_done=0, retract_after=retract_after))
        self.engaged['mp'] = mp
        self._event('scan', 'SCAN_STARTED', f'扫描{rid}开始（{pixels_x}×{pixels_y}，{size_m * 1e6:.3g} µm，{rate_hz:g} Hz）', rid=rid)
        return dict(operation_id=oid)

    def _cmd_fd_acquire(self, z_range_m, rate_hz, trigger_force_n, dwell_s, count, positions_m):
        pr = self._need_op_start(True)
        o = self._need_signal()
        n = count * len(positions_m)
        if n > 64:
            raise ActionRejected('TOO_MANY_CURVES')
        per = 1.0 / rate_hz + dwell_s + FD_MOVE_S
        meta = dict(z_range_m=z_range_m, rate_hz=rate_hz, trigger_force_n=trigger_force_n, dwell_s=dwell_s, count=count, positions_m=copy.deepcopy(positions_m),
                    curves_completed=0, probe_id=self.mounted, probe_type=pr['type_id'], sample_id=self.on_chuck, calibration=dict(self.cal), mode=self.mode)
        rid = self._new_record('FD', 'fd', 'force_curves', meta, dict(curves=[]))
        oid = self._new_op('force_curves', rid, n * per, dict(per=per, n=n, done=0, o=o))
        self._event('force_curve', 'FD_STARTED', f'力曲线{rid}开始（{n}条）', rid=rid)
        return dict(operation_id=oid)

    def _need_free_lever(self):
        if self._press_depth() > 0.0:
            raise ActionRejected('TIP_IN_CONTACT')                     # the tip rests on the surface after a touch stop: lift the Z stage first

    def _cmd_calibration_thermal_tune(self, temperature_k, averages):
        self._need_op_start(False)
        self._need_signal()
        self._need_free_lever()
        dur = 2.0 + 0.1 * averages
        meta = dict(temperature_k=temperature_k, averages=averages, cantilever_db=self.cal['cantilever_db'], sensitivity_v_per_m=self.cal['sensitivity_v_per_m'],
                    probe_id=self.mounted)
        rid = self._new_record('TT', 'tt', 'thermal_tune', meta, {})
        oid = self._new_op('thermal_tune', rid, dur, {})
        self._event('calibration', 'THERMAL_TUNE_STARTED', f'热调谐{rid}开始', rid=rid)
        return dict(operation_id=oid)

    def _cmd_ncm_frequency_sweep(self, start_hz, stop_hz, points):
        self._need_op_start(False)
        self._need_dynamic()
        self._need_signal()
        self._need_free_lever()
        if stop_hz <= start_hz:
            raise ActionRejected('INVALID_ARGUMENT')
        meta = dict(start_hz=start_hz, stop_hz=stop_hz, points=points, probe_id=self.mounted, drive_set=self.drive is not None)
        rid = self._new_record('FS', 'fs', 'frequency_sweep', meta, {})
        oid = self._new_op('frequency_sweep', rid, FREQ_SWEEP_S, {})
        return dict(operation_id=oid)

    # ---------------------------------------------------------------- operation progress
    def _finish(self, rec, status):
        rec['status'] = status
        rec['finished_s'] = self.t

    def _end_op(self, phase, code=None):
        o = self.op
        self.operations[o['op']]['phase'] = phase
        if code:
            self.operations[o['op']]['reason'] = code
        self.op = None
        return o

    def _fail_op(self, code):
        o = self.op
        if o['kind'] == 'scan':
            self._scan_partial(o)
            self._finish(self.records[o['record_id']], 'failed')
            self.records[o['record_id']]['warnings'].append(code.lower())
        elif o['kind'] == 'force_curves':
            self._finish(self.records[o['record_id']], 'failed')
        elif o['record_id']:
            rec = self.records[o['record_id']]
            rec['data'] = {}
            self._finish(rec, 'failed')
        elif o['kind'] == 'approach':
            self.qz_target, self.qz_stop = self.qz, None
        self._end_op('failed', code)
        self._event('error' if o['kind'] != 'approach' else 'approach', code, ERROR_ZH.get(code, code), rid=o['record_id'] or o['op'])

    def _cancel_op(self, entry):
        o = self.op
        if o is None or self.operations[o['op']] is not entry:
            raise ActionRejected('NOT_RUNNING')
        if o['kind'] == 'scan':
            self._scan_partial(o)
            self._finish(self.records[o['record_id']], 'cancelled')
        elif o['kind'] == 'force_curves':
            self._finish(self.records[o['record_id']], 'cancelled')
        elif o['record_id']:
            rec = self.records[o['record_id']]
            rec['data'] = {}
            self._finish(rec, 'cancelled')
        else:
            self.qz_target, self.qz_stop = self.qz, None                # the approach stops where the Z stage is
        self._end_op('cancelled', 'CANCELLED')
        self._event(o['kind'] if o['kind'] in ('approach', 'scan') else 'force_curve' if o['kind'] == 'force_curves' else 'calibration', 'CANCELLED', '操作被取消',
                    rid=o['record_id'] or o['op'])

    def _block_rng(self, rid_seq, j, direction):
        return _rng(self._seed(), 503, rid_seq, j, 1 if direction == 'retrace' else 0)

    def _run_block(self, o, j0, j1):
        job = o['extra']['job']
        rec = self.records[o['record_id']]
        pr = self._probe()
        seq = int(o['record_id'][3:])
        t_start = job['t0'] + j0 / job['rate_hz']
        dxy = self._drift(t_start)[:2]
        out, st = image_block(job, j0, j1, pr['radius'], dxy, lambda t: self._drift(t)[2], lambda t: self._fault_active('vibration_burst', t),
                              lambda j, d: self._block_rng(seq, j, d))
        for ch, dirs in out.items():
            for d, arr in dirs.items():
                rec['data'][ch][d][j0:j1] = arr.astype(np.float32)
        rec['meta']['lines_completed'] = j1
        nl = j1 - j0
        self.led['scan_lines'].add(float(nl))
        self.led['scanner_path_m'].add(2.0 * job['size_m'] * nl)
        mp = job['mp']
        if not job['false'] and st['n']:
            F_mean = st['force_sum'] / st['n']
            factor = {'contact': 1.0, 'tapping': WEAR_TAPPING_FACTOR, 'non_contact': WEAR_NC_FACTOR}[mp['mode']]
            if mp.get('regime') == 'contact_regime':
                factor = WEAR_TAPPING_FACTOR
            hard = min(2.0, max(0.1, math.sqrt(st['Es_max'] / 100e9)))
            dR = WEAR_M_PER_M_N * F_mean * 2.0 * job['size_m'] * nl * factor * hard * (10e-9 / max(pr['radius'], 1e-9))
            r_new = blunted_radius(st['force_max'], pr['radius'] + dR, st['Es_max'])
            pr['radius'] = min(RADIUS_MAX_M, r_new)
            if st['force_max'] > 100e-9 and st['force_max'] > 20 * max(mp.get('force_n') or 0.0, mp['mats'][0]['force']):
                if 'tip_crash' not in rec['warnings']:
                    self.led['tip_crashes'].add(1.0)
                self._warn(rec, 'tip_crash')
        n = max(1, st['n'])
        if st['sat_free'] / n > 0.01:
            self._warn(rec, 'error_saturated')
        if st['scanner_sat'] / n > 0.001:
            self._warn(rec, 'z_scanner_saturated')
        if mp['mode'] == 'contact' and st['sat_hard'] / n > 0.001:
            self._warn(rec, 'pspd_saturated')
        for w in mp.get('warnings', []):
            self._warn(rec, w)
        if job['false']:
            self._warn(rec, 'false_engage')
        if st.get('footprint_truncated'):
            self._warn(rec, 'tip_footprint_truncated')                  # the tip needs a wider footprint than the dilation budget of this image
        if pr['radius'] > 1.5 * job['r_start']:
            self._warn(rec, 'tip_worn')

    @staticmethod
    def _warn(rec, w):
        if w not in rec['warnings']:
            rec['warnings'].append(w)

    def _scan_partial(self, o):
        job = o['extra']['job']
        done = min(job['pixels_y'], int(math.floor((self.t - job['t0']) * job['rate_hz'] + 1e-9)))
        j0 = o['extra']['blocks_done'] * BLOCK_LINES
        if done > j0:
            self._run_block(o, j0, done)

    def _fd_curve(self, o, idx):
        """One force-distance cycle at its position: van der Waals approach, jump-in, DMT contact to the trigger (or the end of the range), retract
        with adhesion and pull-off; VERTICAL signal with the photodiode nonlinearity and noise; the load may blunt the tip."""
        rec = self.records[o['record_id']]
        meta = rec['meta']
        pr = self._probe()
        opt = o['extra']['o']
        tr = pr['truth']
        pos = meta['positions_m'][idx // meta['count']]
        s = self._sample()
        p0 = self._tip_in_sample() or (0.0, 0.0)
        mp = self.engaged['mp']
        k, R = tr['k'], pr['radius']
        _, mat = s['surface'].evaluate(np.array([p0[0] + pos[0]]), np.array([p0[1] + pos[1]]))
        m = s['surface'].materials[int(mat[0])]
        Es = reduced_modulus(tr['tip_material'], m)
        r = _rng(self._seed(), 601, int(o['record_id'][2:]), idx)
        F_ad = adhesion_force(R, m, self.plant['humidity']) * float(np.exp(0.08 * r.standard_normal()))
        C = hamaker_ts(m) * R / 6.0
        if mp['mode'] == 'contact':
            z_eng = -(mp['d_set'] + dmt_indentation(mp['force_n'], R, Es, adhesion_force(R, m, self.plant['humidity'])))
        else:
            z_eng = mp['A_set']
        if self.engaged.get('false'):
            z_eng += 5e-3
        z_start = z_eng + meta['z_range_m'] / 2
        zb = np.linspace(z_start, z_start - meta['z_range_m'], FD_POINTS)
        z_rel = zb                                                     # the feedback re-engages at every position before the ramp
        I, S, pv = opt['I'], opt['S'], opt['p_v']
        V_free = float(pspd_signal(I, pv, S, 0.0))
        dV_trig = meta['trigger_force_n'] * self.cal['sensitivity_v_per_m'] / self.cal['spring_constant_n_m']
        sig_d = math.sqrt(DEFLECTION_NOISE_M ** 2 + 4 * KB * self.plant['room_k'] / (k * 2 * math.pi * tr['f0'] * tr['Q']) * FD_POINTS * meta['rate_hz'])
        d_app, _, _ = fd_branches(z_rel, k, R, Es, F_ad, C)
        n_app = r.standard_normal(FD_POINTS) * sig_d
        V_app = pspd_signal(I, pv, S, d_app + n_app)
        hit = np.nonzero(V_app - V_free >= dV_trig)[0]
        it = int(hit[0]) if len(hit) else FD_POINTS - 1
        d_app, d_ret, info = fd_branches(z_rel[:it + 1], k, R, Es, F_ad, C)
        V_app = pspd_signal(I, pv, S, d_app + n_app[:it + 1])
        V_ret = pspd_signal(I, pv, S, d_ret + r.standard_normal(it + 1) * sig_d)
        F_max = k * float(np.max(d_app))
        pr['radius'] = min(RADIUS_MAX_M, blunted_radius(F_max, pr['radius'], Es))
        kc, Sc = self.cal['spring_constant_n_m'], self.cal['sensitivity_v_per_m']
        zr = zb[:it + 1] - z_start
        curve = dict(position_m=list(pos), z_approach_m=zr.tolist(), deflection_approach_v=np.asarray(V_app).tolist(), z_retract_m=zr[::-1].tolist(),
                     deflection_retract_v=np.asarray(V_ret).tolist(), free_deflection_v=V_free,
                     force_approach_n=((np.asarray(V_app) - V_free) * kc / Sc).tolist(), force_retract_n=((np.asarray(V_ret) - V_free) * kc / Sc).tolist(),
                     triggered=bool(len(hit)), _truth=dict(material=m, adhesion_n=F_ad, reduced_modulus_pa=Es, max_force_n=F_max, tip_radius_m=R))
        rec['data']['curves'].append(curve)
        meta['curves_completed'] = len(rec['data']['curves'])
        if np.max(np.abs(np.concatenate([V_app, V_ret]))) > SAT_LIMIT * I:
            self._warn(rec, 'pspd_saturated')
        if not len(hit):
            self._warn(rec, 'trigger_not_reached')
        self.led['force_curves'].add(1.0)
        self.led['scanner_path_m'].add(2.0 * abs(float(zr[-1])))

    def _complete_thermal(self, o):
        rec = self.records[o['record_id']]
        pr = self._probe()
        tr = pr['truth']
        opt = self._optics()
        meta = rec['meta']
        f0db = probe_nominal(self.cal['cantilever_db'])['f0']
        f = np.linspace(0.6 * f0db, 1.6 * f0db, FREQ_POINTS_TT)
        P = thermal_spectrum(f, opt['S'], tr['k'], tr['f0'], tr['Q'], self.plant['room_k'], meta['averages'], _rng(self._seed(), 701, int(o['record_id'][2:])))
        fit = fit_sho(f, P, meta['averages'])
        rec['data'] = dict(frequency_hz=f.tolist(), psd_v2_per_hz=P.tolist())
        if fit is None:
            self._warn(rec, 'no_resonance_in_window')
            rec['data']['fit'] = None
        else:
            S_cal = self.cal['sensitivity_v_per_m']
            k_cal = OPTICAL_LEVER_CHI * KB * meta['temperature_k'] * S_cal * S_cal / fit['area']
            rec['data']['fit'] = dict(resonance_hz=fit['f0'], quality_factor=fit['Q'], area_v2=fit['area'], white_v2_per_hz=fit['white'],
                                      spring_constant_n_m=k_cal, relative_uncertainty=fit['area_rel_se'])
            self.cal.update(spring_constant_n_m=k_cal, spring_constant_source='thermal_tune', resonance_hz=fit['f0'], quality_factor=fit['Q'], updated_s=self.t)
        self._finish(rec, 'completed')

    def _complete_sweep(self, o):
        rec = self.records[o['record_id']]
        pr = self._probe()
        tr = pr['truth']
        opt = self._optics()
        meta = rec['meta']
        f = np.linspace(meta['start_hz'], meta['stop_hz'], meta['points'])
        A_res = self.drive['A_res'] if self.drive else DEFAULT_DRIVE_RES_M
        r = _rng(self._seed(), 801, int(o['record_id'][2:]))
        A = A_res * lorentz_amp(f, tr['f0'], tr['Q']) * opt['S'] / self.cal['sensitivity_v_per_m']
        A = A + r.standard_normal(len(f)) * 0.02e-9
        ph = lorentz_phase(f, tr['f0'], tr['Q']) + r.standard_normal(len(f)) * PHASE_NOISE_RAD
        i = int(np.argmax(A))
        half = np.nonzero(A >= A[i] / math.sqrt(2))[0]
        width = float(f[half[-1]] - f[half[0]]) if len(half) > 1 else float(f[1] - f[0])
        rec['data'] = dict(frequency_hz=f.tolist(), amplitude_m=A.tolist(), phase_rad=ph.tolist(), peak_hz=float(f[i]), peak_amplitude_m=float(A[i]),
                           quality_factor_estimate=float(f[i] / max(width, float(f[1] - f[0]))))
        if i == 0 or i == len(f) - 1:
            self._warn(rec, 'peak_at_window_edge')
        self._finish(rec, 'completed')

    def _complete_approach(self, o):
        x = o['extra']
        self.qz, self.qz_target, self.qz_stop = x['q_end'], x['q_end'], None
        if x['outcome'] in ('engaged', 'false'):
            self.engaged = dict(mp=x['mp'], ext0=Z_SCAN_RANGE_M * APPROACH_TARGET_EXTENSION, drift_z0=self._drift()[2], t0=self.t, false=x['outcome'] == 'false')
            self._end_op('completed')
            self._event('approach', 'ENGAGED', '进针完成，反馈保持设定点', rid=o['op'])
            if x['outcome'] == 'false':
                self._event('fault', 'FALSE_ENGAGE', 'VERTICAL偏离中心太多，振幅信号被削顶，进针在远处提前触发（评测者可见）', rid=o['op'])
            elif x['mp'].get('regime') == 'contact_regime':
                self._event('fault', 'NC_CONTACT_REGIME', '驱动频率在共振峰低频一侧，非接触反馈点不存在，针尖进入排斥接触（评测者可见）', rid=o['op'])
            return
        pr = self._probe()
        if x['outcome'] == 'crash':
            pr['damage'] = 'tip_broken'
            pr['radius'] = max(pr['radius'], BROKEN_TIP_RADIUS_M)
            self.led['tip_crashes'].add(1.0)
            self._event('fault', 'TIP_CRASH', 'VERTICAL饱和、设定点未到达，针尖压坏（评测者可见）', rid=o['op'])
        self._end_op('failed', 'APPROACH_FAILED')
        self._event('approach', 'APPROACH_FAILED', ERROR_ZH['APPROACH_FAILED'], rid=o['op'])
        self.qz_target, self.qz_speed = min(Z_STAGE_LIMITS_M[1], self.qz + RETRACT_DISTANCE_M), Z_STAGE_MAX_SPEED_M_S

    # ---------------------------------------------------------------- records and analysis
    def _record(self, record_id):
        rec = self.records.get(record_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return rec

    def _image(self, record_id, channel, direction):
        rec = self._record(record_id)
        if rec['kind'] == 'processed':
            if channel != rec['meta']['channel'] or direction != rec['meta']['direction']:
                raise ActionRejected('CHANNEL_NOT_RECORDED')
            return rec, np.asarray(rec['data']['map'], float)
        if rec['kind'] != 'image' or rec['status'] == 'running':
            raise ActionRejected('RECORD_NOT_SUITABLE')
        if channel not in rec['data'] or direction not in rec['data'][channel]:
            raise ActionRejected('CHANNEL_NOT_RECORDED')
        img = np.asarray(rec['data'][channel][direction], float)
        n = rec['meta']['lines_completed']
        if n < 2:
            raise ActionRejected('RECORD_NOT_SUITABLE')
        return rec, img[:n]

    @staticmethod
    def _region(img, region):
        if region is None:
            return img
        x0, y0, x1, y1 = region
        if not (0 <= x0 < x1 <= img.shape[1] and 0 <= y0 < y1 <= img.shape[0]) or (x1 - x0) * (y1 - y0) < 4:
            raise ActionRejected('REGION_OUT_OF_RANGE')
        return img[y0:y1, x0:x1]

    def _cmd_analysis_flatten(self, record_id, channel, direction, method):
        rec, img = self._image(record_id, channel, direction)
        z = flatten_image(img, method)
        if self._record_bytes() + 4 * z.size > RECORD_MEMORY_BYTES:
            raise ActionRejected('RECORDS_FULL')                        # the processed map would pass the pixel memory cap of the records
        st = roughness_stats(z) if channel == 'height' else None           # statistics in m only for the height channel
        meta = dict(source_record_id=record_id, channel=channel, direction=direction, method=method, unit=CHANNEL_UNITS[channel],
                    pixel_size_m=rec['meta'].get('pixel_size_m'), pixels=[z.shape[1], z.shape[0]], stats=st)
        rid = self._new_record('PR', 'pr', 'processed', meta, dict(map=z.astype(np.float32)))
        pr = self.records[rid]
        pr['template_source'] = copy.deepcopy(rec['template_source'])
        self._finish(pr, 'completed')
        return dict(record_id=rid, source_record_id=record_id, stats=st)

    def _cmd_analysis_roughness(self, record_id, channel, direction, flatten, region_px):
        _, img = self._image(record_id, channel, direction)
        z = self._region(flatten_image(img, flatten), region_px)
        st = roughness_stats(z)
        if st is None:
            raise ActionRejected('REGION_OUT_OF_RANGE')
        return st

    def _cmd_analysis_line_profile(self, record_id, channel, direction, flatten, start_px, end_px, points):
        rec, img = self._image(record_id, channel, direction)
        for p in (start_px, end_px):
            if not (0 <= p[0] <= img.shape[1] - 1 and 0 <= p[1] <= img.shape[0] - 1):
                raise ActionRejected('REGION_OUT_OF_RANGE')
        z = flatten_image(img, flatten)
        vals = line_profile(z, start_px, end_px, points)
        px, py = rec['meta']['pixel_size_m']
        L = math.hypot((end_px[0] - start_px[0]) * px, (end_px[1] - start_px[1]) * py)
        return dict(distance_m=np.linspace(0.0, L, points).tolist(), values=vals.tolist(), unit=CHANNEL_UNITS[channel])

    def _cmd_analysis_step_height(self, record_id, channel, direction, flatten, method, region_a_px, region_b_px):
        _, img = self._image(record_id, channel, direction)
        z = flatten_image(img, flatten)
        if method == 'histogram':
            r = step_height_histogram(z)
            if r is None:
                raise ActionRejected('NO_STEP_FOUND')
            return dict(step_m=r['step_m'], lower_level_m=r['lower_level_m'], upper_level_m=r['upper_level_m'], method=method)
        if region_a_px is None or region_b_px is None:
            raise ActionRejected('REGION_REQUIRED')
        a = float(np.nanmean(self._region(z, region_a_px)))
        b = float(np.nanmean(self._region(z, region_b_px)))
        return dict(step_m=b - a, lower_level_m=min(a, b), upper_level_m=max(a, b), method=method)

    def _curve(self, record_id, curve_index):
        rec = self._record(record_id)
        if rec['kind'] != 'force_curves' or rec['status'] == 'running':
            raise ActionRejected('RECORD_NOT_SUITABLE')
        if curve_index >= len(rec['data']['curves']):
            raise ActionRejected('UNKNOWN_CURVE')
        return rec, rec['data']['curves'][curve_index]

    def _cmd_analysis_fd_fit(self, record_id, curve_index, branch, model, tip_radius_m, sample_poisson_ratio, tip_modulus_pa, tip_poisson_ratio):
        rec, c = self._curve(record_id, curve_index)
        k = rec['meta']['calibration']['spring_constant_n_m']
        z, F = (c['z_retract_m'], c['force_retract_n']) if branch == 'retract' else (c['z_approach_m'], c['force_approach_n'])
        r = fit_fd(z, F, k, tip_radius_m, sample_poisson_ratio, tip_modulus_pa, tip_poisson_ratio, model, branch == 'retract')
        if r is None:
            raise ActionRejected('FIT_FAILED')
        return r

    def _cmd_calibration_sensitivity(self, record_id, curve_index, branch):
        self._need_plant()
        self._need_idle()
        rec, c = self._curve(record_id, curve_index)
        z = np.asarray(c['z_approach_m'] if branch == 'approach' else c['z_retract_m'])
        V = np.asarray(c['deflection_approach_v'] if branch == 'approach' else c['deflection_retract_v'])
        i_min = int(np.argmin(V))
        part = np.arange(i_min, len(V)) if branch == 'approach' else np.arange(0, i_min + 1)
        Vp, zp = V[part], z[part]
        lo, hi = c['free_deflection_v'], float(Vp.max())
        sel = (Vp >= lo + 0.2 * (hi - lo)) & (Vp <= lo + 0.8 * (hi - lo))
        if hi - lo <= 0 or sel.sum() < 6:
            raise ActionRejected('FIT_FAILED')
        A = np.vstack([np.ones(int(sel.sum())), zp[sel]]).T
        (c0, c1), *_ = np.linalg.lstsq(A, Vp[sel], rcond=None)
        pred = c0 + c1 * zp[sel]
        ss = float(np.sum((Vp[sel] - Vp[sel].mean()) ** 2))
        r2 = 1.0 - float(np.sum((Vp[sel] - pred) ** 2)) / ss if ss > 0 else 0.0
        if not c1 < 0:
            raise ActionRejected('FIT_FAILED')
        prev = self.cal['sensitivity_v_per_m']
        self.cal.update(sensitivity_v_per_m=float(-c1), sensitivity_source='force_curve', updated_s=self.t)
        return dict(sensitivity_v_per_m=float(-c1), previous_v_per_m=prev, points=int(sel.sum()), r2=r2)

    def _cmd_calibration_set(self, spring_constant_n_m, sensitivity_v_per_m):
        self._need_plant()
        self._need_idle()
        if self.cal['cantilever_db'] is None:
            raise ActionRejected('MODE_NOT_CONFIGURED')
        if spring_constant_n_m is not None:
            self.cal.update(spring_constant_n_m=spring_constant_n_m, spring_constant_source='manual', updated_s=self.t)
        if sensitivity_v_per_m is not None:
            self.cal.update(sensitivity_v_per_m=sensitivity_v_per_m, sensitivity_source='manual', updated_s=self.t)
        return dict(calibration=dict(self.cal))

    def _out(self, rec):
        return {k: copy.deepcopy(v) for k, v in rec.items() if k in ('record_id', 'kind', 'status', 'started_s', 'finished_s', 'meta', 'warnings', 'template_source')}

    def _cmd_measurement_result(self, record_id, channel, direction, region_px, max_values):
        rec = self._record(record_id)
        head = self._out(rec)
        head['meta'] = json.loads(json.dumps(head['meta'], default=float))
        kind = rec['kind']
        if kind in ('image', 'processed'):
            ch = channel or ('height' if kind == 'image' else rec['meta']['channel'])
            d = direction or ('trace' if kind == 'image' else rec['meta']['direction'])
            if kind == 'image':
                if ch not in rec['data'] or d not in rec['data'][ch]:
                    raise ActionRejected('CHANNEL_NOT_RECORDED')
                img = np.asarray(rec['data'][ch][d], float)[:max(rec['meta']['lines_completed'], 0)]
            else:
                if ch != rec['meta']['channel'] or d != rec['meta']['direction']:
                    raise ActionRejected('CHANNEL_NOT_RECORDED')
                img = np.asarray(rec['data']['map'], float)
            if img.shape[0] == 0:
                return dict(record=head, data=dict(channel=ch, direction=d, unit=CHANNEL_UNITS[ch], shape=[0, img.shape[1]], values=[], factor=1, pixel_size_m=None,
                                                   stats=None))
            img = self._region(img, region_px)
            small, f = downsample(img, max_values, None)
            ps = rec['meta'].get('pixel_size_m') or [None, None]
            finite = small[np.isfinite(small)]
            stats = dict(min=float(finite.min()), max=float(finite.max()), mean=float(finite.mean()), rms=float(finite.std())) if finite.size else None
            vals = [[None if not math.isfinite(x) else float(x) for x in row] for row in small]
            return dict(record=head, data=dict(channel=ch, direction=d, unit=CHANNEL_UNITS[ch], shape=list(small.shape), factor=f, values=vals,
                                               pixel_size_m=[ps[0] * f, ps[1] * f] if ps[0] else None, stats=stats))
        if kind == 'force_curves':
            curves = [{k: v for k, v in c.items() if k != '_truth'} for c in rec['data']['curves']]
            total = sum(len(c['z_approach_m']) * 3 + len(c['z_retract_m']) * 3 for c in curves) or 1
            f = max(1, math.ceil(total / max_values))
            if f > 1:
                for c in curves:
                    for key in ('z_approach_m', 'deflection_approach_v', 'force_approach_n', 'z_retract_m', 'deflection_retract_v', 'force_retract_n'):
                        c[key] = c[key][::f]
            return dict(record=head, data=dict(curves=curves, factor=f, units=dict(z='m', deflection='V', force='N')))
        data = copy.deepcopy(rec['data'])
        for key in ('frequency_hz', 'psd_v2_per_hz', 'amplitude_m', 'phase_rad'):
            if key in data:
                data[key] = data[key][::max(1, math.ceil(len(data[key]) * 3 / max_values))]
        return dict(record=head, data=data)

    def _cmd_records_list(self):
        return dict(records=sorted(self.records))

    def _cmd_records_delete(self, record_id):
        rec = self._record(record_id)
        if rec['status'] == 'running':
            raise ActionRejected('RECORD_IN_USE')
        del self.records[record_id]
        return dict(record_id=record_id, remaining=len(self.records))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def ledger(self):
        return {k: v.value() for k, v in self.led.items()}

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        return dict(libraries=library_sha256(), substances=len(nanomech()['subs']), probe_types=len(probes()['types']), sample_templates=len(sample_templates()['templates']))

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)

    # ---------------------------------------------------------------- time
    def step(self, dt_s):
        if not isinstance(dt_s, (int, float)) or isinstance(dt_s, bool) or not math.isfinite(dt_s) or dt_s < 0 or dt_s > MAX_STEP_S:
            raise ActionRejected('INVALID_ARGUMENT')
        if dt_s == 0:
            return
        t_end = self.t + dt_s
        guard = 0
        while self.t < t_end - 1e-12:
            h = min(t_end - self.t, self._next_event_dt())
            self._advance(max(h, 0.0))
            self._handle_events()
            guard += 1
            if guard > 2_000_000:
                raise RuntimeError('step did not converge')
        self.t = t_end if abs(self.t - t_end) < 1e-9 else self.t
        self._handle_events()
        self.revision += 1

    def _next_event_dt(self):
        c = [math.inf]
        t = self.t
        if self.power and self.state == 'booting':
            c.append(self.state_t0 + BOOT_S - t)
        if self.head_move:
            c.append(self.head_move['until'] - t)
        if self.exchange:
            c.append(self.exchange['until'] - t)
        if self._xy_moving():
            c.append(math.hypot(self.xy_target[0] - self.xy[0], self.xy_target[1] - self.xy[1]) / XY_STAGE_SPEED_M_S)
        if self._z_moving():
            c.append(abs(self.qz_target - self.qz) / self.qz_speed)
        if self._f_moving():
            c.append(abs(self.qf_target - self.qf) / FOCUS_SPEED_M_S)
        o = self.op
        if o is not None:
            c.append(o['t0'] + o['duration_s'] - t)
            if o['kind'] == 'scan':
                job = o['extra']['job']
                c.append(job['t0'] + min(job['pixels_y'], (o['extra']['blocks_done'] + 1) * BLOCK_LINES) / job['rate_hz'] - t)
            elif o['kind'] == 'force_curves':
                c.append(o['t0'] + (o['extra']['done'] + 1) * o['extra']['per'] - t)
        return max(0.0, min(c))

    def _advance(self, h):
        if h <= 0:
            return
        if self.power:
            self.led['electrical_energy_j'].add(ELECTRONICS_POWER_W * h)
        if self._beam_emitting():
            self.led['beam_on_s'].add(h)
        if self._xy_moving():
            dx, dy = self.xy_target[0] - self.xy[0], self.xy_target[1] - self.xy[1]
            d = math.hypot(dx, dy)
            step = min(d, XY_STAGE_SPEED_M_S * h)
            if step >= d - 1e-15:
                self.xy = list(self.xy_target)
            else:
                self.xy = [self.xy[0] + dx / d * step, self.xy[1] + dy / d * step]
            self.led['xy_stage_travel_m'].add(step)
        if self._z_moving():
            d = self.qz_target - self.qz
            step = min(abs(d), self.qz_speed * h)
            self.qz = self.qz_target if step >= abs(d) - 1e-16 else self.qz + math.copysign(step, d)
            self.led['z_stage_travel_m'].add(step)
        if self._f_moving():
            d = self.qf_target - self.qf
            step = min(abs(d), FOCUS_SPEED_M_S * h)
            self.qf = self.qf_target if step >= abs(d) - 1e-16 else self.qf + math.copysign(step, d)
            self.led['focus_travel_m'].add(step)
        self.t += h

    def _handle_events(self):
        t = self.t
        eps = 1e-9
        if self.power and self.state == 'booting' and t >= self.state_t0 + BOOT_S - eps:
            self.state = 'ready'
            self._event('power', 'READY', 'SmartScan就绪')
            if 'sld_failure' in self.faults:
                self._raise_error('SLD_FAILURE')
        if self.head_move and t >= self.head_move['until'] - eps:
            self.head_state = self.head_move['to']
            self.head_move = None
            self.led['head_swaps'].add(1.0)
            if self.head_state == 'attached':
                self.disturb_t = t
            self._event('head', 'HEAD_' + self.head_state.upper(), '测头已装上' if self.head_state == 'attached' else '测头已取下')
        if self.exchange and t >= self.exchange['until'] - eps:
            x = self.exchange
            p = self.probes[x['probe_id']]
            if x['action'] == 'mount':
                p['location'], p['mount'] = 'mounted', self._mount_offsets(x['probe_id'])
                self.mounted = x['probe_id']
            else:
                p['location'], p['mount'] = 'box', None
            self.exchange = None
            self.led['probe_exchanges'].add(1.0)
            self._event('probe', 'PROBE_' + x['action'].upper() + 'ED', f'探针{x["probe_id"]}{"已装上" if x["action"] == "mount" else "已放回探针盒"}')
        if self.qz_stop is not None and abs(self.qz - self.qz_stop['q']) < 1e-13:
            st = self.qz_stop
            pr = self._probe()
            if st['kind'] == 'touch':
                pr['radius'] = min(RADIUS_MAX_M, pr['radius'] + TOUCH_WEAR_M)
                self._event('stage', 'TOUCH_STOP', 'Z台下降中针尖触到样品，检测到偏转后停止')
            else:
                pr['damage'] = 'cantilever_broken'
                self.led['tip_crashes'].add(1.0)
                self._event('stage', 'Z_STAGE_STOPPED', 'Z台停止（偏转信号异常）')
                self._event('fault', 'TIP_CRASH', 'PSPD饱和，触碰未被检测，悬臂撞断（评测者可见）')
            self.qz_target, self.qz_stop = self.qz, None
        o = self.op
        if o is None:
            return
        if o['kind'] == 'scan':
            job = o['extra']['job']
            while True:
                b = o['extra']['blocks_done']
                j0 = b * BLOCK_LINES
                if j0 >= job['pixels_y']:
                    break
                j1 = min(job['pixels_y'], j0 + BLOCK_LINES)
                if t < job['t0'] + j1 / job['rate_hz'] - eps:
                    break
                self._run_block(o, j0, j1)
                o['extra']['blocks_done'] = b + 1
            if o['extra']['blocks_done'] * BLOCK_LINES >= job['pixels_y']:
                rec = self.records[o['record_id']]
                self._finish(rec, 'completed')
                self._end_op('completed')
                self._event('scan', 'SCAN_DONE', f'扫描{o["record_id"]}完成', rid=o['record_id'])
                if o['extra']['retract_after']:
                    self._lift_scanner()
                    self.qz_target, self.qz_speed = min(Z_STAGE_LIMITS_M[1], self.qz + RETRACT_DISTANCE_M), Z_STAGE_MAX_SPEED_M_S
        elif o['kind'] == 'force_curves':
            while o['extra']['done'] < o['extra']['n'] and t >= o['t0'] + (o['extra']['done'] + 1) * o['extra']['per'] - eps:
                self._fd_curve(o, o['extra']['done'])
                o['extra']['done'] += 1
            if o['extra']['done'] >= o['extra']['n']:
                self._finish(self.records[o['record_id']], 'completed')
                self._end_op('completed')
                self._event('force_curve', 'FD_DONE', f'力曲线{o["record_id"]}完成', rid=o['record_id'])
        elif t >= o['t0'] + o['duration_s'] - eps:
            if o['kind'] == 'approach':
                self._complete_approach(o)
            elif o['kind'] == 'thermal_tune':
                self._complete_thermal(o)
                self._end_op('completed')
                self._event('calibration', 'THERMAL_TUNE_DONE', f'热调谐{o["record_id"]}完成', rid=o['record_id'])
            elif o['kind'] == 'frequency_sweep':
                self._complete_sweep(o)
                self._end_op('completed')
                self._event('calibration', 'SWEEP_DONE', f'扫频{o["record_id"]}完成', rid=o['record_id'])


# ---------------------------------------------------------------- sessions (identity, visibility filter, idempotent requests)
_RECORD_ARG = {'measurement.result': 'record_id', 'records.delete': 'record_id', 'analysis.flatten': 'record_id', 'analysis.roughness': 'record_id',
               'analysis.line_profile': 'record_id', 'analysis.step_height': 'record_id', 'analysis.fd_fit': 'record_id', 'calibration.sensitivity': 'record_id'}
_DATA_GUARDED = set(_RECORD_ARG)
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED}
def _response_bytes(result):
    try:
        return len(json.dumps(result, default=float))
    except (TypeError, ValueError):
        return 0


_REPLAYABLE_QUERIES = {'measurement.result', 'records.list', 'events.read', 'camera.read', 'ledger.read', 'provenance.read', 'analysis.roughness',
                       'analysis.line_profile', 'analysis.step_height', 'analysis.fd_fit'}
_TRUTH_WARNINGS = {'tip_crash', 'tip_worn', 'nc_contact_regime', 'false_engage', 'tip_footprint_truncated'}      # evaluator knowledge: the instrument cannot tell


class ParkNx10Session:
    def __init__(self, host, principal_id):
        self._host = host
        self._principal = principal_id

    @property
    def _role(self):
        return self._host._principals[self._principal]

    def _target(self, instance_id):
        if not isinstance(instance_id, str) or instance_id != self._host.instance_id:
            raise ActionRejected('UNKNOWN_INSTANCE')

    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:
            self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID:
            raise ActionRejected('UNKNOWN_INSTANCE')
        meta = copy.deepcopy(export_park_nx10_contract_metadata())
        caps = {k: v for k, v in meta['capabilities'].items() if v['visibility'] == 'agent' or self._role != 'agent'}
        slots = {k: v for k, v in meta['state_slots'].items() if v['visibility'] == 'agent' or self._role != 'agent'}
        if self._role == 'agent':
            for c in caps.values():
                c['state_effects'] = [e for e in c['state_effects'] if e['state_id'] in slots]
                c['observes'] = [x for x in c['observes'] if x in slots]
                for pre in c['preconditions']:
                    pre['state_ids'] = [x for x in pre['state_ids'] if x in slots]
                c['execution']['completion_state_ids'] = [x for x in c['execution']['completion_state_ids'] if x in slots]
            for m in meta['mechanisms'].values():
                m['state_ids'] = [x for x in m['state_ids'] if x in slots]
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version='1.0.0', capabilities=caps, state_slots=slots)

    def _foreign(self, object_id):
        owner = self._host._owners.get(object_id)
        return owner is not None and owner != self._principal and self._host._principals.get(owner) == 'agent'

    def _foreign_op(self):
        h = self._host
        return h.op is not None and self._foreign(h.op['op'])

    def _redact(self, rec):
        if self._role == 'agent':
            if 'template_source' in rec:
                rec['template_source'] = dict(library=rec['template_source']['library'], template_id=None, substances=[])
            if 'warnings' in rec:
                rec['warnings'] = [w for w in rec['warnings'] if w not in _TRUTH_WARNINGS]
        return rec

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        try:
            values = h.read(self._role)
        except ActionRejected:
            raise
        except Exception as exc:                                         # noqa: BLE001 - a defect is reported as DEVICE_ERROR, never as a raw exception
            h._event('fault', 'DEVICE_ERROR', f'read: {type(exc).__name__}: {exc}'[:300])
            raise ActionRejected('DEVICE_ERROR') from exc
        foreign = self._role == 'agent' and self._foreign_op()
        values['operation.started_by_other_agent'] = foreign
        if foreign:
            values['operation.id'] = None
            values['scan.lines_completed'] = None
        if self._role == 'agent':
            values['result.last_record_id'] = h._last_record(lambda k: not self._foreign(k))
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None) for k in ids})

    def _guard(self, capability_id, arguments):
        if self._role != 'agent':
            return
        h = self._host
        if capability_id in _CONTROL_GUARDED and self._foreign_op():
            if capability_id == 'power.set' and not h.power:
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED:
            rid = arguments.get(_RECORD_ARG[capability_id])
            if self._foreign(rid):
                raise ActionRejected('ACCESS_DENIED')
            owner = h._owners.get(rid)
            if capability_id == 'records.delete' and owner is not None and owner != self._principal:
                raise ActionRejected('ACCESS_DENIED')                  # an agent deletes only its own records and those of the host

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        h = self._host
        key = digest = None
        try:
            self._target(instance_id)
            if not isinstance(request_id, str) or not request_id:
                raise ActionRejected('INVALID_ARGUMENT')
            key = (self._principal, request_id)
            try:
                digest = json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            except (TypeError, ValueError):
                digest = None
            if key in h._requests:
                old, response = h._requests[key]
                if digest is None or old != digest:
                    raise ActionRejected('REQUEST_CONFLICT')
                if response is not None:
                    return copy.deepcopy(response)
                # an evicted large read-only query result: the same query runs again under its request id (other uses stay conflicts)
            if not isinstance(capability_id, str) or capability_id not in CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and CAPS[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if capability_id in UNSUPPORTED:
                raise ActionRejected('UNSUPPORTED')                  # whatever the arguments
            if not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            validate(CAPS[capability_id]['input_schema'], arguments)
            self._guard(capability_id, arguments)
            try:
                result = h.invoke(capability_id, **arguments)
            except ActionRejected:
                raise
            except Exception as exc:                                     # noqa: BLE001 - a defect is reported as DEVICE_ERROR, never as a raw exception
                h._event('fault', 'DEVICE_ERROR', f'{capability_id}: {type(exc).__name__}: {exc}'[:300])
                raise ActionRejected('DEVICE_ERROR') from exc
            if self._role == 'agent':
                if capability_id == 'records.list':
                    result = dict(records=[r for r in result['records'] if not self._foreign(r)])
                elif capability_id == 'events.read':
                    result = dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in h.events
                                          if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_rid')) and e['kind'] != 'fault'], dropped=h.events_dropped)
                elif capability_id == 'measurement.result':
                    result = dict(result, record=self._redact(result['record']))
            operation = result.get('operation_id') if capability_id in ASYNC else None
            if operation:
                h._owners[operation] = self._principal
                rid = h.operations[operation].get('record_id')
                if rid:
                    h._owners[rid] = self._principal
            if capability_id == 'analysis.flatten':
                h._owners[result['record_id']] = self._principal
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=operation,
                            status='accepted' if operation else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                            result=copy.deepcopy(result), observations={}, error=None)
            h._requests[key] = (digest, copy.deepcopy(response))
            if capability_id in _REPLAYABLE_QUERIES and _response_bytes(result) > LARGE_RESPONSE_BYTES:
                big = h._large_requests.setdefault(self._principal, [])      # a large query result is kept for the last 16 request ids of the principal;
                big.append(key)                                               # an older one re-runs the (read-only) query when its request id comes again
                while len(big) > LARGE_RESPONSE_KEEP:
                    old_key = big.pop(0)
                    if old_key in h._requests and old_key != key:
                        h._requests[old_key] = (h._requests[old_key][0], None)     # keep the request digest, drop the large result
            return response
        except ActionRejected as exc:
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None,
                            status='unsupported' if str(exc) == 'UNSUPPORTED' else 'rejected', state_revision=h.revision,
                            clock={'domain': 'simulation', 'time_s': h.time_s}, result={}, observations={},
                            error=dict(code=str(exc), message=CAP_ERROR_ZH.get((capability_id, str(exc)), ERROR_ZH.get(str(exc), str(exc))), retryable=False,
                                       side_effects='unknown' if str(exc) == 'DEVICE_ERROR' else 'none'))
            if key is not None and digest is not None and str(exc) != 'REQUEST_CONFLICT' and (
                    key not in h._requests or (h._requests[key][0] == digest and h._requests[key][1] is None)):
                h._requests[key] = (digest, copy.deepcopy(response))            # a conflict never rebinds the request id
            return response

    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id)
        raise ActionRejected('ACCESS_DENIED')                       # set points are written through capabilities

    def poll(self, operation_id):
        h = self._host
        if not isinstance(operation_id, str):
            raise ActionRejected('INVALID_ARGUMENT')
        op = h.operations.get(operation_id)
        if op is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        if h._owners.get(operation_id) != self._principal and self._role != 'evaluator':
            raise ActionRejected('ACCESS_DENIED')                       # evaluators observe every operation
        if op.get('record_id') in h.records:
            res = self._redact(h._out(h.records[op['record_id']]))
            res['meta'] = json.loads(json.dumps(res['meta'], default=float))
        else:
            res = {k: v for k, v in op.items() if k in ('phase', 'reason', 'kind')}
        return dict(operation_id=operation_id, status=op['phase'], result=res)

    def _rejected(self, request_id, operation_id, code):
        h = self._host
        return dict(protocol_version=PROTOCOL_VERSION, instance_id=h.instance_id, request_id=request_id, operation_id=operation_id, status='rejected',
                    state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result={}, observations={},
                    error=dict(code=code, message=ERROR_ZH.get(code, code), retryable=False, side_effects='none'))

    def cancel(self, operation_id, request_id):
        """Command interface like invoke: a refused cancel answers with the rejected response envelope (queries - describe, read, poll - raise)."""
        h = self._host
        if not isinstance(request_id, str) or not request_id:
            return self._rejected(request_id, operation_id, 'INVALID_ARGUMENT')
        key = (self._principal, request_id)
        digest = json.dumps(['cancel', operation_id]) if isinstance(operation_id, str) else None
        if key in h._requests:
            old, response = h._requests[key]
            if digest is None or old != digest:
                return self._rejected(request_id, operation_id, 'REQUEST_CONFLICT')
            return copy.deepcopy(response)
        try:
            self.poll(operation_id)
            op = h.operations[operation_id]
            if op['phase'] != 'running':
                raise ActionRejected('NOT_RUNNING')
            h._cancel_op(op)
        except ActionRejected as exc:
            response = self._rejected(request_id, operation_id, str(exc))
            if digest is not None:
                h._requests[key] = (digest, copy.deepcopy(response))
            return response
        h.revision += 1
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=h.instance_id, request_id=request_id, operation_id=operation_id, status='succeeded',
                        state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=dict(phase=op['phase']), observations={}, error=None)
        h._requests[key] = (digest, copy.deepcopy(response))
        return response


# ---------------------------------------------------------------- contract metadata
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'DEVICE_ERROR': '仿真内部错误（调用或操作以失败结束，状态可能已部分改变；详情在评测者可见的fault事件中）',
    'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在', 'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力',
    'ACCESS_DENIED': '无权访问（评测槽、另一个agent的记录或操作）', 'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作不在进行中',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'POWER_ON': '通电或有操作时不能重新声明实验室条件', 'POWER_OFF': '控制电子单元未通电（或操作中断电）',
    'NOT_READY': 'SmartScan初始化中', 'ERROR_PENDING': '有未确认的错误（先排除原因再system.acknowledge）', 'CAUSE_PERSISTS': '错误原因仍存在，不能确认',
    'MEASUREMENT_RUNNING': '有进针、扫描、力曲线、热调谐或扫频在进行', 'ENGAGED': '针尖已进针（先retract）', 'NOT_ENGAGED': '未进针（先approach）',
    'MOTION_IN_PROGRESS': '电动台在移动、测头在装卸或正在换探针', 'HEAD_NOT_ATTACHED': '测头不在燕尾导轨上', 'HEAD_ATTACHED': '测头装在燕尾导轨上（换探针要先取下测头）',
    'NO_PROBE': '探针夹上没有探针', 'UNKNOWN_PROBE': '探针不存在', 'PROBE_MOUNTED': '探针夹上已有探针', 'PROBE_NOT_IN_BOX': '该探针不在探针盒里',
    'DUPLICATE_PROBE': '探针ID已存在', 'TOO_MANY_PROBES': '探针数量超过上限（24）', 'UNKNOWN_PROBE_TYPE': '探针库中没有该型号',
    'MODE_NOT_CONFIGURED': '尚未用head.configure选择测头模式与悬臂数据库条目', 'FOCUS_TOO_LOW': '聚焦台低于参考位2 mm以上，物镜离测头太近（先升起聚焦台）', 'MODE_NOT_DYNAMIC': '驱动与扫频只用于轻敲或True Non-Contact模式',
    'BEAM_OFF': 'SLD不发光（光束开关关、测头未装上或SLD故障）', 'INTENSITY_LOW': '激光强度低于联锁阈值0.5 V（光斑不在悬臂上、悬臂损坏或没有探针；手册：Laser intensity is too low）',
    'KNOB_LIMIT': '旋钮到了行程极限', 'DRIVE_OFF_RESONANCE': '驱动频率离共振太远，双压电片达不到该振幅', 'DRIVE_NOT_SET': '动态模式需先ncm.set_drive',
    'SETPOINT_REQUIRED': '接触模式需要setpoint_force_n、动态模式需要setpoint_amplitude_m（另一个为null）', 'SETPOINT_OUT_OF_RANGE': '振幅设定点须小于自由振幅',
    'NO_SAMPLE_UNDER_TIP': '针尖下没有样品', 'TIP_SPEED_TOO_HIGH': '针尖速度2×尺寸×行频超过200 µm/s', 'SCAN_OUT_OF_RANGE': '扫描区（含偏移与旋转）超出XY扫描器50×50 µm',
    'SIMULATION_LIMIT': '超出仿真上限（仿真边界，不是仪器故障；具体原因见各能力的错误说明）', 'TOO_MANY_CURVES': '一次力曲线采集超过64条',
    'DUPLICATE_SAMPLE': '样品ID已存在', 'TOO_MANY_SAMPLES': '样品数量超过上限（12）', 'OVERRIDE_NOT_APPLICABLE': '覆盖的形貌参数不属于该模板',
    'SAMPLE_TOO_LARGE': '样品超过100×100 mm或厚20 mm', 'UNKNOWN_MATERIAL': '纳米力学库中没有该材料', 'UNKNOWN_SAMPLE': '样品不存在',
    'SAMPLE_NOT_ON_BENCH': '样品不在实验台上', 'CHUCK_OCCUPIED': '吸盘上已有样品', 'SAMPLE_NOT_ON_CHUCK': '该样品不在吸盘上',
    'SAMPLE_OUT_OF_AREA': '样品超出100×100 mm开放区（或小样品偏离磁性样品座）', 'COLLISION_RISK': '针尖离样品太近（放取样品须2 mm、装卸测头1 mm、移样品台跨过样品边缘100 µm或针尖贴在表面）',
    'RECORDS_FULL': '记录数已达上限（200）：删除记录后再试', 'TIP_IN_CONTACT': '针尖压在表面上（触碰停止后悬臂有偏转）：先抬起Z台', 'UNKNOWN_RECORD': '记录不存在', 'RECORD_IN_USE': '记录属于进行中的操作', 'RECORD_NOT_SUITABLE': '记录种类或状态不适用',
    'CHANNEL_NOT_RECORDED': '该记录没有这个通道或方向', 'REGION_OUT_OF_RANGE': '区域或端点超出图像', 'REGION_REQUIRED': 'regions方法需要region_a_px与region_b_px',
    'NO_STEP_FOUND': '高度直方图没有两个峰（不是两级表面）', 'UNKNOWN_CURVE': '力曲线序号不存在', 'FIT_FAILED': '拟合点不足或接触段不单调',
    'SLD_FAILURE': 'SLD失效（锁存；清除故障后确认）', 'Z_STAGE_FAULT': 'Z电动台故障（卡滞）', 'XY_STAGE_FAULT': 'XY电动台故障（卡滞）',
    'APPROACH_FAILED': '自动进针失败：设定点在Z台行程内没有达到（VERTICAL饱和或超出行程），Z台已抬起100 µm', 'CANCELLED': '操作被取消',
}
_S = ['operation.id', 'operation.kind', 'operation.progress', 'operation.remaining_s']
_PS = ['pspd.intensity_v', 'pspd.vertical_v', 'pspd.lateral_v']
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.drift_m'],
    'sample.provide': ['sample.bench_ids', 'truth.samples'],
    'probe.provide': ['probe.box_ids', 'truth.probes'],
    'sample.place': ['sample.bench_ids', 'sample.on_chuck', 'truth.drift_m', 'truth.gap_m', 'truth.samples'],
    'sample.remove': ['sample.bench_ids', 'sample.on_chuck', 'truth.gap_m', 'truth.samples'],
    'power.set': ['beam.emitting', 'engage.state', 'focus.moving', 'focus.target_m', 'ncm.amplitude_m', 'power.enabled', 'scan.lines_completed', 'stage.xy_moving',
                  'stage.xy_target_m', 'system.state', 'truth.drift_m', 'truth.gap_m', 'truth.probe', 'truth.probes', 'truth.tip_force_n', 'z_stage.moving', 'z_stage.target_m',
                  'zscanner.extension_m'] + _S + _PS,
    'power.press_button': ['beam.emitting', 'engage.state', 'focus.moving', 'focus.target_m', 'ncm.amplitude_m', 'power.enabled', 'scan.lines_completed',
                           'stage.xy_moving', 'stage.xy_target_m', 'system.state', 'truth.drift_m', 'truth.gap_m', 'truth.probe', 'truth.probes', 'truth.tip_force_n',
                           'z_stage.moving', 'z_stage.target_m', 'zscanner.extension_m'] + _S + _PS,
    'beam.set': ['beam.emitting', 'beam.switch_on', 'engage.state', 'ncm.amplitude_m', 'truth.gap_m', 'truth.tip_force_n', 'zscanner.extension_m'] + _PS,
    'beam.press_switch': ['beam.emitting', 'beam.switch_on', 'engage.state', 'ncm.amplitude_m', 'truth.gap_m', 'truth.tip_force_n', 'zscanner.extension_m'] + _PS,
    'head.remove': ['beam.emitting', 'head.state', 'ncm.amplitude_m', 'system.error_code', 'truth.faults'] + _PS,
    'head.attach': ['head.state', 'system.error_code', 'truth.faults'],
    'probe.mount': ['probe.box_ids', 'probe.exchanging'],
    'probe.unmount': ['ncm.amplitude_m', 'ncm.drive', 'probe.exchanging', 'probe.mounted', 'truth.probe'] + _PS,
    'head.configure': ['calibration.table', 'head.mode', 'ncm.amplitude_m', 'ncm.drive'],
    'head.turn_knob': ['knob.beam_x_turns', 'knob.beam_y_turns', 'knob.optics_x_turns', 'knob.optics_y_turns', 'knob.pspd_x_turns', 'knob.pspd_y_turns'] + ['ncm.amplitude_m'] + _PS,
    'head.set_knob': ['knob.beam_x_turns', 'knob.beam_y_turns', 'knob.optics_x_turns', 'knob.optics_y_turns', 'knob.pspd_x_turns', 'knob.pspd_y_turns'] + ['ncm.amplitude_m'] + _PS,
    'stage.move_xy': ['stage.xy_moving', 'stage.xy_target_m', 'system.error_code'],
    'z_stage.move': ['system.error_code', 'z_stage.moving', 'z_stage.target_m'],
    'focus.move': ['focus.moving', 'focus.target_m'],
    'ncm.frequency_sweep': list(_S),
    'ncm.set_drive': ['ncm.amplitude_m', 'ncm.drive'],
    'approach': ['engage.state', 'feedback.setpoint', 'truth.gap_m', 'z_stage.moving', 'z_stage.target_m', 'zscanner.extension_m'] + _S,
    'retract': ['engage.state', 'ncm.amplitude_m', 'system.error_code', 'truth.gap_m', 'truth.tip_force_n', 'z_stage.moving', 'z_stage.target_m', 'zscanner.extension_m']
               + ['pspd.vertical_v'],
    'scan.start': ['feedback.setpoint', 'ncm.amplitude_m', 'pspd.vertical_v', 'scan.lines_completed', 'truth.tip_force_n'] + _S,
    'fd.acquire': list(_S),
    'calibration.thermal_tune': list(_S),
    'calibration.sensitivity': ['calibration.table', 'ncm.amplitude_m'],
    'calibration.set': ['calibration.table', 'ncm.amplitude_m'],
    'analysis.flatten': ['result.last_record_id'],
    'analysis.roughness': [], 'analysis.line_profile': [], 'analysis.step_height': [], 'analysis.fd_fit': [],
    'records.delete': ['result.last_record_id'],
    'system.acknowledge': ['system.error_code'],
    'fault.inject': ['beam.emitting', 'engage.state', 'ncm.amplitude_m', 'scan.lines_completed', 'system.error_code', 'truth.faults', 'truth.gap_m', 'truth.probe',
                     'truth.probes', 'truth.tip_force_n', 'z_stage.moving', 'z_stage.target_m', 'stage.xy_moving', 'stage.xy_target_m', 'zscanner.extension_m', 'pspd.vertical_v']
                    + _S + _PS,
    'sim.step': ['clock.time_s'],
}
_OPS = _S + ['result.last_record_id', 'scan.lines_completed']
_MOTION = ['focus.moving', 'focus.position_m', 'stage.x_m', 'stage.xy_m', 'stage.xy_moving', 'stage.y_m', 'z_stage.moving', 'z_stage.position_m']
_ENG = ['engage.state', 'truth.gap_m', 'truth.probe', 'truth.probes', 'truth.tip_force_n', 'zscanner.extension_m']
ON_STEP = {
    'plant.configure': ['truth.drift_m'],
    'power.set': ['beam.emitting', 'calibration.table', 'ncm.amplitude_m', 'system.state', 'truth.drift_m'] + _PS + _MOTION + _ENG + _OPS,
    'power.press_button': ['beam.emitting', 'calibration.table', 'ncm.amplitude_m', 'system.state', 'truth.drift_m'] + _PS + _MOTION + _ENG + _OPS,
    'beam.set': ['beam.emitting', 'zscanner.extension_m'], 'beam.press_switch': ['beam.emitting', 'zscanner.extension_m'],
    'head.remove': ['beam.emitting', 'head.state', 'ncm.amplitude_m'] + _PS,
    'head.attach': ['beam.emitting', 'head.state', 'ncm.amplitude_m', 'truth.drift_m'] + _PS,
    'probe.mount': ['ncm.amplitude_m', 'probe.exchanging', 'probe.mounted', 'truth.probe'] + _PS,
    'probe.unmount': ['probe.box_ids', 'probe.exchanging'],
    'stage.move_xy': ['stage.x_m', 'stage.xy_m', 'stage.xy_moving', 'stage.y_m', 'truth.gap_m'],
    'z_stage.move': ['truth.gap_m', 'truth.probe', 'truth.probes', 'truth.tip_force_n', 'z_stage.moving', 'z_stage.position_m', 'z_stage.target_m'] + _PS
                    + ['ncm.amplitude_m'],
    'focus.move': ['focus.moving', 'focus.position_m'],
    'ncm.frequency_sweep': list(_OPS),
    'approach': ['engage.state', 'ncm.amplitude_m', 'truth.gap_m', 'truth.probe', 'truth.probes', 'truth.tip_force_n', 'z_stage.moving', 'z_stage.position_m',
                 'z_stage.target_m', 'zscanner.extension_m', 'pspd.vertical_v'] + _S,
    'retract': ['truth.gap_m', 'truth.tip_force_n', 'z_stage.moving', 'z_stage.position_m', 'zscanner.extension_m'] + _PS,
    'scan.start': ['engage.state', 'ncm.amplitude_m', 'truth.gap_m', 'truth.probe', 'truth.probes', 'truth.tip_force_n', 'z_stage.moving', 'z_stage.position_m',
                   'z_stage.target_m', 'zscanner.extension_m', 'pspd.vertical_v'] + _OPS,
    'fd.acquire': ['truth.probe', 'truth.probes'] + _OPS,
    'calibration.thermal_tune': ['calibration.table', 'ncm.amplitude_m'] + _OPS,
    'analysis.flatten': ['result.last_record_id'],
    'records.delete': ['result.last_record_id'],
    'sample.place': ['truth.drift_m'],
    'fault.inject': ['beam.emitting', 'calibration.table', 'ncm.amplitude_m', 'system.error_code'] + _PS + _MOTION + _ENG + _OPS,
    'sim.step': ['beam.emitting', 'calibration.table', 'engage.state', 'focus.moving', 'focus.position_m', 'head.state', 'ncm.amplitude_m', 'probe.box_ids',
                 'probe.exchanging', 'probe.mounted', 'result.last_record_id', 'scan.lines_completed', 'stage.x_m', 'stage.xy_m', 'stage.xy_moving', 'stage.y_m',
                 'system.error_code',
                 'system.state', 'truth.drift_m', 'truth.gap_m', 'truth.probe', 'truth.probes', 'truth.tip_force_n', 'z_stage.moving', 'z_stage.position_m',
                 'z_stage.target_m', 'zscanner.extension_m'] + _S + _PS,
}
ON_STEP_ZH = {
    'power.set': '上电20 s后SmartScan就绪（光束随后发光、PSPD有读数）；热漂移自上电起重新计时', 'power.press_button': '同power.set', 'head.remove': '15 s后测头取下', 'head.attach': '15 s后测头装上（光束发光、热漂移重新计时）',
    'probe.mount': '20 s后探针装上（随机的安装偏差决定光斑位置与PSPD读数）', 'probe.unmount': '20 s后探针回到探针盒', 'stage.move_xy': 'XY台以2 mm/s移到目标',
    'z_stage.move': 'Z台以0.8 mm/s移动；下降途中触到样品时触碰停止（轻微磨损）或撞断悬臂', 'focus.move': '聚焦台以0.8 mm/s移动',
    'approach': 'Z台以50 µm/s下降到触发点、Z扫描器调到半伸出、稳定3 s后进针完成；失败时Z台抬起100 µm', 'retract': 'Z台上升给定距离',
    'scan.start': '按行推进（每16行一块写入图像、更新针尖磨损与警告），完成后写入记录，可选自动抬针', 'fd.acquire': '每条曲线结束时写入（接触载荷可能使针尖变钝）',
    'calibration.thermal_tune': '完成时写入功率谱与拟合，并更新校准表的力常数、f0与Q', 'ncm.frequency_sweep': '约6 s后写入扫频记录',
    'sim.step': '推进电动台与聚焦台、测头装卸与换针、初始化、操作进度（扫描块、力曲线、热调谐、扫频、进针）、热漂移、电能与光束时间账',
    'analysis.flatten': '处理本身没有后续演化；之后完成的记录写入新的最新记录号（按有/无调用的对比列为步进效应）', 'records.delete': '同analysis.flatten',
    'sample.place': '放样后热漂移重新计时（漂移随时间积累）', 'fault.inject': '故障在初始化结束时锁存（SLD故障）、光束恢复发光等随时间的影响',
    'beam.set': '', 'beam.press_switch': '',
}
_R = ['power.enabled', 'system.state', 'system.error_code']
_IDLE = ['operation.id', 'engage.state']
PRECONDITIONS = {
    'plant.configure': ('断电且没有操作时才可（重新）声明', ['power.enabled', 'operation.id']),
    'sample.provide': ('已声明实验室条件；样品ID未用过、样品数≤12；样品不超过100×100 mm、厚20 mm；覆盖参数属于模板', ['plant.configured', 'sample.bench_ids']),
    'probe.provide': ('已声明实验室条件；探针ID未用过、探针数≤24；型号在探针库中', ['plant.configured', 'probe.box_ids']),
    'sample.place': ('样品在实验台上、吸盘空；未进针、无操作；电动台静止、测头与探针不在装卸；样品在100×100 mm开放区（小样品在磁性样品座上）；针尖高出新样品顶面2 mm',
                     ['sample.bench_ids', 'sample.on_chuck'] + _IDLE + ['stage.xy_moving', 'z_stage.moving', 'head.state', 'probe.exchanging', 'z_stage.position_m']),
    'sample.remove': ('样品在吸盘上；未进针、无操作；静止；针尖高出样品顶面2 mm', ['sample.on_chuck'] + _IDLE + ['stage.xy_moving', 'z_stage.moving', 'z_stage.position_m']),
    'power.set': ('已声明实验室条件', ['plant.configured', 'power.enabled']),
    'power.press_button': ('同power.set（按一次切换通断）', ['plant.configured', 'power.enabled']),
    'beam.set': ('关光束时不能有需要光束的操作在进行', ['beam.switch_on', 'operation.id']),
    'beam.press_switch': ('同beam.set（切换）', ['beam.switch_on', 'operation.id']),
    'head.remove': ('已声明实验室条件；测头在导轨上；未进针、无操作、电动台静止、不在换针；针尖高出样品顶面1 mm；聚焦台不低于参考位2 mm（手册：聚焦台离测头太近先升起）；'
                    '光束开关应先关（手册：否则SLD可能损坏——通电时开着光束开关装卸测头会使SLD失效，评测者可见）',
                    ['head.state', 'beam.switch_on', 'focus.position_m'] + _IDLE + ['stage.xy_moving', 'z_stage.moving', 'z_stage.position_m']),
    'head.attach': ('测头已取下且不在移动或换针；针尖高出样品顶面1 mm；聚焦台不低于参考位2 mm；光束开关应先关（同head.remove）',
                    ['head.state', 'probe.exchanging', 'z_stage.position_m', 'beam.switch_on', 'focus.position_m']),
    'probe.mount': ('测头已取下（不在移动）；探针夹上没有探针；该探针在探针盒里；不在换针', ['head.state', 'probe.mounted', 'probe.box_ids', 'probe.exchanging']),
    'probe.unmount': ('测头已取下；探针夹上有探针；不在换针', ['head.state', 'probe.mounted', 'probe.exchanging']),
    'head.configure': ('已就绪、无错误；无操作；未进针', _R + _IDLE),
    'head.turn_knob': ('已声明实验室条件；无操作；未进针；累计圈数不超过旋钮极限（光束±15、PSPD与光学±10圈）', ['knob.beam_x_turns', 'knob.beam_y_turns', 'knob.optics_x_turns', 'knob.optics_y_turns', 'knob.pspd_x_turns', 'knob.pspd_y_turns'] + _IDLE),
    'head.set_knob': ('同head.turn_knob（给定累计圈数）', ['knob.beam_x_turns', 'knob.beam_y_turns', 'knob.optics_x_turns', 'knob.optics_y_turns', 'knob.pspd_x_turns', 'knob.pspd_y_turns'] + _IDLE),
    'stage.move_xy': ('已就绪、无错误；无操作、未进针；测头与探针不在装卸；针尖离样品顶面不足100 µm时不能跨过样品边缘、针尖贴在表面时不能移动', _R + _IDLE + ['head.state', 'z_stage.position_m']),
    'z_stage.move': ('已就绪、无错误；无操作、未进针；测头与探针不在装卸；下降时INTENSITY须≥0.5 V（联锁）', _R + _IDLE + ['head.state', 'pspd.intensity_v']),
    'focus.move': ('已就绪、无错误', _R),
    'camera.read': ('已就绪、无错误', _R),
    'ncm.frequency_sweep': ('已就绪、无错误；无操作；测头装上、有探针、已配置轻敲或非接触模式；未进针；光束发光且INTENSITY≥0.5 V；电动台静止',
                            _R + _IDLE + ['head.state', 'probe.mounted', 'head.mode', 'beam.emitting', 'pspd.intensity_v']),
    'ncm.set_drive': ('同扫频；驱动频率处的响应不低于共振的2 %且需要的共振振幅不超过2 µm', _R + _IDLE + ['head.state', 'probe.mounted', 'head.mode', 'pspd.intensity_v']),
    'approach': ('已就绪、无错误；无操作；测头装上、有探针、已配置模式（动态模式已设驱动、振幅设定点小于自由振幅）；未进针；电动台静止；光束发光且INTENSITY≥0.5 V；针尖下有样品',
                 _R + _IDLE + ['head.state', 'probe.mounted', 'head.mode', 'ncm.drive', 'beam.emitting', 'pspd.intensity_v', 'sample.on_chuck', 'stage.xy_m']),
    'retract': ('通电就绪（有锁存错误也可抬针）；无操作；测头与探针不在装卸', ['power.enabled', 'system.state', 'operation.id', 'head.state']),
    'scan.start': ('已进针；无操作；设定点与模式匹配；针尖速度≤200 µm/s；扫描区在XY扫描器内；像素≤2048×2048（仿真上限）；光束发光且INTENSITY≥0.5 V',
                   _R + _IDLE + ['head.mode', 'ncm.drive', 'beam.emitting', 'pspd.intensity_v']),
    'fd.acquire': ('已进针；无操作；曲线数≤64；光束发光且INTENSITY≥0.5 V', _R + _IDLE + ['beam.emitting', 'pspd.intensity_v']),
    'calibration.thermal_tune': ('已就绪、无错误；无操作；测头装上、有探针、已配置模式；未进针；光束发光且INTENSITY≥0.5 V',
                                 _R + _IDLE + ['head.state', 'probe.mounted', 'head.mode', 'beam.emitting', 'pspd.intensity_v']),
    'calibration.sensitivity': ('已声明实验室条件；无操作；记录为已结束的力曲线且接触段足够（≥6点、单调）', ['operation.id', 'result.last_record_id']),
    'calibration.set': ('已声明实验室条件；无操作；已选择悬臂数据库条目', ['operation.id', 'calibration.table']),
    'analysis.flatten': ('记录为已结束的图像（≥2行）且有该通道与方向', ['result.last_record_id']),
    'analysis.roughness': ('同analysis.flatten；区域在图像内', ['result.last_record_id']),
    'analysis.line_profile': ('同analysis.flatten；端点在图像内', ['result.last_record_id']),
    'analysis.step_height': ('同analysis.flatten；histogram需两级表面，regions需两个区域', ['result.last_record_id']),
    'analysis.fd_fit': ('记录为已结束的力曲线，序号存在，接触段足够', ['result.last_record_id']),
    'measurement.result': ('记录存在；通道与方向存在；区域在图像内', ['result.last_record_id']),
    'records.delete': ('记录存在且不属于进行中的操作；agent只能删除自己或宿主的记录', ['operation.id', 'result.last_record_id']),
    'system.acknowledge': ('已声明实验室条件；错误原因已排除（故障已清除）', ['system.error_code']),
    'fault.inject': ('已声明实验室条件', ['plant.configured']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE', 'DEVICE_ERROR']
_RE = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING']
_START = _RE + ['MEASUREMENT_RUNNING', 'HEAD_NOT_ATTACHED', 'MOTION_IN_PROGRESS', 'NO_PROBE', 'MODE_NOT_CONFIGURED', 'BEAM_OFF', 'INTENSITY_LOW']
_REC = ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'CHANNEL_NOT_RECORDED']
CAP_ERRORS = {
    'plant.configure': ['POWER_ON'],
    'sample.provide': ['PLANT_REQUIRED', 'DUPLICATE_SAMPLE', 'TOO_MANY_SAMPLES', 'OVERRIDE_NOT_APPLICABLE', 'SAMPLE_TOO_LARGE', 'UNKNOWN_MATERIAL', 'SIMULATION_LIMIT'],
    'probe.provide': ['PLANT_REQUIRED', 'DUPLICATE_PROBE', 'TOO_MANY_PROBES', 'UNKNOWN_PROBE_TYPE'],
    'sample.place': ['PLANT_REQUIRED', 'UNKNOWN_SAMPLE', 'SAMPLE_NOT_ON_BENCH', 'CHUCK_OCCUPIED', 'MEASUREMENT_RUNNING', 'ENGAGED', 'MOTION_IN_PROGRESS', 'SAMPLE_OUT_OF_AREA',
                     'COLLISION_RISK'],
    'sample.remove': ['PLANT_REQUIRED', 'SAMPLE_NOT_ON_CHUCK', 'MEASUREMENT_RUNNING', 'ENGAGED', 'MOTION_IN_PROGRESS', 'COLLISION_RISK'],
    'power.set': ['PLANT_REQUIRED'], 'power.press_button': ['PLANT_REQUIRED'],
    'beam.set': ['MEASUREMENT_RUNNING'], 'beam.press_switch': ['MEASUREMENT_RUNNING'],
    'head.remove': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'ENGAGED', 'MOTION_IN_PROGRESS', 'HEAD_NOT_ATTACHED', 'COLLISION_RISK', 'FOCUS_TOO_LOW'],
    'head.attach': ['PLANT_REQUIRED', 'MOTION_IN_PROGRESS', 'HEAD_ATTACHED', 'COLLISION_RISK', 'FOCUS_TOO_LOW'],
    'probe.mount': ['PLANT_REQUIRED', 'HEAD_ATTACHED', 'MOTION_IN_PROGRESS', 'UNKNOWN_PROBE', 'PROBE_MOUNTED', 'PROBE_NOT_IN_BOX'],
    'probe.unmount': ['PLANT_REQUIRED', 'HEAD_ATTACHED', 'MOTION_IN_PROGRESS', 'NO_PROBE'],
    'head.configure': _RE + ['MEASUREMENT_RUNNING', 'ENGAGED', 'UNKNOWN_PROBE_TYPE'],
    'head.turn_knob': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'ENGAGED', 'KNOB_LIMIT'],
    'head.set_knob': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'ENGAGED', 'KNOB_LIMIT'],
    'stage.move_xy': _RE + ['MEASUREMENT_RUNNING', 'ENGAGED', 'MOTION_IN_PROGRESS', 'COLLISION_RISK'],        # a stall is latched (system.error_code), not a rejection
    'z_stage.move': _RE + ['MEASUREMENT_RUNNING', 'ENGAGED', 'MOTION_IN_PROGRESS', 'INTENSITY_LOW', 'TIP_IN_CONTACT'],
    'focus.move': _RE + ['MOTION_IN_PROGRESS'], 'camera.read': list(_RE),
    'ncm.frequency_sweep': _START + ['ENGAGED', 'MODE_NOT_DYNAMIC', 'TIP_IN_CONTACT', 'RECORDS_FULL'] + ['POWER_OFF', 'SLD_FAILURE', 'CANCELLED'],
    'ncm.set_drive': [c for c in _START if c != 'MOTION_IN_PROGRESS'] + ['ENGAGED', 'MODE_NOT_DYNAMIC', 'DRIVE_OFF_RESONANCE'],
    'approach': _START + ['ENGAGED', 'SETPOINT_REQUIRED', 'DRIVE_NOT_SET', 'SETPOINT_OUT_OF_RANGE', 'NO_SAMPLE_UNDER_TIP', 'TIP_IN_CONTACT']
                + ['APPROACH_FAILED', 'SLD_FAILURE', 'Z_STAGE_FAULT', 'CANCELLED'],
    'retract': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS'],
    'scan.start': _START + ['NOT_ENGAGED', 'SETPOINT_REQUIRED', 'DRIVE_NOT_SET', 'SETPOINT_OUT_OF_RANGE', 'TIP_SPEED_TOO_HIGH', 'SCAN_OUT_OF_RANGE', 'SIMULATION_LIMIT',
                                  'RECORDS_FULL'] + ['SLD_FAILURE', 'CANCELLED'],
    'fd.acquire': _START + ['NOT_ENGAGED', 'TOO_MANY_CURVES', 'RECORDS_FULL'] + ['SLD_FAILURE', 'CANCELLED'],
    'calibration.thermal_tune': _START + ['ENGAGED', 'TIP_IN_CONTACT', 'RECORDS_FULL'] + ['SLD_FAILURE', 'CANCELLED'],
    'calibration.sensitivity': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'UNKNOWN_CURVE', 'FIT_FAILED'],
    'calibration.set': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'MODE_NOT_CONFIGURED'],
    'analysis.flatten': _REC + ['RECORDS_FULL'], 'analysis.roughness': _REC + ['REGION_OUT_OF_RANGE'], 'analysis.line_profile': _REC + ['REGION_OUT_OF_RANGE'],
    'analysis.step_height': _REC + ['REGION_OUT_OF_RANGE', 'REGION_REQUIRED', 'NO_STEP_FOUND'],
    'analysis.fd_fit': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'UNKNOWN_CURVE', 'FIT_FAILED'],
    'measurement.result': ['UNKNOWN_RECORD', 'CHANNEL_NOT_RECORDED', 'REGION_OUT_OF_RANGE'],
    'records.delete': ['UNKNOWN_RECORD', 'RECORD_IN_USE'],
    'system.acknowledge': ['PLANT_REQUIRED', 'CAUSE_PERSISTS'], 'fault.inject': ['PLANT_REQUIRED'],
}
_PARTIAL = {'POWER_OFF', 'SLD_FAILURE', 'Z_STAGE_FAULT', 'APPROACH_FAILED', 'CANCELLED'}
CAP_ERROR_ZH = {
    ('scan.start', 'SIMULATION_LIMIT'): '单幅图像像素超过仿真上限2048×2048（NX10本身可存4096×4096；仿真边界，不是仪器故障）',
    ('sample.provide', 'SIMULATION_LIMIT'): '程序化形貌对象过多：每2 µm图块颗粒超过5000、二维片超过50或纤维超过200，或一次查询的纤维链点超过10⁶；'
                                            '增大尺寸或降低覆盖度（仿真边界，不是仪器故障）',
    ('scan.start', 'RECORDS_FULL'): '记录已满：记录数达到200，或新图像会使全部记录的图像像素内存超过2 GiB（仿真上限）；删除记录后再试',
    ('analysis.flatten', 'RECORDS_FULL'): '记录已满：记录数达到200，或处理结果会使全部记录的图像像素内存超过2 GiB（仿真上限）；删除记录后再试',
    ('sample.provide', 'INVALID_ARGUMENT'): '参数不符合输入模式，或形貌参数超出物理范围、彼此矛盾（边沿宽于方块、粒径离散超过粒径的三分之一、原子台阶的阶高/台面宽超过0.05）',
    ('approach', 'APPROACH_FAILED'): '进针失败：接触模式中VERTICAL在达到设定点前饱和（PSPD没对中，针尖压坏，评测者可见）或触发点低于Z台下限；Z台抬起100 µm',
    ('approach', 'POWER_OFF'): '未通电；或进针中断电（Z台停在当时位置）', ('approach', 'Z_STAGE_FAULT'): '进针中Z台卡滞（锁存）',
    ('approach', 'SLD_FAILURE'): '进针中SLD失效（锁存，Z扫描器缩回）',
    ('scan.start', 'SLD_FAILURE'): '扫描中SLD失效：记录以failed结束，保留已完成的行；Z扫描器缩回（未进针）',
    ('scan.start', 'POWER_OFF'): '未通电；或扫描中断电：记录以failed结束，保留已完成的行', ('scan.start', 'CANCELLED'): '扫描被取消：记录以cancelled结束，保留已完成的行，针尖仍在反馈中',
    ('fd.acquire', 'CANCELLED'): '取消：已完成的曲线保留', ('fd.acquire', 'SLD_FAILURE'): '采集中SLD失效：已完成的曲线保留，记录failed',
    ('calibration.thermal_tune', 'CANCELLED'): '取消：记录没有数据，校准表不变', ('ncm.frequency_sweep', 'CANCELLED'): '取消：记录没有数据',
}
MODEL_NONE_ZH = {
    'plant.configure': '实验室条件没有可见模型', 'sample.provide': '样品在实验台上，场景中没有样品几何', 'probe.provide': '探针盒没有几何',
    'sample.place': '样品在场景中没有独立几何（吸盘上的样品位为接口点）', 'sample.remove': '样品没有几何', 'fault.inject': '注入故障本身没有可见变化',
    'system.acknowledge': '只清除错误状态', 'head.remove': '测头装卸为宿主/机器人抽象，场景中测头固定在导轨上', 'head.attach': '同head.remove',
    'probe.mount': '悬臂芯片为小构件，装卸没有关节', 'probe.unmount': '同probe.mount', 'head.configure': '软件设置没有可见变化', 'ncm.set_drive': '软件设置没有可见变化',
    'calibration.sensitivity': '软件计算', 'calibration.set': '软件设置', 'analysis.flatten': '软件计算', 'records.delete': '只删除软件中的记录',
    'ncm.frequency_sweep': '悬臂振动尺度在场景中不可见', 'calibration.thermal_tune': '热噪声不可见', 'fd.acquire': 'Z扫描器位移（nm–µm）在场景中不可见',
}
SCENE_MIRRORED = {'stage.x_m', 'stage.y_m', 'z_stage.position_m', 'focus.position_m', 'beam.switch_on', 'beam.emitting', 'power.enabled'} | set(['knob.beam_x_turns', 'knob.beam_y_turns', 'knob.optics_x_turns', 'knob.optics_y_turns', 'knob.pspd_x_turns', 'knob.pspd_y_turns'])


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节（XY台、Z台、聚焦台、调节旋钮）、光束开关键与指示灯（' + '、'.join(seen)
                                   + '；绑定见合同model_bindings），运行层本身不驱动模型' + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {
    'approach': ('Z台以50 µm/s下降到触发点（Z扫描器全伸出时达到设定点），再下降7.5 µm使Z扫描器半伸出，稳定3 s后engage.state=engaged',
                 '取消或失败（断电、SLD失效、Z台卡滞、设定点未到达）时Z台停在当时位置（失败后抬起100 µm），不进针；接触模式VERTICAL饱和时针尖压坏（评测者可见）；'
                 '动态模式VERTICAL偏离中心>0.9×INTENSITY时假进针（在远处触发，评测者可见的false_engage）'),
    'scan.start': ('按行频推进，每16行一块写入图像，全部行完成后记录completed、写入最新记录号（可选抬针100 µm）',
                   '取消时保留已完成的行（按取消时刻计算到当前行），针尖仍在反馈中；SLD失效或断电时保留已完成的行、记录failed、Z扫描器缩回；针尖磨损按块累积'),
    'fd.acquire': ('每条曲线（1/频率+停留+0.05 s）结束时写入，全部完成后记录completed', '取消或失败时已完成的曲线保留'),
    'calibration.thermal_tune': ('2+0.1×平均次数秒后写入功率谱与阻尼谐振子拟合，并更新校准表（k、f0、Q）；窗口内没有共振峰时记录带no_resonance_in_window，校准表不变',
                                 '取消或失败时记录没有数据、校准表不变'),
    'ncm.frequency_sweep': ('6 s后写入振幅与相位曲线、峰值频率与Q估计', '取消或失败时记录没有数据'),
}
RESOURCES = {'approach': ['z_stage', 'z_scanner', 'sld', 'pspd', 'controller', 'host_clock'], 'scan.start': ['xy_scanner', 'z_scanner', 'sld', 'pspd', 'controller', 'host_clock'],
             'fd.acquire': ['z_scanner', 'sld', 'pspd', 'controller', 'host_clock'], 'calibration.thermal_tune': ['sld', 'pspd', 'controller', 'host_clock'],
             'ncm.frequency_sweep': ['bimorph', 'sld', 'pspd', 'controller', 'host_clock'], 'stage.move_xy': ['xy_stage'], 'z_stage.move': ['z_stage'], 'retract': ['z_stage'],
             'focus.move': ['focus_stage'], 'power.set': ['mains', 'controller'], 'power.press_button': ['mains', 'controller'], 'beam.set': ['sld'], 'beam.press_switch': ['sld'], 'head.remove': ['nx_head', 'dovetail'],
             'head.attach': ['nx_head', 'dovetail'], 'probe.mount': ['probehand', 'probe_box'], 'probe.unmount': ['probehand', 'probe_box'], 'head.turn_knob': ['knobs'], 'head.set_knob': ['knobs'],
             'head.configure': ['controller'], 'ncm.set_drive': ['bimorph', 'controller'], 'plant.configure': ['room'], 'sample.provide': ['bench'], 'probe.provide': ['probe_box'],
             'sample.place': ['bench', 'sample_chuck'], 'sample.remove': ['bench', 'sample_chuck'], 'camera.read': ['camera'], 'calibration.sensitivity': ['workstation'],
             'calibration.set': ['workstation'], 'analysis.flatten': ['workstation'], 'analysis.roughness': ['workstation'], 'analysis.line_profile': ['workstation'],
             'analysis.step_height': ['workstation'], 'analysis.fd_fit': ['workstation'], 'records.delete': ['workstation'], 'fault.inject': ['evaluator'], 'sim.step': ['host_clock'],
             'system.acknowledge': ['controller']}
OBSERVES = {
    'power.set': ['power.enabled', 'system.state'], 'power.press_button': ['power.enabled', 'system.state'], 'beam.set': ['beam.switch_on', 'beam.emitting'], 'beam.press_switch': ['beam.switch_on', 'beam.emitting'],
    'head.remove': ['head.state'], 'head.attach': ['head.state'], 'probe.mount': ['probe.mounted', 'probe.exchanging'], 'probe.unmount': ['probe.mounted', 'probe.box_ids'],
    'head.configure': ['head.mode', 'calibration.table'], 'head.turn_knob': ['knob.beam_x_turns', 'knob.beam_y_turns', 'knob.optics_x_turns', 'knob.optics_y_turns', 'knob.pspd_x_turns', 'knob.pspd_y_turns'] + ['pspd.intensity_v', 'pspd.vertical_v', 'pspd.lateral_v'],
    'head.set_knob': ['knob.beam_x_turns', 'knob.beam_y_turns', 'knob.optics_x_turns', 'knob.optics_y_turns', 'knob.pspd_x_turns', 'knob.pspd_y_turns'] + ['pspd.intensity_v', 'pspd.vertical_v', 'pspd.lateral_v'],
    'stage.move_xy': ['stage.xy_m', 'stage.x_m', 'stage.y_m', 'stage.xy_moving'], 'z_stage.move': ['z_stage.position_m', 'z_stage.moving'], 'focus.move': ['focus.position_m', 'focus.moving'],
    'retract': ['engage.state', 'z_stage.position_m'], 'ncm.set_drive': ['ncm.drive', 'ncm.amplitude_m'],
    'approach': ['engage.state', 'operation.id', 'zscanner.extension_m', 'z_stage.position_m'],
    'scan.start': ['operation.id', 'operation.progress', 'scan.lines_completed', 'result.last_record_id'],
    'fd.acquire': ['operation.id', 'operation.progress', 'result.last_record_id'], 'calibration.thermal_tune': ['operation.id', 'calibration.table', 'result.last_record_id'],
    'ncm.frequency_sweep': ['operation.id', 'result.last_record_id'], 'calibration.sensitivity': ['calibration.table'], 'calibration.set': ['calibration.table'],
    'analysis.flatten': ['result.last_record_id'], 'records.delete': ['result.last_record_id'], 'system.acknowledge': ['system.error_code'],
    'sample.place': ['sample.on_chuck'], 'sample.remove': ['sample.bench_ids'],
}
_WB = 'user_workbook'
_BRO = 'manufacturer_primary'
_MAN = 'manufacturer_manual'
_OPT = 'manufacturer_options'
_WEB = 'manufacturer_web'
_FAC = 'facility_utexas'
_FAC2 = 'facility_uw'
_PHYS = 'physics_literature'
_LIB = 'template_libraries'
_SIM = 'simulation_assumption'
CAP_SOURCES = {
    'plant.configure': [_MAN, _OPT, _SIM], 'sample.provide': [_LIB, _MAN, _BRO, _SIM], 'probe.provide': [_LIB, _BRO, _MAN],
    'sample.place': [_BRO, _MAN, _SIM], 'sample.remove': [_MAN, _SIM], 'power.set': [_MAN, _SIM], 'power.press_button': [_MAN, _SIM], 'beam.set': [_MAN, _FAC], 'beam.press_switch': [_MAN, _FAC],
    'head.remove': [_MAN, _FAC, _SIM], 'head.attach': [_MAN, _SIM], 'probe.mount': [_MAN, _BRO, _FAC, _SIM], 'probe.unmount': [_MAN, _SIM],
    'head.configure': [_MAN, _LIB], 'head.turn_knob': [_MAN, _FAC, _SIM], 'head.set_knob': [_MAN, _FAC, _SIM], 'stage.move_xy': [_BRO, _MAN, _WB, _SIM], 'z_stage.move': [_BRO, _MAN, _WB, _SIM],
    'focus.move': [_BRO, _MAN, _SIM], 'camera.read': [_BRO, _MAN, _SIM], 'ncm.frequency_sweep': [_MAN, _FAC2, _PHYS], 'ncm.set_drive': [_MAN, _FAC2, _PHYS],
    'approach': [_MAN, _FAC, _FAC2, _SIM], 'retract': [_MAN], 'scan.start': [_BRO, _MAN, _WB, _WEB, _PHYS, _LIB, _SIM],
    'fd.acquire': [_BRO, _MAN, _PHYS, _LIB, _SIM], 'calibration.thermal_tune': [_BRO, _MAN, _PHYS], 'calibration.sensitivity': [_MAN, _PHYS], 'calibration.set': [_MAN],
    'analysis.flatten': [_MAN, _BRO], 'analysis.roughness': [_BRO, _PHYS], 'analysis.line_profile': [_MAN], 'analysis.step_height': [_MAN, _PHYS],
    'analysis.fd_fit': [_PHYS, _LIB], 'measurement.result': [_WB, _MAN], 'records.list': [_SIM], 'records.delete': [_SIM], 'events.read': [_SIM],
    'system.acknowledge': [_MAN, _SIM], 'provenance.read': [_LIB], 'fault.inject': [_SIM], 'ledger.read': [_SIM], 'sim.step': [_SIM],
    **{k: [_BRO, _OPT, _WEB] for k in UNSUPPORTED}}
PARAM_ZH = {'humidity': '室内相对湿度（0–1；决定毛细凝聚粘附）', 'room_k': '室温（热噪声与记录）', 'acoustic_enclosure': '仪器在声学罩内（声振动×0.25、热漂移×0.5，工程值）',
            'vibration_isolation': '仪器在主动隔振台上（地面振动×0.15，工程值）', 'noise_seed': '噪声种子（决定隐藏的探针实际参数、安装偏差、热漂移方向与测量噪声）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.nx10_afm.limitations。'
CAP_LIMITS = {
    'sample.provide': ['样品为样品库15种模板（校准光栅、HOPG、云母、硅片、SiO2/Si、PMMA膜、PS/PMMA共混、PS-b-PMMA层状相、云母上金颗粒、SiO2上石墨烯与MoS2、云母上质粒DNA、ITO玻璃、'
                       'PS微球单层）的程序化形貌（种子确定，同一位置每次扫描相同）；材料常数取纳米力学库（66种，模量、泊松比、表面能、水接触角、Hamaker常数、损耗角正切逐项标证据）；'
                       '可覆盖形貌数值参数；样品在场景中没有独立几何。'],
    'probe.provide': ['型号标称值取探针库（12种，BudgetSensors/NanoWorld/MikroMasch/Olympus目录与宣传册所列型号；厂家没给的长度、厚度、针尖半径按矩形梁由k与f0推算或取典型值并标'
                      'derived）；每片的实际k按厂家范围（±2σ覆盖）抽取、f0与厚度一致、Q按空气中工程关系、针尖半径在上限以下，均为评测者可见的隐藏真值。'],
    'sample.place': ['宿主/机器人抽象：≤20 mm的样品贴在钢片上放磁性样品座（样品座高3 mm、钢片0.8 mm为工程值），更大的直接放吸盘（最大100×100 mm开放区、厚20 mm、0.5 kg，'
                     '宣传册与手册）；放样后热漂移重新计时；镊子夹取与粘样未建模。'],
    'head.remove': ['燕尾导轨装卸约15 s为工程值；测头取下时SLD不发光；手册要求先关光束开关（通电时未关则SLD失效，评测者可见故障）并在聚焦台离测头太近时先升起；机器人接触未验证。'],
    'probe.mount': ['芯片座靠红宝石球与磁铁定位（手册），新悬臂末端相对光斑±12 µm（1σ）、PSPD上反射光斑±0.9 mm（1σ）为工程值；需用光束与PSPD旋钮细调。'],
    'head.turn_knob': ['旋钮每圈的位移（光束40 µm、PSPD 0.6 mm、光学100 µm）为工程值；光斑在悬臂上的重叠按高斯光斑（1σ 9 µm）与矩形悬臂计算INTENSITY，灵敏度按端载梁斜率'
                       '2ξ−ξ²与悬臂长度计算；PSPD为erf响应的四象限二极管。'],
    'z_stage.move': ['下降联锁INTENSITY≥0.5 V为工程阈值（手册写有该提示但未给数值）；触碰停止与撞针按PSPD是否在±0.9×INTENSITY内判断（工程规则）。'],
    'camera.read': ['清晰度按景深±10 µm的高斯（工程值；10×物镜NA 0.23、WD 50.5 mm为手册值）；光斑位置按0.5 µm量化，没有图像。'],
    'ncm.set_drive': ['驱动能力上限（共振振幅2 µm、响应不低于共振的2 %）为工程值；设定时按当时的真实灵敏度把显示振幅换算为真实振幅，之后改变校准会改变显示值。'],
    'approach': ['自动进针速度50 µm/s、结束时Z扫描器半伸出、稳定3 s为工程值（手册描述进针以Z扫描器伸出检测接触并调到中间位置）；接触模式按“VERTICAL相对自由值增加设定力对应'
                 '的电压”触发，PSPD偏离中心使信号饱和时撞针；动态模式VERTICAL偏离>0.9×INTENSITY时假进针；True Non-Contact：驱动在共振峰低频一侧时范德华吸引先使振幅升高，'
                 '反馈点不存在，针尖进入排斥接触（评测者可见的nc_contact_regime）。'],
    'scan.start': ['成像：程序化形貌经球-锥针尖（半径为隐藏真值，随磨损变大）二维灰度膨胀（针尖足迹上限±24像素：像素很小而针尖很钝时足迹被截断，为仿真边界）；一阶积分反馈（带宽=Z伺服增益×每单位增益带宽：接触1.5 kHz、轻敲0.6 kHz、'
                   '非接触0.5 kHz，工程值）在像素之间按指数精确求解，误差信号饱和（下台阶时针尖“跳伞”、被压超出信号范围）与Z扫描器15 µm量程饱和；增益超过临界值（接触6、轻敲4、非接触3，'
                   '工程值）时叠加振荡；Z检测器噪声0.02 nm（宣传册）、地面/声振动（开放系统0.06 nm RMS，声学罩×0.25、隔振×0.15，工程值）、热漂移（上电或装样后0.8 nm/min横向、1.5 nm/min纵向'
                   '量级并衰减，工程值）、XY扫描器面外弯曲<2 nm（宣传册）；接触模式高度含DMT压入（模量与粘附随材料）；轻敲相位按Cleveland能量耗散关系（粘附滞后1 nm与损耗角正切，工程），'
                   '峰值力=10 kA0/Q·√(1−r²)（工程）；True Non-Contact按Giessibl频移（范德华球-平面）自洽求振幅-距离表，反馈点为振幅降到设定点的最大间隙；Z驱动通道含压电蠕变（1.2 %，'
                   '2 s，工程）；像素上限2048×2048为仿真边界；磨损：滑动Archard型（接触1、轻敲0.03、非接触0.002倍）与Hertz峰值压力超过12 GPa时针尖变钝；每16行一块计算。'],
    'fd.acquire': ['力曲线：范德华逼近（球-平面，jump-in在1.5·(2C/k)^(1/3)）、DMT接触（约化模量、Johnson–Kendall–Roberts之外的粘附按DMT）、拉脱力=2πRW+毛细（湿度25–65 %线性接入，'
                   '工程）；每条曲线粘附随机±8 %；VERTICAL按erf非线性饱和；噪声=光杠杆检测噪声与低频热噪声；液相、粘弹性蠕变与停留时间效应未建模。'],
    'calibration.thermal_tune': ['热噪声谱=阻尼谐振子+白噪声底（功率谱每点按平均次数的Γ分布），拟合ln谱；k=0.817 kBT S²/面积（Butt & Jaschke 1995）；S取当前校准灵敏度，'
                                 '灵敏度未标定时k按(S校准/S真实)²偏差；光斑位置对动态/静态比的影响未建模；温度由用户输入。'],
    'calibration.sensitivity': ['硬样品上接触段斜率=灵敏度；软样品上斜率为S·k_c/(k_c+k)（低估）；PSPD非线性区会降低斜率（选20–80 %段）。'],
    'analysis.fd_fit': ['线性化拟合(F+F_ad)^(2/3)与z+F/k；DMT用回退曲线最小值为粘附力；样品模量由约化模量与给定的针尖模量/泊松比换算；针尖半径由用户输入（真实半径为隐藏真值）。'],
    'measurement.result': ['记录的template_source.template_id与substances列出样品真实模板与材料，只对评测者可见（agent为null与空列表）；图像按块平均降采样到max_values以内。'],
    'plant.configure': ['湿度决定毛细粘附（25 %以下无、65 %以上完全）；声学罩与隔振台的效果为工程系数；重新声明需断电。'],
    'power.set': ['上电初始化20 s为工程值；控制电子单元功率按200 W（手册规格表）计入电能账。'],
}
LIMITATIONS = [
    '选定配置：Park NX10 2024宣传册配置（XY扫描器50×50 µm闭环、Z扫描器15 µm、XY台20×20 mm、Z台25 mm、聚焦台15 mm、样品100×100 mm/20 mm、10×同轴光学、SLD光束检测、'
    '接触/True Non-Contact/轻敲、相位成像、力-距离谱、热调谐）；KPFM、PFM、EFM、MFM、C-AFM、侧向力、SICM、液相池、温控台、纳米刻蚀为不支持的选件，不重复计为独立整机。',
    '厂家公布：扫描范围、台行程、样品尺寸、Z检测器噪声、面外运动、光学与视野、SLD波长、手册中的INTENSITY/VERTICAL/LATERAL对准值、抬针100 µm、最大像素、功率、工作温度与外形尺寸；'
    '整机质量厂家未公布（几何惯量按估计质量与包围盒，证据为assumption）。探针每片实际参数、光杠杆几何、反馈带宽与临界增益、振动与漂移、磨损与屈服、进针与装卸时间为工程假设。',
    '针尖-样品作用：Hertz/DMT接触（约化模量）、DMT拉脱+毛细凝聚、范德华球-平面（Hamaker常数组合规则）；JKR/Maugis过渡、摩擦与侧向力、静电、液相、粘弹性时间依赖与样品损伤未建模。',
    '样品形貌为程序化模板（光栅、原子台阶、高斯粗糙面、颗粒、两相区、二维片层、蠕虫链/闭合环纤维），不是真实AFM数据；材料常数来自公开表（纳米力学库逐项标证据）。',
    '没有真实NX10硬件或SmartScan软件接口；测头装卸、换针与放样为宿主/机器人抽象，没有接触验证。',
]


def _sampling_text(k):
    if k in ('clock.time_s', 'stage.xy_m', 'stage.x_m', 'stage.y_m', 'z_stage.position_m', 'focus.position_m', 'operation.progress', 'operation.remaining_s', 'truth.drift_m', 'zscanner.extension_m',
             'truth.gap_m'):
        return 'Instantaneous value at the read time: constant-speed motions, operation progress and the thermal drift are integrated exactly and cut at every event.'
    if k == 'scan.lines_completed':
        return 'Updated when a block of 16 lines is completed (or at the end, cancellation or failure of the scan).'
    return 'Changes when a command is accepted or at an event of the operation (phase ends, record completion); no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    return '；另一个agent的操作进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）' if k in _CONTROL_GUARDED else ''


def _documented_parameters():
    return {
        'xy_scan_range_m': (XY_SCAN_RANGE_M, 'm', 'XY扫描器50×50 µm（宣传册、调研表）', [_BRO, _WB]),
        'z_scan_range_m': (Z_SCAN_RANGE_M, 'm', '标准测头Z扫描器15 µm（宣传册、调研表；手册>15 µm）', [_BRO, _WB, _MAN]),
        'xy_stage_travel_m': (2 * XY_STAGE_HALF_M, 'm', 'XY电动台20×20 mm（宣传册、调研表）', [_BRO, _WB]),
        'xy_stage_resolution_m': (XY_STAGE_RESOLUTION_M, 'm', 'XY台分辨率5 µm（手册）', [_MAN]),
        'z_stage_travel_m': (Z_STAGE_RANGE_M, 'm', 'Z电动台25 mm（宣传册、调研表）', [_BRO, _WB]),
        'z_stage_max_speed_m_s': (Z_STAGE_MAX_SPEED_M_S, 'm/s', 'Z台最快0.8 mm/s、分辨率80 nm（手册）', [_MAN]),
        'focus_stage_travel_m': (FOCUS_RANGE_M, 'm', '聚焦台15 mm（宣传册）', [_BRO]),
        'sample_max_m': ([SAMPLE_OPEN_MAX_M, SAMPLE_OPEN_MAX_M, SAMPLE_MAX_THICKNESS_M], 'm', '样品最大100×100 mm、厚20 mm（宣传册、调研表）', [_BRO, _WB]),
        'sample_max_mass_kg': (SAMPLE_MAX_MASS_KG, 'kg', '样品吸盘承重500 g（手册）', [_MAN]),
        'z_detector_noise_m': (Z_DETECTOR_NOISE_M, 'm', 'Z检测器噪声0.02 nm（宣传册）', [_BRO]),
        'out_of_plane_motion_m': (XY_BOW_M, 'm', 'XY扫描面外运动<2 nm（宣传册）', [_BRO]),
        'sld_wavelength_m': (SLD_WAVELENGTH_M, 'm', 'SLD 830 nm（手册）', [_MAN]),
        'objective': ([OBJECTIVE_NA, OBJECTIVE_WD_M], '1', '10×物镜NA 0.23、工作距50.5 mm（手册；宣传册写0.21 NA）', [_MAN, _BRO]),
        'field_of_view_m': (list(FIELD_OF_VIEW_M), 'm', '视野480×360 µm（宣传册、手册）', [_BRO, _MAN]),
        'intensity_coated_v': (INTENSITY_COATED_V, 'V', '镀膜悬臂INTENSITY 2–3 V、无镀膜约1 V（手册）', [_MAN]),
        'pspd_centre_tolerance_v': (PSPD_CENTRE_TOL_V, 'V', 'VERTICAL与LATERAL应在±0.5 V内（手册）', [_MAN]),
        'retract_distance_m': (RETRACT_DISTANCE_M, 'm', '完成后抬针100 µm（手册）', [_MAN]),
        'max_pixels': (MAX_PIXELS, '1', '最大4096×4096像素（手册）', [_MAN]),
        'electronics_power_w': (ELECTRONICS_POWER_W, 'W', '功率200 W（手册规格表）', [_MAN]),
        'body_dimensions_m': (list(BODY_DIMENSIONS_M), 'm', '主体245×293×443 mm（宣传册第8页、调研表；手册250×294×450 mm）', [_BRO, _WB, _MAN]),
        'operating_temperature_k': (list(ROOM_OPERATING_K), 'K', '工作温度18–24 °C（手册）', [_MAN]),
    }


def _engineering_parameters():
    return {
        'boot_s': (BOOT_S, 's', 'SmartScan初始化'), 'head_swap_s': (HEAD_SWAP_S, 's', '燕尾导轨装卸测头'), 'probe_mount_s': (PROBE_MOUNT_S, 's', '装/取探针'),
        'xy_stage_speed_m_s': (XY_STAGE_SPEED_M_S, 'm/s', 'XY台速度'), 'focus_speed_m_s': (FOCUS_SPEED_M_S, 'm/s', '聚焦台速度'),
        'approach_speed_m_s': (APPROACH_SPEED_M_S, 'm/s', '自动进针速度'), 'approach_settle_s': (APPROACH_SETTLE_S, 's', '进针后稳定'),
        'knob_beam_m_per_turn': (KNOB_BEAM_M_PER_TURN, 'm', '光束旋钮每圈光斑位移'), 'knob_pspd_m_per_turn': (KNOB_PSPD_M_PER_TURN, 'm', 'PSPD旋钮每圈位移'),
        'knob_optics_m_per_turn': (KNOB_OPTICS_M_PER_TURN, 'm', '光学旋钮每圈视野平移'), 'spot_sigma_m': (SPOT_SIGMA_M, 'm', 'SLD光斑1σ半径'),
        'prealign_sigma_m': (PREALIGN_SIGMA_M, 'm', '预对准：新悬臂末端相对出厂光斑位置1σ'), 'prealign_inset_m': (PREALIGN_INSET_M, 'm', '出厂光斑位置在名义悬臂末端内侧的距离'), 'pspd_mount_sigma_m': (PSPD_MOUNT_SIGMA_M, 'm', '新芯片反射光斑在PSPD上1σ'),
        'pspd_spot_w_m': (PSPD_SPOT_W_M, 'm', 'PSPD上反射光斑erf半宽'), 'sensitivity_v_per_m_100um': (SENSITIVITY_V_PER_M_100UM, 'V/m', '100 µm悬臂、光斑在末端、2.5 V时的VERTICAL灵敏度'),
        'interlock_intensity_v': (INTERLOCK_INTENSITY_V, 'V', 'Z台下降联锁INTENSITY阈值'), 'z_servo_f0_hz': (dict(Z_SERVO_F0_HZ), 'Hz', '每单位Z伺服增益的反馈带宽'),
        'z_servo_critical_gain': (dict(Z_SERVO_CRITICAL_GAIN), '1', '反馈振荡临界增益'), 'oscillation_m': (OSC_NOISE_M, 'm', '每单位超临界增益的振荡幅度'),
        'creep_fraction': (CREEP_FRACTION, '1', 'Z驱动电压通道的蠕变比例'), 'creep_tau_s': (CREEP_TAU_S, 's', '蠕变时间常数'),
        'vibration_open_m': (VIBRATION_OPEN_M, 'm', '开放系统垂直振动RMS'), 'drift_xy_m_s': (DRIFT_XY_M_S, 'm/s', '横向热漂移名义速率'),
        'drift_z_m_s': (DRIFT_Z_M_S, 'm/s', '纵向热漂移名义速率'), 'drift_settle_s': (DRIFT_SETTLE_S, 's', '扰动后热稳定时间常数'),
        'drift_initial_factor': (DRIFT_INITIAL_FACTOR, '1', '扰动刚发生时漂移相对名义速率的倍数（稳定后剩0.3倍）'), 'deflection_noise_m': (DEFLECTION_NOISE_M, 'm', '光杠杆检测噪声RMS'),
        'wear_m_per_m_n': (WEAR_M_PER_M_N, '1/N', '接触模式Archard型磨损系数（针尖半径增量/(滑动距离×力)）'), 'tip_yield_pa': (P_TIP_YIELD_PA, 'Pa', '针尖屈服峰值压力'),
        'broken_tip_radius_m': (BROKEN_TIP_RADIUS_M, 'm', '断针后的针尖半径'), 'tip_half_angle_rad': (TIP_HALF_ANGLE_RAD, 'rad', '库中没有时的针尖半锥角'),
        'q_air_ref': (Q_REF, '1', '空气中Q=450√(f0/300 kHz)'), 'optical_lever_chi': (OPTICAL_LEVER_CHI, '1', '热调谐光杠杆因子0.817（Butt & Jaschke 1995）'),
        'humidity_capillary': (list(HUMIDITY_CAPILLARY), '1', '毛细粘附接入的湿度区间'), 'tip_surface_energy_j_m2': (TIP_GAMMA, 'J/m2', '氧化硅针尖环境表面能'),
        'hamaker_tip_j': (HAMAKER_TIP_J, 'J', '硅针尖Hamaker常数'), 'adhesion_hysteresis_m': (D_HYST_M, 'm', '轻敲周期粘附滞后距离'),
        'tapping_peak_factor': (TAP_PEAK_FACTOR, '1', '轻敲峰值力系数'), 'max_tip_speed_m_s': (MAX_TIP_SPEED_M_S, 'm/s', '最大针尖速度'),
        'holder_height_m': (HOLDER_HEIGHT_M, 'm', '磁性样品座高度'), 'sample_disk_thickness_m': (SAMPLE_DISK_THICKNESS_M, 'm', '样品钢片厚度'),
        'sim_max_pixels': (SIM_MAX_PIXELS, '1', '单幅图像仿真像素上限'), 'block_lines': (BLOCK_LINES, '1', '图像按块计算的行数'),
        'thin_indent_fraction': (THIN_INDENT_FRACTION, '1', '基底上的颗粒/纤维/片层额外压入不超过其高度的比例'), 'dilation_max_px': (DILATION_MAX_PX, '1', '针尖足迹上限（像素）'),
    }


def engineering_parameters():
    """Engineering values of the process model (for the evidence file): name -> value, unit, meaning."""
    return {k: dict(value=v, unit=u, meaning_zh=m, evidence='assumption') for k, (v, u, m) in _engineering_parameters().items()}


def export_park_nx10_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'ParkNx10Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=[_SIM])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.park_nx10.ParkNx10Session.invoke',
                                              invocation=f'session.invoke(instance_id="{INSTANCE_ID}",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='ParkNx10Runtime.invoke / ParkNx10Session._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate',
                                     description_zh='设定、目标、状态标志或立即改变的派生量；运动与操作进度只随step推进') for s in EFFECTS.get(k, [])],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置、分析或查询；运动由step推进',
                                completion_state_ids=(['operation.id', 'engage.state'] if k == 'approach' else ['operation.id', 'result.last_record_id'] if async_ else []),
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=(EXEC_TEXT[k][1] if k in EXEC_TEXT else
                                                    ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果（运动目标），被拒时状态不变'))),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH.get(e, e)),
                              side_effects='unknown' if e == 'DEVICE_ERROR' else 'partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k) or '随step演化') for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in _documented_parameters().items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    for name, (val, unit, meaning) in _engineering_parameters().items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['base', 'deck_plate', 'stage_x_carriage', 'stage_y_carriage', 'xy_scanner', 'sample_chuck', 'sample_holder', 'frame', 'z_stage_carriage', 'nx_head',
                       'probehand', 'cantilever_chip', 'switch_beam', 'led_beam', 'knob_beam_x', 'knob_beam_y', 'knob_pspd_x', 'knob_pspd_y', 'optical_column', 'objective',
                       'focus_stage', 'knob_optics_x', 'knob_optics_y', 'camera', 'rear_panel'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='NX10原子力显微镜：SLD光杠杆与四象限PSPD、预对准探针夹、电动XY/Z/聚焦台、同轴光学、自动进针、接触/轻敲/True Non-Contact成像的Z反馈、针尖卷积与磨损、'
                   '力-距离谱、热调谐与灵敏度标定、SmartAnalysis式处理（展平、粗糙度、剖面、台阶、力曲线拟合）',
        equations=['optical lever: I = I_max Phi_x Phi_y (Gaussian spot on the rectangular cantilever), S = S_100 (I / 2.5 V) g (2 xi - xi^2) (100 um / L), '
                   'VERTICAL = I erf(p / W + d S sqrt(pi) / (2 I))',
                   'thermal tune: P(f) = P_w + A f0^4 / ((f^2 - f0^2)^2 + (f f0 / Q)^2), <V^2> = A pi f0 Q / 2, k = 0.817 kB T S_cal^2 / <V^2>',
                   'contact: F = 4/3 E* sqrt(R) delta^1.5 - F_ad (DMT), F_ad = 2 pi R 2 sqrt(gamma_t gamma_s) + f_RH 4 pi R gamma_w cos(theta), 1/E* = sum (1 - nu^2) / E',
                   'van der Waals: F = -H R / (6 D^2), H = sqrt(H_tip H_sample); jump-in at z_rel = 1.5 (2 C / k)^(1/3), C = H R / 6; pull-off at z_rel = F_ad / k',
                   'tip dilation: image(x) = max_s [h(x + s) - t(s)], t = sphere-cone (R, alpha) surface above the apex',
                   'feedback: de/dt = dy/dt - omega clamp(e), omega = 2 pi g f_mode; exact exponential between pixels in the linear range, constant rate when saturated',
                   'tapping phase: sin(phi) = (f / f0)(A / A0) + Q E_dis / (pi k A A0), E_dis = F_ad d_hyst + pi tan(delta) 0.4 F_peak delta_peak (Cleveland et al. 1998)',
                   'True Non-Contact: Delta f = -(f0 / (k A)) <F(D + A (1 + cos th)) cos th>, A = A_res L(f_d; f0 + Delta f) (self-consistent), set point gap = largest D with A <= A_set',
                   'wear: dR = k_w F_mean (2 S lines) m_f h_f (10 nm / R); blunting when (6 F E*^2 / (pi^3 R^2))^(1/3) > 12 GPa',
                   'drift: v(t) = v0 (0.3 + 9.7 exp(-t / 1800 s)) after power-on / sample loading / head attach'],
        parameters=params, source_ids=[_SIM, _BRO, _MAN, _OPT, _WEB, _FAC, _FAC2, _PHYS, _LIB, _WB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_park_nx10_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'nx10_afm': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'nx10_afm': mechanism})
