"""Runtime of CHEM-162 instrument.evident_bx53p - Evident / Olympus BX53-P polarizing microscope, conoscopic / orthoscopic configuration (U-CPA
intermediate attachment with the focusable Bertrand lens, U-AN360P rotatable analyzer, U-POC-2 polarizing condenser with the 360 deg polarizer, U-SRP rotatable stage with 45 deg click stops,
U-P4RE quadruple nosepiece with UPLFLN 4XP / 10XP / 40XP / 100XOP, U-TP530 / U-TP137 plates, FN 22 trinocular tube, U-LHLEDC100 LED lamp; a generic
colour CMOS camera on the C-mount is an engineering model, not part of the BX53-P).

Process model: transmitted polarized light through the slide per wavelength (400-700 nm, 10 nm) by Jones calculus - polarizer, sample (retardation
2 pi d dn'(lambda) / lambda with the section birefringence from the principal refractive indices of birefringence_library.json and the crystal
orientation), compensator plate, analyzer - weighted by the LED spectrum (engineering) and the CIE 1931 2 deg colour matching functions (CVRL) to
camera RGB; objectives (magnification, NA, field of view FN 22 / M, resolution, defocus blur), condenser (top lens, iris), stage rotation with click
stops and the camera exposure (Poisson and read noise, full well, 12-bit).  With the Bertrand lens in, the camera images the back focal plane:
the conoscopic interference figure of the grain under the 3.4 mm Bertrand field stop, from the two eigen-indices of every propagation direction
inside the objective aperture (oblique path length, refraction by the mean index).  The sample truth (substance, orientations, thickness) is evaluator-only;
the agent sees images, extinction scans and retardation estimates with candidate lists, never a unique phase (workbook gap: texture is not a phase).
step(dt_s) of the host clock is the only way time advances.  Generic schema / capability / session framework follows the CHEM-161 runtime.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.evident_bx53p"
CATALOG_ID = "CHEM-162"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t3_analytical/evident_bx53p'
OUT_DIR = ROOT / 'outputs/expansion_batch04/t3_analytical/evident_bx53p'
BIREF_LIB_PATH = ASSET_DIR / 'birefringence_library.json'
CMF_PATH = OUT_DIR / 'source_cache/cvrl_ciexyz31_1.csv'
DEG = math.pi / 180.0
LAM_M = np.round(np.arange(400e-9, 700.1e-9, 10e-9), 12)          # integration grid (m)

# ---------------------------------------------------------------- manufacturer values (BX53-P brochure / product page)
OBJECTIVES = [dict(name='UPLFLN 4XP', magnification=4.0, na=0.13, working_distance_m=17.0e-3, immersion='air'),
              dict(name='UPLFLN 10XP', magnification=10.0, na=0.30, working_distance_m=10.0e-3, immersion='air'),
              dict(name='UPLFLN 40XP', magnification=40.0, na=0.75, working_distance_m=0.51e-3, immersion='air'),
              dict(name='UPLFLN 100XOP', magnification=100.0, na=1.30, working_distance_m=0.2e-3, immersion='oil')]
FIELD_NUMBER_M = 22e-3                                                # observation tube FN 22
CONDENSER_NA = {'in': 0.9, 'out': 0.18}                               # U-POC-2 top lens in / out
CONDENSER_WD_M, SLIDE_MAX_M = 1.3e-3, 1.5e-3                          # working distance for 1.5 mm slides
IRIS_RANGE_M = (2e-3, 21e-3)                                          # aperture iris diaphragm
STAGE_CLICK_RAD, STAGE_VERNIER_RAD = 45 * DEG, 0.1 * DEG              # 45 deg click stops; 6' vernier
ANALYZER_STEP_RAD = 0.1 * DEG                                         # U-AN360P minimum angle 0.1 deg
BERTRAND_FIELD_STOP_M = 3.4e-3                                        # U-CPA Bertrand field stop (fixed)
PLATES = {'none': 0.0, 'U-TP530': 530e-9, 'U-TP137': 137e-9}          # tint / 1/4 wavelength plate retardations (m)
PLATE_AZIMUTH_RAD = math.pi / 4    # plate slow axis NE-SW as seen by the observer: +45 deg in the image frame (+x east, +y north = away from the operator)
POWER_W = 24.0                                                        # approx. power consumption
# engineering values (not published): camera, LED spectrum, focus travel
FOCUS_RANGE_M = (-0.015, 0.010)
CAMERA = dict(pixels=(96, 72), full_well_e=10000.0, read_noise_e=2.5, bits=12, exposure_s=(1e-4, 10.0), qe=(0.45, 0.55, 0.40))
LED_PHOTONS_PER_S = 2.0e5                                             # electrons / s per pixel at full lamp level, bright field, 10x (engineering)


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


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


def basis(schema, text):
    """State where a numeric input range comes from (manufacturer value, engineering assumption or simulation limit)."""
    return dict(schema, description=text)


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
    elif k == 'array':
        if not isinstance(v, list) or not s.get('minItems', 0) <= len(v) <= s.get('maxItems', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
        for i, x in enumerate(v):
            validate(s['prefixItems'][i] if i < len(s.get('prefixItems', [])) else s['items'], x)
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
    if k == 'object' and isinstance(v, dict) and 'properties' in s:
        return {key: coerce(s['properties'][key], val) if key in s['properties'] else val for key, val in v.items()}
    if k == 'array' and isinstance(v, list):
        return [coerce(s['items'], x) for x in v]
    return v


# ---------------------------------------------------------------- optical data
@functools.lru_cache(maxsize=1)
def biref_lib():
    lib = json.loads(BIREF_LIB_PATH.read_text())
    assert lib['asset_type_id'] == ASSET_TYPE_ID
    return lib


def principal_indices(sid):
    """Principal refractive indices on LAM_M: uniaxial -> (n_o, n_e); biaxial -> (n_alpha, n_beta, n_gamma), each an array."""
    e = biref_lib()['substances'][sid]
    lam_um = np.asarray(biref_lib()['wavelength_um'], float)
    keys = ('o', 'e') if e['optical_class'] == 'uniaxial' else ('alpha', 'beta', 'gamma')
    return e['optical_class'], [np.interp(LAM_M * 1e6, lam_um, np.asarray(e['indices'][k], float)) for k in keys]


@functools.lru_cache(maxsize=1)
def cmf():
    """CIE 1931 2 deg colour matching functions (CVRL ciexyz31_1.csv, 1 nm) on LAM_M."""
    rows = np.array([[float(v) for v in ln.split(',')[:4]] for ln in CMF_PATH.read_text().strip().splitlines() if ln.strip()])
    return np.stack([np.interp(LAM_M * 1e9, rows[:, 0], rows[:, k]) for k in (1, 2, 3)], 1)


def led_spectrum():
    """White phosphor LED relative spectral power (engineering: 450 nm blue emission + broad 560 nm phosphor band), normalised to unit sum."""
    lam = LAM_M * 1e9
    s = 0.9 * np.exp(-0.5 * ((lam - 450) / 11.0) ** 2) + 1.0 * np.exp(-0.5 * ((lam - 565) / 55.0) ** 2)
    return s / s.sum()


XYZ_TO_SRGB = np.array([[3.2406, -1.5372, -0.4986], [-0.9689, 1.8758, 0.0415], [0.0557, -0.2040, 1.0570]])   # IEC 61966-2-1 (linear sRGB, D65)


def section_indices(sid, optic_dir):
    """The two refractive indices (slow n1 >= fast n2, arrays over LAM_M) and the slow-axis azimuth (rad, in the slide plane, from +x) of light
    travelling along the microscope axis (z) through a crystal whose principal axes (columns of the rotation optic_dir, crystal -> slide frame)
    carry the principal indices: eigen-problem of the inverse dielectric tensor projected on the xy plane (Fresnel), per wavelength."""
    kind, n = principal_indices(sid)
    R = np.asarray(optic_dir, float)
    if kind == 'uniaxial':
        n = [n[0], n[0], n[1]]                                        # optic axis = third principal axis
    n1, n2, az = np.empty(len(LAM_M)), np.empty(len(LAM_M)), np.empty(len(LAM_M))
    for i in range(len(LAM_M)):
        B = R @ np.diag([1 / n[0][i] ** 2, 1 / n[1][i] ** 2, 1 / n[2][i] ** 2]) @ R.T   # impermeability tensor in the slide frame
        w, v = np.linalg.eigh(B[:2, :2])                              # D in the xy plane (normal incidence, k along z)
        n1[i], n2[i] = 1 / math.sqrt(w[0]), 1 / math.sqrt(w[1])       # smaller eigenvalue -> larger index (slow)
        az[i] = math.atan2(v[1, 0], v[0, 0])
    return n1, n2, float(np.mod(np.median(az), math.pi))


def retarder(gamma, theta):
    """Jones matrices (N, 2, 2) of linear retarders with retardation gamma (N) and slow axis at theta (rad from +x)."""
    c, s = math.cos(theta), math.sin(theta)
    R = np.array([[c, -s], [s, c]])
    J = np.zeros((len(gamma), 2, 2), complex)
    J[:, 0, 0], J[:, 1, 1] = np.exp(-0.5j * gamma), np.exp(0.5j * gamma)
    return R @ J @ R.T


def polarizer(p):
    c, s = math.cos(p), math.sin(p)
    return np.array([[c * c, c * s], [c * s, s * s]], complex)


def transmission(p, a, analyzer_in, gamma, theta, plate_m=0.0):
    """Transmitted intensity fraction per wavelength (unpolarized source; ideal polarizer / analyzer): polarizer p, sample retarder (gamma, theta),
    compensator plate of retardation plate_m with its slow axis NE-SW (PLATE_AZIMUTH_RAD; quartz dispersion neglected), analyzer a (or none)."""
    E = polarizer(p) @ np.array([math.cos(p), math.sin(p)], complex)   # linearly polarized after the polarizer (1/2 of unpolarized power)
    M = retarder(gamma, theta)
    if plate_m:
        M = retarder(2 * math.pi * plate_m / LAM_M, PLATE_AZIMUTH_RAD) @ M
    out = M @ E
    if analyzer_in:
        out = np.einsum('ij,nj->ni', polarizer(a), out)
    return 0.5 * np.sum(np.abs(out) ** 2, axis=1)


def rgb_of(T):
    """Linear sRGB of a transmission spectrum T (N) under the LED (white point: T = 1 gives Y = 1)."""
    S = led_spectrum()[:, None] * cmf()
    xyz = (T[:, None] * S).sum(0) / S[:, 1].sum()
    return XYZ_TO_SRGB @ xyz


def orientation_matrix(tilt, azimuth, spin=0.0):
    """Crystal principal axes -> slide frame: the third principal axis (optic axis of a uniaxial crystal) tilted by `tilt` from the microscope
    axis toward `azimuth`, then turned by `spin` about it."""
    ct, st, ca, sa = math.cos(tilt), math.sin(tilt), math.cos(azimuth), math.sin(azimuth)
    c3 = np.array([st * ca, st * sa, ct])
    ref = np.array([0.0, 0.0, 1.0]) if abs(ct) < 0.999 else np.array([1.0, 0.0, 0.0])
    c1 = np.cross(ref, c3)
    c1 /= np.linalg.norm(c1)
    c2 = np.cross(c3, c1)
    cs, ss = math.cos(spin), math.sin(spin)
    c1, c2 = cs * c1 + ss * c2, -ss * c1 + cs * c2
    return np.stack([c1, c2, c3], 1)


# ---------------------------------------------------------------- samples and images
def make_sample(spec, rng):
    """Evaluator truth of a slide from its spec: kind single_crystal | thin_section | liquid_crystal | isotropic; substance_id (library),
    thickness_m; grains (thin section: Voronoi seeds with random orientations); defects (liquid crystal: Schlieren strengths +-1/2, +-1)."""
    kind, sid, d = spec['kind'], spec.get('substance_id'), float(spec['thickness_m'])
    s = dict(kind=kind, substance_id=sid, thickness_m=d, grains=[], defects=[])
    if kind == 'thin_section':
        for _ in range(int(spec.get('grains', 40))):
            s['grains'].append(dict(xy=rng.uniform(-1, 1, 2).tolist(), tilt=float(np.arccos(rng.uniform(0, 1))), azimuth=float(rng.uniform(0, 2 * math.pi)),
                                    spin=float(rng.uniform(0, 2 * math.pi))))
    elif kind == 'single_crystal':
        o = spec.get('orientation') or dict(tilt=float(np.arccos(rng.uniform(0, 1))), azimuth=float(rng.uniform(0, 2 * math.pi)), spin=0.0)
        s['grains'].append(dict(xy=[0.0, 0.0], radius=0.6, **o))
    elif kind == 'liquid_crystal':
        for k in range(int(spec.get('defects', 4))):
            s['defects'].append(dict(xy=rng.uniform(-0.8, 0.8, 2).tolist(), strength=float(rng.choice([-1.0, -0.5, 0.5, 1.0]))))
    return s


def grain_spectra(sample, stage_rad, p, a, analyzer_in, plate_m):
    """Transmission spectrum of every grain (thin section / single crystal) for the current stage angle and optics."""
    out = []
    for g in sample['grains']:
        n1, n2, az = section_indices(sample['substance_id'], orientation_matrix(g['tilt'], g['azimuth'], g['spin']))
        gamma = 2 * math.pi * sample['thickness_m'] * (n1 - n2) / LAM_M
        out.append(transmission(p, a, analyzer_in, gamma, az + stage_rad, plate_m))
    return out


def render_linear(sample, stage_rad, p, a, analyzer_in, plate_m, pixels=CAMERA['pixels']):
    """Noise-free linear sRGB image (H, W, 3) of the field: grains by nearest seed (thin section), a disc (single crystal), a planar liquid
    crystal cell with a Schlieren director field; the mounting medium / isotropic regions only pass the polarizer-analyzer pair; the circular
    field stop darkens the corners.  The pattern (grain mosaic, defect positions) is fixed on the slide and turns with the stage; the vibration
    directions turn with it (azimuth + stage angle)."""
    W, H = pixels
    yy, xx = np.mgrid[0:H, 0:W]
    x = (xx - (W - 1) / 2) / ((W - 1) / 2)
    y = -(yy - (H - 1) / 2) / ((W - 1) / 2)                           # image frame: +y up (away from the operator, asset +y)
    cs_, ss_ = math.cos(stage_rad), math.sin(stage_rad)
    xs, ys = cs_ * x + ss_ * y, -ss_ * x + cs_ * y                    # slide coordinates of each pixel: the specimen turns with the (centred) stage
    iso = rgb_of(transmission(p, a, analyzer_in, np.zeros(len(LAM_M)), 0.0, plate_m))
    img = np.broadcast_to(iso, (H, W, 3)).copy()
    kind = sample['kind']
    if kind in ('thin_section', 'single_crystal') and sample['grains']:
        cols = np.array([rgb_of(T) for T in grain_spectra(sample, stage_rad, p, a, analyzer_in, plate_m)])
        seeds = np.array([g['xy'] for g in sample['grains']])
        if kind == 'thin_section':
            idx = np.argmin((xs[..., None] - seeds[:, 0]) ** 2 + (ys[..., None] - seeds[:, 1]) ** 2, axis=-1)
            img = cols[idx]
        else:
            inside = x ** 2 + y ** 2 <= sample['grains'][0]['radius'] ** 2
            img[inside] = cols[0]
    elif kind == 'liquid_crystal':
        phi = np.zeros_like(x)
        for dft in sample['defects']:
            phi += dft['strength'] * np.arctan2(ys - dft['xy'][1], xs - dft['xy'][0])
        n1, n2, _ = section_indices(sample['substance_id'], orientation_matrix(math.pi / 2, 0.0))   # planar: director in the slide plane
        gamma = 2 * math.pi * sample['thickness_m'] * (n1 - n2) / LAM_M
        grid = np.linspace(0, math.pi, 181)
        lut = np.array([rgb_of(transmission(p, a, analyzer_in, gamma, t, plate_m)) for t in grid])   # slow axis = director (positive LC)
        k = np.round(np.mod(phi + stage_rad, math.pi) / math.pi * 180).astype(int)
        img = lut[k]
    img[x ** 2 + y ** 2 > 1.0] = 0.0                                  # circular field stop (field of view FN / M)
    return img


def expose(lin, level, exposure_s, gain, na_obj, rng):
    """Camera counts (H, W, 3) from a linear image: photo-electrons = level x LED rate x QE x exposure x (NA / 0.30)^2, Poisson + read noise,
    full-well clipping, 12-bit ADC; returns counts and the saturated fraction."""
    qe = np.asarray(CAMERA['qe'])
    e = np.clip(lin, 0, None) * level * LED_PHOTONS_PER_S * exposure_s * (na_obj / 0.30) ** 2 * qe / qe.max()
    e = rng.poisson(np.minimum(e, 1e9)).astype(float) + rng.normal(0.0, CAMERA['read_noise_e'], e.shape)
    e = np.clip(e, 0, CAMERA['full_well_e'])
    counts = np.clip(np.round(e * gain * (2 ** CAMERA['bits'] - 1) / CAMERA['full_well_e']), 0, 2 ** CAMERA['bits'] - 1)
    return counts.astype(int), float(np.mean(counts >= 2 ** CAMERA['bits'] - 1))


# ---------------------------------------------------------------- further engineering values (not published by the manufacturer)
SLUG = 'evident_bx53p'
INSTANCE = 'bx53p_01'
PX_CAMERA_M = 3.45e-6                                  # camera pixel pitch (generic CMOS, C-mount 1x adapter)
LAMBDA_REF_M = 550e-9                                  # reference wavelength for resolution / depth of field
FOCUS_REF_M = 0.0                                      # stage focus position that puts the stage top in the focal plane (all objectives parfocal);
                                                       # a specimen on a slide of thickness t is in focus at FOCUS_REF_M - t (cover glass neglected)
ANALYZER_OUT_M, PLATE_IN_M, BERTRAND_IN_M, TOP_LENS_OUT_RAD = -0.035, 0.030, 0.010, 1.4   # scene joint positions of the sliders (engineering)
IRIS_REF_M, IRIS_RAD_PER_M = 0.012, 1.0 / 0.019       # iris lever angle = (diameter - 12 mm) / 19 mm rad
BERTRAND_BLUR_PX_PER_RAD = 3.0                         # conoscopic image blur per rad of Bertrand focus knob from its sharp position (engineering)
COARSE_M_PER_TURN, FINE_M_PER_TURN = 36.8e-3 / 2, 0.1e-3   # focus drive per knob turn (engineering; BX-series style values, not in the cached sources)
RET_SCALE_MAX, RET_MIN_SEPARATION_M, RET_ALT_WINDOW = 1.35, 150e-9, 0.25   # retardation matching: chart brightness factor limit, alternative separation, window (engineering)
POLARIZER_ER = 1.0e-4                                  # extinction ratio of the crossed polarizer / analyzer pair (engineering)
NA_LEAK = 3.0e-3                                       # extra leakage x NA_ill^2 of crossed polars at high illumination aperture (engineering)
TOP_LENS_FIELD_M = 2.4e-3                              # illuminated field diameter at the sample with the condenser top lens in (engineering)
CLICK_CAPTURE_RAD = 1.0 * DEG                          # a stage angle within 1 deg of a 45 deg detent snaps into it (click stop)
ROTATE_S_PER_STEP = 2.0                                # manual stage rotation + settling per scan step (host / robot abstraction)
READOUT_S = 0.05                                       # camera readout time per frame
LED_W_MAX, IDLE_W = 20.0, 4.0                          # electrical power: electronics + LED (sums to the documented approx. 24 W at full level)
MAX_RECORDS, MAX_EVENTS, MAX_SAMPLES = 60, 400, 20
FAULTS = ['lamp_failure', 'camera_disconnected', 'vibration']
KINDS = ['thin_section', 'single_crystal', 'liquid_crystal', 'isotropic']


def fov_m(obj):
    return FIELD_NUMBER_M / obj['magnification']


def depth_of_field_m(obj):
    n = 1.515 if obj['immersion'] == 'oil' else 1.0
    return LAMBDA_REF_M * n / obj['na'] ** 2 + n * PX_CAMERA_M / (obj['magnification'] * obj['na'])


def resolution_m(obj):
    return 0.61 * LAMBDA_REF_M / obj['na']


def blur_px(obj, dz, pixels=CAMERA['pixels']):
    """Gaussian defocus blur (sigma in image pixels): geometric blur radius |dz| NA beyond half the depth of field, plus the diffraction spot."""
    px = fov_m(obj) / pixels[0]
    geo = max(0.0, abs(dz) - 0.5 * depth_of_field_m(obj)) * obj['na']
    return math.hypot(0.5 * geo, 0.42 * resolution_m(obj)) / px


def gaussian_blur(img, sigma):
    if sigma < 0.3:
        return img
    r = int(min(3 * sigma + 1, 40))
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    out = img
    for ax in (0, 1):
        pad = [(0, 0)] * img.ndim
        pad[ax] = (r, r)
        q = np.pad(out, pad, mode='edge')
        out = sum(k[i] * np.take(q, range(i, i + img.shape[ax]), axis=ax) for i in range(2 * r + 1))
    return out


def chart(R_m):
    """Michel-Levy colour (linear sRGB) of a dispersion-free retardation R between crossed polars at 45 deg under the LED."""
    return rgb_of(transmission(0.0, 90 * DEG, True, 2 * math.pi * R_m / LAM_M, 45 * DEG))


@functools.lru_cache(maxsize=1)
def chart_table():
    R = np.arange(0.0, 3000.1e-9, 5e-9)
    return R, np.array([chart(r) for r in R])


# ---------------------------------------------------------------- capabilities and state slots
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.-]+$')
LIBID = dict(type='string', minLength=1, maxLength=60)
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 278.15, 313.15), mains_voltage_v=number('V', 0.0, 264.0), noise_seed=integer(0, 2 ** 31 - 1)))
ORIENT = nullable(obj(dict(tilt_rad=number('rad', 0.0, math.pi), azimuth_rad=number('rad', 0.0, 2 * math.pi), spin_rad=number('rad', 0.0, 2 * math.pi))))
SAMPLE = obj(dict(sample_id=SID, kind=enum(KINDS), substance_id=nullable(LIBID), thickness_m=number('m', 1e-7, 1e-3), slide_thickness_m=number('m', 0.5e-3, 3.0e-3),
                  grains=integer(1, 200), defects=integer(0, 12), orientation=ORIENT, immersion_oil=BOOL))
SAMPLE_EXAMPLE = dict(sample_id='quartz_section', kind='thin_section', substance_id='main_sio2', thickness_m=30e-6, slide_thickness_m=1.0e-3, grains=40, defects=0,
                      orientation=None, immersion_oil=False)
OPERATION = obj(dict(operation_id=TEXT))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
W_, H_ = CAMERA['pixels']
REGION = nullable(obj(dict(x0=integer(0, W_ - 1), y0=integer(0, H_ - 1), x1=integer(1, W_), y1=integer(1, H_))))
ACQ = obj(dict(objective=TEXT, magnification=number('1'), na=number('1'), polarizer_rad=number('rad'), analyzer_inserted=BOOL, analyzer_rad=number('rad'),
               compensator=TEXT, stage_rad=number('rad'), condenser_top_lens=TEXT, iris_m=number('m'), illumination_level=number('1'), exposure_s=number('s'),
               gain=number('1'), stage_z_m=number('m'), fov_m=number('m'), pixel_m=number('m'), bertrand_inserted=BOOL))
RECORD = obj(dict(record_id=TEXT, kind=enum(['image', 'extinction_scan']), status=STATUS, started_s=number('s'), finished_s=nullable(number('s')),
                  sample_id=nullable(TEXT), acquisition=ACQ, warnings=array(TEXT, 0, 16),
                  image=nullable(obj(dict(width=INT, height=INT, bits=INT, counts=array(array(array(integer(0, 4095), 3, 3), 0, W_), 0, H_),
                                          mean_counts=array(number('1'), 3, 3), saturated_fraction=number('1')))),
                  scan=nullable(obj(dict(region=REGION, stage_rad=array(number('rad'), 0, 360), mean_counts=array(number('1'), 0, 360),
                                         extinction_rad=nullable(number('rad')), contrast=nullable(number('1')), residual=nullable(number('1'))))),
                  template_source=obj(dict(library=TEXT, substances=array(TEXT, 0, 4)))))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'optics', 'stage', 'sample', 'camera', 'scan', 'error', 'fault']), code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['electrical_energy_j', 'lamp_on_s', 'led_energy_j', 'frames', 'exposure_s', 'stage_rotation_rad', 'focus_travel_m']
LEDGER = obj({k: number('J' if k.endswith('_j') else 's' if k.endswith('_s') else 'rad' if k.endswith('_rad') else 'm' if k.endswith('_m') else '1') for k in LEDGER_KEYS})
RETARD_OUT = obj(dict(record_id=TEXT, region=REGION, retardation_m=nullable(number('m')), alternatives_m=array(number('m'), 0, 6), orientation_factor=nullable(number('1')),
                      residual=nullable(number('1')), birefringence=nullable(number('1')), candidates=array(obj(dict(substance_id=TEXT, name=TEXT, max_birefringence=number('1'))), 0, 80),
                      unique_identification={'type': 'boolean', 'enum': [False]}, warnings=array(enum(['saturated_region', 'dark_region', 'ambiguous_order']), 0, 3), note_zh=TEXT))   # never a unique phase

cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['frame_base'], '宿主声明实验室条件（室温、电源电压、噪声种子）', 'evaluator', 'simulation')
cap('sample.provide', SAMPLE, obj(dict(sample_id=TEXT, location=TEXT)), SAMPLE_EXAMPLE, ['slide_holder'],
    '在实验台上准备一片载玻片样品：岩石/晶体薄片（多晶粒）、单晶片、液晶盒或各向同性样品；物质取双折射模板库（真值只对评测者可见）', 'agent', 'robot_skill')
cap('sample.place', obj(dict(sample_id=SID)), obj(dict(sample_id=TEXT, slide_thickness_m=number('m'))), dict(sample_id='quartz_section'), ['stage', 'slide_holder'],
    '把载玻片放到旋转载物台上并用片夹固定（载玻片≤1.5 mm，聚光镜工作距离1.3 mm）', 'agent', 'robot_skill')
cap('sample.remove', obj(dict(sample_id=SID)), obj(dict(sample_id=TEXT)), dict(sample_id='quartz_section'), ['stage', 'slide_holder'], '取下载玻片放回实验台', 'agent', 'robot_skill')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['switch_power', 'frame_base'], '主电源开/关（LED照明；约24 W）')
cap('power.press_switch', obj({}), obj(dict(enabled=BOOL)), {}, ['switch_power'], '按一下主电源开关（翻转开/关）')
cap('illumination.set_level', obj(dict(level=number('1', 0.0, 1.0))), obj(dict(level=number('1'))), dict(level=0.6), ['knob_light', 'lamp_housing'], '光强旋钮：LED亮度0–1')
cap('polarizer.set_angle', obj(dict(angle_rad=number('rad', 0.0, 2 * math.pi))), obj(dict(angle_rad=number('rad'))), dict(angle_rad=0.0), ['polarizer_ring', 'condenser'],
    '转动起偏镜（360°；0°位有卡点，0°±1°内落入卡点）')
cap('analyzer.set_inserted', obj(dict(inserted=BOOL)), obj(dict(inserted=BOOL)), dict(inserted=True), ['analyzer_slider', 'cpa_attachment'], '检偏镜插板推入/拉出光路')
cap('analyzer.set_angle', obj(dict(angle_rad=number('rad', 0.0, 2 * math.pi))), obj(dict(angle_rad=number('rad'))), dict(angle_rad=math.pi / 2), ['analyzer_dial'],
    '转动检偏镜刻度盘（360°，读数0.1°）；与起偏镜成90°为正交偏光')
cap('compensator.insert', obj(dict(plate=enum(list(PLATES)))), obj(dict(plate=TEXT, retardation_m=number('m'))), dict(plate='U-TP530'), ['compensator_slider', 'nosepiece'],
    '在物镜转盘插槽插入/取出试板：U-TP530一级红（530 nm）或U-TP137 λ/4（137 nm），慢轴NE–SW（观察者视角；图像坐标中为+45°）')
cap('bertrand.set_inserted', obj(dict(inserted=BOOL)), obj(dict(inserted=BOOL)), dict(inserted=True), ['bertrand_slider', 'cpa_attachment'],
    'U-CPA勃氏镜推入（锥光观察：相机与目镜看到物镜后焦面的干涉图）或拉出（正交观察）')
cap('bertrand.focus', obj(dict(focus_rad=number('rad', -2 * math.pi, 2 * math.pi))), obj(dict(focus_rad=number('rad'))), dict(focus_rad=0.0),
    ['knob_bertrand_focus'], '勃氏镜调焦旋钮（0为清晰位；偏离时干涉图变模糊）')
cap('condenser.set_top_lens', obj(dict(state=enum(['in', 'out']))), obj(dict(state=TEXT, na=number('1'))), dict(state='in'), ['condenser_top_lens', 'condenser'],
    '聚光镜上透镜摆入（NA 0.9，10×以上）或摆出（NA 0.18，4×）')
cap('condenser.set_iris', obj(dict(diameter_m=number('m', IRIS_RANGE_M[0], IRIS_RANGE_M[1]))), obj(dict(diameter_m=number('m'), illumination_na=number('1'))),
    dict(diameter_m=12e-3), ['condenser_iris_lever', 'condenser'], '孔径光阑直径2–21 mm（照明数值孔径与对比度）')
cap('objective.select', obj(dict(index=integer(0, 3))), obj(dict(objective=TEXT, magnification=number('1'), na=number('1'), fov_m=number('m'))), dict(index=1),
    ['nosepiece', 'obj_4x', 'obj_10x', 'obj_40x', 'obj_100x'], '转动四孔物镜转盘选物镜：0=UPLFLN 4XP、1=10XP、2=40XP、3=100XOP（油镜）')
cap('stage.rotate', obj(dict(angle_rad=number('rad', 0.0, 2 * math.pi))), obj(dict(angle_rad=number('rad'), indicated_rad=number('rad'), click_stop=BOOL)),
    dict(angle_rad=math.pi / 4), ['stage'], '转动载物台到给定角度（360°，1°刻度加6′游标读数；距45°卡点1°内落入卡点）')
cap('focus.move', obj(dict(stage_z_m=number('m', FOCUS_RANGE_M[0], FOCUS_RANGE_M[1]))), obj(dict(stage_z_m=number('m'))), dict(stage_z_m=0.0),
    ['stage_carrier', 'knob_coarse_focus', 'fine_focus_wheel'], '粗/细调焦旋钮把载物台升降到给定高度（载物台调焦）')
cap('camera.configure', obj(dict(exposure_s=number('s', CAMERA['exposure_s'][0], CAMERA['exposure_s'][1]), gain=number('1', 1.0, 16.0))),
    obj(dict(exposure_s=number('s'), gain=number('1'))), dict(exposure_s=0.05, gain=1.0), ['camera_port'], '三目接口相机的曝光时间与增益（相机为通用彩色CMOS，工程模型）')
cap('camera.acquire', obj({}), OPERATION, {}, ['camera_port', 'trinocular_tube'], '拍一帧彩色偏光图像（曝光+读出后写入image记录）')
cap('scan.extinction', obj(dict(step_rad=number('rad', 1 * DEG, 40 * DEG), region=REGION)), OPERATION, dict(step_rad=10 * DEG, region=None), ['stage', 'camera_port'],
    '消光扫描：载物台0–180°逐步转动并拍照，拟合区域平均强度I=a−b·cos4(θ−θe)得到消光位θe与衬度（结束后载物台回到起始角）')
cap('analysis.retardation', obj(dict(record_id=TEXT, region=REGION, thickness_m=nullable(number('m', 1e-7, 1e-3)))), RETARD_OUT,
    dict(record_id='IM0001', region=None, thickness_m=30e-6), ['camera_port'],
    '按Michel-Lévy色图从正交偏光图像的区域颜色估计程差（多级重合时给出备选），给定厚度时换算双折射并列出模板库中可能的物质（不做唯一物相判定）', 'agent', 'analysis')
cap('measurement.result', obj(dict(record_id=TEXT)), RECORD, dict(record_id='IM0001'), ['camera_port'], '读取记录（图像计数、采集设置、扫描曲线）', 'agent', 'query')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, MAX_RECORDS))), {}, ['camera_port'], '列出记录', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), ['frame_base'], '读事件日志', 'agent', 'query')
cap('ledger.read', obj({}), LEDGER, {}, ['frame_base'], '能量与使用账本（电能、LED开灯时间、帧数、曝光、载物台转角、调焦行程）', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, library_sha256=TEXT, substances=INT, license=TEXT)), {}, ['frame_base'], '模板库来源', 'agent', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 4))), dict(fault='lamp_failure', active=True), ['frame_base'],
    '评测者注入故障（LED损坏、相机断开、台面振动）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['frame_base'], '宿主推进仿真时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'hardware.connect': '没有真实BX53-P与相机的硬件/软件接口',
    'robot.handle_slide': '机器人接触放片（抓取、夹持）未建模；sample.place为宿主/机器人抽象',
    'hot_stage.texture_evolution': '纹理随温度演变需要热台附件（BX53-P标准配置没有热台）',
    'compensator.berek': 'Berek/Senarmont/Brace-Köhler补偿器（U-CTB、U-CBE、U-CSE、U-CBR1）不在本配置',
    'reflected_light.observe': '反射光/荧光/微分干涉照明不在本配置（透射偏光显微镜）',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['frame_base'], _why, 'agent', 'device_command')

SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference or SLOT_REFERENCE.get(k))


SLOT_REFERENCE = {'polarizer.angle_rad': '振动方向与晶体方位按图像坐标：+x向右（列号增大，资产+x），+y向上（行号减小，资产+y即远离操作者），角度从+x转向+y为正即从上往下看逆时针，与载物台、起偏镜关节绕资产+z的正向一致（载物台未转时载玻片坐标与图像坐标重合）；起偏镜0°卡点=东西向振动', 'analyzer.angle_rad': '振动方向与晶体方位按图像坐标：+x向右（列号增大，资产+x），+y向上（行号减小，资产+y即远离操作者），角度从+x转向+y为正即从上往下看逆时针，与载物台、起偏镜关节绕资产+z的正向一致（载物台未转时载玻片坐标与图像坐标重合）；90°=南北向振动（与0°起偏镜正交）', 'stage.angle_rad': '振动方向与晶体方位按图像坐标：+x向右（列号增大，资产+x），+y向上（行号减小，资产+y即远离操作者），角度从+x转向+y为正即从上往下看逆时针，与载物台、起偏镜关节绕资产+z的正向一致（载物台未转时载玻片坐标与图像坐标重合）；载物台转角同向为正（场景载物台关节绕资产+z轴，0为刻度0°；图像与关节方向的对应为工程约定）', 'stage.indicated_rad': '振动方向与晶体方位按图像坐标：+x向右（列号增大，资产+x），+y向上（行号减小，资产+y即远离操作者），角度从+x转向+y为正即从上往下看逆时针，与载物台、起偏镜关节绕资产+z的正向一致（载物台未转时载玻片坐标与图像坐标重合）；刻度读数同stage.angle_rad', 'analyzer.dial_rad': '刻度盘关节角=analyzer.angle_rad−π/2（场景关节轴+x）', 'objective.nosepiece_rad': '转盘关节角=档位×π/2，绕倾斜15°的转盘轴（资产坐标(0,−sin15°,cos15°)）', 'focus.stage_z_m': '相对参考高度FOCUS_REF_M=0（台面在焦面上），+z向上', 'focus.coarse_turns_rad': '粗调旋钮关节角=focus.stage_z_m/每圈18.4 mm×2π（场景关节轴+x）', 'illumination.knob_rad': '光强旋钮关节角=亮度×1.5π（场景关节轴−y）', 'condenser.top_lens_rad': '上透镜摆臂关节角：入0、出1.4 rad（绕+z）', 'condenser.iris_lever_rad': '孔径光阑拨杆关节角=(直径−12 mm)/19 mm rad（绕+z）', 'compensator.slider_m': '试板插板沿−x推入：0空槽，0.030 m插入', 'analyzer.slider_m': '检偏镜插板沿+x：0插入，−0.035 m拉出', 'bertrand.slider_m': '勃氏镜推拉钮沿+y：0拉出，0.010 m推入', 'bertrand.focus_rad': '勃氏镜调焦旋钮关节角，0为清晰位（绕+x）'}   # reference frames of angles and positions (contract reference_conditions)


slot('clock.time_s', number('s'), 's', 'derived', 'frame_base', '仿真时钟')
slot('plant.configured', BOOL, '1', 'setpoint', 'frame_base', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'setpoint', 'switch_power', '主电源')
slot('illumination.level', number('1'), '1', 'setpoint', 'knob_light', '光强旋钮设定（0–1）')
slot('illumination.knob_rad', number('rad'), 'rad', 'derived', 'knob_light', '光强旋钮转角（=设定×270°；场景旋钮关节镜像）')
slot('illumination.lamp_on', BOOL, '1', 'derived', 'lamp_housing', 'LED发光（通电、亮度>0且LED无故障）')
slot('polarizer.angle_rad', number('rad'), 'rad', 'setpoint', 'polarizer_ring', '起偏镜振动方向（从+x即东西方向起算）')
slot('analyzer.inserted', BOOL, '1', 'setpoint', 'analyzer_slider', '检偏镜在光路中')
slot('analyzer.angle_rad', number('rad'), 'rad', 'setpoint', 'analyzer_dial', '检偏镜振动方向（0.1°刻度）')
slot('optics.crossed', BOOL, '1', 'derived', 'analyzer_dial', '正交偏光（检偏镜插入且与起偏镜成90°±0.5°）')
slot('compensator.plate', enum(list(PLATES)), '1', 'setpoint', 'compensator_slider', '插槽中的试板')
slot('compensator.slider_m', number('m'), 'm', 'derived', 'compensator_slider', '试板插板位置（插入0.030 m，空槽0；场景插板关节镜像）')
slot('analyzer.slider_m', number('m'), 'm', 'derived', 'analyzer_slider', '检偏镜插板位置（插入0，拉出−0.035 m；场景插板关节镜像）')
slot('analyzer.dial_rad', number('rad'), 'rad', 'derived', 'analyzer_dial', '检偏镜刻度盘转角（=检偏镜角度−90°，范围−90°…270°；场景刻度盘关节镜像）')
slot('bertrand.inserted', BOOL, '1', 'setpoint', 'bertrand_slider', '勃氏镜在光路中（锥光观察）')
slot('bertrand.slider_m', number('m'), 'm', 'derived', 'bertrand_slider', '勃氏镜推拉钮位置（推入0.010 m；场景关节镜像）')
slot('bertrand.focus_rad', number('rad'), 'rad', 'setpoint', 'knob_bertrand_focus', '勃氏镜调焦旋钮转角（0为清晰位，工程）')
slot('condenser.top_lens', enum(['in', 'out']), '1', 'setpoint', 'condenser_top_lens', '聚光镜上透镜位置')
slot('condenser.iris_m', number('m'), 'm', 'setpoint', 'condenser_iris_lever', '孔径光阑直径')
slot('condenser.top_lens_rad', number('rad'), 'rad', 'derived', 'condenser_top_lens', '上透镜摆臂转角（入0，出1.4 rad；场景关节镜像）')
slot('condenser.iris_lever_rad', number('rad'), 'rad', 'derived', 'condenser_iris_lever', '孔径光阑拨杆转角（=(直径−12 mm)/19 mm rad；场景关节镜像）')
slot('condenser.illumination_na', number('1'), '1', 'derived', 'condenser', '照明数值孔径（聚光镜NA×光阑/21 mm）')
slot('objective.index', integer(0, 3), '1', 'setpoint', 'nosepiece', '光轴上的物镜档位')
slot('objective.name', TEXT, '1', 'derived', 'nosepiece', '光轴上的物镜')
slot('objective.magnification', number('1'), '1', 'derived', 'nosepiece', '物镜倍率')
slot('objective.na', number('1'), '1', 'derived', 'nosepiece', '物镜数值孔径')
slot('objective.nosepiece_rad', number('rad'), 'rad', 'derived', 'nosepiece', '物镜转盘转角（=档位×90°；场景转盘关节镜像）')
slot('optics.fov_m', number('m'), 'm', 'derived', 'trinocular_tube', '视场直径（FN 22/物镜倍率）')
slot('stage.angle_rad', number('rad'), 'rad', 'setpoint', 'stage', '载物台转角（过程状态；场景载物台关节镜像）')
slot('stage.indicated_rad', number('rad'), 'rad', 'sensor', 'stage', '载物台刻度读数（1°刻度+6′游标，读到0.1°=1.745 mrad；以rad给出）')
slot('stage.click_stop', BOOL, '1', 'derived', 'stage', '载物台落在45°卡点')
slot('focus.stage_z_m', number('m'), 'm', 'setpoint', 'stage_carrier', '载物台调焦高度（场景支架关节镜像）')
slot('focus.coarse_turns_rad', number('rad'), 'rad', 'derived', 'knob_coarse_focus', '粗调焦旋钮转角（=高度/每圈行程×2π；场景旋钮关节镜像）')
slot('camera.exposure_s', number('s'), 's', 'setpoint', 'camera_port', '相机曝光时间')
slot('camera.gain', number('1'), '1', 'setpoint', 'camera_port', '相机增益')
slot('operation.kind', nullable(TEXT), '1', 'derived', 'camera_port', '进行中的操作（拍照/消光扫描）')
slot('operation.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'camera_port', '进行中操作的进度')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'camera_port', '进行中的操作属于另一个agent')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'camera_port', '最近完成的记录')
slot('sample.bench_ids', array(TEXT, 0, MAX_SAMPLES), '1', 'derived', 'slide_holder', '实验台上的载玻片')
slot('sample.on_stage', nullable(TEXT), '1', 'derived', 'slide_holder', '载物台上的载玻片')
slot('panel.lamp_glow', BOOL, '1', 'derived', 'field_lens', '出光窗发光（LED亮时）')
slot('truth.samples', {'type': 'object'}, '1', 'process_truth', 'slide_holder', '样品真值（物质、厚度、晶粒取向、液晶缺陷）', 'evaluator')
slot('truth.focus_error_m', nullable(number('m')), 'm', 'process_truth', 'stage_carrier', '载物台高度减对焦高度', 'evaluator')
slot('truth.center_retardation_m', nullable(number('m')), 'm', 'process_truth', 'slide_holder', '视场中心像素处样品在550 nm的真实程差', 'evaluator')
slot('truth.center_slow_axis_rad', nullable(number('rad')), 'rad', 'process_truth', 'slide_holder', '视场中心晶粒慢轴在图像坐标中的真实方位（=晶体方位+载物台转角，mod π）', 'evaluator',
     reference='图像坐标（+x向右、+y向上即远离操作者，逆时针为正），已含载物台转角')
slot('truth.faults', array(TEXT, 0, 4), '1', 'process_truth', 'frame_base', '注入的故障', 'evaluator')


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


def _wrap(a):
    return float(np.mod(a, 2 * math.pi))


class EvidentBx53pRuntime:
    """BX53-P orthoscopic polarizing microscope with a camera on the trinocular port: power and LED, polarizer, analyzer, plates, condenser,
    nosepiece, rotatable stage, stage focus, image acquisition and extinction scans, retardation analysis (SI units, angles in rad)."""

    def __init__(self, instance_id=INSTANCE):
        self.instance_id = instance_id
        self.t = 0.0
        self.revision = 0
        self.plant = None
        self.power = False
        self.level = 0.5
        self.pol = 0.0
        self.an_in = True
        self.an = math.pi / 2
        self.plate = 'none'
        self.bert, self.bert_focus = False, 0.0
        self.top = 'in'
        self.iris = 12e-3
        self.obj_i = 1
        self.stage = 0.0
        self.z = -5e-3
        self.exposure, self.gain = 0.05, 1.0
        self.samples = {}
        self.on_stage = None
        self.records = {}
        self.seq = dict(im=0, sc=0, op=0)
        self.op = None
        self.operations = {}
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.led = {k: _Sum() for k in LEDGER_KEYS}
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
        return EvidentBx53pSession(self, name)

    def _event(self, kind, code, message, rid=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _obj(self):
        return OBJECTIVES[self.obj_i]

    def _lamp_on(self):
        return self.power and self.level > 0 and 'lamp_failure' not in self.faults

    def _ill_na(self):
        return CONDENSER_NA[self.top] * self.iris / IRIS_RANGE_M[1]

    def _crossed(self):
        d = abs(((self.an - self.pol) % math.pi) - math.pi / 2)
        return self.an_in and d <= 0.5 * DEG

    def _focus_target(self, s):
        return FOCUS_REF_M - s['slide_thickness_m']

    def _front_gap(self, z=None, obj_i=None, sample_id='current'):
        """Clearance between the objective front lens (working distance above the focal plane) and the top of the specimen on its slide (or the stage);
        the cover glass is neglected."""
        z = self.z if z is None else z
        ob = OBJECTIVES[self.obj_i if obj_i is None else obj_i]
        sid = self.on_stage if sample_id == 'current' else sample_id
        top = z - FOCUS_REF_M + (self.samples[sid]['slide_thickness_m'] + self.samples[sid]['truth']['thickness_m'] if sid is not None else 0.0)   # slide + specimen
        return ob['working_distance_m'] - top

    def _center_truth(self):
        if self.on_stage is None:
            return None, None
        s = self.samples[self.on_stage]
        tr = s['truth']
        if tr['kind'] in ('thin_section', 'single_crystal') and tr['grains']:
            g = min(tr['grains'], key=lambda g: g['xy'][0] ** 2 + g['xy'][1] ** 2)
            n1, n2, az = section_indices(tr['substance_id'], orientation_matrix(g['tilt'], g['azimuth'], g['spin']))
            R = float(np.interp(LAMBDA_REF_M, LAM_M, tr['thickness_m'] * (n1 - n2)))
            return R, float(np.mod(az, math.pi))
        if tr['kind'] == 'liquid_crystal':
            n1, n2, _ = section_indices(tr['substance_id'], orientation_matrix(math.pi / 2, 0.0))
            phi = sum(d['strength'] * math.atan2(-d['xy'][1], -d['xy'][0]) for d in tr['defects'])
            return float(np.interp(LAMBDA_REF_M, LAM_M, tr['thickness_m'] * (n1 - n2))), float(np.mod(phi, math.pi))
        return 0.0, None

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        o = self.op
        prog = None
        if o is not None:
            prog = min(1.0, max(0.0, (self.t - o['t0']) / o['duration_s'])) if o['duration_s'] > 0 else 0.0
        ob = self._obj()
        R, az = self._center_truth()
        fe = self.z - self._focus_target(self.samples[self.on_stage]) if self.on_stage else None
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'illumination.level': self.level,
            'illumination.knob_rad': self.level * 1.5 * math.pi, 'illumination.lamp_on': self._lamp_on(), 'polarizer.angle_rad': self.pol,
            'analyzer.inserted': self.an_in, 'analyzer.angle_rad': self.an, 'optics.crossed': self._crossed(), 'compensator.plate': self.plate,
            'analyzer.slider_m': 0.0 if self.an_in else ANALYZER_OUT_M, 'analyzer.dial_rad': float(np.mod(self.an - math.pi / 2 + math.pi / 2, 2 * math.pi) - math.pi / 2),
            'compensator.slider_m': PLATE_IN_M if self.plate != 'none' else 0.0, 'condenser.top_lens_rad': 0.0 if self.top == 'in' else TOP_LENS_OUT_RAD,
            'condenser.iris_lever_rad': (self.iris - IRIS_REF_M) * IRIS_RAD_PER_M, 'bertrand.inserted': self.bert, 'bertrand.slider_m': BERTRAND_IN_M if self.bert else 0.0,
            'bertrand.focus_rad': self.bert_focus,
            'condenser.top_lens': self.top, 'condenser.iris_m': self.iris, 'condenser.illumination_na': self._ill_na(), 'objective.index': self.obj_i,
            'objective.name': ob['name'], 'objective.magnification': ob['magnification'], 'objective.na': ob['na'], 'objective.nosepiece_rad': self.obj_i * math.pi / 2,
            'optics.fov_m': fov_m(ob), 'stage.angle_rad': self.stage, 'stage.indicated_rad': self._indicated(self.stage),
            'stage.click_stop': abs(((self.stage + STAGE_CLICK_RAD / 2) % STAGE_CLICK_RAD) - STAGE_CLICK_RAD / 2) < 1e-9,
            'focus.stage_z_m': self.z, 'focus.coarse_turns_rad': self.z / COARSE_M_PER_TURN * 2 * math.pi, 'camera.exposure_s': self.exposure, 'camera.gain': self.gain,
            'operation.kind': o['kind'] if o else None, 'operation.progress': prog, 'operation.started_by_other_agent': False,
            'result.last_record_id': self._last_record(), 'sample.bench_ids': sorted(k for k, s in self.samples.items() if s['location'] == 'bench'),
            'sample.on_stage': self.on_stage, 'panel.lamp_glow': self._lamp_on(),
            'truth.samples': {k: copy.deepcopy(s['truth']) for k, s in self.samples.items()}, 'truth.focus_error_m': fe, 'truth.center_retardation_m': R,
            'truth.center_slow_axis_rad': None if az is None else float(np.mod(az + self.stage, math.pi)), 'truth.faults': sorted(self.faults),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _last_record(self, keep=None):
        done = [k for k, r in self.records.items() if r['status'] == 'completed' and (keep is None or keep(k))]
        return max(done, key=lambda k: (self.records[k]['finished_s'] or 0.0, k), default=None)

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        props = CAPS[capability_id]['input_schema'].get('properties', {})
        a = {k: (int(v) if props.get(k, {}).get('type') == 'integer' and v is not None else v) for k, v in a.items()}
        out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):
            self.revision += 1
        return out

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_idle(self):
        self._need_plant()
        if self.op is not None:
            raise ActionRejected('OPERATION_RUNNING')

    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed):
        if self.plant is not None and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        self.plant = dict(room_k=room_k, mains_voltage_v=mains_voltage_v, noise_seed=noise_seed)
        if self.power and not (100.0 * 0.9 <= mains_voltage_v <= 240.0 * 1.1):
            self.power = False
            self._event('power', 'MAINS_FAULT', '供电电压超出范围，电源断开')
        return dict(plant=dict(self.plant))

    def _rng(self, tag):
        h = hashlib.sha256(f'{self.plant["noise_seed"] if self.plant else 0}:{tag}'.encode()).digest()
        return np.random.default_rng(int.from_bytes(h[:8], 'little'))

    def _cmd_sample_provide(self, sample_id, kind, substance_id, thickness_m, slide_thickness_m, grains, defects, orientation, immersion_oil):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('SAMPLE_EXISTS')
        if len(self.samples) >= MAX_SAMPLES:
            raise ActionRejected('TOO_MANY_SAMPLES')
        lib = biref_lib()['substances']
        if kind != 'isotropic' and substance_id not in lib:
            raise ActionRejected('UNKNOWN_SUBSTANCE')
        if kind == 'liquid_crystal' and substance_id is not None and lib[substance_id]['optical_class'] != 'uniaxial':
            raise ActionRejected('NOT_A_LIQUID_CRYSTAL')
        spec = dict(kind=kind, substance_id=substance_id, thickness_m=thickness_m, grains=grains, defects=defects,
                    orientation=None if orientation is None else dict(tilt=orientation['tilt_rad'], azimuth=orientation['azimuth_rad'], spin=orientation['spin_rad']))
        tr = make_sample(spec, self._rng('sample:' + sample_id)) if kind != 'isotropic' else dict(kind=kind, substance_id=None, thickness_m=thickness_m, grains=[], defects=[])
        self.samples[sample_id] = dict(location='bench', slide_thickness_m=slide_thickness_m, immersion_oil=immersion_oil, truth=tr)
        self._event('sample', 'SAMPLE_PROVIDED', f'样品{sample_id}放到实验台上')
        return dict(sample_id=sample_id, location='bench')

    def _cmd_sample_place(self, sample_id):
        self._need_idle()
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        if self.on_stage is not None:
            raise ActionRejected('STAGE_OCCUPIED')
        if s['slide_thickness_m'] > SLIDE_MAX_M:
            raise ActionRejected('SLIDE_TOO_THICK')
        if self._front_gap(sample_id=sample_id) < 0:
            raise ActionRejected('OBJECTIVE_COLLISION')
        s['location'] = 'stage'
        self.on_stage = sample_id
        self._event('sample', 'SAMPLE_PLACED', f'载玻片{sample_id}放上载物台')
        return dict(sample_id=sample_id, slide_thickness_m=s['slide_thickness_m'])

    def _cmd_sample_remove(self, sample_id):
        self._need_idle()
        if self.on_stage != sample_id:
            raise ActionRejected('SAMPLE_NOT_ON_STAGE')
        self.samples[sample_id]['location'] = 'bench'
        self.on_stage = None
        self._event('sample', 'SAMPLE_REMOVED', f'载玻片{sample_id}取下')
        return dict(sample_id=sample_id)

    def _set_power(self, enabled):
        self._need_plant()
        if enabled and not (100.0 * 0.9 <= self.plant['mains_voltage_v'] <= 240.0 * 1.1):
            raise ActionRejected('MAINS_OUT_OF_RANGE')
        if enabled != self.power:
            self.power = enabled
            self._event('power', 'POWER_ON' if enabled else 'POWER_OFF', '主电源' + ('打开' if enabled else '关闭'))
        return dict(enabled=self.power)

    def _cmd_power_set(self, enabled):
        return self._set_power(enabled)

    def _cmd_power_press_switch(self):
        return self._set_power(not self.power)

    def _cmd_illumination_set_level(self, level):
        self._need_plant()
        self.level = level
        return dict(level=level)

    def _cmd_polarizer_set_angle(self, angle_rad):
        self._need_idle()
        a = _wrap(angle_rad)
        if min(a, 2 * math.pi - a) <= CLICK_CAPTURE_RAD:            # 0 deg click stop
            a = 0.0
        self.pol = a
        return dict(angle_rad=a)

    def _cmd_analyzer_set_inserted(self, inserted):
        self._need_idle()
        self.an_in = inserted
        return dict(inserted=inserted)

    def _cmd_analyzer_set_angle(self, angle_rad):
        self._need_idle()
        a = _wrap(round(angle_rad / ANALYZER_STEP_RAD) * ANALYZER_STEP_RAD)
        self.an = a
        return dict(angle_rad=a)

    def _cmd_compensator_insert(self, plate):
        self._need_idle()
        self.plate = plate
        return dict(plate=plate, retardation_m=PLATES[plate])

    def _cmd_bertrand_set_inserted(self, inserted):
        self._need_idle()
        self.bert = inserted
        return dict(inserted=inserted)

    def _cmd_bertrand_focus(self, focus_rad):
        self._need_idle()
        self.bert_focus = focus_rad
        return dict(focus_rad=focus_rad)

    def _cmd_condenser_set_top_lens(self, state):
        self._need_idle()
        self.top = state
        return dict(state=state, na=CONDENSER_NA[state])

    def _cmd_condenser_set_iris(self, diameter_m):
        self._need_idle()
        self.iris = diameter_m
        return dict(diameter_m=diameter_m, illumination_na=self._ill_na())

    def _cmd_objective_select(self, index):
        self._need_idle()
        if self._front_gap(obj_i=index) < 0:
            raise ActionRejected('OBJECTIVE_COLLISION')
        self.obj_i = index
        ob = self._obj()
        return dict(objective=ob['name'], magnification=ob['magnification'], na=ob['na'], fov_m=fov_m(ob))

    def _snap_stage(self, a):
        a = _wrap(a)
        k = round(a / STAGE_CLICK_RAD)
        if abs(a - k * STAGE_CLICK_RAD) <= CLICK_CAPTURE_RAD:
            a = _wrap(k * STAGE_CLICK_RAD)
            return a, True
        return a, False

    def _cmd_stage_rotate(self, angle_rad):
        self._need_idle()
        a, click = self._snap_stage(angle_rad)
        d = abs((a - self.stage + math.pi) % (2 * math.pi) - math.pi)
        self.led['stage_rotation_rad'].add(d)
        self.stage = a
        return dict(angle_rad=a, indicated_rad=self._indicated(a), click_stop=click)

    @staticmethod
    def _indicated(a):
        return round(a / STAGE_VERNIER_RAD) * STAGE_VERNIER_RAD                  # the vernier reads to 0.1 deg

    def _cmd_focus_move(self, stage_z_m):
        self._need_idle()
        if self._front_gap(z=stage_z_m) < 0:
            raise ActionRejected('OBJECTIVE_COLLISION')
        self.led['focus_travel_m'].add(abs(stage_z_m - self.z))
        self.z = stage_z_m
        return dict(stage_z_m=stage_z_m)

    def _cmd_camera_configure(self, exposure_s, gain):
        self._need_idle()
        self.exposure, self.gain = exposure_s, gain
        return dict(exposure_s=exposure_s, gain=gain)

    # ---------------------------------------------------------------- imaging
    def _acquisition(self):
        ob = self._obj()
        return dict(objective=ob['name'], magnification=ob['magnification'], na=ob['na'], polarizer_rad=self.pol, analyzer_inserted=self.an_in, analyzer_rad=self.an,
                    compensator=self.plate, stage_rad=self.stage, condenser_top_lens=self.top, iris_m=self.iris, illumination_level=self.level,
                    exposure_s=self.exposure, gain=self.gain, stage_z_m=self.z, fov_m=fov_m(ob), pixel_m=fov_m(ob) / W_, bertrand_inserted=self.bert)

    def _linear_image(self, stage_rad, warnings):
        """Noise-free linear sRGB image at the sensor (before exposure scaling) for the current optics and a given stage angle."""
        ob = self._obj()
        plate = PLATES[self.plate]
        if self.bert:
            return self._conoscopic(ob, stage_rad, plate, warnings)
        if self.on_stage is None:
            lin = np.broadcast_to(rgb_of(transmission(self.pol, self.an, self.an_in, np.zeros(len(LAM_M)), 0.0, plate)), (H_, W_, 3)).copy()
            yy, xx = np.mgrid[0:H_, 0:W_]
            x, y = (xx - (W_ - 1) / 2) / ((W_ - 1) / 2), (yy - (H_ - 1) / 2) / ((W_ - 1) / 2)
            lin[x ** 2 + y ** 2 > 1] = 0.0
        else:
            s = self.samples[self.on_stage]
            lin = render_linear(s['truth'], stage_rad, self.pol, self.an, self.an_in, plate, pixels=(W_, H_)) if s['truth']['kind'] != 'isotropic' else \
                render_linear(dict(kind='isotropic', grains=[], defects=[]), stage_rad, self.pol, self.an, self.an_in, plate, pixels=(W_, H_))
            dz = self.z - self._focus_target(s)
            sig = blur_px(ob, dz)
            if ob['immersion'] == 'oil' and not s['immersion_oil']:
                warnings.append('no_immersion_oil')
                sig = max(sig, 3.0 * blur_px(ob, 0.0))
                lin = lin * (1 / 1.515) ** 2
            if 'vibration' in self.faults:
                sig = math.hypot(sig, 1.5)
            lin = gaussian_blur(lin, sig)
        white = rgb_of(np.full(len(LAM_M), 0.5))
        leak = (POLARIZER_ER + NA_LEAK * self._ill_na() ** 2) * white if self.an_in else 0.0      # imperfect extinction of crossed polars
        lin = lin + leak
        if self.top == 'in' and ob['magnification'] < 10:                       # the top lens illuminates only ~2.4 mm: vignetting at 4x
            yy, xx = np.mgrid[0:H_, 0:W_]
            rr = np.hypot(xx - (W_ - 1) / 2, yy - (H_ - 1) / 2) / ((W_ - 1) / 2) * fov_m(ob) / 2
            lin = lin * np.clip(1.0 - (rr - TOP_LENS_FIELD_M / 2) / 0.4e-3, 0.0, 1.0)[..., None]
            warnings.append('field_not_fully_illuminated')
        if self.top == 'out' and ob['magnification'] >= 10:
            warnings.append('condenser_na_low')
        bright = min(1.0, (self._ill_na() / ob['na']) ** 2) * (ob['na'] / 0.30) ** 2 / max(1e-9, (ob['magnification'] / 10.0) ** 2)   # NA^2 / M^2 relative to 10x
        return lin * bright

    def _conoscopic(self, ob, stage_rad, plate, warnings):
        """Back-focal-plane image (Bertrand lens in): every pixel is a propagation direction, sin(theta) = NA_obj r, inside the illumination
        aperture; the grain under the 3.4 mm Bertrand field stop (the centre grain) gives the interference figure."""
        warnings.append('conoscopic')
        yy, xx = np.mgrid[0:H_, 0:W_]
        u, v = (xx - (W_ - 1) / 2) / ((W_ - 1) / 2), -(yy - (H_ - 1) / 2) / ((W_ - 1) / 2)  # same image frame as the orthoscopic image (+y up)
        r = np.hypot(u, v)
        inside = r <= 1.0
        s_na = ob['na'] * r                                             # n sin(theta) is conserved through the layers: sin(theta_c) = NA r / n_crystal
        phi = np.arctan2(v, u)
        lit = inside & (r * ob['na'] <= self._ill_na() + 1e-9)
        tr_ = self.samples[self.on_stage] if self.on_stage else None
        if ob['immersion'] == 'oil' and tr_ is not None and not tr_['immersion_oil']:
            warnings.append('no_immersion_oil')
            lit &= r * ob['na'] < 1.0                                    # through air no direction beyond n sin(theta) = 1 reaches the objective
        if self._ill_na() < 0.6 * ob['na']:
            warnings.append('illumination_aperture_small')
        tr = self.samples[self.on_stage]['truth'] if self.on_stage else None
        N = H_ * W_
        white_T = transmission(self.pol, self.an, self.an_in, np.zeros(len(LAM_M)), 0.0, plate)
        if tr is None or tr['kind'] == 'isotropic' or not (tr['grains'] or tr['kind'] == 'liquid_crystal'):
            lin = np.broadcast_to(rgb_of(white_T), (H_, W_, 3)).copy()
        else:
            if tr['kind'] == 'liquid_crystal':
                Rg = orientation_matrix(math.pi / 2, 0.0)
            else:
                g = min(tr['grains'], key=lambda g: g['xy'][0] ** 2 + g['xy'][1] ** 2)
                Rg = orientation_matrix(g['tilt'], g['azimuth'], g['spin'])
            c, s_ = math.cos(stage_rad), math.sin(stage_rad)
            Rs = np.array([[c, -s_, 0.0], [s_, c, 0.0], [0.0, 0.0, 1.0]]) @ Rg
            kind, nidx = principal_indices(tr['substance_id'])
            if kind == 'uniaxial':
                nidx = [nidx[0], nidx[0], nidx[1]]
            n_mean = float(np.mean([np.interp(LAMBDA_REF_M, LAM_M, n) for n in nidx]))
            sc = np.clip(s_na / n_mean, 0.0, 0.999).reshape(-1)
            cc = np.sqrt(1.0 - sc ** 2)
            k = np.stack([sc * np.cos(phi).reshape(-1), sc * np.sin(phi).reshape(-1), cc], 1)          # propagation directions inside the crystal
            e1 = np.stack([-np.sin(phi).reshape(-1), np.cos(phi).reshape(-1), np.zeros(N)], 1)        # azimuthal unit vector (perpendicular to k)
            e2 = np.cross(k, e1)
            B = np.einsum('ij,ljk,mk->lim', Rs, np.stack([np.diag([1 / n[i] ** 2 for n in nidx]) for i in range(len(LAM_M))]), Rs)   # (L, 3, 3)
            b11 = np.einsum('ni,lij,nj->ln', e1, B, e1)
            b22 = np.einsum('ni,lij,nj->ln', e2, B, e2)
            b12 = np.einsum('ni,lij,nj->ln', e1, B, e2)
            half = np.sqrt(((b11 - b22) / 2) ** 2 + b12 ** 2)
            n_slow, n_fast = 1 / np.sqrt((b11 + b22) / 2 - half), 1 / np.sqrt((b11 + b22) / 2 + half)
            psi = 0.5 * np.arctan2(2 * b12[len(LAM_M) // 2], (b11 - b22)[len(LAM_M) // 2]) + math.pi / 2           # slow vibration direction in (e1, e2)
            d = np.cos(psi)[:, None] * e1 + np.sin(psi)[:, None] * e2
            alpha = np.arctan2(d[:, 1], d[:, 0])                                                     # its azimuth in the image plane
            gamma = 2 * math.pi * tr['thickness_m'] * (n_slow - n_fast) / cc[None, :] / LAM_M[:, None]   # (L, N)
            ca, sa = np.cos(2 * alpha), np.sin(2 * alpha)
            M = np.zeros((len(LAM_M), N, 2, 2), complex)
            cg, sg = np.cos(gamma / 2), np.sin(gamma / 2)
            M[..., 0, 0] = cg - 1j * sg * ca
            M[..., 1, 1] = cg + 1j * sg * ca
            M[..., 0, 1] = M[..., 1, 0] = -1j * sg * sa
            if plate:
                Mp = retarder(2 * math.pi * plate / LAM_M, PLATE_AZIMUTH_RAD)
                M = np.einsum('lij,lnjk->lnik', Mp, M)
            E = np.array([math.cos(self.pol), math.sin(self.pol)], complex)
            out = np.einsum('lnij,j->lni', M, E)
            if self.an_in:
                A = polarizer(self.an)
                out = np.einsum('ij,lnj->lni', A, out)
            T = 0.5 * np.sum(np.abs(out) ** 2, axis=2)                                               # (L, N)
            S = led_spectrum()[:, None] * cmf()
            xyz = np.einsum('ln,lk->nk', T, S) / S[:, 1].sum()
            lin = (xyz @ XYZ_TO_SRGB.T).reshape(H_, W_, 3)
        lin = np.where(lit[..., None], lin, 0.0)
        sig = BERTRAND_BLUR_PX_PER_RAD * abs(self.bert_focus)
        if tr is not None and self.on_stage and abs(self.z - self._focus_target(self.samples[self.on_stage])) > 5 * depth_of_field_m(ob):
            warnings.append('specimen_out_of_focus')
        lin = gaussian_blur(lin, sig)
        white = rgb_of(np.full(len(LAM_M), 0.5))
        leak = (POLARIZER_ER + NA_LEAK * self._ill_na() ** 2) * white if self.an_in else 0.0
        bright = min(1.0, (self._ill_na() / ob['na']) ** 2) * (ob['na'] / 0.30) ** 2 * 4.0              # the pupil image collects the whole field stop
        return (lin + leak * lit[..., None]) * bright

    def _expose(self, lin, rng):
        level = self.level if self._lamp_on() else 0.0
        return expose(lin, level, self.exposure, self.gain, 0.30, rng)

    def _new_record_id(self, prefix, key):
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        self.seq[key] += 1
        return f'{prefix}{self.seq[key]:04d}'

    def _start(self, kind, duration, **data):
        self.seq['op'] += 1
        oid = f'op_{self.seq["op"]:04d}'
        rid = data.get('record_id')
        self.op = dict(op=oid, kind=kind, t0=self.t, duration_s=duration, **data)
        self.operations[oid] = dict(phase='running', kind=kind, record_id=rid, op=oid)
        return dict(operation_id=oid)

    def _record(self, rid, kind, warnings):
        s = self.samples[self.on_stage]['truth'] if self.on_stage else None
        self.records[rid] = dict(record_id=rid, kind=kind, status='running', started_s=self.t, finished_s=None, sample_id=self.on_stage,
                                 acquisition=self._acquisition(), warnings=warnings, image=None, scan=None,
                                 template_source=dict(library='assets/expansion_batch04/t3_analytical/evident_bx53p/birefringence_library.json',
                                                      substances=[s['substance_id']] if s and s.get('substance_id') else []))

    def _cmd_camera_acquire(self):
        self._need_idle()
        if 'camera_disconnected' in self.faults:
            raise ActionRejected('CAMERA_FAULT')
        rid = self._new_record_id('IM', 'im')
        self._record(rid, 'image', [])
        return self._start('acquire', self.exposure + READOUT_S, record_id=rid)

    def _cmd_scan_extinction(self, step_rad, region):
        self._need_idle()
        if 'camera_disconnected' in self.faults:
            raise ActionRejected('CAMERA_FAULT')
        if region is not None and (region['x1'] <= region['x0'] or region['y1'] <= region['y0']):
            raise ActionRejected('INVALID_REGION')
        n = int(math.floor(math.pi / step_rad + 1e-9))
        angles = [_wrap(self.stage + i * step_rad) for i in range(n)]
        rid = self._new_record_id('SC', 'sc')
        self._record(rid, 'extinction_scan', [])
        self.records[rid]['scan'] = dict(region=region, stage_rad=[], mean_counts=[], extinction_rad=None, contrast=None, residual=None)
        return self._start('extinction_scan', n * (ROTATE_S_PER_STEP + self.exposure + READOUT_S), record_id=rid, angles=angles, step_s=ROTATE_S_PER_STEP + self.exposure + READOUT_S,
                           done=0, start_angle=self.stage, region=region)

    @staticmethod
    def _region_mean(counts, region):
        if region is None:
            region = dict(x0=W_ // 2 - 4, x1=W_ // 2 + 4, y0=H_ // 2 - 4, y1=H_ // 2 + 4)
        return float(counts[region['y0']:region['y1'], region['x0']:region['x1'], :].mean())

    def _finish_acquire(self):
        o = self.op
        r = self.records[o['record_id']]
        warnings = r['warnings']
        lin = self._linear_image(self.stage, warnings)
        counts, sat = self._expose(lin, self._rng(f'frame:{o["record_id"]}'))
        if sat > 0.01:
            warnings.append('saturated')
        if not self._lamp_on():
            warnings.append('lamp_off')
        r['image'] = dict(width=W_, height=H_, bits=CAMERA['bits'], counts=counts.tolist(), mean_counts=[float(v) for v in counts.reshape(-1, 3).mean(0)],
                          saturated_fraction=sat)
        r['warnings'] = sorted(set(warnings))
        self.led['frames'].add(1)
        self.led['exposure_s'].add(self.exposure)
        self._complete(o, r)

    def _scan_frame(self):
        o = self.op
        r = self.records[o['record_id']]
        ang = o['angles'][o['done']]
        d = abs((ang - self.stage + math.pi) % (2 * math.pi) - math.pi)
        self.led['stage_rotation_rad'].add(d)
        self.stage = ang
        warnings = []
        lin = self._linear_image(ang, warnings)
        counts, sat = self._expose(lin, self._rng(f'scan:{o["record_id"]}:{o["done"]}'))
        if not self._lamp_on():
            o['dark_frames'] = o.get('dark_frames', 0) + 1
            warnings.append('lamp_off')
        if sat > 0.01:
            warnings.append('saturated')
        r['scan']['stage_rad'].append(ang)
        r['scan']['mean_counts'].append(self._region_mean(counts, o['region']))
        r['warnings'] = sorted(set(r['warnings']) | set(warnings))
        self.led['frames'].add(1)
        self.led['exposure_s'].add(self.exposure)
        o['done'] += 1
        if o['done'] == len(o['angles']):
            th = np.array(r['scan']['stage_rad']) - o['start_angle']
            I = np.array(r['scan']['mean_counts'])
            X = np.stack([np.ones_like(th), np.cos(4 * th), np.sin(4 * th)], 1)
            well_posed = len(th) >= 3 and np.linalg.svd(X, compute_uv=False)[-1] / math.sqrt(len(th)) >= 0.3
            if not well_posed:
                r['warnings'] = sorted(set(r['warnings']) | {'fit_ill_conditioned'})
            elif o.get('dark_frames'):
                r['warnings'] = sorted(set(r['warnings']) | {'lamp_off'})        # frames without light: the fit would only fit noise
            else:
                c, *_ = np.linalg.lstsq(X, I, rcond=None)
                b = math.hypot(c[1], c[2])
                r['scan']['extinction_rad'] = float(np.mod(o['start_angle'] + math.atan2(-c[2], -c[1]) / 4.0, math.pi / 2)) if b > 0 else None
                r['scan']['contrast'] = float(b / c[0]) if c[0] > 0 else None
                r['scan']['residual'] = float(np.sqrt(np.mean((X @ c - I) ** 2)) / max(1e-9, abs(c[0])))
            if not self._crossed():
                r['warnings'] = sorted(set(r['warnings']) | {'polars_not_crossed'})
            self._return_stage(o)
            self._complete(o, r)

    def _return_stage(self, o):
        back = abs((o['start_angle'] - self.stage + math.pi) % (2 * math.pi) - math.pi)
        self.led['stage_rotation_rad'].add(back)
        self.stage = o['start_angle']

    def _complete(self, o, r):
        r['status'] = 'completed'
        r['finished_s'] = self.t
        self.operations[o['op']]['phase'] = 'completed'
        self._event('camera' if o['kind'] == 'acquire' else 'scan', 'RECORD_DONE', f'记录{r["record_id"]}完成', rid=r['record_id'])
        self.op = None

    def _cancel_op(self, op):
        o = self.op
        if o is None or self.operations.get(o['op']) is not op:
            raise ActionRejected('NOT_RUNNING')
        rid = o.get('record_id')
        if rid in self.records:
            self.records[rid]['status'] = 'cancelled'
            self.records[rid]['finished_s'] = self.t
        if o['kind'] == 'extinction_scan':
            self._return_stage(o)
        self.operations[o['op']]['phase'] = 'cancelled'
        self._event('camera', 'CANCELLED', '操作取消', rid=rid)
        self.op = None

    # ---------------------------------------------------------------- time
    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)

    def step(self, dt_s):
        if not (isinstance(dt_s, (int, float)) and math.isfinite(dt_s) and dt_s >= 0):
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + dt_s
        while True:
            nxt = self._next_event_t()
            if nxt is None or nxt > t_end + 1e-12:
                self._advance(t_end - self.t)
                break
            self._advance(max(0.0, nxt - self.t))
            self._handle_event()
        return dict(time_s=self.t)

    def _next_event_t(self):
        o = self.op
        if o is None:
            return None
        if o['kind'] == 'acquire':
            return o['t0'] + o['duration_s']
        return o['t0'] + (o['done'] + 1) * o['step_s']

    def _advance(self, h):
        if h <= 0:
            return
        if self.power:
            p = IDLE_W + (LED_W_MAX * self.level if self._lamp_on() else 0.0)
            self.led['electrical_energy_j'].add(p * h)
            if self._lamp_on():
                self.led['lamp_on_s'].add(h)
                self.led['led_energy_j'].add(LED_W_MAX * self.level * h)
        self.t += h

    def _handle_event(self):
        o = self.op
        if o['kind'] == 'acquire':
            self._finish_acquire()
        else:
            self._scan_frame()

    # ---------------------------------------------------------------- faults
    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        self._event('fault', 'FAULT_' + ('ON' if active else 'OFF'), fault)
        if active and fault == 'camera_disconnected' and self.op is not None:
            o = self.op
            self.records[o['record_id']]['status'] = 'failed'
            self.records[o['record_id']]['finished_s'] = self.t
            if o['kind'] == 'extinction_scan':
                self._return_stage(o)
            self.operations[o['op']]['phase'] = 'failed'
            self._event('error', 'CAMERA_FAULT', '相机断开，操作失败', rid=o['record_id'])
            self.op = None
        return dict(faults=sorted(self.faults))

    # ---------------------------------------------------------------- analysis and queries
    def _rec(self, rid, kinds=('image',)):
        r = self.records.get(rid)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        if r['kind'] not in kinds or r['status'] != 'completed':
            raise ActionRejected('RECORD_NOT_SUITABLE')
        return r

    def _cmd_analysis_retardation(self, record_id, region, thickness_m):
        r = self._rec(record_id)
        acq = r['acquisition']
        d = abs(((acq['analyzer_rad'] - acq['polarizer_rad']) % math.pi) - math.pi / 2)
        if not acq['analyzer_inserted'] or d > 0.5 * DEG or acq['compensator'] != 'none':
            raise ActionRejected('NOT_CROSSED_POLARS')
        if acq['bertrand_inserted']:
            raise ActionRejected('CONOSCOPIC_IMAGE')
        if region is not None and (region['x1'] <= region['x0'] or region['y1'] <= region['y0']):
            raise ActionRejected('INVALID_REGION')
        counts = np.asarray(r['image']['counts'], float)
        reg = region or dict(x0=W_ // 2 - 4, x1=W_ // 2 + 4, y0=H_ // 2 - 4, y1=H_ // 2 + 4)
        c = counts[reg['y0']:reg['y1'], reg['x0']:reg['x1'], :].reshape(-1, 3).mean(0)
        clipped = float((counts[reg['y0']:reg['y1'], reg['x0']:reg['x1'], :] >= 2 ** CAMERA['bits'] - 1).mean())
        ob = next(o for o in OBJECTIVES if o['name'] == acq['objective'])
        scale = acq['illumination_level'] * LED_PHOTONS_PER_S * acq['exposure_s'] * acq['gain'] * (2 ** CAMERA['bits'] - 1) / CAMERA['full_well_e'] * \
            np.asarray(CAMERA['qe']) / max(CAMERA['qe'])
        bright = min(1.0, (CONDENSER_NA[acq['condenser_top_lens']] * acq['iris_m'] / IRIS_RANGE_M[1] / ob['na']) ** 2) * (ob['na'] / 0.30) ** 2 / max(1e-9, (ob['magnification'] / 10.0) ** 2)
        lin = c / np.maximum(scale * bright, 1e-12)                        # the software knows its own acquisition settings
        ill_na = CONDENSER_NA[acq['condenser_top_lens']] * acq['iris_m'] / IRIS_RANGE_M[1]
        lin = lin - (POLARIZER_ER + NA_LEAK * ill_na ** 2) * rgb_of(np.full(len(LAM_M), 0.5))   # crossed-polar background (calibrated with an empty field)
        R, T = chart_table()
        T = np.clip(T, 0.0, None)                                          # the camera records no negative linear-sRGB values
        num = T @ lin
        den = np.maximum((T * T).sum(1), 1e-12)
        s = np.clip(num / den, 0.0, RET_SCALE_MAX)                          # the dispersion-free chart can be 10-30 % dimmer than a real crystal
        res = np.sqrt(((lin[None, :] - s[:, None] * T) ** 2).sum(1)) / max(1e-9, float(np.linalg.norm(lin)))
        dark = float(np.linalg.norm(lin)) < 5e-3
        note = '颜色与Michel-Lévy色图对照得到程差；同一颜色可对应多级程差；双折射只给出范围内的候选物质，不做唯一物相判定（纹理不等于物相）'
        if clipped > 0.0:
            return dict(record_id=record_id, region=region, retardation_m=None, alternatives_m=[], orientation_factor=None, residual=None, birefringence=None,
                        candidates=[], unique_identification=False, warnings=['saturated_region'],
                        note_zh=f'区域有{clipped * 100:.0f} %的像素通道饱和，颜色不可信；降低曝光、增益或亮度后重拍。' + note)
        if dark:
            return dict(record_id=record_id, region=region, retardation_m=None, alternatives_m=[], orientation_factor=None, residual=None, birefringence=None,
                        candidates=[], unique_identification=False, warnings=['dark_region'], note_zh='区域在消光位或各向同性（太暗）；转动载物台到45°后重拍。' + note)
        rmin = float(res.min())
        near = np.where(res <= rmin + 0.02)[0]
        best = int(near[np.argmin(R[near])])                               # equally good matches: the lowest order (no colour can tell them apart)
        loc = [i for i in range(len(R)) if (i == 0 or res[i] <= res[i - 1]) and (i == len(R) - 1 or res[i] <= res[i + 1])]   # local minima of the match
        alts = []
        for i in sorted(loc, key=lambda i: res[i]):                        # distinct colour minima (other orders) within the dispersion window
            if res[i] > rmin + RET_ALT_WINDOW or len(alts) >= 6:
                break
            if all(abs(R[i] - R[j]) >= RET_MIN_SEPARATION_M for j in [best] + alts):
                alts.append(int(i))
        plausible = [best] + alts
        dn = None if thickness_m is None else float(R[best] / thickness_m)
        cands = []
        if thickness_m is not None:
            dn_low = float(min(R[i] for i in plausible) / thickness_m)     # every plausible order: a grain shows at most d x (n_max - n_min)
            for sid, e in sorted(biref_lib()['substances'].items()):
                if e['birefringence_589'] >= 0.8 * dn_low:
                    cands.append(dict(substance_id=sid, name=e['name'][:60], max_birefringence=e['birefringence_589']))
        return dict(record_id=record_id, region=region, retardation_m=float(R[best]), alternatives_m=[float(R[i]) for i in alts], orientation_factor=float(s[best]),
                    residual=float(res[best]), birefringence=dn, candidates=cands[:80], unique_identification=False, warnings=['ambiguous_order'] if alts else [],
                    note_zh=note + '；候选物质按估计与备选中的最低程差列出（最大双折射不小于最低程差/厚度的0.8倍）；高级次（>1600 nm）与强色散物质可能不在备选中')

    @staticmethod
    def _out(r):
        return copy.deepcopy(r)

    def _cmd_measurement_result(self, record_id):
        r = self.records.get(record_id)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return self._out(r)

    def _cmd_records_list(self):
        return dict(records=sorted(self.records))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def ledger(self):
        return {k: v.value() for k, v in self.led.items()}

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        return dict(library=str(BIREF_LIB_PATH.relative_to(ROOT)), library_sha256=hashlib.sha256(BIREF_LIB_PATH.read_bytes()).hexdigest(),
                    substances=len(biref_lib()['substances']), license='refractiveindex.info database: CC0 1.0 (public domain); CIE 1931 CMF: CVRL')


ASYNC = {'camera.acquire', 'scan.extinction'}
_DATA_GUARDED = {'measurement.result'}
_RECORD_ARG = {'measurement.result': 'record_id'}
_ANALYSIS_RECORD_ARGS = {'analysis.retardation': ['record_id']}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED}


class EvidentBx53pSession:
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
        meta = copy.deepcopy(export_evident_bx53p_contract_metadata())
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

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        foreign = self._role == 'agent' and self._foreign_op()
        values['operation.started_by_other_agent'] = foreign
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
            if capability_id in ('power.set', 'power.press_switch') and not h.power:
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED and self._foreign(arguments.get(_RECORD_ARG[capability_id])):
            raise ActionRejected('ACCESS_DENIED')
        for arg in _ANALYSIS_RECORD_ARGS.get(capability_id, []):
            if arguments.get(arg) is not None and self._foreign(arguments[arg]):
                raise ActionRejected('ACCESS_DENIED')

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
                if digest is None or old != digest or not isinstance(response, dict):
                    raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(response)
            if not isinstance(capability_id, str) or capability_id not in CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and CAPS[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            validate(CAPS[capability_id]['input_schema'], arguments)
            self._guard(capability_id, arguments)
            result = h.invoke(capability_id, **arguments)
            if self._role == 'agent':
                if capability_id == 'records.list':
                    result = dict(records=[r for r in result['records'] if not self._foreign(r)])
                elif capability_id == 'events.read':
                    result = dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in h.events
                                          if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_rid')) and e['kind'] != 'fault'], dropped=h.events_dropped)
                elif capability_id == 'measurement.result':
                    result = dict(result, template_source=dict(library=result['template_source']['library'], substances=[]))   # the sample identity is truth
            operation = result.get('operation_id') if capability_id in ASYNC else None
            if operation:
                h._owners[operation] = self._principal
                rid = h.operations[operation].get('record_id')
                if rid:
                    h._owners[rid] = self._principal
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=operation,
                            status='accepted' if operation else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                            result=copy.deepcopy(result), observations={}, error=None)
            h._requests[key] = (digest, copy.deepcopy(response))
            return response
        except ActionRejected as exc:
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None,
                            status='unsupported' if str(exc) == 'UNSUPPORTED' else 'rejected', state_revision=h.revision,
                            clock={'domain': 'simulation', 'time_s': h.time_s}, result={}, observations={},
                            error=dict(code=str(exc), message=ERROR_ZH.get(str(exc), str(exc)), retryable=False, side_effects='none'))
            if key is not None and digest is not None and key not in h._requests:
                h._requests[key] = (digest, copy.deepcopy(response))
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
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        if op.get('record_id') in h.records:
            res = h._out(h.records[op['record_id']])
            if self._role == 'agent':
                res['template_source'] = dict(library=res['template_source']['library'], substances=[])
        else:
            res = dict(phase=op['phase'])
        return dict(operation_id=operation_id, status='succeeded' if op['phase'] == 'completed' else op['phase'], result=res)   # protocol.md: running / succeeded / failed / cancelled

    def cancel(self, operation_id, request_id):
        h = self._host
        if not isinstance(request_id, str) or not request_id:
            raise ActionRejected('INVALID_ARGUMENT')
        key = (self._principal, request_id)
        digest = json.dumps(['cancel', operation_id]) if isinstance(operation_id, str) else None
        if key in h._requests:
            old, response = h._requests[key]
            if digest is None or old != digest:
                raise ActionRejected('REQUEST_CONFLICT')
            if isinstance(response, str):
                raise ActionRejected(response)
            return copy.deepcopy(response)
        try:
            self.poll(operation_id)
            op = h.operations[operation_id]
            if op['phase'] != 'running':
                raise ActionRejected('NOT_RUNNING')
            h._cancel_op(op)
        except ActionRejected as exc:
            if digest is not None:
                h._requests[key] = (digest, str(exc))
            raise
        h.revision += 1
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=h.instance_id, request_id=request_id, operation_id=operation_id, status='succeeded',
                        state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=dict(phase=op['phase']), observations={}, error=None)
        h._requests[key] = (digest, copy.deepcopy(response))
        return response


ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（证据槽、另一个agent的记录或操作）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'MAINS_OUT_OF_RANGE': '电源电压超出100–240 VAC ±10 %',
    'UNKNOWN_SUBSTANCE': '双折射模板库中没有该物质', 'NOT_A_LIQUID_CRYSTAL': '液晶盒只能用单轴液晶模板', 'SAMPLE_EXISTS': '样品ID已存在',
    'TOO_MANY_SAMPLES': '样品数量超过上限（20）', 'UNKNOWN_SAMPLE': '样品不存在', 'SAMPLE_NOT_ON_BENCH': '样品不在实验台上', 'STAGE_OCCUPIED': '载物台上已有载玻片',
    'SLIDE_TOO_THICK': '载玻片超过1.5 mm（聚光镜工作距离1.3 mm只适用1.5 mm以内的载玻片）', 'SAMPLE_NOT_ON_STAGE': '该载玻片不在载物台上',
    'OPERATION_RUNNING': '拍照或消光扫描进行中', 'CONOSCOPIC_IMAGE': '程差分析需要正交观察（勃氏镜拉出）的图像；锥光图像是后焦面干涉图', 'CAMERA_FAULT': '相机断开', 'INVALID_REGION': '区域的右下角须在左上角的右下方', 'RECORDS_FULL': '记录数已达上限（60）',
    'UNKNOWN_RECORD': '记录不存在', 'RECORD_NOT_SUITABLE': '记录类型不对或未完成', 'NOT_CROSSED_POLARS': '程差分析需要正交偏光（检偏镜插入、与起偏镜成90°）且不插试板的图像',
    'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作已结束',
    'OBJECTIVE_COLLISION': '物镜前端会碰到样品（载玻片上样品顶面高于焦面超过物镜工作距离：4× 17 mm、10× 10 mm、40× 0.51 mm、100×油 0.2 mm；样品厚度计入，盖玻片忽略）',
}
_LAMP = ['illumination.lamp_on', 'panel.lamp_glow']
_STAGE = ['stage.angle_rad', 'stage.click_stop', 'stage.indicated_rad', 'truth.center_slow_axis_rad']
_OPS = ['operation.kind', 'operation.progress']
_TRUTH_SLIDE = ['truth.center_retardation_m', 'truth.center_slow_axis_rad', 'truth.focus_error_m']
EFFECTS = {
    'plant.configure': ['plant.configured', 'power.enabled'] + _LAMP, 'sample.provide': ['sample.bench_ids', 'truth.samples'],
    'sample.place': ['sample.bench_ids', 'sample.on_stage'] + _TRUTH_SLIDE, 'sample.remove': ['sample.bench_ids', 'sample.on_stage'] + _TRUTH_SLIDE,
    'power.set': ['power.enabled'] + _LAMP, 'power.press_switch': ['power.enabled'] + _LAMP,
    'illumination.set_level': ['illumination.knob_rad', 'illumination.level'] + _LAMP, 'polarizer.set_angle': ['optics.crossed', 'polarizer.angle_rad'],
    'analyzer.set_inserted': ['analyzer.inserted', 'analyzer.slider_m', 'optics.crossed'], 'analyzer.set_angle': ['analyzer.angle_rad', 'analyzer.dial_rad', 'optics.crossed'],
    'compensator.insert': ['compensator.plate', 'compensator.slider_m'], 'condenser.set_top_lens': ['condenser.illumination_na', 'condenser.top_lens', 'condenser.top_lens_rad'],
    'condenser.set_iris': ['condenser.illumination_na', 'condenser.iris_m', 'condenser.iris_lever_rad'],
    'bertrand.set_inserted': ['bertrand.inserted', 'bertrand.slider_m'], 'bertrand.focus': ['bertrand.focus_rad'],
    'objective.select': ['objective.index', 'objective.magnification', 'objective.na', 'objective.name', 'objective.nosepiece_rad', 'optics.fov_m'],
    'stage.rotate': list(_STAGE), 'focus.move': ['focus.coarse_turns_rad', 'focus.stage_z_m', 'truth.focus_error_m'], 'camera.configure': ['camera.exposure_s', 'camera.gain'],
    'camera.acquire': list(_OPS), 'scan.extinction': list(_OPS), 'fault.inject': ['truth.faults'] + _LAMP + _OPS + _STAGE, 'sim.step': ['clock.time_s'],
}
ON_STEP = {'camera.acquire': _OPS + ['result.last_record_id'], 'scan.extinction': _OPS + ['result.last_record_id'] + _STAGE,
           'fault.inject': _OPS + ['result.last_record_id'] + _STAGE,     # a camera fault ends a running operation: it no longer completes later
           'sim.step': _OPS + ['result.last_record_id'] + _STAGE}
ON_STEP_ZH = {'fault.inject': '相机断开使进行中的操作失败，之后不再完成（不再转台、不再写记录）', 'camera.acquire': '曝光+读出结束时写入图像记录并清除操作', 'scan.extinction': '逐步转动载物台并拍照，结束时拟合并回到起始角',
              'sim.step': '时钟推进；进行中的操作按计划时刻完成'}
PRECONDITIONS = {
    'plant.configure': ('噪声种子只能在首次声明时设定', ['plant.configured']),
    'sample.provide': ('已声明实验室条件；样品ID未用过；物质在双折射模板库中（液晶须为单轴模板）', ['plant.configured', 'sample.bench_ids']),
    'sample.place': ('载玻片在实验台上、载物台空、无进行中的操作；载玻片≤1.5 mm；放上后物镜不碰样品（载玻片+样品厚度）', ['sample.bench_ids', 'sample.on_stage', 'operation.kind', 'focus.stage_z_m', 'objective.index']),
    'sample.remove': ('载玻片在载物台上；无进行中的操作', ['sample.on_stage', 'operation.kind']),
    'power.set': ('已声明实验室条件；开机时电源电压在100–240 VAC ±10 %', ['plant.configured', 'power.enabled']),
    'power.press_switch': ('已声明实验室条件；断电时按下为开机（电源电压须在范围内），通电时按下为关机', ['plant.configured', 'power.enabled']),
    'camera.acquire': ('已声明实验室条件；无进行中的操作；相机连接', ['plant.configured', 'operation.kind']),
    'scan.extinction': ('已声明实验室条件；无进行中的操作；相机连接；区域有效；步长1–40°（45°步长只采到4θ的两个相位，无法拟合）', ['plant.configured', 'operation.kind']),
    'analysis.retardation': ('完成的图像记录，采集时正交偏光且没有插试板', ['result.last_record_id']),
}
for _k in ('polarizer.set_angle', 'analyzer.set_inserted', 'analyzer.set_angle', 'compensator.insert', 'condenser.set_top_lens', 'condenser.set_iris',
           'objective.select', 'stage.rotate', 'focus.move', 'camera.configure', 'bertrand.set_inserted', 'bertrand.focus'):
    PRECONDITIONS[_k] = ('已声明实验室条件；无进行中的拍照或扫描（手动部件在采集中不动）', ['plant.configured', 'operation.kind'])
PRECONDITIONS['focus.move'] = ('已声明实验室条件；无进行中的拍照或扫描；载玻片上样品顶面（载玻片+样品厚度）不高于焦面加当前物镜工作距离', ['plant.configured', 'operation.kind', 'objective.index', 'sample.on_stage'])
PRECONDITIONS['objective.select'] = ('已声明实验室条件；无进行中的拍照或扫描；在当前载物台高度下新物镜不碰样品（载玻片+样品厚度）', ['plant.configured', 'operation.kind', 'focus.stage_z_m', 'sample.on_stage'])
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_IDLE = ['PLANT_REQUIRED', 'OPERATION_RUNNING']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'sample.provide': ['PLANT_REQUIRED', 'SAMPLE_EXISTS', 'TOO_MANY_SAMPLES', 'UNKNOWN_SUBSTANCE', 'NOT_A_LIQUID_CRYSTAL'],
    'sample.place': _IDLE + ['UNKNOWN_SAMPLE', 'SAMPLE_NOT_ON_BENCH', 'STAGE_OCCUPIED', 'SLIDE_TOO_THICK', 'OBJECTIVE_COLLISION'], 'sample.remove': _IDLE + ['SAMPLE_NOT_ON_STAGE'],
    'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_switch': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'illumination.set_level': ['PLANT_REQUIRED'],
    'camera.acquire': _IDLE + ['CAMERA_FAULT', 'RECORDS_FULL'], 'scan.extinction': _IDLE + ['CAMERA_FAULT', 'INVALID_REGION', 'RECORDS_FULL'],
    'analysis.retardation': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'NOT_CROSSED_POLARS', 'CONOSCOPIC_IMAGE', 'INVALID_REGION'], 'measurement.result': ['UNKNOWN_RECORD'],
    'fault.inject': ['PLANT_REQUIRED'], 'records.list': [], 'events.read': [], 'ledger.read': [], 'provenance.read': [], 'sim.step': [],
}
for _k in ('polarizer.set_angle', 'analyzer.set_inserted', 'analyzer.set_angle', 'compensator.insert', 'condenser.set_top_lens', 'condenser.set_iris',
           'objective.select', 'stage.rotate', 'focus.move', 'camera.configure', 'bertrand.set_inserted', 'bertrand.focus'):
    CAP_ERRORS[_k] = list(_IDLE)
CAP_ERRORS['objective.select'] = _IDLE + ['OBJECTIVE_COLLISION']
CAP_ERRORS['focus.move'] = _IDLE + ['OBJECTIVE_COLLISION']
_PARTIAL = {'CAMERA_FAULT'}
CAP_ERROR_ZH = {('camera.acquire', 'CAMERA_FAULT'): '相机断开；或拍照中断开（记录以failed结束，没有图像）',
                ('scan.extinction', 'CAMERA_FAULT'): '相机断开；或扫描中断开（记录以failed结束，已测角度保留，载物台回到起始角）'}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件，没有可见模型变化', 'sample.provide': '只在过程层实验台上增加样品；场景没有样品几何',
    'sample.place': '载玻片只在过程层（场景载物台中心为接口点）', 'sample.remove': '同上', 'fault.inject': '评估者故障注入，没有可见模型变化',
    'camera.configure': '只写相机设置', 'camera.acquire': '拍照没有可见运动', 'analysis.retardation': '分析计算', 'measurement.result': '只读记录',
    'records.list': '只读列表', 'events.read': '只读事件', 'ledger.read': '只读账目（evaluator）', 'provenance.read': '只读来源记录',
}
SCENE_MIRRORED = {'stage.angle_rad', 'polarizer.angle_rad', 'analyzer.dial_rad', 'analyzer.slider_m', 'compensator.slider_m', 'condenser.top_lens_rad',
                  'condenser.iris_lever_rad', 'objective.nosepiece_rad', 'focus.stage_z_m', 'focus.coarse_turns_rad', 'illumination.knob_rad', 'bertrand.slider_m',
                  'bertrand.focus_rad', 'power.enabled', 'panel.lamp_glow'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节或出光窗发光（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {
    'camera.acquire': ('操作结束（完成、失败或取消）：曝光时间+0.05 s读出后写入image记录', '取消时记录以cancelled结束、没有图像；相机断开时failed。'),
    'scan.extinction': ('操作结束：0–180°按步长逐步转台（每步2 s转动+曝光+读出）并拍照，拟合后写入extinction_scan记录，载物台回到起始角',
                        '取消或相机断开时记录以cancelled/failed结束，已测角度保留在记录中，载物台回到起始角。'),
}
RESOURCES = {'camera.acquire': ['camera', 'host_clock'], 'scan.extinction': ['stage', 'camera', 'host_clock'], 'stage.rotate': ['stage'], 'focus.move': ['stage_carrier'],
             'objective.select': ['nosepiece'], 'polarizer.set_angle': ['polarizer'], 'analyzer.set_inserted': ['analyzer'], 'analyzer.set_angle': ['analyzer'],
             'compensator.insert': ['compensator_slot'], 'condenser.set_top_lens': ['condenser'], 'bertrand.set_inserted': ['bertrand_lens'], 'bertrand.focus': ['bertrand_lens'], 'condenser.set_iris': ['condenser'], 'illumination.set_level': ['lamp'],
             'power.set': ['mains'], 'power.press_switch': ['mains'], 'plant.configure': ['room', 'mains'], 'sample.provide': ['bench'], 'sample.place': ['bench', 'stage'],
             'sample.remove': ['bench', 'stage'], 'fault.inject': ['evaluator'], 'sim.step': ['host_clock'], 'camera.configure': ['camera']}
OBSERVES = {
    'power.set': ['power.enabled', 'illumination.lamp_on', 'panel.lamp_glow'], 'power.press_switch': ['power.enabled', 'illumination.lamp_on', 'panel.lamp_glow'],
    'illumination.set_level': ['illumination.level', 'illumination.lamp_on'], 'polarizer.set_angle': ['polarizer.angle_rad', 'optics.crossed'],
    'analyzer.set_inserted': ['analyzer.inserted', 'optics.crossed'], 'analyzer.set_angle': ['analyzer.angle_rad', 'optics.crossed'], 'compensator.insert': ['compensator.plate'],
    'condenser.set_top_lens': ['condenser.top_lens', 'condenser.illumination_na'], 'bertrand.set_inserted': ['bertrand.inserted'], 'bertrand.focus': ['bertrand.focus_rad'], 'condenser.set_iris': ['condenser.iris_m', 'condenser.illumination_na'],
    'objective.select': ['objective.name', 'objective.na', 'optics.fov_m'], 'stage.rotate': ['stage.indicated_rad', 'stage.click_stop'], 'focus.move': ['focus.stage_z_m'],
    'camera.configure': ['camera.exposure_s', 'camera.gain'], 'camera.acquire': ['operation.kind', 'result.last_record_id'],
    'scan.extinction': ['operation.kind', 'operation.progress', 'stage.indicated_rad', 'result.last_record_id'], 'sample.place': ['sample.on_stage'],
    'sample.remove': ['sample.bench_ids'],
}
_WB = 'user_workbook'
_PAGE = 'manufacturer_primary'
_BRO = 'manufacturer_brochure'
_RII = 'refractiveindex_info'
_SIM = 'simulation_assumption'
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({
    'sample.provide': [_RII, _WB, _SIM], 'sample.place': [_PAGE, _BRO], 'power.set': [_BRO, _SIM], 'power.press_switch': [_BRO, _SIM], 'illumination.set_level': [_BRO, _SIM],
    'polarizer.set_angle': [_BRO], 'analyzer.set_inserted': [_BRO], 'analyzer.set_angle': [_BRO], 'compensator.insert': [_BRO], 'condenser.set_top_lens': [_BRO, _PAGE],
    'condenser.set_iris': [_BRO], 'bertrand.set_inserted': [_BRO], 'bertrand.focus': [_BRO, _SIM], 'objective.select': [_BRO, _PAGE, _WB], 'stage.rotate': [_BRO, _PAGE, _WB], 'focus.move': [_PAGE, _SIM], 'camera.acquire': [_RII, _SIM, _WB],
    'scan.extinction': [_BRO, _SIM, _WB], 'analysis.retardation': [_RII, _SIM, _WB], 'provenance.read': [_RII]})
CAP_SOURCES.update({k: [_PAGE, _BRO] for k in UNSUPPORTED})
PARAM_ZH = {'room_k': '实验室室温', 'mains_voltage_v': '供电电压', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.bx53p_polarized_light.limitations。'
CAP_LIMITS = {
    'sample.provide': ['主折射率色散取自refractiveindex.info原始文献；薄片、单晶取向、液晶缺陷布置是样品输入或按种子随机生成，不是厂家数据；聚合物球晶没有模板数据，未提供。'],
    'camera.acquire': ['相机（像素、量子效率、满阱、读出噪声）、LED光谱与透过率为工程值；图像按96×72模拟像素；景深、衍射与离焦按几何光学近似；起偏检偏为理想偏振器加消光比漏光。'],
    'scan.extinction': ['载物台转动是宿主/机器人的手动操作抽象（每步2 s）；拟合假设I=a−b·cos4(θ−θe)，多个晶粒跨区域时结果为平均。'],
    'analysis.retardation': ['程差按无色散Michel-Lévy色图估计（色图负的线性sRGB分量截为0，扣除按采集设置标定的正交偏光背景，亮度因子允许到1.35倍以容纳色散）；'
                             '颜色不唯一：残差相差≤0.02时取最低级次，相距≥150 nm的其他颜色极小值在残差窗口0.25内列为备选（最多6个），并给warnings: ambiguous_order；'
                             '单晶扫描（retardation_sweep.json，50–2800 nm）：石英、刚玉、MgF2、方解石、金红石——估计本身正确约48 %，估计或备选含真实级次约85 %；'
                             '另8种（ZnO、LiNbO3、KTP、尿素、LiIO3、BBO、KDP、TeO2）——约35 %与约78 %；1600 nm以下的漏判主要是色散强的金红石、LiNbO3、KTP（另有TeO2在1400–1600 nm）；'
                             '估计错误时都带ambiguous_order；候选物质含真实物质约98–99 %，但列表很宽（平均约37–42种/58种），只起排除作用；'
                             '候选物质按估计与备选中最低程差/厚度的0.8倍列出；区域有饱和像素时不估计（saturated_region）；不能唯一判定物相。'],
    'stage.rotate': ['45°卡点的落入范围±1°为工程值；锁定与三点对中未建模。'],
    'bertrand.set_inserted': ['锥光图像按视场中心晶粒（勃氏镜Ø3.4 mm视场光阑下）的逐方向本征折射率计算；折射按平均折射率、界面反射与会聚光的偏振旋转未建模；液晶按视场中心指向矢。'],
    'bertrand.focus': ['勃氏镜调焦只模糊干涉图（每rad 3像素，工程值）。'], 'focus.move': ['调焦行程与每圈行程为工程值（厂家缓存资料未给出）。'],
}
LIMITATIONS = [
    '选定配置：BX53-P锥光/正交配置（U-CPA可调焦勃氏镜、U-AN360P、U-POC-2、U-SRP、U-P4RE、UPLFLN 4XP/10XP/40XP/100XOP、U-TP530/U-TP137、倾斜三目FN 22、LED），约17 kg；'
    '调研表的16 kg对应正交配置（冲突已记录）；相机为三目接口上的通用彩色CMOS工程模型。',
    '光学：逐波长（400–700 nm，10 nm）Jones计算（起偏、样品程差、试板、检偏），样品主折射率来自refractiveindex.info（CC0），CIE 1931颜色匹配函数（CVRL）转sRGB；'
    '锥光干涉图按物镜孔径内各传播方向的本征折射率计算；热台、Berek类补偿器不在配置内。',
    '样品在场景中没有几何；载玻片放置、转台与调焦是宿主/机器人抽象，没有接触验证。',
    '没有真实BX53-P与相机的硬件或软件接口。',
]


def _sampling_text(k):
    if k in ('clock.time_s', 'operation.progress'):
        return 'Instantaneous value at the read time; operations complete at their scheduled times inside step().'
    return 'Changes when a command is accepted or at an event of an operation; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or k in _ANALYSIS_RECORD_ARGS or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    return '；另一个agent的操作进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）' if k in _CONTROL_GUARDED else ''


def export_evident_bx53p_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'EvidentBx53pSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.evident_bx53p.EvidentBx53pSession.invoke',
                                              invocation=f'session.invoke(instance_id={INSTANCE!r},capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='EvidentBx53pRuntime.invoke / EvidentBx53pSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的派生量；操作进度只随step推进')
                                for s in EFFECTS.get(k, [])],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置、分析或查询',
                                completion_state_ids=['operation.kind', 'result.last_record_id'] if async_ else [],
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH.get(e, e)), side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'objectives_na': ([o['na'] for o in OBJECTIVES], '1', 'UPLFLN 4XP/10XP/40XP/100XOP数值孔径0.13/0.30/0.75/1.30（厂家宣传册）', [_BRO]),
            'objectives_magnification': ([o['magnification'] for o in OBJECTIVES], '1', '物镜倍率4/10/40/100（厂家）', [_BRO, _WB]),
            'objectives_working_distance_m': ([o['working_distance_m'] for o in OBJECTIVES], 'm', '工作距离17.0/10.0/0.51/0.2 mm（厂家）', [_BRO]),
            'field_number_m': (FIELD_NUMBER_M, 'm', '观察筒视场数FN 22（厂家）', [_PAGE, _BRO, _WB]),
            'condenser_na': ([CONDENSER_NA['in'], CONDENSER_NA['out']], '1', '聚光镜NA 0.9（上透镜入）/0.18（出）（厂家）', [_BRO, _PAGE, _WB]),
            'condenser_working_distance_m': (CONDENSER_WD_M, 'm', '聚光镜工作距离1.3 mm，适用1.5 mm载玻片（厂家）', [_PAGE, _WB]),
            'iris_range_m': (list(IRIS_RANGE_M), 'm', '孔径光阑2–21 mm（厂家）', [_BRO]),
            'stage_click_rad': (STAGE_CLICK_RAD, 'rad', '载物台45°卡点（厂家）', [_BRO, _WB]), 'stage_vernier_rad': (STAGE_VERNIER_RAD, 'rad', '游标6′（0.1°）（厂家）', [_BRO]),
            'analyzer_step_rad': (ANALYZER_STEP_RAD, 'rad', '检偏镜最小读数0.1°（厂家）', [_BRO]),
            'plates_m': ([PLATES['U-TP530'], PLATES['U-TP137']], 'm', 'U-TP530一级红530 nm、U-TP137 λ/4 137 nm（厂家）', [_BRO]),
            'power_w': (POWER_W, 'W', '功耗约24 W（厂家）', [_BRO])}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    for name, (val, unit, meaning) in {
            'camera_pixels': (list(CAMERA['pixels']), '1', '模拟相机像素网格'), 'camera_full_well_e': (CAMERA['full_well_e'], '1', '满阱电子数'),
            'camera_read_noise_e': (CAMERA['read_noise_e'], '1', '读出噪声（电子）'), 'camera_bits': (CAMERA['bits'], '1', 'ADC位数'),
            'led_photons_per_s': (LED_PHOTONS_PER_S, '1/s', '满亮度10×明场每像素电子产率'), 'polarizer_extinction_ratio': (POLARIZER_ER, '1', '正交偏光消光比'),
            'na_leak': (NA_LEAK, '1', '高照明孔径下正交偏光额外漏光系数（×NA²）'), 'top_lens_field_m': (TOP_LENS_FIELD_M, 'm', '上透镜入时照明视场直径'),
            'click_capture_rad': (CLICK_CAPTURE_RAD, 'rad', '卡点落入范围'), 'rotate_s_per_step': (ROTATE_S_PER_STEP, 's', '扫描每步转台时间'),
            'focus_range_m': (list(FOCUS_RANGE_M), 'm', '调焦行程'), 'coarse_m_per_turn': (COARSE_M_PER_TURN, 'm', '粗调每圈行程'),
            'fine_m_per_turn': (FINE_M_PER_TURN, 'm', '细调每圈行程'), 'led_w_max': (LED_W_MAX, 'W', 'LED满亮度电功率（与电子部分合计约24 W）')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['frame_base', 'lamp_housing', 'condenser', 'polarizer_ring', 'stage', 'nosepiece', 'obj_4x', 'obj_10x', 'obj_40x', 'obj_100x', 'compensator_slider',
                       'analyzer_slider', 'analyzer_dial', 'bertrand_slider', 'cpa_attachment', 'trinocular_tube', 'camera_port', 'stage_carrier'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='BX53-P透射偏光显微镜：LED照明、起偏镜、样品（双折射薄片/单晶/液晶）、试板、检偏镜与相机的逐波长偏振光传播；消光扫描与Michel-Lévy程差分析',
        equations=['E_out = A(a) C_45(R_c) R(theta) diag(e^{-i Gamma/2}, e^{i Gamma/2}) R(-theta) P(p) E_in ; Gamma = 2 pi d (n_slow - n_fast)(lambda) / lambda',
                   'crossed polars: T = sin^2(2(theta - p)) sin^2(Gamma/2)',
                   'section indices: eigenvalues of the xy block of R diag(1/n_a^2, 1/n_b^2, 1/n_c^2) R^T (normal incidence)',
                   'RGB = M_sRGB sum_lambda T(lambda) S_LED(lambda) [xbar, ybar, zbar](lambda) / sum S_LED ybar',
                   'counts = round(gain (2^12 - 1)/FW * clip(Poisson(level R_LED t QE b rgb) + N(0, sigma_read), 0, FW)), b = min(1, (NA_ill/NA_obj)^2) (NA_obj/0.3)^2/(M/10)^2',
                   'extinction scan: I(theta) = a - b cos 4(theta - theta_e), least squares',
                   'conoscopy: pixel r -> sin(theta) = NA r; inside the crystal sin(theta_c) = sin(theta)/n_mean; eigen-indices of B projected on the plane perpendicular to k; '
                   'Gamma = 2 pi d (n_s - n_f) / (lambda cos(theta_c))'],
        parameters=params, source_ids=[_SIM, _RII, _PAGE, _BRO, _WB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_bx53p_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'bx53p_polarized_light': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'bx53p_polarized_light': mechanism})
