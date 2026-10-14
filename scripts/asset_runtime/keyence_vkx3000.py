"""CHEM-155 KEYENCE VK-X3000 controller + VK-X3100 measurement head on the VK-D3 motorized base (3D surface profiler: laser confocal,
focus variation, white light interferometry and spectral-interference film thickness): runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time):
  * power: controller, head and motorized stage power, boot (the head Z stage retracts to its origin), power loss stops every motion and
    fails a running measurement; the coaxial coarse / fine knobs of the base lift the stage by hand (also without power);
  * mechanics: motorized XY stage (100 x 100 mm), manual stage lift (30 mm, engineering), head Z stage (38 mm, engineering), 6-hole electric
    revolver, optical zoom 1-8x; exact constant-speed motion with event cuts at the arrivals; objective / sample collision guard;
  * samples: substrate + up to four surface features (step, grating, pillar array, pits, spherical cap, wear groove, sinusoidal standard) of
    library materials, an optional conformal transparent film, band-limited random roughness (deterministic random-phase field per sample)
    and a tilt; placed on the circular sample plate;
  * laser confocal (404 nm): per pixel the Fresnel reflectances of the top surface and of a buried interface under transparent material
    (optical constants from the template library), specular capture limited by the objective NA and diffuse scatter from unresolved
    roughness, the plane-mirror axial response of a confocal head with a finite pinhole (1 Airy unit; paraxial integral over the pinhole in
    x = 2 dz (1 - cos a) / lambda, no zeros or side lobes), a Z scan at the pitch with per-frame Z jitter, intensity noise,
    PMT gain from the automatic gain (AAG) or a manual gain, optional double scan for dark pixels, saturation, and the peak search (strongest,
    first or both peaks = film mode) with a log-parabola interpolation; frame time from the documented 7900 Hz line rate;
  * focus variation: camera contrast needs surface texture (unresolved roughness or edges); smooth transparent films are seen through;
  * white light interferometry (20x interference objective): envelope + phase, phase change on reflection of each material (n, k at the LED
    centre wavelength), thin-film phase of transparent films, slope limit of the interference objective, envelope mode on rough surfaces;
  * spectral-interference film thickness: normal-incidence reflectance spectrum of the film stack (Fresnel / Airy), noise, least-squares fit
    of the thickness with the film and substrate materials named by the operator (a wrong material gives a wrong thickness);
  * instrument errors: hidden Z-scale and lateral-scale errors (calibration with a step-height or pitch standard), Z-stage repeatability;
  * stitching of field grids with stage moves; analysis: plane levelling, ISO 25178 areal and ISO 21920 profile roughness, ISO 5436-1 step
    height, profiles;
  * fault injection (evaluator): floor vibration, laser failure, stage cable disconnected, revolver jam;
  * ledgers: laser energy (emitted, to the sample, reflected, absorbed (in an opaque top material, in a transparent layer or by the black
    sample plate), transmitted (through a transparent top into the material below, or into a transparent bulk)), frames, stage and Z travel.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import random
from pathlib import Path

import numpy as np


class ActionRejected(ValueError):
    pass


# ---------------------------------------------------------------- schema helpers
def number(unit='1', lo=None, hi=None):
    s = {'type': 'number', 'x-unit': unit}
    if lo is not None:
        s['minimum'] = lo
    if hi is not None:
        s['maximum'] = hi
    return s


def obj(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


def array(s, minimum=0, maximum=4096):
    return dict(type='array', items=s, minItems=minimum, maxItems=maximum)


def enum(items):
    return {'type': 'string', 'enum': list(items)}


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
            if set(v) != set(s['properties']):
                raise ActionRejected('INVALID_ARGUMENT')
            for key, value in v.items():
                validate(s['properties'][key], value)
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


# ---------------------------------------------------------------- identity, paths
PROTOCOL_VERSION = '1.0.0'
SLUG = 'keyence_vkx3000'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-155'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t3_analytical' / SLUG
LIBRARY_PATH = ASSET / 'optical_constants_library.json'
LIBRARY_REL = str(LIBRARY_PATH.relative_to(ROOT))

# ---------------------------------------------------------------- documented specifications (KEYENCE VK-X3000 / VK-X3100 / VK-D3 pages)
LASER_WAVELENGTH_M = 404e-9                      # VK-X3100: semiconductor laser, 404 nm
LASER_MAX_W = 1.0e-3                             # max. laser output 1.0 mW (class 2)
LINE_RATE_HZ = 7900.0                            # max. laser measurement speed: line 7900 Hz
SURFACE_RATE_HZ = 125.0                          # max. laser measurement speed: surface 125 Hz
PMT_BITS = 16                                    # 16-bit photomultiplier
HEIGHT_DISPLAY_RES_M = 0.1e-9                    # laser confocal / focus variation height display resolution (VK-X3100)
WLI_DISPLAY_RES_M = 0.01e-9                      # white light interferometry height display resolution
CONFOCAL_REPEAT_M = {10.0: 100e-9, 20.0: 40e-9, 50.0: 12e-9}          # laser confocal height repeatability sigma
FV_REPEAT_M = {5.0: 500e-9, 10.0: 100e-9, 20.0: 50e-9, 50.0: 20e-9}   # focus variation height repeatability sigma
CONFOCAL_WIDTH_REPEAT_3S_M = {10.0: 200e-9, 20.0: 100e-9, 50.0: 40e-9}
WLI_TOPO_REPEAT_M = 0.08e-9                      # surface topography repeatability (typical)
WLI_RMS_REPEAT_M = 0.008e-9                      # repeatability of RMS (typical)
FILM_REPEAT_M = 0.1e-9                           # spectral interference film thickness repeatability sigma (typical)
FILM_ACCURACY_REL = 0.006                        # +-0.6 % (typical)
FILM_RANGE_M = (100e-9, 5000e-9)                 # film thickness range of the same configuration (CMU Manufacturing Futures Institute page)
HEIGHT_ACCURACY_M = (0.2e-6, 0.01)               # height accuracy 0.2 + L/100 um (standard sample, >= 20x lens)
WIDTH_ACCURACY_REL = 0.02                        # width accuracy +-2 % of the measured value
ZOOM_RANGE = (1.0, 8.0)                          # optical zoom 1 to 8x
TOTAL_MAG_RANGE = (42.0, 28800.0)                # total magnification on a 23-inch full-screen display
FOV_RANGE_M = (11e-6, 7398e-6)                   # field of view
CAMERA_PIXELS = 5.6e6                            # optical observation: 5.6 million pixels
REVOLVER_HOLES = 6                               # 6-hole electric revolver
MOTORIZED_XY_M = (0.100, 0.100)                  # motorized operation range (VK-D3)
MAINS_V = (100.0, 240.0)                         # power voltage 100 to 240 VAC, 50/60 Hz
POWER_VA = 150.0                                 # power consumption 150 VA
AMBIENT_K = (288.15, 301.15)                     # ambient temperature +15 to 28 C
RH_RANGE = (0.20, 0.80)                          # relative humidity 20 to 80 % RH
MASS_CONTROLLER_KG, MASS_HEAD_KG, MASS_BASE_KG = 3.0, 13.0, 19.5
# geometry of the drawings (dimension_drawing_measurements.json)
FOCUS_Z0 = 0.288 - 0.0467                        # focal plane at the head Z origin (46.7 mm below the attaching face on the 288 mm column)
SAMPLE_PLATE_Z = 0.2085329                       # sample plate surface at lift 0 (stage top 206.03 mm + 2.5 mm plate, from the VK-D3 drawings)

# ---------------------------------------------------------------- engineering values (not documented)
FOV_CONST_M = 7398e-6 * 2.5                      # FOV_h = 18.5 mm / (M x zoom): anchored to the documented 7398 um at 2.5x / 1x
FOV_ASPECT = 0.75                                # 4:3 laser scan field
DISPLAY_IMAGE_M = 42.0 * 7398e-6                 # total magnification = 0.311 m / FOV_h (anchored to 42x at 7398 um)
SIM_GRID = (128, 96)                             # simulated pixels per field (the instrument scans 1024 x 768)
NOMINAL_GRID = (1024, 768)
QUALITY_LINES = {'high_speed': 64, 'standard': 384, 'high_accuracy': 768}   # lines per laser frame: 64 lines -> 123 Hz ~ documented 125 Hz
FV_FPS = 30.0
WLI_FPS = 100.0
WLI_LAMBDA0_M = 600e-9                           # white-LED centre wavelength for interferometry
WLI_COHERENCE_M = 1.2e-6
WLI_SLOPE_NA = 0.40                              # 20x interference objective
FILM_BAND_NM = (420.0, 780.0)
STAGE_SPEED_M_S = 0.010
LIFT_RANGE_M = (-0.030, 0.0)
LIFT_SPEED_M_S = 0.002
COARSE_PITCH_M = 0.004                           # coarse knob 4 mm per turn
HEAD_Z_RANGE_M = (0.0, 0.038)
HEAD_Z_SPEED_M_S = 0.005
REVOLVER_S_PER_STEP = 0.8
BOOT_S = 45.0
AUTOFOCUS_S = 2.0
AUTORANGE_S = 3.0
FILM_MEASURE_S = 1.5
MEASURE_OVERHEAD_S = 1.0
COLLISION_MARGIN_M = 0.10e-3
SAFE_TIP_CLEARANCE_M = 1.0e-3                    # a lens change first retracts Z so that the new tip is 1 mm above the sample
PARFOCAL_M = 0.045
OPTICS_TRANSMISSION = 0.5                        # laser power fraction reaching the sample
SIGMA_REL_INTENSITY = 0.0255                     # relative intensity noise of a laser sample (calibrated to the documented repeatability)
PIXEL_Z_NOISE_M = 10.0e-9                        # independent Z noise of every pixel in every frame (engineering; sets the 50x floor in the calibration)
SIGMA_ADD = 1.5e-3                               # additive noise, fraction of full scale
FRAME_Z_JITTER_M = 8.0e-9                        # Z-stage position noise per frame (calibrated with SIGMA_REL_INTENSITY)
AAG_TARGET = 0.85
DOUBLE_SCAN_GAIN = 8.0
VALID_PEAK = 6.0                                 # peak must exceed VALID_PEAK x SIGMA_ADD
PINHOLE_V = 3.83                                 # detector pinhole radius in optical units v = 2 pi NA r / lambda (1 Airy unit diameter; engineering)
AXIAL_X_MAX = 12.0                               # tabulated axial response 0 <= x <= 12, 1/x^2 tail beyond
SIDE_LOBE_MARGIN = 1.5                          # first-peak / film modes: a weaker local maximum counts only above SIDE_LOBE_MARGIN x the axial-response
                                                 # envelope of a stronger local maximum at that distance (its tail, or noise on the tail)
SAME_PEAK_FWHM = 0.85                            # local maxima closer than 0.85 axial FWHM are one peak split by noise
DIFFUSE_COLLECTION = 0.5
Z_SCALE_SD = 2.0e-3                              # hidden Z scale error (1 sigma)
XY_SCALE_SD = 4.0e-3                             # hidden lateral scale error per lens (1 sigma)
Z_REPEAT_M = 3.0e-9                              # per-measurement Z offset repeatability
VIBRATION_M = 30e-9                              # injected floor vibration (Z, 1 sigma per scan line of a laser frame)
FV_TEXTURE_REF_M = 30e-9                         # unresolved roughness giving full focus-variation contrast
FILM_NOISE_REL = 0.003
FILM_NOISE_ABS = 2.0e-4                          # absolute reflectance noise floor of the spectrometer (engineering)
FILM_MIN_SIGNAL = 0.02                           # collected fraction below which the spectrum is too weak to fit (engineering)
FILM_GAIN_MAX = 1.1                              # upper bound of the fitted spectrometer gain (reference-normalised spectrum, engineering)
FILM_FRINGE_SNR = 5.0                            # fitted interference term (peak to peak) must exceed this many noise sigmas (engineering)
FILM_BARE_GAIN = 1.0                             # film fit must beat the bare named substrate by this many noise sigmas (RMS, engineering)
MAX_RECORDS = 60
MAX_EVENTS = 400
MAX_SAMPLES = 20
MAX_Z_STEPS = 4000

# ---------------------------------------------------------------- objectives on the revolver (configuration and data: engineering)
LENSES = [
    dict(slot=0, id='obj_2_5x', mag=2.5, na=0.075, wd_m=0.0088, kind='ring', label='2.5x', modes=()),
    dict(slot=1, id='obj_10x', mag=10.0, na=0.30, wd_m=0.0165, kind='ring', label='10x', modes=('laser_confocal', 'focus_variation', 'film_thickness')),
    dict(slot=2, id='obj_20x', mag=20.0, na=0.46, wd_m=0.0031, kind='plan', label='20x', modes=('laser_confocal', 'focus_variation', 'film_thickness')),
    dict(slot=3, id='obj_50x', mag=50.0, na=0.95, wd_m=0.00035, kind='plan_apo', label='50x', modes=('laser_confocal', 'focus_variation', 'film_thickness')),
    dict(slot=4, id='obj_150x', mag=150.0, na=0.95, wd_m=0.0002, kind='plan_apo', label='150x', modes=('laser_confocal',)),
    dict(slot=5, id='obj_20x_di', mag=20.0, na=0.40, wd_m=0.0047, kind='interference', label='20x DI', modes=('white_light_interferometry',)),
]
MODES = ['laser_confocal', 'focus_variation', 'white_light_interferometry']
PEAK_MODES = ['strongest', 'first', 'film']
QUALITIES = list(QUALITY_LINES)
FAULTS = ['vibration', 'laser_failure', 'stage_disconnected', 'revolver_jam']
FAULT_ERROR = {'laser_failure': 'LASER_FAULT', 'stage_disconnected': 'STAGE_FAULT', 'revolver_jam': 'REVOLVER_FAULT'}


def lens_of(slot):
    return LENSES[int(slot)]


@functools.lru_cache(maxsize=None)
def _axial_table():
    """Plane-mirror axial response of the confocal head with a circular pinhole of radius PINHOLE_V (optical units), normalised to 1 in focus:
    I(x) = int_0^vp |h(v, x)|^2 v dv, h(v, x) = 2 int_0^1 exp(2 pi i x rho^2) J0(v rho) rho d rho (paraxial Debye integral, Wilson & Carlini form),
    x = 2 dz (1 - cos a) / lambda (the on-axis point-detector limit is sinc^2 x, first zero at x = 1).  Returns (x, I, envelope) where the
    envelope is the largest response at x or beyond."""
    rho = np.linspace(0.0, 1.0, 401)
    wr = np.full(rho.size, rho[1]); wr[[0, -1]] /= 2
    v = np.linspace(0.0, PINHOLE_V, 121)
    wv = np.full(v.size, v[1]); wv[[0, -1]] /= 2
    th = (np.arange(64) + 0.5) * math.pi / 64
    J = np.cos(np.multiply.outer(np.outer(v, rho), np.sin(th))).mean(axis=-1)          # J0(v rho) by the midpoint rule of its integral form
    x = np.arange(0.0, AXIAL_X_MAX + 1e-9, 0.01)
    H = (2.0 * J * (rho * wr)[None, :]) @ np.exp(2j * math.pi * np.outer(rho * rho, x))
    I = (np.abs(H) ** 2 * (v * wv)[:, None]).sum(axis=0)
    I = I / I[0]
    env = np.maximum.accumulate(I[::-1])[::-1]
    return x, I, env


def axial_response(x):
    """Normalised confocal plane-mirror response at x = 2 dz (1 - cos a) / lambda (array)."""
    xt, it, _ = _axial_table()
    ax = np.abs(x)
    return np.where(ax <= AXIAL_X_MAX, np.interp(ax, xt, it), it[-1] * (AXIAL_X_MAX / np.maximum(ax, AXIAL_X_MAX)) ** 2)


def axial_envelope(x):
    xt, _, env = _axial_table()
    ax = np.abs(x)
    return np.where(ax <= AXIAL_X_MAX, np.interp(ax, xt, env), env[-1] * (AXIAL_X_MAX / np.maximum(ax, AXIAL_X_MAX)) ** 2)


@functools.lru_cache(maxsize=None)
def axial_fwhm_x():
    xt, it, _ = _axial_table()
    i = int(np.argmax(it < 0.5))
    return float(2.0 * (xt[i - 1] + (0.5 - it[i - 1]) * (xt[i] - xt[i - 1]) / (it[i] - it[i - 1])))


def axial_fwhm_m(na, lam=LASER_WAVELENGTH_M):
    """FWHM (m) of the confocal plane-mirror axial response (finite pinhole) for an objective of numerical aperture na."""
    return axial_fwhm_x() * lam / (2.0 * (1.0 - math.sqrt(1.0 - na * na)))


def fov_m(mag, zoom):
    fx = FOV_CONST_M / (mag * zoom)
    return (fx, FOV_ASPECT * fx)


def total_magnification(mag, zoom):
    return DISPLAY_IMAGE_M / fov_m(mag, zoom)[0]


def confocal_spec_sigma(mag):
    """Documented laser-confocal height repeatability; 150x is not listed (the 50x value is used, same NA 0.95)."""
    return CONFOCAL_REPEAT_M.get(mag, CONFOCAL_REPEAT_M[50.0] if mag > 50.0 else None)


def fv_spec_sigma(mag):
    return FV_REPEAT_M.get(mag)


# ---------------------------------------------------------------- optical-constant template library
_LIB = [None]


def library():
    if _LIB[0] is None:
        L = json.loads(LIBRARY_PATH.read_text())
        grid = np.asarray(L['grid']['wavelength_nm'], float)
        subs = {}
        for s in L['substances']:
            subs[s['substance_id']] = dict(s, _n=np.asarray(s['n'], float), _k=np.asarray(s['k'], float))
        _LIB[0] = dict(meta=L, grid=grid, subs=subs)
    return _LIB[0]


def substance(sid):
    s = library()['subs'].get(sid)
    if s is None:
        raise ActionRejected('UNKNOWN_SUBSTANCE')
    return s


def nk(sid, lam_m):
    """Complex index N = n + i k of a library substance at lam (m); the laser line uses the value tabulated at 404 nm."""
    s = substance(sid)
    if abs(lam_m - LASER_WAVELENGTH_M) < 1e-12:
        return complex(s['laser_404nm']['n'], s['laser_404nm']['k'])
    g = library()['grid']
    x = lam_m * 1e9
    return complex(float(np.interp(x, g, s['_n'])), float(np.interp(x, g, s['_k'])))


def nk_array(sid, lam_nm):
    s = substance(sid)
    g = library()['grid']
    return np.interp(lam_nm, g, s['_n']) + 1j * np.interp(lam_nm, g, s['_k'])


def is_transparent(sid, thickness_m, lam_m=LASER_WAVELENGTH_M):
    """Transparent enough that a buried interface returns light: absorption depth longer than a fifth of the layer thickness."""
    k = nk(sid, lam_m).imag
    if k <= 0.0:
        return True
    depth = lam_m / (4 * math.pi * k)
    return depth > 0.2 * thickness_m


# ---------------------------------------------------------------- Fresnel optics
def r_interface(n1, n2):
    return (n1 - n2) / (n1 + n2)


def incoherent_rta(N1, N2, t, lam):
    """Incoherent reflectance R, transmission into the substrate T and absorption in the layer A of air / layer (N1, thickness t) / N2
    (normal incidence, all multiple reflections summed); t = 0 gives the bare surface of N2.  R + T + A = 1 is a property of the three
    independent expressions (checked by the energy ledger)."""
    if t <= 0.0:
        R = abs(r_interface(1.0, N2)) ** 2
        return R, 1.0 - R, 0.0
    R1 = abs(r_interface(1.0, N1)) ** 2
    R2 = abs(r_interface(N1, N2)) ** 2
    tau = math.exp(-4 * math.pi * N1.imag * t / lam)
    D = 1.0 - R1 * R2 * tau * tau
    R = R1 + (1 - R1) ** 2 * R2 * tau * tau / D
    T = (1 - R1) * (1 - R2) * tau / D
    A = (1 - R1) * (1 - tau) * (1 + R2 * tau) / D
    return R, T, A


def coherent_r(N1, N2, t, lam):
    """Amplitude reflection coefficient of air / thin film (N1, t) / substrate N2 at normal incidence (Airy)."""
    r01, r12 = r_interface(1.0, N1), r_interface(N1, N2)
    ph = np.exp(2j * 2 * np.pi * N1 * t / lam)
    return (r01 + r12 * ph) / (1 + r01 * r12 * ph)


def n_effective(n, na, weight=0.5):
    """Focus-shift factor inside a medium of index n for an objective of numerical aperture na: the peak of a buried interface appears at
    t / n_eff below the top surface (paraxial n_eff = n; weighted over the aperture)."""
    s2 = weight * na * na
    return math.sqrt(max(n * n - s2, 1e-6)) / math.sqrt(max(1.0 - s2, 1e-6))


# ---------------------------------------------------------------- sample surface model
FEATURE_KINDS = ['step', 'grating', 'pillars', 'pits', 'cap', 'groove', 'sine']
N_ROUGH = 256                                    # random-phase components of the roughness field


def _smooth_step(u, e):
    """0 -> 1 across u = 0 with a 10-90 % width e (tanh edge)."""
    return 0.5 * (1.0 + np.tanh(u / max(e / 2.197, 1e-12)))


def feature_height(f, X, Y):
    """Height (m) of one feature at sample coordinates X, Y and the feature mask (1 where the feature material is on top)."""
    k, H, w, p = f['kind'], f['height_m'], f['width_m'], f['pitch_m']
    x0, y0, ext = f['x_m'], f['y_m'], f['extent_m']
    edge = max(min(0.1 * (p if p > 0 else w), 0.3e-6), 0.05e-6)
    inside = _smooth_step(ext / 2 - np.abs(X - x0), edge) * _smooth_step(ext / 2 - np.abs(Y - y0), edge)
    if k == 'step':
        prof = _smooth_step(X - x0, max(w, 0.05e-6)) * _smooth_step(x0 + ext - X, max(w, 0.05e-6)) * _smooth_step(ext / 2 - np.abs(Y - y0), edge)
        h = H * prof
        return h, prof > 0.5
    if k == 'grating':
        ph = np.mod(X - x0, p)
        prof = _smooth_step(ph, edge) * _smooth_step(w - ph, edge) + _smooth_step(ph - p, edge)
        h = H * prof * inside
        return h, (prof * inside) > 0.5
    if k in ('pillars', 'pits'):
        dx = np.mod(X - x0 + p / 2, p) - p / 2
        dy = np.mod(Y - y0 + p / 2, p) - p / 2
        r = np.hypot(dx, dy)
        prof = _smooth_step(w / 2 - r, edge) * inside
        h = (abs(H) if k == 'pillars' else -abs(H)) * prof
        return h, (prof > 0.5) if k == 'pillars' else np.zeros_like(prof, dtype=bool)
    if k == 'cap':
        R = (w * w / 4 + H * H) / (2 * abs(H)) if H else 1.0
        r = np.hypot(X - x0, Y - y0)
        z = np.sqrt(np.maximum(R * R - r * r, 0.0)) - (R - abs(H))
        h = np.sign(H) * np.maximum(z, 0.0) * (r < w / 2)
        return h, (r < w / 2) & (H > 0)
    if k == 'groove':
        u = X - x0
        along = _smooth_step(ext / 2 - np.abs(Y - y0), edge)
        g = -abs(H) * np.exp(-4 * math.log(2) * u * u / (w * w))
        pile = 0.12 * abs(H) * np.exp(-4 * math.log(2) * (np.abs(u) - 0.8 * w) ** 2 / (0.3 * w) ** 2)
        return (g + pile) * along, np.zeros(np.shape(X), dtype=bool)
    if k == 'sine':
        h = 0.5 * H * np.sin(2 * math.pi * (X - x0) / p) * inside
        return h, np.zeros(np.shape(X), dtype=bool)
    raise ValueError(k)


def rough_components(sample_id, seed, sq, ell):
    """Deterministic random-phase components (kx, ky, amplitude, phase) of an isotropic Gaussian-correlated roughness field."""
    h = int(hashlib.sha256(f'{sample_id}|{seed}'.encode()).hexdigest()[:12], 16)
    rng = np.random.default_rng(h)
    kx = rng.normal(0.0, math.sqrt(2.0) / ell, N_ROUGH)
    ky = rng.normal(0.0, math.sqrt(2.0) / ell, N_ROUGH)
    ph = rng.uniform(0, 2 * math.pi, N_ROUGH)
    amp = np.full(N_ROUGH, sq * math.sqrt(2.0 / N_ROUGH))
    return kx, ky, amp, ph


def surface_map(s, X, Y, dx, dy, psf_sigma, seed):
    """Surface of sample s on a pixel grid (sample coordinates X, Y; pixel pitch dx, dy; optical PSF sigma).
    Returns dict(h, top, below, t_buried, slope, sigma_sub, edge): height above the substrate reference (m, incl. film, features, resolved
    roughness and tilt), material ids on top and under a transparent layer, the transparent thickness above the buried interface, the local
    slope (rad), the unresolved roughness (m) and a mask of pixels at feature edges."""
    base = np.zeros_like(X)
    top = np.full(X.shape, s['substrate_substance_id'], dtype=object)
    below = np.full(X.shape, None, dtype=object)
    t_b = np.zeros_like(X)
    for f in s['features']:
        h, mask = feature_height(f, X, Y)
        base = base + h
        if f['kind'] in ('step', 'grating', 'pillars', 'cap') and f['substance_id'] and f['height_m'] > 0:
            m = mask & (h > 0)
            top = np.where(m, f['substance_id'], top)
            tr = is_transparent(f['substance_id'], f['height_m'])
            if tr:
                below = np.where(m, s['substrate_substance_id'], below)
                t_b = np.where(m, h, t_b)
    rough = s['roughness']
    sig_sub = 0.0
    if rough['sq_m'] > 0:
        kx, ky, amp, ph = rough_components(s['sample_id'], seed, rough['sq_m'], rough['correlation_length_m'])
        att = np.sinc(kx * dx / (2 * math.pi)) * np.sinc(ky * dy / (2 * math.pi)) * np.exp(-(kx * kx + ky * ky) * psf_sigma * psf_sigma / 2)
        a_res = amp * att
        sig_sub = float(math.sqrt(max(0.0, float(np.sum(amp * amp - a_res * a_res)) / 2)))
        arg = np.multiply.outer(X, kx) + np.multiply.outer(Y, ky) + ph
        base = base + np.cos(arg) @ a_res
    base = base + X * math.tan(s['tilt_x_rad']) + Y * math.tan(s['tilt_y_rad'])
    film = s['film']
    h = base
    if film:
        h = base + film['thickness_m']
        tr = is_transparent(film['substance_id'], film['thickness_m'])
        if tr:
            below = np.where(np.ones(X.shape, dtype=bool), top, below)
            t_b = np.full(X.shape, film['thickness_m'])
        else:
            below = np.full(X.shape, None, dtype=object); t_b = np.zeros_like(X)
        top = np.full(X.shape, film['substance_id'], dtype=object)
    if min(X.shape) < 2:                         # single points (focus, truth readings): no local slope
        return dict(h=h, top=top, below=below, t_buried=t_b, slope=np.zeros_like(h), sigma_sub=sig_sub, edge=np.zeros(X.shape, dtype=bool))
    gy, gx = np.gradient(h, dy, dx)
    slope = np.arctan(np.hypot(gx, gy))
    edge = np.zeros(X.shape, dtype=bool)
    if s['features']:
        g2y, g2x = np.gradient(base - (X * math.tan(s['tilt_x_rad']) + Y * math.tan(s['tilt_y_rad'])), dy, dx)
        edge = np.hypot(g2x, g2y) > 0.02
    return dict(h=h, top=top, below=below, t_buried=t_b, slope=slope, sigma_sub=sig_sub, edge=edge)


def sample_extremes(s):
    """Highest and lowest point of a sample above its substrate reference (features, film, 3 sigma roughness and tilt), m."""
    hi = lo = 0.0
    for f in s['features']:
        if f['kind'] == 'groove':
            lo = min(lo, -abs(f['height_m']))
            hi = max(hi, 0.12 * abs(f['height_m']))
        elif f['kind'] == 'sine':
            hi = max(hi, abs(f['height_m']) / 2); lo = min(lo, -abs(f['height_m']) / 2)
        elif f['kind'] == 'pits':
            lo = min(lo, -abs(f['height_m']))
        else:
            hi = max(hi, max(f['height_m'], 0.0)); lo = min(lo, min(f['height_m'], 0.0))
    r = 3 * s['roughness']['sq_m']
    t = s['film']['thickness_m'] if s['film'] else 0.0
    half = 0.5 * math.hypot(s['size_x_m'], s['size_y_m'])
    tilt = half * math.tan(max(abs(s['tilt_x_rad']), abs(s['tilt_y_rad'])))
    return hi + r + t + tilt, lo - r - tilt


# ---------------------------------------------------------------- capabilities and state slots
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SAMPLE_ID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.-]+$')
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, relative_humidity=0.45, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 283.15, 308.15), mains_voltage_v=number('V', 0.0, 264.0), relative_humidity=number('1', 0.0, 1.0), noise_seed=INT))
FEATURE = obj(dict(kind=enum(FEATURE_KINDS), substance_id=nullable(dict(type='string', minLength=1, maxLength=40)), height_m=number('m', -5.0e-4, 5.0e-4),
                   width_m=number('m', 5.0e-8, 0.05), pitch_m=number('m', 0.0, 0.05), x_m=number('m', -0.05, 0.05), y_m=number('m', -0.05, 0.05),
                   extent_m=number('m', 1.0e-6, 0.1)))
FILM = obj(dict(substance_id=dict(type='string', minLength=1, maxLength=40), thickness_m=number('m', 5.0e-9, 2.0e-5)))
ROUGH = obj(dict(sq_m=number('m', 0.0, 5.0e-6), correlation_length_m=number('m', 1.0e-7, 5.0e-4)))
SAMPLE = obj(dict(sample_id=SAMPLE_ID, substrate_substance_id=dict(type='string', minLength=1, maxLength=40), thickness_m=number('m', 1.0e-4, 0.07),
                  size_x_m=number('m', 1.0e-3, 0.1), size_y_m=number('m', 1.0e-3, 0.1), features=array(FEATURE, 0, 4), film=nullable(FILM), roughness=ROUGH,
                  tilt_x_rad=number('rad', -0.03, 0.03), tilt_y_rad=number('rad', -0.03, 0.03)))
SAMPLE_EXAMPLE = dict(sample_id='sio2_step_on_si', substrate_substance_id='si', thickness_m=5.25e-4, size_x_m=0.010, size_y_m=0.010,
                      features=[dict(kind='step', substance_id='sio2_film', height_m=500e-9, width_m=0.5e-6, pitch_m=0.0, x_m=0.0, y_m=0.0, extent_m=0.004)],
                      film=None, roughness=dict(sq_m=0.3e-9, correlation_length_m=2.0e-6), tilt_x_rad=0.0, tilt_y_rad=0.0)
OPERATION = obj(dict(operation_id=TEXT))
STATUS = enum(['running', 'completed', 'failed', 'cancelled'])
SETTINGS = obj(dict(mode=enum(MODES), quality=enum(QUALITIES), pitch_m=nullable(number('m', 1.0e-9, 1.0e-4)), gain=nullable(number('1', 0.05, 1000.0)),
                    double_scan=BOOL, peak_mode=enum(PEAK_MODES), film_refractive_index=nullable(number('1', 1.0, 4.0))))
SETTINGS_EXAMPLE = dict(mode='laser_confocal', quality='standard', pitch_m=None, gain=None, double_scan=False, peak_mode='strongest', film_refractive_index=None)
MAP = array(array(nullable(number('m')), 1, 512), 1, 512)
RECORD = obj(dict(record_id=TEXT, kind=enum(['height_map', 'stitched_map', 'film_thickness']), status=STATUS, started_s=number('s'), finished_s=nullable(number('s')),
                  reason=nullable(TEXT), settings=nullable(SETTINGS), objective=nullable(TEXT), magnification=nullable(number('1')), zoom=nullable(number('1')),
                  stage_xy_m=array(number('m'), 2, 2), fov_m=nullable(array(number('m'), 2, 2)), grid=nullable(array(INT, 2, 2)),
                  pixel_pitch_m=nullable(array(number('m'), 2, 2)), z_range_m=nullable(array(number('m'), 2, 2)), pitch_m=nullable(number('m')), n_frames=INT,
                  gain=nullable(number('1')), height_m=nullable(MAP), intensity=nullable(array(array(nullable(number('1')), 1, 512), 1, 512)),
                  film_thickness_m=nullable(MAP), valid_fraction=nullable(number('1', 0.0, 1.0)), duration_s=number('s'),
                  film=nullable(obj(dict(thickness_m=nullable(number('m')), fit_residual=nullable(number('1')), film_substance_id=TEXT, substrate_substance_id=TEXT,
                                         wavelength_m=array(number('m'), 0, 400), reflectance=array(number('1'), 0, 400), fit=array(number('1'), 0, 400),
                                         in_range=BOOL, collected_fraction=number('1', 0.0, 1.0), collected_fraction_interface=nullable(number('1', 0.0, 1.0)),
                                         fringe_visibility=number('1', 0.0, 1.0), fit_collected_fractions=array(number('1', 0.0, FILM_GAIN_MAX), 2, 2),
                                         fringe_to_noise=number('1', 0.0), gain_over_bare_substrate=number('1', 0.0)))),
                  template_source=obj(dict(library=TEXT, substances=array(TEXT, 0, 32))), note_zh=TEXT))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'stage', 'head', 'measurement', 'calibration', 'sample', 'error', 'fault']), code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['laser_on_s', 'laser_energy_j', 'energy_to_sample_j', 'energy_reflected_j', 'energy_absorbed_j', 'energy_transmitted_j', 'energy_residual_j',
               'frames', 'stage_travel_m', 'z_travel_m', 'lift_travel_m', 'revolver_steps']
LEDGER = obj({k: number('J' if k.startswith('energy') or k == 'laser_energy_j' else 's' if k.endswith('_s') else 'm' if k.endswith('_m') else '1') for k in LEDGER_KEYS})
REGION = nullable(obj(dict(x0_m=number('m', -0.2, 0.2), y0_m=number('m', -0.2, 0.2), x1_m=number('m', -0.2, 0.2), y1_m=number('m', -0.2, 0.2))))
LEVEL = enum(['none', 'plane'])
ROUGH_OUT = obj(dict(record_id=TEXT, leveling=LEVEL, valid_pixels=INT, areal=obj({k: nullable(number(u)) for k, u in
                                                                                    [('sa_m', 'm'), ('sq_m', 'm'), ('sz_m', 'm'), ('sp_m', 'm'), ('sv_m', 'm'),
                                                                                     ('ssk', '1'), ('sku', '1'), ('sdq', '1'), ('sdr', '1')]}),
                     profile=obj({k: nullable(number(u)) for k, u in [('ra_m', 'm'), ('rq_m', 'm'), ('rz_m', 'm'), ('rp_m', 'm'), ('rv_m', 'm'), ('rsk', '1'), ('rku', '1'),
                                                                   ('lambda_c_m', 'm'), ('evaluation_length_m', 'm')]}), note_zh=TEXT))
STEP_OUT = obj(dict(record_id=TEXT, axis=enum(['x', 'y']), step_height_m=nullable(number('m')), std_error_m=nullable(number('m')), lower_level_m=nullable(number('m')),
                    upper_level_m=nullable(number('m')), method=TEXT, n_lower=INT, n_upper=INT, note_zh=TEXT))
cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['controller_body'], '宿主声明实验室条件（室温、电源电压、相对湿度、噪声种子）', 'evaluator', 'configuration')
cap('sample.provide', SAMPLE, obj(dict(sample_id=TEXT, location=TEXT)), SAMPLE_EXAMPLE, ['sample_plate'],
    '宿主在实验台上提供一个样品（衬底、表面特征、透明膜、粗糙度、倾斜；材料来自光学常数模板库）', 'evaluator', 'configuration')
cap('sample.place', obj(dict(sample_id=SAMPLE_ID, x_m=number('m', -0.04, 0.04), y_m=number('m', -0.04, 0.04))), obj(dict(sample_id=TEXT, x_m=number('m'), y_m=number('m'))),
    dict(sample_id='sio2_step_on_si', x_m=0.0, y_m=0.0), ['sample_plate', 'sample_clips'], '把实验台上的样品放到圆形样品台上（宿主/机器人抽象；样品中心相对台面中心的位置）',
    'agent', 'robot_skill')
cap('sample.remove', obj(dict(sample_id=SAMPLE_ID)), obj(dict(sample_id=TEXT)), dict(sample_id='sio2_step_on_si'), ['sample_plate'], '把样品从样品台取回实验台（宿主/机器人抽象）',
    'agent', 'robot_skill')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL, state=TEXT)), dict(enabled=True), ['controller_body', 'head_shell', 'stage_motor_box'],
    '控制器电源（同时给测量头与电动台供电）；上电后启动，测量头Z台回原点')
cap('power.press_button', obj({}), obj(dict(enabled=BOOL, state=TEXT)), {}, ['button_power', 'controller_body', 'head_shell', 'stage_motor_box'],
    '按控制器正面电源键（瞬时键，按一次切换通断；与power.set同一上电/断电过程）')
cap('system.acknowledge', obj({}), obj(dict(cleared=nullable(TEXT))), {}, ['controller_body'], '确认已排除原因的锁存错误')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 8))), dict(fault='vibration', active=True), ['controller_body'],
    '注入或清除故障（评测者）：地面振动、激光故障、电动台电缆断开、转盘卡住', 'evaluator', 'configuration')
cap('stage.move', obj(dict(x_m=number('m', -0.05, 0.05), y_m=number('m', -0.05, 0.05))), obj(dict(target_m=array(number('m'), 2, 2), duration_s=number('s'))),
    dict(x_m=0.01, y_m=-0.005), ['stage_x', 'stage_y', 'stage_motor_box'], '电动XY台移到目标位置（±50 mm）')
cap('stage.go_to', obj(dict(position=enum(['center', 'load', 'sample_center']))), obj(dict(target_m=array(number('m'), 2, 2), duration_s=number('s'))),
    dict(position='sample_center'), ['stage_x', 'stage_y'], '电动XY台移到命名位置（中心、前方装样位、样品中心）')
cap('stage.set_lift', obj(dict(lift_m=number('m', LIFT_RANGE_M[0], LIFT_RANGE_M[1]))), obj(dict(target_m=number('m'), duration_s=number('s'))), dict(lift_m=-0.010),
    ['focus_knob', 'stage_lift'], '手动转动粗/微调旋钮升降载物台（放入较高样品；不需要通电）', 'agent', 'device_command')
cap('lens.select', obj(dict(slot=integer(0, 5))), obj(dict(objective=TEXT, magnification=number('1'), duration_s=number('s'), z_retract_m=number('m'))), dict(slot=3),
    ['revolver', 'z_stage'] + [L['id'] for L in LENSES], '电动转盘切换物镜；新物镜可能碰样品时先抬起Z')
cap('zoom.set', obj(dict(zoom=number('1', ZOOM_RANGE[0], ZOOM_RANGE[1]))), obj(dict(zoom=number('1'), fov_m=array(number('m'), 2, 2))), dict(zoom=2.0), ['head_shell'],
    '光学变焦1–8×（改变视场与总倍率）')
cap('focus.move', obj(dict(z_m=number('m', HEAD_Z_RANGE_M[0], HEAD_Z_RANGE_M[1]))), obj(dict(target_m=number('m'), duration_s=number('s'))), dict(z_m=0.020), ['z_stage'],
    '测量头Z台移到目标位置（焦面随之下移；碰撞保护）')
cap('focus.auto', obj({}), OPERATION, {}, ['z_stage', 'head_shell'], '自动对焦：在当前位置附近扫描找到视场中心的表面并停在焦面')
cap('measurement.configure', SETTINGS, obj(dict(settings=SETTINGS)), SETTINGS_EXAMPLE, ['head_shell', 'controller_body'],
    '测量设置：成像原理（激光共焦/焦点变化/白光干涉）、质量（扫描行数）、Z间距、增益（自动AAG或手动）、双重扫描、峰模式（最强/首峰/膜）、膜折射率')
cap('measurement.set_z_range', obj(dict(upper_z_m=number('m', HEAD_Z_RANGE_M[0], HEAD_Z_RANGE_M[1]), lower_z_m=number('m', HEAD_Z_RANGE_M[0], HEAD_Z_RANGE_M[1]))),
    obj(dict(z_range_m=array(number('m'), 2, 2))), dict(upper_z_m=0.030, lower_z_m=0.0302), ['z_stage'], '手动设定扫描上限与下限（测量头Z坐标，上限的焦面更高）')
cap('measurement.auto_range', obj({}), OPERATION, {}, ['z_stage', 'head_shell'], '自动上下限：快速预扫描找出视场内最高与最低表面并加余量')
cap('measurement.start', obj({}), OPERATION, {}, ['head_shell', 'z_stage', 'controller_body'] + [L['id'] for L in LENSES], '测量当前视场的三维高度图（按当前成像原理与设置）')
cap('measurement.stitch', obj(dict(nx=integer(1, 5), ny=integer(1, 5), overlap=number('1', 0.05, 0.5))), OPERATION, dict(nx=3, ny=2, overlap=0.2),
    ['head_shell', 'z_stage', 'stage_x', 'stage_y'], '拼接测量：电动台按网格移动逐视场测量并拼成一张高度图')
cap('film.measure', obj(dict(film_substance_id=dict(type='string', minLength=1, maxLength=40), substrate_substance_id=dict(type='string', minLength=1, maxLength=40))),
    OPERATION, dict(film_substance_id='sio2_film', substrate_substance_id='si'), ['head_shell'], '光谱干涉膜厚测量（视场中心；按操作者指定的膜与衬底材料拟合）')
cap('calibration.z_scale', obj(dict(record_id=TEXT, certified_height_m=number('m', 1.0e-8, 1.0e-3), axis=enum(['x', 'y']), lower=array(number('m', -0.2, 0.2), 2, 2),
                                    upper=array(number('m', -0.2, 0.2), 2, 2))),
    obj(dict(factor=number('1'), measured_height_m=number('m'), calibration_id=TEXT)),
    dict(record_id='HM0001', certified_height_m=1.803e-6, axis='x', lower=[-150e-6, -20e-6], upper=[20e-6, 150e-6]), ['z_stage', 'controller_body'],
    'Z标尺校准：用台阶高度标准片的测量记录与证书值计算比例修正（此后的高度乘以修正系数）')
cap('calibration.xy_scale', obj(dict(record_id=TEXT, certified_pitch_m=number('m', 1.0e-6, 1.0e-2), axis=enum(['x', 'y']))),
    obj(dict(factor=number('1'), measured_pitch_m=number('m'), objective=TEXT, calibration_id=TEXT)),
    dict(record_id='HM0001', certified_pitch_m=10.0e-6, axis='x'), ['head_shell', 'controller_body'], '横向标尺校准：用节距标准（光栅）测量记录与证书值计算当前物镜的横向修正')
cap('measurement.result', obj(dict(record_id=TEXT)), RECORD, dict(record_id='HM0001'), ['controller_body'], '读取测量记录（高度图、强度、设置、膜厚）', 'agent', 'query')
cap('data.roughness', obj(dict(record_id=TEXT, region=REGION, leveling=LEVEL, s_filter_m=nullable(number('m', 1.0e-8, 1.0e-2)), l_filter_m=nullable(number('m', 1.0e-7, 1.0)),
                               profile_axis=enum(['x', 'y']), lambda_c_m=nullable(number('m', 1.0e-7, 1.0)))), ROUGH_OUT,
    dict(record_id='HM0001', region=None, leveling='plane', s_filter_m=None, l_filter_m=None, profile_axis='x', lambda_c_m=None), ['controller_body'],
    '粗糙度：ISO 25178面参数（Sa、Sq、Sz、Sp、Sv、Ssk、Sku、Sdq、Sdr）与ISO 21920轮廓参数（中心线；Ra、Rq、Rz等，高斯滤波λc）', 'agent', 'analysis')
cap('data.step_height', obj(dict(record_id=TEXT, axis=enum(['x', 'y']), lower=nullable(array(number('m', -0.2, 0.2), 2, 2)), upper=nullable(array(number('m', -0.2, 0.2), 2, 2)),
                                 leveling=LEVEL)), STEP_OUT, dict(record_id='HM0001', axis='x', lower=[-150e-6, -20e-6], upper=[20e-6, 150e-6], leveling='plane'),
    ['controller_body'], '台阶高度：ISO 5436-1 A1型（上下两台面带共斜率双平行线拟合）或直方图双峰法（未给区域时）', 'agent', 'analysis')
cap('data.profile', obj(dict(record_id=TEXT, start_m=array(number('m', -0.2, 0.2), 2, 2), end_m=array(number('m', -0.2, 0.2), 2, 2), points=integer(2, 512))),
    obj(dict(distance_m=array(number('m'), 2, 512), height_m=array(nullable(number('m')), 2, 512))),
    dict(record_id='HM0001', start_m=[-100e-6, 0.0], end_m=[100e-6, 0.0], points=128), ['controller_body'], '沿直线提取高度轮廓（双线性插值）', 'agent', 'analysis')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, 200))), {}, ['controller_body'], '列出测量记录', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, 500), dropped=INT)), dict(since_s=0.0), ['controller_body'], '读取事件与报警',
    'agent', 'query')
cap('ledger.read', obj({}), LEDGER, {}, ['controller_body'], '能量与使用账本（激光能量去向、帧数、行程）', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, library_sha256=TEXT, substances=INT, license=TEXT)), {}, ['controller_body'], '模板库来源', 'agent', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['controller_body'], '宿主推进仿真时间', 'evaluator', 'simulation')
UNSUPPORTED = {
    'hardware.connect': '没有真实VK-X3000/VK-X3100的硬件或VK Viewer软件接口',
    'robot.handle_sample': '机器人接触装样（抓取、放置、压片）未建模；sample.place为宿主抽象',
    'head.red_laser_vk_x3050': 'VK-X3050（661 nm）测量头不在本配置',
    'stage.manual_xy': '手动XY台（70×70 mm）不在本配置（本配置为VK-D3电动台）',
    'lens.install_objective': '更换转盘上的物镜（安装其它物镜，如5×）未建模；物镜配置固定',
    'export.vk_file': 'VK专有数据文件导出与CAD比对未建模',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['controller_body'], _why, 'agent', 'device_command')


SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


XY = array(number('m'), 2, 2)
slot('clock.time_s', number('s'), 's', 'derived', 'controller_body', '仿真时钟')
slot('plant.configured', BOOL, '1', 'setpoint', 'controller_body', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'setpoint', 'controller_body', '控制器电源（同时给测量头与电动台供电）')
slot('system.state', enum(['off', 'booting', 'ready']), '1', 'derived', 'controller_body', '系统状态（断电/启动中/就绪）')
slot('system.error_code', nullable(TEXT), '1', 'derived', 'controller_body', '锁存的错误代码')
slot('stage.x_m', number('m'), 'm', 'derived', 'stage_x', 'XY台X位置（过程状态；场景X滑台关节镜像）')
slot('stage.y_m', number('m'), 'm', 'derived', 'stage_y', 'XY台Y位置（过程状态；场景Y滑台关节镜像）')
slot('stage.x_indicated_m', nullable(number('m')), 'm', 'sensor', 'stage_x', 'X位置读数（0.1 µm显示；断电为空）')
slot('stage.y_indicated_m', nullable(number('m')), 'm', 'sensor', 'stage_y', 'Y位置读数（0.1 µm显示；断电为空）')
slot('stage.target_m', XY, 'm', 'derived', 'stage_x', 'XY台控制器当前目标（移动命令，或拼接程序逐视场写入）')
slot('stage.moving', BOOL, '1', 'derived', 'stage_x', 'XY台在移动')
slot('stage.position_label', nullable(TEXT), '1', 'derived', 'stage_x', '命名位置（到位后）')
slot('stage.lift_m', number('m'), 'm', 'derived', 'stage_lift', '载物台升降位置（0为最高位，负值为下降；手动旋钮）')
slot('stage.lift_target_m', number('m'), 'm', 'setpoint', 'focus_knob', '载物台升降目标（手动旋钮）')
slot('stage.focus_drive_rad', number('rad'), 'rad', 'derived', 'focus_knob', '粗调旋钮转角（=−升降/(4 mm)×2π；场景旋钮关节镜像）')
slot('head.z_m', number('m'), 'm', 'derived', 'z_stage', '测量头Z台位置（向下为正；焦面高度=Z原点焦面−Z；场景Z台关节镜像）')
slot('head.z_target_m', number('m'), 'm', 'derived', 'z_stage', '测量头Z台控制器当前目标（移动命令、换物镜抬起，或测量/对焦程序逐阶段写入）')
slot('head.focus_z_indicated_m', nullable(number('m')), 'm', 'sensor', 'z_stage', 'Z位置读数（显示器，0.1 µm；断电为空）')
slot('head.z_moving', BOOL, '1', 'derived', 'z_stage', 'Z台在移动')
slot('head.revolver_rad', number('rad'), 'rad', 'derived', 'revolver', '物镜转盘转角（场景转盘关节镜像）')
slot('head.lens_slot', integer(0, 5), '1', 'setpoint', 'revolver', '选定的物镜档位（0–5）')
slot('head.objective', nullable(TEXT), '1', 'derived', 'revolver', '光轴上的物镜（转盘转动中为空）')
slot('head.magnification', nullable(number('1')), '1', 'derived', 'revolver', '光轴上物镜的倍率（转盘转动中为空）')
slot('head.revolver_moving', BOOL, '1', 'derived', 'revolver', '转盘在转动')
slot('optics.zoom', number('1'), '1', 'setpoint', 'head_shell', '光学变焦倍数')
slot('optics.fov_m', nullable(XY), 'm', 'derived', 'head_shell', '视场宽×高（名义值）')
slot('optics.total_magnification', nullable(number('1')), '1', 'derived', 'head_shell', '23英寸全屏显示时的总倍率')
slot('measurement.settings', SETTINGS, '1', 'setpoint', 'controller_body', '测量设置')
slot('measurement.z_range_m', nullable(XY), 'm', 'derived', 'z_stage', '扫描上限与下限（Z坐标；上限焦面更高）：手动设定值或自动上下限的结果')
slot('measurement.id', nullable(TEXT), '1', 'derived', 'controller_body', '进行中的测量记录ID')
slot('measurement.kind', nullable(TEXT), '1', 'derived', 'controller_body', '进行中的操作种类')
slot('measurement.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'controller_body', '进行中操作的进度')
slot('measurement.remaining_s', nullable(number('s', 0.0)), 's', 'derived', 'controller_body', '进行中操作的剩余时间')
slot('measurement.started_by_other_agent', BOOL, '1', 'derived', 'controller_body', '进行中的操作属于另一个agent')
slot('laser.on', BOOL, '1', 'derived', 'head_shell', '激光扫描中')
slot('result.last_record_id', nullable(TEXT), '1', 'derived', 'controller_body', '最近完成的记录ID（agent只看到自己或宿主创建的记录）')
slot('result.last_value', nullable(number('m')), 'm', 'derived', 'controller_body', '最近一次测量或分析的代表值（台阶高度、Sa、膜厚或视场高度范围）')
slot('calibration.z_scale', obj(dict(factor=number('1'), calibration_id=nullable(TEXT), calibrated_s=nullable(number('s')))), '1', 'setpoint', 'controller_body',
     'Z标尺修正系数（此后测量的高度乘以该系数）')
slot('calibration.xy_scale', obj({L['id']: number('1') for L in LENSES}), '1', 'setpoint', 'controller_body', '各物镜横向标尺修正系数')
slot('sample.bench_ids', array(TEXT, 0, MAX_SAMPLES), '1', 'derived', 'sample_plate', '实验台上的样品')
slot('sample.on_stage', nullable(obj(dict(sample_id=TEXT, x_m=number('m'), y_m=number('m')))), '1', 'derived', 'sample_plate', '样品台上的样品及其中心位置')
slot('panel.head_lamp', BOOL, '1', 'derived', 'lamp_head', '测量头正面蓝色灯条（就绪时亮）')
slot('panel.stage_lamp', BOOL, '1', 'derived', 'lamp_stage', '电动台POWER灯')
slot('panel.controller_lamp', BOOL, '1', 'derived', 'lamp_controller', '控制器电源灯')
slot('panel.laser_lamp', BOOL, '1', 'derived', 'lamp_laser', '激光发射指示灯')
slot('truth.surface_z_on_axis_m', nullable(number('m')), 'm', 'process_truth', 'sample_plate', '光轴上样品表面的真实绝对高度（台面坐标）', 'evaluator')
slot('truth.focus_error_m', nullable(number('m')), 'm', 'process_truth', 'z_stage', '焦面减表面真实高度', 'evaluator')
slot('truth.z_scale_error', number('1'), '1', 'process_truth', 'z_stage', '仪器真实Z标尺误差', 'evaluator')
slot('truth.xy_scale_error', obj({L['id']: number('1') for L in LENSES}), '1', 'process_truth', 'head_shell', '各物镜真实横向标尺误差', 'evaluator')
slot('truth.samples', {'type': 'object'}, '1', 'process_truth', 'sample_plate', '样品真实定义与位置', 'evaluator')
slot('truth.faults', array(TEXT, 0, 8), '1', 'process_truth', 'controller_body', '注入的故障', 'evaluator')


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


def _axis_move(pos, target, speed, dt):
    d = target - pos
    step = speed * dt
    return target if abs(d) <= step else pos + math.copysign(step, d)


def _nan_to_none(a):
    return [[None if not math.isfinite(v) else float(v) for v in row] for row in np.asarray(a, float)]


def _none_to_nan(m):
    return np.array([[np.nan if v is None else v for v in row] for row in m], float)


class KeyenceVkx3000Runtime:
    """VK-X3000 + VK-X3100 on the VK-D3 base: power, motorized XY stage, manual stage lift, head Z stage, revolver, zoom, laser confocal /
    focus variation / white light interferometry height maps, spectral film thickness, stitching, calibration and analysis (SI units)."""

    def __init__(self, instance_id='vk_x3000_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.revision = 0
        self.plant = None
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.error = None
        self.faults = set()
        self.fault_log = []                               # (time, fault, active): vibration intervals for running scans
        self.stage = dict(x=0.0, y=0.0)
        self.stage_target = dict(x=0.0, y=0.0)
        self.position_label = 'center'
        self.lift, self.lift_target = 0.0, 0.0
        self.z, self.z_target = 0.0, 0.0
        self.z_speed = HEAD_Z_SPEED_M_S
        self.rev, self.rev_target = 0.0, 0.0
        self.slot = 0
        self.zoom = 1.0
        self.settings = dict(SETTINGS_EXAMPLE)
        self.z_range = None
        self.samples = {}
        self.on_stage = None
        self.records = {}
        self.seq = dict(hm=0, st=0, ft=0, op=0, ca=0)
        self.op = None
        self.operations = {}
        self.head_queue = []                              # sequential head motions of a lens change: ('z', target) / ('rev', target)
        self.events, self.events_dropped = [], 0
        self.cal = dict(z=dict(factor=1.0, calibration_id=None, calibrated_s=None), xy={L['id']: 1.0 for L in LENSES})
        self.truth = dict(z_scale=0.0, xy_scale={L['id']: 0.0 for L in LENSES}, film_lambda_scale=0.0)
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self.last_value = None
        self.last_value_rid = None                        # record the displayed result comes from (agent filtering)
        self.used_substances = set()
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
        return KeyenceVkx3000Session(self, name)

    def _event(self, kind, code, message, rid=None, t=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t if t is None else t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _lens(self):
        return LENSES[self.slot]

    def _revolver_settled(self):
        return abs(self.rev - self.rev_target) < 1e-12 and not any(q[0] == 'rev' for q in self.head_queue)

    def _active_lens(self):
        """Objective on the optical axis (from the revolver angle), or None while the axis is between two holes."""
        step = 2 * math.pi / REVOLVER_HOLES
        k = int(round(self.rev / step))
        if abs(self.rev - k * step) > 1e-9:
            return None
        return LENSES[k % REVOLVER_HOLES]

    def _guard_lens(self):
        """Objective whose tip is lowest near the optical axis (collision checks): the one on the axis, or the lower-tipped of the two
        neighbours when the revolver stopped between two holes."""
        lens = self._active_lens()
        if lens is not None:
            return lens
        step = 2 * math.pi / REVOLVER_HOLES
        k = int(math.floor(self.rev / step))
        a, b = LENSES[k % REVOLVER_HOLES], LENSES[(k + 1) % REVOLVER_HOLES]
        return a if a['wd_m'] <= b['wd_m'] else b

    def _stage_moving(self):
        return any(abs(self.stage[a] - self.stage_target[a]) > 1e-12 for a in 'xy')

    def _z_moving(self):
        return abs(self.z - self.z_target) > 1e-12 or any(q[0] == 'z' for q in self.head_queue)

    def _laser_on(self):
        o = self.op
        return bool(o and self.power and o.get('phase') in ('scan', 'prescan') and o.get('laser'))

    def _quant(self, v, res=1e-7):
        return round(v / res) * res

    # ---------------------------------------------------------------- sample geometry in the instrument
    def _sample_top_z(self, lift=None):
        """Highest point of the sample on the stage (or the plate) in absolute bench coordinates; with the manual lift still rising, at its target
        (collision checks must hold for the whole motion)."""
        base = SAMPLE_PLATE_Z + (max(self.lift, self.lift_target) if lift is None else lift)
        if self.on_stage is None:
            return base
        s = self.samples[self.on_stage]
        return base + s['thickness_m'] + sample_extremes(s)[0]

    def _sz(self):
        return 1.0 + self.truth['z_scale']              # true Z displacement per reported Z coordinate (hidden scale error)

    def _focus_true(self, z):
        return FOCUS_Z0 - z * self._sz()

    def _z_for_focus(self, zf):
        return (FOCUS_Z0 - zf) / self._sz()

    def _tip_z(self, lens, z):
        return self._focus_true(z) + lens['wd_m']

    def _axis_in_sample(self):
        s = self.samples[self.on_stage]
        return (-self.stage['x'] - s['x_m'], -self.stage['y'] - s['y_m'])

    def _surface_z_at(self, xs, ys):
        s = self.samples[self.on_stage]
        X, Y = np.array([[xs]]), np.array([[ys]])
        m = surface_map(s, X, Y, 1e-7, 1e-7, 0.0, self._seed())
        inside = abs(xs) <= s['size_x_m'] / 2 and abs(ys) <= s['size_y_m'] / 2
        return (SAMPLE_PLATE_Z + self.lift + s['thickness_m'] + float(m['h'][0, 0])) if inside else (SAMPLE_PLATE_Z + self.lift)

    def _seed(self):
        return self.plant['noise_seed'] if self.plant else 0

    # ---------------------------------------------------------------- read
    def read(self, visibility='agent'):
        on = self.power
        lens = self._active_lens()
        o = self.op
        prog = rem = None
        if o is not None:
            prog = min(1.0, max(0.0, (self.t - o['t0']) / o['duration_s'])) if o['duration_s'] > 0 else 0.0
            rem = max(0.0, o['t0'] + o['duration_s'] - self.t)
        surf = foc = None
        if self.on_stage is not None:
            xs, ys = self._axis_in_sample()
            surf = self._surface_z_at(xs, ys)
            foc = self._focus_true(self.z) - surf
        fov = list(fov_m(lens['mag'], self.zoom)) if lens else None
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'system.state': self.state,
            'system.error_code': self.error, 'stage.x_m': self.stage['x'], 'stage.y_m': self.stage['y'],
            'stage.x_indicated_m': self._quant(self.stage['x']) if on else None, 'stage.y_indicated_m': self._quant(self.stage['y']) if on else None,
            'stage.target_m': [self.stage_target['x'], self.stage_target['y']], 'stage.moving': self._stage_moving(), 'stage.position_label': self.position_label,
            'stage.lift_m': self.lift, 'stage.lift_target_m': self.lift_target, 'stage.focus_drive_rad': -self.lift / COARSE_PITCH_M * 2 * math.pi,
            'head.z_m': self.z, 'head.z_target_m': self.z_target, 'head.focus_z_indicated_m': self._quant(self.z) if on else None, 'head.z_moving': self._z_moving(),
            'head.revolver_rad': self.rev, 'head.lens_slot': self.slot, 'head.objective': lens['label'] if lens else None, 'head.magnification': lens['mag'] if lens else None,
            'head.revolver_moving': not self._revolver_settled(), 'optics.zoom': self.zoom, 'optics.fov_m': fov,
            'optics.total_magnification': total_magnification(lens['mag'], self.zoom) if lens else None, 'measurement.settings': dict(self.settings),
            'measurement.z_range_m': list(self.z_range) if self.z_range else None, 'measurement.id': o['record_id'] if o else None,
            'measurement.kind': o['kind'] if o else None, 'measurement.progress': prog, 'measurement.remaining_s': rem, 'measurement.started_by_other_agent': False,
            'laser.on': self._laser_on(), 'result.last_record_id': self._last_record(), 'result.last_value': self.last_value,
            'calibration.z_scale': dict(self.cal['z']), 'calibration.xy_scale': dict(self.cal['xy']),
            'sample.bench_ids': sorted(k for k, s in self.samples.items() if s['location'] == 'bench'),
            'sample.on_stage': (dict(sample_id=self.on_stage, x_m=self.samples[self.on_stage]['x_m'], y_m=self.samples[self.on_stage]['y_m']) if self.on_stage else None),
            'panel.head_lamp': self.power and self.state == 'ready', 'panel.stage_lamp': self.power and 'stage_disconnected' not in self.faults,
            'panel.controller_lamp': self.power, 'panel.laser_lamp': self._laser_on(),
            'truth.surface_z_on_axis_m': surf, 'truth.focus_error_m': foc, 'truth.z_scale_error': self.truth['z_scale'],
            'truth.xy_scale_error': dict(self.truth['xy_scale']), 'truth.samples': self._truth_samples(), 'truth.faults': sorted(self.faults),
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _last_record(self, keep=None):
        done = [k for k, r in self.records.items() if r['status'] == 'completed' and (keep is None or keep(k))]
        return max(done, key=lambda k: (self.records[k]['finished_s'] or 0.0, k), default=None)

    def _truth_samples(self):
        return {k: {kk: copy.deepcopy(vv) for kk, vv in s.items()} for k, s in self.samples.items()}

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        props = CAPS[capability_id]['input_schema'].get('properties', {})
        a = {k: (int(v) if props.get(k, {}).get('type') == 'integer' else v) for k, v in a.items()}
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**a)
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):
            self.revision += 1
        return out

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_power(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _need_ready(self, allow_error=False):
        self._need_power()
        if self.state != 'ready':
            raise ActionRejected('NOT_READY')
        if self.error is not None and not allow_error:
            raise ActionRejected('ERROR_PENDING')

    def _need_idle(self):
        if self.op is not None:
            raise ActionRejected('MEASUREMENT_RUNNING')

    def _need_head_still(self):
        if self._z_moving() or not self._revolver_settled():
            raise ActionRejected('MOTION_IN_PROGRESS')

    def _need_stage_still(self):
        if self._stage_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')

    # ---------------------------------------------------------------- setup, samples, power, faults
    def _cmd_plant_configure(self, room_k, mains_voltage_v, relative_humidity, noise_seed):
        first = self.plant is None
        if not first and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        self.plant = dict(room_k=room_k, mains_voltage_v=mains_voltage_v, relative_humidity=relative_humidity, noise_seed=noise_seed)
        if first:
            rng = random.Random(noise_seed * 7919 + 17)
            self.truth['z_scale'] = rng.gauss(0.0, Z_SCALE_SD)
            self.truth['xy_scale'] = {L['id']: rng.gauss(0.0, XY_SCALE_SD) for L in LENSES}
            self.truth['film_lambda_scale'] = rng.gauss(0.0, 1.0e-3)
        if self.power and not (MAINS_V[0] * 0.9 <= mains_voltage_v <= MAINS_V[1] * 1.1):
            self._power_off('MAINS_FAULT')
        return dict(plant=dict(self.plant))

    def _cmd_sample_provide(self, sample_id, substrate_substance_id, thickness_m, size_x_m, size_y_m, features, film, roughness, tilt_x_rad, tilt_y_rad):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('SAMPLE_EXISTS')
        if len(self.samples) >= MAX_SAMPLES:
            raise ActionRejected('TOO_MANY_SAMPLES')
        ids = [substrate_substance_id] + [f['substance_id'] for f in features if f['substance_id']] + ([film['substance_id']] if film else [])
        for sid in ids:
            substance(sid)
        for f in features:
            if f['kind'] in ('grating', 'pillars', 'pits', 'sine') and not (f['pitch_m'] > f['width_m'] or (f['kind'] == 'sine' and f['pitch_m'] > 0)):
                raise ActionRejected('INVALID_FEATURE')
            if f['kind'] == 'cap' and not (0 < f['height_m'] <= f['width_m'] / 2):
                raise ActionRejected('INVALID_FEATURE')
        self.samples[sample_id] = dict(sample_id=sample_id, substrate_substance_id=substrate_substance_id, thickness_m=thickness_m, size_x_m=size_x_m, size_y_m=size_y_m,
                                       features=copy.deepcopy(features), film=copy.deepcopy(film), roughness=copy.deepcopy(roughness), tilt_x_rad=tilt_x_rad,
                                       tilt_y_rad=tilt_y_rad, location='bench', x_m=0.0, y_m=0.0)
        self.used_substances.update(ids)
        self._event('sample', 'SAMPLE_PROVIDED', f'样品{sample_id}放在实验台上')
        return dict(sample_id=sample_id, location='bench')

    def _cmd_sample_place(self, sample_id, x_m, y_m):
        self._need_plant()
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_SAMPLE')
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        if self.on_stage is not None:
            raise ActionRejected('STAGE_OCCUPIED')
        self._need_idle()
        if self._stage_moving() or self._z_moving() or not self._revolver_settled():
            raise ActionRejected('MOTION_IN_PROGRESS')
        if abs(x_m) + s['size_x_m'] / 2 > 0.07 or abs(y_m) + s['size_y_m'] / 2 > 0.07:
            raise ActionRejected('OUTSIDE_PLATE')
        lens = self._guard_lens()
        top = SAMPLE_PLATE_Z + max(self.lift, self.lift_target) + s['thickness_m'] + sample_extremes(s)[0]      # a rising lift: at its target
        if top > self._tip_z(lens, self.z) - SAFE_TIP_CLEARANCE_M:
            raise ActionRejected('SAMPLE_TOO_TALL')
        s['location'], s['x_m'], s['y_m'] = 'stage', x_m, y_m
        self.on_stage = sample_id
        self._event('sample', 'SAMPLE_PLACED', f'样品{sample_id}放到样品台（中心{x_m * 1e3:.1f}, {y_m * 1e3:.1f} mm）')
        return dict(sample_id=sample_id, x_m=x_m, y_m=y_m)

    def _cmd_sample_remove(self, sample_id):
        self._need_plant()
        if self.on_stage != sample_id:
            raise ActionRejected('SAMPLE_NOT_ON_STAGE')
        self._need_idle()
        if self._stage_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')
        s = self.samples[sample_id]
        s['location'], s['x_m'], s['y_m'] = 'bench', 0.0, 0.0
        self.on_stage = None
        self._event('sample', 'SAMPLE_REMOVED', f'样品{sample_id}取回实验台')
        return dict(sample_id=sample_id)

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        if active:
            self.faults.add(fault)
            code = FAULT_ERROR.get(fault)
            if code and self.power:
                self._raise_error(code)
        else:
            self.faults.discard(fault)
        self.fault_log.append((self.t, fault, active))
        self._event('fault', 'FAULT_' + ('ON' if active else 'OFF'), fault)
        if active and fault == 'laser_failure' and self.op is not None and self.op.get('laser'):
            self._fail_op('LASER_FAULT')
        if active and fault == 'stage_disconnected':
            self.stage_target = dict(self.stage)
            if self.op is not None and self.op['kind'] == 'stitched_map':
                self._fail_op('STAGE_FAULT')
        if active and fault == 'revolver_jam':
            if any(q[0] == 'rev' for q in self.head_queue) or abs(self.rev - self.rev_target) > 1e-12:
                self.rev_target = self.rev
                self.head_queue = [q for q in self.head_queue if q[0] != 'rev']
        return dict(faults=sorted(self.faults))

    def _raise_error(self, code):
        if self.error is None:
            self.error = code
            self._event('error', code, ERROR_ZH.get(code, code))

    def _cmd_power_press_button(self):
        return self._cmd_power_set(not self.power)

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            if not (MAINS_V[0] * 0.9 <= self.plant['mains_voltage_v'] <= MAINS_V[1] * 1.1):
                raise ActionRejected('MAINS_OUT_OF_RANGE')
            self.power, self.state, self.state_t0 = True, 'booting', self.t
            self.z_target, self.z_speed = 0.0, HEAD_Z_SPEED_M_S           # the head Z stage returns to its origin during the boot
            self.head_queue = []
            if self.error == 'MAINS_FAULT':
                self.error = None
            for f in self.faults:
                if FAULT_ERROR.get(f):
                    self._raise_error(FAULT_ERROR[f])
            self._event('power', 'POWER_ON', '控制器通电，启动中（测量头Z台回原点）')
        elif not enabled and self.power:
            self._power_off(None)
        return dict(enabled=self.power, state=self.state)

    def _power_off(self, code):
        if self.op is not None:
            self._fail_op('POWER_OFF')
        self.power, self.state = False, 'off'
        self.stage_target = dict(self.stage)
        self.z_target = self.z
        self.rev_target = self.rev
        self.head_queue = []
        if code:
            self.error = code
            self._event('error', code, ERROR_ZH.get(code, code))
        self._event('power', 'POWER_OFF', '控制器断电：各轴停止')

    def _cmd_system_acknowledge(self):
        self._need_power()
        if self.error is None:
            return dict(cleared=None)
        cause = {v: k for k, v in FAULT_ERROR.items()}.get(self.error)
        if cause in self.faults or (self.error == 'MAINS_FAULT' and not (MAINS_V[0] * 0.9 <= self.plant['mains_voltage_v'] <= MAINS_V[1] * 1.1)):
            raise ActionRejected('CAUSE_PERSISTS')
        cleared, self.error = self.error, None
        self._event('error', 'ACKNOWLEDGED', f'确认错误{cleared}')
        return dict(cleared=cleared)

    # ---------------------------------------------------------------- stage, lift, head
    def _stage_checks(self):
        self._need_ready()
        if 'stage_disconnected' in self.faults:
            raise ActionRejected('STAGE_FAULT')
        self._need_idle()

    def _move_stage(self, x, y, label=None):
        self.stage_target = dict(x=x, y=y)
        self.position_label = label
        d = max(abs(x - self.stage['x']), abs(y - self.stage['y']))
        return dict(target_m=[x, y], duration_s=d / STAGE_SPEED_M_S)

    def _cmd_stage_move(self, x_m, y_m):
        self._stage_checks()
        return self._move_stage(x_m, y_m)

    def _cmd_stage_go_to(self, position):
        self._stage_checks()
        if position == 'center':
            return self._move_stage(0.0, 0.0, 'center')
        if position == 'load':
            return self._move_stage(0.0, -MOTORIZED_XY_M[1] / 2, 'load')
        if self.on_stage is None:
            raise ActionRejected('NO_SAMPLE')
        s = self.samples[self.on_stage]
        x, y = -s['x_m'], -s['y_m']
        if abs(x) > MOTORIZED_XY_M[0] / 2 + 1e-12 or abs(y) > MOTORIZED_XY_M[1] / 2 + 1e-12:
            raise ActionRejected('OUT_OF_RANGE')
        return self._move_stage(x, y, 'sample_center')

    def _cmd_stage_set_lift(self, lift_m):
        self._need_plant()
        self._need_idle()
        if not self._revolver_settled():
            raise ActionRejected('MOTION_IN_PROGRESS')
        if lift_m > self.lift:
            top_new = self._sample_top_z(lift=lift_m)
            if top_new > self._tip_z(self._guard_lens(), max(self.z, self.z_target)) - COLLISION_MARGIN_M:
                raise ActionRejected('COLLISION_RISK')
        self.lift_target = lift_m
        return dict(target_m=lift_m, duration_s=abs(lift_m - self.lift) / LIFT_SPEED_M_S)

    def _cmd_lens_select(self, slot):
        self._need_ready()
        if 'revolver_jam' in self.faults:
            raise ActionRejected('REVOLVER_FAULT')
        self._need_idle()
        self._need_head_still()
        new = LENSES[slot]
        step = 2 * math.pi / REVOLVER_HOLES
        target = slot * step
        top = self._sample_top_z()
        # every objective the revolver sweeps through the optical axis on its way (and the new one) must clear the sample by 1 mm
        k_lo, k_hi = math.floor(min(self.rev, target) / step + 1e-9), math.ceil(max(self.rev, target) / step - 1e-9)
        guard = min((LENSES[k % REVOLVER_HOLES] for k in range(k_lo, k_hi + 1)), key=lambda L: L['wd_m'])
        z_safe = self.z
        if self._tip_z(guard, self.z) < top + SAFE_TIP_CLEARANCE_M:
            z_safe = self._z_for_focus(top + SAFE_TIP_CLEARANCE_M - guard['wd_m'])
            if z_safe < HEAD_Z_RANGE_M[0]:
                raise ActionRejected('COLLISION_RISK')
        self.slot = slot
        self.head_queue = ([('z', z_safe)] if z_safe != self.z else []) + [('rev', target)]
        self._start_head_queue()
        dur = abs(z_safe - self.z) / HEAD_Z_SPEED_M_S + abs(target - self.rev) / (2 * math.pi / REVOLVER_HOLES) * REVOLVER_S_PER_STEP
        self._event('head', 'LENS_SELECT', f'切换到{new["label"]}物镜')
        return dict(objective=new['label'], magnification=new['mag'], duration_s=dur, z_retract_m=z_safe - self.z)

    def _start_head_queue(self):
        while self.head_queue:
            kind, target = self.head_queue[0]
            if kind == 'z':
                if abs(self.z - target) <= 1e-12:
                    self.z = target; self.head_queue.pop(0); continue
                self.z_target, self.z_speed = target, HEAD_Z_SPEED_M_S
                return
            if abs(self.rev - target) <= 1e-12:
                self.rev = target; self.head_queue.pop(0); continue
            self.rev_target = target
            return

    def _cmd_zoom_set(self, zoom):
        self._need_ready()
        self._need_idle()
        self.zoom = zoom
        lens = self._active_lens() or self._lens()
        return dict(zoom=zoom, fov_m=list(fov_m(lens['mag'], zoom)))

    def _check_z_target(self, z, lens=None):
        lens = lens or self._guard_lens()
        if self._tip_z(lens, z) < self._sample_top_z() + COLLISION_MARGIN_M:
            raise ActionRejected('COLLISION_RISK')

    def _cmd_focus_move(self, z_m):
        self._need_ready()
        self._need_idle()
        self._need_head_still()
        self._check_z_target(z_m)
        self.z_target, self.z_speed = z_m, HEAD_Z_SPEED_M_S
        return dict(target_m=z_m, duration_s=abs(z_m - self.z) / HEAD_Z_SPEED_M_S)

    def _cmd_measurement_configure(self, mode, quality, pitch_m, gain, double_scan, peak_mode, film_refractive_index):
        self._need_plant()
        self._need_idle()
        if peak_mode == 'film' and film_refractive_index is None:
            raise ActionRejected('FILM_INDEX_REQUIRED')
        if mode != 'laser_confocal' and (double_scan or peak_mode != 'strongest'):
            raise ActionRejected('SETTING_NOT_FOR_MODE')
        self.settings = dict(mode=mode, quality=quality, pitch_m=pitch_m, gain=gain, double_scan=double_scan, peak_mode=peak_mode,
                             film_refractive_index=film_refractive_index)
        return dict(settings=dict(self.settings))

    def _cmd_measurement_set_z_range(self, upper_z_m, lower_z_m):
        self._need_plant()
        self._need_idle()
        if not upper_z_m < lower_z_m:
            raise ActionRejected('INVALID_Z_RANGE')
        self.z_range = (upper_z_m, lower_z_m)
        return dict(z_range_m=[upper_z_m, lower_z_m])

    # ---------------------------------------------------------------- operations
    def _new_op(self, kind, rid=None, **data):
        self.seq['op'] += 1
        oid = f'op{self.seq["op"]:05d}'
        self.operations[oid] = dict(operation_id=oid, kind=kind, phase='running', record_id=rid, started_s=self.t, reason=None)
        return oid

    def _new_record_id(self, prefix, key):
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        self.seq[key] += 1
        return f'{prefix}{self.seq[key]:04d}'

    def _op_checks(self, need_sample=True):
        self._need_ready()
        self._need_idle()
        self._need_head_still()
        self._need_stage_still()
        if self._lift_moving():
            raise ActionRejected('MOTION_IN_PROGRESS')
        if need_sample and self.on_stage is None:
            raise ActionRejected('NO_SAMPLE')

    def _lift_moving(self):
        return abs(self.lift - self.lift_target) > 1e-12

    def _pitch(self, mode, lens):
        s = self.settings
        if s['pitch_m'] is not None:
            return s['pitch_m']
        if mode == 'laser_confocal':
            return axial_fwhm_m(lens['na']) / {'high_speed': 3.0, 'standard': 5.0, 'high_accuracy': 8.0}[s['quality']]
        if mode == 'focus_variation':
            return 0.55e-6 / (lens['na'] ** 2) / 3.0
        return WLI_LAMBDA0_M / 8.0

    def _frame_s(self, mode):
        if mode == 'laser_confocal':
            return QUALITY_LINES[self.settings['quality']] / LINE_RATE_HZ
        return 1.0 / (FV_FPS if mode == 'focus_variation' else WLI_FPS)

    def _scan_plan(self, mode, lens, z_range):
        up, low = z_range
        p = self._pitch(mode, lens)
        n = int(math.floor((low - up) / p + 1e-9)) + 1
        if n < 3:
            raise ActionRejected('Z_RANGE_TOO_SMALL')
        if n > MAX_Z_STEPS:
            raise ActionRejected('TOO_MANY_STEPS')
        passes = 2 if (mode == 'laser_confocal' and self.settings['double_scan']) else 1
        return dict(pitch=p, n=n, frame_s=self._frame_s(mode), passes=passes, t_scan=n * self._frame_s(mode) * passes)

    def _mode_checks(self, mode, lens):
        if lens is None:
            raise ActionRejected('NO_OBJECTIVE' if self._revolver_settled() else 'MOTION_IN_PROGRESS')
        if mode not in lens['modes']:
            raise ActionRejected('LENS_NOT_SUPPORTED')
        if mode == 'laser_confocal' and 'laser_failure' in self.faults:
            raise ActionRejected('LASER_FAULT')

    def _program_times(self, items):
        """Absolute end time of every program item from the current positions (the op owns the head Z and, for stitching, the stage)."""
        t, z = self.t, self.z
        sx, sy = self.stage['x'], self.stage['y']
        out = []
        for it in items:
            k = it[0]
            if k == 'z':
                d = abs(it[1] - z) / HEAD_Z_SPEED_M_S; z = it[1]
            elif k == 'scan':
                d = it[2]; z = it[1]
            elif k == 'xy':
                d = max(abs(it[1] - sx), abs(it[2] - sy)) / STAGE_SPEED_M_S; sx, sy = it[1], it[2]
            elif k in ('wait', 'prescan'):
                d = it[1]
            else:
                d = 0.0
            t = t + d
            out.append(t)
        return out

    def _start_op(self, kind, record_id, items, laser, ctx):
        oid = self._new_op(kind, record_id)
        ends = self._program_times(items)
        self.op = dict(kind=kind, op=oid, record_id=record_id, t0=self.t, duration_s=ends[-1] - self.t if ends else 0.0, items=items, ends=ends, i=-1,
                       phase=None, laser=laser, ctx=ctx, fields=[])
        self._next_item()
        return dict(operation_id=oid)

    def _next_item(self):
        o = self.op
        while o is not None:
            o['i'] += 1
            if o['i'] >= len(o['items']):
                return
            it = o['items'][o['i']]
            k = it[0]
            o['phase'] = {'scan': 'scan'}.get(k, k)
            if k == 'z':
                self.z_target, self.z_speed = it[1], HEAD_Z_SPEED_M_S
            elif k == 'scan':
                self._scan_begin()
                self.z_target, self.z_speed = it[1], abs(it[1] - self.z) / it[2] if it[2] > 0 else HEAD_Z_SPEED_M_S
            elif k == 'xy':
                self.stage_target = dict(x=it[1], y=it[2]); self.position_label = None
            elif k == 'call':
                getattr(self, it[1])(*it[2:])
                if self.op is not o:
                    return
            if self.t < o['ends'][o['i']] - 1e-12:
                return

    def _cmd_focus_auto(self):
        self._op_checks()
        lens = self._active_lens()
        if lens is None:
            raise ActionRejected('NO_OBJECTIVE')
        if not lens['modes']:
            raise ActionRejected('LENS_NOT_SUPPORTED')
        target = self._autofocus_target(lens)
        if target is None:
            raise ActionRejected('NO_SURFACE_FOUND')
        self._check_z_target(target, lens)
        items = [('wait', AUTOFOCUS_S), ('z', target), ('call', '_finish_autofocus')]
        return self._start_op('autofocus', None, items, False, dict(target=target))

    def _autofocus_target(self, lens):
        """Z at which the brightest interface under the axis is in focus, if it lies within the search window around the current Z."""
        xs, ys = self._axis_in_sample()
        s = self.samples[self.on_stage]
        z_s = self._surface_z_at(xs, ys)
        X, Y = np.array([[xs]]), np.array([[ys]])
        m = surface_map(s, X, Y, 1e-6, 1e-6, 0.0, self._seed())
        target_z = z_s
        if m['below'][0, 0] is not None and m['t_buried'][0, 0] > 0:
            N1, N2 = nk(m['top'][0, 0], LASER_WAVELENGTH_M), nk(m['below'][0, 0], LASER_WAVELENGTH_M)
            R1 = abs(r_interface(1.0, N1)) ** 2
            tau = math.exp(-4 * math.pi * N1.imag * m['t_buried'][0, 0] / LASER_WAVELENGTH_M)
            if (1 - R1) ** 2 * abs(r_interface(N1, N2)) ** 2 * tau * tau > R1:          # the autofocus locks onto the brighter buried interface
                target_z = z_s - m['t_buried'][0, 0] / n_effective(N1.real, lens['na'])
        z = self._z_for_focus(target_z)
        window = max(20 * axial_fwhm_m(lens['na']), 3.0e-4)
        if abs(z - self.z) > window or not HEAD_Z_RANGE_M[0] <= z <= HEAD_Z_RANGE_M[1]:
            return None
        rng = np.random.default_rng(self._op_seed('af'))
        return float(min(max(z + rng.normal(0.0, 0.1 * axial_fwhm_m(lens['na'])), HEAD_Z_RANGE_M[0]), HEAD_Z_RANGE_M[1]))

    def _op_seed(self, tag):
        return int(hashlib.sha256(f'{self._seed()}|{tag}|{self.seq["op"]}|{self.seq["hm"]}|{self.seq["st"]}|{self.seq["ft"]}'.encode()).hexdigest()[:12], 16)

    def _finish_autofocus(self):
        o = self.op
        self.operations[o['op']].update(phase='completed', finished_s=self.t, z_m=self.z)
        self._event('measurement', 'AUTOFOCUS_DONE', f'自动对焦完成：Z={self.z * 1e3:.4f} mm')
        self.op = None

    def _cmd_measurement_auto_range(self):
        self._op_checks()
        mode = self.settings['mode']
        lens = self._active_lens()
        self._mode_checks(mode, lens)
        rng_ = self._field_extent_z(lens)
        if rng_ is None:
            raise ActionRejected('NO_SURFACE_FOUND')
        items = [('prescan', AUTORANGE_S), ('call', '_finish_auto_range', rng_)]
        laser = mode == 'laser_confocal'
        ctx = dict(lens=dict(lens), rta=self._rta_field(self._field_surface(lens, coarse=True)) if laser else None)   # the laser scans the field during the pre-scan
        return self._start_op('auto_range', None, items, laser, ctx)

    def _field_extent_z(self, lens):
        """Z range (upper, lower) covering the surfaces in the field (+ a margin of 2 FWHM or 3 roughness sigmas); None if no surface lies
        within 2 mm of the current focus."""
        f = self._field_surface(lens, coarse=True)
        zs = [f['z_top']]
        if f['z_buried'] is not None:
            zs.append(f['z_buried'])
        zmax = max(float(np.nanmax(z)) for z in zs)
        zmin = min(float(np.nanmin(z)) for z in zs)
        focus = self._focus_true(self.z)
        if zmax < focus - 2.0e-3 or zmin > focus + 2.0e-3:
            return None
        margin = max(2 * axial_fwhm_m(lens['na']), 3 * self.samples[self.on_stage]['roughness']['sq_m'], 1.0e-6)
        up, low = self._z_for_focus(zmax + margin), self._z_for_focus(zmin - margin)
        up = max(up, HEAD_Z_RANGE_M[0]); low = min(low, HEAD_Z_RANGE_M[1])
        tip_limit = self._z_for_focus(self._sample_top_z() + COLLISION_MARGIN_M - lens['wd_m'])
        low = min(low, tip_limit)
        if not up < low:
            return None
        return (up, low)

    def _finish_auto_range(self, rng_):
        o = self.op
        self.z_range = rng_
        self.operations[o['op']].update(phase='completed', finished_s=self.t, z_range_m=list(rng_))
        self._event('measurement', 'AUTO_RANGE_DONE', f'自动上下限：Z {rng_[0] * 1e3:.4f}–{rng_[1] * 1e3:.4f} mm')
        self.op = None

    def _measurement_items(self, mode, lens):
        if self.z_range is None:
            raise ActionRejected('Z_RANGE_NOT_SET')
        up, low = self.z_range
        self._check_z_target(low, lens)
        plan = self._scan_plan(mode, lens, self.z_range)
        return plan, [('z', up), ('scan', low, plan['t_scan'])]

    def _cmd_measurement_start(self):
        self._op_checks()
        mode = self.settings['mode']
        lens = self._active_lens()
        self._mode_checks(mode, lens)
        plan, scan = self._measurement_items(mode, lens)
        rid = self._new_record_id('HM', 'hm')
        z0 = self.z
        items = [('wait', MEASURE_OVERHEAD_S)] + scan + [('call', '_scan_done'), ('z', z0), ('call', '_finish_measurement')]
        ctx = self._context(mode, lens, plan)
        self.records[rid] = self._record_stub(rid, 'height_map', ctx)
        self._event('measurement', 'MEASURE_START', f'测量{rid}开始（{mode}，{lens["label"]}，{plan["n"]}个Z步）', rid=rid)
        return self._start_op('height_map', rid, items, mode == 'laser_confocal', ctx)

    def _cmd_measurement_stitch(self, nx, ny, overlap):
        self._op_checks()
        if 'stage_disconnected' in self.faults:
            raise ActionRejected('STAGE_FAULT')
        mode = self.settings['mode']
        lens = self._active_lens()
        self._mode_checks(mode, lens)
        plan, scan = self._measurement_items(mode, lens)
        fx, fy = fov_m(lens['mag'], self.zoom)
        cx, cy = self.stage['x'], self.stage['y']
        pts = []
        for j in range(ny):
            for i in range(nx):
                px = cx - (i - (nx - 1) / 2) * fx * (1 - overlap)
                py = cy - (j - (ny - 1) / 2) * fy * (1 - overlap)
                if abs(px) > MOTORIZED_XY_M[0] / 2 or abs(py) > MOTORIZED_XY_M[1] / 2:
                    raise ActionRejected('OUT_OF_RANGE')
                pts.append((i, j, px, py))
        rid = self._new_record_id('ST', 'st')
        z0 = self.z
        items = [('wait', MEASURE_OVERHEAD_S)]
        for (i, j, px, py) in pts:
            items += [('xy', px, py)] + scan + [('call', '_scan_done', i, j), ('z', self.z_range[0])]
        items += [('xy', cx, cy), ('z', z0), ('call', '_finish_stitch', nx, ny, overlap)]
        ctx = self._context(mode, lens, plan)
        self.records[rid] = self._record_stub(rid, 'stitched_map', ctx)
        self._event('measurement', 'STITCH_START', f'拼接测量{rid}开始（{nx}×{ny}视场）', rid=rid)
        return self._start_op('stitched_map', rid, items, mode == 'laser_confocal', ctx)

    def _cmd_film_measure(self, film_substance_id, substrate_substance_id):
        self._op_checks()
        substance(film_substance_id); substance(substrate_substance_id)
        lens = self._active_lens()
        self._mode_checks('film_thickness', lens)
        rid = self._new_record_id('FT', 'ft')
        ctx = self._context('film_thickness', lens, None)
        ctx.update(film_id=film_substance_id, substrate_id=substrate_substance_id)
        self.records[rid] = self._record_stub(rid, 'film_thickness', ctx)
        items = [('wait', FILM_MEASURE_S), ('call', '_finish_film')]
        self._event('measurement', 'FILM_START', f'膜厚测量{rid}开始', rid=rid)
        return self._start_op('film_thickness', rid, items, False, ctx)

    def _context(self, mode, lens, plan):
        return dict(mode=mode, lens=dict(lens), zoom=self.zoom, settings=dict(self.settings), plan=plan, z_range=list(self.z_range) if self.z_range else None,
                    sample_id=self.on_stage, lift=self.lift, cal_z=self.cal['z']['factor'], cal_xy=self.cal['xy'][lens['id']], seed=self._op_seed(mode))

    def _record_stub(self, rid, kind, ctx):
        lens = ctx['lens']
        fov = list(fov_m(lens['mag'], ctx['zoom'])) if kind != 'film_thickness' else None
        return dict(record_id=rid, kind=kind, status='running', started_s=self.t, finished_s=None, reason=None,
                    settings=dict(ctx['settings']) if kind != 'film_thickness' else None, objective=lens['label'], magnification=lens['mag'], zoom=ctx['zoom'],
                    stage_xy_m=[self.stage['x'], self.stage['y']], fov_m=fov, grid=None, pixel_pitch_m=None,
                    z_range_m=list(ctx['z_range']) if ctx['z_range'] and kind != 'film_thickness' else None, pitch_m=ctx['plan']['pitch'] if ctx['plan'] else None,
                    n_frames=0, gain=None, height_m=None, intensity=None, film_thickness_m=None, valid_fraction=None, duration_s=0.0, film=None,
                    template_source=dict(library=LIBRARY_REL, substances=[]), note_zh='', _cal_z=ctx['cal_z'], _cal_xy=ctx['cal_xy'])

    def _fail_op(self, code):
        o = self.op
        if o is None:
            return
        self.operations[o['op']].update(phase='failed', finished_s=self.t, reason=code)
        if o['record_id'] in self.records:
            r = self.records[o['record_id']]
            r.update(status='failed', finished_s=self.t, reason=code, duration_s=self.t - r['started_s'])
        self._event('measurement', 'OP_FAILED', f'操作{o["op"]}失败：{code}', rid=o['record_id'])
        self.op = None
        self.z_target, self.z_speed = self.z, HEAD_Z_SPEED_M_S
        if o['kind'] == 'stitched_map':
            self.stage_target = dict(self.stage)

    def _cancel_op(self, op):
        o = self.op
        if o is None or o['op'] != op['operation_id']:
            raise ActionRejected('NOT_RUNNING')
        self.operations[o['op']].update(phase='cancelled', finished_s=self.t, reason='USER_CANCEL')
        if o['record_id'] in self.records:
            r = self.records[o['record_id']]
            r.update(status='cancelled', finished_s=self.t, reason='USER_CANCEL', duration_s=self.t - r['started_s'])
        self._event('measurement', 'OP_CANCELLED', f'操作{o["op"]}取消', rid=o['record_id'])
        self.op = None
        self.z_target, self.z_speed = self.z, HEAD_Z_SPEED_M_S
        if o['kind'] == 'stitched_map':
            self.stage_target = dict(self.stage)

    # ---------------------------------------------------------------- time
    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)

    def step(self, dt_s):
        if not (isinstance(dt_s, (int, float)) and math.isfinite(dt_s) and dt_s >= 0):
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + dt_s
        guard = 0
        while self.t < t_end and guard < 1_000_000:
            guard += 1
            h = min(t_end - self.t, self._next_event_dt())
            self._advance(h)
            self.t = t_end if h >= t_end - self.t else self.t + h
            self._handle_events()
        self._handle_events()

    def _next_event_dt(self):
        c = [math.inf]
        if self.state == 'booting':
            c.append(self.state_t0 + BOOT_S - self.t)
        if self.power:
            for a in 'xy':
                if abs(self.stage[a] - self.stage_target[a]) > 1e-12:
                    c.append(abs(self.stage_target[a] - self.stage[a]) / STAGE_SPEED_M_S)
            if abs(self.z - self.z_target) > 1e-12:
                c.append(abs(self.z_target - self.z) / self.z_speed)
            if abs(self.rev - self.rev_target) > 1e-12:
                c.append(abs(self.rev_target - self.rev) / (2 * math.pi / REVOLVER_HOLES) * REVOLVER_S_PER_STEP)
        if abs(self.lift - self.lift_target) > 1e-12:
            c.append(abs(self.lift_target - self.lift) / LIFT_SPEED_M_S)
        o = self.op
        if o is not None and 0 <= o['i'] < len(o['ends']):
            c.append(o['ends'][o['i']] - self.t)
        return max(0.0, min(c))

    def _advance(self, h):
        if h <= 0:
            return
        if self.power:
            for a in 'xy':
                new = _axis_move(self.stage[a], self.stage_target[a], STAGE_SPEED_M_S, h)
                self.led['stage_travel_m'].add(abs(new - self.stage[a])); self.stage[a] = new
            new = _axis_move(self.z, self.z_target, self.z_speed, h)
            self.led['z_travel_m'].add(abs(new - self.z)); self.z = new
            w = 2 * math.pi / REVOLVER_HOLES / REVOLVER_S_PER_STEP
            new = _axis_move(self.rev, self.rev_target, w, h)
            self.led['revolver_steps'].add(abs(new - self.rev) / (2 * math.pi / REVOLVER_HOLES)); self.rev = new
            if self._laser_on():
                self.led['laser_on_s'].add(h)
                e = LASER_MAX_W * h
                self.led['laser_energy_j'].add(e)
                self._book_energy(e)
        new = _axis_move(self.lift, self.lift_target, LIFT_SPEED_M_S, h)
        self.led['lift_travel_m'].add(abs(new - self.lift)); self.lift = new

    def _book_energy(self, e):
        """Energy of the laser scan over the field: reflected, absorbed in the top (transparent) layer and entering the substrate, from the
        per-pixel incoherent R, T, A of the field captured at the scan start."""
        rta = self.op['ctx'].get('rta')
        if rta is None:
            return
        es = e * OPTICS_TRANSMISSION
        self.led['energy_to_sample_j'].add(es)
        self.led['energy_reflected_j'].add(es * rta[0]); self.led['energy_transmitted_j'].add(es * rta[1]); self.led['energy_absorbed_j'].add(es * rta[2])

    def _handle_events(self):
        for _ in range(10000):
            changed = False
            if self.state == 'booting' and self.t >= self.state_t0 + BOOT_S - 1e-9:
                self.state = 'ready'
                self._event('power', 'READY', '启动完成')
                changed = True
            if self.power:
                for a in 'xy':
                    if abs(self.stage[a] - self.stage_target[a]) <= 1e-12:
                        self.stage[a] = self.stage_target[a]
                if abs(self.z - self.z_target) <= 1e-12:
                    self.z = self.z_target
                if abs(self.rev - self.rev_target) <= 1e-12:
                    self.rev = self.rev_target
                if self.head_queue:
                    kind, target = self.head_queue[0]
                    arrived = abs(self.z - target) <= 1e-12 if kind == 'z' else abs(self.rev - target) <= 1e-12
                    if arrived and (kind != 'z' or abs(self.z_target - target) <= 1e-12) and (kind != 'rev' or abs(self.rev_target - target) <= 1e-12):
                        self.head_queue.pop(0)
                        if self.head_queue:
                            self._start_head_queue()
                        changed = True
            if abs(self.lift - self.lift_target) <= 1e-12:
                self.lift = self.lift_target
            o = self.op
            if o is not None and 0 <= o['i'] < len(o['ends']) and self.t >= o['ends'][o['i']] - 1e-9:
                it = o['items'][o['i']]
                if it[0] == 'z':
                    self.z = it[1]
                elif it[0] == 'scan':
                    self.z = it[1]
                elif it[0] == 'xy':
                    self.stage = dict(x=it[1], y=it[2])
                self._next_item()
                changed = True
            if not changed:
                return

    # ---------------------------------------------------------------- field physics
    PLATE_R, PLATE_SIGMA = 0.04, 0.5e-6              # black anodised sample plate (engineering)

    def _field_surface(self, lens, coarse=False, ctx=None, stage=None, lam=LASER_WAVELENGTH_M):
        s = self.samples[self.on_stage if ctx is None else ctx['sample_id']]
        zoom = self.zoom if ctx is None else ctx['zoom']
        lift = self.lift if ctx is None else ctx['lift']
        nx, ny = (32, 24) if coarse else SIM_GRID
        fxn, fyn = fov_m(lens['mag'], zoom)
        sc = 1.0 + self.truth['xy_scale'][lens['id']]
        dx, dy = fxn * sc / nx, fyn * sc / ny
        sx, sy = (self.stage['x'], self.stage['y']) if stage is None else stage
        xa, ya = -sx - s['x_m'], -sy - s['y_m']
        X = xa + (np.arange(nx) - (nx - 1) / 2) * dx
        Y = ya + (np.arange(ny) - (ny - 1) / 2) * dy
        X, Y = np.meshgrid(X, Y)
        m = surface_map(s, X, Y, dx, dy, 0.21 * lam / lens['na'], self._seed())
        inside = (np.abs(X) <= s['size_x_m'] / 2) & (np.abs(Y) <= s['size_y_m'] / 2)
        base = SAMPLE_PLATE_Z + lift
        z_top = np.where(inside, base + s['thickness_m'] + m['h'], base)
        t_b = np.where(inside, m['t_buried'], 0.0)
        below = np.where(inside, m['below'], None)
        top = np.where(inside, m['top'], None)
        z_b = None
        if np.any(t_b > 0):
            z_b = np.where(t_b > 0, z_top - t_b, np.nan)
        return dict(X=X, Y=Y, dx_nom=fxn / nx, dy_nom=fyn / ny, z_top=z_top, top=top, below=below, t_b=t_b, slope=np.where(inside, m['slope'], 0.0),
                    sigma_sub=m['sigma_sub'], edge=m['edge'] & inside, inside=inside, z_buried=z_b, nx=nx, ny=ny)

    def _optics(self, f, lam):
        """Per-pixel complex indices of the top layer and of the material under a transparent layer (plate pixels: None)."""
        cache = {}

        def N(sid):
            if sid not in cache:
                cache[sid] = nk(sid, lam)
            return cache[sid]
        N1 = np.array([[N(v) if v is not None else np.nan for v in row] for row in f['top']], complex)
        N2 = np.array([[N(v) if v is not None else np.nan for v in row] for row in f['below']], complex)
        return N1, N2

    def _rta_field(self, f):
        lam = LASER_WAVELENGTH_M
        N1, N2 = self._optics(f, lam)
        R = T = A = 0.0
        n = 0
        opaque = {}

        def opq(sid):
            if sid not in opaque:
                opaque[sid] = not is_transparent(sid, 1.0e-3)
            return opaque[sid]
        for j in range(f['ny']):
            for i in range(f['nx']):
                if not f['inside'][j, i]:
                    r, t, a = self.PLATE_R, 0.0, 1.0 - self.PLATE_R          # the black anodised plate absorbs what it does not reflect
                elif f['t_b'][j, i] > 0 and f['below'][j, i] is not None:
                    r, t, a = incoherent_rta(N1[j, i], N2[j, i], float(f['t_b'][j, i]), lam)
                else:
                    r, t, a = incoherent_rta(N1[j, i], N1[j, i], 0.0, lam)
                    if opq(f['top'][j, i]):
                        t, a = 0.0, t                                         # an opaque top material (metal, Si) absorbs what enters it
                R += r; T += t; A += a; n += 1
        return (R / n, T / n, A / n)

    def _vibrating(self, t0, t1):
        """Intervals of [t0, t1] with the vibration fault active (from the fault log)."""
        on = None
        state = False
        iv = []
        for (t, fault, active) in self.fault_log:
            if fault != 'vibration':
                continue
            if t <= t0:
                state = active
                continue
            if t >= t1:
                break
            if active and not state:
                on = t
            elif not active and state:
                iv.append((max(t0, on if on is not None else t0), t))
                on = None
            state = active
        if state:
            iv.append((max(t0, on if on is not None else t0), t1))
        return iv

    def _scan_begin(self):
        """Capture the field under the axis at the start of a scan (the stage and sample do not move during it)."""
        o = self.op
        ctx = o['ctx']
        lens = ctx['lens']
        lam = LASER_WAVELENGTH_M if ctx['mode'] == 'laser_confocal' else WLI_LAMBDA0_M
        f = self._field_surface(lens, ctx=ctx, stage=(self.stage['x'], self.stage['y']), lam=lam)
        ctx['field'] = f
        ctx['field_stage'] = (self.stage['x'], self.stage['y'])
        ctx['rta'] = self._rta_field(f) if ctx['mode'] == 'laser_confocal' else None
        o['scan_t0'] = self.t

    def _scan_done(self, i=0, j=0):
        o = self.op
        ctx = o['ctx']
        plan = ctx['plan']
        rng = np.random.default_rng(ctx['seed'] + 7 * (1 + i + 31 * j))
        f = ctx['field']
        mode = ctx['mode']
        if mode == 'laser_confocal':
            res = self._confocal(f, ctx, plan, o['scan_t0'], rng)
        elif mode == 'focus_variation':
            res = self._focus_variation(f, ctx, plan, o['scan_t0'], rng)
        else:
            res = self._wli(f, ctx, plan, o['scan_t0'], rng)
        self.led['frames'].add(plan['n'] * plan['passes'])
        res.update(i=i, j=j, stage=ctx['field_stage'])
        o['fields'].append(res)

    def _gain(self, S, ctx):
        g = ctx['settings']['gain']
        if g is not None:
            return g
        ref = float(np.quantile(S, 0.995))
        return AAG_TARGET / max(ref, 1e-6)

    def _confocal(self, f, ctx, plan, t0, rng):
        lens = ctx['lens']
        lam, na = LASER_WAVELENGTH_M, lens['na']
        alpha = math.asin(na)
        c = 2 * (1 - math.cos(alpha)) / lam
        N1, N2 = self._optics(f, lam)
        R1 = np.where(f['inside'], np.abs((1 - N1) / (1 + N1)) ** 2, self.PLATE_R)
        R1 = np.nan_to_num(R1, nan=self.PLATE_R)
        S2 = np.zeros_like(R1)
        z2 = np.full_like(R1, np.nan)
        tb = f['t_b']
        mb = (tb > 0) & np.array([[v is not None for v in row] for row in f['below']])
        if np.any(mb):
            with np.errstate(invalid='ignore', divide='ignore'):
                r12 = np.abs((N1 - N2) / (N1 + N2)) ** 2
            tau = np.exp(-4 * math.pi * np.imag(N1) * tb / lam)
            S2 = np.where(mb, (1 - R1) ** 2 * np.nan_to_num(r12) * tau * tau, 0.0)
            neff = np.vectorize(lambda n: n_effective(n, na) if np.isfinite(n) else 1.0)(np.real(N1))
            z2 = np.where(mb, f['z_top'] - tb / neff, np.nan)
        sig_px = np.where(f['inside'], f['sigma_sub'], self.PLATE_SIGMA)
        g = np.exp(-(4 * math.pi * sig_px / lam) ** 2)
        spec = np.clip(1 - f['slope'] / alpha, 0.0, 1.0)
        cap = spec * g + (1 - g) * DIFFUSE_COLLECTION * na * na
        S1 = R1 * cap
        S2 = S2 * cap
        G = self._gain(np.maximum(S1, S2), ctx)
        up, low = ctx['z_range']
        n, p = plan['n'], plan['pitch']
        s_z = self.truth['z_scale']
        vib = self._vibrating(t0, t0 + plan['t_scan'])
        res = [self._confocal_pass(S1, S2, f['z_top'], z2, G * mult, up, p, n, c, s_z, t0 + k * n * plan['frame_s'], plan['frame_s'], vib, rng, ctx['settings'])
               for k, mult in enumerate([1.0, DOUBLE_SCAN_GAIN][:plan['passes']])]
        zp, val, zf = res[0]
        if plan['passes'] == 2:
            zp2, val2, zf2 = res[1]
            use = (val < 0.1) & (val2 < 0.999) & np.isfinite(zp2)
            zp = np.where(use, zp2, zp); val = np.where(use, val2 / DOUBLE_SCAN_GAIN, val); zf = np.where(use, zf2, zf) if zf is not None else None
        off = rng.normal(0.0, Z_REPEAT_M)
        h = -ctx['cal_z'] * zp + off
        film = None
        if zf is not None:
            nf = ctx['settings']['film_refractive_index']
            film = ctx['cal_z'] * zf * n_effective(nf, na)
        return dict(h=h, intensity=np.clip(val, 0.0, 1.0), film=film, gain=G)

    def _confocal_pass(self, S1, S2, z1, z2, G, up, p, n, c, s_z, t0, frame_s, vib, rng, settings):
        """One Z scan: intensities of every pixel at every frame (noise, jitter, saturation), streamed peak search.  'strongest' keeps the global
        maximum; 'first' and 'film' keep the six largest local maxima and accept, after the scan, those that are not the response tail (or noise
        on it) or a noise split of a stronger maximum (SIDE_LOBE_MARGIN, SAME_PEAK_FWHM); 'first' returns the first accepted one in scan order (the
        topmost surface) and 'film' also the strongest accepted one after it (the lower interface)."""
        mode = settings['peak_mode']
        shape = S1.shape
        thr = VALID_PEAK * SIGMA_ADD
        two = bool(np.any(S2 > 0))
        z2f = np.nan_to_num(z2, nan=-1.0)
        prev2 = np.zeros(shape); prev = np.zeros(shape)
        best = np.full(shape, -1.0); bj = np.zeros(shape, int); bprev = np.zeros(shape); bnext = np.zeros(shape)
        K = 6
        kv = np.full(shape + (K,), -1.0); kj = np.full(shape + (K,), -1, int); kp = np.zeros(shape + (K,)); kn = np.zeros(shape + (K,))
        for j in range(n):
            tj = t0 + j * frame_s
            eps = rng.normal(0.0, FRAME_Z_JITTER_M)
            if any(a <= tj < b for a, b in vib):                         # the frame is scanned line by line: each line sees its own displacement
                eps = eps + VIBRATION_M * rng.standard_normal((shape[0], 1))
            zj = up + j * p
            foc = FOCUS_Z0 - (zj * (1 + s_z) + eps) - PIXEL_Z_NOISE_M * rng.standard_normal(shape)
            I = S1 * axial_response(c * (foc - z1))
            if two:
                I = I + S2 * axial_response(c * (foc - z2f))
            I = G * I
            I = I * (1 + SIGMA_REL_INTENSITY * rng.standard_normal(shape)) + SIGMA_ADD * rng.standard_normal(shape)
            I = np.clip(I, 0.0, 1.0)
            bnext = np.where(bj == j - 1, I, bnext)
            upd = I > best
            best = np.where(upd, I, best); bprev = np.where(upd, prev, bprev); bj = np.where(upd, j, bj); bnext = np.where(upd, 0.0, bnext)
            if mode != 'strongest' and j >= 2:
                lm = (prev > prev2) & (prev >= I) & (prev > thr)
                if lm.any():
                    idx = np.argmin(kv, axis=-1)
                    cur = np.take_along_axis(kv, idx[..., None], -1)[..., 0]
                    rep = lm & (prev > cur)
                    if rep.any():
                        r_, c_ = np.nonzero(rep)
                        k_ = idx[r_, c_]
                        kv[r_, c_, k_] = prev[r_, c_]; kj[r_, c_, k_] = j - 1; kp[r_, c_, k_] = prev2[r_, c_]; kn[r_, c_, k_] = I[r_, c_]
            prev2, prev = prev, I

        def refine(jj, v0, vm, vp):
            ok = (jj >= 1) & (jj <= n - 2) & (v0 > thr) & (vm > 0) & (vp > 0)
            lm_, l0, lp = np.log(np.maximum(vm, 1e-12)), np.log(np.maximum(v0, 1e-12)), np.log(np.maximum(vp, 1e-12))
            den = lm_ - 2 * l0 + lp
            d = np.where(np.abs(den) > 1e-12, 0.5 * (lm_ - lp) / np.where(np.abs(den) > 1e-12, den, 1.0), 0.0)
            d = np.clip(d, -0.5, 0.5)
            return np.where(ok, up + (jj + d) * p, np.nan)
        if mode == 'strongest':
            zp = refine(bj, best, bprev, bnext)
            return zp, np.where(np.isfinite(zp), best, 0.0), None

        # a local maximum is a surface only if it is not a noise split of a stronger local maximum (closer than SAME_PEAK_FWHM) and rises above
        # SIDE_LOBE_MARGIN x the summed response tails of all stronger maxima of the pixel plus the noise threshold (noise riding on the tails)
        x = c * p * np.abs(kj[..., :, None] - kj[..., None, :]).astype(float)          # [.., k, m] distance of k from m in units of 1/c
        stronger = (kv[..., None, :] > kv[..., :, None]) & (kj[..., None, :] >= 0)
        split = (stronger & (x < SAME_PEAK_FWHM * axial_fwhm_x())).any(axis=-1)
        tail = np.where(stronger, kv[..., None, :] * axial_envelope(x), 0.0).sum(axis=-1)
        acc = (kj >= 0) & (kv > thr) & ~split & (kv >= SIDE_LOBE_MARGIN * tail + thr)

        def pick(sel, key):
            i = np.argmin(np.where(sel, key, np.inf), axis=-1)[..., None]
            has = np.take_along_axis(sel, i, -1)[..., 0]
            g = lambda a: np.take_along_axis(a, i, -1)[..., 0]
            return has, g(kj), g(kv), g(kp), g(kn)
        h1, fj, fv, fp, fn = pick(acc, kj.astype(float))
        z_first = np.where(h1, refine(fj, fv, fp, fn), np.nan)
        if mode == 'first':
            return z_first, np.where(np.isfinite(z_first), fv, 0.0), None
        sel2 = acc & (kj > fj[..., None]) & h1[..., None]
        h2, sj, sv, sp, sn = pick(sel2, -kv)
        z_second = np.where(h2, refine(sj, sv, sp, sn), np.nan)
        film = np.where(np.isfinite(z_first) & np.isfinite(z_second), z_second - z_first, np.nan)
        return z_first, np.where(np.isfinite(z_first), fv, 0.0), film

    def _vibration_fraction(self, t0, plan):
        iv = self._vibrating(t0, t0 + plan['t_scan'])
        return sum(b - a for a, b in iv) / plan['t_scan'] if plan['t_scan'] > 0 else 0.0

    def _in_scan(self, zt, ctx, margin):
        """Pixels whose in-focus Z (reported coordinate) lies inside the scanned range with `margin` on both sides (the peak / envelope must be
        bracketed by frames); others have no data."""
        up, low = ctx['z_range']
        return (zt >= up + margin) & (zt <= low - margin)

    def _focus_variation(self, f, ctx, plan, t0, rng):
        lens = ctx['lens']
        sig = fv_spec_sigma(lens['mag'])
        lam = 0.55e-6
        N1, N2 = self._optics(f, lam)
        R1 = np.where(f['inside'], np.nan_to_num(np.abs((1 - N1) / (1 + N1)) ** 2, nan=self.PLATE_R), self.PLATE_R)
        target = f['z_top'].copy()
        mb = f['t_b'] > 0
        if np.any(mb):
            with np.errstate(invalid='ignore', divide='ignore'):
                r12 = np.nan_to_num(np.abs((N1 - N2) / (N1 + N2)) ** 2)
            tau = np.exp(-4 * math.pi * np.imag(N1) * f['t_b'] / lam)
            brighter = mb & ((1 - R1) ** 2 * r12 * tau * tau > R1)
            neff = np.vectorize(lambda n: n_effective(n, lens['na']) if np.isfinite(n) else 1.0)(np.real(N1))
            target = np.where(brighter, f['z_top'] - f['t_b'] / neff, target)
        top = f['top']
        medge = np.zeros(top.shape, bool)
        medge[:, 1:] |= top[:, 1:] != top[:, :-1]; medge[1:, :] |= top[1:, :] != top[:-1, :]
        sig_px = np.where(f['inside'], f['sigma_sub'], self.PLATE_SIGMA)
        tex = np.clip(np.sqrt((sig_px / FV_TEXTURE_REF_M) ** 2 + (f['edge'] | medge).astype(float)), 0.0, 1.0)
        valid = (tex >= 0.1) & (f['slope'] < math.radians(80))
        s_z = self.truth['z_scale']
        zt = (FOCUS_Z0 - target) / (1 + s_z)
        valid = valid & self._in_scan(zt, ctx, plan['pitch'])          # the contrast maximum must lie between two scanned frames
        noise = rng.standard_normal(target.shape) * sig / np.maximum(tex, 0.05)
        fv = self._vibration_fraction(t0, plan)
        if fv > 0:                                                    # floor vibration blurs the focus curves (engineering: 1 sigma per pixel)
            noise = noise + VIBRATION_M * math.sqrt(fv) * rng.standard_normal(target.shape)
        zp = np.where(valid, zt + noise, np.nan)
        h = -ctx['cal_z'] * zp + rng.normal(0.0, Z_REPEAT_M)
        return dict(h=h, intensity=np.where(valid, np.clip(tex, 0, 1), 0.0), film=None, gain=None)

    def _wli(self, f, ctx, plan, t0, rng):
        lens = ctx['lens']
        lam = WLI_LAMBDA0_M
        N1, N2 = self._optics(f, lam)
        inside = f['inside']
        r01 = np.where(inside, (1 - N1) / (1 + N1), -math.sqrt(self.PLATE_R))
        r01 = np.where(np.isfinite(r01), r01, -math.sqrt(self.PLATE_R))
        z_eff = f['z_top'].copy()
        r_eff = r01.copy()
        mb = (f['t_b'] > 0) & np.array([[v is not None for v in row] for row in f['below']])
        if np.any(mb):
            for jj, ii in zip(*np.nonzero(mb)):
                t_ = float(f['t_b'][jj, ii]); n1, n2 = N1[jj, ii], N2[jj, ii]
                if n1.real * t_ > WLI_COHERENCE_M:                  # separated envelopes: the stronger one is measured
                    r12 = (n1 - n2) / (n1 + n2)
                    a_bot = (1 - abs(r01[jj, ii]) ** 2) * abs(r12) * math.exp(-4 * math.pi * n1.imag * t_ / lam)
                    if a_bot > abs(r01[jj, ii]):
                        z_eff[jj, ii] = f['z_top'][jj, ii] - n1.real * t_
                        r_eff[jj, ii] = -a_bot
                else:
                    r_eff[jj, ii] = complex(coherent_r(n1, n2, t_, lam))
        phi = np.angle(-r_eff)
        R = np.abs(r_eff) ** 2
        sig_px = np.where(inside, f['sigma_sub'], self.PLATE_SIGMA)
        smooth = sig_px < lam / 8
        noise_sd = np.where(smooth, WLI_TOPO_REPEAT_M * np.sqrt(0.3 / np.maximum(R, 1e-3)), 20e-9 + 0.3 * sig_px)
        valid = (f['slope'] < math.asin(WLI_SLOPE_NA)) & (R > 1e-3)
        z_meas = z_eff - np.where(smooth, phi * lam / (4 * math.pi), 0.0)
        s_z = self.truth['z_scale']
        zt = (FOCUS_Z0 - z_meas) / (1 + s_z)
        valid = valid & self._in_scan((FOCUS_Z0 - z_eff) / (1 + s_z), ctx, max(plan['pitch'], 0.5 * WLI_COHERENCE_M))   # the envelope must be scanned
        fv = self._vibration_fraction(t0, plan)
        if fv > 0:                                                    # vibration during the phase steps: height noise (engineering: 0.5 x amplitude)
            noise_sd = np.sqrt(noise_sd ** 2 + fv * (0.5 * VIBRATION_M) ** 2)
        zp = np.where(valid, zt + noise_sd * rng.standard_normal(zt.shape), np.nan)
        h = -ctx['cal_z'] * zp + rng.normal(0.0, 0.05e-9)
        return dict(h=h, intensity=np.where(valid, np.clip(R / max(float(np.max(R)), 1e-6), 0, 1), 0.0), film=None, gain=None)

    def _finish_measurement(self):
        o = self.op
        r = self.records[o['record_id']]
        fr = o['fields'][0]
        f = o['ctx']['field']
        cal_xy = o['ctx']['cal_xy']
        self._write_map(r, fr['h'], fr['intensity'], fr['film'], f['dx_nom'] * cal_xy, f['dy_nom'] * cal_xy)
        r.update(gain=fr['gain'], n_frames=o['ctx']['plan']['n'] * o['ctx']['plan']['passes'])
        self._complete(o, r)

    def _write_map(self, r, h, inten, film, dx, dy):
        r['height_m'] = _nan_to_none(h)
        r['intensity'] = _nan_to_none(inten)
        r['film_thickness_m'] = _nan_to_none(film) if film is not None else None
        r['grid'] = [int(h.shape[1]), int(h.shape[0])]
        r['pixel_pitch_m'] = [dx, dy]
        r['fov_m'] = [dx * h.shape[1], dy * h.shape[0]]
        v = np.isfinite(h)
        r['valid_fraction'] = float(v.mean())
        if v.any():
            hv = h[v]
            self.last_value = float(np.quantile(hv, 0.99) - np.quantile(hv, 0.01))
            self.last_value_rid = r['record_id']
        subs = sorted({x for x in np.asarray(self.op['ctx']['field']['top']).ravel().tolist() + np.asarray(self.op['ctx']['field']['below']).ravel().tolist() if x})
        r['template_source'] = dict(library=LIBRARY_REL, substances=subs)

    def _complete(self, o, r):
        r.update(status='completed', finished_s=self.t, duration_s=self.t - r['started_s'])
        self.operations[o['op']].update(phase='completed', finished_s=self.t)
        self._event('measurement', 'MEASURE_DONE', f'记录{r["record_id"]}完成（有效像素{(r["valid_fraction"] or 0) * 100:.1f}%）', rid=r['record_id'])
        self.op = None

    def _finish_stitch(self, nx, ny, overlap):
        o = self.op
        r = self.records[o['record_id']]
        f0 = o['ctx']['field']
        px_, py_ = SIM_GRID
        sx_ = max(1, int(round(px_ * (1 - overlap)))); sy_ = max(1, int(round(py_ * (1 - overlap))))
        W, H = (nx - 1) * sx_ + px_, (ny - 1) * sy_ + py_
        acc = np.zeros((H, W)); cnt = np.zeros((H, W)); ia = np.zeros((H, W))
        placed = {}
        for fr in sorted(o['fields'], key=lambda d: (d['j'], d['i'])):
            h = fr['h'].copy()
            x0, y0 = fr['i'] * sx_, fr['j'] * sy_
            win = acc[y0:y0 + py_, x0:x0 + px_]
            wc = cnt[y0:y0 + py_, x0:x0 + px_]
            ov = (wc > 0) & np.isfinite(h)
            if ov.sum() > 20:                                             # height matching on the overlap (software correction)
                h = h + float(np.nanmedian((win / np.where(wc > 0, wc, 1))[ov] - h[ov]))
            ok = np.isfinite(h)
            acc[y0:y0 + py_, x0:x0 + px_] += np.where(ok, h, 0.0); cnt[y0:y0 + py_, x0:x0 + px_] += ok
            ia[y0:y0 + py_, x0:x0 + px_] += np.where(ok, fr['intensity'], 0.0)
            placed[(fr['i'], fr['j'])] = True
        m = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
        it = np.where(cnt > 0, ia / np.maximum(cnt, 1), 0.0)
        k = max(1, int(math.ceil(max(W / 320, H / 240))))
        if k > 1:
            Hc, Wc = (H // k) * k, (W // k) * k
            mm = m[:Hc, :Wc].reshape(Hc // k, k, Wc // k, k)
            m = np.nanmean(np.where(np.isfinite(mm), mm, np.nan), axis=(1, 3)) if np.isfinite(mm).any() else mm[:, 0, :, 0]
            it = it[:Hc, :Wc].reshape(Hc // k, k, Wc // k, k).mean(axis=(1, 3))
        cal_xy = o['ctx']['cal_xy']
        self._write_map(r, m, it, None, f0['dx_nom'] * cal_xy * k, f0['dy_nom'] * cal_xy * k)
        r.update(n_frames=o['ctx']['plan']['n'] * o['ctx']['plan']['passes'] * nx * ny, note_zh=f'{nx}×{ny}视场、重叠{overlap:.0%}拼接，按重叠区中值匹配高度')
        self._complete(o, r)

    def _film_stack_centre(self, ctx):
        lens = ctx['lens']
        f = self._field_surface(lens, coarse=True, ctx=ctx)
        j, i = f['ny'] // 2, f['nx'] // 2
        return f['top'][j, i], f['below'][j, i], float(f['t_b'][j, i]), bool(f['inside'][j, i])

    def _finish_film(self):
        o = self.op
        ctx = o['ctx']
        r = self.records[o['record_id']]
        top, below, t_b, inside = self._film_stack_centre(ctx)
        rng = np.random.default_rng(ctx['seed'] + 99)
        lam_nm = np.arange(FILM_BAND_NM[0], FILM_BAND_NM[1] + 1e-9, 2.0)
        lam = lam_nm * 1e-9
        # the spectrometer collects through the objective: each reflected beam couples with the amplitude sqrt(eta) of its defocus (axial response
        # of the objective at the 600 nm band centre): the top-surface beam at the top, the buried-interface beam at its apparent depth
        zs = self._surface_z_at(*self._axis_in_sample())
        zf = self._focus_true(self.z)
        c600 = 2 * (1 - math.sqrt(1 - ctx['lens']['na'] ** 2)) / 600e-9
        eta_top = float(axial_response(np.array(c600 * (zf - zs))))
        eta_int = None
        if inside and top is not None:
            N1 = nk_array(top, lam_nm)
            r01 = (1 - N1) / (1 + N1)
            r_true = math.sqrt(eta_top) * r01
            if below is not None and t_b > 0:
                eta_int = float(axial_response(np.array(c600 * (zf - (zs - t_b / n_effective(nk(top, 600e-9).real, ctx['lens']['na']))))))
                r12 = r_interface(N1, nk_array(below, lam_nm))
                ph = np.exp(2j * 2 * np.pi * N1 * t_b / lam)
                r_true = r_true + math.sqrt(eta_int) * (1 - r01 ** 2) * r12 * ph / (1 + r01 * r12 * ph)
            R_true = np.abs(r_true) ** 2
        else:
            R_true = np.full(lam_nm.shape, eta_top * self.PLATE_R)
        eta = max(eta_top, eta_int or 0.0)
        lam_meas_nm = lam_nm * (1 + self.truth['film_lambda_scale'])
        R_meas = R_true * (1 + FILM_NOISE_REL * rng.standard_normal(lam_nm.shape)) + FILM_NOISE_ABS * rng.standard_normal(lam_nm.shape)
        Nf, Ns = nk_array(ctx['film_id'], lam_meas_nm), nk_array(ctx['substrate_id'], lam_meas_nm)
        lm = lam_meas_nm * 1e-9
        rf01 = (1 - Nf) / (1 + Nf)
        rf12 = r_interface(Nf, Ns)

        n_pts = R_meas.size
        RR = float(R_meas @ R_meas)

        def columns(ds_):
            # R = |a rf01 + b B(d)|^2 = a^2 |rf01|^2 + b^2 |B|^2 + a b 2 Re(conj(rf01) B) for every thickness in ds_ (rows): shape (nd, 3, n)
            ph_ = np.exp(2j * 2 * np.pi * np.multiply.outer(np.atleast_1d(ds_), Nf / lm))
            B = (1 - rf01 ** 2) * rf12 * ph_ / (1 + rf01 * rf12 * ph_)
            return np.stack([np.broadcast_to(np.abs(rf01) ** 2, B.shape), np.abs(B) ** 2, 2 * np.real(np.conj(rf01) * B)], axis=1)
        n_eff_fit = n_effective(nk(ctx['film_id'], 600e-9).real, ctx['lens']['na'])

        def tied(ds_, xs):
            """Least squares of the coherent two-beam model for every thickness in ds_ and focus offset xs (axial-response units at 600 nm, focus
            minus top surface): the collected fractions follow the objective's own axial response, a^2 = g P(x) for the top surface and
            b^2 = g P(x + c600 d / n_eff) for the buried interface, with one gain 0 <= g <= FILM_GAIN_MAX (reference-normalised spectrum);
            closed form in g from the Gram matrix of the three columns."""
            ds_ = np.atleast_1d(ds_)
            C = columns(ds_)
            et = np.broadcast_to(axial_response(xs)[None, :], (ds_.size, xs.size))
            ei = axial_response(xs[None, :] + c600 * ds_[:, None] / n_eff_fit)
            U = np.stack([et, ei, np.sqrt(et * ei)], axis=-1)
            num = np.einsum('dxi,di->dx', U, C @ R_meas)
            den = np.einsum('dxi,dxi->dx', np.einsum('dxi,dij->dxj', U, C @ C.transpose(0, 2, 1)), U)
            g = np.clip(num / np.maximum(den, 1e-30), 0.0, FILM_GAIN_MAX)
            return np.maximum((g * g * den - 2 * g * num + RR) / n_pts, 0.0), g, U, C
        xs_coarse, xs_fine = np.linspace(-AXIAL_X_MAX, AXIAL_X_MAX, 481), np.linspace(-AXIAL_X_MAX, AXIAL_X_MAX, 4801)
        ds = np.geomspace(20e-9, 8.0e-6, 3000)
        res_c = np.concatenate([tied(ds[i:i + 500], xs_coarse)[0].min(axis=1) for i in range(0, ds.size, 500)])
        bd = float(ds[int(np.argmin(res_c))])
        a, b = bd / 1.003, bd * 1.003
        gr = (math.sqrt(5) - 1) / 2

        def fr(d):
            return float(tied(d, xs_fine)[0].min())
        c_, d_ = b - gr * (b - a), a + gr * (b - a)
        fc, fd = fr(c_), fr(d_)
        for _ in range(60):
            if fc < fd:
                b, d_, fd = d_, c_, fc; c_ = b - gr * (b - a); fc = fr(c_)
            else:
                a, c_, fc = c_, d_, fd; d_ = a + gr * (b - a); fd = fr(d_)
        d_fit = 0.5 * (a + b)
        res0, g0, U0, C0 = tied(d_fit, xs_fine)
        k = int(np.argmin(res0[0]))
        w = g0[0, k] * U0[0, k]                                                  # fitted top / interface fractions and their coherent product
        fit0 = w @ C0[0]
        resid = math.sqrt(float(res0[0, k])) / max(abs(float(np.mean(R_meas))), 1e-6)
        visibility = float((fit0.max() - fit0.min()) / max(fit0.max() + fit0.min(), 1e-12))     # Michelson contrast of the fitted fringes
        # the thickness lives in the interference term: it must stand clearly above the spectrometer noise, and the film model must explain the
        # spectrum better than the named substrate without a film (free scale and a flat background: a bare substrate, a film index-matched to it
        # and the black sample plate have no fringes)
        sigma = math.sqrt(float(np.mean((FILM_NOISE_REL * R_meas) ** 2)) + FILM_NOISE_ABS ** 2)
        fringe_snr = float(w[2]) * float(np.ptp(C0[0, 2])) / sigma
        Rs = np.abs((1 - Ns) / (1 + Ns)) ** 2
        A_bare = np.column_stack([Rs, np.ones_like(Rs)])
        sol = np.linalg.lstsq(A_bare, R_meas, rcond=None)[0]
        if sol.min() < 0.0:                                                      # non-negative scale and background: best boundary solution
            sol = min((np.array([max(float(Rs @ R_meas) / max(float(Rs @ Rs), 1e-30), 0.0), 0.0]), np.array([0.0, max(float(np.mean(R_meas)), 0.0)])),
                      key=lambda v: float(np.mean((A_bare @ v - R_meas) ** 2)))
        bare_gain = math.sqrt(max(float(np.mean((A_bare @ sol - R_meas) ** 2)) - float(res0[0, k]), 0.0)) / sigma
        fringes = fringe_snr >= FILM_FRINGE_SNR and bare_gain >= FILM_BARE_GAIN
        w_top, w_int = float(w[0]), float(w[1])
        # a film opaque over the fitted thickness returns no light from its lower interface: the spectrum carries no thickness information
        tau_max = float(np.max(np.exp(-4 * np.pi * np.imag(Nf) * d_fit / lm)))
        opaque = tau_max < 1.0e-2
        d_fit = float(d_fit + rng.normal(0.0, FILM_REPEAT_M))
        ok = FILM_RANGE_M[0] <= d_fit <= FILM_RANGE_M[1] and resid < 0.03 and eta >= FILM_MIN_SIGNAL and not opaque and fringes
        fit = w @ columns(d_fit)[0]
        r['film'] = dict(thickness_m=d_fit if ok else None, fit_residual=resid, film_substance_id=ctx['film_id'], substrate_substance_id=ctx['substrate_id'],
                         wavelength_m=[float(x) * 1e-9 for x in lam_meas_nm], reflectance=[float(x) for x in R_meas], fit=[float(x) for x in fit], in_range=bool(ok),
                         collected_fraction=eta_top, collected_fraction_interface=eta_int, fringe_visibility=float(np.clip(visibility, 0.0, 1.0)),
                         fit_collected_fractions=[w_top, w_int], fringe_to_noise=float(fringe_snr), gain_over_bare_substrate=float(bare_gain))
        r['template_source'] = dict(library=LIBRARY_REL, substances=sorted({x for x in (top, below, ctx['film_id'], ctx['substrate_id']) if x}))
        r['valid_fraction'] = 1.0 if ok else 0.0
        if ok:
            self.last_value = d_fit
            self.last_value_rid = r['record_id']
        r['note_zh'] = '' if ok else ('失焦：收集到的光只有' + format(eta, '.3f') + '，信号不足' if eta < FILM_MIN_SIGNAL else
                                     '所指定的膜材料在拟合厚度下不透明（金属或强吸收膜），光谱干涉无法测厚' if opaque else
                                     '光谱中没有膜干涉条纹（无膜、膜与衬底折射率相近或光轴不在样品上），不能测厚' if not fringes else
                                     '拟合厚度超出100–5000 nm或拟合残差过大（膜材料不符或视场中心无膜）')
        self._complete(o, r)

    # ---------------------------------------------------------------- analysis
    def _rec(self, rid, kinds=('height_map', 'stitched_map')):
        r = self.records.get(rid)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        if r['kind'] not in kinds or r['status'] != 'completed' or r.get('height_m') is None:
            raise ActionRejected('RECORD_NOT_SUITABLE')
        return r

    @staticmethod
    def _coords(r):
        h = _none_to_nan(r['height_m'])
        ny, nx = h.shape
        dx, dy = r['pixel_pitch_m']
        x = (np.arange(nx) - (nx - 1) / 2) * dx
        y = (np.arange(ny) - (ny - 1) / 2) * dy
        return h, x, y

    @staticmethod
    def _level(h, X, Y, how):
        if how == 'none':
            return h
        v = np.isfinite(h)
        if v.sum() < 3:
            return h
        A = np.column_stack([X[v], Y[v], np.ones(v.sum())])
        coef, *_ = np.linalg.lstsq(A, h[v], rcond=None)
        return h - (coef[0] * X + coef[1] * Y + coef[2])

    @staticmethod
    def _gauss_filter(z, dx, dy, cutoff):
        """ISO 16610-61 Gaussian filter (alpha = sqrt(ln2/pi)) as a normalised separable convolution that tolerates missing pixels."""
        sig_x = 0.1874 * cutoff / dx
        sig_y = 0.1874 * cutoff / dy
        v = np.isfinite(z).astype(float)
        zz = np.where(v > 0, z, 0.0)

        def conv(a, s, axis):
            if s < 0.3:
                return a
            half = int(math.ceil(4 * s))
            k = np.exp(-0.5 * (np.arange(-half, half + 1) / s) ** 2)
            return np.apply_along_axis(lambda m: np.convolve(m, k, mode='same'), axis, a)
        num = conv(conv(zz, sig_x, 1), sig_y, 0)
        den = conv(conv(v, sig_x, 1), sig_y, 0)
        return np.where(den > 1e-6, num / np.maximum(den, 1e-12), np.nan)

    def _cmd_data_roughness(self, record_id, region, leveling, s_filter_m, l_filter_m, profile_axis, lambda_c_m):
        r = self._rec(record_id)
        h, x, y = self._coords(r)
        X, Y = np.meshgrid(x, y)
        if region is not None:
            sel = (X >= min(region['x0_m'], region['x1_m'])) & (X <= max(region['x0_m'], region['x1_m'])) & (Y >= min(region['y0_m'], region['y1_m'])) & \
                  (Y <= max(region['y0_m'], region['y1_m']))
            rows, cols = np.nonzero(sel)
            if rows.size < 16:
                raise ActionRejected('REGION_TOO_SMALL')
            h, X, Y = h[rows.min():rows.max() + 1, cols.min():cols.max() + 1], X[rows.min():rows.max() + 1, cols.min():cols.max() + 1], Y[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
        dx, dy = r['pixel_pitch_m']
        z = self._level(h, X, Y, leveling)
        if s_filter_m is not None:
            z = self._gauss_filter(z, dx, dy, s_filter_m)
        if l_filter_m is not None:
            z = z - self._gauss_filter(z, dx, dy, l_filter_m)
        v = np.isfinite(z)
        if v.sum() < 16:
            raise ActionRejected('TOO_FEW_VALID_PIXELS')
        zc = z - np.nanmean(z)
        zv = zc[v]
        sq = float(np.sqrt(np.mean(zv ** 2)))
        gy, gx = np.gradient(zc, dy, dx)
        gv = np.isfinite(gx) & np.isfinite(gy)
        areal = dict(sa_m=float(np.mean(np.abs(zv))), sq_m=sq, sz_m=float(zv.max() - zv.min()), sp_m=float(zv.max()), sv_m=float(-zv.min()),
                     ssk=float(np.mean(zv ** 3) / sq ** 3) if sq > 0 else None, sku=float(np.mean(zv ** 4) / sq ** 4) if sq > 0 else None,
                     sdq=float(np.sqrt(np.mean(gx[gv] ** 2 + gy[gv] ** 2))) if gv.any() else None,
                     sdr=float(np.mean(np.sqrt(1 + gx[gv] ** 2 + gy[gv] ** 2)) - 1) if gv.any() else None)
        line = zc[zc.shape[0] // 2, :] if profile_axis == 'x' else zc[:, zc.shape[1] // 2]
        dl = dx if profile_axis == 'x' else dy
        L = dl * line.size
        lc = lambda_c_m if lambda_c_m is not None else min(0.8e-3, L / 5)
        note = '' if lambda_c_m is not None or L >= 4.0e-3 else f'评定长度{L * 1e6:.0f} µm不足5×0.8 mm，λc自动取L/5'
        prof = dict(ra_m=None, rq_m=None, rz_m=None, rp_m=None, rv_m=None, rsk=None, rku=None, lambda_c_m=lc, evaluation_length_m=L)
        lv = np.isfinite(line)
        if lv.sum() >= 8:
            li = np.interp(np.arange(line.size), np.nonzero(lv)[0], line[lv])
            sig = 0.1874 * lc / dl
            half = int(math.ceil(4 * sig))
            k = np.exp(-0.5 * (np.arange(-half, half + 1) / max(sig, 1e-9)) ** 2); k /= k.sum()
            wav = np.convolve(np.pad(li, half, mode='reflect'), k, mode='valid') if half > 0 else li
            rr = li - wav
            rq = float(np.sqrt(np.mean(rr ** 2)))
            seg = np.array_split(rr, 5) if rr.size >= 10 else [rr]
            prof.update(ra_m=float(np.mean(np.abs(rr))), rq_m=rq, rp_m=float(rr.max()), rv_m=float(-rr.min()), rz_m=float(np.mean([s.max() - s.min() for s in seg])),
                        rsk=float(np.mean(rr ** 3) / rq ** 3) if rq > 0 else None, rku=float(np.mean(rr ** 4) / rq ** 4) if rq > 0 else None)
        self.last_value = areal['sa_m']
        self.last_value_rid = record_id
        self.revision += 1                                                # the displayed result changed
        return dict(record_id=record_id, leveling=leveling, valid_pixels=int(v.sum()), areal=areal, profile=prof, note_zh=note)

    def _step(self, r, axis, lower, upper, leveling):
        h, x, y = self._coords(r)
        X, Y = np.meshgrid(x, y)
        prof = np.nanmean(np.where(np.isfinite(h), h, np.nan), axis=0 if axis == 'x' else 1) if np.isfinite(h).any() else None
        coord = x if axis == 'x' else y
        if prof is None:
            return None
        if lower is None or upper is None:
            v = np.isfinite(h)
            if v.sum() < 20:
                return None

            def kmeans(zv):
                c1, c2 = np.quantile(zv, 0.1), np.quantile(zv, 0.9)
                lab = None
                for _ in range(50):
                    lab = np.abs(zv - c1) > np.abs(zv - c2)
                    if lab.all() or (~lab).all():
                        return None
                    n1, n2 = zv[~lab].mean(), zv[lab].mean()
                    if abs(n1 - c1) < 1e-15 and abs(n2 - c2) < 1e-15:
                        break
                    c1, c2 = n1, n2
                return lab
            zv = h[v]
            lab = kmeans(zv)
            if lab is None:
                return None
            plane = np.zeros_like(zv)
            if leveling == 'plane':
                # the plane is fitted TOGETHER with the step (z = a x + b y + c + d label), relabelling on the plane-removed heights until the
                # labels no longer change: a plane fitted through the step alone would tilt into it and shrink the step.  Starts: labels of the
                # raw heights, and labels after removing the robust tilt (median of the pixel-to-pixel height differences: the step edge is only a
                # few pixels) - a tilt larger than the step dominates the raw heights; the better converged fit wins
                A0 = np.column_stack([X[v], Y[v], np.ones(int(v.sum()))])
                with np.errstate(all='ignore'):
                    gx = float(np.nanmedian(np.diff(h, axis=1))) / float(X[0, 1] - X[0, 0]) if h.shape[1] > 1 else 0.0
                    gy = float(np.nanmedian(np.diff(h, axis=0))) / float(Y[1, 0] - Y[0, 0]) if h.shape[0] > 1 else 0.0
                gx, gy = (gx if math.isfinite(gx) else 0.0), (gy if math.isfinite(gy) else 0.0)
                best = None
                for start in (lab, kmeans(zv - (gx * X[v] + gy * Y[v]))):
                    lab_ = start
                    if lab_ is None:
                        continue
                    for _ in range(30):
                        coef, *_ = np.linalg.lstsq(np.column_stack([A0, lab_.astype(float)]), zv, rcond=None)
                        new = kmeans(zv - A0 @ coef[:3])
                        if new is None or (new == lab_).all():
                            break
                        lab_ = new
                    coef, *_ = np.linalg.lstsq(np.column_stack([A0, lab_.astype(float)]), zv, rcond=None)
                    sse = float(np.sum((zv - np.column_stack([A0, lab_.astype(float)]) @ coef) ** 2))
                    if best is None or sse < best[0]:
                        best = (sse, lab_, A0 @ coef[:3])
                if best is None:
                    return None
                _, lab, plane = best
            zl = zv - plane
            lo_, hi_ = zl[~lab], zl[lab]
            se = math.sqrt(lo_.var() / lo_.size + hi_.var() / hi_.size)
            return dict(step=float(hi_.mean() - lo_.mean()), se=se, lower=float(lo_.mean()), upper=float(hi_.mean()), n_lower=int(lo_.size), n_upper=int(hi_.size),
                        method='histogram (two-level k-means' + (', plane fitted jointly with the step)' if leveling == 'plane' else ')'))
        ml = (coord >= min(lower)) & (coord <= max(lower)) & np.isfinite(prof)
        mu = (coord >= min(upper)) & (coord <= max(upper)) & np.isfinite(prof)
        if ml.sum() < 3 or mu.sum() < 3:
            return None
        # two parallel lines (one common slope, one offset per band) fitted to both bands: the slope comes from both sides of the edge, so the
        # step is not an extrapolation of a line fitted to one band only (ISO 5436-1 type A1 evaluation adapted to a single step)
        xs = np.concatenate([coord[ml], coord[mu]]); zs = np.concatenate([prof[ml], prof[mu]])
        ind = np.concatenate([np.zeros(int(ml.sum())), np.ones(int(mu.sum()))])
        A = np.column_stack([xs, np.ones_like(xs), ind])
        coef, *_ = np.linalg.lstsq(A, zs, rcond=None)
        resid = zs - A @ coef
        dof = max(1, zs.size - 3)
        cov = np.linalg.inv(A.T @ A) * float(resid @ resid) / dof
        return dict(step=float(coef[2]), se=float(math.sqrt(max(cov[2, 2], 0.0))), lower=float(np.mean(prof[ml])), upper=float(np.mean(prof[mu])),
                    n_lower=int(ml.sum()), n_upper=int(mu.sum()),
                    method='ISO 5436-1 type A1 (single step): two parallel lines with a common slope fitted to the lower and upper bands')

    def _cmd_data_step_height(self, record_id, axis, lower, upper, leveling):
        r = self._rec(record_id)
        s = self._step(r, axis, lower, upper, leveling)
        if s is None:
            return dict(record_id=record_id, axis=axis, step_height_m=None, std_error_m=None, lower_level_m=None, upper_level_m=None, method='none',
                        n_lower=0, n_upper=0, note_zh='区域内有效像素不足或未找到两个高度水平')
        self.last_value = abs(s['step'])
        self.last_value_rid = record_id
        self.revision += 1                                                # the displayed result changed
        return dict(record_id=record_id, axis=axis, step_height_m=s['step'], std_error_m=s['se'], lower_level_m=s['lower'], upper_level_m=s['upper'], method=s['method'],
                    n_lower=s['n_lower'], n_upper=s['n_upper'], note_zh='')

    def _cmd_data_profile(self, record_id, start_m, end_m, points):
        r = self._rec(record_id)
        h, x, y = self._coords(r)
        xs = np.linspace(start_m[0], end_m[0], points); ys = np.linspace(start_m[1], end_m[1], points)
        dx, dy = r['pixel_pitch_m']
        fi = (xs - x[0]) / dx; fj = (ys - y[0]) / dy
        out = []
        for a, b_ in zip(fi, fj):
            i0, j0 = int(math.floor(a)), int(math.floor(b_))
            if i0 < 0 or j0 < 0 or i0 + 1 >= h.shape[1] or j0 + 1 >= h.shape[0]:
                out.append(None); continue
            u, v = a - i0, b_ - j0
            q = h[j0, i0] * (1 - u) * (1 - v) + h[j0, i0 + 1] * u * (1 - v) + h[j0 + 1, i0] * (1 - u) * v + h[j0 + 1, i0 + 1] * u * v
            out.append(float(q) if math.isfinite(q) else None)
        d = np.hypot(xs - xs[0], ys - ys[0])
        return dict(distance_m=[float(v) for v in d], height_m=out)

    def _cmd_calibration_z_scale(self, record_id, certified_height_m, axis, lower, upper):
        self._need_plant()
        self._need_idle()
        r = self._rec(record_id, ('height_map',))
        s = self._step(r, axis, lower, upper, 'plane')
        if s is None or abs(s['step']) < 1e-12:
            raise ActionRejected('STEP_NOT_FOUND')
        used = r['_cal_z']
        raw = abs(s['step']) / used
        self.seq['ca'] += 1
        cid = f'CAZ{self.seq["ca"]:03d}'
        self.cal['z'] = dict(factor=certified_height_m / raw, calibration_id=cid, calibrated_s=self.t)
        self._event('calibration', 'Z_SCALE', f'Z标尺校准{cid}：系数{self.cal["z"]["factor"]:.5f}')
        return dict(factor=self.cal['z']['factor'], measured_height_m=abs(s['step']), calibration_id=cid)

    def _cmd_calibration_xy_scale(self, record_id, certified_pitch_m, axis):
        self._need_plant()
        self._need_idle()
        r = self._rec(record_id, ('height_map',))
        h, x, y = self._coords(r)
        prof = np.nanmean(h, axis=0 if axis == 'x' else 1)
        dl = r['pixel_pitch_m'][0 if axis == 'x' else 1]
        v = np.isfinite(prof)
        if v.sum() < 16:
            raise ActionRejected('PITCH_NOT_FOUND')
        p = np.interp(np.arange(prof.size), np.nonzero(v)[0], prof[v])
        p = (p - np.polyval(np.polyfit(np.arange(p.size), p, 1), np.arange(p.size))) * np.hanning(p.size)
        nfft = 16 * p.size
        spec = np.abs(np.fft.rfft(p, nfft))
        k = int(np.argmax(spec[1:])) + 1
        if k <= 1 or k >= spec.size - 1:
            raise ActionRejected('PITCH_NOT_FOUND')
        a_, b_, c_ = spec[k - 1], spec[k], spec[k + 1]
        dk = 0.5 * (a_ - c_) / (a_ - 2 * b_ + c_) if (a_ - 2 * b_ + c_) != 0 else 0.0
        pitch = nfft * dl / (k + dk)
        lid = next(L['id'] for L in LENSES if L['label'] == r['objective'])
        raw = pitch / r['_cal_xy']
        self.seq['ca'] += 1
        cid = f'CAX{self.seq["ca"]:03d}'
        self.cal['xy'][lid] = certified_pitch_m / raw
        self._event('calibration', 'XY_SCALE', f'横向标尺校准{cid}（{r["objective"]}）：系数{self.cal["xy"][lid]:.5f}')
        return dict(factor=self.cal['xy'][lid], measured_pitch_m=pitch, objective=r['objective'], calibration_id=cid)

    # ---------------------------------------------------------------- queries
    @staticmethod
    def _out(r):
        return {k: copy.deepcopy(v) for k, v in r.items() if not k.startswith('_')}

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
        d = {k: v.value() for k, v in self.led.items()}
        d['energy_residual_j'] = d['energy_to_sample_j'] - d['energy_reflected_j'] - d['energy_absorbed_j'] - d['energy_transmitted_j']
        return d

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        L = library()['meta']
        return dict(library=LIBRARY_REL, library_sha256=hashlib.sha256(LIBRARY_PATH.read_bytes()).hexdigest(), substances=len(L['substances']), license=L['license'])


ASYNC = {'focus.auto', 'measurement.auto_range', 'measurement.start', 'measurement.stitch', 'film.measure'}
_DATA_GUARDED = {'measurement.result'}
_RECORD_ARG = {'measurement.result': 'record_id'}
_ANALYSIS_RECORD_ARGS = {'data.roughness': ['record_id'], 'data.step_height': ['record_id'], 'data.profile': ['record_id'], 'calibration.z_scale': ['record_id'],
                         'calibration.xy_scale': ['record_id']}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill')
                    and k not in UNSUPPORTED}


class KeyenceVkx3000Session:
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
        meta = copy.deepcopy(export_keyence_vkx3000_contract_metadata())
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
        values['measurement.started_by_other_agent'] = foreign
        if foreign:
            values['measurement.id'] = None
        if self._role == 'agent':
            values['result.last_record_id'] = h._last_record(lambda k: not self._foreign(k))
            if self._foreign(h.last_value_rid):                          # the displayed result of another agent's record is not shown
                values['result.last_value'] = None
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
            if capability_id in ('power.set', 'power.press_button') and not h.power:
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED:
            rid = arguments.get(_RECORD_ARG[capability_id])
            if self._foreign(rid):
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
                    result = dict(result, template_source=dict(library=result['template_source']['library'], substances=[]))
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
            res = {k: v for k, v in op.items() if k in ('phase', 'reason', 'z_m', 'z_range_m')}
        return dict(operation_id=operation_id, status=op['phase'], result=res)

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
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'POWER_OFF': '控制器未通电', 'NOT_READY': '系统启动中',
    'ERROR_PENDING': '有未确认的错误', 'CAUSE_PERSISTS': '错误原因仍存在，不能确认', 'MAINS_OUT_OF_RANGE': '电源电压超出100–240 VAC ±10 %',
    'MAINS_FAULT': '供电电压超出范围，控制器断电', 'UNKNOWN_SUBSTANCE': '模板库中没有该材料', 'SAMPLE_EXISTS': '样品ID已存在', 'TOO_MANY_SAMPLES': '样品数量超过上限',
    'INVALID_FEATURE': '表面特征参数不自洽（节距须大于宽度；球冠高度须≤半径）', 'UNKNOWN_SAMPLE': '样品不存在', 'SAMPLE_NOT_ON_BENCH': '样品不在实验台上',
    'STAGE_OCCUPIED': '样品台上已有样品', 'OUTSIDE_PLATE': '样品超出圆形样品台可放置范围', 'SAMPLE_TOO_TALL': '样品太高：物镜与样品之间留不出安全距离（先下降载物台或抬起Z）',
    'SAMPLE_NOT_ON_STAGE': '该样品不在样品台上', 'MEASUREMENT_RUNNING': '有测量或自动操作进行中', 'MOTION_IN_PROGRESS': '轴在移动或转盘在转动',
    'STAGE_FAULT': '电动台故障（电缆断开）', 'NO_SAMPLE': '样品台上没有样品', 'OUT_OF_RANGE': '目标超出XY行程', 'COLLISION_RISK': '目标位置会使物镜碰到样品（碰撞保护）',
    'REVOLVER_FAULT': '物镜转盘故障（卡住）', 'LASER_FAULT': '激光故障', 'LENS_NOT_SUPPORTED': '光轴上的物镜不支持该测量原理', 'FILM_INDEX_REQUIRED': '膜模式需要膜折射率',
    'SETTING_NOT_FOR_MODE': '双重扫描与峰模式只用于激光共焦', 'INVALID_Z_RANGE': '上限Z必须小于下限Z', 'Z_RANGE_NOT_SET': '尚未设定扫描上下限（手动或自动）',
    'Z_RANGE_TOO_SMALL': '扫描范围小于3个Z步', 'TOO_MANY_STEPS': 'Z步数超过4000（增大间距或缩小范围）', 'NO_SURFACE_FOUND': '在搜索范围内没有找到表面',
    'RECORDS_FULL': '记录数已满', 'UNKNOWN_RECORD': '记录不存在', 'RECORD_NOT_SUITABLE': '记录不是已完成的高度图', 'REGION_TOO_SMALL': '区域内像素太少',
    'TOO_FEW_VALID_PIXELS': '有效像素太少', 'STEP_NOT_FOUND': '记录中找不到台阶', 'PITCH_NOT_FOUND': '记录中找不到周期结构', 'NOT_RUNNING': '操作不在运行中',
    'UNKNOWN_OPERATION': '操作不存在', 'USER_CANCEL': '用户取消', 'NO_OBJECTIVE': '光轴上没有物镜（转盘停在两孔之间，先选择物镜）',
}
EFFECTS = {
    'plant.configure': ['head.focus_z_indicated_m', 'head.revolver_moving', 'head.z_moving', 'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind',
        'measurement.progress', 'measurement.remaining_s', 'panel.controller_lamp', 'panel.head_lamp', 'panel.laser_lamp', 'panel.stage_lamp', 'plant.configured',
        'power.enabled', 'stage.moving', 'stage.target_m', 'stage.x_indicated_m', 'stage.y_indicated_m', 'system.error_code', 'system.state', 'truth.xy_scale_error',
        'truth.z_scale_error'],
    'sample.provide': ['sample.bench_ids', 'truth.samples'],
    'sample.place': ['sample.bench_ids', 'sample.on_stage', 'truth.focus_error_m', 'truth.samples', 'truth.surface_z_on_axis_m'],
    'sample.remove': ['sample.bench_ids', 'sample.on_stage', 'truth.focus_error_m', 'truth.samples', 'truth.surface_z_on_axis_m'],
    'power.set': ['head.focus_z_indicated_m', 'head.revolver_moving', 'head.z_moving', 'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind',
        'measurement.progress', 'measurement.remaining_s', 'panel.controller_lamp', 'panel.head_lamp', 'panel.laser_lamp', 'panel.stage_lamp', 'power.enabled',
        'stage.moving', 'stage.target_m', 'stage.x_indicated_m', 'stage.y_indicated_m', 'system.error_code', 'system.state'],
    'power.press_button': ['head.focus_z_indicated_m', 'head.revolver_moving', 'head.z_moving', 'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind',
        'measurement.progress', 'measurement.remaining_s', 'panel.controller_lamp', 'panel.head_lamp', 'panel.laser_lamp', 'panel.stage_lamp', 'power.enabled',
        'stage.moving', 'stage.target_m', 'stage.x_indicated_m', 'stage.y_indicated_m', 'system.error_code', 'system.state'],
    'system.acknowledge': ['system.error_code'],
    'fault.inject': ['head.revolver_moving', 'head.z_moving', 'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind', 'measurement.progress',
        'measurement.remaining_s', 'panel.laser_lamp', 'panel.stage_lamp', 'stage.moving', 'stage.target_m', 'system.error_code', 'truth.faults'],
    'stage.move': ['stage.moving', 'stage.position_label', 'stage.target_m'],
    'stage.go_to': ['stage.moving', 'stage.position_label', 'stage.target_m'],
    'stage.set_lift': ['stage.lift_target_m'],
    'lens.select': ['head.lens_slot', 'head.revolver_moving', 'head.z_moving', 'head.z_target_m'],
    'zoom.set': ['optics.fov_m', 'optics.total_magnification', 'optics.zoom'],
    'focus.move': ['head.z_moving', 'head.z_target_m'],
    'focus.auto': ['measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent'],
    'measurement.configure': ['measurement.settings'],
    'measurement.set_z_range': ['measurement.z_range_m'],
    'measurement.auto_range': ['laser.on', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent',
        'panel.laser_lamp'],
    'measurement.start': ['measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent'],
    'measurement.stitch': ['measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent'],
    'film.measure': ['measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent'],
    'calibration.z_scale': ['calibration.z_scale'],
    'calibration.xy_scale': ['calibration.xy_scale'],
}
EFFECTS['sim.step'] = ['clock.time_s']
EFFECTS['data.roughness'] = ['result.last_value']                   # the analysis result is shown in the result box of the monitor
EFFECTS['data.step_height'] = ['result.last_value']
ON_STEP = {
    'plant.configure': ['head.focus_z_indicated_m', 'head.magnification', 'head.objective', 'head.revolver_moving', 'head.revolver_rad', 'head.z_m', 'head.z_moving',
        'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'optics.fov_m',
        'optics.total_magnification', 'panel.head_lamp', 'panel.laser_lamp', 'result.last_record_id', 'result.last_value', 'stage.moving', 'stage.target_m',
        'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'system.state', 'truth.focus_error_m', 'truth.surface_z_on_axis_m'],
    'sample.remove': ['truth.focus_error_m', 'truth.surface_z_on_axis_m'],
    'power.set': ['head.focus_z_indicated_m', 'head.magnification', 'head.objective', 'head.revolver_moving', 'head.revolver_rad', 'head.z_m', 'head.z_moving',
        'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'optics.fov_m',
        'optics.total_magnification', 'panel.head_lamp', 'panel.laser_lamp', 'result.last_record_id', 'result.last_value', 'stage.moving', 'stage.target_m',
        'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'system.state', 'truth.focus_error_m', 'truth.surface_z_on_axis_m'],
    'power.press_button': ['head.focus_z_indicated_m', 'head.magnification', 'head.objective', 'head.revolver_moving', 'head.revolver_rad', 'head.z_m', 'head.z_moving',
        'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'optics.fov_m',
        'optics.total_magnification', 'panel.head_lamp', 'panel.laser_lamp', 'result.last_record_id', 'result.last_value', 'stage.moving', 'stage.target_m',
        'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'system.state', 'truth.focus_error_m', 'truth.surface_z_on_axis_m'],
    'fault.inject': ['head.focus_z_indicated_m', 'head.magnification', 'head.objective', 'head.revolver_moving', 'head.revolver_rad', 'head.z_m', 'head.z_moving',
        'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'optics.fov_m',
        'optics.total_magnification', 'panel.laser_lamp', 'result.last_record_id', 'result.last_value', 'stage.moving', 'stage.target_m', 'stage.x_indicated_m',
        'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'truth.focus_error_m', 'truth.surface_z_on_axis_m'],
    'stage.move': ['stage.moving', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'truth.focus_error_m', 'truth.surface_z_on_axis_m'],
    'stage.go_to': ['stage.moving', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'truth.focus_error_m', 'truth.surface_z_on_axis_m'],
    'stage.set_lift': ['stage.focus_drive_rad', 'stage.lift_m', 'truth.focus_error_m', 'truth.surface_z_on_axis_m'],
    'lens.select': ['head.focus_z_indicated_m', 'head.magnification', 'head.objective', 'head.revolver_moving', 'head.revolver_rad', 'head.z_m', 'head.z_moving',
        'optics.fov_m', 'optics.total_magnification', 'truth.focus_error_m'],
    'zoom.set': ['optics.fov_m', 'optics.total_magnification'],
    'focus.move': ['head.focus_z_indicated_m', 'head.z_m', 'head.z_moving', 'truth.focus_error_m'],
    'focus.auto': ['head.focus_z_indicated_m', 'head.z_m', 'head.z_moving', 'head.z_target_m', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s',
        'measurement.started_by_other_agent', 'truth.focus_error_m'],
    'measurement.auto_range': ['laser.on', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent',
        'measurement.z_range_m', 'panel.laser_lamp'],
    'measurement.start': ['head.focus_z_indicated_m', 'head.z_m', 'head.z_moving', 'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind',
        'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent', 'panel.laser_lamp', 'result.last_record_id', 'result.last_value',
        'truth.focus_error_m'],
    'measurement.stitch': ['head.focus_z_indicated_m', 'head.z_m', 'head.z_moving', 'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind',
        'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent', 'panel.laser_lamp', 'result.last_record_id', 'result.last_value',
        'stage.moving', 'stage.position_label', 'stage.target_m', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m', 'stage.y_m', 'truth.focus_error_m',
        'truth.surface_z_on_axis_m'],
    'film.measure': ['measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent',
        'result.last_record_id', 'result.last_value'],
    'sim.step': ['head.focus_z_indicated_m', 'head.magnification', 'head.objective', 'head.revolver_moving', 'head.revolver_rad', 'head.z_m', 'head.z_moving',
        'head.z_target_m', 'laser.on', 'measurement.id', 'measurement.kind', 'measurement.progress', 'measurement.remaining_s', 'measurement.started_by_other_agent',
        'measurement.z_range_m', 'optics.fov_m', 'optics.total_magnification', 'panel.head_lamp', 'panel.laser_lamp', 'result.last_record_id', 'result.last_value',
        'stage.focus_drive_rad', 'stage.lift_m', 'stage.moving', 'stage.position_label', 'stage.target_m', 'stage.x_indicated_m', 'stage.x_m', 'stage.y_indicated_m',
        'stage.y_m', 'system.state', 'truth.focus_error_m', 'truth.surface_z_on_axis_m'],
}
ON_STEP_ZH = {
    'power.set': '通电后启动45 s（工程值）后就绪，启动时测量头Z台以5 mm/s回到原点；断电时各轴停止、进行中的测量失败',
    'power.press_button': '同power.set（按键切换到的状态）',
    'stage.move': '电动台以10 mm/s（工程值）移到目标，两轴同时移动',
    'stage.go_to': '同stage.move',
    'stage.set_lift': '手摇旋钮以2 mm/s（工程值）升降载物台，旋钮转角随升降（4 mm/转）',
    'lens.select': '需要时先以5 mm/s抬起Z，再以每档0.8 s（工程值）转动转盘；转动中光轴上没有物镜（倍率读数为空）',
    'focus.move': 'Z台以5 mm/s移到目标，焦面随之移动',
    'focus.auto': '2 s搜索后Z台移到对焦位置',
    'measurement.auto_range': '3 s预扫描后写入扫描上下限',
    'measurement.start': '先移到扫描上限，再按帧时间逐步扫描到下限（激光共焦：行数/7900 Hz；焦点变化30 fps；白光干涉100 fps，工程值），完成后Z回到起点并写入记录',
    'measurement.stitch': '逐视场：XY台移到视场、Z扫描、抬回上限；全部完成后XY与Z回到起点并写入拼接记录',
    'film.measure': '1.5 s（工程值）后写入膜厚记录',
    'fault.inject': '激光故障使进行中的激光测量失败；电缆断开使电动台停止与拼接失败；转盘卡住停止转盘；振动在其后激光共焦扫描的每一行加入Z扰动（条纹）',
    'plant.configure': '供电超出100–240 VAC ±10 %时控制器断电',
    'sim.step': '宿主推进时间：轴运动、启动、测量与自动操作的阶段在事件时刻精确切分',
}
_R = ['power.enabled', 'system.state', 'system.error_code']
_OP = _R + ['measurement.id', 'head.z_moving', 'head.revolver_moving', 'stage.moving', 'sample.on_stage']
PRECONDITIONS = {
    'plant.configure': ('噪声种子只能在首次声明时设定', ['plant.configured']),
    'sample.provide': ('已声明实验室条件；样品ID未用过；材料在模板库中；特征参数自洽', ['plant.configured', 'sample.bench_ids']),
    'sample.place': ('样品在实验台上、样品台空、无测量与运动；样品在样品台内；最高点（升降台仍在上升时按其目标高度）与当前物镜端部之间留有1 mm', ['sample.bench_ids', 'sample.on_stage', 'measurement.id',
                                                                              'stage.moving', 'head.z_m', 'head.objective', 'stage.lift_m']),
    'sample.remove': ('样品在样品台上；无测量；台不在移动', ['sample.on_stage', 'measurement.id', 'stage.moving']),
    'power.set': ('已声明实验室条件；开机时电源电压在100–240 VAC ±10 %', ['plant.configured', 'power.enabled']),
    'power.press_button': ('已声明实验室条件；断电时按下为开机（电源电压须在100–240 VAC ±10 %），通电时按下为断电', ['plant.configured', 'power.enabled']),
    'system.acknowledge': ('已通电；错误原因已排除', _R),
    'fault.inject': ('已声明实验室条件', ['plant.configured']),
    'stage.move': ('已就绪、无错误；电动台无故障；无测量', _R + ['measurement.id']),
    'stage.go_to': ('同stage.move；样品中心须在±50 mm行程内', _R + ['measurement.id', 'sample.on_stage']),
    'stage.set_lift': ('已声明实验室条件；无测量；转盘静止；上升时样品最高点与物镜端部之间留有0.1 mm（不需要通电）', ['plant.configured', 'measurement.id', 'head.revolver_moving',
                                                                                     'stage.lift_m', 'head.z_m']),
    'lens.select': ('已就绪、无错误；转盘无故障；无测量；Z与转盘静止；转动中扫过光轴的每只物镜（含新物镜）在可抬起的Z范围内离样品最高点（升降台上升时按目标）留1 mm', _R + ['measurement.id', 'head.z_moving', 'head.revolver_moving',
                                                                                 'sample.on_stage']),
    'zoom.set': ('已就绪、无错误；无测量', _R + ['measurement.id']),
    'focus.move': ('已就绪、无错误；无测量；Z与转盘静止；目标Z时物镜端部离样品最高点（升降台上升时按目标）留0.1 mm', _R + ['measurement.id', 'head.z_moving', 'head.revolver_moving', 'sample.on_stage']),
    'focus.auto': ('已就绪、无错误；无测量与运动；样品在台上；光轴上的物镜可用于测量；表面在搜索范围内', _OP + ['head.objective']),
    'measurement.configure': ('已声明实验室条件；无测量；膜模式给出折射率；双重扫描与峰模式只用于激光共焦', ['plant.configured', 'measurement.id']),
    'measurement.set_z_range': ('已声明实验室条件；无测量；上限Z<下限Z', ['plant.configured', 'measurement.id']),
    'measurement.auto_range': ('同focus.auto；物镜支持所选原理；激光原理时激光无故障', _OP + ['head.objective', 'measurement.settings']),
    'measurement.start': ('已就绪、无错误；无测量与运动；样品在台上；物镜支持所选原理；已设上下限且Z步数3–4000；下限不会使物镜碰样品；激光原理时激光无故障',
                          _OP + ['head.objective', 'measurement.settings', 'measurement.z_range_m']),
    'measurement.stitch': ('同measurement.start；电动台无故障；各视场中心在±50 mm行程内', _OP + ['head.objective', 'measurement.settings', 'measurement.z_range_m']),
    'film.measure': ('已就绪、无错误；无测量与运动；样品在台上；光轴上的物镜支持膜厚测量；材料在模板库中（样品须在焦点附近，失焦时测量完成但收集的光不足而无结果）', _OP + ['head.objective']),
    'calibration.z_scale': ('已声明实验室条件；无测量；记录为已完成的单视场高度图且区域内找得到台阶', ['plant.configured', 'measurement.id', 'result.last_record_id']),
    'calibration.xy_scale': ('已声明实验室条件；无测量；记录为已完成的单视场高度图且有周期结构', ['plant.configured', 'measurement.id', 'result.last_record_id']),
    'measurement.result': ('记录存在', ['result.last_record_id']),
    'data.roughness': ('记录为已完成的高度图；区域与有效像素足够', ['result.last_record_id']),
    'data.step_height': ('记录为已完成的高度图', ['result.last_record_id']),
    'data.profile': ('记录为已完成的高度图', ['result.last_record_id']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_RE = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING']
_OPS = _RE + ['MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'NO_SAMPLE']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'sample.provide': ['PLANT_REQUIRED', 'SAMPLE_EXISTS', 'TOO_MANY_SAMPLES', 'UNKNOWN_SUBSTANCE', 'INVALID_FEATURE'],
    'sample.place': ['PLANT_REQUIRED', 'UNKNOWN_SAMPLE', 'SAMPLE_NOT_ON_BENCH', 'STAGE_OCCUPIED', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'OUTSIDE_PLATE', 'SAMPLE_TOO_TALL'],
    'sample.remove': ['PLANT_REQUIRED', 'SAMPLE_NOT_ON_STAGE', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS'],
    'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_button': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'system.acknowledge': ['PLANT_REQUIRED', 'POWER_OFF', 'CAUSE_PERSISTS'],
    'fault.inject': ['PLANT_REQUIRED'],
    'stage.move': _RE + ['STAGE_FAULT', 'MEASUREMENT_RUNNING'], 'stage.go_to': _RE + ['STAGE_FAULT', 'MEASUREMENT_RUNNING', 'NO_SAMPLE', 'OUT_OF_RANGE'],
    'stage.set_lift': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'COLLISION_RISK'],
    'lens.select': _RE + ['REVOLVER_FAULT', 'MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'COLLISION_RISK'],
    'zoom.set': _RE + ['MEASUREMENT_RUNNING'], 'focus.move': _RE + ['MEASUREMENT_RUNNING', 'MOTION_IN_PROGRESS', 'COLLISION_RISK'],
    'focus.auto': _OPS + ['NO_OBJECTIVE', 'LENS_NOT_SUPPORTED', 'NO_SURFACE_FOUND', 'COLLISION_RISK', 'USER_CANCEL', 'POWER_OFF'],
    'measurement.configure': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'FILM_INDEX_REQUIRED', 'SETTING_NOT_FOR_MODE'],
    'measurement.set_z_range': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'INVALID_Z_RANGE'],
    'measurement.auto_range': _OPS + ['NO_OBJECTIVE', 'LENS_NOT_SUPPORTED', 'LASER_FAULT', 'NO_SURFACE_FOUND', 'USER_CANCEL'],
    'measurement.start': _OPS + ['NO_OBJECTIVE', 'LENS_NOT_SUPPORTED', 'LASER_FAULT', 'Z_RANGE_NOT_SET', 'COLLISION_RISK', 'Z_RANGE_TOO_SMALL', 'TOO_MANY_STEPS', 'RECORDS_FULL',
                                 'USER_CANCEL'],
    'measurement.stitch': _OPS + ['STAGE_FAULT', 'NO_OBJECTIVE', 'LENS_NOT_SUPPORTED', 'LASER_FAULT', 'Z_RANGE_NOT_SET', 'COLLISION_RISK', 'Z_RANGE_TOO_SMALL', 'TOO_MANY_STEPS',
                                  'OUT_OF_RANGE', 'RECORDS_FULL', 'USER_CANCEL'],
    'film.measure': _OPS + ['UNKNOWN_SUBSTANCE', 'NO_OBJECTIVE', 'LENS_NOT_SUPPORTED', 'RECORDS_FULL', 'USER_CANCEL'],
    'calibration.z_scale': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'STEP_NOT_FOUND'],
    'calibration.xy_scale': ['PLANT_REQUIRED', 'MEASUREMENT_RUNNING', 'UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'PITCH_NOT_FOUND'],
    'measurement.result': ['UNKNOWN_RECORD'], 'data.roughness': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE', 'REGION_TOO_SMALL', 'TOO_FEW_VALID_PIXELS'],
    'data.step_height': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE'], 'data.profile': ['UNKNOWN_RECORD', 'RECORD_NOT_SUITABLE'],
    'records.list': [], 'events.read': [], 'ledger.read': [], 'provenance.read': [], 'sim.step': [],
}
_PARTIAL = {'USER_CANCEL', 'POWER_OFF', 'LASER_FAULT', 'STAGE_FAULT'}
CAP_ERROR_ZH = {('measurement.start', 'POWER_OFF'): '未通电；或测量中断电（记录以failed结束，不保留部分数据）',
                ('measurement.stitch', 'STAGE_FAULT'): '电动台电缆断开；或拼接中断开（拼接失败，已测视场丢弃）',
                ('measurement.start', 'LASER_FAULT'): '激光故障；或激光测量中发生激光故障（记录以failed结束）'}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件，没有可见模型变化', 'sample.provide': '只在过程层实验台上增加样品；场景没有样品几何',
    'sample.place': '样品只在过程层（场景没有样品几何，圆形样品台为接口点）', 'sample.remove': '同上', 'fault.inject': '评估者故障注入，没有可见模型变化',
    'system.acknowledge': '只清除错误状态', 'measurement.configure': '只写设置', 'measurement.set_z_range': '只写设置', 'film.measure': '光谱测量没有可见运动',
    'calibration.z_scale': '只写修正系数', 'calibration.xy_scale': '只写修正系数', 'measurement.result': '只读记录', 'data.roughness': '分析计算（结果写到显示器结果框）', 'data.step_height': '分析计算（结果写到显示器结果框）',
    'data.profile': '分析计算', 'records.list': '只读列表', 'events.read': '只读事件', 'ledger.read': '只读账目（evaluator）', 'provenance.read': '只读来源记录',
    'zoom.set': '变焦镜组在测量头内，没有可见几何（读数经显示器镜像）',
}
SCENE_MIRRORED = {'stage.x_m', 'stage.y_m', 'stage.lift_m', 'stage.focus_drive_rad', 'head.z_m', 'head.revolver_rad', 'head.focus_z_indicated_m', 'head.magnification',
                  'optics.zoom', 'result.last_value', 'measurement.remaining_s', 'panel.head_lamp', 'panel.stage_lamp', 'panel.controller_lamp', 'panel.laser_lamp'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节、显示器读数或指示灯（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {   # capabilities whose phases differ from the scan template (completion criterion, partial failure)
    'film.measure': ('操作结束（完成、失败或取消）：1.5 s光谱采集（视场中心，白光光源）完成后写入膜厚记录；没有移动、扫描或返回阶段',
                     '取消或断电时记录以cancelled/failed结束，不保留部分光谱；光谱测量不用激光，也不移动电动台或Z台，激光故障与电动台故障不影响它；'
                     'Z、XY与升降保持原位。'),
}
RESOURCES = {'measurement.start': ['laser', 'z_stage', 'detector', 'host_clock'], 'measurement.stitch': ['laser', 'z_stage', 'xy_stage', 'detector', 'host_clock'],
             'measurement.auto_range': ['laser', 'z_stage', 'detector', 'host_clock'], 'focus.auto': ['z_stage', 'detector', 'host_clock'],
             'film.measure': ['spectrometer', 'host_clock'], 'stage.move': ['xy_stage'], 'stage.go_to': ['xy_stage'], 'stage.set_lift': ['stage_lift'],
             'lens.select': ['revolver', 'z_stage'], 'focus.move': ['z_stage'], 'zoom.set': ['zoom'], 'power.set': ['mains'], 'power.press_button': ['mains'], 'plant.configure': ['room', 'mains'],
             'sample.provide': ['bench'], 'sample.place': ['bench', 'sample_plate'], 'sample.remove': ['bench', 'sample_plate'], 'fault.inject': ['evaluator'],
             'sim.step': ['host_clock'], 'system.acknowledge': ['controller']}
OBSERVES = {
    'power.set': ['power.enabled', 'system.state', 'panel.controller_lamp', 'panel.head_lamp', 'panel.stage_lamp'],
    'power.press_button': ['power.enabled', 'system.state', 'panel.controller_lamp', 'panel.head_lamp', 'panel.stage_lamp'],
    'stage.move': ['stage.x_indicated_m', 'stage.y_indicated_m', 'stage.moving'], 'stage.go_to': ['stage.x_indicated_m', 'stage.y_indicated_m', 'stage.position_label'],
    'stage.set_lift': ['stage.lift_m'], 'lens.select': ['head.objective', 'head.magnification', 'head.revolver_moving'], 'zoom.set': ['optics.zoom', 'optics.fov_m'],
    'focus.move': ['head.focus_z_indicated_m', 'head.z_moving'], 'focus.auto': ['head.focus_z_indicated_m', 'measurement.id'],
    'measurement.auto_range': ['measurement.z_range_m'], 'measurement.start': ['measurement.id', 'measurement.progress', 'result.last_record_id', 'laser.on'],
    'measurement.stitch': ['measurement.id', 'measurement.progress', 'result.last_record_id'], 'film.measure': ['measurement.id', 'result.last_record_id'],
    'calibration.z_scale': ['calibration.z_scale'], 'calibration.xy_scale': ['calibration.xy_scale'], 'measurement.configure': ['measurement.settings'],
    'measurement.set_z_range': ['measurement.z_range_m'], 'sample.place': ['sample.on_stage'], 'sample.remove': ['sample.bench_ids'], 'system.acknowledge': ['system.error_code'],
}
_SPEC = 'manufacturer_primary'
_HEAD = 'manufacturer_head'
_BASE = 'manufacturer_base'
_DRAW = 'dimension_drawings'
_CMU = 'cmu_same_configuration'
_REL = 'related_vk_x250k'
_PHOTO = 'product_photos'
_X1000 = 'related_vk_x1000'
_PAGES = 'manufacturer_pages'
_OCL = 'optical_constants_library'
_RII = 'refractiveindex_info'
_PHYS = 'physics_literature'
_ISO = 'iso_standards'
_SIM = 'simulation_assumption'
_WB = 'user_workbook'
CAP_SOURCES = {
    'plant.configure': [_SPEC, _SIM], 'sample.provide': [_OCL, _RII, _WB], 'sample.place': [_DRAW, _SIM], 'sample.remove': [_SIM], 'power.set': [_SPEC, _HEAD], 'power.press_button': [_SPEC, _PHOTO, _SIM],
    'system.acknowledge': [_SIM], 'fault.inject': [_SIM], 'stage.move': [_BASE, _SPEC], 'stage.go_to': [_BASE], 'stage.set_lift': [_DRAW, _SIM],
    'lens.select': [_SPEC, _HEAD, _SIM], 'zoom.set': [_SPEC, _HEAD], 'focus.move': [_DRAW, _SIM], 'focus.auto': [_SIM], 'measurement.configure': [_SPEC, _HEAD, _PAGES, _REL, _X1000],
    'measurement.set_z_range': [_SPEC, _REL], 'measurement.auto_range': [_SPEC, _REL, _SIM], 'measurement.start': [_SPEC, _HEAD, _REL, _X1000, _OCL, _RII, _PHYS],
    'measurement.stitch': [_SPEC, _PAGES, _CMU, _BASE, _REL], 'film.measure': [_SPEC, _HEAD, _CMU, _OCL, _RII, _PHYS], 'calibration.z_scale': [_SPEC, _ISO],
    'calibration.xy_scale': [_SPEC, _ISO], 'measurement.result': [_SPEC], 'data.roughness': [_ISO], 'data.step_height': [_ISO], 'data.profile': [_ISO],
    'records.list': [_SIM], 'events.read': [_SIM], 'ledger.read': [_SIM, _PHYS], 'provenance.read': [_OCL, _RII], 'sim.step': [_SIM],
    **{k: [_SPEC, _HEAD] for k in UNSUPPORTED}}
PARAM_ZH = {'room_k': '实验室室温（厂家使用环境+15–28 °C）', 'mains_voltage_v': '供电电压（厂家100–240 VAC）', 'relative_humidity': '相对湿度（厂家20–80 %RH）',
            'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.vk_x3000_profiler.limitations。'
_ROBOT = '放样与取样为宿主/机器人技能的过程层抽象（没有抓取、压片与接触验证）；场景没有样品几何。'
CAP_LIMITS = {
    'power.press_button': ['电源键为控制器正面的瞬时按键（位置与外观按照片，按下行程1.5 mm与弹簧为工程值）；按一次切换通断，与power.set为同一过程。'],
    'sample.provide': ['样品=衬底+最多4个表面特征（台阶、光栅、柱阵、坑阵、球冠、磨痕、正弦标准片）+可选共形透明膜+高斯相关随机粗糙度（每个样品固定的随机相位场）+倾斜；'
                       '特征边缘为tanh过渡（10–90 %宽度）；只追踪一个埋入界面（透明膜下更深的界面忽略）。'],
    'sample.place': [_ROBOT, '放样时样品最高点须比当前物镜端部低1 mm（安全距离为工程值）。'],
    'sample.remove': [_ROBOT],
    'stage.move': ['速度10 mm/s为工程值；到位无随机误差（拼接的视场位置按指令位置）。'],
    'stage.set_lift': ['粗/微调旋钮驱动载物台升降是按尺寸图结构推断的机构；行程30 mm与4 mm/转为工程值；微调旋钮只作为同一升降的细分，未单独建模。'],
    'lens.select': ['物镜配置（2.5×与10×环形照明、20×、50×、150×、20×干涉）及NA/WD为工程假设（公开资料未列）；转动速度为工程值；换镜时按1 mm安全距离自动抬Z。'],
    'zoom.set': ['视场=18.5 mm/(物镜倍率×变焦)，按厂家最大视场7398 µm（2.5×、1×）定标；厂家最小视场11 µm与最高总倍率28800×对应的倍率比（约685）大于物镜×变焦比（480），'
                 '差异可能来自显示条件或数码变焦，模型不外推。'],
    'focus.auto': ['在当前Z附近±max(20×轴向半高宽, 0.3 mm)搜索最亮界面（透明膜时可能锁定在更亮的下界面）；残差0.1×半高宽。'],
    'measurement.auto_range': ['自动上下限设定的功能来自相关型号VK-X250K规格（VK-X3000页未列）；激光原理时预扫描3 s内激光开启（计入激光能量账）；'
                               '用32×24粗网格求视场内最高/最低表面，余量为2×半高宽或3×粗糙度；不模拟预扫描本身的强度数据与Z运动。'],
    'measurement.start': ['激光共焦：模拟网格128×96像素（仪器1024×768）；平面镜轴向响应按1艾里单位针孔（工程假设）的傍轴针孔积分计算，无零点与旁瓣，'
                          '半高宽为点探测器sinc²的1.29倍；镜面捕获按坡度与asin(NA)线性下降，未分辨粗糙度产生漫反射；'
                          '相对强度噪声2.55 %、每像素每帧Z噪声10 nm（工程值）、每帧共同Z抖动8 nm与加性噪声按厂家各物镜重复性（10×/20×/50×：100/40/12 nm）标定；'
                          '150×用12 nm（相关型号VK-X250K的150×值，VK-X3100页未列）；帧时间=行数/7900 Hz；'
                          '针孔共焦光学、自动增益（AAG）与双重扫描来自相关型号VK-X250K规格，透明膜厚与埋藏界面测量来自相关型号VK-X1000设施页，VK-X3000页未列，模型的阈值为工程假设；'
                          '顶面与埋藏界面的反射按强度（非相干）相加，膜厚小于轴向半高宽时的激光相干干涉未建模；'
                          '首峰/膜模式：每像素保留6个最大局部峰；距更强峰0.85半高宽内视为同一峰，弱峰须高于所有更强峰在该处轴向响应包络之和的1.5倍再加噪声阈值，否则视为拖尾上的噪声；'
                          '膜厚=（第二峰−首峰）×按物镜NA修正的有效折射率；两峰分不开（膜太薄或NA太低）时该像素无膜厚；'
                          '焦点变化：纹理=未分辨粗糙度/30 nm与边缘，重复性按厂家值（10×/20×/50×：100/50/20 nm）除以纹理；'
                          '白光干涉：只用20×干涉物镜，反射相位按各材料600 nm处n、k，薄透明膜按相干多层反射相位，厚膜两个包络取强者，粗糙面为包络模式（20 nm+0.3σ）；'
                          '焦点变化与白光干涉只在对焦位置落在扫描上下限之内（留1个间距或半个相干包络）时有数据；'
                          '仪器Z标尺误差（σ 0.2 %）与横向误差（σ 0.4 %）隐藏，需用标准片校准。'],
    'measurement.stitch': ['视场网格按重叠率排布，高度按重叠区中值匹配；拼接图超过320×240时整数倍降采样；视场之间抬回扫描上限再移动XY。'],
    'film.measure': ['光谱420–780 nm（工程值）正入射多层反射；光谱仪波长标尺误差（σ 0.1 %）隐藏；拟合在20 nm–8 µm内搜索；有效范围100–5000 nm（同配置设施页）；'
                     '材料按操作者指定（材料不符则厚度偏差）；光经物镜收集：顶面反射与埋藏界面反射各按其离焦量的物镜轴向响应（600 nm）以振幅√η耦合，'
                     '离焦的一束使条纹对比度下降；拟合用同一相干两束模型R=g·|√P(x)·r01+√P(x+c·d/n_eff)·B(d)|²：两束的收集比例由物镜自身的轴向响应P和'
                     '一个焦点偏移x决定，增益g为0…1.1（参考镜归一），拟合量为厚度、焦点偏移与增益，厚度由条纹位置决定；'
                     '两束中较强的收集比例低于0.02、残差超过3 %、所指定的膜在拟合厚度下单程透过率低于1 %（不透明膜）、拟合干涉项的峰峰值不到噪声σ的5倍、'
                     '或膜模型的均方根残差比“所指定衬底无膜”（自由比例加平坦背景）模型改善不到1σ（无膜、膜与衬底折射率相近或光轴落在黑色载物板上，光谱中'
                     '没有干涉条纹）时无结果；噪声为相对0.3 %加绝对2×10⁻⁴（工程值）。所指定的膜或衬底材料与样品不符时厚度有偏差，'
                     '甚至可能把无膜样品拟合成看似可信的膜（例如裸铝指定为银上的PMMA），与真实反射式膜厚仪给错模型时相同，结果须由操作者核对材料。'
                     '重复性：厂家典型值0.1 nm按每次附加σ 0.1 nm计入；带内不到一个条纹的薄膜（约300 nm以下）由光谱噪声决定，可更差'
                     '（例如Al上110 nm PMMA在50×约0.3–0.6 nm）。'],
    'calibration.z_scale': ['修正系数=证书高度/原始测得高度（原始=记录高度÷记录时的系数），用于其后的测量。'],
    'calibration.xy_scale': ['节距用平均轮廓的FFT峰（抛物线细化）求得，修正当前记录所用物镜。'],
    'data.roughness': ['面参数在整平与可选S/L高斯滤波（ISO 16610-61，α=√(ln2/π)）后计算；轮廓取中心行/列，λc默认0.8 mm或评定长度/5；'
                       '模拟网格的像素尺寸限制可分辨的粗糙度波长。'],
    'data.step_height': ['A1型（单台阶）：上下两台面带用同一斜率、各自截距的双平行线最小二乘拟合，台阶高度为两截距之差（斜率由边缘两侧共同确定，不外推单侧直线）；'
                         '未给区域时用两水平k-means直方图法；leveling=plane时平面与台阶联合拟合（z=ax+by+c+d·标签，按去平面后的高度重新分类直到不变），'
                         '从原始高度与去掉稳健倾斜（相邻像素高度差的中位数）后的高度两个起点迭代，取残差较小者。'],
    'fault.inject': ['评估者注入；振动在激光共焦扫描的每一行加入30 nm（1σ）Z扰动（逐行扫描，条纹），在焦点变化中加入每像素30 nm、白光干涉中加入每像素15 nm的高度噪声（按振动时间比例，工程值）。'],
}
LIMITATIONS = [
    '选定配置：VK-X3000控制器、VK-X3100测量头（404 nm激光、白光LED、彩色CMOS、16位光电倍增管、6孔电动转盘、1–8×光学变焦）与VK-D3电动底座（100×100 mm电动XY）；'
    '物镜组合（2.5×、10×、20×、50×、150×、20×干涉）与参数为工程假设。',
    '测量物理：逐像素菲涅耳反射（光学常数模板库，CC0）、镜面捕获与漫反射、共焦轴向响应、Z扫描与峰拟合、焦点变化纹理判据、白光干涉相位与包络、光谱干涉膜厚；'
    '噪声常数按厂家重复性标定，不代表某台仪器的实测。',
    '样品为解析表面（特征+随机相位粗糙度）；样品在场景中没有几何；接触、夹持与样品变形未建模。',
    '模拟网格128×96像素远少于仪器1024×768：横向分辨率、宽度测量与粗糙度的短波成分都受网格限制（记录中给出名义像素数与模拟像素间距）。',
    '没有真实VK-X3000/VK-X3100/VK-D3的硬件与VK Viewer/VK Analyzer软件接口；机器人放样没有接触验证。',
]


def _sampling_text(k):
    if k in ('stage.x_m', 'stage.y_m', 'stage.lift_m', 'stage.focus_drive_rad', 'head.z_m', 'head.revolver_rad', 'clock.time_s', 'measurement.progress', 'measurement.remaining_s'):
        return 'Instantaneous value at the read time: constant-speed motions and operation phases are integrated exactly and cut at every event (arrivals, phase ends).'
    if k in ('stage.x_indicated_m', 'stage.y_indicated_m', 'head.focus_z_indicated_m'):
        return 'Encoder / display reading of the instantaneous position, quantised to 0.1 um; null while unpowered.'
    return 'Changes when a command is accepted or at an event of the operation (phase ends, record completion); no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or k in _ANALYSIS_RECORD_ARGS or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    return '；另一个agent的操作进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）' if k in _CONTROL_GUARDED else ''


def export_keyence_vkx3000_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'KeyenceVkx3000Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.keyence_vkx3000.KeyenceVkx3000Session.invoke',
                                              invocation=f'session.invoke(instance_id="vk_x3000_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['measurement.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='KeyenceVkx3000Runtime.invoke / KeyenceVkx3000Session._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate',
                                     description_zh='设定、目标、状态标志或立即改变的派生量；位置与操作进度只随step推进') for s in EFFECTS.get(k, [])],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=(EXEC_TEXT[k][0] if k in EXEC_TEXT else '操作结束（完成、失败或取消）：各阶段（移动、扫描、返回）按计划时刻完成后写入结果'
                                                      if async_ else '原子设定、配置、分析或查询；运动由step推进'),
                                completion_state_ids=(['measurement.id', 'measurement.progress', 'result.last_record_id'] if k in ('measurement.start', 'measurement.stitch', 'film.measure')
                                                      else ['measurement.id'] if async_ else []),
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=(EXEC_TEXT[k][1] if k in EXEC_TEXT else
                                                    '取消或失败（断电、激光故障、电动台故障）时记录以cancelled/failed结束，不保留部分数据；Z停在当时位置。' if k in ('measurement.start', 'measurement.stitch')
                                                    else '取消或失败时Z停在当时位置。' if async_ else
                                                    ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果（运动目标），被拒时状态不变'))),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH.get(e, e)), side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'laser_wavelength_m': (LASER_WAVELENGTH_M, 'm', 'VK-X3100半导体激光404 nm（厂家）', [_HEAD, _SPEC]), 'laser_max_w': (LASER_MAX_W, 'W', '最大激光输出1.0 mW，2类（厂家）', [_HEAD]),
            'line_rate_hz': (LINE_RATE_HZ, 'Hz', '激光最高测量速度：行7900 Hz（厂家）', [_HEAD]), 'surface_rate_hz': (SURFACE_RATE_HZ, 'Hz', '激光最高测量速度：面125 Hz（厂家）', [_HEAD]),
            'height_display_resolution_m': (HEIGHT_DISPLAY_RES_M, 'm', '激光共焦与焦点变化高度显示分辨率0.1 nm（厂家；不是精度）', [_HEAD, _WB]),
            'wli_display_resolution_m': (WLI_DISPLAY_RES_M, 'm', '白光干涉高度显示分辨率0.01 nm（厂家）', [_HEAD]),
            'confocal_repeatability_m': ([CONFOCAL_REPEAT_M[k] for k in (10.0, 20.0, 50.0)], 'm', '激光共焦高度重复性σ：10×/20×/50×为100/40/12 nm（厂家）', [_HEAD]),
            'fv_repeatability_m': ([FV_REPEAT_M[k] for k in (5.0, 10.0, 20.0, 50.0)], 'm', '焦点变化高度重复性σ：5×/10×/20×/50×为500/100/50/20 nm（厂家）', [_HEAD]),
            'wli_topography_repeatability_m': (WLI_TOPO_REPEAT_M, 'm', '白光干涉表面形貌重复性0.08 nm（厂家典型值）', [_HEAD]),
            'wli_rms_repeatability_m': (WLI_RMS_REPEAT_M, 'm', 'RMS重复性0.008 nm（厂家典型值）', [_HEAD]),
            'film_repeatability_m': (FILM_REPEAT_M, 'm', '光谱干涉膜厚重复性σ 0.1 nm（厂家典型值）', [_HEAD]),
            'film_accuracy_rel': (FILM_ACCURACY_REL, '1', '光谱干涉膜厚精度±0.6 %（厂家典型值）', [_HEAD]),
            'film_range_m': (list(FILM_RANGE_M), 'm', '膜厚100–5000 nm（同配置的CMU设施页）', [_CMU]),
            'height_accuracy': (list(HEIGHT_ACCURACY_M), 'm', '高度精度0.2+L/100 µm（厂家，20×以上物镜测标准样品；第二项为L的系数，无量纲）', [_HEAD]),
            'width_accuracy_rel': (WIDTH_ACCURACY_REL, '1', '宽度精度±2 %（厂家）', [_HEAD]), 'zoom_range': (list(ZOOM_RANGE), '1', '光学变焦1–8×（厂家）', [_HEAD]),
            'total_magnification_range': (list(TOTAL_MAG_RANGE), '1', '总倍率42–28800×（23英寸全屏显示，厂家）', [_HEAD, _WB]),
            'fov_range_m': (list(FOV_RANGE_M), 'm', '视场11–7398 µm（厂家）', [_HEAD, _WB]), 'motorized_xy_m': (list(MOTORIZED_XY_M), 'm', '电动XY行程100×100 mm（厂家VK-D3）', [_BASE, _WB]),
            'revolver_holes': (REVOLVER_HOLES, '1', '6孔电动转盘（厂家）', [_HEAD]), 'camera_pixels': (CAMERA_PIXELS, '1', '光学观察560万像素（厂家）', [_HEAD]),
            'mains_voltage_v': (list(MAINS_V), 'V', '电源100–240 VAC 50/60 Hz（厂家）', [_SPEC]), 'power_va': (POWER_VA, 'V*A', '功耗150 VA（厂家）', [_SPEC]),
            'ambient_k': (list(AMBIENT_K), 'K', '使用环境温度+15–28 °C（厂家）', [_SPEC]), 'relative_humidity': (list(RH_RANGE), '1', '相对湿度20–80 %RH（厂家）', [_SPEC]),
            'focus_z0_m': (FOCUS_Z0, 'm', 'Z原点时焦面高度＝立柱顶面288 mm−46.7 mm（厂家尺寸图）', [_DRAW]),
            'mass_head_kg': (MASS_HEAD_KG, 'kg', 'VK-X3100约13 kg（厂家）', [_HEAD]), 'mass_controller_kg': (MASS_CONTROLLER_KG, 'kg', 'VK-X3000约3 kg（厂家）', [_SPEC, _WB]),
            'mass_base_kg': (MASS_BASE_KG, 'kg', 'VK-D3约19.5 kg（厂家）', [_BASE])}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    for name, (val, unit, meaning) in {
            'fov_const_m': (FOV_CONST_M, 'm', '视场常数（FOV=常数/(倍率×变焦)，按7398 µm定标）'), 'sim_grid_px': (list(SIM_GRID), '1', '模拟像素网格'),
            'sigma_rel_intensity': (SIGMA_REL_INTENSITY, '1', '激光强度相对噪声'), 'sigma_add': (SIGMA_ADD, '1', '加性噪声（满量程分数）'),
            'frame_z_jitter_m': (FRAME_Z_JITTER_M, 'm', '每帧Z台位置噪声'), 'z_scale_sd': (Z_SCALE_SD, '1', '隐藏Z标尺误差（1σ）'), 'xy_scale_sd': (XY_SCALE_SD, '1', '隐藏横向误差（1σ）'),
            'stage_speed_m_s': (STAGE_SPEED_M_S, 'm/s', 'XY台速度'), 'head_z_speed_m_s': (HEAD_Z_SPEED_M_S, 'm/s', 'Z台速度'), 'head_z_range_m': (list(HEAD_Z_RANGE_M), 'm', 'Z台行程'),
            'lift_range_m': (list(LIFT_RANGE_M), 'm', '载物台升降行程'), 'revolver_s_per_step': (REVOLVER_S_PER_STEP, 's', '转盘每档时间'), 'boot_s': (BOOT_S, 's', '启动时间'),
            'fv_fps': (FV_FPS, '1/s', '焦点变化帧率'), 'wli_fps': (WLI_FPS, '1/s', '白光干涉帧率'), 'wli_lambda0_m': (WLI_LAMBDA0_M, 'm', '白光中心波长'),
            'wli_coherence_m': (WLI_COHERENCE_M, 'm', '白光相干长度'), 'collision_margin_m': (COLLISION_MARGIN_M, 'm', '物镜碰撞保护余量'),
            'optics_transmission': (OPTICS_TRANSMISSION, '1', '激光到样品的透过率'),
            'vibration_m': (VIBRATION_M, 'm', '注入振动的Z扰动（激光帧逐行扫描，每行1σ；焦点变化每像素1σ，白光干涉每像素0.5σ）'),
            'pixel_z_noise_m': (PIXEL_Z_NOISE_M, 'm', '每像素每帧独立Z噪声（与强度噪声一起按厂家重复性标定）'),
            'pinhole_v': (PINHOLE_V, '1', '共焦针孔半径（光学单位v=2πNA·r/λ，1艾里单位直径）'),
            'side_lobe_margin': (SIDE_LOBE_MARGIN, '1', '首峰/膜模式：弱峰须高于更强峰轴向响应拖尾之和的倍数'),
            'same_peak_fwhm': (SAME_PEAK_FWHM, '1', '首峰/膜模式：视为同一峰的间距（轴向半高宽的倍数）'),
            'valid_peak': (VALID_PEAK, '1', '有效峰须超过加性噪声的倍数'), 'aag_target': (AAG_TARGET, '1', '自动增益把99.5 %分位强度调到满量程的比例'),
            'double_scan_gain': (DOUBLE_SCAN_GAIN, '1', '双重扫描第二遍的增益倍数'), 'diffuse_collection': (DIFFUSE_COLLECTION, '1', '漫反射收集系数（×NA²）'),
            'z_repeat_m': (Z_REPEAT_M, 'm', '每次测量Z偏移重复性（1σ）'), 'fv_texture_ref_m': (FV_TEXTURE_REF_M, 'm', '焦点变化满纹理对应的未分辨粗糙度'),
            'film_noise_rel': (FILM_NOISE_REL, '1', '膜厚光谱相对噪声'), 'film_noise_abs': (FILM_NOISE_ABS, '1', '膜厚光谱绝对噪声（反射率）'),
            'film_min_signal': (FILM_MIN_SIGNAL, '1', '膜厚测量最低收集比例（失焦）'), 'film_gain_max': (FILM_GAIN_MAX, '1', '膜厚拟合中光谱增益的上限（参考镜归一）'),
            'film_fringe_snr': (FILM_FRINGE_SNR, '1', '膜厚测量要求的干涉项峰峰值（噪声σ的倍数）'),
            'film_bare_gain': (FILM_BARE_GAIN, '1', '膜模型相对无膜衬底模型须改善的均方根残差（噪声σ的倍数）'), 'film_band_m': ([FILM_BAND_NM[0] * 1e-9, FILM_BAND_NM[1] * 1e-9], 'm', '膜厚光谱范围')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['head_shell', 'z_stage', 'revolver', 'revolver_housing', 'stage_x', 'stage_y', 'stage_lift', 'focus_knob', 'sample_plate', 'controller_body']
                      + [L['id'] for L in LENSES],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='VK-X3000/VK-X3100/VK-D3三维表面轮廓显微镜：电动XY、手动升降、Z扫描、电动转盘与变焦；激光共焦、焦点变化、白光干涉与光谱干涉膜厚的逐像素测量模型；标定与ISO分析',
        equations=['I(z) = G sum_i S_i P(2 (z - z_i)(1 - cos a)/lambda), P(x) = int_0^vp |2 int_0^1 exp(2 pi i x rho^2) J0(v rho) rho d rho|^2 v dv / P(0), vp = 3.83',
                   'S = R f_capture, f_capture = clip(1 - theta/a) exp(-(4 pi sigma/lambda)^2) + (1 - exp(...)) 0.5 NA^2',
                   'R_1 = |(1 - N)/(1 + N)|^2 ; buried: S_2 = (1 - R_1)^2 |(N_1 - N_2)/(N_1 + N_2)|^2 exp(-8 pi k_1 t/lambda), z_2 = z_1 - t/n_eff(n_1, NA)',
                   'z_peak = z_j + p/2 (ln I_{j-1} - ln I_{j+1})/(ln I_{j-1} - 2 ln I_j + ln I_{j+1}) ; FWHM_z = x_FWHM lambda/(2 (1 - cos a)), x_FWHM = 1.14',
                   'WLI: h = z_eff - arg(-r_eff) lambda_0/(4 pi), r_eff = (r_01 + r_12 e^{2 i beta})/(1 + r_01 r_12 e^{2 i beta}), beta = 2 pi N_1 t/lambda_0',
                   'film: R(d, x, g) = g |sqrt(P(x)) r_01 + sqrt(P(x + c d/n_eff)) B(d)|^2, B = (1 - r_01^2) r_12 e^{2 i beta}/(1 + r_01 r_12 e^{2 i beta}), '
                   'c = 2 (1 - cos a)/600 nm, 0 <= g <= 1.1; min over d (20 nm-8 um), x, g of sum_lambda (R - R_meas)^2 over 420-780 nm; accepted if '
                   'g sqrt(P(x) P(x + c d/n_eff)) ptp(2 Re(conj(r_01) B)) >= 5 sigma and sqrt(res_bare - res_film) >= 1 sigma, '
                   'res_bare = min_{c_s, o >= 0} mean (c_s |r_air/sub|^2 + o - R_meas)^2',
                   'incoherent energy: R = R_1 + (1 - R_1)^2 R_2 tau^2/D, T = (1 - R_1)(1 - R_2) tau/D, A = (1 - R_1)(1 - tau)(1 + R_2 tau)/D, D = 1 - R_1 R_2 tau^2',
                   'Sq = sqrt(mean z^2), Sa = mean |z|, Gaussian filter alpha = sqrt(ln 2 / pi)'],
        parameters=params, source_ids=[_SIM, _OCL, _RII, _SPEC, _HEAD, _BASE, _DRAW, _CMU, _PHYS, _ISO, _WB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_vkx3000_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'vk_x3000_profiler': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'vk_x3000_profiler': mechanism})
